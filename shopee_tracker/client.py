"""Shopee internal API client.

Uses curl_cffi to impersonate a real Chrome TLS fingerprint, plus cookies
exported by shopee_tracker.session (login flow) so that endpoints gated
behind authentication still return data.
"""

from __future__ import annotations

import json
import random
import time
from pathlib import Path
from typing import Any

from .proxy import ProxyConfig, for_curl, load_proxy

COOKIE_FILE = Path("cookies.json")
BASE = "https://shopee.vn"
DEFAULT_IMPERSONATE = "chrome124"


class ShopeeAPIError(RuntimeError):
    pass


class ShopeeClient:
    def __init__(
        self,
        cookie_file: Path = COOKIE_FILE,
        impersonate: str = DEFAULT_IMPERSONATE,
        min_delay: float = 1.8,
        max_delay: float = 4.2,
        proxy: ProxyConfig | None = None,
    ) -> None:
        from curl_cffi import requests  # lazy: chỉ cần khi thực sự gọi mạng

        self.min_delay = min_delay
        self.max_delay = max_delay
        self.session = requests.Session(impersonate=impersonate)
        self.session.headers.update(
            {
                "x-api-source": "pc",
                "x-shopee-language": "vi",
                "x-requested-with": "XMLHttpRequest",
                "Referer": f"{BASE}/",
                "Accept": "application/json",
            }
        )
        if proxy is None:
            proxy = load_proxy()
        self._proxies = for_curl(proxy)
        if self._proxies:
            print(f"[ShopeeClient] Dùng proxy: {proxy.display()}")
        self._load_cookies(cookie_file)

    def close(self) -> None:
        try:
            self.session.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _load_cookies(self, cookie_file: Path) -> None:
        if not cookie_file.exists():
            print(
                f"[!] {cookie_file} không tồn tại. Các endpoint yêu cầu đăng nhập sẽ lỗi.\n"
                f"    Chạy: python -m shopee_tracker.session"
            )
            return
        data = json.loads(cookie_file.read_text(encoding="utf-8"))
        for c in data:
            domain = c.get("domain") or ".shopee.vn"
            self.session.cookies.set(c["name"], c["value"], domain=domain)

    def _sleep(self) -> None:
        time.sleep(random.uniform(self.min_delay, self.max_delay))

    def _get(self, path: str, params: dict[str, Any], referer: str | None = None) -> dict:
        headers = {}
        if referer:
            headers["Referer"] = referer
        r = self.session.get(
            f"{BASE}{path}",
            params=params,
            headers=headers,
            timeout=20,
            proxies=self._proxies,
        )
        if r.status_code == 429:
            raise ShopeeAPIError(f"Rate-limited (429) tại {path}. Nghỉ rồi thử lại.")
        if r.status_code >= 400:
            raise ShopeeAPIError(f"HTTP {r.status_code} tại {path}: {r.text[:200]}")
        try:
            payload = r.json()
        except Exception as e:
            raise ShopeeAPIError(f"Response không phải JSON: {e}") from e
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
        params: dict[str, Any] = {}
        if username:
            params["username"] = username
        if shopid:
            params["shopid"] = shopid
        referer = f"{BASE}/{username}" if username else f"{BASE}/shop/{shopid}"
        return self._get("/api/v4/shop/get_shop_detail", params, referer=referer)

    def shop_products_page(
        self,
        shopid: str | int,
        limit: int = 30,
        offset: int = 0,
        sort_by: str = "pop",
        referer_username: str | None = None,
    ) -> dict:
        params = {
            "shopid": shopid,
            "limit": limit,
            "offset": offset,
            "order": "desc",
            "sort_by": sort_by,
        }
        referer = (
            f"{BASE}/{referer_username}"
            if referer_username
            else f"{BASE}/shop/{shopid}"
        )
        return self._get("/api/v4/shop/search_items", params, referer=referer)

    def iter_shop_products(
        self,
        shopid: str | int,
        max_items: int = 200,
        page_size: int = 30,
        referer_username: str | None = None,
    ):
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


# --- Hybrid (curl → Playwright fallback) ---------------------------------


# Substrings in ShopeeAPIError messages that suggest Shopee is blocking
# curl_cffi specifically, and that a real browser is worth trying.
_BLOCK_SIGNALS = (
    "HTTP 403",
    "HTTP 429",
    "Rate-limited",
    "không phải JSON",
    "captcha",
    "CAPTCHA",
)


def _looks_blocked(err: ShopeeAPIError) -> bool:
    s = str(err)
    return any(sig in s for sig in _BLOCK_SIGNALS)


class HybridClient:
    """Try curl_cffi first; on block-like errors, switch to Playwright and stay.

    Keeps the same public methods as ShopeeClient so tracker code is agnostic.
    """

    def __init__(
        self,
        cookie_file: Path = COOKIE_FILE,
        user_data_dir: Path | None = None,
        headless: bool = True,
        proxy: ProxyConfig | None = None,
    ) -> None:
        if proxy is None:
            proxy = load_proxy()
        self._proxy = proxy
        self._curl = ShopeeClient(cookie_file=cookie_file, proxy=proxy)
        self._pw = None  # lazy
        self._blocked = False
        self._cookie_file = cookie_file
        self._user_data_dir = user_data_dir
        self._headless = headless

    def _get_pw(self):
        if self._pw is None:
            from .playwright_client import DEFAULT_PROFILE_DIR, PlaywrightShopeeClient

            print("[HybridClient] Khởi động Playwright fallback...")
            self._pw = PlaywrightShopeeClient(
                user_data_dir=self._user_data_dir or DEFAULT_PROFILE_DIR,
                cookie_file=self._cookie_file,
                headless=self._headless,
                proxy=self._proxy,
            )
            self._blocked = True
        return self._pw

    def close(self) -> None:
        try:
            self._curl.close()
        finally:
            if self._pw is not None:
                self._pw.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _call(self, method: str, *args, **kwargs):
        if self._blocked:
            return getattr(self._get_pw(), method)(*args, **kwargs)
        try:
            return getattr(self._curl, method)(*args, **kwargs)
        except ShopeeAPIError as e:
            if _looks_blocked(e):
                print(f"[HybridClient] curl bị block ({e}) → fallback Playwright")
                return getattr(self._get_pw(), method)(*args, **kwargs)
            raise

    def shop_detail(self, **kwargs):
        return self._call("shop_detail", **kwargs)

    def shop_products_page(self, *args, **kwargs):
        return self._call("shop_products_page", *args, **kwargs)

    def iter_shop_products(self, *args, **kwargs):
        seen: set[int] = set()
        if not self._blocked:
            try:
                for item in self._curl.iter_shop_products(*args, **kwargs):
                    try:
                        seen.add(int(item["itemid"]))
                    except (KeyError, TypeError, ValueError):
                        pass
                    yield item
                return
            except ShopeeAPIError as e:
                if not _looks_blocked(e):
                    raise
                print(
                    f"[HybridClient] curl bị block giữa chừng ({e}) → chuyển Playwright"
                )

        for item in self._get_pw().iter_shop_products(*args, **kwargs):
            try:
                if int(item["itemid"]) in seen:
                    continue
            except (KeyError, TypeError, ValueError):
                pass
            yield item
