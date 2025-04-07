# main.py
from flask import Flask, request, jsonify
from datetime import datetime
from firebase_admin import credentials, firestore, initialize_app
import os

app = Flask(__name__)

# Firebase 初始化
if not firestore._apps:
    cred = credentials.ApplicationDefault()
    initialize_app(cred)
db = firestore.client()


@app.route("/")
def index():
    return "GuaGuaBOT API is live"


@app.route("/redeem_submit", methods=["POST"])
def redeem_submit():
    data = request.get_json()
    code = data.get("code")
    ids = data.get("ids", [])
    timestamp = datetime.now().isoformat()

    if not code or not ids:
        return jsonify({"error": "Missing code or ids"}), 400

    results = []
    for pid in ids:
        result = {
            "player_id": pid,
            "code": code,
            "result": "success",  # 模擬成功
            "reason": "",
            "datetime": timestamp,
        }
        # 🔐 寫入 Firestore
        db.collection("redeem_logs").add(result)
        results.append(result)

    return jsonify({"results": results})


@app.route("/add_notify", methods=["POST"])
def add_notify():
    data = request.get_json()
    guild_id = str(data.get("guild_id"))
    dt_str = data.get("datetime")
    message = data.get("message", "")
    channel_id = data.get("channel_id")
    mention = data.get("mention", "")

    try:
        dt = datetime.fromisoformat(dt_str)
    except Exception:
        return jsonify({"error": "Invalid datetime format"}), 400

    db.collection("notifications").add(
        {
            "guild_id": guild_id,
            "datetime": dt,
            "message": message,
            "mention": mention,
            "channel_id": channel_id,
        }
    )

    return jsonify({"status": "ok", "guild_id": guild_id})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
