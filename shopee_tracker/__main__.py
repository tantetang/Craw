"""CLI: resolve a Shopee shop URL and either print info, take a tracked
snapshot (with diff vs previous), or show DB history.

Usage:
    python -m shopee_tracker info    <url> [--limit N]
    python -m shopee_tracker track   <url> [--limit N] [--full] [--db PATH]
    python -m shopee_tracker history <url> [-n N] [--db PATH]
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from . import db
from .client import ShopeeAPIError, ShopeeClient
from .resolver import ShopRef, resolve
from .tracker import DiffReport, track_shop


PRICE_DIV = 100_000


def fmt_price(value: int | None) -> str:
    if value is None or value == 0:
        return "-"
    return f"{value / PRICE_DIV:,.0f}đ"


def fmt_int(value: int | None) -> str:
    if value is None:
        return "-"
    return f"{value:,}"


def fmt_ts(ts: int | None) -> str:
    if not ts:
        return "-"
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except (OSError, ValueError):
        return str(ts)


def truncate(s: str, n: int = 70) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def print_shop_header(data: dict) -> None:
    print("=" * 72)
    print(f" Shop    : {data.get('name')}")
    print(f" ShopID  : {data.get('shopid')}")
    print(f" Account : @{(data.get('account') or {}).get('username', '?')}")
    print(
        f" Theo dõi: {fmt_int(data.get('follower_count'))}"
        f"   | Đang theo dõi: {fmt_int(data.get('following_count'))}"
    )
    print(
        f" Rating  : {data.get('rating_star')}  "
        f"({fmt_int(data.get('rating_good'))} tốt / "
        f"{fmt_int(data.get('rating_normal'))} TB / "
        f"{fmt_int(data.get('rating_bad'))} tệ)"
    )
    print(f" Sản phẩm: {fmt_int(data.get('item_count'))}")
    print(
        f" Phản hồi: rate={data.get('response_rate')}%  "
        f"time={data.get('response_time')}s"
    )
    print(f" Tham gia: {fmt_ts(data.get('ctime'))}")
    print("=" * 72)


def print_product(index: int, item: dict) -> None:
    rating = (item.get("item_rating") or {}).get("rating_star") or 0
    price_min = item.get("price_min") or item.get("price")
    price_max = item.get("price_max") or item.get("price")
    if price_min and price_max and price_min != price_max:
        price_str = f"{fmt_price(price_min)} - {fmt_price(price_max)}"
    else:
        price_str = fmt_price(price_min)
    print(f"{index:>3}. {truncate(item.get('name') or '', 72)}")
    print(
        f"     {price_str}"
        f"  | đã bán: {fmt_int(item.get('historical_sold'))}"
        f"  | kho: {fmt_int(item.get('stock'))}"
        f"  | ★{rating:.1f}"
        f"  | id={item.get('itemid')}"
    )


def _resolve_or_exit(url: str) -> ShopRef:
    print(f"[~] Resolving: {url}")
    ref = resolve(url)
    print(f"    → {ref.final_url}")
    print(f"    shopid={ref.shopid} username={ref.username}")
    if not ref.username and not ref.shopid:
        print("Không nhận ra shop từ URL.", file=sys.stderr)
        sys.exit(2)
    return ref


def cmd_info(args: argparse.Namespace) -> int:
    ref = _resolve_or_exit(args.url)
    client = ShopeeClient()
    try:
        detail = client.shop_detail(username=ref.username, shopid=ref.shopid)
    except ShopeeAPIError as e:
        print(f"shop_detail lỗi: {e}", file=sys.stderr)
        return 1
    data = detail.get("data") or {}
    if not data:
        print(f"Response không có data: {detail}", file=sys.stderr)
        return 1
    print_shop_header(data)

    shopid = str(data.get("shopid") or ref.shopid)
    username = (data.get("account") or {}).get("username") or ref.username

    print(f"\nLấy {args.limit} sản phẩm...\n")
    try:
        count = 0
        for i, item in enumerate(
            client.iter_shop_products(
                shopid=shopid, max_items=args.limit, referer_username=username
            ),
            start=1,
        ):
            print_product(i, item)
            count += 1
        print(f"\n→ Đã lấy {count} sản phẩm.")
    except ShopeeAPIError as e:
        print(f"search_items lỗi: {e}", file=sys.stderr)
        return 1
    return 0


def _print_diff_report(report: DiffReport) -> None:
    print("\n" + "=" * 72)
    print(f" DIFF REPORT: {report.shop_name} (shopid={report.shopid})")
    print("=" * 72)

    if report.first_run:
        print("→ Lần đầu crawl shop này, chưa có snapshot cũ để so sánh.")
        return

    empty = (
        not report.shop_changes
        and not report.new_products
        and not report.price_changes
        and not report.sold_deltas
        and not report.stock_changes
        and not report.disappeared
    )
    if empty:
        print("→ Không có thay đổi so với lần crawl trước.")
        return

    if report.shop_changes:
        print("\n-- THAY ĐỔI SHOP --")
        for c in report.shop_changes:
            print(f"   {c.field}: {c.old} → {c.new}")

    if report.new_products:
        print(f"\n-- SẢN PHẨM MỚI ({len(report.new_products)}) --")
        for c in report.new_products[:50]:
            print(f"   + [{c.itemid}] {fmt_price(c.new)}  {truncate(c.name, 60)}")
        if len(report.new_products) > 50:
            print(f"   ... và {len(report.new_products) - 50} SP mới khác")

    if report.price_changes:
        print(f"\n-- GIÁ THAY ĐỔI ({len(report.price_changes)}) --")
        for c in sorted(
            report.price_changes,
            key=lambda x: abs((x.new or 0) - (x.old or 0)),
            reverse=True,
        )[:30]:
            arrow = "↓" if (c.new or 0) < (c.old or 0) else "↑"
            print(
                f"   {arrow} [{c.itemid}] {fmt_price(c.old)} → {fmt_price(c.new)}"
                f"  {truncate(c.name, 50)}"
            )

    if report.sold_deltas:
        print(f"\n-- ĐÃ BÁN TĂNG ({len(report.sold_deltas)}) --")
        for c in sorted(report.sold_deltas, key=lambda x: x.delta or 0, reverse=True)[:30]:
            print(
                f"   +{c.delta:,} (tổng {c.new:,})  [{c.itemid}]  "
                f"{truncate(c.name, 50)}"
            )

    if report.stock_changes:
        print(f"\n-- KHO THAY ĐỔI ({len(report.stock_changes)}) --")
        for c in report.stock_changes[:30]:
            print(
                f"   [{c.itemid}] {c.old} → {c.new}  {truncate(c.name, 50)}"
            )

    if report.disappeared:
        print(f"\n-- SP BIẾN MẤT ({len(report.disappeared)}) --")
        for c in report.disappeared[:30]:
            print(f"   [{c.itemid}] {truncate(c.name, 60)}")


def cmd_track(args: argparse.Namespace) -> int:
    ref = _resolve_or_exit(args.url)
    client = ShopeeClient()
    print(f"\n[~] Crawl + snapshot → {args.db} (limit={args.limit}, full={args.full})")
    try:
        report = track_shop(
            ref=ref,
            client=client,
            limit=args.limit,
            db_path=Path(args.db),
            detect_removed=args.full,
        )
    except ShopeeAPIError as e:
        print(f"Crawl lỗi: {e}", file=sys.stderr)
        return 1
    _print_diff_report(report)
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    ref = _resolve_or_exit(args.url)
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"DB chưa tồn tại: {db_path}. Chạy `track` trước.", file=sys.stderr)
        return 1

    with db.connect(db_path) as conn:
        shopid = None
        if ref.shopid:
            shopid = int(ref.shopid)
        elif ref.username:
            row = db.find_shop_by_username(conn, ref.username)
            if row:
                shopid = int(row["shopid"])
        if shopid is None:
            print("Không tìm thấy shop trong DB.", file=sys.stderr)
            return 1

        snaps = db.list_shop_snapshots(conn, shopid, limit=args.n)
        if not snaps:
            print(f"Shopid={shopid} chưa có snapshot.")
            return 0
        print(f"\n{len(snaps)} snapshot gần nhất của shopid={shopid}:\n")
        print(
            f"  {'Thời điểm':<18}  {'Follower':>10}  {'SP':>7}  "
            f"{'★':>4}  {'Resp%':>6}"
        )
        for s in snaps:
            print(
                f"  {fmt_ts(s['ts']):<18}  {fmt_int(s['follower_count']):>10}  "
                f"{fmt_int(s['item_count']):>7}  "
                f"{(s['rating_star'] or 0):>4.2f}  "
                f"{fmt_int(s['response_rate']):>6}"
            )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="shopee_tracker")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_info = sub.add_parser("info", help="In thông tin shop + danh sách SP (không ghi DB)")
    p_info.add_argument("url")
    p_info.add_argument("--limit", type=int, default=30)
    p_info.set_defaults(func=cmd_info)

    p_track = sub.add_parser("track", help="Crawl, lưu snapshot, và in diff vs lần trước")
    p_track.add_argument("url")
    p_track.add_argument("--limit", type=int, default=100)
    p_track.add_argument("--db", default=str(db.DEFAULT_DB))
    p_track.add_argument(
        "--full",
        action="store_true",
        help="Giả định --limit bao phủ toàn shop, phát hiện SP biến mất",
    )
    p_track.set_defaults(func=cmd_track)

    p_hist = sub.add_parser("history", help="Xem snapshot lịch sử shop từ DB")
    p_hist.add_argument("url")
    p_hist.add_argument("-n", type=int, default=15)
    p_hist.add_argument("--db", default=str(db.DEFAULT_DB))
    p_hist.set_defaults(func=cmd_history)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
