"""Fallback client that issues Shopee API calls from inside a real Chromium.

Uses `page.evaluate(fetch)` so every request carries the browser's real
cookies, TLS fingerprint, and any short-lived tokens Shopee injects
(`af-ac-enc-*`, `SZ-TOKEN`) — things that are hard to reproduce outside
a browser.

The same method signatures as `ShopeeClient` so `track_shop()` can swap
clients without changes.
"""

from __future__ import annotations

import json
import random
import time
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlencode

from .client import COOKIE_FILE, ShopeeAPIError
from .proxy import ProxyConfig, for_playwright, load_proxy
from .stealth import STEALTH_LAUNCH_ARGS, STEALTH_USER_AGENT, apply_stealth, warm_up

BASE = "https://shopee.vn"
DEFAULT_PROFILE_DIR = Path("./chrome_profile")

_FETCH_SCRIPT = """
async (url) => {
    const r = await fetch(url, {
        headers: {
            'x-api-source': 'pc',
            'x-shopee-language': 'vi',
            'x-requested-with': 'XMLHttpRequest',
            'Accept': 'application/json',
        },
        credentials: 'include',
    });
    const body = await r.text();
    return { status: r.status, body };
}
"""


class PlaywrightShopeeClient:
    def __init__(
        self,
        user_data_dir: Path = DEFAULT_PROFILE_DIR,
        cookie_file: Path | None = COOKIE_FILE,
        headless: bool = True,
        min_delay: float = 1.0,
        max_delay: float = 2.5,
        proxy: ProxyConfig | None = None,
    ) -> None:
        from playwright.sync_api import sync_playwright  # lazy

        self.min_delay = min_delay
        self.max_delay = max_delay
        if proxy is None:
            proxy = load_proxy()
        pw_proxy = for_playwright(proxy)
        if pw_proxy:
            print(f"[PlaywrightClient] Dùng proxy: {proxy.display()}")

        self._pw = sync_playwright().start()
        launch_kwargs: dict[str, Any] = {
            "user_data_dir": str(user_data_dir),
            "headless": headless,
            "locale": "vi-VN",
            "timezone_id": "Asia/Ho_Chi_Minh",
            "viewport": {"width": 1366, "height": 820},
            "user_agent": STEALTH_USER_AGENT,
            "args": STEALTH_LAUNCH_ARGS,
        }
        if pw_proxy:
            launch_kwargs["proxy"] = pw_proxy
        self._context = self._pw.chromium.launch_persistent_context(**launch_kwargs)
        apply_stealth(self._context)

        # Seed cookies from cookies.json if persistent profile is empty.
        if cookie_file and cookie_file.exists():
            try:
                cookies_data = json.loads(cookie_file.read_text(encoding="utf-8"))
                self._context.add_cookies(cookies_data)
            except Exception as e:
                print(f"[PlaywrightClient] Không thể seed cookies: {e}")

        self._page = (
            self._context.pages[0] if self._context.pages else self._context.new_page()
        )

        # Warm up: visit home + scroll giả user thật để qua anti-bot check.
        warm_up(self._page, f"{BASE}/")
        self._current_referer_url: str = f"{BASE}/"

    def close(self) -> None:
        try:
            self._context.close()
        finally:
            self._pw.stop()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _sleep(self) -> None:
        time.sleep(random.uniform(self.min_delay, self.max_delay))

    def _ensure_on(self, url: str) -> None:
        if self._current_referer_url == url:
            return
        try:
            self._page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        except Exception as e:
            print(f"[PlaywrightClient] goto({url}) lỗi: {e}")
        self._current_referer_url = url

    def _fetch_json(self, path: str, params: dict[str, Any]) -> dict:
        url = f"{BASE}{path}?{urlencode(params)}"
        result = self._page.evaluate(_FETCH_SCRIPT, url)
        status = result.get("status")
        body = result.get("body") or ""
        if status == 429:
            raise ShopeeAPIError(f"Rate-limited (429) tại {path}")
        if status and status >= 400:
            raise ShopeeAPIError(f"HTTP {status} tại {path}: {body[:200]}")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as e:
            raise ShopeeAPIError(
                f"Response không phải JSON ({status}): {body[:200]}"
            ) from e
        if payload.get("error"):
            raise ShopeeAPIError(
                f"Shopee error={payload.get('error')} msg={payload.get('error_msg')}"
            )
        return payload

    def shop_detail(
        self,
        username: str | None = None,
        shopid: str | None = None,
    ) -> dict:
        if not username and not shopid:
            raise ValueError("Cần ít nhất username hoặc shopid")
        referer = f"{BASE}/{username}" if username else f"{BASE}/shop/{shopid}"
        self._ensure_on(referer)
        params: dict[str, Any] = {}
        if username:
            params["username"] = username
        if shopid:
            params["shopid"] = shopid
        return self._fetch_json("/api/v4/shop/get_shop_detail", params)

    def shop_products_page(
        self,
        shopid: str | int,
        limit: int = 30,
        offset: int = 0,
        sort_by: str = "pop",
        referer_username: str | None = None,
    ) -> dict:
        referer = (
            f"{BASE}/{referer_username}"
            if referer_username
            else f"{BASE}/shop/{shopid}"
        )
        self._ensure_on(referer)
        params = {
            "shopid": shopid,
            "limit": limit,
            "offset": offset,
            "order": "desc",
            "sort_by": sort_by,
        }
        return self._fetch_json("/api/v4/shop/search_items", params)

    def iter_shop_products(
        self,
        shopid: str | int,
        max_items: int = 200,
        page_size: int = 30,
        referer_username: str | None = None,
    ) -> Iterator[dict]:
        offset = 0
        fetched = 0
        first = True
        while fetched < max_items:
            if not first:
                self._sleep()
            first = False
            data = self.shop_products_page(
                shopid=shopid,
                limit=page_size,
                offset=offset,
                referer_username=referer_username,
            )
            batch = data.get("items") or []
            if not batch:
                return
            for item in batch:
                yield item
                fetched += 1
                if fetched >= max_items:
                    return
            if len(batch) < page_size:
                return
            offset += page_size
