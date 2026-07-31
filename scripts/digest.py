#!/usr/bin/env python3
"""Send the daily digest (pushups + lifting).

Run from cron at the desired time. Subject is stable so replies thread.
"""

from __future__ import annotations

import smtplib
import sys
from datetime import datetime
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid

from common import (
    DIGEST_SUBJECT,
    LIFT_KINDS,
    LOCAL_TZ,
    PUSHUP_KINDS,
    all_prs,
    db,
    lift_totals,
    load_env,
    totals,
)

try:
    from chart import render_lifts_chart, render_pushups_chart
    CHART_AVAILABLE = True
except Exception as e:
    CHART_AVAILABLE = False
    _CHART_IMPORT_ERROR = str(e)

PUSHUPS_CID_LIGHT = "pushups-chart-light"
PUSHUPS_CID_DARK = "pushups-chart-dark"
LIFTS_CID_LIGHT = "lifts-chart-light"
LIFTS_CID_DARK = "lifts-chart-dark"


# --- Helpers --------------------------------------------------------------

def _fmt_num(n: int) -> str:
    """Plain integer with thousand separator."""
    return f"{n:,}"


def _fmt_lb(n: int) -> str:
    """Pounds with thousand separator and unit."""
    return f"{n:,} lb"


def _fmt_date_range(monday, sunday) -> str:
    """'Apr 13 - Apr 19', ASCII hyphen."""
    if monday is None:
        return ""
    if monday.year != datetime.now(LOCAL_TZ).year:
        return f"{monday.strftime('%b %-d, %Y')} - {sunday.strftime('%b %-d, %Y')}"
    return f"{monday.strftime('%b %-d')} - {sunday.strftime('%b %-d')}"


def _table_html(rows, total_row, value_fmt) -> str:
    """Render a 2-column 'kind / value' table with a moss-colored total row."""
    out = ['<table class="grid" role="presentation">']
    for label, value in rows:
        out.append(
            f'<tr><td class="label">{label}</td>'
            f'<td class="value">{value_fmt(value)}</td></tr>'
        )
    label, value = total_row
    out.append(
        f'<tr class="total"><td class="label">{label}</td>'
        f'<td class="value">{value_fmt(value)}</td></tr>'
    )
    out.append("</table>")
    return "\n".join(out)


def _tile_html(label: str, value_str: str, subtext: str, is_total: bool) -> str:
    """One PR tile. White card with warm stone border. Total uses moss for the label."""
    label_color = "#7E8B74" if is_total else "#6F8891"
    subtext_html = (
        f'<div class="tile-sub" style="font-size:11px;color:#6F8891;margin-top:6px;'
        f'font-variant-numeric:tabular-nums;">{subtext}</div>'
        if subtext else
        '<div class="tile-sub" style="font-size:11px;color:#6F8891;margin-top:6px;">&nbsp;</div>'
    )
    return (
        '<td class="tile" style="width:50%;background-color:#FFFFFF;'
        'border:1px solid #C8C1B8;padding:16px 18px;border-radius:2px;vertical-align:top;">'
        f'<div class="tile-label" style="font-size:11px;letter-spacing:0.12em;'
        f'text-transform:uppercase;color:{label_color};">{label}</div>'
        f'<div class="tile-value" style="font-size:22px;font-weight:500;color:#1F2221;'
        f'margin-top:8px;font-variant-numeric:tabular-nums;">{value_str}</div>'
        f'{subtext_html}'
        '</td>'
    )


def _tiles_grid_html(prs: dict, kinds: tuple, value_fmt) -> str:
    """2x2 grid of tiles. Order: Total | kind1, kind2 | kind3."""
    order = ("total",) + kinds  # 4 entries

    def cell(key):
        v, monday, sunday = prs.get(key, (0, None, None))
        label = "Total" if key == "total" else key.capitalize()
        value_str = value_fmt(v) if v else "0"
        subtext = _fmt_date_range(monday, sunday)
        return _tile_html(label, value_str, subtext, is_total=(key == "total"))

    return (
        '<table role="presentation" style="width:100%;border-collapse:separate;'
        'border-spacing:8px 8px;margin-left:-8px;margin-right:-8px;">'
        f'<tr>{cell(order[0])}{cell(order[1])}</tr>'
        f'<tr>{cell(order[2])}{cell(order[3])}</tr>'
        '</table>'
    )


def _col_header_html(text: str) -> str:
    """Small metric-name header that sits atop a column."""
    return (
        f'<div class="col-header" style="font-size:13px;font-weight:600;color:#1F2221;'
        f'margin-bottom:10px;letter-spacing:-0.01em;">{text}</div>'
    )


def _two_col_section_html(left_header, left_table, right_header, right_table) -> str:
    """Pushups table on the left, Lifting table on the right, sharing one row."""
    return (
        '<table role="presentation" style="width:100%;border-collapse:collapse;margin-bottom:32px;">'
        '<tr>'
        '<td style="width:50%;vertical-align:top;padding-right:11px;">'
        f'{_col_header_html(left_header)}{left_table}'
        '</td>'
        '<td style="width:50%;vertical-align:top;padding-left:11px;">'
        f'{_col_header_html(right_header)}{right_table}'
        '</td>'
        '</tr>'
        '</table>'
    )


def _paired_tiles_html(pushup_prs: dict, lift_prs: dict) -> str:
    """PR tiles paired by row: pushups down the left, lifting down the right.

    Row order: Total, then Standard/Push, Diamond/Pull, Wide/Lower.
    """
    rows = (
        ("total", "total"),
        ("standard", "push"),
        ("diamond", "pull"),
        ("wide", "lower"),
    )

    def cell(prs, key, value_fmt):
        v, monday, sunday = prs.get(key, (0, None, None))
        label = "Total" if key == "total" else key.capitalize()
        value_str = value_fmt(v) if v else "0"
        subtext = _fmt_date_range(monday, sunday)
        return _tile_html(label, value_str, subtext, is_total=(key == "total"))

    trs = "".join(
        f'<tr>{cell(pushup_prs, pk, _fmt_num)}{cell(lift_prs, lk, _fmt_lb)}</tr>'
        for pk, lk in rows
    )
    return (
        '<table role="presentation" style="width:100%;border-collapse:separate;'
        'border-spacing:8px 8px;margin-left:-8px;margin-right:-8px;">'
        f'{trs}'
        '</table>'
    )


# --- HTML rendering -------------------------------------------------------

def render_html(
    pt: dict,
    lt: dict,
    pushup_prs: dict,
    lift_prs: dict,
    today_str: str,
    has_pushup_chart: bool,
    has_lift_chart: bool,
) -> str:
    # Chart captions sit above each stacked chart so it's clear which is which.
    pushup_chart_block = ""
    if has_pushup_chart:
        pushup_chart_block = f"""
            {_col_header_html("Pushups")}
            <div class="chart-wrap" style="margin:0 0 24px 0;border:1px solid #C8C1B8;border-radius:2px;background-color:#F3F1ED;overflow:hidden;">
              <picture>
                <source srcset="cid:{PUSHUPS_CID_DARK}" media="(prefers-color-scheme: dark)">
                <img src="cid:{PUSHUPS_CID_LIGHT}" alt="Pushups, last 12 weeks"
                     style="width:100%;max-width:560px;height:auto;display:block;">
              </picture>
            </div>
"""
    lift_chart_block = ""
    if has_lift_chart:
        lift_chart_block = f"""
            {_col_header_html("Lifting")}
            <div class="chart-wrap" style="margin:0 0 32px 0;border:1px solid #C8C1B8;border-radius:2px;background-color:#F3F1ED;overflow:hidden;">
              <picture>
                <source srcset="cid:{LIFTS_CID_DARK}" media="(prefers-color-scheme: dark)">
                <img src="cid:{LIFTS_CID_LIGHT}" alt="Lifting volume, last 12 weeks"
                     style="width:100%;max-width:560px;height:auto;display:block;">
              </picture>
            </div>
"""

    pushup_this_week = _table_html(
        [("Standard", pt["this_week"]["standard"]),
         ("Diamond", pt["this_week"]["diamond"]),
         ("Wide", pt["this_week"]["wide"])],
        ("Total", pt["this_week"]["total"]),
        _fmt_num,
    )
    pushup_all_time = _table_html(
        [("Standard", pt["all_time"]["standard"]),
         ("Diamond", pt["all_time"]["diamond"]),
         ("Wide", pt["all_time"]["wide"])],
        ("Total", pt["all_time"]["total"]),
        _fmt_num,
    )
    lift_this_week = _table_html(
        [("Push", lt["this_week"]["push"]),
         ("Pull", lt["this_week"]["pull"]),
         ("Lower", lt["this_week"]["lower"])],
        ("Total", lt["this_week"]["total"]),
        _fmt_lb,
    )
    lift_all_time = _table_html(
        [("Push", lt["all_time"]["push"]),
         ("Pull", lt["all_time"]["pull"]),
         ("Lower", lt["all_time"]["lower"])],
        ("Total", lt["all_time"]["total"]),
        _fmt_lb,
    )

    # Integrated: pushups + lifting share each time-period section, side by side.
    this_week_section = _two_col_section_html(
        "Pushups", pushup_this_week, "Lifting", lift_this_week
    )
    all_time_section = _two_col_section_html(
        "Pushups", pushup_all_time, "Lifting", lift_all_time
    )
    best_weeks_section = _paired_tiles_html(pushup_prs, lift_prs)

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<style>
  body {{
    margin:0;padding:0;background-color:#F3F1ED;
    font-family:'Inter',-apple-system,BlinkMacSystemFont,'Helvetica Neue',Arial,sans-serif;
    -webkit-font-smoothing:antialiased;
  }}
  .eyebrow {{
    font-size:11px;letter-spacing:0.12em;text-transform:uppercase;
    color:#6F8891;margin-bottom:24px;
  }}
  h1 {{
    font-size:22px;font-weight:500;color:#1F2221;margin:0 0 40px 0;
    letter-spacing:-0.01em;
  }}
  h2 {{
    font-size:18px;font-weight:500;color:#1F2221;margin:0 0 28px 0;
    letter-spacing:-0.01em;
  }}
  .section-label {{
    font-size:11px;letter-spacing:0.12em;text-transform:uppercase;
    color:#6F8891;margin-bottom:14px;
  }}
  .section-divider {{
    height:1px;background-color:#C8C1B8;margin:24px 0 40px 0;border:none;
  }}
  .grid {{ width:100%;border-collapse:collapse;margin-bottom:32px; }}
  .grid td {{ padding:14px 0;border-bottom:1px solid #C8C1B8;font-size:15px; }}
  .grid td.label {{ color:#3B3F42; }}
  .grid td.value {{ text-align:right;color:#1F2221;font-variant-numeric:tabular-nums; }}
  .grid tr.total td {{
    border-bottom:none;padding-top:18px;font-size:17px;color:#1F2221;font-weight:500;
  }}
  .grid tr.total td.label {{ color:#7E8B74; }}
  .chart-wrap {{
    margin:0 0 32px 0;border:1px solid #C8C1B8;border-radius:2px;
    background-color:#F3F1ED;overflow:hidden;
  }}
  .footer {{
    margin-top:48px;padding-top:24px;border-top:1px solid #C8C1B8;
    font-size:12px;color:#6F8891;line-height:1.6;
  }}
  .footer code {{
    background-color:#FFFFFF;border:1px solid #C8C1B8;padding:2px 6px;
    border-radius:2px;color:#3B3F42;
    font-family:'SFMono-Regular',Menlo,Consolas,monospace;font-size:11px;
  }}
  .footer .footer-label {{ color:#7E8B74; }}

  .howto {{
    margin:0 0 40px 0;padding:14px 16px;background-color:#FFFFFF;
    border:1px solid #C8C1B8;border-radius:2px;
    font-size:13px;color:#3B3F42;line-height:1.7;
  }}
  .howto code {{
    background-color:#F3F1ED;border:1px solid #C8C1B8;padding:1px 6px;
    border-radius:2px;color:#1F2221;
    font-family:'SFMono-Regular',Menlo,Consolas,monospace;font-size:12px;
  }}
  .howto .howto-hint {{ color:#6F8891; }}

  /* Dark mode for clients that honor prefers-color-scheme. The chart PNG
     stays light; everything else flips. */
  @media (prefers-color-scheme: dark) {{
    body, .outer, .inner {{ background-color:#111315 !important; }}
    h1, h2 {{ color:#F3F1ED !important; }}
    .col-header {{ color:#F3F1ED !important; }}
    .grid td.label {{ color:#C8C1B8 !important; }}
    .grid td.value {{ color:#F3F1ED !important; }}
    .grid tr.total td {{ color:#F3F1ED !important; }}
    .grid td {{ border-bottom-color:#3B3F42 !important; }}
    .section-divider {{ background-color:#3B3F42 !important; }}
    .chart-wrap {{
      background-color:#111315 !important;
      border-color:#3B3F42 !important;
    }}
    .howto {{
      background-color:#1F2221 !important;
      border-color:#3B3F42 !important;
      color:#C8C1B8 !important;
    }}
    .howto code {{
      background-color:#111315 !important;
      border-color:#3B3F42 !important;
      color:#F3F1ED !important;
    }}
    .footer {{ border-top-color:#3B3F42 !important; }}
    .footer code {{
      background-color:#1F2221 !important;border-color:#3B3F42 !important;
      color:#C8C1B8 !important;
    }}
    .tile {{
      background-color:#1F2221 !important;
      border-color:#3B3F42 !important;
    }}
    .tile-value {{ color:#F3F1ED !important; }}
  }}
</style>
</head>
<body style="margin:0;padding:0;background-color:#F3F1ED;">
<table role="presentation" class="outer" width="100%" border="0" cellpadding="0" cellspacing="0"
       bgcolor="#F3F1ED" style="background-color:#F3F1ED;width:100%;">
  <tr>
    <td align="center" valign="top" bgcolor="#F3F1ED" style="background-color:#F3F1ED;">
      <table role="presentation" class="inner" width="560" border="0" cellpadding="0" cellspacing="0"
             bgcolor="#F3F1ED" style="background-color:#F3F1ED;max-width:560px;width:100%;">
        <tr>
          <td style="padding:48px 32px;color:#1F2221;
                     font-family:'Inter',-apple-system,BlinkMacSystemFont,'Helvetica Neue',Arial,sans-serif;">

            <div class="eyebrow" style="font-size:11px;letter-spacing:0.12em;text-transform:uppercase;color:#6F8891;margin-bottom:24px;">{today_str}</div>
            <h1 style="font-size:22px;font-weight:500;color:#1F2221;margin:0 0 24px 0;letter-spacing:-0.01em;">{DIGEST_SUBJECT}</h1>

            <div class="howto" style="margin:0 0 40px 0;padding:14px 16px;background-color:#FFFFFF;border:1px solid #C8C1B8;border-radius:2px;font-size:13px;color:#3B3F42;line-height:1.7;">
              reply <code style="background-color:#F3F1ED;border:1px solid #C8C1B8;padding:1px 6px;border-radius:2px;color:#1F2221;font-family:'SFMono-Regular',Menlo,Consolas,monospace;font-size:12px;">#/#/#</code> <span class="howto-hint" style="color:#6F8891;">(standard/dia/wide)</span> to log pushups<br>
              reply <code style="background-color:#F3F1ED;border:1px solid #C8C1B8;padding:1px 6px;border-radius:2px;color:#1F2221;font-family:'SFMono-Regular',Menlo,Consolas,monospace;font-size:12px;">lift #/#/#</code> <span class="howto-hint" style="color:#6F8891;">(push/pull/lower)</span> to log weight lifting
            </div>

            <div class="section-label" style="font-size:11px;letter-spacing:0.12em;text-transform:uppercase;color:#6F8891;margin-bottom:14px;">This week</div>
            {this_week_section}

            <div class="section-label" style="font-size:11px;letter-spacing:0.12em;text-transform:uppercase;color:#6F8891;margin-bottom:14px;">Last 12 weeks</div>
{pushup_chart_block}{lift_chart_block}
            <div class="section-label" style="font-size:11px;letter-spacing:0.12em;text-transform:uppercase;color:#6F8891;margin-bottom:14px;">All time</div>
            {all_time_section}

            <div class="section-label" style="font-size:11px;letter-spacing:0.12em;text-transform:uppercase;color:#6F8891;margin-bottom:14px;">Best weeks</div>
            <div style="margin-bottom:32px;">{best_weeks_section}</div>

            <div class="footer" style="margin-top:48px;padding-top:24px;border-top:1px solid #C8C1B8;font-size:12px;color:#6F8891;line-height:1.6;">
              Reply to log.<br><br>

              <span class="footer-label" style="color:#7E8B74;">Pushups</span><br>
              <code>20 standard</code> &nbsp;
              <code>20s</code> &nbsp;
              <code>10 std, 5 dia, 5 wide</code> &nbsp;
              <code>20/10/5</code> (std/dia/wide)<br><br>

              <span class="footer-label" style="color:#7E8B74;">Lifting</span><br>
              <code>push 4050</code> &nbsp;
              <code>pull 3000</code> &nbsp;
              <code>lower 6200</code> &nbsp;
              <code>lift 4050/3000/6200</code> (push/pull/lower)<br><br>

              <span class="footer-label" style="color:#7E8B74;">Push:</span> floor press, overhead press, lateral raises, flyes, tricep extensions<br>
              <span class="footer-label" style="color:#7E8B74;">Pull:</span> bent-over rows, single-arm rows, curls, hammer curls, shrugs<br>
              <span class="footer-label" style="color:#7E8B74;">Lower:</span> goblet squats, lunges, Bulgarian split squats, RDLs, hip thrusts<br><br>

              Week starts Monday, America/Los_Angeles.
            </div>
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>
</body>
</html>"""


def render_text(pt: dict, lt: dict, pushup_prs: dict, lift_prs: dict, today_str: str) -> str:
    pt_at = pt["all_time"]; pt_wk = pt["this_week"]
    lt_at = lt["all_time"]; lt_wk = lt["this_week"]

    def two_col(p_rows, l_rows):
        """Lay out pushup rows on the left, lift rows on the right."""
        out = []
        for (pl, pv), (ll, lv) in zip(p_rows, l_rows):
            left = f"{pl:9} {pv:>7}"
            right = f"{ll:9} {lv:>11}"
            out.append(f"    {left}     {right}")
        return "\n".join(out)

    this_week = two_col(
        [("Standard", pt_wk["standard"]), ("Diamond", pt_wk["diamond"]),
         ("Wide", pt_wk["wide"]), ("Total", pt_wk["total"])],
        [("Push", _fmt_lb(lt_wk["push"])), ("Pull", _fmt_lb(lt_wk["pull"])),
         ("Lower", _fmt_lb(lt_wk["lower"])), ("Total", _fmt_lb(lt_wk["total"]))],
    )
    all_time = two_col(
        [("Standard", pt_at["standard"]), ("Diamond", pt_at["diamond"]),
         ("Wide", pt_at["wide"]), ("Total", pt_at["total"])],
        [("Push", _fmt_lb(lt_at["push"])), ("Pull", _fmt_lb(lt_at["pull"])),
         ("Lower", _fmt_lb(lt_at["lower"])), ("Total", _fmt_lb(lt_at["total"]))],
    )

    def pr_pair(pk, lk):
        pv, pm, ps = pushup_prs.get(pk, (0, None, None))
        lv, lm, ls = lift_prs.get(lk, (0, None, None))
        p_str = f"{pk.capitalize():9} {_fmt_num(pv) if pv else '0':>7}"
        l_str = f"{lk.capitalize():9} {(_fmt_lb(lv) if lv else '0'):>11}"
        return f"    {p_str}     {l_str}"

    best_weeks = "\n".join([
        pr_pair("total", "total"),
        pr_pair("standard", "push"),
        pr_pair("diamond", "pull"),
        pr_pair("wide", "lower"),
    ])

    return f"""{DIGEST_SUBJECT}  /  {today_str}

reply #/#/# (standard/dia/wide) to log pushups
reply lift #/#/# (push/pull/lower) to log weight lifting

                  PUSHUPS                  LIFTING

This week
{this_week}

All time
{all_time}

Best weeks
{best_weeks}

Reply to log.
Pushups: "20 standard", "20s", "10 std, 5 dia, 5 wide", "20/10/5"
Lifting: "push 4050", "pull 3000", "lower 6200", "lift 4050/3000/6200"

Push:  floor press, overhead press, lateral raises, flyes, tricep extensions
Pull:  bent-over rows, single-arm rows, curls, hammer curls, shrugs
Lower: goblet squats, lunges, Bulgarian split squats, RDLs, hip thrusts

Week starts Monday, America/Los_Angeles.
"""


# --- Main -----------------------------------------------------------------

def main() -> int:
    env = load_env()
    gmail_address = env["GMAIL_ADDRESS"]
    gmail_password = env["GMAIL_APP_PASSWORD"]
    notify_email = env["NOTIFY_EMAIL"]

    conn = db()
    pt = totals(conn)
    lt = lift_totals(conn)
    pushup_prs = all_prs(conn, "entries")
    lift_prs = all_prs(conn, "lifts")

    pushup_chart_light = None
    pushup_chart_dark = None
    lift_chart_light = None
    lift_chart_dark = None
    chart_error = None
    if CHART_AVAILABLE:
        try:
            pushup_chart_light = render_pushups_chart(conn, theme="light")
            pushup_chart_dark = render_pushups_chart(conn, theme="dark")
            lift_chart_light = render_lifts_chart(conn, theme="light")
            lift_chart_dark = render_lifts_chart(conn, theme="dark")
        except Exception as e:
            chart_error = f"chart render failed: {e}"
    else:
        chart_error = f"chart module unavailable: {_CHART_IMPORT_ERROR}"

    has_pushup_chart = pushup_chart_light is not None
    has_lift_chart = lift_chart_light is not None

    now_local = datetime.now(LOCAL_TZ)
    today_str = now_local.strftime("%A, %B %-d")

    outer = MIMEMultipart("related")
    outer["Subject"] = DIGEST_SUBJECT
    outer["From"] = gmail_address
    outer["To"] = notify_email
    outer["Date"] = formatdate(localtime=True)
    outer["Message-ID"] = make_msgid(domain="pushups.local")

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(render_text(pt, lt, pushup_prs, lift_prs, today_str), "plain"))
    alt.attach(MIMEText(render_html(pt, lt, pushup_prs, lift_prs, today_str, has_pushup_chart, has_lift_chart), "html"))
    outer.attach(alt)

    def _attach(png_bytes, cid, filename):
        img = MIMEImage(png_bytes, _subtype="png")
        img.add_header("Content-ID", f"<{cid}>")
        img.add_header("Content-Disposition", "inline", filename=filename)
        outer.attach(img)

    if has_pushup_chart:
        _attach(pushup_chart_light, PUSHUPS_CID_LIGHT, "pushups-light.png")
        _attach(pushup_chart_dark, PUSHUPS_CID_DARK, "pushups-dark.png")
    if has_lift_chart:
        _attach(lift_chart_light, LIFTS_CID_LIGHT, "lifts-light.png")
        _attach(lift_chart_dark, LIFTS_CID_DARK, "lifts-dark.png")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as srv:
        srv.login(gmail_address, gmail_password)
        srv.sendmail(gmail_address, [notify_email], outer.as_string())

    chart_note = []
    if has_pushup_chart:
        chart_note.append("pushup-chart")
    if has_lift_chart:
        chart_note.append("lift-chart")
    if not chart_note:
        chart_note.append(f"no charts ({chart_error})")
    print(
        f"[{now_local.isoformat()}] sent digest. "
        f"pushup_total={pt['this_week']['total']} lift_total={lt['this_week']['total']} "
        f"({', '.join(chart_note)})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
