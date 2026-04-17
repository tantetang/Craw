"""Export DB snapshots ra CSV (hoặc Excel nếu có pandas/openpyxl).

Hàm chính:
    export_latest_products   – mỗi SP, snapshot mới nhất (flat, dễ xem Excel)
    export_product_snapshots – toàn bộ time-series của SP trong 1 shop
    export_shop_snapshots    – time-series shop-level (follower, item_count, …)
    export_all               – ghi cả 3 file vào 1 thư mục
"""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

from . import db

PRICE_DIV = 100_000


def _vnd(v: int | None) -> str:
    if v is None:
        return ""
    return f"{v / PRICE_DIV:.0f}"


def _write_csv(path: Path, rows: list[sqlite3.Row], extra_cols: dict | None = None) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return 0
    keys = list(rows[0].keys())
    if extra_cols:
        keys += [k for k in extra_cols if k not in keys]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(keys)
        for row in rows:
            values = [row[k] if k in row.keys() else (extra_cols or {}).get(k, "") for k in keys]
            w.writerow(values)
    return len(rows)


# ---------------------------------------------------------------------------
# Shop-level time series
# ---------------------------------------------------------------------------

def export_shop_snapshots(
    conn: sqlite3.Connection,
    shopid: int,
    out_path: Path,
) -> int:
    rows = conn.execute(
        """
        SELECT
            datetime(ts, 'unixepoch', 'localtime') AS thoi_diem,
            ts,
            follower_count,
            following_count,
            item_count,
            rating_star,
            rating_good,
            rating_normal,
            rating_bad,
            response_rate,
            response_time
        FROM shop_snapshots
        WHERE shopid = ?
        ORDER BY ts
        """,
        (shopid,),
    ).fetchall()
    return _write_csv(out_path, rows)


# ---------------------------------------------------------------------------
# Product time series (lịch sử giá)
# ---------------------------------------------------------------------------

def export_product_snapshots(
    conn: sqlite3.Connection,
    shopid: int,
    out_path: Path,
) -> int:
    rows = conn.execute(
        """
        SELECT
            datetime(ps.ts, 'unixepoch', 'localtime') AS thoi_diem,
            ps.ts,
            ps.itemid,
            p.name AS ten_san_pham,
            ps.price_min AS gia_min_raw,
            ps.price_max AS gia_max_raw,
            CAST(ps.price_min AS REAL) / 100000 AS gia_min_vnd,
            CAST(ps.price_max AS REAL) / 100000 AS gia_max_vnd,
            ps.stock AS ton_kho,
            ps.historical_sold AS da_ban,
            ps.rating_star AS sao,
            ps.rating_count AS so_danh_gia
        FROM product_snapshots ps
        LEFT JOIN products p
               ON p.shopid = ps.shopid AND p.itemid = ps.itemid
        WHERE ps.shopid = ?
        ORDER BY ps.itemid, ps.ts
        """,
        (shopid,),
    ).fetchall()
    return _write_csv(out_path, rows)


# ---------------------------------------------------------------------------
# Latest snapshot per product (flat, Excel-friendly)
# ---------------------------------------------------------------------------

def export_latest_products(
    conn: sqlite3.Connection,
    shopid: int,
    out_path: Path,
) -> int:
    rows = conn.execute(
        """
        SELECT
            p.itemid,
            p.name AS ten_san_pham,
            datetime(p.ctime,      'unixepoch', 'localtime') AS ngay_dang,
            datetime(p.first_seen, 'unixepoch', 'localtime') AS lan_dau_thay,
            datetime(p.last_seen,  'unixepoch', 'localtime') AS lan_cuoi_thay,
            datetime(ps.ts,        'unixepoch', 'localtime') AS snapshot_moi_nhat,
            CAST(ps.price_min AS REAL) / 100000 AS gia_min_vnd,
            CAST(ps.price_max AS REAL) / 100000 AS gia_max_vnd,
            ps.stock        AS ton_kho,
            ps.historical_sold AS da_ban,
            ps.rating_star  AS sao,
            ps.rating_count AS so_danh_gia
        FROM products p
        LEFT JOIN (
            SELECT ps1.*
            FROM product_snapshots ps1
            INNER JOIN (
                SELECT itemid, MAX(ts) AS max_ts
                FROM product_snapshots
                WHERE shopid = ?
                GROUP BY itemid
            ) latest
              ON latest.itemid = ps1.itemid AND latest.max_ts = ps1.ts
            WHERE ps1.shopid = ?
        ) ps ON ps.itemid = p.itemid
        WHERE p.shopid = ?
        ORDER BY ps.historical_sold DESC NULLS LAST
        """,
        (shopid, shopid, shopid),
    ).fetchall()
    return _write_csv(out_path, rows)


# ---------------------------------------------------------------------------
# Convenience: export tất cả vào 1 thư mục
# ---------------------------------------------------------------------------

def export_all(
    db_path: Path,
    shopid: int,
    shop_name: str,
    out_dir: Path,
) -> dict[str, int]:
    safe = (shop_name or str(shopid)).replace("/", "_").replace(" ", "_")
    results: dict[str, int] = {}

    with db.connect(db_path) as conn:
        path_shop = out_dir / f"{safe}_shop_timeline.csv"
        results["shop_timeline"] = export_shop_snapshots(conn, shopid, path_shop)

        path_products = out_dir / f"{safe}_products_latest.csv"
        results["products_latest"] = export_latest_products(conn, shopid, path_products)

        path_history = out_dir / f"{safe}_products_history.csv"
        results["products_history"] = export_product_snapshots(conn, shopid, path_history)

    return results


# ---------------------------------------------------------------------------
# Optional: write Excel if pandas + openpyxl available
# ---------------------------------------------------------------------------

def export_excel(
    db_path: Path,
    shopid: int,
    shop_name: str,
    out_path: Path,
) -> None:
    try:
        import pandas as pd
    except ImportError as e:
        raise ImportError("Cần pandas: pip install pandas openpyxl") from e

    safe = (shop_name or str(shopid)).replace("/", "_").replace(" ", "_")

    with db.connect(db_path) as conn:
        df_shop = _query_df(conn, shopid, "shop")
        df_latest = _query_df(conn, shopid, "latest")
        df_history = _query_df(conn, shopid, "history")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        df_latest.to_excel(writer, sheet_name="SP mới nhất", index=False)
        df_shop.to_excel(writer, sheet_name="Shop timeline", index=False)
        df_history.to_excel(writer, sheet_name="Lịch sử SP", index=False)


def _query_df(conn: sqlite3.Connection, shopid: int, kind: str):
    import pandas as pd

    if kind == "shop":
        rows = conn.execute(
            "SELECT datetime(ts,'unixepoch','localtime') AS thoi_diem, "
            "follower_count, item_count, rating_star, response_rate "
            "FROM shop_snapshots WHERE shopid=? ORDER BY ts",
            (shopid,),
        ).fetchall()
    elif kind == "latest":
        rows = conn.execute(
            """SELECT p.itemid, p.name,
               CAST(ps.price_min AS REAL)/100000 AS gia_vnd,
               ps.stock, ps.historical_sold, ps.rating_star
               FROM products p
               LEFT JOIN (
                   SELECT ps1.* FROM product_snapshots ps1
                   INNER JOIN (SELECT itemid, MAX(ts) m FROM product_snapshots
                               WHERE shopid=? GROUP BY itemid) l
                     ON l.itemid=ps1.itemid AND l.m=ps1.ts
                   WHERE ps1.shopid=?
               ) ps ON ps.itemid=p.itemid
               WHERE p.shopid=?
               ORDER BY ps.historical_sold DESC""",
            (shopid, shopid, shopid),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT datetime(ps.ts,'unixepoch','localtime') AS thoi_diem,
               ps.itemid, p.name,
               CAST(ps.price_min AS REAL)/100000 AS gia_vnd,
               ps.stock, ps.historical_sold
               FROM product_snapshots ps
               LEFT JOIN products p ON p.shopid=ps.shopid AND p.itemid=ps.itemid
               WHERE ps.shopid=? ORDER BY ps.itemid, ps.ts""",
            (shopid,),
        ).fetchall()

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows, columns=rows[0].keys())
