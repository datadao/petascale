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

_CAT_COLORS = ["#26a641", "#58a6ff", "#f78166", "#d2a8ff", "#ffa657", "#a5d6ff"]


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
    # Sniff to handle .png-extension files that are actually JPEGs.
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
) -> str:
    """Render the cat-cards strip (avatar + name + 30-day summary stats)."""
    if not cats:
        return ""

    # Per cat: most recent day with potty events, that day's mean weight, last-seen timestamp.
    stats = {
        row[0]: (row[1], row[2], row[3])  # (last_day_iso, last_day_avg_kg, last_seen_iso)
        for row in con.execute("""
            WITH daily AS (
                SELECT cat,
                       date_trunc('day', to_timestamp(timestamp / 1000)) AS day,
                       avg(weight_g) / 1000.0                            AS avg_kg
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
                   (SELECT max(to_timestamp(timestamp / 1000))::varchar
                    FROM sqlite.events
                    WHERE type = 'potty' AND cat = ld.cat) AS last_seen
            FROM latest_day ld
            WHERE rn = 1
        """).fetchall()
    }

    cards: list[str] = []
    for cat in cats:
        avatar = _avatar_data_uri(cat.avatar_path)
        last_day, last_day_avg_kg, last_seen = stats.get(cat.name, (None, None, None))

        if avatar:
            img = f'<img src="{avatar}" alt="{html.escape(cat.name)}">'
        else:
            initial = html.escape(cat.name[:1].upper())
            img = f'<div class="avatar-fallback">{initial}</div>'

        avg_line = (
            f'<div class="stat">{last_day_avg_kg:.2f} kg avg on {html.escape(last_day[:10])}</div>'
            if last_day_avg_kg is not None and last_day
            else '<div class="stat">no recent visits</div>'
        )
        last_seen_line = (
            f'<div class="stat">last seen {html.escape(last_seen[:16])}</div>'
            if last_seen
            else ""
        )

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


_PAGE_CSS = """
body {
  background: #0d1117;
  color: #c9d1d9;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  margin: 0;
  padding: 0;
}
.cats-header {
  display: flex;
  flex-wrap: wrap;
  gap: 24px;
  padding: 18px 24px;
  border-bottom: 1px solid #21262d;
  background: #0d1117;
}
.cat-card {
  display: flex;
  align-items: center;
  gap: 14px;
}
.cat-card img,
.cat-card .avatar-fallback {
  width: 64px;
  height: 64px;
  border-radius: 50%;
  object-fit: cover;
  border: 2px solid #30363d;
  flex-shrink: 0;
}
.cat-card .avatar-fallback {
  background: #161b22;
  color: #58a6ff;
  font-size: 28px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
}
.cat-card .info { font-size: 13px; line-height: 1.45; }
.cat-card .name { font-weight: 600; font-size: 15px; color: #e6edf3; }
.cat-card .stat { color: #8b949e; }
"""


def _attach_sources(con: duckdb.DuckDBPyConnection, db_path: str, archive_dir: Path) -> str:
    """Attach SQLite and create a unified view over SQLite + Parquet."""
    con.execute(f"ATTACH '{db_path}' AS sqlite (TYPE sqlite, READ_ONLY)")

    parquet_files = sorted(archive_dir.glob("*.parquet")) if archive_dir.exists() else []

    if parquet_files:
        globs = str(archive_dir / "*.parquet")
        # SQLite has backfill overlap with Parquet — only take SQLite rows
        # from the day after the newest Parquet file to avoid double-counting.
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
    con = duckdb.connect()
    _attach_sources(con, db_path, Path(archive_dir))
    cats_header = _build_cats_header(con, cfg.cats)

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

    # Per-cat potty weight history (last 30 days)
    cat_weights = con.execute("""
        SELECT
            to_timestamp(timestamp / 1000) AS ts,
            cat,
            weight_g
        FROM sqlite.events
        WHERE type = 'potty'
          AND cat IS NOT NULL
          AND timestamp >= epoch_ms(now()) - 30::BIGINT * 86400000
        ORDER BY ts
    """).fetchall()

    # Daily counts (potty per cat + cleanings)
    daily_counts = con.execute("""
        SELECT
            date_trunc('day', to_timestamp(timestamp / 1000)) AS day,
            type,
            COALESCE(cat, '_cleaning') AS series,
            count(*) AS n
        FROM sqlite.events
        WHERE timestamp >= epoch_ms(now()) - 30::BIGINT * 86400000
        GROUP BY 1, 2, 3
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

    fig = make_subplots(
        rows=4, cols=1,
        row_heights=[0.20, 0.30, 0.28, 0.22],
        subplot_titles=[
            "Data density — readings per hour (all time)",
            "Raw weight — last 24 hours",
            "Potty weight by cat — last 30 days",
            "Daily event counts — last 30 days",
        ],
        vertical_spacing=0.08,
    )

    # Row 1: data density heatmap
    if history:
        days  = sorted({str(r[0].date()) for r in history})
        hours = list(range(24))
        grid  = {(str(r[0].date()), int(r[1])): r[2] for r in history}
        z     = [[grid.get((d, h), 0) for d in days] for h in hours]

        fig.add_trace(
            go.Heatmap(
                z=z, x=days, y=[f"{h:02d}:00" for h in hours],
                colorscale=[[0, "#161b22"], [0.01, "#0e4429"], [0.25, "#006d32"],
                            [0.6, "#26a641"], [1, "#39d353"]],
                showscale=False,
                hovertemplate="%{x} %{y}: %{z} readings<extra></extra>",
            ),
            row=1, col=1,
        )

    # Row 2: raw weight last 24h
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
                    line=dict(color=_CAT_COLORS[i % len(_CAT_COLORS)], width=1),
                    hovertemplate=f"{sid} %{{x|%H:%M:%S}}: %{{y:.0f}}g<extra></extra>",
                    legendgroup="raw",
                ),
                row=2, col=1,
            )
        fig.update_yaxes(title_text="grams", row=2, col=1)

    # Row 3: per-cat potty weight scatter (kg) over last 30 days
    if cat_weights:
        cats = sorted({r[1] for r in cat_weights})
        for i, name in enumerate(cats):
            pts = [(r[0], r[2] / 1000.0) for r in cat_weights if r[1] == name]
            fig.add_trace(
                go.Scatter(
                    x=[p[0] for p in pts],
                    y=[p[1] for p in pts],
                    mode="markers",
                    name=name,
                    marker=dict(color=_CAT_COLORS[i % len(_CAT_COLORS)], size=7,
                                line=dict(width=0)),
                    hovertemplate=f"{name} %{{x|%Y-%m-%d %H:%M}}: %{{y:.2f}} kg<extra></extra>",
                    legendgroup="cats",
                ),
                row=3, col=1,
            )
        fig.update_yaxes(title_text="weight (kg)", row=3, col=1)

    # Row 4: daily event count bars
    if daily_counts:
        # Group by series (cat name or '_cleaning')
        series_set = sorted({r[2] for r in daily_counts})
        for i, ser in enumerate(series_set):
            label = "cleaning" if ser == "_cleaning" else f"{ser} potty"
            color = "#a371f7" if ser == "_cleaning" else _CAT_COLORS[i % len(_CAT_COLORS)]
            pts = [(r[0], r[3]) for r in daily_counts if r[2] == ser]
            fig.add_trace(
                go.Bar(
                    x=[p[0] for p in pts],
                    y=[p[1] for p in pts],
                    name=label,
                    marker_color=color,
                    legendgroup="counts",
                ),
                row=4, col=1,
            )
        fig.update_yaxes(title_text="count/day", row=4, col=1)

    fig.update_layout(
        title=dict(
            text=(
                f"petascale — {total_readings:,} readings · {n_sensors} sensor(s) · {n_events} events<br>"
                f"<sup>{oldest}  →  {newest}</sup>"
            ),
            font_size=16,
        ),
        paper_bgcolor="#0d1117",
        plot_bgcolor="#0d1117",
        font_color="#c9d1d9",
        margin=dict(t=100, b=40, l=60, r=20),
        height=1100,
        legend=dict(bgcolor="#161b22", bordercolor="#30363d", borderwidth=1),
        barmode="stack",
    )
    fig.update_xaxes(showgrid=False, tickfont_size=10)
    fig.update_yaxes(showgrid=True, gridcolor="#21262d")

    fig_html = fig.to_html(full_html=False, include_plotlyjs="cdn")
    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
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
