#!/usr/bin/env python3
"""Check the burner inbox for replies to the daily digest.

Handles replies to the new 'Daily digest' subject AND legacy 'Pushup digest'
replies (one-week backwards compat for the rename).

For each unprocessed reply:
  1. Parse the body for pushup and/or lift entries.
  2. Insert into the appropriate table(s).
  3. Send a confirmation reply (threaded) summarizing what was logged.
  4. Apply 'pushups-processed' label.

Unparseable replies get 'pushups-unparsed' label and a short error reply.
"""

from __future__ import annotations

import email
import email.utils
import imaplib
import smtplib
import sys
from datetime import datetime
from email.header import decode_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid

from common import (
    DIGEST_SUBJECT,
    LEGACY_SUBJECTS,
    LIFT_KINDS,
    LOCAL_TZ,
    PUSHUP_KINDS,
    db,
    insert_lifts,
    insert_pushups,
    lift_totals,
    load_env,
    parse_reply,
    totals,
)

PROCESSED_LABEL = "pushups-processed"  # legacy label name kept for continuity
UNPARSED_LABEL = "pushups-unparsed"


def get_header(msg, name: str) -> str:
    val = msg.get(name, "")
    parts = decode_header(val)
    out = ""
    for s, enc in parts:
        if isinstance(s, bytes):
            out += s.decode(enc or "utf-8", errors="replace")
        else:
            out += s
    return out


def get_body_text(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return _strip_html(payload.decode(charset, errors="replace"))
        return ""
    payload = msg.get_payload(decode=True) or b""
    charset = msg.get_content_charset() or "utf-8"
    text = payload.decode(charset, errors="replace")
    if msg.get_content_type() == "text/html":
        text = _strip_html(text)
    return text


def _strip_html(html: str) -> str:
    import re
    text = re.sub(r"<style.*?</style>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+\n", "\n", text)
    return text.strip()


def ensure_label(M, label: str) -> None:
    typ, data = M.list()
    existing = " ".join(r.decode(errors="replace") for r in data if r)
    if f'"{label}"' not in existing:
        M.create(f'"{label}"')


def send_reply(env, to_addr, in_reply_to, references, original_subject, body_text, body_html):
    gmail_address = env["GMAIL_ADDRESS"]
    gmail_password = env["GMAIL_APP_PASSWORD"]
    subj = original_subject if original_subject.lower().startswith("re:") else f"Re: {original_subject}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subj
    msg["From"] = gmail_address
    msg["To"] = to_addr
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="pushups.local")
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = (references + " " + in_reply_to).strip() if references else in_reply_to
    msg.attach(MIMEText(body_text, "plain"))
    msg.attach(MIMEText(body_html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as srv:
        srv.login(gmail_address, gmail_password)
        srv.sendmail(gmail_address, [to_addr], msg.as_string())


def confirmation_html(pushup_logged, lift_logged, pt, lt):
    rows = []
    for k in PUSHUP_KINDS:
        if k in pushup_logged:
            rows.append(f'<tr><td style="padding:6px 0;color:#1F2221;font-size:14px;">+{pushup_logged[k]} {k}</td></tr>')
    for k in LIFT_KINDS:
        if k in lift_logged:
            rows.append(f'<tr><td style="padding:6px 0;color:#1F2221;font-size:14px;">+{lift_logged[k]:,} lb {k}</td></tr>')
    logged_rows = "".join(rows)

    totals_rows = []
    if pushup_logged:
        totals_rows.append(f'<tr><td style="padding:6px 0;color:#3B3F42;">This week pushups</td><td style="padding:6px 0;text-align:right;color:#1F2221;font-variant-numeric:tabular-nums;">{pt["this_week"]["total"]}</td></tr>')
        totals_rows.append(f'<tr><td style="padding:6px 0;color:#3B3F42;">All time pushups</td><td style="padding:6px 0;text-align:right;color:#1F2221;font-variant-numeric:tabular-nums;">{pt["all_time"]["total"]}</td></tr>')
    if lift_logged:
        totals_rows.append(f'<tr><td style="padding:6px 0;color:#3B3F42;">This week lifting</td><td style="padding:6px 0;text-align:right;color:#1F2221;font-variant-numeric:tabular-nums;">{lt["this_week"]["total"]:,} lb</td></tr>')
        totals_rows.append(f'<tr><td style="padding:6px 0;color:#3B3F42;">All time lifting</td><td style="padding:6px 0;text-align:right;color:#1F2221;font-variant-numeric:tabular-nums;">{lt["all_time"]["total"]:,} lb</td></tr>')
    totals_html = "".join(totals_rows)

    return f"""<!DOCTYPE html>
<html>
<head>
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
</head>
<body style="margin:0;padding:0;background-color:#F3F1ED;">
<table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0" bgcolor="#F3F1ED" style="background-color:#F3F1ED;width:100%;">
  <tr><td align="center" valign="top" bgcolor="#F3F1ED" style="background-color:#F3F1ED;">
    <table role="presentation" width="560" border="0" cellpadding="0" cellspacing="0" bgcolor="#F3F1ED" style="background-color:#F3F1ED;max-width:560px;width:100%;">
      <tr><td style="padding:40px 32px;font-family:'Inter',-apple-system,sans-serif;color:#1F2221;">
        <div style="font-size:11px;letter-spacing:0.12em;text-transform:uppercase;color:#7E8B74;margin-bottom:16px;">Logged</div>
        <table role="presentation" style="width:100%;border-collapse:collapse;margin-bottom:28px;">{logged_rows}</table>
        <div style="font-size:11px;letter-spacing:0.12em;text-transform:uppercase;color:#6F8891;margin-bottom:12px;">Running totals</div>
        <table role="presentation" style="width:100%;border-collapse:collapse;font-size:14px;">{totals_html}</table>
      </td></tr>
    </table>
  </td></tr>
</table>
</body></html>"""


def confirmation_text(pushup_logged, lift_logged, pt, lt):
    lines = ["Logged:"]
    for k in PUSHUP_KINDS:
        if k in pushup_logged:
            lines.append(f"  +{pushup_logged[k]} {k}")
    for k in LIFT_KINDS:
        if k in lift_logged:
            lines.append(f"  +{lift_logged[k]:,} lb {k}")
    lines.append("")
    if pushup_logged:
        lines.append(f"This week pushups: {pt['this_week']['total']}")
        lines.append(f"All time pushups:  {pt['all_time']['total']}")
    if lift_logged:
        lines.append(f"This week lifting: {lt['this_week']['total']:,} lb")
        lines.append(f"All time lifting:  {lt['all_time']['total']:,} lb")
    return "\n".join(lines)


def unparsed_text(warnings):
    return (
        "Couldn't parse that reply.\n\n"
        f"Reason: {'; '.join(warnings)}\n\n"
        "Pushups:\n"
        '  "20 standard"\n'
        '  "20s"\n'
        '  "10 std, 5 dia, 5 wide"\n'
        '  "20/10/5"  (std/dia/wide)\n'
        "\n"
        "Lifting:\n"
        '  "push 4050"\n'
        '  "pull 3000"\n'
        '  "lower 6200"\n'
        '  "lift 4050/3000/6200"  (push/pull/lower)\n'
    )


def main() -> int:
    env = load_env()
    gmail_address = env["GMAIL_ADDRESS"]
    gmail_password = env["GMAIL_APP_PASSWORD"]

    M = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    M.login(gmail_address, gmail_password)
    M.select("INBOX")

    ensure_label(M, PROCESSED_LABEL)
    ensure_label(M, UNPARSED_LABEL)

    # Build subject clause: new subject plus any legacy subjects.
    subj_clauses = [f'subject:\\"{DIGEST_SUBJECT}\\"'] + [f'subject:\\"{s}\\"' for s in LEGACY_SUBJECTS]
    subj_or = "{" + " ".join(subj_clauses) + "}"
    query = (
        f'(X-GM-RAW "{subj_or} '
        f'-label:{PROCESSED_LABEL} -label:{UNPARSED_LABEL} -from:me")'
    )
    typ, data = M.uid("SEARCH", None, query)
    uids = data[0].split() if data and data[0] else []

    if not uids:
        print(f"[{datetime.now(LOCAL_TZ).isoformat()}] no new replies")
        M.logout()
        return 0

    conn = db()
    processed = 0
    unparsed = 0

    for uid in uids:
        typ, fetched = M.uid("FETCH", uid, "(RFC822)")
        if not fetched or not fetched[0]:
            continue
        raw = fetched[0][1]
        msg = email.message_from_bytes(raw)

        from_name, from_addr = email.utils.parseaddr(get_header(msg, "From"))
        subject = get_header(msg, "Subject")
        msg_id = get_header(msg, "Message-ID")
        references = get_header(msg, "References")
        body = get_body_text(msg)

        pushup_logged, lift_logged, warnings = parse_reply(body)
        uid_str = uid.decode() if isinstance(uid, bytes) else str(uid)

        if pushup_logged or lift_logged:
            if pushup_logged:
                insert_pushups(conn, pushup_logged, source_uid=uid_str, raw_text=body[:2000])
            if lift_logged:
                insert_lifts(conn, lift_logged, source_uid=uid_str, raw_text=body[:2000])

            pt = totals(conn)
            lt = lift_totals(conn)
            send_reply(
                env, from_addr, msg_id, references, subject,
                confirmation_text(pushup_logged, lift_logged, pt, lt),
                confirmation_html(pushup_logged, lift_logged, pt, lt),
            )
            M.uid("STORE", uid, "+X-GM-LABELS", f'"{PROCESSED_LABEL}"')
            M.uid("STORE", uid, "+FLAGS", "\\Seen")
            processed += 1
            print(f"  uid={uid_str} from={from_addr} pushups={pushup_logged} lifts={lift_logged}")
        else:
            send_reply(
                env, from_addr, msg_id, references, subject,
                unparsed_text(warnings),
                f"<pre style='font-family:monospace;color:#1F2221;background:#F3F1ED;padding:20px;border:1px solid #C8C1B8;'>{unparsed_text(warnings)}</pre>",
            )
            M.uid("STORE", uid, "+X-GM-LABELS", f'"{UNPARSED_LABEL}"')
            M.uid("STORE", uid, "+FLAGS", "\\Seen")
            unparsed += 1
            print(f"  uid={uid_str} from={from_addr} unparsed: {warnings}")

    M.logout()
    print(f"[{datetime.now(LOCAL_TZ).isoformat()}] processed={processed} unparsed={unparsed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
