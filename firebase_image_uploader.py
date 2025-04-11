import os
from google.cloud import storage
from datetime import datetime
import uuid

# 初始化 Firebase Storage 用戶端
storage_client = storage.Client()
bucket_name = os.environ.get("FIREBASE_STORAGE_BUCKET")  # 例如：gua-guabot.appspot.com
bucket = storage_client.bucket(bucket_name)

def upload_image_to_firebase(local_path: str, user_id: str = "default"):
    try:
        # 建立唯一檔名
        filename = f"{user_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.png"
        blob = bucket.blob(f"screenshots/{filename}")

        # 上傳檔案
        blob.upload_from_filename(local_path)

        # 設為公開讀取
        blob.make_public()

        return blob.public_url
    except Exception as e:
        print(f"❌ 上傳圖片失敗: {e}")
        return ""
