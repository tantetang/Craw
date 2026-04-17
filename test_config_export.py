"""Tests cho config.py (YAML/JSON loader) và export.py (CSV writer)."""

from __future__ import annotations

import csv
import json
import tempfile
import time
from pathlib import Path

from shopee_tracker.config import ShopEntry, load_shops
from shopee_tracker import db
from shopee_tracker.export import (
    export_all,
    export_latest_products,
    export_product_snapshots,
    export_shop_snapshots,
)


def assert_(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ✓ {msg}")


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------

def test_config_json():
    print("\n[config] JSON format")
    data = {"shops": [
        "https://shopee.vn/shop_a",
        {"url": "https://shopee.vn/shop_b", "alias": "b", "limit": 50, "engine": "hybrid"},
    ]}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(data, f)
        p = Path(f.name)
    try:
        shops = load_shops(p)
        assert_(len(shops) == 2, "2 entries")
        assert_(isinstance(shops[0], ShopEntry), "type ShopEntry")
        assert_(shops[0].url == "https://shopee.vn/shop_a", "string URL")
        assert_(shops[0].limit == 100, "default limit")
        assert_(shops[1].alias == "b", "alias parsed")
        assert_(shops[1].limit == 50, "custom limit")
        assert_(shops[1].engine == "hybrid", "engine parsed")
    finally:
        p.unlink(missing_ok=True)


def test_config_missing_file():
    print("\n[config] FileNotFoundError nếu file không tồn tại")
    try:
        load_shops(Path("nonexistent_shops.yaml"))
    except FileNotFoundError:
        print("  ✓ FileNotFoundError raised")
        return
    raise AssertionError("Phải raise FileNotFoundError")


def test_config_missing_url():
    print("\n[config] ValueError nếu entry không có 'url'")
    data = {"shops": [{"alias": "no_url"}]}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(data, f)
        p = Path(f.name)
    try:
        load_shops(p)
    except ValueError:
        print("  ✓ ValueError raised")
    finally:
        p.unlink(missing_ok=True)


def test_config_missing_shops_key():
    print("\n[config] ValueError nếu không có key 'shops'")
    data = {"data": []}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(data, f)
        p = Path(f.name)
    try:
        load_shops(p)
    except ValueError:
        print("  ✓ ValueError raised")
    finally:
        p.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Export tests (using real SQLite from test_tracker fixture)
# ---------------------------------------------------------------------------

def _build_test_db(tmp_dir: Path) -> tuple[Path, int]:
    """Tạo DB nhỏ với 1 shop + 2 SP + 2 snapshot rounds."""
    db_path = tmp_dir / "test.sqlite"
    ts1 = int(time.time()) - 3600
    ts2 = int(time.time())
    shopid = 9999

    with db.connect(db_path) as conn:
        db.upsert_shop(conn, shopid, "testshop", "Test Shop VN", ts1)
        db.insert_shop_snapshot(conn, shopid, ts1, {
            "follower_count": 1000, "following_count": 10, "item_count": 2,
            "rating_star": 4.8, "rating_good": 900, "rating_normal": 50,
            "rating_bad": 5, "response_rate": 95, "response_time": 3600,
        })
        db.upsert_shop(conn, shopid, "testshop", "Test Shop VN", ts2)
        db.insert_shop_snapshot(conn, shopid, ts2, {
            "follower_count": 1050, "following_count": 10, "item_count": 3,
            "rating_star": 4.85, "rating_good": 950, "rating_normal": 50,
            "rating_bad": 5, "response_rate": 96, "response_time": 3500,
        })
        for itemid, name, price in [(1, "Áo thun", 15_000_000_000), (2, "Quần jean", 30_000_000_000)]:
            db.upsert_product(conn, shopid, itemid, name, ts1, ts1)
            db.insert_product_snapshot(conn, shopid, itemid, ts1, {
                "price_min": price, "price_max": price, "stock": 50,
                "historical_sold": 100, "rating_star": 4.5, "rating_count": 10,
            })
            db.upsert_product(conn, shopid, itemid, name, ts2, ts2)
            db.insert_product_snapshot(conn, shopid, itemid, ts2, {
                "price_min": price, "price_max": price, "stock": 45,
                "historical_sold": 120, "rating_star": 4.6, "rating_count": 12,
            })
    return db_path, shopid


def _read_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    with path.open(encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        rows = list(reader)
    return rows[0], rows[1:]


def test_export_shop_snapshots():
    print("\n[export] shop_snapshots")
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        db_path, shopid = _build_test_db(tmp)
        out = tmp / "shop.csv"
        with db.connect(db_path) as conn:
            n = export_shop_snapshots(conn, shopid, out)
        assert_(n == 2, f"2 snapshot rows, got {n}")
        headers, rows = _read_csv(out)
        assert_("follower_count" in headers, "follower_count column")
        assert_(len(rows) == 2, "2 data rows")
        assert_(rows[0][headers.index("follower_count")] == "1000", "first row follower=1000")


def test_export_product_snapshots():
    print("\n[export] product_snapshots (time series)")
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        db_path, shopid = _build_test_db(tmp)
        out = tmp / "products_ts.csv"
        with db.connect(db_path) as conn:
            n = export_product_snapshots(conn, shopid, out)
        assert_(n == 4, f"2 SP × 2 snapshots = 4 rows, got {n}")
        headers, rows = _read_csv(out)
        assert_("gia_min_vnd" in headers, "gia_min_vnd column")
        vnd_col = headers.index("gia_min_vnd")
        assert_(float(rows[0][vnd_col]) == 150_000.0, "áo thun = 150,000đ")


def test_export_latest_products():
    print("\n[export] latest products (flat)")
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        db_path, shopid = _build_test_db(tmp)
        out = tmp / "latest.csv"
        with db.connect(db_path) as conn:
            n = export_latest_products(conn, shopid, out)
        assert_(n == 2, f"2 SP, got {n}")
        headers, rows = _read_csv(out)
        assert_("ten_san_pham" in headers, "ten_san_pham column")
        assert_("da_ban" in headers, "da_ban column")
        # Both should show latest sold=120
        da_ban_col = headers.index("da_ban")
        assert_(all(r[da_ban_col] == "120" for r in rows), "da_ban=120 (latest snapshot)")


def test_export_all():
    print("\n[export] export_all → 3 files")
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        db_path, shopid = _build_test_db(tmp)
        out_dir = tmp / "out"
        counts = export_all(db_path, shopid, "TestShop", out_dir)
        assert_(counts["shop_timeline"] == 2, "shop_timeline: 2 rows")
        assert_(counts["products_latest"] == 2, "products_latest: 2 rows")
        assert_(counts["products_history"] == 4, "products_history: 4 rows")
        files = list(out_dir.glob("*.csv"))
        assert_(len(files) == 3, f"3 CSV files, got {len(files)}")


if __name__ == "__main__":
    test_config_json()
    test_config_missing_file()
    test_config_missing_url()
    test_config_missing_shops_key()
    test_export_shop_snapshots()
    test_export_product_snapshots()
    test_export_latest_products()
    test_export_all()
    print("\n✅ ALL CONFIG + EXPORT TESTS PASSED")
