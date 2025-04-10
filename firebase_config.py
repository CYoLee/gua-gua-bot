# firebase_config.py
import os, json, firebase_admin
from firebase_admin import credentials

# 檢查是否已有 Firebase 應用啟動
if not firebase_admin._apps:
    key = os.environ.get("FIREBASE_KEY_JSON") or os.environ.get("FIREBASE_CREDENTIALS", "{}")
    try:
        cred_dict = json.loads(key)
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred)
    except Exception as e:
        print(f"❌ Firebase 初始化錯誤: {e}")
        raise RuntimeError("無法初始化 Firebase")
