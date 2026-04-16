"""SQLite schema + helpers for persisting shop/product snapshots."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

DEFAULT_DB = Path("shopee_tracker.sqlite")

SCHEMA = """
CREATE TABLE IF NOT EXISTS shops (
    shopid      INTEGER PRIMARY KEY,
    username    TEXT,
    name        TEXT,
    first_seen  INTEGER NOT NULL,
    last_seen   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS shop_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    shopid          INTEGER NOT NULL,
    ts              INTEGER NOT NULL,
    follower_count  INTEGER,
    following_count INTEGER,
    item_count      INTEGER,
    rating_star     REAL,
    rating_good     INTEGER,
    rating_normal   INTEGER,
    rating_bad      INTEGER,
    response_rate   INTEGER,
    response_time   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_shop_snap_shopid_ts
    ON shop_snapshots(shopid, ts);

CREATE TABLE IF NOT EXISTS products (
    shopid      INTEGER NOT NULL,
    itemid      INTEGER NOT NULL,
    name        TEXT,
    ctime       INTEGER,
    first_seen  INTEGER NOT NULL,
    last_seen   INTEGER NOT NULL,
    PRIMARY KEY (shopid, itemid)
);

CREATE TABLE IF NOT EXISTS product_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    shopid          INTEGER NOT NULL,
    itemid          INTEGER NOT NULL,
    ts              INTEGER NOT NULL,
    price_min       INTEGER,
    price_max       INTEGER,
    stock           INTEGER,
    historical_sold INTEGER,
    rating_star     REAL,
    rating_count    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_prod_snap_shop_item_ts
    ON product_snapshots(shopid, itemid, ts);
"""


@contextmanager
def connect(path: Path = DEFAULT_DB) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert_shop(
    conn: sqlite3.Connection,
    shopid: int,
    username: str | None,
    name: str | None,
    ts: int,
) -> None:
    conn.execute(
        """
        INSERT INTO shops (shopid, username, name, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(shopid) DO UPDATE SET
            username = COALESCE(excluded.username, shops.username),
            name = COALESCE(excluded.name, shops.name),
            last_seen = excluded.last_seen
        """,
        (shopid, username, name, ts, ts),
    )


def insert_shop_snapshot(
    conn: sqlite3.Connection,
    shopid: int,
    ts: int,
    data: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT INTO shop_snapshots (
            shopid, ts, follower_count, following_count, item_count,
            rating_star, rating_good, rating_normal, rating_bad,
            response_rate, response_time
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            shopid,
            ts,
            data.get("follower_count"),
            data.get("following_count"),
            data.get("item_count"),
            data.get("rating_star"),
            data.get("rating_good"),
            data.get("rating_normal"),
            data.get("rating_bad"),
            data.get("response_rate"),
            data.get("response_time"),
        ),
    )


def latest_shop_snapshot(
    conn: sqlite3.Connection,
    shopid: int,
    before_ts: int,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT * FROM shop_snapshots
        WHERE shopid = ? AND ts < ?
        ORDER BY ts DESC
        LIMIT 1
        """,
        (shopid, before_ts),
    ).fetchone()


def list_shop_snapshots(
    conn: sqlite3.Connection,
    shopid: int,
    limit: int = 20,
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT * FROM shop_snapshots
        WHERE shopid = ?
        ORDER BY ts DESC
        LIMIT ?
        """,
        (shopid, limit),
    ).fetchall()


def upsert_product(
    conn: sqlite3.Connection,
    shopid: int,
    itemid: int,
    name: str | None,
    ctime: int | None,
    ts: int,
) -> None:
    conn.execute(
        """
        INSERT INTO products (shopid, itemid, name, ctime, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(shopid, itemid) DO UPDATE SET
            name = COALESCE(excluded.name, products.name),
            last_seen = excluded.last_seen
        """,
        (shopid, itemid, name, ctime, ts, ts),
    )


def insert_product_snapshot(
    conn: sqlite3.Connection,
    shopid: int,
    itemid: int,
    ts: int,
    snap: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT INTO product_snapshots (
            shopid, itemid, ts, price_min, price_max, stock,
            historical_sold, rating_star, rating_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            shopid,
            itemid,
            ts,
            snap.get("price_min"),
            snap.get("price_max"),
            snap.get("stock"),
            snap.get("historical_sold"),
            snap.get("rating_star"),
            snap.get("rating_count"),
        ),
    )


def latest_product_snapshots_before(
    conn: sqlite3.Connection,
    shopid: int,
    before_ts: int,
) -> dict[int, sqlite3.Row]:
    """Most recent snapshot per itemid strictly before `before_ts`."""
    rows = conn.execute(
        """
        SELECT ps.*
        FROM product_snapshots ps
        INNER JOIN (
            SELECT itemid, MAX(ts) AS max_ts
            FROM product_snapshots
            WHERE shopid = ? AND ts < ?
            GROUP BY itemid
        ) latest
          ON latest.itemid = ps.itemid AND latest.max_ts = ps.ts
        WHERE ps.shopid = ?
        """,
        (shopid, before_ts, shopid),
    ).fetchall()
    return {row["itemid"]: row for row in rows}


def list_known_products(
    conn: sqlite3.Connection,
    shopid: int,
) -> dict[int, sqlite3.Row]:
    rows = conn.execute(
        "SELECT itemid, name, last_seen FROM products WHERE shopid = ?",
        (shopid,),
    ).fetchall()
    return {row["itemid"]: row for row in rows}


def find_shop_by_username(
    conn: sqlite3.Connection,
    username: str,
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM shops WHERE username = ? LIMIT 1",
        (username,),
    ).fetchone()
