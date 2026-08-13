"""Render the usage numbers as a local HTML dashboard and open it.

The terminal table in `usage_report` answers "what are the numbers". This
answers "how is it going" — the shape of a week is a thing you see, not a thing
you read down a column.

Deliberately a generated file rather than a served page: there is no dashboard
process to remember to start or to leave running, nothing new listening on a
port, and the output is a single self-contained file you can keep or mail to
yourself. `--watch` re-renders on a timer and the page reloads itself, which is
as close to live as this needs to be.

Self-contained by the same rule as the app itself: inline CSS, inline SVG, no
CDN, no fonts fetched, no script. It opens from a file:// URL with the network
off.

    cd src && uv run python -m scripts.usage_dashboard
    cd src && uv run python -m scripts.usage_dashboard --remote --days 30
    cd src && uv run python -m scripts.usage_dashboard --remote --watch
"""

from __future__ import annotations

import argparse
import asyncio
import html
import tempfile
import time
import webbrowser
from datetime import datetime
from pathlib import Path

from app.api.schemas import UsageStats
from scripts.usage_report import DEFAULT_REMOTE_URL, _fetch_local, _fetch_remote

# The funnel in the order somebody actually moves through it. `usage_stats`
# returns the events sorted alphabetically, which puts "drive_started" first and
# makes the drop-off between stages unreadable.
FUNNEL_ORDER = [
    ("page_view", "Opened a page"),
    ("plan_submitted", "Asked for a plan"),
    ("trip_planned", "Got one back"),
    ("drive_started", "Drove it"),
]

# Charted separately: it is an error count, not a stage. Left in the funnel it
# reads as a step people pass through.
FAILURE_EVENT = "plan_failed"

# Light: the app's own validated chart pair (globals.css --color-chart-*).
# Dark: re-stepped for the dark surface and re-validated — an automatic flip of
# the light values lands outside the dark lightness band.
#
# Both pairs pass the dataviz six checks:
#   light on #ffffff  — CVD ΔE 28.1, normal 32.5
#   dark  on #141d2c  — CVD ΔE 23.5, normal 24.4
# The light amber sits under 3:1 on white, which obliges the direct labels and
# the table view below — those are relief for that warning, not decoration.
CSS = """
:root {
  --page: #f4f7f6; --surface: #ffffff; --line: #e8edf1;
  --ink: #0e1a2b; --ink-soft: #55677f; --ink-mute: #7d8ca0;
  --s1: #3f6dbf; --s2: #d98e1f; --accent: #17a56b;
  --s1-wash: rgba(63, 109, 191, 0.10);
}
@media (prefers-color-scheme: dark) {
  :root {
    --page: #0b1220; --surface: #141d2c; --line: #26344a;
    --ink: #e8edf1; --ink-soft: #aab6c5; --ink-mute: #7d8ca0;
    --s1: #5b8ad4; --s2: #b8842a; --accent: #43c389;
    --s1-wash: rgba(91, 138, 212, 0.14);
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 32px 20px 64px; background: var(--page); color: var(--ink);
  font: 15px/1.5 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
}
.wrap { max-width: 940px; margin: 0 auto; }
h1 { font-size: 20px; font-weight: 650; margin: 0; letter-spacing: -0.01em; }
.sub { color: var(--ink-soft); font-size: 13px; margin-top: 4px; }
.card {
  background: var(--surface); border: 1px solid var(--line); border-radius: 14px;
  padding: 20px 22px; margin-top: 18px;
}
.card h2 {
  font-size: 13px; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.06em; color: var(--ink-soft); margin: 0 0 14px;
}
/* Hero: proportional figures — tabular-nums makes a big number look gappy. */
.hero { font-size: 54px; font-weight: 650; line-height: 1.05; letter-spacing: -0.02em; }
.hero-label { color: var(--ink-soft); font-size: 13px; margin-top: 2px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 14px; }
.tile { background: var(--surface); border: 1px solid var(--line); border-radius: 14px; padding: 16px 18px; }
.tile .v { font-size: 27px; font-weight: 650; letter-spacing: -0.01em; }
.tile .l { color: var(--ink-soft); font-size: 12.5px; margin-top: 2px; }
.note {
  border-left: 2px solid var(--accent); padding: 2px 0 2px 12px; margin-top: 14px;
  color: var(--ink-soft); font-size: 13px;
}
/* Capped and scrolled: at --days 90 an uncapped table is most of the page,
   and it is the reference view, not the headline. */
.scroll { max-height: 340px; overflow-y: auto; }
table { border-collapse: collapse; width: 100%; font-size: 13.5px; }
th, td { text-align: right; padding: 6px 10px; border-bottom: 1px solid var(--line); }
th:first-child, td:first-child { text-align: left; }
th {
  color: var(--ink-soft); font-weight: 600; font-size: 12px;
  position: sticky; top: 0; background: var(--surface);
}
td { font-variant-numeric: tabular-nums; }
tr:last-child td { border-bottom: 0; }
.legend { display: flex; gap: 18px; margin-bottom: 6px; font-size: 13px; color: var(--ink-soft); }
.key { display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 6px; vertical-align: 1px; }
.empty { color: var(--ink-mute); font-size: 13.5px; font-style: italic; }
/* Four short breakdowns side by side rather than four cards deep — they are
   read together ("phones, in NL, on narrow screens"), not one at a time. */
.split { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 22px; }
.split h3 {
  font-size: 12px; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.05em; color: var(--ink-soft); margin: 0 0 10px;
}
/* The retention pair: one number that means nothing without its denominator,
   so they are set as a fraction rather than as two tiles. */
.ratio { display: flex; align-items: baseline; gap: 10px; }
.ratio .n { font-size: 40px; font-weight: 650; letter-spacing: -0.02em; }
.ratio .d { color: var(--ink-soft); font-size: 13.5px; }
.bar-track { height: 8px; background: var(--s1-wash); border-radius: 4px; margin-top: 12px; overflow: hidden; }
.bar-fill { height: 100%; background: var(--accent); border-radius: 4px; }
footer { color: var(--ink-mute); font-size: 12.5px; margin-top: 26px; line-height: 1.65; }
svg { display: block; width: 100%; height: auto; overflow: visible; }
"""


def _esc(s: object) -> str:
    return html.escape(str(s), quote=True)


def _nice_ceiling(v: int) -> int:
    """Round an axis top up to something a person would have chosen."""
    if v <= 5:
        return max(1, v)
    step = 10 ** (len(str(v)) - 1)
    for mult in (1, 2, 2.5, 5, 10):
        top = step * mult
        if top >= v:
            return int(top)
    return v


def _bar_path(x: float, y: float, w: float, h: float, r: float = 4.0) -> str:
    """A bar with its data-end rounded and its baseline end square.

    Below ~2r there is no room to round anything, so short bars degrade to a
    plain rect rather than to the pinched teardrop an unguarded arc produces.
    """
    if w <= 0:
        return ""
    if w < 2 * r:
        return f"M{x},{y} h{w:.1f} v{h:.1f} h{-w:.1f} z"
    return (
        f"M{x},{y} h{w - r:.1f} a{r},{r} 0 0 1 {r},{r} "
        f"v{h - 2 * r:.1f} a{r},{r} 0 0 1 {-r},{r} h{-(w - r):.1f} z"
    )


def _daily_chart(s: UsageStats) -> str:
    """Two series over time: page views and visitors, one shared axis.

    Never a second y-axis — both are counts of the same kind, and two scales
    would invent a relationship between them that the data does not contain.
    """
    days = s.daily
    if not days:
        return '<p class="empty">No days in range.</p>'

    W, H = 900, 250
    L, R, T, B = 46, 128, 14, 30  # right margin holds the end labels
    pw, ph = W - L - R, H - T - B

    top = _nice_ceiling(max([d.page_views for d in days] + [d.visitors for d in days] + [1]))
    n = len(days)
    x = lambda i: L + (pw * i / (n - 1) if n > 1 else pw / 2)  # noqa: E731
    y = lambda v: T + ph - (ph * v / top)  # noqa: E731

    out: list[str] = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Daily page views and visitors">']

    # Gridlines + y ticks: solid hairlines, one step off the surface. Never
    # dashed — dashing reads as "projected" when it is only a grid.
    for k in range(5):
        v = top * k / 4
        gy = y(v)
        out.append(f'<line x1="{L}" y1="{gy:.1f}" x2="{L + pw}" y2="{gy:.1f}" stroke="var(--line)" stroke-width="1"/>')
        out.append(
            f'<text x="{L - 10}" y="{gy + 4:.1f}" text-anchor="end" font-size="11" '
            f'fill="var(--ink-mute)" style="font-variant-numeric:tabular-nums">{v:.0f}</text>'
        )

    # X labels: at most ~6, so they never collide however wide the window is.
    step = max(1, round(n / 6))
    for i, d in enumerate(days):
        if i % step == 0 or i == n - 1:
            out.append(
                f'<text x="{x(i):.1f}" y="{T + ph + 19}" text-anchor="middle" font-size="11" '
                f'fill="var(--ink-mute)">{_esc(d.day[5:])}</text>'
            )

    series = [("page_views", "var(--s1)", "Page views"), ("visitors", "var(--s2)", "Visitors")]

    # Area wash under page views only — a 10% tint, and only for the outer
    # series, so the two fills never muddy each other.
    pts_pv = [(x(i), y(d.page_views)) for i, d in enumerate(days)]
    if n > 1:
        area = " ".join(f"{px:.1f},{py:.1f}" for px, py in pts_pv)
        out.append(
            f'<polygon points="{L},{T + ph} {area} {L + pw},{T + ph}" fill="var(--s1-wash)"/>'
        )

    for attr, colour, label in series:
        pts = [(x(i), y(getattr(d, attr))) for i, d in enumerate(days)]
        if n > 1:
            path = " ".join(f"{px:.1f},{py:.1f}" for px, py in pts)
            out.append(
                f'<polyline points="{path}" fill="none" stroke="{colour}" stroke-width="2" '
                f'stroke-linejoin="round" stroke-linecap="round"/>'
            )
        # Markers only when they fit; the final point always gets one so the
        # line has a definite end to read the label off.
        marks = pts if n <= 14 else pts[-1:]
        for px, py in marks:
            out.append(
                f'<circle cx="{px:.1f}" cy="{py:.1f}" r="4" fill="{colour}" '
                f'stroke="var(--surface)" stroke-width="2"/>'
            )
        # Direct end label: a colour key beside INK text. The text itself never
        # wears the series colour — the light amber is unreadable on white.
        ex, ey = pts[-1]
        val = getattr(days[-1], attr)
        out.append(f'<circle cx="{ex + 16:.1f}" cy="{ey:.1f}" r="4" fill="{colour}"/>')
        out.append(
            f'<text x="{ex + 26:.1f}" y="{ey + 4:.1f}" font-size="12.5" fill="var(--ink-soft)">'
            f'{_esc(label)} <tspan fill="var(--ink)" font-weight="600">{val}</tspan></text>'
        )

    out.append("</svg>")
    legend = (
        '<div class="legend">'
        '<span><i class="key" style="background:var(--s1)"></i>Page views</span>'
        '<span><i class="key" style="background:var(--s2)"></i>Visitors</span>'
        "</div>"
    )
    return legend + "".join(out)


def _hbars(rows: list[tuple[str, int]], *, empty: str) -> str:
    """Horizontal bars for magnitude — one measure, so one hue for every bar.

    A darker-where-bigger ramp would double-encode length as colour and spend
    the only free channel on what the bar length already says.
    """
    rows = [r for r in rows if r]
    if not rows:
        return f'<p class="empty">{_esc(empty)}</p>'

    W = 900
    label_w, val_w, row_h, bar_h = 190, 56, 30, 18
    plot = W - label_w - val_w
    top = max([c for _, c in rows] + [1])
    H = row_h * len(rows)

    out = [f'<svg viewBox="0 0 {W} {H}" role="img">']
    for i, (label, count) in enumerate(rows):
        cy = i * row_h
        ty = cy + row_h / 2 + 4
        out.append(
            f'<text x="0" y="{ty:.1f}" font-size="13" fill="var(--ink-soft)">{_esc(label)}</text>'
        )
        w = plot * count / top
        out.append(
            f'<path d="{_bar_path(label_w, cy + (row_h - bar_h) / 2, w, bar_h)}" fill="var(--s1)"/>'
        )
        # Value at the tip, always outside the bar: inside, a short bar clips it
        # and a zero bar has no inside at all.
        out.append(
            f'<text x="{label_w + w + 9:.1f}" y="{ty:.1f}" font-size="12.5" font-weight="600" '
            f'fill="var(--ink)" style="font-variant-numeric:tabular-nums">{count}</text>'
        )
    out.append("</svg>")
    return "".join(out)


def _corridor_table(rows, *, empty: str, gap_column: bool = False) -> str:
    """Corridors as a table rather than bars.

    Bars compare one number; a corridor carries three (how many planned it, how
    far, how many stops), and the third only means anything against the second.
    """
    if not rows:
        return f'<div class="empty">{_esc(empty)}</div>'
    head = (
        "<tr><th>Corridor</th><th>Trips</th><th>Distance</th>"
        + ("<th>Stops/100&nbsp;km</th>" if gap_column else "<th>Stops</th>")
        + "</tr>"
    )
    body = ""
    for c in rows:
        if gap_column:
            last = f"{c.stops_per_100km:.2f}" if c.stops_per_100km is not None else "–"
        else:
            last = f"{c.avg_stops:.1f}" if c.avg_stops is not None else "–"
        body += (
            f"<tr><td>{_esc(c.label)}</td><td>{c.trips}</td>"
            f"<td>{c.distance_km:,}&nbsp;km</td><td>{last}</td></tr>"
        )
    return f'<div class="scroll"><table><thead>{head}</thead><tbody>{body}</tbody></table></div>'


def _quota_tiles(s: UsageStats) -> str:
    """Today's spend against each free tier.

    Today, not the window: the ceilings reset at midnight UTC, so a weekly
    total cannot tell you whether tonight's post will run the tank dry — which
    is the only question this section exists to answer.
    """
    if not s.providers:
        return '<div class="empty">No external calls recorded yet.</div>'
    out = ['<div class="tiles">']
    for p in s.providers:
        hit = f"{100.0 * p.hit_rate:.0f}% served from cache" if p.hit_rate is not None else "no traffic yet"
        if p.headroom_pct is None:
            head = f"{p.calls_today:,} calls today · no published daily cap"
            bar = ""
        else:
            head = f"{p.calls_today:,} of {p.daily_quota:,} today"
            used = 100.0 - p.headroom_pct
            # Amber past 60%, red past 85% — a quota chart that stays one
            # colour until it is too late is decoration.
            colour = "var(--accent)" if used < 60 else ("var(--s2)" if used < 85 else "#d9534f")
            bar = (
                f'<div class="bar-track"><div class="bar-fill" '
                f'style="width:{min(used, 100.0):.1f}%;background:{colour}"></div></div>'
            )
        fails = (
            f'<div class="l" style="color:#d9534f">{p.failures_today} failed call(s) today</div>'
            if p.failures_today else ""
        )
        out.append(
            f'<div class="tile"><div class="v" style="font-size:22px">{_esc(p.provider.upper())}</div>'
            f'<div class="l">{_esc(head)}</div>{bar}'
            f'<div class="l" style="margin-top:8px">{_esc(hit)} · {p.avg_ms:.0f} ms avg</div>'
            f"{fails}</div>"
        )
    out.append("</div>")
    return "".join(out)


def _campaigns(s: UsageStats) -> str:
    if not s.campaigns:
        return (
            '<div class="empty">No tagged links yet. Put <code>?src=your-tag</code> '
            "on the links you post — Reddit&rsquo;s app strips referrers, so an "
            "untagged launch shows its best channel as &ldquo;direct&rdquo;.</div>"
        )
    body = ""
    for c in s.campaigns:
        conv = f"{c.conversion_pct:.0f}%" if c.conversion_pct is not None else "–"
        body += (
            f"<tr><td>{_esc(c.label)}</td><td>{c.page_views:,}</td>"
            f"<td>{c.plans:,}</td><td>{conv}</td></tr>"
        )
    return (
        '<div class="scroll"><table><thead><tr><th>Tag</th><th>Arrived</th>'
        f"<th>Asked for a plan</th><th>Rate</th></tr></thead><tbody>{body}</tbody></table></div>"
    )


def _retention(s: UsageStats) -> str:
    """The one number the daily pseudonym could never produce.

    Rendered as a fraction because the denominator is doing real work: it counts
    only browsers that kept an id, so opting out, private windows and cleared
    storage are all outside it. A bare percentage here would be quoted, and it
    would be quoted wrong.
    """
    if not s.identified_visitors:
        return (
            '<div class="empty">No browser in this window sent a persistent id, '
            "so there is nothing to answer with yet.</div>"
        )
    pct = 100.0 * s.returning_visitors / s.identified_visitors
    return f"""
    <div class="ratio">
      <span class="n">{pct:.0f}%</span>
      <span class="d">{s.returning_visitors:,} of {s.identified_visitors:,} browsers
      had been here before this window opened</span>
    </div>
    <div class="bar-track"><div class="bar-fill" style="width:{min(pct, 100.0):.1f}%"></div></div>
    <div class="note">
      Counts only browsers that keep an id. Opting out, a private window and
      cleared storage all read as new, so this is a <strong>floor</strong>, not
      an estimate. &ldquo;Returning&rdquo; means seen before this window opened —
      not seen twice inside it, which is just one visit reading two pages.
    </div>"""


def _profiles(s: UsageStats) -> str:
    """The opt-in username, stock beside flow.

    Rendered as a fraction for the same reason retention is: `published` on its
    own rises whenever the app gets busier, which is not the same as anybody
    using the feature. The stock counts carry their own label because they
    ignore the window — a figure that does not move when the range narrows
    reads as a broken filter unless it says so.
    """
    p = s.profiles
    if not (p.live or p.claimed or p.released):
        return '<div class="empty">Nobody has claimed a username yet.</div>'

    rate = 100.0 * p.publish_rate if p.publish_rate is not None else None
    share = (
        f"""
    <div class="ratio">
      <span class="n">{rate:.0f}%</span>
      <span class="d">{p.published:,} of {p.trips:,} trips in this window were
      planned under a name</span>
    </div>
    <div class="bar-track"><div class="bar-fill" style="width:{min(rate, 100.0):.1f}%"></div></div>"""
        if rate is not None
        else '<div class="empty">No trips planned in this window.</div>'
    )
    return f"""{share}
    <div style="margin-top:16px">{_hbars(
        [
            ("names right now (all-time)", p.live),
            ("…of those, publish something", p.with_trips),
            ("claimed in window", p.claimed),
            ("released in window", p.released),
            ("public lists opened", p.list_views),
        ],
        empty="",
    )}</div>
    {'<div style="margin-top:16px">' + _hbars(
        [(r.label, r.count) for r in p.per_name], empty="") + '</div>'
     if p.per_name else ''}
    <div class="note">
      The first two rows are <strong>all-time stock</strong> and do not move with
      the window; the rest is the window. Claims count names that still exist, so
      one claimed and released inside it shows up only as a release. Releases and
      list views come from events, so they start the day counting shipped and
      miss anyone who opted out.
    </div>"""


def render(s: UsageStats, source: str, *, watch_seconds: int | None = None) -> str:
    counts = {f.label: f.count for f in s.funnel}
    funnel_rows = [(nice, counts.get(key, 0)) for key, nice in FUNNEL_ORDER]
    failures = counts.get(FAILURE_EVENT, 0)

    # Events only exist from the day counting shipped; the trip totals go back
    # to the first deploy. Saying so is the difference between a quiet zero and
    # a dashboard that looks broken.
    counting_live = s.page_views > 0
    if counting_live:
        note = (
            f"Page views and the funnel are counted from this app's own "
            f"<code>app_events</code> table. Trip and drive totals come from the "
            f"trips table, which goes back to the first deploy — so those two "
            f"rows have a longer memory than the rest of this page."
        )
    else:
        note = (
            "No page views recorded in this window yet. Event counting only "
            "starts when a deploy carrying it goes live, and there is nothing "
            "to backfill from — the trip and drive totals below are real "
            "history, everything else begins now."
        )

    refresh = (
        f'<meta http-equiv="refresh" content="{watch_seconds}">' if watch_seconds else ""
    )

    daily_rows = "".join(
        f"<tr><td>{_esc(d.day)}</td><td>{d.page_views}</td><td>{d.visitors}</td></tr>"
        for d in reversed(s.daily)
    )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>EV Trip Optimizer — usage</title>{refresh}
<style>{CSS}</style></head>
<body><div class="wrap">

  <h1>Is anyone using this?</h1>
  <div class="sub">
    Last {s.days} day(s) · {_esc(source)} · generated {s.generated_at:%Y-%m-%d %H:%M} UTC
    {' · refreshing every ' + str(watch_seconds) + 's' if watch_seconds else ''}
  </div>

  <div class="card">
    <div class="hero">{s.page_views:,}</div>
    <div class="hero-label">page views in the last {s.days} day(s)</div>
    <div class="note">{note}</div>
  </div>

  <div class="card" style="padding-top:18px">
    <h2>Day by day</h2>
    {_daily_chart(s)}
  </div>

  <div class="tiles" style="margin-top:18px">
    <div class="tile"><div class="v">{s.visitors:,}</div><div class="l">visitor-days</div></div>
    <div class="tile"><div class="v">{s.trips_planned:,}</div><div class="l">trips planned</div></div>
    <div class="tile"><div class="v">{s.drives_started:,}</div><div class="l">drives started</div></div>
    <div class="tile"><div class="v">{s.trips_planned_since_launch:,}</div><div class="l">trips planned, ever</div></div>
  </div>

  <div class="card">
    <h2>What people did</h2>
    {_hbars(funnel_rows, empty="Nothing counted yet.")}
    <div class="note">
      {failures} plan{'' if failures == 1 else 's'} failed in this window.
      The gap between <em>asked for a plan</em> and <em>got one back</em> is the
      one number none of the older data could answer.
    </div>
  </div>

  <div class="card">
    <h2>Pages</h2>
    {_hbars([(p.label, p.count) for p in s.top_paths], empty="No page views in this window.")}
  </div>

  <div class="card">
    <h2>Why planning failed</h2>
    {_hbars([(f.label, f.count) for f in s.failure_reasons],
            empty="Nothing failed in this window.")}
    <div class="note">
      Recorded server-side, so this counts every failure rather than only the
      ones a browser managed to report — and it is the reason the plan actually
      failed for, not the message somebody read.
      <strong>no_route</strong>, <strong>no_chargers</strong> and
      <strong>corridor_cold</strong> are demand we could not serve, which is a
      roadmap; <strong>upstream_error</strong> climbing is what a blown free-tier
      quota looks like from inside.
    </div>
  </div>

  <div class="card">
    <h2>Free-tier headroom, today</h2>
    {_quota_tiles(s)}
    <div class="note">
      The one number here that can spoil a launch rather than merely
      under-measure it. Both providers are free tiers on daily ceilings, and the
      caches exist to stay under them — so the share served from cache is what
      actually predicts whether a spike survives its own front page. Resets at
      midnight UTC.
    </div>
  </div>

  <div class="card">
    <h2>Which link worked</h2>
    {_campaigns(s)}
    <div class="note">
      Ranked by arrivals, but read the rate: how many people a subreddit sends
      is a fact about the subreddit, and how many of them went on to plan a trip
      is a fact about whether this tool is for them.
    </div>
  </div>

  <div class="card">
    <h2>Did they come back?</h2>
    {_retention(s)}
  </div>

  <div class="card">
    <h2>Came from</h2>
    <div class="split">
      <div>
        <h3>Site</h3>
        {_hbars([(r.label, r.count) for r in s.top_referrers],
                empty="Nothing but direct visits and internal links.")}
      </div>
      <div>
        <h3>Which page</h3>
        {_hbars([(r.label, r.count) for r in s.top_referrer_paths],
                empty="No external links yet.")}
      </div>
    </div>
    <div class="note">
      The path is kept, the query string never is — that is where a link out of
      a webmail or a search carries somebody's token or address.
    </div>
  </div>

  <div class="card">
    <h2>Who is using it</h2>
    <div class="split">
      <div><h3>Country</h3>{_hbars([(c.label, c.count) for c in s.countries],
        empty="Not recorded — needs a CDN in front and TRUSTED_PROXY_HEADERS on.")}</div>
      <div><h3>Device</h3>{_hbars([(c.label, c.count) for c in s.devices],
        empty="Nothing recorded yet.")}</div>
      <div><h3>Browser</h3>{_hbars([(c.label, c.count) for c in s.browsers],
        empty="Nothing recorded yet.")}</div>
      <div><h3>Screen width</h3>{_hbars([(c.label, c.count) for c in s.viewports],
        empty="Nothing recorded yet.")}</div>
    </div>
    <div class="note">
      Buckets, never the raw user agent — a full one is a fingerprint, and this
      table has no business holding one. Device matters more here than it looks:
      the journey scene is real 3D, so the phone/desktop split decides whether
      it is the centrepiece or the reason the page struggles.
    </div>
  </div>

  <div class="card">
    <h2>Where people drive</h2>
    {_corridor_table(s.top_corridors, empty="No trips planned in this window.")}
    <div class="note">
      From <code>trip_stats</code>, which is derived from every trip ever
      planned — so unlike the counts above, this reaches back to the first
      deploy. Origins and destinations are rounded to a ~20&nbsp;&times;&nbsp;25&nbsp;km
      box, which is why they read as coordinates rather than town names.
    </div>
    {f'<div class="note"><strong>{s.trips_deleted:,} trip(s) were deleted during '
     'this window</strong> and are gone from every corridor above — these count '
     'rows that still exist, so that count is the only trace left of them.</div>'
     if s.trips_deleted else ''}
  </div>

  <div class="card">
    <h2>Where charging fails them</h2>
    {_corridor_table(s.hardest_corridors,
                     empty="No corridors with a feasible plan yet.", gap_column=True)}
    {'<h3 style="margin-top:20px">No feasible plan at any speed</h3>'
     + _corridor_table(s.infeasible_corridors, empty="")
     if s.infeasible_corridors else ''}
    <div class="note">
      Ranked by stops per 100&nbsp;km, not raw stops — otherwise this is a list of
      the longest routes, which is a fact about geography rather than about
      charging. This is the one view here nobody else can build: real demand
      against real charge curves, rather than an operator's coverage map.
    </div>
  </div>

  <div class="card">
    <h2>Roads we don&rsquo;t model</h2>
    {f'<div class="hero" style="font-size:34px">{s.unplaceable_trips:,}</div>'
     f'<div class="hero-label">trip(s) start or end somewhere the app knows nothing about '
     f'&mdash; no country, no real speed cap, just the generic fallback</div>'
     if s.unplaceable_trips else ''}
    {'<div style="margin-top:16px">' + _hbars(
        [(c.label + ' (no real cap)', c.count) for c in s.unmodelled_countries],
        empty="") + '</div>' if s.unmodelled_countries else ''}
    {'<div class="empty">Every trip so far is inside the countries the simulator models.</div>'
     if not s.unplaceable_trips and not s.unmodelled_countries else ''}
    <div class="note">
      An empty country list next to a large count above does <em>not</em> mean
      &ldquo;no gap&rdquo;: <code>geo.country_at</code> only knows western Europe, so a US
      or Japanese route cannot even be named and never reaches the list.
    </div>
  </div>

  <div class="tiles" style="margin-top:18px">
    <div class="card" style="margin-top:0">
      <h2>Trips per planner</h2>
      {_hbars([(r.label, r.count) for r in s.repeat_planners],
              empty="Nobody has planned a trip with a persistent id yet.")}
      <div class="note">
        Backfilled history has no id, so &ldquo;1 trip&rdquo; is overstated and
        repeat use is a floor.
      </div>
    </div>
    <div class="card" style="margin-top:0">
      <h2>Cars</h2>
      {_hbars([(r.label, r.count) for r in s.top_cars], empty="No trips planned yet.")}
    </div>
  </div>

  <div class="card">
    <h2>Usernames</h2>
    {_profiles(s)}
  </div>

  <div class="card">
    <h2>The numbers</h2>
    <div class="scroll"><table>
      <thead><tr><th>Day</th><th>Page views</th><th>Visitors</th></tr></thead>
      <tbody>{daily_rows}</tbody>
    </table></div>
  </div>

  <footer>
    &ldquo;Visitors&rdquo; counts <strong>visitor-days</strong>, not people. The
    pseudonym behind it is salted with the date and rotates at midnight UTC, so
    somebody who comes back tomorrow is counted again and cannot be followed
    between the two — that is the property that makes counting this way
    defensible, and it is the reason the daily column is the honest one.
    Rows are deleted after 90 days.
    <br><br>
    Retention and &ldquo;trips per planner&rdquo; come from the other pseudonym: a random
    id the browser makes and can clear, which does not rotate. It exists because
    the daily one cannot answer whether anyone returns. Everything drawn from it
    is a floor — anyone opting out, in a private window, or with cleared storage
    reads as new. It is erased from planned trips after 15 months, which is long
    enough to see whether a holiday route comes back a second summer.
  </footer>

</div></body></html>
"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--remote", action="store_true", help="read the deployed API")
    ap.add_argument("--url", default=DEFAULT_REMOTE_URL)
    ap.add_argument("--watch", type=int, nargs="?", const=60, default=None,
                    metavar="SECONDS", help="re-render on a timer (default 60s)")
    ap.add_argument("--no-open", action="store_true", help="write the file, don't open it")
    ap.add_argument("--out", type=Path, default=Path(tempfile.gettempdir()) / "evtrip-usage.html")
    args = ap.parse_args()

    def once() -> None:
        stats = asyncio.run(
            _fetch_remote(args.url, args.days) if args.remote else _fetch_local(args.days)
        )
        source = args.url if args.remote else "local database"
        args.out.write_text(render(stats, source, watch_seconds=args.watch), encoding="utf-8")

    once()
    print(f"Dashboard → {args.out}")
    if not args.no_open:
        webbrowser.open(args.out.resolve().as_uri())

    if args.watch:
        print(f"Re-rendering every {args.watch}s. Ctrl-C to stop.")
        try:
            while True:
                time.sleep(args.watch)
                once()
                print(f"  refreshed {datetime.now():%H:%M:%S}")
        except KeyboardInterrupt:
            print("\nStopped.")


if __name__ == "__main__":
    main()
