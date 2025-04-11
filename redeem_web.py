import os
import uuid
from firebase_admin import storage
from playwright.sync_api import sync_playwright
from pytesseract import image_to_string
from PIL import Image

# 建立 screenshots 資料夾（Cloud Run 只能用 /tmp）
TMP_DIR = "/tmp/screenshots"
os.makedirs(TMP_DIR, exist_ok=True)

def process_redeem_code(player_id, code):
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            page.goto("https://example.com/redeem")  # 替換成實際兌換網址

            # 模擬輸入資料（可依實際需求修改）
            page.fill("#player_id", player_id)
            page.fill("#code", code)
            page.click("#submit")

            page.wait_for_timeout(3000)  # 等待畫面載入結果

            # 截圖存檔
            file_name = f"{player_id}_{uuid.uuid4().hex}.png"
            file_path = os.path.join(TMP_DIR, file_name)
            page.screenshot(path=file_path)
            browser.close()

        # OCR 分析圖檔（可選）
        result_text = image_to_string(Image.open(file_path), lang="chi_tra+eng")

        # 判斷成功/失敗（根據 OCR 結果或頁面狀態）
        is_success = "成功" in result_text or "已領取" in result_text

        # 上傳至 Firebase Storage
        bucket = storage.bucket()
        blob = bucket.blob(f"screenshots/{file_name}")
        blob.upload_from_filename(file_path)
        blob.make_public()
        image_url = blob.public_url

        return {
            "success": is_success,
            "reason": "OCR判斷為失敗" if not is_success else "",
            "screenshot": image_url
        }

    except Exception as e:
        return {
            "success": False,
            "reason": f"例外錯誤: {e}",
            "screenshot": ""
        }
