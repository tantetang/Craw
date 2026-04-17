"""CLI: resolve a Shopee shop URL and either print info, take a tracked
snapshot (with diff vs previous), or show DB history.

Usage:
    python -m shopee_tracker info      <url> [--limit N]
    python -m shopee_tracker track     <url> [--limit N] [--full] [--db PATH]
    python -m shopee_tracker track-all [--config shops.yaml] [--db PATH]
    python -m shopee_tracker history   <url> [-n N] [--db PATH]
    python -m shopee_tracker export    <url> [--out DIR] [--format csv|excel] [--db PATH]
    python -m shopee_tracker dashboard [--port N] [--db PATH]
    python -m shopee_tracker proxy     {show|set|clear|test|enable|disable}
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from . import db
from .client import HybridClient, ShopeeAPIError, ShopeeClient
from .config import DEFAULT_CONFIG as DEFAULT_CONFIG_PATH
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


def _build_client(args: argparse.Namespace):
    engine = getattr(args, "engine", "curl")
    headless = not getattr(args, "show_browser", False)
    if engine == "curl":
        return ShopeeClient()
    if engine == "playwright":
        from .playwright_client import PlaywrightShopeeClient

        return PlaywrightShopeeClient(headless=headless)
    if engine == "hybrid":
        return HybridClient(headless=headless)
    raise ValueError(f"Engine không hợp lệ: {engine}")


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
    with _build_client(args) as client:
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
    print(
        f"\n[~] Crawl + snapshot → {args.db} "
        f"(engine={args.engine}, limit={args.limit}, full={args.full})"
    )
    with _build_client(args) as client:
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


def cmd_track_all(args: argparse.Namespace) -> int:
    from .config import DEFAULT_CONFIG, load_shops

    config_path = Path(args.config)
    try:
        shops = load_shops(config_path)
    except (FileNotFoundError, ValueError, ImportError) as e:
        print(f"Lỗi đọc config: {e}", file=sys.stderr)
        return 1

    if not shops:
        print("Config không có shop nào.")
        return 0

    print(f"[track-all] {len(shops)} shop từ {config_path}\n")
    errors: list[str] = []
    for i, entry in enumerate(shops, 1):
        alias = entry.alias or entry.url
        print(f"[{i}/{len(shops)}] {alias}")
        if entry.note:
            print(f"  note: {entry.note}")

        # merge engine from config vs CLI (CLI wins if not default)
        engine = args.engine if args.engine != "curl" else entry.engine
        # build a throwaway Namespace for _build_client
        fake = argparse.Namespace(engine=engine, show_browser=args.show_browser)

        try:
            ref = resolve(entry.url)
        except Exception as e:
            print(f"  Resolve lỗi: {e}", file=sys.stderr)
            errors.append(alias)
            continue

        with _build_client(fake) as client:
            try:
                report = track_shop(
                    ref=ref,
                    client=client,
                    limit=entry.limit,
                    db_path=Path(args.db),
                    detect_removed=entry.full,
                )
            except (ShopeeAPIError, Exception) as e:
                print(f"  Crawl lỗi: {e}", file=sys.stderr)
                errors.append(alias)
                continue

        _print_diff_report(report)
        print()

    if errors:
        print(f"\n⚠ {len(errors)} shop lỗi: {', '.join(errors)}", file=sys.stderr)
        return 1
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    from .export import export_all, export_excel

    ref = _resolve_or_exit(args.url)
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"DB chưa tồn tại: {db_path}. Chạy `track` trước.", file=sys.stderr)
        return 1

    with db.connect(db_path) as conn:
        shopid = None
        shop_name = None
        if ref.shopid:
            shopid = int(ref.shopid)
        if ref.username:
            row = db.find_shop_by_username(conn, ref.username)
            if row:
                shopid = shopid or int(row["shopid"])
                shop_name = row["name"] or row["username"]
        if shopid is None:
            print("Không tìm thấy shop trong DB.", file=sys.stderr)
            return 1
        if shop_name is None:
            row2 = conn.execute("SELECT name, username FROM shops WHERE shopid=?", (shopid,)).fetchone()
            if row2:
                shop_name = row2["name"] or row2["username"] or str(shopid)

    out_dir = Path(args.out)
    fmt = args.format

    if fmt == "excel":
        try:
            out_path = out_dir / f"{(shop_name or str(shopid)).replace(' ', '_')}.xlsx"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            export_excel(db_path, shopid, shop_name or "", out_path)
            print(f"Đã xuất Excel: {out_path}")
        except ImportError as e:
            print(f"Lỗi: {e}", file=sys.stderr)
            return 1
    else:
        counts = export_all(db_path, shopid, shop_name or str(shopid), out_dir)
        for key, n in counts.items():
            print(f"  {key}: {n} dòng → {out_dir}")

    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    import os
    import shutil
    import subprocess
    from . import dashboard as _dash_module

    if shutil.which("streamlit") is None:
        print(
            "Streamlit chưa cài. Chạy:\n  pip install streamlit pandas",
            file=sys.stderr,
        )
        return 1

    env = os.environ.copy()
    env["SHOPEE_TRACKER_DB"] = args.db

    dash_file = Path(_dash_module.__file__)
    cmd = [
        "streamlit", "run", str(dash_file),
        "--server.port", str(args.port),
        "--server.headless", "true",
    ]
    print(f"Mở dashboard tại http://localhost:{args.port}")
    return subprocess.call(cmd, env=env)


def cmd_proxy(args: argparse.Namespace) -> int:
    from .proxy import (
        DEFAULT_PROXY_FILE,
        ProxyConfig,
        load_proxy,
        save_proxy,
        test_proxy,
    )

    action = args.action

    if action == "show":
        cfg = load_proxy()
        if cfg is None:
            print("Proxy: (chưa cấu hình hoặc đang disabled)")
            print(f"  File: {DEFAULT_PROXY_FILE.resolve()}")
            print(f"  Env:  SHOPEE_PROXY (chưa set)")
            return 0
        print(f"Proxy: {cfg.display()}")
        print(f"  server   = {cfg.server}")
        print(f"  username = {cfg.username or '(none)'}")
        print(f"  enabled  = {cfg.enabled}")
        return 0

    if action == "set":
        if not args.server:
            print("Thiếu --server. Ví dụ: --server http://proxy.example.com:8080", file=sys.stderr)
            return 2
        cfg = ProxyConfig(
            server=args.server,
            username=args.username or "",
            password=args.password or "",
            enabled=True,
        )
        path = save_proxy(cfg)
        print(f"Đã lưu proxy → {path}")
        print(f"  {cfg.display()}")
        return 0

    if action == "clear":
        if DEFAULT_PROXY_FILE.exists():
            DEFAULT_PROXY_FILE.unlink()
            print(f"Đã xoá {DEFAULT_PROXY_FILE}")
        else:
            print(f"{DEFAULT_PROXY_FILE} chưa tồn tại.")
        return 0

    if action in ("enable", "disable"):
        if not DEFAULT_PROXY_FILE.exists():
            print(f"Chưa có {DEFAULT_PROXY_FILE}. Chạy `proxy set` trước.", file=sys.stderr)
            return 1
        import json as _json
        data = _json.loads(DEFAULT_PROXY_FILE.read_text(encoding="utf-8"))
        data["enabled"] = (action == "enable")
        DEFAULT_PROXY_FILE.write_text(
            _json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"Đã {action} proxy.")
        return 0

    if action == "test":
        cfg = load_proxy()
        if cfg is None:
            print("Chưa cấu hình proxy.", file=sys.stderr)
            return 1
        print(f"Testing {cfg.display()}...")
        ok, msg = test_proxy(cfg)
        print(msg)
        return 0 if ok else 1

    print(f"Action không hợp lệ: {action}", file=sys.stderr)
    return 2


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

    engine_parent = argparse.ArgumentParser(add_help=False)
    engine_parent.add_argument(
        "--engine",
        choices=["curl", "playwright", "hybrid"],
        default="curl",
        help="curl (default, nhanh) | playwright (browser thật) | "
        "hybrid (curl trước, fallback Playwright khi bị block)",
    )
    engine_parent.add_argument(
        "--show-browser",
        action="store_true",
        help="Với playwright/hybrid: chạy non-headless (thấy cửa sổ Chromium)",
    )

    p_info = sub.add_parser(
        "info",
        parents=[engine_parent],
        help="In thông tin shop + danh sách SP (không ghi DB)",
    )
    p_info.add_argument("url")
    p_info.add_argument("--limit", type=int, default=30)
    p_info.set_defaults(func=cmd_info)

    p_track = sub.add_parser(
        "track",
        parents=[engine_parent],
        help="Crawl, lưu snapshot, và in diff vs lần trước",
    )
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

    p_all = sub.add_parser(
        "track-all",
        parents=[engine_parent],
        help="Crawl tất cả shop trong shops.yaml, lưu snapshot + in diff",
    )
    p_all.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Đường dẫn shops.yaml")
    p_all.add_argument("--db", default=str(db.DEFAULT_DB))
    p_all.set_defaults(func=cmd_track_all)

    p_export = sub.add_parser("export", help="Xuất dữ liệu ra CSV / Excel")
    p_export.add_argument("url")
    p_export.add_argument("--out", default="exports", help="Thư mục đầu ra")
    p_export.add_argument(
        "--format",
        choices=["csv", "excel"],
        default="csv",
        help="csv (mặc định) hoặc excel (cần pandas + openpyxl)",
    )
    p_export.add_argument("--db", default=str(db.DEFAULT_DB))
    p_export.set_defaults(func=cmd_export)

    p_dash = sub.add_parser("dashboard", help="Mở Streamlit dashboard")
    p_dash.add_argument("--port", type=int, default=8501)
    p_dash.add_argument("--db", default=str(db.DEFAULT_DB))
    p_dash.set_defaults(func=cmd_dashboard)

    p_proxy = sub.add_parser(
        "proxy",
        help="Quản lý proxy (show/set/clear/test/enable/disable)",
    )
    p_proxy.add_argument(
        "action",
        choices=["show", "set", "clear", "test", "enable", "disable"],
    )
    p_proxy.add_argument("--server", help="Ví dụ: http://proxy.example.com:8080 hoặc socks5://host:port")
    p_proxy.add_argument("--username", default="")
    p_proxy.add_argument("--password", default="")
    p_proxy.set_defaults(func=cmd_proxy)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
