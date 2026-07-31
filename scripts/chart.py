"""Weekly stacked-bar charts for the daily digest.

Renders PNG bytes ready to attach via CID. On-brand palette.
Used by digest.py for both the pushup chart and the lifting chart.
"""

from __future__ import annotations

import io
import sqlite3
from datetime import datetime, timedelta, timezone

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter, MaxNLocator

from common import LIFT_KINDS, LOCAL_TZ, PUSHUP_KINDS

# Brand palette, parameterized by theme so we can render matching variants
# for both light-mode and dark-mode mail clients.
LIGHT_PALETTE = dict(
    bg="#F3F1ED",           # off-white (matches light email body)
    text="#1F2221",         # charcoal
    muted="#6F8891",        # slate blue (readable on off-white)
    gridline="#C8C1B8",     # warm stone (subtle on off-white)
    # Triad: third color is steel gray (warm stone would disappear on light bg).
    series=("#6F8891", "#7E8B74", "#3B3F42"),
)
DARK_PALETTE = dict(
    bg="#111315",           # graphite (matches dark email body)
    text="#F3F1ED",         # off-white
    muted="#6F8891",        # slate (works on either)
    gridline="#3B3F42",     # steel gray
    # Third color can be warm stone again since it pops on dark bg.
    series=("#6F8891", "#7E8B74", "#C8C1B8"),
)


def _weekly_buckets(conn, table, value_col, kinds, weeks_back):
    """Return list of {label, kind1, kind2, kind3, total} oldest first."""
    now_local = datetime.now(LOCAL_TZ)
    monday_this_week = (now_local - timedelta(days=now_local.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    buckets = []
    for i in range(weeks_back - 1, -1, -1):
        start_local = monday_this_week - timedelta(weeks=i)
        end_local = start_local + timedelta(weeks=1)
        start_utc = start_local.astimezone(timezone.utc).isoformat()
        end_utc = end_local.astimezone(timezone.utc).isoformat()

        row = {"label": start_local.strftime("%-m/%-d")}
        total = 0
        for kind in kinds:
            n = conn.execute(
                f"SELECT COALESCE(SUM({value_col}),0) FROM {table} "
                f"WHERE kind = ? AND ts_utc >= ? AND ts_utc < ?",
                (kind, start_utc, end_utc),
            ).fetchone()[0]
            row[kind] = n
            total += n
        row["total"] = total
        buckets.append(row)
    return buckets


def _k_formatter(x, _pos):
    """Format axis labels. 1500 -> '1.5k', 30000 -> '30k', 200 -> '200'."""
    if x == 0:
        return "0"
    ax = abs(x)
    if ax >= 10000:
        return f"{x/1000:.0f}k"
    if ax >= 1000:
        return f"{x/1000:.1f}k"
    return f"{x:.0f}"


def _render(buckets, kinds, labels_for_legend, palette) -> bytes:
    """Generic stacked-bar renderer. Used by both metric charts and both themes."""
    bg = palette["bg"]
    muted = palette["muted"]
    gridline = palette["gridline"]
    series_colors = palette["series"]

    x_labels = [b["label"] for b in buckets]
    series_vals = [[b[k] for b in buckets] for k in kinds]

    fig, ax = plt.subplots(figsize=(8, 3.2), dpi=150, facecolor=bg)
    ax.set_facecolor(bg)

    x = list(range(len(x_labels)))
    bar_kwargs = dict(width=0.6, edgecolor=bg, linewidth=1)
    bottoms = [0] * len(x)
    for vals, color, label in zip(series_vals, series_colors, labels_for_legend):
        ax.bar(x, vals, bottom=bottoms, color=color, label=label, **bar_kwargs)
        bottoms = [b + v for b, v in zip(bottoms, vals)]

    ax.set_xticks(x)
    ax.set_xticklabels(x_labels, color=muted, fontsize=9)
    ax.tick_params(axis="x", colors=muted, length=0, pad=8)
    ax.tick_params(axis="y", colors=muted, length=0, pad=4)
    ax.yaxis.set_major_locator(MaxNLocator(integer=True, nbins=5))
    ax.yaxis.set_major_formatter(FuncFormatter(_k_formatter))

    for spine in ("top", "right", "left", "bottom"):
        ax.spines[spine].set_visible(False)

    ax.yaxis.grid(True, color=gridline, linewidth=0.5, alpha=0.8)
    ax.set_axisbelow(True)

    for lbl in ax.get_yticklabels():
        lbl.set_fontsize(9)

    ax.legend(
        loc="upper left",
        bbox_to_anchor=(0.0, 1.18),
        ncol=3,
        frameon=False,
        labelcolor=muted,
        fontsize=9,
        handlelength=1.0,
        handleheight=1.0,
        columnspacing=1.6,
        borderpad=0,
    )

    fig.tight_layout(pad=0.6)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=bg, bbox_inches="tight", pad_inches=0.3)
    plt.close(fig)
    return buf.getvalue()


def _palette_for(theme: str) -> dict:
    return DARK_PALETTE if theme == "dark" else LIGHT_PALETTE


def render_pushups_chart(conn: sqlite3.Connection, weeks_back: int = 12, theme: str = "light") -> bytes:
    buckets = _weekly_buckets(conn, "entries", "count", PUSHUP_KINDS, weeks_back)
    return _render(buckets, PUSHUP_KINDS, ("Standard", "Diamond", "Wide"), _palette_for(theme))


def render_lifts_chart(conn: sqlite3.Connection, weeks_back: int = 12, theme: str = "light") -> bytes:
    buckets = _weekly_buckets(conn, "lifts", "lb", LIFT_KINDS, weeks_back)
    return _render(buckets, LIFT_KINDS, ("Push", "Pull", "Lower"), _palette_for(theme))


# Back-compat alias.
def render_weekly_chart(conn, weeks_back=12):
    return render_pushups_chart(conn, weeks_back)
