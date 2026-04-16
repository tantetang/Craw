"""CLI: resolve a Shopee shop URL (kể cả short link) và in thông tin.

Usage:
    python -m shopee_tracker <shop_url> [--limit N]

Ví dụ:
    python -m shopee_tracker https://s.shopee.vn/BPvG4PSVy
    python -m shopee_tracker https://shopee.vn/shopname --limit 100
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from .client import ShopeeAPIError, ShopeeClient
from .resolver import resolve


PRICE_DIV = 100_000  # Shopee lưu giá ở đơn vị micro (1 VND = 100_000).


def fmt_price(value: int | None) -> str:
    if not value:
        return "-"
    return f"{value / PRICE_DIV:,.0f}đ"


def fmt_ts(ts: int | None) -> str:
    if not ts:
        return "-"
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except (OSError, ValueError):
        return str(ts)


def fmt_int(value: int | None) -> str:
    if value is None:
        return "-"
    return f"{value:,}"


def print_shop_header(data: dict) -> None:
    print("=" * 72)
    print(f" Shop    : {data.get('name')}")
    print(f" ShopID  : {data.get('shopid')}")
    print(f" Account : @{data.get('account', {}).get('username', '?')}")
    print(f" Theo dõi: {fmt_int(data.get('follower_count'))}"
          f"   | Đang theo dõi: {fmt_int(data.get('following_count'))}")
    print(f" Rating  : {data.get('rating_star')}  "
          f"({fmt_int(data.get('rating_good'))} tốt / "
          f"{fmt_int(data.get('rating_normal'))} TB / "
          f"{fmt_int(data.get('rating_bad'))} tệ)")
    print(f" Sản phẩm: {fmt_int(data.get('item_count'))}")
    print(f" Phản hồi: rate={data.get('response_rate')}%  "
          f"time={data.get('response_time')}s")
    print(f" Tham gia: {fmt_ts(data.get('ctime'))}")
    print(f" Last active: {fmt_ts(data.get('last_active_time'))}")
    if data.get("shop_location"):
        print(f" Vị trí  : {data.get('shop_location')}")
    print("=" * 72)


def print_product(index: int, item: dict) -> None:
    rating = (item.get("item_rating") or {}).get("rating_star") or 0
    price_min = item.get("price_min") or item.get("price")
    price_max = item.get("price_max") or item.get("price")
    if price_min and price_max and price_min != price_max:
        price_str = f"{fmt_price(price_min)} - {fmt_price(price_max)}"
    else:
        price_str = fmt_price(price_min)

    name = (item.get("name") or "").strip()
    if len(name) > 72:
        name = name[:69] + "..."
    print(f"{index:>3}. {name}")
    print(
        f"     {price_str}"
        f"  | đã bán: {fmt_int(item.get('historical_sold'))}"
        f"  | kho: {fmt_int(item.get('stock'))}"
        f"  | ★{rating:.1f}"
        f"  | id={item.get('itemid')}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="shopee_tracker")
    parser.add_argument("url", help="Link shop Shopee (shopee.vn/... hoặc s.shopee.vn/...)")
    parser.add_argument("--limit", type=int, default=30, help="Số sản phẩm tối đa (mặc định 30)")
    args = parser.parse_args(argv)

    print(f"[1/3] Resolving URL...")
    ref = resolve(args.url)
    print(f"      → {ref.final_url}")
    print(f"      shopid={ref.shopid} username={ref.username}")
    if not ref.username and not ref.shopid:
        print("Không nhận ra shop từ URL. Kiểm tra lại link.", file=sys.stderr)
        return 2

    client = ShopeeClient()

    print(f"\n[2/3] Lấy thông tin shop...")
    try:
        detail = client.shop_detail(username=ref.username, shopid=ref.shopid)
    except ShopeeAPIError as e:
        print(f"Lỗi gọi API shop_detail: {e}", file=sys.stderr)
        return 1
    data = detail.get("data") or {}
    if not data:
        print(f"Không có data trong response: {detail}", file=sys.stderr)
        return 1
    print_shop_header(data)

    shopid = str(data.get("shopid") or ref.shopid)
    username = (data.get("account") or {}).get("username") or ref.username

    print(f"\n[3/3] Lấy {args.limit} sản phẩm (sort: phổ biến)...\n")
    try:
        count = 0
        for i, item in enumerate(
            client.iter_shop_products(
                shopid=shopid,
                max_items=args.limit,
                referer_username=username,
            ),
            start=1,
        ):
            print_product(i, item)
            count += 1
        print(f"\n→ Đã lấy {count} sản phẩm.")
    except ShopeeAPIError as e:
        print(f"Lỗi khi lấy sản phẩm: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
