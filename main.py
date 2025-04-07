from flask import Flask, jsonify, request
import os
import firebase_admin
from firebase_admin import credentials, firestore

app = Flask(__name__)

# Optional: Load service account (for local dev or specific permission needs)
if not firebase_admin._apps:
    try:
        cred_path = "service_account.json"
        if os.path.exists(cred_path):
            cred = credentials.Certificate(cred_path)
            firebase_admin.initialize_app(cred)
        else:
            firebase_admin.initialize_app()  # Use default credentials (Cloud Run)
        db = firestore.client()
    except Exception as e:
        print(f"Firestore init error: {e}")
        db = None


@app.route("/")
def index():
    return "✅ GuaGuaBOT Cloud Run Ready"


@app.route("/healthz")
def health():
    return jsonify({"status": "ok"}), 200


@app.route("/firestore_test", methods=["POST"])
def firestore_test():
    if not db:
        return jsonify({"error": "Firestore unavailable"}), 500
    data = request.json or {}
    doc_ref = db.collection("test_collection").document()
    doc_ref.set({"message": data.get("message", "default"), "source": "cloud_run_test"})
    return jsonify({"result": "success", "id": doc_ref.id})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
