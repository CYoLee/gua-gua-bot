# main.py
from flask import Flask, request, jsonify
from datetime import datetime
from firebase_admin import credentials, firestore, initialize_app
import os

# Firestore 初始化（只初始化一次）
if not firestore._apps:
    cred = credentials.ApplicationDefault()
    initialize_app(cred)
db = firestore.client()

app = Flask(__name__)


@app.route("/")
def index():
    return "✅ GuaGuaBOT Cloud Run Ready"


@app.route("/redeem_submit", methods=["POST"])
def redeem_submit():
    data = request.get_json()
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


@app.route("/notify_submit", methods=["POST"])
def notify_submit():
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

    return jsonify({"message": "Notify task created"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
