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

from curl_cffi import requests

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
    ) -> None:
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
        self._load_cookies(cookie_file)

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
        r = self.session.get(f"{BASE}{path}", params=params, headers=headers, timeout=20)
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
