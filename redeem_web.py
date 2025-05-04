import asyncio
import base64
import json
import os
import io
import traceback
import hashlib
import requests
import time

from flask import Flask, request, jsonify
from playwright.async_api import async_playwright, TimeoutError
from dotenv import load_dotenv
import firebase_admin
from firebase_admin import credentials, firestore
from PIL import Image
import cv2
import numpy as np
import pytesseract
import nest_asyncio
from datetime import datetime
import easyocr

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
REDEEM_RETRIES = 9
# === 主流程 ===

async def run_redeem_with_retry(player_id, code, max_retries=REDEEM_RETRIES):
    debug_logs = []
    for redeem_retry in range(max_retries + 1):
        result = await _redeem_once(player_id, code, debug_logs, redeem_retry)
        if not result:
            result = {"success": False, "reason": "無回應", "debug_logs": debug_logs}
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


async def _redeem_once(player_id, code, debug_logs, redeem_retry):
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
                await page.wait_for_selector(".name", timeout=8000)
            except TimeoutError:
                return await _package_result(page, False, "登入失敗，角色名稱未出現", player_id, debug_logs)

            await page.fill('input[placeholder="請輸入兌換碼"]', code)

            for attempt in range(1, OCR_MAX_RETRIES + 1):
                try:
                    captcha_text, method_used = await _solve_captcha(page, attempt, player_id)
                    log_entry(attempt, captcha_text=captcha_text, method=method_used)
                    if not captcha_text or len(captcha_text.strip()) < 4:
                        log_entry(attempt, info=f"辨識過短（長度={len(captcha_text.strip())}），強制送出")

                    await page.fill('input[placeholder="請輸入驗證碼"]', captcha_text)

                    # 主動點擊並偵測是否被 modal 擋住
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

                                    # 點確認按鈕
                                    confirm_btn = await modal.query_selector(".confirm_btn")
                                    if confirm_btn and await confirm_btn.is_visible():
                                        await confirm_btn.click()
                                        await page.wait_for_timeout(500)

                                    if "驗證碼錯誤" in message or "驗證碼已過期" in message:
                                        await _refresh_captcha(page)
                                        break  # 繼續下一次 OCR attempt

                                    if any(k in message for k in FAILURE_KEYWORDS):
                                        return await _package_result(page, False, message, player_id, debug_logs)

                                    if "成功" in message:
                                        return await _package_result(page, True, message, player_id, debug_logs)

                                    return await _package_result(page, False, f"未知錯誤：{message}", player_id, debug_logs)

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

            return await _package_result(page, False, f"OCR辨識失敗超過{OCR_MAX_RETRIES}次", player_id, debug_logs)

    except Exception:
        return {"player_id": player_id, "success": False, "reason": "例外錯誤", "debug_logs": debug_logs}
    finally:
        if browser:
            await browser.close()

async def _solve_captcha(page, attempt, player_id):
    global reader
    fallback_text = f"_try{attempt}"
    captcha_bytes = None
    final_text = ""
    method_used = "none"

    try:
        captcha_img = await page.query_selector(".verify_pic")
        if not captcha_img:
            return fallback_text, method_used

        src = await captcha_img.get_attribute("src")
        if not src or "data:image" not in src:
            return fallback_text, method_used

        await page.wait_for_timeout(800)

        for _ in range(10):
            try:
                box = await captcha_img.bounding_box()
                if box and box["height"] > 10:
                    captcha_bytes = await captcha_img.screenshot()
                    if captcha_bytes and len(captcha_bytes) > 1024:
                        break
            except:
                pass
            await page.wait_for_timeout(300)

        if not captcha_bytes:
            if DEBUG_MODE:
                _save_blank_captcha_image(player_id, attempt)
            return fallback_text, method_used

        # Image preprocessing
        img = Image.open(io.BytesIO(captcha_bytes)).convert("L")
        img = img.resize((img.width * 3, img.height * 3), Image.LANCZOS)
        img_np = np.array(img)
        img_np = cv2.copyMakeBorder(img_np, 2, 2, 2, 2, cv2.BORDER_CONSTANT, value=255)
        img_np = cv2.adaptiveThreshold(img_np, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 15, 10)
        img_np = cv2.medianBlur(img_np, 3)
        kernel = np.ones((2, 2), np.uint8)
        img_np = cv2.morphologyEx(img_np, cv2.MORPH_OPEN, kernel)
        img_np = cv2.filter2D(img_np, -1, np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]]))

        def clean_text(text):
            corrections = {
                "0": "O", "1": "I", "5": "S", "8": "B", "$": "S", "6": "G",
                "l": "I", "|": "I", "2": "Z", "9": "g", "§": "S", "£": "E",
                "4": "A", "@": "A"
            }
            for wrong, correct in corrections.items():
                text = text.replace(wrong, correct)
            return ''.join(filter(str.isalnum, text.strip().replace(" ", "").replace("\n", "")))

        # Tesseract
        text_tesseract = clean_text(pytesseract.image_to_string(Image.fromarray(img_np), lang="eng", config=OCR_CONFIG))

        # EasyOCR fallback
        text_easyocr = ""
        if USE_EASYOCR and (not text_tesseract or len(text_tesseract) < 4):
            if reader is None:
                reader = easyocr.Reader(["en"], gpu=False)
            result = reader.readtext(img_np, detail=0)
            text_easyocr = clean_text(''.join(result))

        # Choose the better one
        if text_easyocr and len(text_easyocr) >= 4:
            final_text = text_easyocr
            method_used = "easyocr"
        elif text_tesseract and len(text_tesseract) >= 4:
            final_text = text_tesseract
            method_used = "tesseract"

        if DEBUG_MODE:
            label = f"{final_text or fallback_text}_{method_used}"
            _save_debug_captcha_image(img_np, label, player_id, attempt)
            print(f"[Captcha] 辨識完成（使用：{method_used}）：{final_text or fallback_text}")

        # 2Captcha fallback
        if not final_text:
            print(f"[Captcha] 嘗試使用 2Captcha（第 {attempt} 次）")
            result = solve_with_2captcha(captcha_bytes)
            if result:
                print(f"[Captcha] 2Captcha 成功：{result}")
                final_text = result
                method_used = "2captcha"
            else:
                print("[Captcha] 2Captcha 辨識失敗")
                return fallback_text, method_used

        if DEBUG_MODE:
            print(f"[Captcha] 最終驗證碼：{final_text} (辨識方式：{method_used})")

        return final_text, method_used

    except Exception:
        if DEBUG_MODE and captcha_bytes:
            date_folder = f"debug/{datetime.now().strftime('%Y%m%d')}"
            os.makedirs(date_folder, exist_ok=True)
            with open(f"{date_folder}/captcha_{player_id}_attempt{attempt}_error.png", "wb") as f:
                f.write(captcha_bytes)
        return fallback_text, method_used

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

def solve_with_2captcha(image_bytes):
    if not CAPTCHA_API_KEY or not check_captcha_limit():
        return None

    base64_img = base64.b64encode(image_bytes).decode("utf-8")
    resp = requests.post("http://2captcha.com/in.php", data={
        "key": CAPTCHA_API_KEY,
        "method": "base64",
        "body": base64_img,
        "json": 1
    }).json()

    if resp.get("status") != 1:
        return None

    captcha_id = resp.get("request")
    for _ in range(20):
        time.sleep(5)
        result = requests.get(f"http://2captcha.com/res.php?key={CAPTCHA_API_KEY}&action=get&id={captcha_id}&json=1").json()
        if result.get("status") == 1:
            return result.get("request")
        elif result.get("request") == "CAPCHA_NOT_READY":
            continue
        else:
            return None
    return None

async def _refresh_captcha(page):
    try:
        refresh_btn = await page.query_selector('.reload_btn')
        captcha_img = await page.query_selector('.verify_pic')
        if not refresh_btn or not captcha_img:
            if DEBUG_MODE:
                print("[Captcha] 找不到刷新按鈕或驗證碼圖片")
            return

        # 儲存原圖 base64 hash（更準確判斷圖片是否變更）
        original_bytes = await captcha_img.screenshot()
        original_hash = hashlib.md5(original_bytes).hexdigest() if original_bytes else ""

        await refresh_btn.click()
        await page.wait_for_timeout(1200)

        # 若 modal 跳出，先處理掉
        for _ in range(8):
            modal = await page.query_selector('.message_modal')
            if modal:
                msg_el = await modal.query_selector('p.msg')
                if msg_el:
                    msg_text = await msg_el.inner_text()
                    if DEBUG_MODE:
                        print(f"[Captcha Modal] 訊息出現：{msg_text.strip()}")
                    if any(k in msg_text for k in ["過於頻繁", "伺服器繁忙", "請稍後再試"]):
                        confirm_btn = await modal.query_selector('.confirm_btn')
                        if confirm_btn:
                            await confirm_btn.click()
                        await page.wait_for_timeout(1500)
                        return
            await page.wait_for_timeout(300)

        # 嘗試最多 30 次判斷圖是否真的更新（比較圖片 hash + bounding box）
        for i in range(30):
            await page.wait_for_timeout(150)

            new_bytes = await captcha_img.screenshot()
            if not new_bytes or len(new_bytes) < 1024:
                continue
            new_hash = hashlib.md5(new_bytes).hexdigest()
            if new_hash != original_hash:
                box = await captcha_img.bounding_box()
                if box and box["height"] > 10:
                    if DEBUG_MODE:
                        print(f"[Captcha] 成功刷新 (hash 比對不同，第 {i+1} 次)")
                    return
        else:
            if DEBUG_MODE:
                print("[Captcha] 刷新失敗：圖片內容或位置未更新")

    except Exception as e:
        if DEBUG_MODE:
            print(f"[Captcha Refresh Error] {str(e)}")

async def _package_result(page, success, message, player_id, debug_logs):
    try:
        if DEBUG_MODE:
            date_folder = f"debug/{datetime.now().strftime('%Y%m%d')}"
            os.makedirs(date_folder, exist_ok=True)
            html = await page.content()
            screenshot = await page.screenshot()
            with open(f"{date_folder}/debug.html", "w", encoding="utf-8") as f:
                f.write(html)
            with open(f"{date_folder}/debug.png", "wb") as f:
                f.write(screenshot)
        return {
            "player_id": player_id,
            "success": success,
            "reason": message if not success else None,
            "message": message if success else None,
            "debug_logs": debug_logs
        }
    except Exception:
        return {
            "player_id": player_id,
            "success": success,
            "reason": message,
            "debug_logs": debug_logs
        }
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

@app.route("/redeem_submit", methods=["POST"])
def redeem_submit():
    data = request.json
    code = data.get("code")
    player_id = data.get("player_id")
    if not code or not player_id:
        return jsonify({"success": False, "reason": "缺少 code 或 player_id"}), 400

    async def async_main():
        return await run_redeem_with_retry(player_id, code)

    asyncio.set_event_loop(asyncio.new_event_loop())
    result = asyncio.run(async_main())
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
    app.run(host="0.0.0.0", port=8080)
