"""Streamlit dashboard — chạy bằng:

    python -m shopee_tracker dashboard
    # hoặc trực tiếp:
    streamlit run shopee_tracker/dashboard.py

Biến môi trường:
    SHOPEE_TRACKER_DB   đường dẫn đến file SQLite (mặc định shopee_tracker.sqlite)
"""

from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

# Dashboard tự import pandas — không cần cài cho phần còn lại của package
try:
    import pandas as pd
    _HAS_PANDAS = True
except ImportError:
    _HAS_PANDAS = False

from . import db

PRICE_DIV = 100_000

st.set_page_config(
    page_title="Shopee Tracker",
    page_icon="🛍️",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Cached loaders
# ---------------------------------------------------------------------------

@st.cache_data(ttl=60)
def _load_shops(db_path: str) -> list[dict]:
    with db.connect(Path(db_path)) as conn:
        rows = conn.execute(
            "SELECT shopid, username, name, first_seen, last_seen FROM shops ORDER BY last_seen DESC"
        ).fetchall()
    return [dict(r) for r in rows]


@st.cache_data(ttl=60)
def _load_shop_timeline(db_path: str, shopid: int) -> list[dict]:
    with db.connect(Path(db_path)) as conn:
        rows = conn.execute(
            """SELECT ts, follower_count, item_count, rating_star,
                      rating_good, rating_bad, response_rate
               FROM shop_snapshots
               WHERE shopid = ?
               ORDER BY ts""",
            (shopid,),
        ).fetchall()
    return [dict(r) for r in rows]


@st.cache_data(ttl=60)
def _load_products_latest(db_path: str, shopid: int) -> list[dict]:
    with db.connect(Path(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT p.itemid, p.name, p.first_seen,
                   ps.ts AS latest_ts,
                   CAST(ps.price_min AS REAL) / 100000 AS gia_min,
                   CAST(ps.price_max AS REAL) / 100000 AS gia_max,
                   ps.stock, ps.historical_sold, ps.rating_star
            FROM products p
            LEFT JOIN (
                SELECT ps1.*
                FROM product_snapshots ps1
                INNER JOIN (
                    SELECT itemid, MAX(ts) AS m
                    FROM product_snapshots WHERE shopid = ?
                    GROUP BY itemid
                ) l ON l.itemid = ps1.itemid AND l.m = ps1.ts
                WHERE ps1.shopid = ?
            ) ps ON ps.itemid = p.itemid
            WHERE p.shopid = ?
            ORDER BY ps.historical_sold DESC NULLS LAST
            """,
            (shopid, shopid, shopid),
        ).fetchall()
    return [dict(r) for r in rows]


@st.cache_data(ttl=60)
def _load_product_history(db_path: str, shopid: int, itemid: int) -> list[dict]:
    with db.connect(Path(db_path)) as conn:
        rows = conn.execute(
            """SELECT ts,
                      CAST(price_min AS REAL) / 100000 AS gia_min,
                      CAST(price_max AS REAL) / 100000 AS gia_max,
                      stock, historical_sold, rating_star
               FROM product_snapshots
               WHERE shopid = ? AND itemid = ?
               ORDER BY ts""",
            (shopid, itemid),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Helper: convert unix ts list → date strings (no pandas needed)
# ---------------------------------------------------------------------------

def _ts_to_str(ts: int) -> str:
    from datetime import datetime
    try:
        return datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")
    except Exception:
        return str(ts)


def _rows_to_chart_data(rows: list[dict], x_key: str, y_keys: list[str]) -> dict:
    """Build dict suitable for st.line_chart (index + columns)."""
    if not rows:
        return {}
    if _HAS_PANDAS:
        import pandas as pd
        df = pd.DataFrame(rows)
        df[x_key] = pd.to_datetime(df[x_key], unit="s")
        return df.set_index(x_key)[y_keys]
    # Fallback without pandas: dict[col → list]
    result = {y: [r[y] for r in rows] for y in y_keys}
    return result


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------

def main() -> None:
    st.title("🛍️ Shopee Competitor Tracker")

    # --- sidebar: DB + shop selector ------------------------------------------
    default_db = os.environ.get("SHOPEE_TRACKER_DB", "shopee_tracker.sqlite")
    db_path = st.sidebar.text_input("SQLite DB", value=default_db)

    if not Path(db_path).exists():
        st.error(
            f"DB không tồn tại: **{db_path}**  \n"
            "Hãy chạy `python -m shopee_tracker track <url>` trước."
        )
        return

    shops = _load_shops(db_path)
    if not shops:
        st.warning("Chưa có shop nào trong DB.")
        return

    def _shop_label(s: dict) -> str:
        name = s["name"] or "?"
        uname = s["username"] or "?"
        return f"{name} (@{uname})"

    selected = st.sidebar.selectbox(
        "Chọn shop",
        options=shops,
        format_func=_shop_label,
    )
    shopid = int(selected["shopid"])

    st.sidebar.markdown("---")
    st.sidebar.caption(
        f"shopid: `{shopid}`  \n"
        f"username: `{selected['username']}`  \n"
        f"theo dõi từ: {_ts_to_str(selected['first_seen'])}"
    )

    # --- header ---------------------------------------------------------------
    st.header(_shop_label(selected))

    # --- KPI row từ snapshot mới nhất -----------------------------------------
    timeline = _load_shop_timeline(db_path, shopid)
    if timeline:
        latest = timeline[-1]
        prev   = timeline[-2] if len(timeline) > 1 else None

        def _delta(key: str):
            if prev is None:
                return None
            old = prev.get(key)
            new = latest.get(key)
            if old is None or new is None:
                return None
            return new - old

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Follower",   f"{latest['follower_count'] or 0:,}",
                  delta=_delta("follower_count"))
        c2.metric("Sản phẩm",  f"{latest['item_count'] or 0:,}",
                  delta=_delta("item_count"))
        c3.metric("Rating ★",  f"{latest['rating_star'] or 0:.2f}",
                  delta=round(_delta("rating_star") or 0, 3) or None)
        c4.metric("Phản hồi",  f"{latest['response_rate'] or 0}%",
                  delta=_delta("response_rate"))

        # --- timeline chart ---------------------------------------------------
        st.subheader("📈 Timeline shop")
        chart_data = _rows_to_chart_data(timeline, "ts", ["follower_count", "item_count"])
        if chart_data is not None:
            st.line_chart(chart_data)

    # --- product table --------------------------------------------------------
    st.subheader("🛒 Sản phẩm (snapshot mới nhất)")
    products = _load_products_latest(db_path, shopid)

    if not products:
        st.info("Chưa có sản phẩm nào trong DB.")
    else:
        # search filter
        q = st.text_input("Tìm tên SP", placeholder="Nhập từ khóa...").strip().lower()
        if q:
            products = [p for p in products if q in (p["name"] or "").lower()]

        display_keys = ["itemid", "name", "gia_min", "stock", "historical_sold", "rating_star"]
        display_labels = ["ID", "Tên sản phẩm", "Giá (VND)", "Kho", "Đã bán", "★"]

        if _HAS_PANDAS:
            import pandas as pd
            df = pd.DataFrame(products)[display_keys]
            df.columns = display_labels
            df["Giá (VND)"] = df["Giá (VND)"].apply(
                lambda v: f"{v:,.0f}" if v else "-"
            )
            st.dataframe(df, use_container_width=True, height=380)
        else:
            # basic table without pandas
            header = "| " + " | ".join(display_labels) + " |"
            sep    = "| " + " | ".join(["---"] * len(display_labels)) + " |"
            rows_md = [header, sep]
            for p in products[:200]:
                row = [
                    str(p["itemid"]),
                    (p["name"] or "")[:60],
                    f"{p['gia_min']:,.0f}" if p.get("gia_min") else "-",
                    str(p.get("stock") or "-"),
                    str(p.get("historical_sold") or 0),
                    str(p.get("rating_star") or "-"),
                ]
                rows_md.append("| " + " | ".join(row) + " |")
            st.markdown("\n".join(rows_md))

        # --- price history chart for single product ---------------------------
        st.subheader("📊 Lịch sử giá / lượt bán — 1 sản phẩm")
        item_options = products[:300]
        selected_item = st.selectbox(
            "Chọn sản phẩm",
            options=item_options,
            format_func=lambda p: f"[{p['itemid']}] {(p['name'] or '')[:70]}",
        )
        if selected_item:
            hist = _load_product_history(db_path, shopid, int(selected_item["itemid"]))
            if len(hist) < 2:
                st.info("Chưa đủ 2 snapshot để vẽ biểu đồ. Chạy track thêm lần nữa.")
            else:
                price_data = _rows_to_chart_data(hist, "ts", ["gia_min"])
                sold_data  = _rows_to_chart_data(hist, "ts", ["historical_sold"])
                col_price, col_sold = st.columns(2)
                with col_price:
                    st.caption("Giá min (VND)")
                    st.line_chart(price_data)
                with col_sold:
                    st.caption("Tổng đã bán")
                    st.line_chart(sold_data)

                if _HAS_PANDAS:
                    import pandas as pd
                    st.dataframe(
                        pd.DataFrame(hist).assign(
                            thoi_diem=lambda df: pd.to_datetime(df["ts"], unit="s")
                        ).drop(columns=["ts"]),
                        use_container_width=True,
                    )


if __name__ == "__main__":
    main()
