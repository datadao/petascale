"""Render petascale dashboard to a self-contained HTML file.

Reads SQLite directly (last 7 days) + Parquet archive (older data).

Usage:
    uv run --extra analytics python scripts/dashboard.py [--db PATH] [--archive DIR] [--out PATH]
"""

import argparse
import base64
import html
import logging
import mimetypes
import time as _time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import duckdb
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from petascale.config import CatProfile, load_config

log = logging.getLogger(__name__)

_DB_DEFAULT = "/data/petascale.db"
_ARCHIVE_DEFAULT = "/data/archive"
_OUT_DEFAULT = "/data/dashboard.html"

_CAT_COLORS = ["#2da44e", "#0969da", "#cf222e", "#8250df", "#bc4c00", "#1b7f37"]
_CLEAN_COLOR = "#8250df"
_AVATAR_PROD_DIR = Path("/data/avatars")
_PLOTLY_CDN = "https://cdn.plot.ly/plotly-6.7.0.min.js"


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _resolve_avatar(path: str | None) -> Path | None:
    if not path:
        return None
    candidates = [Path(path).expanduser(), _AVATAR_PROD_DIR / Path(path).name]
    for c in candidates:
        if c.is_file():
            return c
    log.warning("avatar not found in any of: %s", [str(c) for c in candidates])
    return None


def _avatar_data_uri(path: str | None) -> str | None:
    p = _resolve_avatar(path)
    if p is None:
        return None
    mime, _ = mimetypes.guess_type(p.name)
    head = p.read_bytes()[:4]
    if head[:3] == b"\xff\xd8\xff":
        mime = "image/jpeg"
    elif head[:4] == b"\x89PNG":
        mime = "image/png"
    elif not mime:
        mime = "application/octet-stream"
    encoded = base64.b64encode(p.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _ago(delta_ms: float | None) -> str:
    if not delta_ms:
        return ""
    total_h = int(delta_ms) // 3_600_000
    m = (int(delta_ms) % 3_600_000) // 60_000
    if total_h > 48:
        d = total_h // 24
        h = total_h % 24
        return f"{d}d {h}h ago"
    return f"{total_h}h {m:02d}m ago"


def _build_cats_header(
    con: duckdb.DuckDBPyConnection,
    cats: list[CatProfile],
    tz: str,
    algo: str = "v1",
) -> str:
    if not cats:
        return ""

    stats = {
        row[0]: (row[1], row[2], row[3], row[4])
        for row in con.execute(f"""
            WITH daily AS (
                SELECT cat,
                       date_trunc('day', to_timestamp(timestamp / 1000)
                           AT TIME ZONE '{tz}')          AS day,
                       avg(weight_g) / 1000.0             AS avg_kg
                FROM sqlite.events
                WHERE type = 'potty' AND cat IS NOT NULL AND algo = '{algo}'
                GROUP BY 1, 2
            ),
            latest_day AS (
                SELECT cat, day, avg_kg,
                       row_number() OVER (PARTITION BY cat ORDER BY day DESC) AS rn
                FROM daily
            )
            SELECT ld.cat,
                   ld.day::varchar      AS last_day,
                   ld.avg_kg            AS last_day_avg_kg,
                   (SELECT max(to_timestamp(timestamp / 1000)
                               AT TIME ZONE '{tz}')::varchar
                    FROM sqlite.events
                    WHERE type = 'potty' AND cat = ld.cat AND algo = '{algo}') AS last_seen,
                   (SELECT epoch_ms(now()) - max(timestamp)
                    FROM sqlite.events
                    WHERE type = 'potty' AND cat = ld.cat AND algo = '{algo}') AS last_seen_delta_ms
            FROM latest_day ld
            WHERE rn = 1
        """).fetchall()
    }

    cards: list[str] = []
    for cat in cats:
        avatar = _avatar_data_uri(cat.avatar_path)
        last_day, last_day_avg_kg, last_seen, last_seen_delta = stats.get(
            cat.name, (None, None, None, None)
        )

        if avatar:
            img = f'<img src="{avatar}" alt="{html.escape(cat.name)}">'
        else:
            initial = html.escape(cat.name[:1].upper())
            img = f'<div class="avatar-fallback">{initial}</div>'

        if last_day_avg_kg is not None and last_day:
            lbs = last_day_avg_kg * 2.20462
            avg_line = (
                f'<div class="stat">'
                f'{last_day_avg_kg:.2f} kg ({lbs:.2f} lbs) avg · {html.escape(last_day[:10])}'
                f'</div>'
            )
        else:
            avg_line = '<div class="stat">no recent visits</div>'

        if last_seen:
            ago = _ago(last_seen_delta)
            last_seen_line = (
                f'<div class="stat">last seen {html.escape(last_seen[:16])}'
                f'<span class="ago"> · {ago}</span></div>'
            )
        else:
            last_seen_line = ""

        cards.append(f"""
        <div class="cat-card">
          {img}
          <div class="info">
            <div class="name">{html.escape(cat.name)}</div>
            {avg_line}
            {last_seen_line}
          </div>
        </div>
        """)

    return f'<div class="cats-header">{"".join(cards)}</div>'


def _build_sensors_strip(
    con: duckdb.DuckDBPyConnection,
    sensors: list,
    tz: str,
    algo: str = "v1",
) -> str:
    rows = con.execute(f"""
        SELECT
            sensor_id,
            max(to_timestamp(timestamp / 1000) AT TIME ZONE '{tz}')::varchar AS last_cleaned,
            epoch_ms(now()) - max(timestamp) AS delta_ms
        FROM sqlite.events
        WHERE type = 'cleaning' AND algo = '{algo}'
        GROUP BY sensor_id
        ORDER BY sensor_id
    """).fetchall()

    if not rows:
        return ""

    sensor_name = {s.id: s.name for s in sensors}

    items: list[str] = []
    for sensor_id, last_cleaned_str, delta_ms in rows:
        friendly = html.escape(sensor_name.get(sensor_id, sensor_id))
        if last_cleaned_str:
            dt = html.escape(last_cleaned_str[:16])
            ago = _ago(delta_ms)
            stat = f'last cleaned {dt}<span class="ago"> · {ago}</span>'
        else:
            stat = "never cleaned"
        items.append(
            f'<div class="sensor-item">'
            f'<span class="sensor-name">{friendly}</span>'
            f'<span class="sensor-stat"> — {stat}</span>'
            f'</div>'
        )

    return f'<div class="sensors-strip">{"".join(items)}</div>'


def _build_health_section(
    health_rows: list,
    week_weights: list,
    cats: list[CatProfile],
    cat_color: dict[str, str],
    cat_alert: dict[str, int],
) -> str:
    """Health status cards + last-7-day weight chart with 30d band."""
    cat_by_name = {c.name: c for c in cats}

    # Build status cards
    cards: list[str] = []
    for row in health_rows:
        name, gap_ms, last_g, avg_g, min_g, max_g = row

        # Potty gap severity
        gap_h = int(gap_ms) // 3_600_000
        gap_m = (int(gap_ms) % 3_600_000) // 60_000
        if gap_h < 12:
            sev = "ok"
            gap_str = f"{gap_h}h {gap_m:02d}m ago"
        elif gap_h < 24:
            sev = "warn"
            gap_str = f"{gap_h}h {gap_m:02d}m ago"
        else:
            sev = "bad"
            days = gap_h // 24
            hrs = gap_h % 24
            gap_str = f"{days}d {hrs}h ago"

        # Weight drift row
        weight_html = ""
        if last_g is not None and avg_g is not None:
            last_kg = last_g / 1000.0
            lbs = last_kg * 2.20462
            delta = last_g - avg_g
            sign = "+" if delta >= 0 else "−"
            arrow = "↑" if delta > 0 else "↓"
            alert = cat_alert.get(name, 300)
            drift_sev = "bad" if abs(delta) > alert else ("warn" if abs(delta) > alert * 0.7 else "ok")
            weight_html = f"""
          <div class="h-row">
            <span class="h-label">Weight</span>
            <span class="h-val">{last_kg:.2f} kg ({lbs:.2f} lbs)</span>
          </div>
          <div class="h-row">
            <span class="h-label">vs 30d avg</span>
            <span class="h-delta {drift_sev}">{arrow} {sign}{abs(int(delta))}g</span>
          </div>"""

        cards.append(f"""
        <div class="health-card {sev}">
          <div class="h-name">{html.escape(name)}</div>
          <div class="h-row">
            <span class="h-label">Last potty</span>
            <span class="h-val {sev}">{html.escape(gap_str)}</span>
          </div>
          {weight_html}
        </div>""")

    if not cards:
        cards_html = '<p class="no-active">No cats seen in the last 7 days.</p>'
    else:
        cards_html = f'<div class="health-grid">{"".join(cards)}</div>'

    # Build weight chart (7d scatter + 30d band)
    active_names = [row[0] for row in health_rows]
    if not active_names:
        return cards_html

    # Band data keyed by cat
    band = {row[0]: (row[3], row[4], row[5]) for row in health_rows}  # avg_g, min_g, max_g

    # Scatter keyed by cat
    pts_by_cat: dict[str, list] = {}
    for row in week_weights:
        pts_by_cat.setdefault(row[0], []).append((row[1], row[2]))

    now_dt = datetime.now()
    week_ago_dt = now_dt - timedelta(days=7)

    fig = go.Figure()
    for name in active_names:
        color = cat_color.get(name, _CAT_COLORS[0])
        r, g, b = _hex_to_rgb(color)
        avg_g, min_g, max_g = band.get(name, (None, None, None))

        if avg_g and min_g and max_g:
            min_kg = min_g / 1000.0
            max_kg = max_g / 1000.0
            avg_kg = avg_g / 1000.0
            fig.add_trace(go.Scatter(
                x=[week_ago_dt, now_dt], y=[max_kg, max_kg],
                mode="lines", line=dict(width=0),
                showlegend=False, hoverinfo="skip",
            ))
            fig.add_trace(go.Scatter(
                x=[week_ago_dt, now_dt], y=[min_kg, min_kg],
                mode="lines", line=dict(width=0),
                fill="tonexty", fillcolor=f"rgba({r},{g},{b},0.12)",
                showlegend=False, hoverinfo="skip",
            ))
            fig.add_trace(go.Scatter(
                x=[week_ago_dt, now_dt], y=[avg_kg, avg_kg],
                mode="lines", line=dict(color=color, width=1.5, dash="dash"),
                name=f"{name} 30d avg",
                hovertemplate=f"{name} 30d avg: {avg_kg:.2f} kg<extra></extra>",
            ))

        pts = pts_by_cat.get(name, [])
        if pts:
            lbs_vals = [p[1] * 2.20462 for p in pts]
            fig.add_trace(go.Scatter(
                x=[p[0] for p in pts],
                y=[p[1] for p in pts],
                mode="markers",
                name=name,
                marker=dict(color=color, size=7, opacity=0.8, line=dict(width=0)),
                customdata=[[lb] for lb in lbs_vals],
                hovertemplate=(
                    f"{name} %{{x|%b %d %H:%M}}: "
                    f"%{{y:.2f}} kg (%{{customdata[0]:.2f}} lbs)<extra></extra>"
                ),
            ))

    fig.update_layout(
        title=dict(
            text="Weight — last 7 days  <span style='font-size:11px;color:#57606a'>"
                 "(shaded band = 30d min/max · dashed = 30d avg)</span>",
            font_size=13, font_color="#1f2328",
        ),
        paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
        font_color="#1f2328",
        margin=dict(t=45, b=30, l=52, r=16),
        height=300, autosize=True,
        legend=dict(
            orientation="h", yanchor="bottom", y=-0.30,
            xanchor="center", x=0.5,
            bgcolor="#f6f8fa", bordercolor="#d0d7de", borderwidth=1, font_size=11,
        ),
    )
    fig.update_yaxes(title_text="kg", showgrid=True, gridcolor="#d0d7de", title_font_size=11)
    fig.update_xaxes(showgrid=False, tickfont_size=10)

    chart_html = fig.to_html(full_html=False, include_plotlyjs=False, config={"responsive": True})
    return cards_html + f'<div class="chart-wrap">{chart_html}</div>'


def _attach_sources(con: duckdb.DuckDBPyConnection, db_path: str, archive_dir: Path) -> str:
    con.execute(f"ATTACH '{db_path}' AS sqlite (TYPE sqlite, READ_ONLY)")

    parquet_files = sorted(archive_dir.glob("*.parquet")) if archive_dir.exists() else []

    if parquet_files:
        globs = str(archive_dir / "*.parquet")
        newest_day = date.fromisoformat(parquet_files[-1].stem)
        sqlite_from_ms = int(
            datetime(newest_day.year, newest_day.month, newest_day.day,
                     tzinfo=timezone.utc).timestamp() * 1000
        ) + 86_400_000
        con.execute(f"""
            CREATE VIEW all_measurements AS
            SELECT * FROM sqlite.raw_measurements WHERE timestamp >= {sqlite_from_ms}
            UNION ALL
            SELECT * FROM read_parquet('{globs}')
        """)
        log.info("using SQLite (from %s) + %d Parquet file(s)",
                 newest_day + timedelta(days=1), len(parquet_files))
    else:
        con.execute("CREATE VIEW all_measurements AS SELECT * FROM sqlite.raw_measurements")
        log.info("using SQLite only (no Parquet archive yet)")

    return "all_measurements"


_PAGE_CSS = """
*, *::before, *::after { box-sizing: border-box; }
body {
  background: #f6f8fa;
  color: #1f2328;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  margin: 0; padding: 0;
}
/* Cat header */
.cats-header {
  display: flex; flex-wrap: wrap; gap: 20px;
  padding: 16px 20px;
  border-bottom: 1px solid #d0d7de;
  background: #ffffff;
}
.cat-card { display: flex; align-items: center; gap: 12px; }
.cat-card img, .cat-card .avatar-fallback {
  width: 60px; height: 60px; border-radius: 50%; object-fit: cover;
  border: 2px solid #d0d7de; flex-shrink: 0;
}
.cat-card .avatar-fallback {
  background: #eaf5ff; color: #0969da; font-size: 26px; font-weight: 600;
  display: flex; align-items: center; justify-content: center;
}
.cat-card .info { font-size: 13px; line-height: 1.55; }
.cat-card .name { font-weight: 600; font-size: 15px; color: #1f2328; }
.cat-card .stat { color: #57606a; }
.cat-card .stat .ago { color: #8250df; font-weight: 500; }
/* Sensors strip */
.sensors-strip {
  display: flex; flex-wrap: wrap; gap: 20px;
  padding: 10px 20px;
  border-bottom: 1px solid #d0d7de;
  background: #f6f8fa; font-size: 13px;
}
.sensor-item { color: #57606a; }
.sensor-item .sensor-name { font-weight: 600; color: #1f2328; }
.sensor-item .ago { color: #8250df; font-weight: 500; }
/* Tabs */
.tab-nav {
  display: flex; gap: 0;
  padding: 12px 20px 0;
  border-bottom: 1px solid #d0d7de;
  background: #f6f8fa;
}
.tab-btn {
  padding: 7px 20px;
  border: 1px solid transparent; border-bottom: none;
  border-radius: 6px 6px 0 0;
  background: none; cursor: pointer;
  font-size: 13px; font-family: inherit; color: #57606a;
  margin-right: 4px;
}
.tab-btn.active {
  background: #fff; border-color: #d0d7de; border-bottom-color: #fff;
  color: #1f2328; font-weight: 600; margin-bottom: -1px;
}
.tab-pane { display: none; }
.tab-pane.active { display: block; }
/* Health cards */
.health-grid {
  display: flex; flex-wrap: wrap; gap: 12px;
  padding: 16px 20px;
}
.health-card {
  background: #fff;
  border: 1px solid #d0d7de; border-left: 4px solid #d0d7de;
  border-radius: 6px; padding: 14px 18px;
  min-width: 220px; flex: 1; max-width: 340px;
}
.health-card.ok  { border-left-color: #2da44e; }
.health-card.warn { border-left-color: #bf8700; }
.health-card.bad  { border-left-color: #cf222e; }
.h-name { font-weight: 600; font-size: 15px; color: #1f2328; margin-bottom: 10px; }
.h-row {
  display: flex; justify-content: space-between; align-items: baseline;
  margin-bottom: 5px; font-size: 13px;
}
.h-label { color: #57606a; }
.h-val { font-weight: 500; color: #1f2328; }
.h-val.ok   { color: #2da44e; }
.h-val.warn { color: #bf8700; }
.h-val.bad  { color: #cf222e; font-weight: 600; }
.h-delta { font-size: 12px; font-weight: 500; }
.h-delta.ok   { color: #57606a; }
.h-delta.warn { color: #bf8700; }
.h-delta.bad  { color: #cf222e; }
.chart-wrap { padding: 4px 20px 16px; }
.no-active { padding: 16px 20px; color: #57606a; }
/* Mobile */
@media (max-width: 480px) {
  .cats-header { gap: 14px; padding: 12px 14px; }
  .cat-card img, .cat-card .avatar-fallback { width: 48px; height: 48px; font-size: 20px; }
  .cat-card .info { font-size: 12px; }
  .cat-card .name { font-size: 14px; }
  .health-card { max-width: 100%; }
  .chart-wrap { padding: 4px 8px 12px; }
}
"""

_TAB_JS = """
function switchTab(name) {
  document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  document.getElementById('btn-' + name).classList.add('active');
}
"""


def build(db_path: str, archive_dir: str, out_path: str) -> None:
    cfg = load_config()
    tz = cfg.timezone
    algo = cfg.active_algos[0] if cfg.active_algos else "v1"

    con = duckdb.connect()
    _attach_sources(con, db_path, Path(archive_dir))

    cats_header = _build_cats_header(con, cfg.cats, tz, algo)
    sensors_strip = _build_sensors_strip(con, cfg.sensors, tz, algo)

    # Health tab: active cats (seen within 7 days) + 30d band
    health_rows = con.execute(f"""
        WITH last7 AS (
            SELECT cat, epoch_ms(now()) - max(timestamp) AS gap_ms
            FROM sqlite.events
            WHERE type = 'potty' AND cat IS NOT NULL AND algo = '{algo}'
              AND timestamp >= epoch_ms(now()) - 7 * 86400000
            GROUP BY cat
        ),
        last_w AS (
            SELECT cat, weight_g,
                   row_number() OVER (PARTITION BY cat ORDER BY timestamp DESC) AS rn
            FROM sqlite.events
            WHERE type = 'potty' AND cat IS NOT NULL AND algo = '{algo}'
        ),
        band30 AS (
            SELECT cat, avg(weight_g) AS avg_g, min(weight_g) AS min_g, max(weight_g) AS max_g
            FROM sqlite.events
            WHERE type = 'potty' AND cat IS NOT NULL AND algo = '{algo}'
              AND timestamp >= epoch_ms(now()) - 30 * 86400000
            GROUP BY cat
        )
        SELECT l7.cat, l7.gap_ms, lw.weight_g, b.avg_g, b.min_g, b.max_g
        FROM last7 l7
        LEFT JOIN last_w lw ON l7.cat = lw.cat AND lw.rn = 1
        LEFT JOIN band30 b ON l7.cat = b.cat
        ORDER BY l7.cat
    """).fetchall()

    week_weights = con.execute(f"""
        SELECT cat,
               to_timestamp(timestamp / 1000) AT TIME ZONE '{tz}' AS ts,
               weight_g / 1000.0 AS kg
        FROM sqlite.events
        WHERE type = 'potty' AND cat IS NOT NULL AND algo = '{algo}'
          AND timestamp >= epoch_ms(now()) - 7 * 86400000
        ORDER BY ts
    """).fetchall()

    # Charts tab: existing data
    history = con.execute("""
        SELECT
            date_trunc('day',  to_timestamp(timestamp / 1000)) AS day,
            date_part('hour',  to_timestamp(timestamp / 1000)) AS hour,
            count(*) AS n
        FROM all_measurements
        GROUP BY 1, 2
        ORDER BY 1, 2
    """).fetchall()

    weight_24h = con.execute("""
        SELECT
            to_timestamp(timestamp / 1000) AS ts,
            sensor_id,
            value
        FROM all_measurements
        WHERE sensor_type = 'weight'
          AND timestamp >= epoch_ms(now()) - 86400000
        ORDER BY ts
    """).fetchall()

    cat_weights = con.execute(f"""
        SELECT
            (to_timestamp(timestamp / 1000) AT TIME ZONE '{tz}') AS ts,
            cat,
            weight_g
        FROM sqlite.events
        WHERE type = 'potty'
          AND cat IS NOT NULL
          AND algo = '{algo}'
          AND timestamp >= epoch_ms(now()) - 30::BIGINT * 86400000
        ORDER BY ts
    """).fetchall()

    cat_daily_avg = con.execute(f"""
        SELECT
            date_trunc('day', to_timestamp(timestamp / 1000) AT TIME ZONE '{tz}') AS day,
            cat,
            avg(weight_g) / 1000.0 AS avg_kg
        FROM sqlite.events
        WHERE type = 'potty'
          AND cat IS NOT NULL
          AND algo = '{algo}'
          AND timestamp >= epoch_ms(now()) - 30::BIGINT * 86400000
        GROUP BY 1, 2
        ORDER BY 1
    """).fetchall()

    daily_potty = con.execute(f"""
        SELECT
            date_trunc('day', to_timestamp(timestamp / 1000) AT TIME ZONE '{tz}') AS day,
            cat,
            count(*) AS n
        FROM sqlite.events
        WHERE type = 'potty'
          AND cat IS NOT NULL
          AND algo = '{algo}'
          AND timestamp >= epoch_ms(now()) - 30::BIGINT * 86400000
        GROUP BY 1, 2
        ORDER BY 1
    """).fetchall()

    daily_cleaning = con.execute(f"""
        SELECT
            date_trunc('day', to_timestamp(timestamp / 1000) AT TIME ZONE '{tz}') AS day,
            count(*) AS n
        FROM sqlite.events
        WHERE type = 'cleaning'
          AND algo = '{algo}'
          AND timestamp >= epoch_ms(now()) - 30::BIGINT * 86400000
        GROUP BY 1
        ORDER BY 1
    """).fetchall()

    stats = con.execute("""
        SELECT
            count(*)                                             AS total_readings,
            count(DISTINCT sensor_id)                           AS sensors,
            min(to_timestamp(timestamp / 1000))::varchar        AS oldest,
            max(to_timestamp(timestamp / 1000))::varchar        AS newest
        FROM all_measurements
    """).fetchone()

    n_events = con.execute(
        f"SELECT count(*) FROM sqlite.events WHERE algo = '{algo}'"
    ).fetchone()[0]

    con.close()

    total_readings, n_sensors, oldest, newest = stats

    configured_names = [c.name for c in cfg.cats]
    names_in_data = {r[1] for r in cat_weights}
    cat_names = [n for n in configured_names if n in names_in_data]
    cat_names += sorted(names_in_data - set(configured_names))
    cat_color = {name: _CAT_COLORS[i % len(_CAT_COLORS)] for i, name in enumerate(cat_names)}
    cat_alert = {c.name: c.weight_alert_g for c in cfg.cats}

    # ── Health tab ──────────────────────────────────────────────────────────
    health_html = _build_health_section(
        health_rows, week_weights, cfg.cats, cat_color, cat_alert,
    )

    # ── Charts tab ──────────────────────────────────────────────────────────
    n_cat_bars = len(cat_names)
    n_rows = 1 + n_cat_bars + 1 + 1 + 1

    raw_h = [0.30] + [0.13] * n_cat_bars + [0.12, 0.14, 0.22]
    total_h = sum(raw_h)
    row_heights = [h / total_h for h in raw_h]

    subplot_titles = (
        ["Potty weight by cat — last 30 days"]
        + [f"{name} — daily visits" for name in cat_names]
        + ["Litter box cleanings — last 30 days",
           "Data density — readings per hour (all time)",
           "Raw sensor weight — last 24 hours"]
    )

    fig = make_subplots(
        rows=n_rows, cols=1,
        row_heights=row_heights,
        subplot_titles=subplot_titles,
        vertical_spacing=0.055,
    )

    for name in cat_names:
        color = cat_color[name]
        pts = [(r[0], r[2] / 1000.0) for r in cat_weights if r[1] == name]
        if not pts:
            continue
        lbs_vals = [p[1] * 2.20462 for p in pts]
        fig.add_trace(
            go.Scatter(
                x=[p[0] for p in pts], y=[p[1] for p in pts],
                mode="markers", name=name,
                marker=dict(color=color, size=6, opacity=0.55, line=dict(width=0)),
                customdata=[[lb] for lb in lbs_vals],
                hovertemplate=(
                    f"{name} %{{x|%Y-%m-%d %H:%M}}: "
                    f"%{{y:.2f}} kg (%{{customdata[0]:.2f}} lbs)<extra></extra>"
                ),
            ),
            row=1, col=1,
        )
        avg_pts = [(r[0], r[2]) for r in cat_daily_avg if r[1] == name]
        if avg_pts:
            avg_lbs = [p[1] * 2.20462 for p in avg_pts]
            fig.add_trace(
                go.Scatter(
                    x=[p[0] for p in avg_pts], y=[p[1] for p in avg_pts],
                    mode="lines+markers", name=f"{name} daily avg",
                    showlegend=False,
                    line=dict(color=color, width=2.5),
                    marker=dict(color=color, size=5),
                    customdata=[[lb] for lb in avg_lbs],
                    hovertemplate=(
                        f"{name} avg %{{x|%Y-%m-%d}}: "
                        f"%{{y:.2f}} kg (%{{customdata[0]:.2f}} lbs)<extra></extra>"
                    ),
                ),
                row=1, col=1,
            )
    fig.update_yaxes(title_text="kg", row=1, col=1)

    for offset, name in enumerate(cat_names):
        row_idx = 2 + offset
        color = cat_color[name]
        pts = [(r[0], r[2]) for r in daily_potty if r[1] == name]
        if pts:
            fig.add_trace(
                go.Bar(
                    x=[p[0] for p in pts], y=[p[1] for p in pts],
                    name=name, showlegend=False, marker_color=color,
                    hovertemplate=f"{name} %{{x|%b %d}}: %{{y}} visits<extra></extra>",
                ),
                row=row_idx, col=1,
            )
        fig.update_yaxes(title_text="visits", row=row_idx, col=1, tick0=0, dtick=1)

    clean_row = 2 + n_cat_bars
    if daily_cleaning:
        pts = [(r[0], r[1]) for r in daily_cleaning]
        fig.add_trace(
            go.Bar(
                x=[p[0] for p in pts], y=[p[1] for p in pts],
                name="cleaning", showlegend=False, marker_color=_CLEAN_COLOR,
                hovertemplate="%{x|%b %d}: %{y} cleanings<extra></extra>",
            ),
            row=clean_row, col=1,
        )
    fig.update_yaxes(title_text="cleans", row=clean_row, col=1, tick0=0, dtick=1)

    density_row = clean_row + 1
    if history:
        days  = sorted({str(r[0].date()) for r in history})
        hours = list(range(24))
        grid  = {(str(r[0].date()), int(r[1])): r[2] for r in history}
        z     = [[grid.get((d, h), 0) for d in days] for h in hours]
        fig.add_trace(
            go.Heatmap(
                z=z, x=days, y=[f"{h:02d}:00" for h in hours],
                colorscale=[[0, "#ebedf0"], [0.01, "#9be9a8"], [0.25, "#40c463"],
                            [0.6, "#30a14e"], [1, "#216e39"]],
                showscale=False, showlegend=False,
                hovertemplate="%{x} %{y}: %{z} readings<extra></extra>",
            ),
            row=density_row, col=1,
        )

    raw_row = density_row + 1
    if weight_24h:
        raw_sensors = sorted({r[1] for r in weight_24h})
        for i, sid in enumerate(raw_sensors):
            pts = [(r[0], r[2]) for r in weight_24h if r[1] == sid]
            fig.add_trace(
                go.Scatter(
                    x=[p[0] for p in pts], y=[p[1] for p in pts],
                    mode="lines", name=sid, showlegend=False,
                    line=dict(color=_CAT_COLORS[i % len(_CAT_COLORS)], width=1),
                    hovertemplate=f"{sid} %{{x|%H:%M:%S}}: %{{y:.0f}}g<extra></extra>",
                ),
                row=raw_row, col=1,
            )
        fig.update_yaxes(title_text="g", row=raw_row, col=1)

    height = 1050 + n_cat_bars * 160
    fig.update_layout(
        title=dict(
            text=(
                f"petascale — {total_readings:,} readings · {n_sensors} sensor(s)"
                f" · {n_events} events<br>"
                f"<sup>{oldest}  →  {newest}</sup>"
            ),
            font_size=15, font_color="#1f2328",
        ),
        paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
        font_color="#1f2328",
        margin=dict(t=85, b=65, l=52, r=16),
        height=height, autosize=True,
        legend=dict(
            orientation="h", yanchor="bottom", y=-0.05, xanchor="center", x=0.5,
            bgcolor="#f6f8fa", bordercolor="#d0d7de", borderwidth=1, font_size=12,
        ),
        barmode="stack",
    )
    fig.update_xaxes(showgrid=False, tickfont_size=10)
    fig.update_yaxes(showgrid=True, gridcolor="#d0d7de", title_font_size=11)

    charts_fig_html = fig.to_html(
        full_html=False, include_plotlyjs=False, config={"responsive": True},
    )

    # ── Assemble page ────────────────────────────────────────────────────────
    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>petascale</title>
  <script src="{_PLOTLY_CDN}"></script>
  <style>{_PAGE_CSS}</style>
</head>
<body>
{cats_header}
{sensors_strip}
<div class="tab-nav">
  <button id="btn-health" class="tab-btn active" onclick="switchTab('health')">Health</button>
  <button id="btn-charts" class="tab-btn" onclick="switchTab('charts')">Charts</button>
</div>
<div id="tab-health" class="tab-pane active">
{health_html}
</div>
<div id="tab-charts" class="tab-pane">
{charts_fig_html}
</div>
<script>{_TAB_JS}</script>
</body>
</html>"""

    Path(out_path).write_text(page)
    log.info("wrote %s  (%d readings, %d sensors, %d events)",
             out_path, total_readings, n_sensors, n_events)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--db",      default=_DB_DEFAULT)
    parser.add_argument("--archive", default=_ARCHIVE_DEFAULT)
    parser.add_argument("--out",     default=_OUT_DEFAULT)
    args = parser.parse_args()
    build(args.db, args.archive, args.out)
