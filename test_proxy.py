"""Tests cho shopee_tracker.proxy (không cần network)."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from shopee_tracker.proxy import (
    ENV_VAR,
    ProxyConfig,
    _parse_url,
    for_curl,
    for_playwright,
    load_proxy,
    save_proxy,
)


def assert_(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ✓ {msg}")


def test_to_url_no_auth():
    print("\n[proxy] to_url không auth")
    cfg = ProxyConfig(server="http://host:8080")
    assert_(cfg.to_url() == "http://host:8080", "plain URL")


def test_to_url_with_auth():
    print("\n[proxy] to_url có auth (+ escape)")
    cfg = ProxyConfig(server="http://host:8080", username="u@x", password="p a")
    url = cfg.to_url()
    assert_(url == "http://u%40x:p%20a@host:8080", f"escaped auth ({url})")


def test_to_url_schemeless():
    print("\n[proxy] to_url tự thêm http://")
    cfg = ProxyConfig(server="host:8080")
    assert_(cfg.to_url() == "http://host:8080", "thêm http:// prefix")


def test_display_hides_password():
    print("\n[proxy] display() ẩn password")
    cfg = ProxyConfig(server="http://host:8080", username="u", password="secret")
    s = cfg.display()
    assert_("secret" not in s, "password bị ẩn")
    assert_("u" in s, "username hiện")


def test_is_active():
    print("\n[proxy] is_active")
    assert_(ProxyConfig(server="h").is_active(), "enabled + server → active")
    assert_(not ProxyConfig(server="h", enabled=False).is_active(), "disabled → inactive")
    assert_(not ProxyConfig(server="   ").is_active(), "empty server → inactive")


def test_save_and_load_roundtrip():
    print("\n[proxy] save/load round-trip")
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        p = Path(f.name)
    try:
        cfg = ProxyConfig(server="http://h:9", username="u", password="p", enabled=True)
        save_proxy(cfg, p)
        loaded = load_proxy(p)
        assert_(loaded is not None, "loaded non-None")
        assert_(loaded.server == "http://h:9", "server round-trip")
        assert_(loaded.username == "u", "username round-trip")
        assert_(loaded.password == "p", "password round-trip")
    finally:
        p.unlink(missing_ok=True)


def test_load_disabled_returns_none():
    print("\n[proxy] load_proxy trả None khi disabled")
    with tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump({"enabled": False, "server": "http://h:1"}, f)
        p = Path(f.name)
    try:
        # ensure env var doesn't override
        old_env = os.environ.pop(ENV_VAR, None)
        try:
            assert_(load_proxy(p) is None, "disabled → None")
        finally:
            if old_env is not None:
                os.environ[ENV_VAR] = old_env
    finally:
        p.unlink(missing_ok=True)


def test_load_missing_file_returns_none():
    print("\n[proxy] load_proxy trả None khi file không tồn tại")
    old_env = os.environ.pop(ENV_VAR, None)
    try:
        assert_(
            load_proxy(Path("nonexistent_proxy_xxx.json")) is None,
            "missing file → None",
        )
    finally:
        if old_env is not None:
            os.environ[ENV_VAR] = old_env


def test_env_var_override():
    print("\n[proxy] ENV var override file")
    os.environ[ENV_VAR] = "http://u:p@envhost:9"
    try:
        cfg = load_proxy(Path("nonexistent.json"))
        assert_(cfg is not None, "env var parsed")
        assert_(cfg.server == "http://envhost:9", "env server")
        assert_(cfg.username == "u", "env username")
        assert_(cfg.password == "p", "env password")
    finally:
        del os.environ[ENV_VAR]


def test_parse_url():
    print("\n[proxy] _parse_url các biến thể")
    cfg = _parse_url("socks5://user:pass@1.2.3.4:1080")
    assert_(cfg.server == "socks5://1.2.3.4:1080", "socks5 server")
    assert_(cfg.username == "user", "user parsed")
    cfg2 = _parse_url("host:8080")  # no scheme
    assert_(cfg2.server == "http://host:8080", "default scheme http")


def test_for_curl_format():
    print("\n[proxy] for_curl format")
    cfg = ProxyConfig(server="http://h:1", username="u", password="p")
    out = for_curl(cfg)
    assert_(out is not None, "non-None")
    assert_(out["http"] == out["https"], "same http/https")
    assert_("u:p@h:1" in out["http"], "auth embedded")


def test_for_curl_none():
    print("\n[proxy] for_curl(None) trả None khi chưa config")
    # chặn load_proxy bằng env + file tạm không tồn tại
    old_env = os.environ.pop(ENV_VAR, None)
    try:
        cfg = ProxyConfig(server="", enabled=False)
        assert_(for_curl(cfg) is None, "inactive → None")
    finally:
        if old_env is not None:
            os.environ[ENV_VAR] = old_env


def test_for_playwright_format():
    print("\n[proxy] for_playwright format")
    cfg = ProxyConfig(server="socks5://h:1080", username="u", password="p")
    out = for_playwright(cfg)
    assert_(out is not None, "non-None")
    assert_(out["server"] == "socks5://h:1080", "server key")
    assert_(out["username"] == "u", "username key")
    assert_(out["password"] == "p", "password key")


def test_for_playwright_no_auth():
    print("\n[proxy] for_playwright không auth → không có keys u/p")
    cfg = ProxyConfig(server="http://h:1")
    out = for_playwright(cfg)
    assert_(out is not None, "non-None")
    assert_("username" not in out, "không có username key")
    assert_("password" not in out, "không có password key")


if __name__ == "__main__":
    test_to_url_no_auth()
    test_to_url_with_auth()
    test_to_url_schemeless()
    test_display_hides_password()
    test_is_active()
    test_save_and_load_roundtrip()
    test_load_disabled_returns_none()
    test_load_missing_file_returns_none()
    test_env_var_override()
    test_parse_url()
    test_for_curl_format()
    test_for_curl_none()
    test_for_playwright_format()
    test_for_playwright_no_auth()
    print("\n✅ ALL PROXY TESTS PASSED")
