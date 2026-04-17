"""Resolve Shopee short URLs (s.shopee.vn/...) and parse shop identifiers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from .proxy import for_curl, load_proxy

SHOP_PATH_RE = re.compile(r"^/(?:shop/(?P<shopid>\d+)|(?P<username>[^/?#]+))/?$")


@dataclass
class ShopRef:
    final_url: str
    username: str | None
    shopid: str | None


def resolve_short_url(url: str, timeout: int = 15) -> str:
    """Follow redirects of a short link and return the final URL."""
    from curl_cffi import requests  # lazy import: only needed for network call

    proxies = for_curl(load_proxy())
    r = requests.get(
        url,
        impersonate="chrome124",
        allow_redirects=True,
        timeout=timeout,
        proxies=proxies,
    )
    return str(r.url)


def parse_shop_identifier(url: str) -> ShopRef:
    """Extract shopid and/or username from a Shopee shop URL.

    Handles three common forms:
      - shopee.vn/<username>
      - shopee.vn/shop/<shopid>
      - shopee.vn/<username>.<shopid>  (legacy product-ish path)
    """
    parsed = urlparse(url)
    path = parsed.path or "/"
    m = SHOP_PATH_RE.match(path)
    if not m:
        return ShopRef(final_url=url, username=None, shopid=None)

    shopid = m.group("shopid")
    username = m.group("username")

    if username and "." in username:
        name_part, _, tail = username.rpartition(".")
        if tail.isdigit() and name_part:
            return ShopRef(final_url=url, username=name_part, shopid=tail)

    return ShopRef(final_url=url, username=username, shopid=shopid)


def resolve(url: str) -> ShopRef:
    final = resolve_short_url(url)
    return parse_shop_identifier(final)
