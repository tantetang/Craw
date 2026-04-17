"""Import cookies từ browser thật sang cookies.json.

Dùng khi Shopee chặn mọi nỗ lực automation login (CAPTCHA, /verify/traffic).
Chrome / Firefox thật của user thường không bị flag → export cookie từ đó.

Usage:
    # Export cookies từ browser qua extension "Cookie-Editor" / "EditThisCookie"
    # hoặc DevTools → Application → Cookies → Copy all as JSON
    # rồi chạy:
    python -m shopee_tracker.cookies_import /path/to/exported.json

Định dạng hỗ trợ:
    1. DevTools JSON     [{"name":..., "value":..., "domain":...}, ...]
    2. Cookie-Editor     Netscape tabular .txt / JSON
    3. Netscape format   # Netscape HTTP Cookie File
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

COOKIE_FILE = Path("cookies.json")


def _parse_json(text: str) -> list[dict]:
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("JSON phải là list of cookie objects")
    return data


def _parse_netscape(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, _flag, path, secure, expires, name, value = parts[:7]
        out.append({
            "name": name,
            "value": value,
            "domain": domain,
            "path": path,
            "secure": secure.lower() == "true",
            "expires": int(expires) if expires.isdigit() else -1,
        })
    return out


def _normalize(raw: list[dict]) -> list[dict]:
    """Chuẩn hoá schema về format mà session.py / client.py đang dùng."""
    out = []
    for c in raw:
        if "name" not in c or "value" not in c:
            continue
        domain = c.get("domain") or ".shopee.vn"
        # Chrome export đôi khi là "hostOnly"/"session" boolean
        entry = {
            "name": c["name"],
            "value": c["value"],
            "domain": domain if domain.startswith(".") or domain == "shopee.vn" else f".{domain}",
            "path": c.get("path", "/"),
            "secure": bool(c.get("secure", False)),
            "httpOnly": bool(c.get("httpOnly") or c.get("http_only") or False),
        }
        exp = c.get("expires") or c.get("expirationDate") or c.get("expiry")
        if exp and exp != -1:
            try:
                entry["expires"] = int(float(exp))
            except (TypeError, ValueError):
                pass
        out.append(entry)
    return out


def _filter_shopee(cookies: list[dict]) -> list[dict]:
    return [c for c in cookies if "shopee" in (c.get("domain") or "").lower()]


def import_cookies(src: Path, dest: Path = COOKIE_FILE) -> int:
    text = src.read_text(encoding="utf-8")
    text_stripped = text.lstrip()
    if text_stripped.startswith("[") or text_stripped.startswith("{"):
        raw = _parse_json(text)
    else:
        raw = _parse_netscape(text)

    cookies = _filter_shopee(_normalize(raw))
    if not cookies:
        raise ValueError("Không tìm thấy cookie Shopee nào trong file nguồn")

    dest.write_text(
        json.dumps(cookies, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    has_spc_u = any(c["name"] == "SPC_U" for c in cookies)
    return len(cookies), has_spc_u


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import Shopee cookies từ browser export")
    parser.add_argument("source", help="File cookie export (JSON hoặc Netscape)")
    parser.add_argument("--out", default=str(COOKIE_FILE), help="cookies.json đầu ra")
    args = parser.parse_args(argv)

    src = Path(args.source)
    if not src.exists():
        print(f"File không tồn tại: {src}", file=sys.stderr)
        return 1

    try:
        count, has_spc_u = import_cookies(src, Path(args.out))
    except Exception as e:
        print(f"Import lỗi: {e}", file=sys.stderr)
        return 1

    print(f"Đã import {count} cookie Shopee → {args.out}")
    if not has_spc_u:
        print("⚠ CẢNH BÁO: không thấy SPC_U → chưa login? Login vào shopee.vn ở browser rồi export lại.")
    else:
        print("✓ Có SPC_U — user đã login.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
