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

from .proxy import for_playwright, load_proxy
from .stealth import STEALTH_LAUNCH_ARGS, STEALTH_USER_AGENT, apply_stealth, warm_up

COOKIE_FILE = Path("cookies.json")
LOGIN_URL = "https://shopee.vn/buyer/login"
HOME_URL = "https://shopee.vn/"


def run(cookie_file: Path = COOKIE_FILE) -> None:
    proxy = load_proxy()
    pw_proxy = for_playwright(proxy)
    if pw_proxy:
        print(f"[session] Dùng proxy: {proxy.display()}")

    with sync_playwright() as p:
        launch_kwargs: dict = {
            "headless": False,
            "args": STEALTH_LAUNCH_ARGS,
        }
        if pw_proxy:
            launch_kwargs["proxy"] = pw_proxy
        browser = p.chromium.launch(**launch_kwargs)
        ctx = browser.new_context(
            locale="vi-VN",
            timezone_id="Asia/Ho_Chi_Minh",
            viewport={"width": 1366, "height": 820},
            user_agent=STEALTH_USER_AGENT,
        )
        apply_stealth(ctx)
        page = ctx.new_page()
        # Warm-up: vào home trước rồi mới login → giảm flag bot
        warm_up(page, "https://shopee.vn/")
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
