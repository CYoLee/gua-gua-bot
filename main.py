# main.py
from flask import Flask, request, jsonify
from firebase_admin import credentials, firestore, initialize_app
from datetime import datetime
import os

app = Flask(__name__)

# Initialize Firebase
if not firestore._apps:
    cred = credentials.ApplicationDefault()
    initialize_app(cred)
db = firestore.client()


@app.route("/", methods=["POST"])
def handle_redeem():
    try:
        data = request.get_json()
        code = data.get("code")
        player_id = data.get("id")

        if not code or not player_id:
            return jsonify({"error": "Missing code or id"}), 400

        log_data = {
            "code": code,
            "player_id": player_id,
            "result": "received",
            "datetime": datetime.utcnow().isoformat(),
            "source": "cloud_run_flask",
        }
        db.collection("redeem_logs").add(log_data)
        return jsonify({"status": "ok", "message": "Logged to Firestore."})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True)
