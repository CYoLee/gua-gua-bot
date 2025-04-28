#redeem_web.py
import base64
import io
import asyncio
from fastapi import FastAPI
from playwright.async_api import async_playwright
import pytesseract

app = FastAPI()

@app.post("/redeem_submit")
async def redeem_submit(guild_id: str, player_id: str, code: str):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        try:
            for attempt in range(3):  # 最多重試3次
                await page.goto("https://wos-giftcode.centurygame.com/")
                
                # 1. 輸入 Player ID
                await page.fill('input[placeholder="請輸入角色ID"]', player_id)
                await page.click('text=登入')
                
                # 2. 等待頁面載入並填入兌換碼
                await page.wait_for_selector('input[placeholder="請輸入兌換碼"]', timeout=5000)
                await page.fill('input[placeholder="請輸入兌換碼"]', code)

                # 3. OCR 讀取驗證碼
                captcha_element = await page.query_selector("div.verify_pic_con img")
                captcha_src = await captcha_element.get_attribute("src")
                if not captcha_src or "base64," not in captcha_src:
                    return {"success": False, "error": "無法取得驗證碼圖片"}

                b64_data = captcha_src.split("base64,")[1]
                image_bytes = base64.b64decode(b64_data)
                captcha_text = pytesseract.image_to_string(io.BytesIO(image_bytes), config="--psm 7").strip()

                print(f"[Debug] OCR辨識驗證碼：{captcha_text}")

                await page.fill('input[placeholder="請輸入驗證碼"]', captcha_text)

                # 4. 送出兌換
                await page.click('text=確認兌換')

                # 5. 等待結果
                try:
                    await page.wait_for_selector("div.dialog-content", timeout=5000)
                    result_text = await page.inner_text("div.dialog-content")

                    print(f"[Debug] 兌換結果：{result_text}")

                    # 成功或明確錯誤都直接回傳
                    if "成功" in result_text or "已經兌換" in result_text or "已領取" in result_text:
                        await browser.close()
                        return {"success": True, "message": result_text}
                    elif "驗證碼錯誤" in result_text or "驗證碼已過期" in result_text:
                        print("[Debug] 驗證碼錯誤或過期，重新嘗試...")
                        continue  # 自動重試
                    else:
                        await browser.close()
                        return {"success": False, "message": result_text}

                except Exception as e:
                    print(f"[Debug] 等待結果超時：{e}")
                    continue  # 沒等到視窗也重試

            # 如果三次都失敗
            await browser.close()
            return {"success": False, "error": "驗證碼辨識失敗或其他問題，請稍後再試"}

        except Exception as e:
            await browser.close()
            return {"success": False, "error": str(e)}
