# redeem_worker_cloud.py

import os
import json
import asyncio
import pytesseract
from datetime import datetime
from google.cloud import storage
from firebase_admin import firestore, credentials, initialize_app
from playwright.async_api import async_playwright
from dotenv import load_dotenv
from PIL import Image

load_dotenv()

# Firebase 初始化
if not firestore._apps:
    cred_json = os.environ.get("FIREBASE_CREDENTIALS", "{}")
    cred_dict = json.loads(
        cred_json
        if isinstance(cred_json, str)
        else os.environ.get("FIREBASE_KEY_JSON", "{}")
    )
    cred = credentials.Certificate(cred_dict)
    initialize_app(cred)

db = firestore.client()
storage_client = storage.Client()
bucket_name = os.environ.get("FIREBASE_STORAGE_BUCKET")

async def process_task(doc_id, data):
    code = data.get("code")
    player_id = data.get("player_id")
    task_ref = db.collection("redeem_tasks").document(doc_id)

    if not code or not player_id:
        await task_ref.update({"status": "error", "reason": "缺少 code 或 player_id"})
        return

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()

            await page.goto("https://wos-giftcode.centurygame.com/")
            await page.fill("#playerId", player_id)
            await page.click("button.btn-confirm")
            await page.wait_for_selector("img.avatar", timeout=5000)

            await page.fill("#giftCode", code)
            await page.click("button.btn-submit")
            await page.wait_for_timeout(2500)

            screenshot_path = f"/tmp/{player_id}_{code}.png"
            await page.screenshot(path=screenshot_path)
            await browser.close()

            text = pytesseract.image_to_string(Image.open(screenshot_path), lang="chi_tra")
            reason = "未知錯誤"
            success_keywords = ["成功", "已發送", "領取成功"]
            failure_keywords = {
                "已使用": "已兌換",
                "無效": "無效代碼",
                "不存在": "代碼錯誤",
                "伺服器": "伺服器錯誤",
                "角色": "角色錯誤"
            }

            result = "success" if any(k in text for k in success_keywords) else "failure"
            for key, val in failure_keywords.items():
                if key in text:
                    reason = val
                    break

            # 上傳圖片到 Firebase Storage
            if bucket_name:
                bucket = storage_client.bucket(bucket_name)
                blob = bucket.blob(f"redeem_screenshots/{os.path.basename(screenshot_path)}")
                blob.upload_from_filename(screenshot_path)
                blob.make_public()
                image_url = blob.public_url
            else:
                image_url = ""

            await task_ref.update({
                "status": result,
                "reason": reason if result == "failure" else "success",
                "image_url": image_url,
                "updated_at": datetime.utcnow()
            })

    except Exception as e:
        await task_ref.update({"status": "error", "reason": str(e)})

async def main():
    while True:
        try:
            query = db.collection("redeem_tasks").where("status", "==", "pending").limit(1)
            docs = query.stream()
            tasks = []
            for doc in docs:
                tasks.append(process_task(doc.id, doc.to_dict()))
            if tasks:
                await asyncio.gather(*tasks)
            else:
                await asyncio.sleep(5)
        except Exception as e:
            print(f"❌ Worker error: {e}")
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main())
