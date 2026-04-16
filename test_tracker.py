"""Tests cho db.py + tracker.py bằng fake client (không gọi mạng).

Chạy: python test_tracker.py
"""

from __future__ import annotations

import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any, Iterator

from shopee_tracker import db
from shopee_tracker.resolver import ShopRef
from shopee_tracker.tracker import track_shop


class FakeClient:
    """Replays fixed responses; also exposes iter_shop_products from a list."""

    def __init__(self, detail: dict, items: list[dict]):
        self._detail = detail
        self._items = items

    def shop_detail(self, username: str | None = None, shopid: str | None = None) -> dict:
        return self._detail

    def iter_shop_products(
        self,
        shopid: Any,
        max_items: int,
        referer_username: str | None = None,
    ) -> Iterator[dict]:
        for item in self._items[:max_items]:
            yield item


def make_detail(shopid=7777, name="Shop Demo", followers=1000, item_count=5, rating=4.8):
    return {
        "data": {
            "shopid": shopid,
            "name": name,
            "account": {"username": "demo"},
            "follower_count": followers,
            "following_count": 10,
            "item_count": item_count,
            "rating_star": rating,
            "rating_good": 900,
            "rating_normal": 50,
            "rating_bad": 5,
            "response_rate": 95,
            "response_time": 3600,
            "ctime": 1_700_000_000,
        }
    }


def make_item(itemid, name, price, sold=0, stock=100, rating=4.5):
    return {
        "itemid": itemid,
        "name": name,
        "price": price,
        "price_min": price,
        "price_max": price,
        "stock": stock,
        "historical_sold": sold,
        "item_rating": {"rating_star": rating, "rating_count": [0, 0, 0, 0, 10]},
        "ctime": 1_700_000_000,
    }


def assert_(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ✓ {msg}")


def test_flow():
    with tempfile.TemporaryDirectory() as d:
        db_path = Path(d) / "test.sqlite"
        ref = ShopRef(final_url="x", username="demo", shopid="7777")

        print("\n[Run 1] first snapshot — expect first_run=True, no diffs")
        items_v1 = [
            make_item(1, "Áo thun nam size L", price=15_000_000_000, sold=100, stock=50),
            make_item(2, "Quần jean slim fit", price=30_000_000_000, sold=20, stock=30),
            make_item(3, "Giày sneaker trắng", price=50_000_000_000, sold=5, stock=10),
        ]
        client = FakeClient(make_detail(), items_v1)
        r1 = track_shop(ref, client, limit=10, db_path=db_path)
        assert_(r1.first_run is True, "first_run=True ở lần đầu")
        assert_(r1.shopid == 7777, "shopid parsed correctly")
        assert_(len(r1.new_products) == 0, "không mark new ở first run")
        assert_(len(r1.price_changes) == 0, "không có price change ở first run")

        print("\n[Run 2] sau khi sleep 1s — item 1 giảm giá, item 2 bán thêm, item 4 mới")
        time.sleep(1.1)
        items_v2 = [
            make_item(1, "Áo thun nam size L", price=12_000_000_000, sold=120, stock=45),
            make_item(2, "Quần jean slim fit", price=30_000_000_000, sold=35, stock=25),
            make_item(3, "Giày sneaker trắng", price=50_000_000_000, sold=5, stock=10),
            make_item(4, "Mũ lưỡi trai đen", price=8_000_000_000, sold=0, stock=100),
        ]
        detail_v2 = make_detail(followers=1050, item_count=6, rating=4.85)
        client = FakeClient(detail_v2, items_v2)
        r2 = track_shop(ref, client, limit=10, db_path=db_path)

        assert_(r2.first_run is False, "first_run=False ở lần 2")
        shop_fields = {c.field for c in r2.shop_changes}
        assert_("follower_count" in shop_fields, "follower_count thay đổi detected")
        assert_("item_count" in shop_fields, "item_count thay đổi detected")
        assert_("rating_star" in shop_fields, "rating_star thay đổi detected")

        new_ids = {c.itemid for c in r2.new_products}
        assert_(new_ids == {4}, f"chỉ item 4 mới, got {new_ids}")

        price_ids = {c.itemid for c in r2.price_changes}
        assert_(price_ids == {1}, f"chỉ item 1 đổi giá, got {price_ids}")
        p1 = next(c for c in r2.price_changes if c.itemid == 1)
        assert_(p1.old == 15_000_000_000 and p1.new == 12_000_000_000, "giá item 1: 15B → 12B")

        sold_ids = {c.itemid for c in r2.sold_deltas}
        assert_(sold_ids == {1, 2}, f"item 1 và 2 có sold delta, got {sold_ids}")
        s2 = next(c for c in r2.sold_deltas if c.itemid == 2)
        assert_(s2.delta == 15 and s2.new == 35, f"item 2 delta=15 new=35, got {s2}")

        stock_ids = {c.itemid for c in r2.stock_changes}
        assert_(stock_ids == {1, 2}, f"stock thay đổi ở 1,2, got {stock_ids}")

        assert_(r2.disappeared == [], "không detect_removed=True → list rỗng")

        print("\n[Run 3] detect_removed=True, item 3 biến mất")
        time.sleep(1.1)
        items_v3 = [
            make_item(1, "Áo thun nam size L", price=12_000_000_000, sold=120, stock=45),
            make_item(2, "Quần jean slim fit", price=30_000_000_000, sold=35, stock=25),
            make_item(4, "Mũ lưỡi trai đen", price=8_000_000_000, sold=0, stock=100),
        ]
        client = FakeClient(detail_v2, items_v3)
        r3 = track_shop(ref, client, limit=10, db_path=db_path, detect_removed=True)
        gone = {c.itemid for c in r3.disappeared}
        assert_(gone == {3}, f"item 3 biến mất, got {gone}")

        print("\n[History] kiểm tra shop_snapshots có 3 rows")
        with db.connect(db_path) as conn:
            snaps = db.list_shop_snapshots(conn, 7777, limit=10)
            assert_(len(snaps) == 3, f"3 shop snapshots, got {len(snaps)}")
            rows = conn.execute(
                "SELECT COUNT(*) AS n FROM product_snapshots"
            ).fetchone()
            assert_(rows["n"] == 3 + 4 + 3, f"product_snapshots tổng = 10, got {rows['n']}")


if __name__ == "__main__":
    test_flow()
    print("\n✅ ALL TESTS PASSED")
