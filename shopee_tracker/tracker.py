"""High-level flow: crawl one shop, persist snapshot, compute diff vs previous."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import db
from .resolver import ShopRef

if TYPE_CHECKING:
    from .client import ShopeeClient


def _extract_product_snap(item: dict[str, Any]) -> dict[str, Any]:
    price_min = item.get("price_min") or item.get("price")
    price_max = item.get("price_max") or item.get("price")
    rating = item.get("item_rating") or {}
    rating_counts = rating.get("rating_count") or []
    total_ratings = sum(rating_counts) if isinstance(rating_counts, list) else rating_counts
    return {
        "price_min": price_min,
        "price_max": price_max,
        "stock": item.get("stock"),
        "historical_sold": item.get("historical_sold"),
        "rating_star": rating.get("rating_star"),
        "rating_count": total_ratings,
    }


@dataclass
class ShopFieldChange:
    field: str
    old: Any
    new: Any


@dataclass
class ProductChange:
    itemid: int
    name: str
    old: Any = None
    new: Any = None
    delta: int | None = None


@dataclass
class DiffReport:
    shopid: int
    shop_name: str
    first_run: bool
    shop_changes: list[ShopFieldChange] = field(default_factory=list)
    new_products: list[ProductChange] = field(default_factory=list)
    price_changes: list[ProductChange] = field(default_factory=list)
    sold_deltas: list[ProductChange] = field(default_factory=list)
    stock_changes: list[ProductChange] = field(default_factory=list)
    disappeared: list[ProductChange] = field(default_factory=list)


_SHOP_FIELDS_TO_DIFF = (
    "follower_count",
    "item_count",
    "rating_star",
    "rating_good",
    "rating_bad",
    "response_rate",
)


def track_shop(
    ref: ShopRef,
    client: "ShopeeClient",
    limit: int = 100,
    db_path: Path = db.DEFAULT_DB,
    detect_removed: bool = False,
) -> DiffReport:
    """Crawl one shop, write a snapshot, and return a DiffReport vs last snapshot.

    `detect_removed=True` marks previously-seen products that are missing from
    this run as "disappeared". Only accurate when `limit` covers the whole shop,
    otherwise items beyond the limit look falsely removed.
    """
    ts = int(time.time())

    detail = client.shop_detail(username=ref.username, shopid=ref.shopid)
    sdata = detail.get("data") or {}
    if not sdata:
        raise RuntimeError(f"Empty shop detail for {ref}")

    shopid = int(sdata["shopid"])
    username = (sdata.get("account") or {}).get("username") or ref.username
    name = sdata.get("name") or ""

    with db.connect(db_path) as conn:
        prev_shop = db.latest_shop_snapshot(conn, shopid, ts)
        prev_products = db.latest_product_snapshots_before(conn, shopid, ts)
        known_products = db.list_known_products(conn, shopid)

        db.upsert_shop(conn, shopid, username, name, ts)
        db.insert_shop_snapshot(conn, shopid, ts, sdata)

        current: dict[int, dict[str, Any]] = {}
        for item in client.iter_shop_products(
            shopid=shopid,
            max_items=limit,
            referer_username=username,
        ):
            itemid = int(item["itemid"])
            current[itemid] = item
            snap = _extract_product_snap(item)
            db.upsert_product(
                conn,
                shopid,
                itemid,
                item.get("name"),
                item.get("ctime"),
                ts,
            )
            db.insert_product_snapshot(conn, shopid, itemid, ts, snap)

    first_run = prev_shop is None
    report = DiffReport(shopid=shopid, shop_name=name, first_run=first_run)
    if first_run:
        return report

    if prev_shop is not None:
        for key in _SHOP_FIELDS_TO_DIFF:
            old = prev_shop[key] if key in prev_shop.keys() else None
            new = sdata.get(key)
            if new is not None and old != new:
                report.shop_changes.append(ShopFieldChange(key, old, new))

    for itemid, item in current.items():
        name_i = (item.get("name") or "").strip()
        snap = _extract_product_snap(item)
        prev = prev_products.get(itemid)

        if prev is None:
            if itemid not in known_products:
                report.new_products.append(
                    ProductChange(itemid=itemid, name=name_i, new=snap["price_min"])
                )
            continue

        old_price = prev["price_min"]
        new_price = snap["price_min"]
        if old_price != new_price and new_price is not None:
            report.price_changes.append(
                ProductChange(itemid=itemid, name=name_i, old=old_price, new=new_price)
            )

        old_sold = prev["historical_sold"] or 0
        new_sold = snap["historical_sold"] or 0
        if new_sold > old_sold:
            report.sold_deltas.append(
                ProductChange(
                    itemid=itemid,
                    name=name_i,
                    old=old_sold,
                    new=new_sold,
                    delta=new_sold - old_sold,
                )
            )

        old_stock = prev["stock"]
        new_stock = snap["stock"]
        if old_stock != new_stock and new_stock is not None:
            report.stock_changes.append(
                ProductChange(itemid=itemid, name=name_i, old=old_stock, new=new_stock)
            )

    if detect_removed:
        for itemid, row in known_products.items():
            if itemid not in current:
                report.disappeared.append(
                    ProductChange(itemid=itemid, name=row["name"] or "")
                )

    return report
