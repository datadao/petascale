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


def _resolve_avatar(path: str | None) -> Path | None:
    """Find the avatar file. Tries the path as given, then /data/avatars/<basename>.

    The same local cats.local.toml works in both environments: locally the
    repo-root relative path (e.g. .private/avatars/cat_a.png) resolves; in
    the container that path doesn't exist but the deployed avatars do at
    /data/avatars/<basename>.
    """
    if not path:
        return None
    candidates = [Path(path).expanduser()]
    candidates.append(_AVATAR_PROD_DIR / Path(path).name)
    for c in candidates:
        if c.is_file():
            return c
    log.warning("avatar not found in any of: %s", [str(c) for c in candidates])
    return None


def _avatar_data_uri(path: str | None) -> str | None:
    """Read an avatar file and return a base64 data URI, or None if missing."""
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


def _build_cats_header(
    con: duckdb.DuckDBPyConnection,
    cats: list[CatProfile],
    tz: str,
) -> str:
    """Render the cat-cards strip (avatar + name + stats + last cleaning)."""
    if not cats:
        return ""

    # Per cat: most recent day avg weight (local tz) + last seen timestamp.
    stats = {
        row[0]: (row[1], row[2], row[3])
        for row in con.execute(f"""
            WITH daily AS (
                SELECT cat,
                       date_trunc('day', to_timestamp(timestamp / 1000)
                           AT TIME ZONE '{tz}')          AS day,
                       avg(weight_g) / 1000.0             AS avg_kg
                FROM sqlite.events
                WHERE type = 'potty' AND cat IS NOT NULL
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
                    WHERE type = 'potty' AND cat = ld.cat) AS last_seen
            FROM latest_day ld
            WHERE rn = 1
        """).fetchall()
    }

    # Last litter-box cleaning (shared across all cats).
    clean_row = con.execute(f"""
        SELECT
            max(to_timestamp(timestamp / 1000) AT TIME ZONE '{tz}')::varchar,
            epoch_ms(now()) - max(timestamp)
        FROM sqlite.events
        WHERE type = 'cleaning'
    """).fetchone()
    last_clean_str: str | None = None
    last_clean_ago: str | None = None
    if clean_row and clean_row[0]:
        last_clean_str = clean_row[0][:16]
        delta_ms = int(clean_row[1])
        h = delta_ms // 3_600_000
        m = (delta_ms % 3_600_000) // 60_000
        last_clean_ago = f"{h}h {m:02d}m ago"

    cards: list[str] = []
    for cat in cats:
        avatar = _avatar_data_uri(cat.avatar_path)
        last_day, last_day_avg_kg, last_seen = stats.get(cat.name, (None, None, None))

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

        last_seen_line = (
            f'<div class="stat">last seen {html.escape(last_seen[:16])}</div>'
            if last_seen else ""
        )

        if last_clean_str:
            clean_line = (
                f'<div class="stat clean">'
                f'last cleaned {html.escape(last_clean_str)}'
                f'<span class="ago"> · {html.escape(last_clean_ago or "")}</span>'
                f'</div>'
            )
        else:
            clean_line = ""

        cards.append(f"""
        <div class="cat-card">
          {img}
          <div class="info">
            <div class="name">{html.escape(cat.name)}</div>
            {avg_line}
            {last_seen_line}
            {clean_line}
          </div>
        </div>
        """)

    return f'<div class="cats-header">{"".join(cards)}</div>'


_PAGE_CSS = """
*, *::before, *::after { box-sizing: border-box; }
body {
  background: #f6f8fa;
  color: #1f2328;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  margin: 0;
  padding: 0;
}
.cats-header {
  display: flex;
  flex-wrap: wrap;
  gap: 20px;
  padding: 16px 20px;
  border-bottom: 1px solid #d0d7de;
  background: #ffffff;
}
.cat-card {
  display: flex;
  align-items: center;
  gap: 12px;
}
.cat-card img,
.cat-card .avatar-fallback {
  width: 60px;
  height: 60px;
  border-radius: 50%;
  object-fit: cover;
  border: 2px solid #d0d7de;
  flex-shrink: 0;
}
.cat-card .avatar-fallback {
  background: #eaf5ff;
  color: #0969da;
  font-size: 26px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
}
.cat-card .info { font-size: 13px; line-height: 1.55; }
.cat-card .name { font-weight: 600; font-size: 15px; color: #1f2328; }
.cat-card .stat { color: #57606a; }
.cat-card .stat.clean { color: #57606a; }
.cat-card .stat .ago { color: #8250df; font-weight: 500; }
@media (max-width: 480px) {
  .cats-header { gap: 14px; padding: 12px 14px; }
  .cat-card img, .cat-card .avatar-fallback { width: 48px; height: 48px; font-size: 20px; }
  .cat-card .info { font-size: 12px; }
  .cat-card .name { font-size: 14px; }
}
"""


def _attach_sources(con: duckdb.DuckDBPyConnection, db_path: str, archive_dir: Path) -> str:
    """Attach SQLite and create a unified view over SQLite + Parquet."""
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


def build(db_path: str, archive_dir: str, out_path: str) -> None:
    cfg = load_config()
    tz = cfg.timezone
    con = duckdb.connect()
    _attach_sources(con, db_path, Path(archive_dir))
    cats_header = _build_cats_header(con, cfg.cats, tz)

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

    # Per-cat potty weight (individual visits) — last 30 days, in local tz
    cat_weights = con.execute(f"""
        SELECT
            (to_timestamp(timestamp / 1000) AT TIME ZONE '{tz}') AS ts,
            cat,
            weight_g
        FROM sqlite.events
        WHERE type = 'potty'
          AND cat IS NOT NULL
          AND timestamp >= epoch_ms(now()) - 30::BIGINT * 86400000
        ORDER BY ts
    """).fetchall()

    # Per-cat daily average weight — last 30 days
    cat_daily_avg = con.execute(f"""
        SELECT
            date_trunc('day', to_timestamp(timestamp / 1000) AT TIME ZONE '{tz}') AS day,
            cat,
            avg(weight_g) / 1000.0 AS avg_kg
        FROM sqlite.events
        WHERE type = 'potty'
          AND cat IS NOT NULL
          AND timestamp >= epoch_ms(now()) - 30::BIGINT * 86400000
        GROUP BY 1, 2
        ORDER BY 1
    """).fetchall()

    # Per-cat daily potty visit count
    daily_potty = con.execute(f"""
        SELECT
            date_trunc('day', to_timestamp(timestamp / 1000) AT TIME ZONE '{tz}') AS day,
            cat,
            count(*) AS n
        FROM sqlite.events
        WHERE type = 'potty'
          AND cat IS NOT NULL
          AND timestamp >= epoch_ms(now()) - 30::BIGINT * 86400000
        GROUP BY 1, 2
        ORDER BY 1
    """).fetchall()

    # Daily cleaning counts
    daily_cleaning = con.execute(f"""
        SELECT
            date_trunc('day', to_timestamp(timestamp / 1000) AT TIME ZONE '{tz}') AS day,
            count(*) AS n
        FROM sqlite.events
        WHERE type = 'cleaning'
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

    n_events = con.execute("SELECT count(*) FROM sqlite.events").fetchone()[0]

    con.close()

    total_readings, n_sensors, oldest, newest = stats

    # Ordered cat names: config order for cats with data, then any unconfigured ones.
    configured_names = [c.name for c in cfg.cats]
    names_in_data = {r[1] for r in cat_weights}
    cat_names = [n for n in configured_names if n in names_in_data]
    cat_names += sorted(names_in_data - set(configured_names))
    cat_color = {name: _CAT_COLORS[i % len(_CAT_COLORS)] for i, name in enumerate(cat_names)}

    # Dynamic row layout: weight scatter | per-cat visit bars | cleaning | density | raw
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

    # Row 1: scatter (individual visits) + daily avg line per cat
    for name in cat_names:
        color = cat_color[name]
        pts = [(r[0], r[2] / 1000.0) for r in cat_weights if r[1] == name]
        if not pts:
            continue
        lbs_vals = [p[1] * 2.20462 for p in pts]
        fig.add_trace(
            go.Scatter(
                x=[p[0] for p in pts],
                y=[p[1] for p in pts],
                mode="markers",
                name=name,
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
                    x=[p[0] for p in avg_pts],
                    y=[p[1] for p in avg_pts],
                    mode="lines+markers",
                    name=f"{name} daily avg",
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

    # Rows 2…n_cat_bars+1: one bar chart per cat
    for offset, name in enumerate(cat_names):
        row_idx = 2 + offset
        color = cat_color[name]
        pts = [(r[0], r[2]) for r in daily_potty if r[1] == name]
        if pts:
            fig.add_trace(
                go.Bar(
                    x=[p[0] for p in pts],
                    y=[p[1] for p in pts],
                    name=name,
                    showlegend=False,
                    marker_color=color,
                    hovertemplate=f"{name} %{{x|%b %d}}: %{{y}} visits<extra></extra>",
                ),
                row=row_idx, col=1,
            )
        fig.update_yaxes(title_text="visits", row=row_idx, col=1,
                         tick0=0, dtick=1)

    # Cleaning row
    clean_row = 2 + n_cat_bars
    if daily_cleaning:
        pts = [(r[0], r[1]) for r in daily_cleaning]
        fig.add_trace(
            go.Bar(
                x=[p[0] for p in pts],
                y=[p[1] for p in pts],
                name="cleaning",
                showlegend=False,
                marker_color=_CLEAN_COLOR,
                hovertemplate="%{x|%b %d}: %{y} cleanings<extra></extra>",
            ),
            row=clean_row, col=1,
        )
    fig.update_yaxes(title_text="cleans", row=clean_row, col=1,
                     tick0=0, dtick=1)

    # Data density heatmap
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
                showscale=False,
                showlegend=False,
                hovertemplate="%{x} %{y}: %{z} readings<extra></extra>",
            ),
            row=density_row, col=1,
        )

    # Raw weight last 24h
    raw_row = density_row + 1
    if weight_24h:
        sensors = sorted({r[1] for r in weight_24h})
        for i, sid in enumerate(sensors):
            pts = [(r[0], r[2]) for r in weight_24h if r[1] == sid]
            fig.add_trace(
                go.Scatter(
                    x=[p[0] for p in pts],
                    y=[p[1] for p in pts],
                    mode="lines",
                    name=sid,
                    showlegend=False,
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
            font_size=15,
            font_color="#1f2328",
        ),
        paper_bgcolor="#ffffff",
        plot_bgcolor="#ffffff",
        font_color="#1f2328",
        margin=dict(t=85, b=65, l=52, r=16),
        height=height,
        autosize=True,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=-0.05,
            xanchor="center",
            x=0.5,
            bgcolor="#f6f8fa",
            bordercolor="#d0d7de",
            borderwidth=1,
            font_size=12,
        ),
        barmode="stack",
    )
    fig.update_xaxes(showgrid=False, tickfont_size=10)
    fig.update_yaxes(showgrid=True, gridcolor="#d0d7de", title_font_size=11)

    fig_html = fig.to_html(
        full_html=False,
        include_plotlyjs="cdn",
        config={"responsive": True},
    )
    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>petascale</title>
  <style>{_PAGE_CSS}</style>
</head>
<body>
{cats_header}
{fig_html}
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
