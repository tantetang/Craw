"""Proxy config: load / save / format cho curl_cffi & Playwright.

Ưu tiên đọc:
    1. biến môi trường SHOPEE_PROXY (URL đầy đủ, vd http://user:pass@host:port)
    2. file proxy.json ở CWD

File proxy.json mẫu:
    {
      "enabled": true,
      "server":   "http://proxy.example.com:8080",
      "username": "",
      "password": ""
    }

Server có thể là http / https / socks5 scheme.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import quote, urlparse

DEFAULT_PROXY_FILE = Path("proxy.json")
ENV_VAR = "SHOPEE_PROXY"


@dataclass
class ProxyConfig:
    server: str
    username: str = ""
    password: str = ""
    enabled: bool = True

    def is_active(self) -> bool:
        return bool(self.enabled and self.server.strip())

    def to_url(self) -> str:
        """Trả URL đầy đủ có auth (dùng cho curl/requests)."""
        if not self.is_active():
            return ""
        server = self.server.strip()
        if "://" not in server:
            server = "http://" + server
        scheme, rest = server.split("://", 1)
        if not self.username:
            return f"{scheme}://{rest}"
        u = quote(self.username, safe="")
        p = quote(self.password, safe="")
        return f"{scheme}://{u}:{p}@{rest}"

    def display(self) -> str:
        """Chuỗi an toàn để log (ẩn password)."""
        if not self.is_active():
            return "(disabled)"
        server = self.server
        if self.username:
            return f"{server} (auth: {self.username}:***)"
        return server


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------

def load_proxy(path: Path = DEFAULT_PROXY_FILE) -> ProxyConfig | None:
    """Trả về ProxyConfig đang bật, hoặc None nếu không có / disabled."""
    env_url = os.environ.get(ENV_VAR, "").strip()
    if env_url:
        return _parse_url(env_url)

    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

    if not isinstance(data, dict):
        return None
    if not data.get("enabled", True):
        return None
    server = (data.get("server") or "").strip()
    if not server:
        return None
    cfg = ProxyConfig(
        server=server,
        username=data.get("username", "") or "",
        password=data.get("password", "") or "",
        enabled=True,
    )
    return cfg if cfg.is_active() else None


def save_proxy(
    config: ProxyConfig | dict,
    path: Path = DEFAULT_PROXY_FILE,
) -> Path:
    if isinstance(config, ProxyConfig):
        data = asdict(config)
    else:
        data = dict(config)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _parse_url(url: str) -> ProxyConfig:
    """Parse URL dạng http://user:pass@host:port → ProxyConfig."""
    parsed = urlparse(url if "://" in url else f"http://{url}")
    user = parsed.username or ""
    password = parsed.password or ""
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    scheme = parsed.scheme or "http"
    server = f"{scheme}://{host}{port}"
    return ProxyConfig(server=server, username=user, password=password, enabled=True)


# ---------------------------------------------------------------------------
# Format converters
# ---------------------------------------------------------------------------

def for_curl(proxy: ProxyConfig | None) -> dict[str, str] | None:
    """Dict {'http': url, 'https': url} cho curl_cffi / requests.

    Nếu proxy là None → tự load từ file/env.
    """
    if proxy is None:
        proxy = load_proxy()
    if proxy is None:
        return None
    url = proxy.to_url()
    if not url:
        return None
    return {"http": url, "https": url}


def for_playwright(proxy: ProxyConfig | None) -> dict[str, str] | None:
    """Dict {'server': ..., 'username': ..., 'password': ...} cho Playwright.

    Nếu proxy là None → tự load.
    """
    if proxy is None:
        proxy = load_proxy()
    if proxy is None:
        return None
    if not proxy.is_active():
        return None
    out: dict[str, str] = {"server": proxy.server.strip()}
    if proxy.username:
        out["username"] = proxy.username
    if proxy.password:
        out["password"] = proxy.password
    return out


# ---------------------------------------------------------------------------
# Simple connectivity test
# ---------------------------------------------------------------------------

def test_proxy(proxy: ProxyConfig | None = None, timeout: int = 15) -> tuple[bool, str]:
    """Gọi ipinfo.io qua proxy. Trả (ok, message)."""
    if proxy is None:
        proxy = load_proxy()
    if proxy is None:
        return False, "Chưa cấu hình proxy (hoặc đang disabled)."
    try:
        from curl_cffi import requests  # lazy
    except ImportError:
        return False, "curl_cffi chưa cài: pip install curl-cffi"

    proxies = for_curl(proxy)
    try:
        r = requests.get(
            "https://ipinfo.io/json",
            impersonate="chrome124",
            proxies=proxies,
            timeout=timeout,
        )
        if r.status_code != 200:
            return False, f"HTTP {r.status_code}: {r.text[:200]}"
        data = r.json()
        ip = data.get("ip", "?")
        country = data.get("country", "?")
        city = data.get("city", "?")
        return True, f"OK — IP {ip} ({city}, {country})"
    except Exception as e:
        return False, f"Lỗi: {e}"
