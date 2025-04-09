# main.py
import os
import requests
from flask import Flask, request, jsonify
from datetime import datetime
import firebase_admin
from firebase_admin import credentials, firestore

# 初始化 Firebase
cred_env = os.environ.get("FIREBASE_CREDENTIALS")
cred_json = json.loads(cred_env)

if cred_json.get("type") != "service_account":
    raise RuntimeError("❌ FIREBASE_CREDENTIALS 缺少正確欄位")


# 環境變數設定
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

# Flask 應用
app = Flask(__name__)


@app.route("/")
def index():
    return "✅ GuaGuaBOT Cloud Run Ready"


@app.route("/redeem_submit", methods=["POST"])
def redeem_submit():
    try:
        data = request.get_json(force=True)
        code = data.get("code")
        ids = data.get("ids", [])
        batch_id = data.get("batch_id", "default")

        if not code or not isinstance(ids, list):
            return jsonify({"error": "Invalid payload"}), 400

        timestamp = datetime.utcnow().isoformat()
        result = []

        for pid in ids:
            doc = {
                "player_id": pid,
                "code": code,
                "batch_id": batch_id,
                "timestamp": timestamp,
                "result": "simulated_success",
            }
            db.collection("redeem_logs").add(doc)
            result.append({"player_id": pid, "status": "ok"})

        return jsonify({"message": "Submitted", "result": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/notify_submit", methods=["POST"])
def notify_submit():
    try:
        data = request.get_json()
        message = data.get("message")
        channel_id = data.get("channel_id")
        guild_id = data.get("guild_id")
        remind_at = data.get("datetime")

        if not all([message, channel_id, guild_id, remind_at]):
            return jsonify({"error": "Missing fields"}), 400

        db.collection("notifications").add(
            {
                "message": message,
                "channel_id": channel_id,
                "guild_id": guild_id,
                "datetime": datetime.fromisoformat(remind_at),
            }
        )

        # 發送 Discord Webhook
        if DISCORD_WEBHOOK_URL:
            try:
                resp = requests.post(
                    DISCORD_WEBHOOK_URL, json={"content": message}, timeout=5
                )
                if resp.status_code != 204:
                    print(f"⚠️ Webhook failed: {resp.status_code} {resp.text}")
            except Exception as e:
                print(f"❌ Webhook error: {e}")

        return jsonify({"message": "Notify task created"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
