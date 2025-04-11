import os
import uuid
from firebase_admin import storage

def upload_image_to_storage(local_path: str) -> str:
    """
    上傳圖片至 Firebase Storage screenshots 資料夾，回傳公開網址
    """
    # 產生唯一檔名
    filename = f"{uuid.uuid4().hex}.png"
    blob = storage.bucket().blob(f"screenshots/{filename}")
    blob.upload_from_filename(local_path)
    blob.make_public()
    return blob.public_url
