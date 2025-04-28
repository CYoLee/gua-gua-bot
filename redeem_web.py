import asyncio
from flask import Flask, request, jsonify
from playwright.async_api import async_playwright, TimeoutError
import pytesseract
import base64
import io
import os
import json
import firebase_admin
from firebase_admin import credentials, firestore
from dotenv import load_dotenv

app = Flask(__name__)

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

FAILURE_KEYWORDS = ["請先輸入", "不存在", "伺服器繁忙", "錯誤", "無效", "超出", "無法", "類型", "已使用"]
RETRY_KEYWORDS = ["伺服器繁忙", "請稍後再試", "系統異常", "請重試", "處理中"]

# === 核心兌換流程 ===
async def redeem_code(player_id: str, code: str):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-gpu"])
        context = await browser.new_context(locale="zh-TW")
        page = await context.new_page()

        for attempt in range(3):  # 最多重試3次
            try:
                print(f"[{player_id}] 第 {attempt+1} 次嘗試")

                await page.goto("https://wos-giftcode.centurygame.com/", timeout=60000)
                await page.fill('input[placeholder="請輸入角色ID"]', player_id)
                await page.click(".login_btn")
                await page.wait_for_timeout(2000)  # 等待登入完成

                await page.fill('input[placeholder="請輸入兌換碼"]', code)

                # 取得驗證碼圖片並OCR辨識
                captcha_element = await page.query_selector("div.verify_pic_con img")
                captcha_src = await captcha_element.get_attribute("src")

                if not captcha_src or "base64," not in captcha_src:
                    return {"success": False, "reason": "驗證碼圖片解析失敗"}

                b64_data = captcha_src.split("base64,")[1]
                image_bytes = base64.b64decode(b64_data)
                captcha_text = pytesseract.image_to_string(io.BytesIO(image_bytes), config="--psm 7").strip()
                captcha_text = captcha_text.replace(" ", "").replace("\n", "")
                print(f"[{player_id}] OCR辨識驗證碼：{captcha_text}")

                if not captcha_text:
                    print(f"[{player_id}] OCR辨識為空，刷新重試")
                    continue  # 重新整理頁面重試

                await page.fill('input[placeholder="請輸入驗證碼"]', captcha_text)
                await page.click(".exchange_btn")

                try:
                    await page.wait_for_selector("p.msg", timeout=5000)
                    message = await page.locator("p.msg").inner_text()
                    print(f"[{player_id}] 頁面回覆：{message}")

                    # 成功判斷
                    if "成功" in message or "已經兌換" in message or "已領取" in message:
                        await browser.close()
                        return {"success": True, "message": message}

                    # 驗證碼錯誤或過期，重試
                    if "驗證碼" in message or "請輸入正確" in message:
                        print(f"[{player_id}] 驗證碼錯誤或過期，刷新重試")
                        continue

                    # 其他失敗
                    if any(k in message for k in FAILURE_KEYWORDS):
                        await browser.close()
                        return {"success": False, "reason": message}

                    await browser.close()
                    return {"success": False, "reason": "未知錯誤：" + message}

                except TimeoutError:
                    print(f"[{player_id}] 等待兌換結果超時，刷新重試")
                    continue  # timeout也重新整理

            except Exception as e:
                print(f"[{player_id}] 例外錯誤：{e}")
                continue

        await browser.close()
        return {"success": False, "reason": "三次重試後仍失敗"}

# === API: 單人兌換用
@app.route("/redeem_submit", methods=["POST"])
def redeem_submit():
    data = request.json
    guild_id = data.get("guild_id")
    player_id = data.get("player_id")
    code = data.get("code")

    if not player_id or not code:
        return jsonify({"success": False, "reason": "缺少 player_id 或 code"}), 400

    import nest_asyncio
    nest_asyncio.apply()
    asyncio.set_event_loop(asyncio.new_event_loop())

    result = asyncio.run(redeem_code(player_id, code))

    if result.get("success"):
        return jsonify({
            "message": "兌換完成（單人）",
            "success": [result],
            "fails": []
        })
    else:
        return jsonify({
            "message": "兌換完成（單人）",
            "success": [],
            "fails": [{
                "player_id": player_id,
                "reason": result.get("reason")
            }]
        })

# === Health Check
@app.route("/")
def index():
    return "GuaGua Redeem Server Ready."

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
