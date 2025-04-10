# firebase_config.py

import os
import json
import base64
import firebase_admin
from firebase_admin import credentials, initialize_app

if not firebase_admin._apps:
    try:
        if "FIREBASE_KEY_BASE64" in os.environ:
            key_json = base64.b64decode(os.environ["FIREBASE_KEY_BASE64"]).decode("utf-8")
        else:
            key_json = os.environ.get("FIREBASE_KEY_JSON") or os.environ.get("FIREBASE_CREDENTIALS", "{}")

        cred_dict = json.loads(key_json)
        cred = credentials.Certificate(cred_dict)
        initialize_app(cred)

    except Exception as e:
        print(f"❌ Firebase 初始化錯誤: {e}")
        raise RuntimeError("無法初始化 Firebase")
