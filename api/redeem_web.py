# redeem_web.py
import asyncio
import base64
import json
import os
import io
import traceback
import hashlib
import requests
import time
import contextlib
import sys
import logging
import aiohttp
import threading

from io import BytesIO
from flask import Flask, request, jsonify
from playwright.async_api import async_playwright, TimeoutError
from dotenv import load_dotenv
import firebase_admin
from firebase_admin import credentials, firestore
from PIL import Image
import subprocess
#print("=== pip list ===")
#print(subprocess.getoutput("pip list"))
import cv2
import numpy as np
import pytesseract
import nest_asyncio
from datetime import datetime
import easyocr

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

@contextlib.contextmanager
def suppress_stdout():
    with open(os.devnull, "w") as devnull:
        old_stdout = sys.stdout
        sys.stdout = devnull
        try:
            yield
        finally:
            sys.stdout = old_stdout

# === 初始化 ===
app = Flask(__name__)
nest_asyncio.apply()

# === 設定 ===
OCR_MAX_RETRIES = 3
PAGE_LOAD_TIMEOUT = 60000
USE_EASYOCR = True
DEBUG_MODE = True
OCR_CONFIG = r"--oem 3 --psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_*"
reader = None  # EasyOCR reader

# === Firebase Init ===
load_dotenv()
cred_json = json.loads(base64.b64decode(
    os.environ.get("FIREBASE_KEY_BASE64") or os.environ.get("FIREBASE_CREDENTIALS", "{}")
).decode("utf-8"))
if "private_key" in cred_json:
    cred_json["private_key"] = cred_json["private_key"].replace("\\n", "\n")
if not firebase_admin._apps:
    firebase_admin.initialize_app(credentials.Certificate(cred_json))
db = firestore.client()

FAILURE_KEYWORDS = ["請先輸入", "不存在", "錯誤", "無效", "超出", "無法", "類型", "已使用"]
RETRY_KEYWORDS = ["驗證碼錯誤", "驗證碼已過期", "伺服器繁忙", "請稍後再試", "系統異常", "請重試", "處理中"]
REDEEM_RETRIES = 3
# === 主流程 ===
async def process_redeem(payload):
    start_time = time.time()
    code = payload.get("code")
    player_ids = payload.get("player_ids")
    debug = payload.get("debug", False)

    MAX_BATCH_SIZE = 5
    doc_ref_base = db.collection("ids")
    all_success = []
    all_fail = []
    all_received = []  # 用來儲存已領取過的 ID

    for i in range(0, len(player_ids), MAX_BATCH_SIZE):
        batch = player_ids[i:i + MAX_BATCH_SIZE]
        tasks = [run_redeem_with_retry(pid, code, debug=debug) for pid in batch]
        results = await asyncio.gather(*tasks)
        await asyncio.sleep(1)

        for r in results:
            if r.get("success"):
                all_success.append({
                    "player_id": r["player_id"],
                    "message": r.get("message")
                })
                logger.info(f"[{r['player_id']}] ✅ 重新成功：{r.get('message')}")
            else:
                reason = r.get("reason")  # 確保 reason 獲得賦值
                if "您已領取過該禮物" in reason:
                    # 已領取過的 ID 不算真正的失敗，單獨統計並刪除失敗資料
                    all_received.append({
                        "player_id": r["player_id"],
                        "message": reason
                    })
                    # 刪除該玩家的資料，因為他已領取過禮物
                    db.collection("failed_redeems").document(code).collection("players").document(r["player_id"]).delete()
                    logger.info(f"[{r['player_id']}] 已領取過該禮物，無法再次領取，從 failed_redeems 中刪除。")
                    continue  # 跳過該 ID，並且不進行刪除等操作

                all_fail.append({
                    "player_id": r.get("player_id"),
                    "reason": reason
                })
                logger.warning(f"[{r['player_id']}] ❌ 重新失敗：{reason}")

                # 特定錯誤訊息需刪除資料
                if "您已領取過該禮物" not in reason and "兌換成功，請在信件中領取獎勳" not in reason:
                    db.collection("failed_redeems").document(code).collection("players").document(r["player_id"]).delete()
                    logger.info(f"[{r['player_id']}] 資料已刪除：已領取過或完成兌換，理由：{reason}")

                # 針對其他特殊錯誤進行更新
                if r.get("reason") in ["驗證碼三次辨識皆失敗", "Timeout：單人兌換超過 90 秒"]:
                    doc = doc_ref_base.document("global").collection("players").document(r["player_id"]).get()
                    name = doc.to_dict().get("name", "未知") if doc.exists else "未知"
                    db.collection("failed_redeems").document(code).collection("players").document(r["player_id"]).set({
                        "name": name,
                        "reason": r.get("reason"),
                        "updated_at": datetime.datetime.now(datetime.timezone.utc)  # 修正為 UTC 時間
                    })
                else:
                    # 若為其他失敗情況，則刪除該玩家資料
                    db.collection("failed_redeems").document(code).collection("players").document(r["player_id"]).delete()

    # ✅ 全部處理完才發送 webhook
    webhook_message = (
        f"🔁 重新兌換完成：成功 {len(all_success)} 筆，失敗 {len(all_fail)} 筆\n"
        f"禮包碼：{code}\n"
    )
    # 顯示已領取過的 ID
    if all_received:
        received_lines = []
        for r in all_received:
            received_lines.append(f"{r['player_id']} ({r['message']})")
        webhook_message += "📋 已領取過的 ID（未列入失敗）：\n" + "\n".join(received_lines) + "\n"

    # 顯示失敗的 ID
    if all_fail:
        failed_lines = []
        for r in all_fail:
            pid = r["player_id"]
            doc = db.collection("ids").document("global").collection("players").document(pid).get()
            name = doc.to_dict().get("name", "未知") if doc.exists else "未知"
            failed_lines.append(f"{pid} ({name})")
        webhook_message += "⚠️ 仍失敗的 ID：\n" + "\n".join(failed_lines) + "\n"
    else:
        webhook_message += "✅ 所有失敗紀錄已成功兌換 / All failed records successfully redeemed"

    webhook_message += f"\n⌛ 執行時間：約 {time.time() - start_time:.1f} 秒"

    if os.getenv("DISCORD_WEBHOOK_URL"):
        try:
            resp = requests.post(os.getenv("DISCORD_WEBHOOK_URL"), json={
                "content": webhook_message
            })
            logger.info(f"Webhook 發送結果：{resp.status_code} {resp.text}")
        except Exception as e:
            logger.warning(f"Webhook 發送失敗：{e}")
    else:
        logger.warning("DISCORD_WEBHOOK_URL 未設定，跳過 webhook 發送 / Webhook URL not set, skipping webhook")


async def run_redeem_with_retry(player_id, code, debug=False):
    debug_logs = []

    for redeem_retry in range(REDEEM_RETRIES + 1):
        try:
            result = await asyncio.wait_for(
                _redeem_once(player_id, code, debug_logs, redeem_retry, debug=debug),
                timeout=90  # 每次單人兌換最多 90 秒
            )
        except asyncio.TimeoutError:
            logger.error(f"[{player_id}] 第 {redeem_retry + 1} 次：超過 90 秒 timeout")
            return {
                "success": False,
                "reason": "Timeout：單人兌換超過 90 秒",
                "player_id": player_id,
                "debug_logs": debug_logs
            }

        if result is None or not isinstance(result, dict):
            logger.error(f"[{player_id}] 第 {redeem_retry + 1} 次：_redeem_once 回傳 None 或格式錯誤 → {result}")
            return {
                "success": False,
                "reason": "無效回傳（None 或錯誤格式）",
                "player_id": player_id,
                "debug_logs": debug_logs
            }

        # ✅ 防止 NoneType 的 reason
        reason = result.get("reason") or ""

        if reason.startswith("_try"):
            return result

        if result.get("success"):
            return result

        if "登入失敗" in reason or "請先登入" in reason:
            return result

        if any(k in reason for k in RETRY_KEYWORDS):
            debug_logs.append({
                "retry": redeem_retry + 1,
                "info": f"Retry due to: {reason}"
            })
            await asyncio.sleep(2 + redeem_retry)
        else:
            return result

    return result

async def _redeem_once(player_id, code, debug_logs, redeem_retry, debug=False):
    browser = None

    def log_entry(attempt, **kwargs):
        entry = {"redeem_retry": redeem_retry, "attempt": attempt}
        entry.update(kwargs)
        debug_logs.append(entry)

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--disable-gpu"])
            context = await browser.new_context(locale="zh-TW")
            page = await context.new_page()
            await page.goto("https://wos-giftcode.centurygame.com/", timeout=PAGE_LOAD_TIMEOUT)
            await page.fill('input[type="text"]', player_id)
            await page.click(".login_btn")

            # 嘗試等待錯誤 modal
            try:
                await page.wait_for_selector(".message_modal", timeout=5000)
                modal_text = await page.inner_text(".message_modal .msg")
                log_entry(0, error_modal=modal_text)
                if any(k in modal_text for k in FAILURE_KEYWORDS):
                    logger.info(f"[{player_id}] 登入失敗：{modal_text}")
                    return await _package_result(page, False, f"登入失敗：{modal_text}", player_id, debug_logs, debug=debug)
            except TimeoutError:
                pass  # 無 modal 則繼續檢查登入成功

            # 加強：等待 .name 與兌換欄位都出現才視為成功
            try:
                await page.wait_for_selector(".name", timeout=5000)
                await page.wait_for_selector('input[placeholder="請輸入兌換碼"]', timeout=5000)
            except TimeoutError:
                return await _package_result(page, False, "登入失敗（未成功進入兌換頁） / Login failed (did not reach redeem page)", player_id, debug_logs, debug=debug)

            await page.fill('input[placeholder="請輸入兌換碼"]', code)

            for attempt in range(1, OCR_MAX_RETRIES + 1):
                try:
                    captcha_text, method_used = await _solve_captcha(page, attempt, player_id)
                    log_entry(attempt, captcha_text=captcha_text, method=method_used)

                    await page.fill('input[placeholder="請輸入驗證碼"]', captcha_text or "")

                    try:
                        await page.click(".exchange_btn", timeout=3000)
                        await page.wait_for_timeout(1000)

                        for _ in range(10):
                            modal = await page.query_selector(".message_modal")
                            if modal:
                                msg_el = await modal.query_selector("p.msg")
                                if msg_el:
                                    message = await msg_el.inner_text()
                                    log_entry(attempt, server_message=message)
                                    logger.info(f"[{player_id}] 第 {attempt} 次：伺服器回應：{message}")

                                    confirm_btn = await modal.query_selector(".confirm_btn")
                                    if confirm_btn and await confirm_btn.is_visible():
                                        await confirm_btn.click()
                                        await page.wait_for_timeout(500)

                                    if "驗證碼錯誤" in message or "驗證碼已過期" in message:
                                        await _refresh_captcha(page, player_id=player_id)
                                        break

                                    if any(k in message for k in FAILURE_KEYWORDS):
                                        return await _package_result(page, False, message, player_id, debug_logs, debug=debug)

                                    if "成功" in message:
                                        return await _package_result(page, True, message, player_id, debug_logs, debug=debug)

                                    return await _package_result(page, False, f"未知錯誤：{message}", player_id, debug_logs, debug=debug)

                            await page.wait_for_timeout(300)

                        else:
                            log_entry(attempt, server_message="未出現 modal 回應（點擊被遮蔽或失敗）")
                            await _refresh_captcha(page, player_id=player_id)
                            continue

                    except Exception as e:
                        log_entry(attempt, error=f"點擊或等待 modal 時失敗: {str(e)}")
                        await _refresh_captcha(page, player_id=player_id)
                        await page.wait_for_timeout(1000)
                        continue

                except Exception:
                    log_entry(attempt, error=traceback.format_exc())
                    await _refresh_captcha(page, player_id=player_id)
                    await page.wait_for_timeout(1000)

            log_entry(attempt, info="驗證碼三次辨識皆失敗，放棄兌換")
            logger.info(f"[{player_id}] 最終失敗：驗證碼三次辨識皆失敗 / Final failure: CAPTCHA failed 3 times")
            return await _package_result(page, False, "驗證碼三次辨識皆失敗，放棄兌換", player_id, debug_logs, debug=debug)

    except Exception as e:
        logger.exception(f"[{player_id}] 發生例外錯誤：{e}")
        html, img = None, None
        if debug:
            try:
                html = await page.content() if 'page' in locals() else "<no page>"
                img = await page.screenshot() if 'page' in locals() else None
            except:
                pass
        return {
            "player_id": player_id,
            "success": False,
            "reason": "例外錯誤",
            "debug_logs": debug_logs,
            "debug_html_base64": base64.b64encode(html.encode("utf-8")).decode() if html else None,
            "debug_img_base64": base64.b64encode(img).decode() if img else None
        }

    finally:
        if browser:
            await browser.close()

    return {
        "player_id": player_id,
        "success": False,
        "reason": "未知錯誤（流程未命中任何 return）",
        "debug_logs": debug_logs
    }

async def _solve_captcha(page, attempt, player_id):
    fallback_text = f"_try{attempt}"
    method_used = "none"
    def log_entry(attempt, **kwargs):
        entry = {"attempt": attempt}
        entry.update(kwargs)
        logger.info(f"[{player_id}] DebugLog: {entry}")

    try:
        captcha_img = await page.query_selector(".verify_pic")
        if not captcha_img:
            logger.info(f"[{player_id}] 第 {attempt} 次：未找到驗證碼圖片")
            return fallback_text, method_used

        await page.wait_for_timeout(500)
        try:
            captcha_bytes = await asyncio.wait_for(captcha_img.screenshot(), timeout=10)
        except Exception as e:
            logger.warning(f"[{player_id}] 第 {attempt} 次：captcha screenshot timeout 或錯誤 → {e}")
            return fallback_text, method_used

        # ✅ 圖片過小則自動刷新，避免 2Captcha 拒收
        if not captcha_bytes or len(captcha_bytes) < 100:
            logger.warning(f"[{player_id}] 第 {attempt} 次：驗證碼圖太小（{len(captcha_bytes) if captcha_bytes else 0} bytes），自動刷新")
            await _refresh_captcha(page, player_id=player_id)
            return fallback_text, method_used

        # 強化圖片 → base64 編碼
        b64_img = preprocess_image_for_2captcha(captcha_bytes)

        logger.info(f"[{player_id}] 第 {attempt} 次：使用 2Captcha 辨識")
        result = await solve_with_2captcha(b64_img)
        if result == "UNSOLVABLE":
            logger.warning(f"[{player_id}] 第 {attempt} 次：2Captcha 回傳無解 → 自動刷新圖")
            log_entry(attempt, info="2Captcha 回傳 UNSOLVABLE")
            await _refresh_captcha(page, player_id=player_id)
            return fallback_text, method_used

        if result:
            result = result.strip()
            if len(result) == 4 and result.isalnum():
                method_used = "2captcha"
                logger.info(f"[{player_id}] 第 {attempt} 次：2Captcha 成功辨識 → {result}")
                return result, method_used
            else:
                logger.warning(f"[{player_id}] 第 {attempt} 次：2Captcha 回傳長度不符（{len(result)}字 → {result}），強制刷新")
                await _refresh_captcha(page, player_id=player_id)
                return fallback_text, method_used

    except Exception as e:
        logger.exception(f"[{player_id}] 第 {attempt} 次：例外錯誤：{e}")
        return fallback_text, method_used

def preprocess_image_for_2captcha(img_bytes, scale=2.5):
    """轉灰階、二值化、放大並轉 base64 編碼"""
    img = Image.open(BytesIO(img_bytes)).convert("L")  # 灰階
    img = img.point(lambda x: 0 if x < 140 else 255, '1')  # 二值化
    new_size = (int(img.width * scale), int(img.height * scale))
    img = img.resize(new_size, Image.LANCZOS)
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("utf-8")

def _clean_ocr_text(text):
    """替換常見誤判字元並移除非字母數字"""
    corrections = {
        "0": "O", "1": "I", "5": "S", "8": "B", "$": "S", "6": "G",
        "l": "I", "|": "I", "2": "Z", "9": "g", "§": "S", "£": "E",
        "4": "A", "@": "A"
    }
    for wrong, correct in corrections.items():
        text = text.replace(wrong, correct)
    return ''.join(filter(str.isalnum, text))

def _save_debug_captcha_image(img_np, label, player_id, attempt):
    date_folder = f"debug/{datetime.now().strftime('%Y%m%d')}"
    os.makedirs(date_folder, exist_ok=True)
    filename = f"captcha_{player_id}_attempt{attempt}_{label}.png"
    Image.fromarray(img_np).save(os.path.join(date_folder, filename))


def _save_blank_captcha_image(player_id, attempt):
    date_folder = f"debug/{datetime.now().strftime('%Y%m%d')}"
    os.makedirs(date_folder, exist_ok=True)
    Image.new("RGB", (200, 50), "white").save(
        os.path.join(date_folder, f"captcha_{player_id}_attempt{attempt}_blank_none.png")
    )

CAPTCHA_API_KEY = os.getenv("CAPTCHA_API_KEY")
CAPTCHA_DAILY_LIMIT = 30
CAPTCHA_USAGE_FILE = "captcha_usage.txt"

def check_captcha_limit():
    today = datetime.now().strftime("%Y%m%d")
    try:
        with open(CAPTCHA_USAGE_FILE, "r+") as f:
            lines = f.readlines()
            if lines and lines[0].startswith(today):
                count = int(lines[0].strip().split(",")[1])
                if count >= CAPTCHA_DAILY_LIMIT:
                    return False
                f.seek(0)
                f.write(f"{today},{count+1}")
                f.truncate()
            else:
                f.seek(0)
                f.write(f"{today},1")
                f.truncate()
        return True
    except:
        with open(CAPTCHA_USAGE_FILE, "w") as f:
            f.write(f"{today},1")
        return True

async def solve_with_2captcha(b64_img):
    api_key = os.getenv("CAPTCHA_API_KEY")
    payload = {
        "key": api_key,
        "method": "base64",
        "body": b64_img,
        "json": 1,
        "numeric": 0,
        "min_len": 4,
        "max_len": 5,
        "language": 2
    }

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post("http://2captcha.com/in.php", data=payload) as resp:
                if resp.content_type != "application/json":
                    text = await resp.text()
                    logger.error(f"2Captcha 提交回傳非 JSON（{resp.status}）：{text}")
                    return None

                res = await resp.json()
                if res.get("status") != 1:
                    logger.warning(f"2Captcha 提交失敗：{res}")
                    return None

                request_id = res["request"]
        except Exception as e:
            logger.exception(f"提交 2Captcha 發生錯誤：{e}")
            return None

        # 等待辨識結果
        for _ in range(20):
            await asyncio.sleep(5)
            try:
                async with session.get(f"http://2captcha.com/res.php?key={api_key}&action=get&id={request_id}&json=1") as resp:
                    if resp.content_type != "application/json":
                        text = await resp.text()
                        logger.error(f"2Captcha 查詢回傳非 JSON（{resp.status}）：{text}")
                        return None

                    result = await resp.json()
                    if result.get("status") == 1:
                        return result.get("request")
                    if result.get("request") == "ERROR_CAPTCHA_UNSOLVABLE":
                        logger.warning(f"2Captcha 回傳無法解碼錯誤 → {result}")
                        return "UNSOLVABLE"
                    elif result.get("request") != "CAPCHA_NOT_READY":
                        logger.warning(f"2Captcha 回傳錯誤結果：{result}")
                        return None

            except Exception as e:
                logger.exception(f"查詢 2Captcha 結果發生錯誤：{e}")
                return None

    return None

async def _refresh_captcha(page, player_id=None):
    try:
        refresh_btn = await page.query_selector('.reload_btn')
        captcha_img = await page.query_selector('.verify_pic')
        if not refresh_btn or not captcha_img:
            logger.info(f"[{player_id}] 無法定位驗證碼圖片或刷新按鈕")
            return

        # 先確保 modal 已經關閉
        for _ in range(10):
            modal = await page.query_selector('.message_modal')
            if not modal:
                break
            confirm_btn = await modal.query_selector('.confirm_btn')
            if confirm_btn and await confirm_btn.is_visible():
                await confirm_btn.click()
            await page.wait_for_timeout(1000)

        try:
            original_bytes = await asyncio.wait_for(captcha_img.screenshot(), timeout=10)
        except Exception as e:
            logger.warning(f"[{player_id}] captcha 原圖 screenshot timeout 或錯誤 → {e} / original captcha screenshot timeout or error")
            return
        original_hash = hashlib.md5(original_bytes).hexdigest() if original_bytes else ""

        # 點擊刷新按鈕
        await refresh_btn.click()
        await page.wait_for_timeout(1500)

        # 處理 modal（如果彈出錯誤訊息）
        for _ in range(8):
            modal = await page.query_selector('.message_modal')
            if modal:
                msg_el = await modal.query_selector('p.msg')
                if msg_el:
                    msg_text = await msg_el.inner_text()
                    logger.info(f"[{player_id}] Captcha Modal：{msg_text.strip()}")
                    if any(k in msg_text for k in ["過於頻繁", "伺服器繁忙", "請稍後再試"]):
                        confirm_btn = await modal.query_selector('.confirm_btn')
                        if confirm_btn:
                            await confirm_btn.click()
                        await page.wait_for_timeout(1500)
                        return
            await page.wait_for_timeout(300)

        # 等待圖刷新
        for i in range(30):
            await page.wait_for_timeout(150)
            try:
                new_bytes = await asyncio.wait_for(captcha_img.screenshot(), timeout=10)
            except Exception as e:
                logger.warning(f"[{player_id}] captcha 新圖 screenshot timeout 或錯誤（第 {i+1} 次）→ {e}")
                continue

            if not new_bytes or len(new_bytes) < 1024:
                continue
            new_hash = hashlib.md5(new_bytes).hexdigest()
            if new_hash != original_hash:
                box = await captcha_img.bounding_box()
                if box and box["height"] > 10:
                    logger.info(f"[{player_id}] 成功刷新驗證碼 (hash 第 {i+1} 次變化)")
                    return
        else:
            logger.info(f"[{player_id}] 刷新失敗：圖片內容未更新 / Refresh failed: Captcha image did not update")

    except Exception as e:
        logger.info(f"[{player_id}] Captcha 刷新例外：{str(e)} / Refresh captcha exception: {str(e)}")

async def _package_result(page, success, message, player_id, debug_logs, debug=False):
    result = {
        "player_id": player_id,
        "success": success,
        "reason": message if not success else None,
        "message": message if success else None,
        "debug_logs": debug_logs
    }

    if debug and page:
        try:
            html = await page.content()
            screenshot = await page.screenshot()
            result["debug_html_base64"] = base64.b64encode(html.encode("utf-8")).decode("utf-8")
            result["debug_img_base64"] = base64.b64encode(screenshot).decode("utf-8")
        except Exception as e:
            result["debug_html_base64"] = None
            result["debug_img_base64"] = None
            debug_logs.append({"error": f"[{player_id}] 無法擷取 debug 畫面: {str(e)}"})
    return result

# === Flask API ===
@app.route("/add_id", methods=["POST"])
def add_id():
    try:
        data = request.json
        guild_id = data.get("guild_id")
        player_id = data.get("player_id")

        if not guild_id or not player_id:
            return jsonify({"success": False, "reason": "缺少 guild_id 或 player_id"}), 400

        async def fetch_name():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(locale="zh-TW")
                page = await context.new_page()

                name = "未知名稱"
                for attempt in range(3):
                    try:
                        await page.goto("https://wos-giftcode.centurygame.com/")
                        await page.fill('input[type="text"]', player_id)
                        await page.click(".login_btn")
                        await page.wait_for_selector('input[placeholder="請輸入兌換碼"]', timeout=5000)
                        await page.wait_for_selector(".name", timeout=8000)
                        name_el = await page.query_selector(".name")
                        name = await name_el.inner_text() if name_el else "未知名稱"
                        break
                    except:
                        await page.wait_for_timeout(1000 + attempt * 500)

                await browser.close()
                return name

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        player_name = loop.run_until_complete(fetch_name())

        # 🔍 若名稱不同才更新 Firestore
        ref = db.collection("ids").document(guild_id).collection("players").document(player_id)
        existing_doc = ref.get()
        existing_name = existing_doc.to_dict().get("name") if existing_doc.exists else None

        if existing_name != player_name:
            ref.set({
                "name": player_name,
                "updated_at": datetime.utcnow()
            }, merge=True)

        return jsonify({
            "success": True,
            "message": f"已新增或更新 {player_id} 至 guild {guild_id} / Added or updated to guild {guild_id}",
            "name": player_name
        })

    except Exception as e:
        return jsonify({"success": False, "reason": str(e)}), 500

@app.route("/list_ids", methods=["GET"])
def list_ids():
    try:
        guild_id = request.args.get("guild_id")
        if not guild_id:
            return jsonify({"success": False, "reason": "缺少 guild_id"}), 400

        docs = db.collection("ids").document(guild_id).collection("players").stream()
        players = [{"id": doc.id, **doc.to_dict()} for doc in docs]

        return jsonify({"success": True, "players": players})

    except Exception as e:
        return jsonify({"success": False, "reason": str(e)}), 500

@app.route("/redeem_submit", methods=["POST"])
def redeem_submit():
    data = request.json
    code = data.get("code")
    player_ids = data.get("player_ids")
    debug = data.get("debug", False)

    if not code or not player_ids:
        return jsonify({"success": False, "reason": "缺少 code 或 player_ids"}), 400

    if not isinstance(player_ids, list):
        return jsonify({"success": False, "reason": "player_ids 必須是列表"}), 400

    MAX_BATCH_SIZE = 5
    start_time = time.time()

    async def process_all():
        all_success = []
        all_fail = []
        final_failed_ids = []

        async def fetch_and_store_name(pid):
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(locale="zh-TW")
                page = await context.new_page()
                name = "未知名稱"
                for attempt in range(3):
                    try:
                        await page.goto("https://wos-giftcode.centurygame.com/")
                        await page.fill('input[type="text"]', pid)
                        await page.click(".login_btn")
                        await page.wait_for_selector('input[placeholder="請輸入兌換碼"]', timeout=5000)
                        await page.wait_for_selector(".name", timeout=5000)
                        name_el = await page.query_selector(".name")
                        name = await name_el.inner_text() if name_el else "未知名稱"
                        break
                    except:
                        await page.wait_for_timeout(1000 + attempt * 500)
                await browser.close()
                return name

        # 先查 Firestore 並補全缺失 ID
        doc_ref_base = db.collection("ids")
        loop = asyncio.get_event_loop()
        for pid in player_ids:
            doc_ref = doc_ref_base.document("global").collection("players").document(pid)
            if not doc_ref.get().exists:
                name = loop.run_until_complete(fetch_and_store_name(pid))
                doc_ref.set({
                    "name": name,
                    "updated_at": datetime.utcnow()
                }, merge=True)
                logger.info(f"[{pid}] 📌 自動新增至 Firestore：{name}")

        # 開始兌換處理
        for i in range(0, len(player_ids), MAX_BATCH_SIZE):
            batch = player_ids[i:i + MAX_BATCH_SIZE]
            tasks = [run_redeem_with_retry(pid, code, debug=debug) for pid in batch]
            results = await asyncio.gather(*tasks)
            await asyncio.sleep(1)
            for r in results:
                if r.get("success"):
                    all_success.append({
                        "player_id": r["player_id"],
                        "message": r.get("message")
                    })
                    logger.info(f"[{r['player_id']}] ✅ 成功：{r.get('message')}")
                else:
                    all_fail.append({
                        "player_id": r.get("player_id"),
                        "reason": r.get("reason"),
                        "debug_logs": r.get("debug_logs", []),
                        "debug_img_base64": r.get("debug_img_base64", None),
                        "debug_html_base64": r.get("debug_html_base64", None)
                    })
                    logger.warning(f"[{r['player_id']}] ❌ 失敗：{r.get('reason')}")

                    if "驗證碼三次辨識皆失敗" in (r.get("reason") or ""):
                        doc = doc_ref_base.document("global").collection("players").document(r["player_id"]).get()
                        name = doc.to_dict().get("name", "未知") if doc.exists else "未知"
                        final_failed_ids.append(f"{r['player_id']} ({name})")

                if r.get("reason") in ["驗證碼三次辨識皆失敗", "Timeout：單人兌換超過 90 秒"]:
                    doc = doc_ref_base.document("global").collection("players").document(r["player_id"]).get()
                    name = doc.to_dict().get("name", "未知") if doc.exists else "未知"
                    db.collection("failed_redeems").document(code).collection("players").document(r["player_id"]).set({
                        "name": name,
                        "reason": r.get("reason"),
                        "updated_at": datetime.utcnow()
                    })

        webhook_message = (
            f"🎁 處理完成：成功 {len(all_success)} 筆，失敗 {len(all_fail)} 筆\n"
            f"禮包碼：{code}\n"
        )
        if final_failed_ids:
            webhook_message += "⚠️ 三次辨識失敗的 ID（請改用/retry_failed）：\n" + "\n".join(final_failed_ids)
        else:
            webhook_message += "✅ 無任何 ID 出現三次辨識失敗 / No ID failed 3 times"

        webhook_message += f"\n⌛ 執行時間：約 {time.time() - start_time:.1f} 秒"

        if os.getenv("DISCORD_WEBHOOK_URL"):
            try:
                resp = requests.post(os.getenv("DISCORD_WEBHOOK_URL"), json={
                    "content": webhook_message
                })
                logger.info(f"Webhook 發送結果：{resp.status_code} {resp.text}")
            except Exception as e:
                logger.warning(f"Webhook 發送失敗：{e}")
        else:
            logger.warning("DISCORD_WEBHOOK_URL 未設定，跳過 webhook 發送")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(process_all())

    return jsonify({"message": "兌換已完成，Webhook 已送出（或已嘗試） / Redemption completed, webhook sent (or attempted)"}), 200

@app.route("/update_names_api", methods=["POST"])
def update_names_api():
    try:
        data = request.json
        guild_id = data.get("guild_id")
        if not guild_id:
            return jsonify({"success": False, "reason": "缺少 guild_id"}), 400

        player_ids = [doc.id for doc in db.collection("ids").document(guild_id).collection("players").stream()]
        updated = []

        async def fetch_all():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(locale="zh-TW")
                page = await context.new_page()

                for pid in player_ids:
                    name = "未知名稱"

                    for attempt in range(3):  # 最多重試 3 次
                        try:
                            await page.goto("https://wos-giftcode.centurygame.com/")
                            await page.fill('input[type="text"]', pid)
                            await page.click(".login_btn")
                            await page.wait_for_selector('input[placeholder="請輸入兌換碼"]', timeout=5000)
                            await page.wait_for_selector(".name", timeout=5000)
                            name_el = await page.query_selector(".name")
                            name = await name_el.inner_text() if name_el else "未知名稱"
                            break  # 有成功取得名稱就中止重試
                        except:
                            await page.wait_for_timeout(1000 + attempt * 500)

                    doc_ref = db.collection("ids").document(guild_id).collection("players").document(pid)
                    existing_doc = doc_ref.get()
                    existing_name = existing_doc.to_dict().get("name") if existing_doc.exists else None

                    if name != "未知名稱":
                        if existing_name != name or existing_name in [None, "未知名稱"]:
                            doc_ref.update({
                                "name": name,
                                "updated_at": datetime.utcnow()
                            })
                            updated.append({"player_id": pid, "name": name})
                    else:
                        logger.info(f"[{pid}] 保留原名稱（未更新）：{existing_name}")

                await browser.close()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(fetch_all())

        return jsonify({
            "success": True,
            "guild_id": guild_id,
            "updated": updated
        })

    except Exception as e:
        return jsonify({"success": False, "reason": str(e)}), 500

@app.route("/retry_failed", methods=["POST"])
def retry_failed():
    data = request.json
    code = data.get("code")
    debug = data.get("debug", False)

    if not code:
        return jsonify({"success": False, "reason": "缺少 code"}), 400

    doc_ref_base = db.collection("failed_redeems").document(code).collection("players")
    failed_docs = doc_ref_base.stream()
    player_ids = [doc.id for doc in failed_docs]

    if not player_ids:
        return jsonify({"success": False, "reason": f"找不到 failed_redeems 清單：{code}"}), 404

    # 呼叫現有流程
    try:
        payload = {
            "code": code,
            "player_ids": player_ids,
            "debug": debug
        }
        # 假設這段是呼叫本地內部 API（也可直接 call 內部函式）
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(process_redeem(payload))
        return jsonify({"success": True, "message": f"已針對 {len(player_ids)} 筆失敗紀錄重新兌換"}), 200
    except Exception as e:
        return jsonify({"success": False, "reason": str(e)}), 500


@app.route("/")
def health():
    return "Worker ready for redeeming!"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))  # Cloud Run 預設 PORT
    app.run(host="0.0.0.0", port=port)