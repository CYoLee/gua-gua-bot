# main.py
from flask import Flask, request, jsonify
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime
import pytz
import os

# Initialize Firebase
if not firebase_admin._apps:
    cred = credentials.ApplicationDefault()
    firebase_admin.initialize_app(cred)
db = firestore.client()

app = Flask(__name__)
TIMEZONE = pytz.timezone("Asia/Taipei")


@app.route("/")
def index():
    return "✅ GuaGuaBOT Cloud Run Ready"


@app.route("/redeem_submit", methods=["POST"])
def redeem_submit():
    try:
        data = request.get_json()
        code = data["code"]
        ids = data["ids"]
        batch_id = data.get("batch_id", "default")
        timestamp = datetime.now().astimezone(TIMEZONE)

        for pid in ids:
            db.collection("redeem_logs").add(
                {
                    "code": code,
                    "player_id": pid,
                    "batch_id": batch_id,
                    "timestamp": firestore.SERVER_TIMESTAMP,
                    "datetime": timestamp.isoformat(),
                    "result": "success",
                    "reason": "",
                }
            )

        return jsonify({"status": "ok", "processed": len(ids)})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/notify_submit", methods=["POST"])
def notify_submit():
    try:
        data = request.get_json()
        date = data["date"]
        time = data["time"]
        message = data["message"]
        channel_id = data["channel_id"]
        mention = data.get("mention", "")
        guild_id = data.get("guild_id", "default")

        dt = TIMEZONE.localize(datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M"))
        db.collection("notifications").add(
            {
                "guild_id": str(guild_id),
                "channel_id": channel_id,
                "datetime": dt,
                "mention": mention,
                "message": message,
            }
        )

        return jsonify({"status": "ok", "datetime": dt.isoformat()})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
