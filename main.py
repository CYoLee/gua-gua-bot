# main.py
import os
import json
from threading import Thread
from flask import Flask, request, jsonify
from redeem_bot import start_discord_bot

app = Flask(__name__)


@app.route("/")
def index():
    return "✅ GuaGuaBOT 正常啟動中"


@app.route("/redeem_submit", methods=["POST"])
def redeem_submit():
    data = request.get_json(force=True)
    code = data.get("code")
    ids = data.get("ids", [])
    batch_id = data.get("batch_id")

    if not code or not ids:
        return jsonify({"error": "Missing code or ids"}), 400

    # 呼叫 Discord Bot 兌換處理模組
    try:
        # 模擬回應邏輯（這裡你可自行對接 Firebase 或其他系統）
        success = [{"player_id": pid} for pid in ids if pid.startswith("1")]
        failure = [{"player_id": pid, "reason": "ID格式錯誤"} for pid in ids if not pid.startswith("1")]

        return jsonify({"success": success, "failure": failure})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    Thread(target=run_flask).start()
    start_discord_bot()
