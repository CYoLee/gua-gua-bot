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

async def run_redeem_with_retry(player_id, code, debug=False):  # 加 debug
    debug_logs = []
    for redeem_retry in range(REDEEM_RETRIES + 1):
        result = await _redeem_once(player_id, code, debug_logs, redeem_retry, debug=debug)  # 傳入 debug
        if not result:
            result = {"success": False, "reason": "無回應", "debug_logs": debug_logs}
        if result.get("reason", "").startswith("_try"):
            # 若 OCR fallback 都失敗，直接放棄，不再 retry
            return result
        if result.get("success"):
            return result
        reason = result.get("reason", "")
        
        # 若是登入問題，不應 retry
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

            try:
                await page.wait_for_selector(".message_modal", timeout=5000)
                modal_text = await page.inner_text(".message_modal .msg")
                log_entry(0, error_modal=modal_text)
                if any(k in modal_text for k in ["請先輸入", "不存在", "錯誤", "無效", "伺服器繁忙"]):
                    logger.info(f"[{player_id}] 登入失敗：{modal_text}")
                    return await _package_result(page, False, f"登入失敗：{modal_text}", player_id, debug_logs)
            except TimeoutError:
                try:
                    await page.wait_for_selector(".name", timeout=5000)
                except TimeoutError:
                    if debug:
                        html = await page.content()
                        img = await page.screenshot()
                        return await _package_result(page, False, "登入失敗（未成功登入也未出現錯誤提示）", player_id, debug_logs, html, img)
                    return await _package_result(page, False, "登入失敗（未成功登入也未出現錯誤提示）", player_id, debug_logs)

            await page.fill('input[placeholder="請輸入兌換碼"]', code)

            for attempt in range(1, OCR_MAX_RETRIES + 1):
                try:
                    captcha_text, method_used = await _solve_captcha(page, attempt, player_id)
                    log_entry(attempt, captcha_text=captcha_text, method=method_used)

                    if not captcha_text or len(captcha_text.strip()) < 4:
                        log_entry(attempt, info=f"辨識過短（長度={len(captcha_text.strip())}），強制送出")

                    await page.fill('input[placeholder="請輸入驗證碼"]', captcha_text)

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
                                        await _refresh_captcha(page)
                                        break

                                    if any(k in message for k in FAILURE_KEYWORDS):
                                        return await _package_result(page, False, message, player_id, debug_logs)

                                    if "成功" in message:
                                        return await _package_result(page, True, message, player_id, debug_logs, debug=debug)

                                    return await _package_result(page, False, f"未知錯誤：{message}", player_id, debug_logs, debug=debug)

                            await page.wait_for_timeout(300)

                        else:
                            log_entry(attempt, server_message="未出現 modal 回應（點擊被遮蔽或失敗）")
                            await _refresh_captcha(page)
                            continue

                    except Exception as e:
                        log_entry(attempt, error=f"點擊或等待 modal 時失敗: {str(e)}")
                        await _refresh_captcha(page)
                        await page.wait_for_timeout(1000)
                        continue

                except Exception:
                    log_entry(attempt, error=traceback.format_exc())
                    await _refresh_captcha(page)
                    await page.wait_for_timeout(1000)

            log_entry(attempt, info="驗證碼三次辨識皆失敗，放棄兌換")
            logger.info(f"[{player_id}] 最終失敗：驗證碼三次辨識皆失敗")
            return await _package_result(page, False, "驗證碼三次辨識皆失敗，放棄兌換", player_id, debug_logs, debug=debug)

    except Exception:
        html, img = None, None
        if debug:
            try:
                html = await page.content() if 'page' in locals() else "<no page>"
                img = await page.screenshot() if 'page' in locals() else None
            except:
                pass
        return {"player_id": player_id, "success": False, "reason": "例外錯誤", "debug_logs": debug_logs,
                "debug_html_base64": base64.b64encode(html.encode("utf-8")).decode() if html else None,
                "debug_img_base64": base64.b64encode(img).decode() if img else None}

    finally:
        if browser:
            await browser.close()

async def _solve_captcha(page, attempt, player_id):
    fallback_text = f"_try{attempt}"
    method_used = "none"

    try:
        captcha_img = await page.query_selector(".verify_pic")
        if not captcha_img:
            logger.info(f"[{player_id}] 第 {attempt} 次：未找到驗證碼圖片")
            return fallback_text, method_used

        await page.wait_for_timeout(500)
        captcha_bytes = await captcha_img.screenshot()

        if not captcha_bytes or len(captcha_bytes) < 1024:
            logger.info(f"[{player_id}] 第 {attempt} 次：圖片資料不足，長度={len(captcha_bytes) if captcha_bytes else 0}")
            return fallback_text, method_used

        # 強化圖片 → base64 編碼
        b64_img = preprocess_image_for_2captcha(captcha_bytes)

        logger.info(f"[{player_id}] 第 {attempt} 次：使用 2Captcha 辨識")
        result = await solve_with_2captcha(b64_img)

        if result:
            method_used = "2captcha"
            logger.info(f"[{player_id}] 第 {attempt} 次：2Captcha 成功辨識 → {result}")
            return result, method_used
        else:
            logger.info(f"[{player_id}] 第 {attempt} 次：2Captcha 辨識失敗，回傳 None")
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
        async with session.post("http://2captcha.com/in.php", data=payload) as resp:
            res = await resp.json()
            if res.get("status") != 1:
                return None
            request_id = res["request"]

        # 等待結果
        for _ in range(20):
            await asyncio.sleep(5)
            async with session.get(f"http://2captcha.com/res.php?key={api_key}&action=get&id={request_id}&json=1") as resp:
                result = await resp.json()
                if result.get("status") == 1:
                    return result.get("request")
                if result.get("request") != "CAPCHA_NOT_READY":
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
            await page.wait_for_timeout(300)

        original_bytes = await captcha_img.screenshot()
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
            new_bytes = await captcha_img.screenshot()
            if not new_bytes or len(new_bytes) < 1024:
                continue
            new_hash = hashlib.md5(new_bytes).hexdigest()
            if new_hash != original_hash:
                box = await captcha_img.bounding_box()
                if box and box["height"] > 10:
                    logger.info(f"[{player_id}] 成功刷新驗證碼 (hash 第 {i+1} 次變化)")
                    return
        else:
            logger.info(f"[{player_id}] 刷新失敗：圖片內容未更新")

    except Exception as e:
        logger.info(f"[{player_id}] Captcha 刷新例外：{str(e)}")

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
            "message": f"已新增或更新 {player_id} 至 guild {guild_id}",
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

    MAX_BATCH_SIZE = 1

    async def process_all():
        all_success = []
        all_fail = []

        for i in range(0, len(player_ids), MAX_BATCH_SIZE):
            batch = player_ids[i:i + MAX_BATCH_SIZE]
            tasks = [run_redeem_with_retry(pid, code, debug=debug) for pid in batch]
            results = await asyncio.gather(*tasks)

            for r in results:
                if r.get("success"):
                    all_success.append({"player_id": r["player_id"], "message": r.get("message")})
                else:
                    all_fail.append({
                        "player_id": r.get("player_id"),
                        "reason": r.get("reason"),
                        "debug_logs": r.get("debug_logs", []),
                        "debug_img_base64": r.get("debug_img_base64", None),
                        "debug_html_base64": r.get("debug_html_base64", None)
                    })

        return {
            "success": all_success,
            "fails": all_fail,
            "message": f"兌換完成，共成功 {len(all_success)} 筆，失敗 {len(all_fail)} 筆"
        }

    asyncio.set_event_loop(asyncio.new_event_loop())
    result = asyncio.run(process_all())

    return jsonify(result)

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
                            await page.wait_for_selector(".name", timeout=8000)
                            name_el = await page.query_selector(".name")
                            name = await name_el.inner_text() if name_el else "未知名稱"
                            break  # 有成功取得名稱就中止重試
                        except:
                            await page.wait_for_timeout(1000 + attempt * 500)

                    doc_ref = db.collection("ids").document(guild_id).collection("players").document(pid)
                    existing_doc = doc_ref.get()
                    existing_name = existing_doc.to_dict().get("name") if existing_doc.exists else None

                    if existing_name != name:
                        doc_ref.update({
                            "name": name,
                            "updated_at": datetime.utcnow()
                        })
                        updated.append({"player_id": pid, "name": name})

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

@app.route("/")
def health():
    return "Worker ready for redeeming!"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))  # Cloud Run 預設 PORT
    app.run(host="0.0.0.0", port=port)