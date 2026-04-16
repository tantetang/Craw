"""One-time interactive login. Saves cookies for later use by the API client.

Usage:
    python -m shopee_tracker.session

Opens a real Chromium window. Log in manually (phone OTP, CAPTCHA, whatever
Shopee throws at you), then press Enter in the terminal. Cookies are dumped
to cookies.json in the current working directory.
"""

from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

COOKIE_FILE = Path("cookies.json")
LOGIN_URL = "https://shopee.vn/buyer/login"
HOME_URL = "https://shopee.vn/"


def run(cookie_file: Path = COOKIE_FILE) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context(
            locale="vi-VN",
            viewport={"width": 1366, "height": 820},
        )
        page = ctx.new_page()
        page.goto(LOGIN_URL)
        print("==> Đăng nhập Shopee trong cửa sổ trình duyệt.")
        print("    Khi đã thấy trang chủ có avatar của bạn, quay lại terminal")
        print("    và bấm Enter để lưu cookie.")
        input("    [Enter] khi đã đăng nhập xong: ")

        # Visit home to make sure all SPC_* cookies are set.
        page.goto(HOME_URL)
        page.wait_for_load_state("networkidle")

        cookies = ctx.cookies()
        cookie_file.write_text(
            json.dumps(cookies, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        has_user = any(c["name"] == "SPC_U" for c in cookies)
        print(f"Đã lưu {len(cookies)} cookie → {cookie_file}")
        if not has_user:
            print("CẢNH BÁO: không thấy cookie SPC_U — có vẻ bạn chưa login thành công.")
        browser.close()


if __name__ == "__main__":
    run()
