"""Shared helpers for the daily digest tracker (pushups + lifting).

Stdlib only. Credentials come from a .env file located at runtime; see
env_path() for the search order.
"""

from __future__ import annotations

import os
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# Derived from this file's location rather than hardcoded, so the project
# works under any directory name.
PROJECT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_DIR / "data" / "pushups.db"

CONFIG_DIR = Path.home() / ".config" / "fit-this"

LOCAL_TZ = ZoneInfo("America/Los_Angeles")

PUSHUP_KINDS = ("standard", "diamond", "wide")
LIFT_KINDS = ("push", "pull", "lower")

# New subject. check_inbox.py searches for this plus the legacy "Pushup digest"
# for one week of backwards compat.
DIGEST_SUBJECT = "fit this"
LEGACY_SUBJECTS = ("Daily digest", "Pushup digest")


def env_path() -> Path:
    """Locate the .env holding Gmail credentials.

    Search order, first hit wins:
      1. $FIT_THIS_ENV            explicit override
      2. <project>/.env           colocated with the project (gitignored)
      3. ~/.config/fit-this/.env  standard user config location

    Keeping this configurable means credentials can live wherever you
    already manage them; point one of these at that file (a symlink is
    fine) rather than duplicating secrets.
    """
    override = os.environ.get("FIT_THIS_ENV")
    candidates = [Path(override)] if override else []
    candidates += [PROJECT_DIR / ".env", CONFIG_DIR / ".env"]

    for path in candidates:
        if path.is_file():
            return path

    searched = "\n  ".join(str(p) for p in candidates)
    raise FileNotFoundError(
        "No .env found with Gmail credentials. Searched:\n  "
        f"{searched}\n"
        "Set FIT_THIS_ENV, or create one of the above with "
        "GMAIL_ADDRESS, GMAIL_APP_PASSWORD, and NOTIFY_EMAIL."
    )


def load_env() -> dict:
    env = {}
    for line in env_path().read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def db() -> sqlite3.Connection:
    PROJECT_DIR.joinpath("data").mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('standard','diamond','wide')),
            count INTEGER NOT NULL CHECK (count > 0),
            source_uid TEXT,
            raw_text TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_kind ON entries(kind)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_ts ON entries(ts_utc)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS lifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('push','pull','lower')),
            lb INTEGER NOT NULL CHECK (lb > 0),
            source_uid TEXT,
            raw_text TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lifts_kind ON lifts(kind)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lifts_ts ON lifts(ts_utc)")
    conn.commit()
    return conn


def week_start_utc(now_local: datetime | None = None) -> str:
    """Return ISO UTC string for Monday 00:00 local time of the current week."""
    if now_local is None:
        now_local = datetime.now(LOCAL_TZ)
    monday_local = (now_local - timedelta(days=now_local.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return monday_local.astimezone(timezone.utc).isoformat()


def _kind_totals(conn, table, value_col, kinds, week_cutoff):
    out = {"all_time": {}, "this_week": {}}
    for kind in kinds:
        row = conn.execute(
            f"SELECT COALESCE(SUM({value_col}),0) FROM {table} WHERE kind = ?", (kind,)
        ).fetchone()
        out["all_time"][kind] = row[0]
        row = conn.execute(
            f"SELECT COALESCE(SUM({value_col}),0) FROM {table} WHERE kind = ? AND ts_utc >= ?",
            (kind, week_cutoff),
        ).fetchone()
        out["this_week"][kind] = row[0]
    out["all_time"]["total"] = sum(out["all_time"][k] for k in kinds)
    out["this_week"]["total"] = sum(out["this_week"][k] for k in kinds)
    return out


def totals(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
    """Pushup totals. Kept for backward compat with old code paths."""
    return _kind_totals(conn, "entries", "count", PUSHUP_KINDS, week_start_utc())


def lift_totals(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
    """Lift totals (pounds)."""
    return _kind_totals(conn, "lifts", "lb", LIFT_KINDS, week_start_utc())


def all_prs(conn: sqlite3.Connection, table: str) -> dict[str, tuple]:
    """Best weeks for each kind + 'total' in the given table.

    Returns {kind: (value, monday_local, sunday_local)} or {kind: (0, None, None)}.
    Ties broken by earliest week (first to set the bar holds the PR).
    """
    if table == "entries":
        value_col, kinds = "count", PUSHUP_KINDS
    elif table == "lifts":
        value_col, kinds = "lb", LIFT_KINDS
    else:
        raise ValueError(f"unknown table: {table}")

    # Bucket all rows by local-Monday in one pass.
    buckets: dict = defaultdict(lambda: defaultdict(int))
    cursor = conn.execute(f"SELECT ts_utc, kind, {value_col} FROM {table}")
    for ts_utc, kind_row, value in cursor:
        dt = datetime.fromisoformat(ts_utc)
        local = dt.astimezone(LOCAL_TZ)
        monday = (local - timedelta(days=local.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        buckets[monday][kind_row] += value

    out = {}
    for kind in kinds + ("total",):
        candidates = []
        for monday, sums in buckets.items():
            v = sum(sums.values()) if kind == "total" else sums.get(kind, 0)
            if v > 0:
                candidates.append((monday, v))
        if not candidates:
            out[kind] = (0, None, None)
            continue
        candidates.sort(key=lambda x: (-x[1], x[0]))
        best_monday, best_value = candidates[0]
        sunday = best_monday + timedelta(days=6)
        out[kind] = (best_value, best_monday, sunday)
    return out


# -------- Reply parsing --------

# Aliases for each kind. Single-letter shorthands kept distinct: s/d/w for
# pushups, l for lower. Bare 'p' avoided to prevent confusion.
_PUSHUP_ALIASES = {
    "standard": "standard", "std": "standard", "s": "standard",
    "reg": "standard", "regular": "standard", "normal": "standard",
    "diamond": "diamond", "dia": "diamond", "d": "diamond",
    "wide": "wide", "w": "wide",
}
_LIFT_ALIASES = {
    "push": "push", "chest": "push",
    "pull": "pull", "back": "pull",
    "lower": "lower", "legs": "lower", "l": "lower",
}

# Number followed by a word ("20 standard"). Used after positional checks fail.
_PAIR_NUM_FIRST_RE = re.compile(r"\+?\s*(\d{1,6})\s*([a-zA-Z]+)")
# Word followed by a number ("push 4050"). Same kind set, opposite order.
_PAIR_WORD_FIRST_RE = re.compile(r"([a-zA-Z]+)\s*\+?\s*(\d{1,6})")

# Pushup positional: bare "N/N/N" (the whole fragment).
_PUSHUP_POSITIONAL_RE = re.compile(r"^\s*(\d{1,4})\s*/\s*(\d{1,4})\s*/\s*(\d{1,4})\s*$")

# Lift positional: "lift N/N/N", case-insensitive.
_LIFT_POSITIONAL_RE = re.compile(
    r"^\s*lift\s+(\d{1,6})\s*/\s*(\d{1,6})\s*/\s*(\d{1,6})\s*$", re.IGNORECASE
)


def strip_quoted(body: str) -> str:
    """Drop quoted history. Cuts at the first 'On ... wrote:' or leading '>' block."""
    lines = body.splitlines()
    out = []
    for line in lines:
        stripped = line.strip()
        if re.match(r"^On .+ wrote:\s*$", stripped):
            break
        if stripped.startswith(">"):
            break
        if stripped in ("--", "—") or stripped.startswith("-- "):
            break
        out.append(line)
    return "\n".join(out).strip()


def parse_reply(body: str) -> tuple[dict[str, int], dict[str, int], list[str]]:
    """Parse a reply body. Returns (pushup_counts, lift_counts, warnings).

    Strategy:
      1. Strip quoted history.
      2. Split into fragments by newline AND comma.
      3. Per fragment:
         a. Try lift positional ('lift N/N/N').
         b. Try pushup positional ('N/N/N').
         c. Scan for (number, word) pairs and route to pushup or lift dict.
      4. A fragment with no pairs and no positional is silently skipped
         (handles signatures, "Sent from my iPhone", etc).
    """
    text = strip_quoted(body)
    pushup_counts: dict[str, int] = {}
    lift_counts: dict[str, int] = {}
    warnings: list[str] = []

    if not text:
        return pushup_counts, lift_counts, ["empty reply body"]

    # Build fragments by splitting on newlines and commas.
    fragments = []
    for line in text.splitlines():
        for frag in line.split(","):
            frag = frag.strip()
            if frag:
                fragments.append(frag)

    for frag in fragments:
        m = _LIFT_POSITIONAL_RE.match(frag)
        if m:
            push, pull, lower = (int(x) for x in m.groups())
            if push:
                lift_counts["push"] = lift_counts.get("push", 0) + push
            if pull:
                lift_counts["pull"] = lift_counts.get("pull", 0) + pull
            if lower:
                lift_counts["lower"] = lift_counts.get("lower", 0) + lower
            continue

        m = _PUSHUP_POSITIONAL_RE.match(frag)
        if m:
            std, dia, wide = (int(x) for x in m.groups())
            if std:
                pushup_counts["standard"] = pushup_counts.get("standard", 0) + std
            if dia:
                pushup_counts["diamond"] = pushup_counts.get("diamond", 0) + dia
            if wide:
                pushup_counts["wide"] = pushup_counts.get("wide", 0) + wide
            continue

        # Scan for number+word pairs in BOTH orderings.
        # Track consumed spans so the same substring can't be counted twice
        # (e.g., "20s" matches num-first as (20, s); word-first wouldn't match
        # the same span, but we guard anyway).
        consumed_spans = []

        def _overlaps(start, end):
            return any(not (end <= s or start >= e) for s, e in consumed_spans)

        def _handle_pair(num_str, word, span):
            word_lower = word.lower()
            n = int(num_str)
            if n == 0:
                return
            if word_lower in _PUSHUP_ALIASES:
                kind = _PUSHUP_ALIASES[word_lower]
                pushup_counts[kind] = pushup_counts.get(kind, 0) + n
                consumed_spans.append(span)
            elif word_lower in _LIFT_ALIASES:
                kind = _LIFT_ALIASES[word_lower]
                lift_counts[kind] = lift_counts.get(kind, 0) + n
                consumed_spans.append(span)
            elif word_lower == "lift":
                warnings.append(
                    "use 'push N', 'pull N', or 'lower N' (or 'lift N/N/N' for all three)"
                )
            else:
                warnings.append(f"unknown kind '{word}'")

        # Pass 1: num-then-word (e.g. "20 standard", "20s")
        for m in _PAIR_NUM_FIRST_RE.finditer(frag):
            if _overlaps(m.start(), m.end()):
                continue
            _handle_pair(m.group(1), m.group(2), (m.start(), m.end()))

        # Pass 2: word-then-num (e.g. "push 4050", "legs 6200")
        for m in _PAIR_WORD_FIRST_RE.finditer(frag):
            if _overlaps(m.start(), m.end()):
                continue
            _handle_pair(m.group(2), m.group(1), (m.start(), m.end()))

    if pushup_counts or lift_counts:
        # Got real data. Suppress any spurious 'unknown kind' warnings from
        # tokens that probably are signature text rather than user intent.
        warnings = []
    elif not warnings:
        warnings.append("no count/kind pairs found")

    return pushup_counts, lift_counts, warnings


def insert_pushups(conn, counts, source_uid, raw_text):
    ts = datetime.now(timezone.utc).isoformat()
    for kind, count in counts.items():
        conn.execute(
            "INSERT INTO entries (ts_utc, kind, count, source_uid, raw_text) VALUES (?,?,?,?,?)",
            (ts, kind, count, source_uid, raw_text),
        )
    conn.commit()


def insert_lifts(conn, counts, source_uid, raw_text):
    ts = datetime.now(timezone.utc).isoformat()
    for kind, lb in counts.items():
        conn.execute(
            "INSERT INTO lifts (ts_utc, kind, lb, source_uid, raw_text) VALUES (?,?,?,?,?)",
            (ts, kind, lb, source_uid, raw_text),
        )
    conn.commit()


# Back-compat for any external caller still using the old name.
def insert_entries(conn, counts, source_uid, raw_text):
    insert_pushups(conn, counts, source_uid, raw_text)
