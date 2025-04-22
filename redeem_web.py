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
    "請先輸入", "不存在", "伺服器繁忙", "錯誤", "無效", "超出", "無法", "類型", "已使用"]
RETRY_KEYWORDS = ["伺服器繁忙", "請稍後再試", "系統異常", "請重試", "處理中"]

async def get_nickname_by_id(player_id: str) -> str:
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(locale="zh-TW")
            page = await context.new_page()
            await page.goto("https://wos-giftcode.centurygame.com/", timeout=60000)

            await page.fill('input[type="text"]', player_id)
            await page.wait_for_selector(".login_btn", timeout=30000)
            await page.click(".login_btn")
            await page.wait_for_timeout(2000)

            nickname_locator = page.locator(".name")
            if await nickname_locator.count() > 0:
                return await nickname_locator.inner_text()
            return ""
    except Exception as e:
        print(f"[get_nickname_by_id] 取得名稱失敗: {e}")
        return ""

async def run_redeem_with_retry(player_id, code, max_retries=2):
    for attempt in range(max_retries + 1):
        result = await _redeem_once(player_id, code)
        reason = result.get("reason", "")
        if result.get("success"):
            return result
        if not any(keyword in reason for keyword in RETRY_KEYWORDS):
            return result
        if attempt < max_retries:
            print(f"[Retry] 嘗試重試 {player_id} 第 {attempt + 1} 次：{reason}")
            await asyncio.sleep(2 + attempt)
    return result

async def _redeem_once(player_id, code):
    browser = None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--disable-gpu"])
            context = await browser.new_context(locale="zh-TW")
            page = await context.new_page()
            await page.goto("https://wos-giftcode.centurygame.com/", timeout=60000)
            await page.fill('input[type="text"]', player_id)

            try:
                await page.wait_for_selector(".login_btn", timeout=30000)
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

            if "成功" in message:  # 假設當頁面顯示"成功"時才會算為成功
                return {"player_id": player_id, "success": True, "message": message}
            else:
                return {"player_id": player_id, "success": False, "reason": "未知錯誤"}
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
            # ✅ 單人模式：補進 Firestore（如有 guild_id）
            if guild_id:
                player_ref = db.collection("ids").document(guild_id).collection("players").document(player_id)
                doc = player_ref.get()
                if not doc.exists:
                    nickname = await get_nickname_by_id(player_id)
                    player_ref.set({
                        "auto_added": True,
                        "name": nickname
                    })
                else:
                    data = doc.to_dict()
                    if "name" not in data or not data["name"]:
                        nickname = await get_nickname_by_id(player_id)
                        player_ref.update({"name": nickname})

            result = await run_redeem_with_retry(player_id, code)
            return {
                "message": "兌換完成（單人）",
                "success": [result] if result.get("success") else [],
                "fails": [] if result.get("success") else [result]
            }

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
            print(f"[Batch] 開始處理第 {i//batch_size + 1} 批，共 {len(batch)} 人")

            try:
                # 設定 timeout 為 60 秒，避免卡住
                batch_results = await asyncio.wait_for(
                    asyncio.gather(*[run_redeem_with_retry(pid, code) for pid in batch]),
                    timeout=60
                )
                print(f"[Batch] 第 {i//batch_size + 1} 批完成")
                print(f"[Batch] 第 {i//batch_size + 1} 批成功 {sum(1 for r in batch_results if r['success'])} 人")
                results.extend(batch_results)
            except asyncio.TimeoutError:
                print(f"[Batch] 第 {i//batch_size + 1} 批執行超時，略過該批")
                for pid in batch:
                    results.append({
                        "player_id": pid,
                        "success": False,
                        "reason": "批次執行超時"
                    })
        # 整理成功與失敗清單
        success_details = []
        fail_details = []

        for result in results:
            pid = result.get("player_id", "未知ID")
            if result.get("success"):
                success_details.append({
                    "player_id": pid,
                    "message": result.get("message", "兌換成功")
                })
            else:
                fail_details.append({
                    "player_id": pid,
                    "reason": result.get("reason", "未知錯誤")
                })

        return {
            "message": f"兌換完成，成功 {len(success_details)} 筆，失敗 {len(fail_details)} 筆",
            "success": success_details,
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

    async def process_add():
        doc_ref = db.collection("ids").document(guild_id).collection("players").document(player_id)
        doc = doc_ref.get()
        if doc.exists:
            data = doc.to_dict()
            if "name" not in data or not data["name"]:
                nickname = await get_nickname_by_id(player_id)
                doc_ref.update({"name": nickname})
        else:
            nickname = await get_nickname_by_id(player_id)
            doc_ref.set({
                "player_id": player_id,
                "name": nickname
            })

    import nest_asyncio
    nest_asyncio.apply()
    asyncio.set_event_loop(asyncio.new_event_loop())
    asyncio.run(process_add())
    
    return jsonify({"success": True, "message": f"已新增或更新 {player_id} 至 guild {guild_id}"}), 200

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
    players = [{"id": doc.id, "name": doc.to_dict().get("name", "")} for doc in docs]

    return jsonify({"guild_id": guild_id, "players": players, "count": len(players)})

@app.route("/fix_missing_names", methods=["POST"])
def fix_missing_names():
    data = request.json
    guild_id = data.get("guild_id")
    if not guild_id:
        return jsonify({"success": False, "reason": "缺少 guild_id"}), 400

    async def fix_names():
        players_ref = db.collection("ids").document(guild_id).collection("players")
        docs = list(players_ref.stream())
        updated = 0
        for doc in docs:
            pid = doc.id
            info = doc.to_dict()
            nickname = await get_nickname_by_id(pid)
            if not nickname:
                continue
            if "name" not in info or not info["name"]:
                players_ref.document(pid).update({"name": nickname})
                updated += 1
            elif info["name"] != nickname:
                players_ref.document(pid).update({"name": nickname})
                updated += 1
        return updated

    import nest_asyncio
    nest_asyncio.apply()
    asyncio.set_event_loop(asyncio.new_event_loop())
    updated_count = asyncio.run(fix_names())
    return jsonify({"success": True, "updated": updated_count})

@app.route("/")
def health():
    return "Worker ready for redeeming!"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
