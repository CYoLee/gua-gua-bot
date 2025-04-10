# main.py
import os
import json
from threading import Thread
from flask import Flask, request, jsonify
from redeem_bot import start_discord_bot
from redeem_web import process_redeem_code  # ⬅️ 你要另外建立這個檔案或模組

app = Flask(__name__)

@app.route("/")
def index():
    return "✅ GuaGuaBOT 正常啟動中"

@app.route("/redeem_submit", methods=["POST"])
def redeem_submit():
    try:
        data = request.get_json(force=True)
        code = data.get("code")
        ids = data.get("ids", [])
        batch_id = data.get("batch_id", "")

        if not code or not ids:
            return jsonify({"error": "code 與 ids 為必填"}), 400

        success, failure = [], []

        for player_id in ids:
            result = process_redeem_code(player_id, code)
            if result["success"]:
                success.append({"player_id": player_id})
            else:
                failure.append({
                    "player_id": player_id,
                    "reason": result.get("reason", "未知錯誤")
                })

        return jsonify({"success": success, "failure": failure})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    Thread(target=run_flask).start()
    start_discord_bot()
