# redeem_web.py
import asyncio
from flask import Flask, request, jsonify
from playwright.async_api import async_playwright, TimeoutError
import os
import json
import base64
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

FAILURE_KEYWORDS = [
    "請先輸入", "不存在", "伺服器繁忙", "錯誤", "無效", "超出", "領取", "類型", "已使用"
]

async def run_redeem(player_id, code):
    browser = None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--disable-gpu"])
            context = await browser.new_context(locale="zh-TW")
            page = await context.new_page()
            await page.goto("https://wos-giftcode.centurygame.com/", timeout=60000)
            await page.fill('input[type="text"]', player_id)

            try:
                await page.wait_for_selector(".login_btn", timeout=15000)
                await page.click(".login_btn")
            except TimeoutError:
                return {"player_id": player_id, "success": False, "reason": "登入按鈕未出現"}

            await page.wait_for_timeout(2000)
            await page.fill('input[placeholder="請輸入兌換碼"]', code)

            try:
                await page.wait_for_selector(".exchange_btn", timeout=15000)
                await page.click(".exchange_btn")
            except TimeoutError:
                return {"player_id": player_id, "success": False, "reason": "兌換按鈕未出現"}

            try:
                await page.wait_for_selector("p.msg", timeout=5000)
                message = await page.locator("p.msg").inner_text()
            except TimeoutError:
                return {"player_id": player_id, "success": False, "reason": "未出現兌換回覆訊息"}

            if any(keyword in message for keyword in FAILURE_KEYWORDS):
                return {"player_id": player_id, "success": False, "reason": message}
            else:
                return {"player_id": player_id, "success": True, "message": message}
    except Exception as e:
        return {"player_id": player_id, "success": False, "reason": f"例外錯誤: {str(e)}"}
    finally:
        if browser:
            await browser.close()

@app.route("/redeem_submit", methods=["POST"])
def redeem_submit():
    data = request.json
    code = data.get("code")
    player_id = data.get("player_id")
    guild_id = data.get("guild_id")

    if not code:
        return jsonify({"success": False, "reason": "缺少 code"}), 400

    async def async_main():
        if player_id:
            # ✅ 單人模式：有 guild_id 就補進 Firestore
            if guild_id:
                player_ref = db.collection("ids").document(guild_id).collection("players").document(player_id)
                if not player_ref.get().exists:
                    player_ref.set({"auto_added": True})

            result = await run_redeem(player_id, code)
            return result

        if not guild_id:
            return {"success": False, "reason": "多人兌換需提供 guild_id"}

        players_ref = db.collection("ids").document(guild_id).collection("players")
        docs = players_ref.stream()
        player_ids = [doc.id for doc in docs]

        if not player_ids:
            return {"success": False, "reason": "此 guild_id 下沒有任何 player_id"}

        # ✅ 分批 async 執行，每批最多 5 人
        batch_size = 5
        results = []
        for i in range(0, len(player_ids), batch_size):
            batch = player_ids[i:i + batch_size]
            tasks = [run_redeem(pid, code) for pid in batch]
            results.extend(await asyncio.gather(*tasks))

        # 統計結果
        success_count = 0
        fail_count = 0
        fail_details = []

        for result in results:
            if result.get("success"):
                success_count += 1
            else:
                fail_count += 1
                if len(fail_details) < 10:
                    fail_details.append({
                        "player_id": result.get("player_id"),
                        "reason": result.get("reason", "未知錯誤")
                    })

        return {
            "message": f"兌換完成，成功 {success_count} 筆，失敗 {fail_count} 筆",
            "fails": fail_details
        }


    # 用 nest_asyncio 兼容 Flask 同步框架
    import nest_asyncio
    nest_asyncio.apply()
    asyncio.set_event_loop(asyncio.new_event_loop())
    result = asyncio.run(async_main())
    return jsonify(result)

@app.route("/add_id", methods=["POST"])
def add_id():
    data = request.json
    guild_id = data.get("guild_id")
    player_id = data.get("player_id")

    if not guild_id or not player_id:
        return jsonify({"success": False, "reason": "缺少 guild_id 或 player_id"}), 400

    db.collection("ids").document(guild_id).collection("players").document(player_id).set({"added_by": "api"})
    return jsonify({"success": True, "message": f"已新增 {player_id} 至 guild {guild_id}"}), 200

@app.route("/remove_id", methods=["POST"])
def remove_id():
    data = request.json
    guild_id = data.get("guild_id")
    player_id = data.get("player_id")

    if not guild_id or not player_id:
        return jsonify({"success": False, "reason": "缺少 guild_id 或 player_id"}), 400

    db.collection("ids").document(guild_id).collection("players").document(player_id).delete()
    return jsonify({"success": True, "message": f"已移除 {player_id} 從 guild {guild_id}"}), 200

@app.route("/list_ids", methods=["GET"])
def list_ids():
    guild_id = request.args.get("guild_id")
    if not guild_id:
        return jsonify({"success": False, "reason": "請提供 guild_id"}), 400

    players_ref = db.collection("ids").document(guild_id).collection("players")
    docs = players_ref.stream()
    player_ids = [doc.id for doc in docs]

    return jsonify({"guild_id": guild_id, "player_ids": player_ids, "count": len(player_ids)})

@app.route("/")
def health():
    return "Worker ready for redeeming!"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
