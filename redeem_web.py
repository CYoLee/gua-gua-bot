# redeem_web.py
from playwright.sync_api import sync_playwright

def process_redeem_code(player_id: str, code: str) -> dict:
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto("https://[原本兌換網站URL]")

            # Step 1: 輸入 ID 並確認
            page.fill('input[name="player_id"]', player_id)
            page.click('button#query-id')
            page.wait_for_timeout(2000)  # 或使用 page.wait_for_selector

            # Step 2: 輸入兌換碼
            page.fill('input[name="redeem_code"]', code)
            page.click('button#submit-code')
            page.wait_for_timeout(2000)

            # Step 3: 根據結果文字判斷成功與否
            result_text = page.inner_text('#result-message')

            browser.close()

            if "兌換成功" in result_text or "已使用過" in result_text:
                return {"success": True}
            else:
                return {"success": False, "reason": result_text}

    except Exception as e:
        return {"success": False, "reason": str(e)}

