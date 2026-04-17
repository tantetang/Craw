"""Tests cho HybridClient fallback logic (không cần network / browser)."""

from __future__ import annotations

from shopee_tracker.client import HybridClient, ShopeeAPIError, _looks_blocked


class FakeCurl:
    def __init__(self, mode: str):
        self.mode = mode
        self.calls: list[str] = []

    def shop_detail(self, **kwargs):
        self.calls.append(f"shop_detail:{kwargs}")
        if self.mode == "block_on_detail":
            raise ShopeeAPIError("HTTP 403 tại /api/v4/shop/get_shop_detail: ...")
        if self.mode == "other_error":
            raise ShopeeAPIError("Shopee error=5 msg=Invalid shop")
        return {"source": "curl", "data": {"shopid": 1, "name": "curl-shop"}}

    def iter_shop_products(self, shopid, max_items=200, page_size=30, referer_username=None):
        self.calls.append(f"iter:{shopid}")
        if self.mode == "block_mid_iter":
            yield {"itemid": 1, "name": "a"}
            yield {"itemid": 2, "name": "b"}
            raise ShopeeAPIError("HTTP 429 tại /api/v4/shop/search_items")
        if self.mode == "block_on_iter":
            raise ShopeeAPIError("HTTP 403 tại /api/v4/shop/search_items")
        for i in range(3):
            yield {"itemid": i + 1, "name": f"curl-{i}"}

    def close(self):
        pass


class FakePW:
    def __init__(self):
        self.calls: list[str] = []

    def shop_detail(self, **kwargs):
        self.calls.append(f"shop_detail:{kwargs}")
        return {"source": "pw", "data": {"shopid": 1, "name": "pw-shop"}}

    def iter_shop_products(self, shopid, max_items=200, page_size=30, referer_username=None):
        self.calls.append(f"iter:{shopid}")
        # PW sees items 1..5 (superset of what curl partially yielded)
        for i in range(5):
            yield {"itemid": i + 1, "name": f"pw-{i}"}

    def close(self):
        pass


def _make_hybrid(curl_mode: str, pw: FakePW) -> HybridClient:
    h = HybridClient.__new__(HybridClient)
    h._curl = FakeCurl(curl_mode)
    h._pw = None
    h._blocked = False
    h._cookie_file = None
    h._user_data_dir = None
    h._headless = True
    # Inject PW when get_pw is called
    h._get_pw = lambda: (_pw_set(h, pw) or pw)  # type: ignore[attr-defined]
    return h


def _pw_set(h, pw):
    if h._pw is None:
        h._pw = pw
        h._blocked = True


def assert_(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ✓ {msg}")


def test_block_signal_detection():
    print("\n[_looks_blocked]")
    assert_(_looks_blocked(ShopeeAPIError("HTTP 403 ...")), "403 → blocked")
    assert_(_looks_blocked(ShopeeAPIError("HTTP 429 ...")), "429 → blocked")
    assert_(_looks_blocked(ShopeeAPIError("Rate-limited (429)")), "Rate-limited text → blocked")
    assert_(_looks_blocked(ShopeeAPIError("Response không phải JSON: ...")), "non-JSON → blocked")
    assert_(
        not _looks_blocked(ShopeeAPIError("Shopee error=5 msg=Invalid shop")),
        "lỗi Shopee khác (shop không tồn tại) KHÔNG coi là block",
    )


def test_happy_path_stays_on_curl():
    print("\n[happy path] curl thành công, không fallback")
    pw = FakePW()
    h = _make_hybrid("happy", pw)
    result = h.shop_detail(username="demo")
    assert_(result["source"] == "curl", "shop_detail dùng curl")
    items = list(h.iter_shop_products(shopid=1, max_items=10))
    assert_(len(items) == 3, "iter trả 3 items từ curl")
    assert_(pw.calls == [], "PW không được gọi")


def test_fallback_on_block():
    print("\n[fallback] curl trả HTTP 403 → PW được gọi thay")
    pw = FakePW()
    h = _make_hybrid("block_on_detail", pw)
    result = h.shop_detail(username="demo")
    assert_(result["source"] == "pw", "shop_detail trả từ PW sau fallback")
    assert_(h._blocked is True, "HybridClient đánh dấu _blocked=True")
    # Subsequent call goes directly to PW, skip curl
    result2 = h.shop_detail(username="demo")
    assert_(result2["source"] == "pw", "call thứ 2 vẫn dùng PW (không thử lại curl)")


def test_non_block_error_propagates():
    print("\n[non-block error] error khác (invalid shop) không trigger fallback")
    pw = FakePW()
    h = _make_hybrid("other_error", pw)
    try:
        h.shop_detail(username="demo")
    except ShopeeAPIError as e:
        assert_("Invalid shop" in str(e), "raise lỗi gốc")
        assert_(h._blocked is False, "_blocked vẫn False")
        assert_(pw.calls == [], "PW không được gọi")
        return
    raise AssertionError("expected ShopeeAPIError")


def test_iter_fallback_mid_stream_dedup():
    print("\n[iter mid-block] curl yield 2 items rồi raise 429 → PW yield 5 items, dedup còn 3 mới")
    pw = FakePW()
    h = _make_hybrid("block_mid_iter", pw)
    items = list(h.iter_shop_products(shopid=1, max_items=10))
    ids = [i["itemid"] for i in items]
    assert_(ids[:2] == [1, 2], "2 items đầu từ curl")
    assert_(ids[2:] == [3, 4, 5], "items còn lại từ PW, 1 và 2 đã dedup")
    assert_(len(pw.calls) == 1, "PW.iter được gọi đúng 1 lần")


def test_iter_fallback_on_first_call():
    print("\n[iter first-block] curl raise ngay từ đầu → PW full list")
    pw = FakePW()
    h = _make_hybrid("block_on_iter", pw)
    items = list(h.iter_shop_products(shopid=1, max_items=10))
    assert_(len(items) == 5, "PW trả đủ 5 items")


if __name__ == "__main__":
    test_block_signal_detection()
    test_happy_path_stays_on_curl()
    test_fallback_on_block()
    test_non_block_error_propagates()
    test_iter_fallback_mid_stream_dedup()
    test_iter_fallback_on_first_call()
    print("\n✅ ALL HYBRID TESTS PASSED")
