# fit this

**A daily email that asks one thing: did you fit this into your day?**

The name is the whole idea. It sounds like *fitness*, and it's a note to
self — *fit this in*. Not a program, not a plan. Just do some pushups or
move some weight, today, somewhere in the cracks.

Every morning at 7:00 you get one email. It shows your last 12 weeks, this
week's totals, and your best week ever. To log a workout you hit reply and
type `20s`. That's it. That's the entire interface.

```
Subject: fit this

    20s

Sent. Logged: +20 standard.
```

## Why this exists

Most fitness apps optimize for tracking. This one optimizes for *showing
up*.

The hard part of working out was never the workout — it was the friction
around it. Download an app, make an account, remember it exists, open it,
navigate to the right screen, tap through a logging flow, ignore the
upsell. Every one of those steps is a place to quit.

So the design goal was to get the friction as close to zero as it goes:

- **It comes to you.** I already check my email. The reminder arrives in a
  place I'm going to be anyway, so there's nothing to remember and nothing
  to open.
- **Logging is a reply.** No app, no account, no login, no screen. Reply
  to the email that's already sitting there.
- **The shortest possible message counts.** `20s` is three characters and
  a send button. If logging takes longer than a set of pushups, the
  logging is the problem.
- **It's free and it's yours.** Runs on your own machine, on a cron job,
  against a SQLite file you own. No subscription, no cloud, no account, no
  one selling your workout history back to you.

## Consistency is the game

The thing being gamified here is not intensity. It's **not skipping.**

**Blank weeks are bummers.** The 12-week chart is the whole mechanic. A
week with nothing in it shows up as a gap, and gaps are visible for three
months. That's the entire pressure system — no streak counters, no badges,
no push notification guilt-tripping you at 8pm.

And the counter-pressure is deliberately weak: **it only takes one entry
to not be blank.** Ten pushups on a bad day makes the bar appear. The
system would rather you log something tiny than skip. A short bar beats no
bar, always.

**Twelve weeks, because persistence needs a long lens.** One rough week
looks like a catastrophe on a 7-day view and like noise on a 12-week view.
The window is long enough that a bad Tuesday doesn't matter and a
three-week vanishing act obviously does.

**Best-week tiles reward the ceiling, the chart rewards the floor.** PRs
are there so a big week means something, and the current in-progress week
is eligible — if Tuesday's email catches you ahead of pace, the tile says
so. But the tiles are the side quest. The chart is the point.

## Two metrics, on purpose

Pushups (standard / diamond / wide) and lifting (push / pull / lower).
That's the whole scope, and leaving things out is a feature.

**Enough variety for balance, not enough to require thought.** Three
pushup variations hit the chest and triceps from different angles. Push /
pull / lower is the minimum split that keeps a body balanced instead of
building a front and neglecting a back. That's the floor for a sane
routine — and deliberately not one step above it.

**No steps. No runs. No cardio, no weight, no sleep, no macros.** Not
because they don't matter, but because they're not what I'm trying to
improve, and every extra metric is another thing to log and another way to
feel behind. Strength work is where I wanted to build the habit, so
strength work is the only thing that exists here. A tracker that measures
everything is a tracker you eventually stop opening.

**One email, not two.** Pushups and lifting are the same daily cadence.
Splitting them into separate notifications would mean two things competing
for the same attention. A divider in the middle handles it.

## What arrives each morning

```
fit this  /  [date]

Pushups
  Chart (last 12 weeks, stacked by kind)
  This week table
  All time table
  Best week tiles (Total + Standard + Diamond + Wide)
─────
Lifting
  Chart (last 12 weeks, stacked by kind)
  This week table
  All time table
  Best week tiles (Total + Push + Pull + Lower)

[footer: reply formats + exercise examples]
```

Charts are rendered as PNGs and inlined, so they survive every mail client
without a fight. The subject line is stable, so every reply threads into
the same conversation instead of scattering across your inbox.

## Logging

Reply to the email. Type as little as you can get away with.

### Pushups

| Input | Logged |
|-------|--------|
| `20s` | +20 standard |
| `20 standard` | +20 standard |
| `+15 std` | +15 standard |
| `10 std, 5 diamond, 5 wide` | +10 / +5 / +5 |
| `5d 5w 25s` | +25 standard, +5 diamond, +5 wide |
| `did 25 regular today` | +25 standard |
| `20/10/5` | +20 / +10 / +5 (positional std/dia/wide) |

Aliases: `standard | std | s | reg | regular | normal`, `diamond | dia | d`,
`wide | w`.

### Lifting (pounds)

| Input | Logged |
|-------|--------|
| `push 4050` | +4,050 lb push |
| `pull 3000` | +3,000 lb pull |
| `lower 6200` | +6,200 lb lower |
| `legs 6200` | +6,200 lb lower (alias) |
| `push 4050, pull 3000, lower 6200` | all three on one line |
| `lift 4050/3000/6200` | positional (push/pull/lower) |

Aliases: `push | chest`, `pull | back`, `lower | legs | l`.

### Mixed in one reply

```
20 standard
push 4050
pull 3000
lower 6200
```

Or all on one line: `20 standard, push 4050, pull 3000, lower 6200`.

Quoted history below the reply is stripped before parsing. Signatures
(your name, "Sent from my iPhone", "See More from...") are ignored because
the parser only acts on fragments that yield clean number+kind pairs or
positional triplets.

You get a confirmation reply back, so a typo that didn't parse is
obvious the same morning rather than three weeks later.

## Architecture

```
~/fit-this/
├── data/                     (gitignored — your data stays yours)
│   ├── pushups.db            SQLite: entries (pushups) + lifts (tonnage)
│   ├── digest.log            cron stdout/stderr from digest.py
│   └── inbox.log             cron stdout/stderr from check_inbox.py
└── scripts/
    ├── common.py             env loader, db, week math, parser, PR computation
    ├── chart.py              stacked-bar chart renderer (both metrics, PNG)
    ├── digest.py             daily digest email (cron)
    └── check_inbox.py        polls for replies, parses, updates db, confirms (cron)
```

Two SQLite tables, both auto-created on first run:

- `entries` — pushup history (kind in standard/diamond/wide, count)
- `lifts` — lifting tonnage history (kind in push/pull/lower, lb)

Gmail labels handle dedupe (`pushups-processed`, `pushups-unparsed`). Those
names date from the pushups-only era and are kept deliberately — renaming
them would orphan every already-processed reply.

## Install

Needs Python 3 and a Gmail account with an
[app password](https://support.google.com/accounts/answer/185833).

```bash
# 1. Clone it
git clone https://github.com/pjens45/fit-this.git ~/fit-this

# 2. Create the .env with your credentials
cat > ~/fit-this/.env <<'ENV'
GMAIL_ADDRESS=you@gmail.com
GMAIL_APP_PASSWORD=abcd efgh ijkl mnop
NOTIFY_EMAIL=where-the-digest-goes@example.com
ENV
chmod 600 ~/fit-this/.env

# 3. Install matplotlib (for the charts). One-time, ~50MB.
pip install matplotlib --break-system-packages

# 4. Sanity check
/usr/bin/python3 ~/fit-this/scripts/digest.py
# Check the inbox set as NOTIFY_EMAIL. You should see "fit this" with two
# charts and tile sections (will be empty before any data exists).

# 5. Add cron entries (crontab -e). Cron does not expand ~, so use the
#    absolute path for your user.
0 7 * * *  /usr/bin/python3 /home/USER/fit-this/scripts/digest.py >> /home/USER/fit-this/data/digest.log 2>&1
*/5 * * * * /usr/bin/python3 /home/USER/fit-this/scripts/check_inbox.py >> /home/USER/fit-this/data/inbox.log 2>&1
```

`GMAIL_ADDRESS` sends the mail and `NOTIFY_EMAIL` receives it; they can be
the same address.

### Where credentials come from

`.env` is resolved at runtime, first hit wins:

| Order | Location |
|-------|----------|
| 1 | `$FIT_THIS_ENV` — explicit override |
| 2 | `<project>/.env` — colocated, gitignored |
| 3 | `~/.config/fit-this/.env` — standard user config |

If you already manage these credentials somewhere else, point one of those
at that file — a symlink works — instead of copying secrets around. The
`.env` is gitignored and never lives in this repo.

The project directory name is not hardcoded anywhere; `PROJECT_DIR` is
derived from the scripts' own location, so you can install it under any
name you like.

### Upgrading an existing install

Drop the new scripts over the old ones — there's no data migration. `db()`
creates the `lifts` table idempotently on the next run and leaves `entries`
untouched. Since `PROJECT_DIR` is derived from the scripts' location, an
install living under an older directory name keeps working as-is.

The subject line has been through two renames (`Pushup digest` → `Daily
digest` → `fit this`). Both older subjects stay in `LEGACY_SUBJECTS` in
`common.py` and the IMAP query matches all three, so replies to old threads
keep processing. Drop the ones you no longer need once those threads go
quiet.

## Operations

**Backfill an entry with a custom timestamp.**

```bash
/usr/bin/python3 << 'PY'
import os
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

ts_local = datetime(2026, 5, 13, 7, 50, tzinfo=ZoneInfo("America/Los_Angeles"))
ts_utc = ts_local.astimezone(ZoneInfo("UTC")).isoformat()

conn = sqlite3.connect(os.path.expanduser("~/fit-this/data/pushups.db"))
# Pushups: insert into entries (kind in standard/diamond/wide, count integer)
conn.execute("INSERT INTO entries (ts_utc, kind, count, raw_text) VALUES (?,?,?,?)",
             (ts_utc, "standard", 20, "backfill"))
# Lifting: insert into lifts (kind in push/pull/lower, lb integer)
conn.execute("INSERT INTO lifts (ts_utc, kind, lb, raw_text) VALUES (?,?,?,?)",
             (ts_utc, "push", 4050, "backfill"))
conn.commit()
PY
```

**Audit recent activity.**

```bash
sqlite3 ~/fit-this/data/pushups.db \
  "SELECT date(ts_utc), kind, sum(count) FROM entries
   WHERE ts_utc >= '2026-05-04T07:00:00+00:00'
   GROUP BY date(ts_utc), kind ORDER BY 1,2;"

sqlite3 ~/fit-this/data/pushups.db \
  "SELECT date(ts_utc), kind, sum(lb) FROM lifts
   WHERE ts_utc >= '2026-05-04T07:00:00+00:00'
   GROUP BY date(ts_utc), kind ORDER BY 1,2;"
```

**Fix a fat-finger entry.** No undo command. Edit the db directly:

```bash
sqlite3 ~/fit-this/data/pushups.db "DELETE FROM lifts WHERE id = <id>;"
```

Find the offending id with:
`SELECT id, ts_utc, kind, lb, raw_text FROM lifts ORDER BY id DESC LIMIT 10;`

**Reset (start over).** `rm ~/fit-this/data/pushups.db` — next run recreates
both tables.

**Replay a message.** Remove the `pushups-processed` label from the thread
in Gmail. The next `check_inbox.py` pass will reprocess it. (Delete the
corresponding rows by `source_uid` first if you don't want a double-count.)

## Design notes

- **Stdlib + matplotlib only.** No web framework, no ORM, no surprise
  upgrade breakage. matplotlib is the one heavy dependency and it earns its
  weight by producing PNGs that render reliably in every mail client.
- **Two separate tables.** `lifts` could live in `entries` with a metric
  column, but keeping them apart means schema evolution per metric stays
  clean (lifts may someday want weight/reps/sets fields).
- **History over running totals.** Every entry is preserved. PRs, monthly
  views, streaks — anything you want later is a query away.
- **Word-then-number and number-then-word both accepted.** `20 standard` is
  natural for pushups (count first); `push 4050` is natural for lifting
  (category first, weight always large). The parser handles both, with span
  deduplication to prevent double-counting.
- **Ties on best-week go to the earlier week.** First to set the bar holds
  the PR.
