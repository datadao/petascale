"""Render petascale dashboard to a self-contained HTML file.

Reads SQLite directly (last 7 days) + Parquet archive (older data).

Usage:
    uv run --extra analytics python scripts/dashboard.py [--db PATH] [--archive DIR] [--out PATH]
"""

import argparse
import logging
from pathlib import Path

import duckdb
import plotly.graph_objects as go
from plotly.subplots import make_subplots

log = logging.getLogger(__name__)

_DB_DEFAULT = "/data/petascale.db"
_ARCHIVE_DEFAULT = "/data/archive"
_OUT_DEFAULT = "/data/dashboard.html"


def _attach_sources(con: duckdb.DuckDBPyConnection, db_path: str, archive_dir: Path) -> str:
    """Attach SQLite and create a unified view over SQLite + Parquet."""
    con.execute(f"ATTACH '{db_path}' AS sqlite (TYPE sqlite, READ_ONLY)")

    parquet_files = sorted(archive_dir.glob("*.parquet")) if archive_dir.exists() else []

    if parquet_files:
        globs = str(archive_dir / "*.parquet")
        con.execute(f"""
            CREATE VIEW all_measurements AS
            SELECT * FROM sqlite.raw_measurements
            UNION ALL
            SELECT * FROM read_parquet('{globs}')
        """)
        log.info("using SQLite + %d Parquet file(s)", len(parquet_files))
    else:
        con.execute("CREATE VIEW all_measurements AS SELECT * FROM sqlite.raw_measurements")
        log.info("using SQLite only (no Parquet archive yet)")

    return "all_measurements"


def build(db_path: str, archive_dir: str, out_path: str) -> None:
    con = duckdb.connect()
    _attach_sources(con, db_path, Path(archive_dir))

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

    stats = con.execute("""
        SELECT
            count(*)                                             AS total_readings,
            count(DISTINCT sensor_id)                           AS sensors,
            min(to_timestamp(timestamp / 1000))::varchar        AS oldest,
            max(to_timestamp(timestamp / 1000))::varchar        AS newest
        FROM all_measurements
    """).fetchone()

    con.close()

    total_readings, n_sensors, oldest, newest = stats

    fig = make_subplots(
        rows=2, cols=1,
        row_heights=[0.40, 0.60],
        subplot_titles=[
            "Data density — readings per hour (all time)",
            "Raw weight — last 24 hours",
        ],
        vertical_spacing=0.12,
    )

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

    if weight_24h:
        sensors = sorted({r[1] for r in weight_24h})
        colors  = ["#26a641", "#58a6ff", "#f78166", "#d2a8ff"]
        for i, sid in enumerate(sensors):
            pts = [(r[0], r[2]) for r in weight_24h if r[1] == sid]
            fig.add_trace(
                go.Scatter(
                    x=[p[0] for p in pts],
                    y=[p[1] for p in pts],
                    mode="lines",
                    name=sid,
                    line=dict(color=colors[i % len(colors)], width=1),
                    hovertemplate=f"{sid} %{{x|%H:%M:%S}}: %{{y:.0f}}g<extra></extra>",
                ),
                row=2, col=1,
            )
        fig.update_yaxes(title_text="grams", row=2, col=1)

    fig.update_layout(
        title=dict(
            text=(
                f"petascale — {total_readings:,} readings · {n_sensors} sensor(s)<br>"
                f"<sup>{oldest}  →  {newest}</sup>"
            ),
            font_size=16,
        ),
        paper_bgcolor="#0d1117",
        plot_bgcolor="#0d1117",
        font_color="#c9d1d9",
        margin=dict(t=100, b=40, l=60, r=20),
        height=720,
        legend=dict(bgcolor="#161b22", bordercolor="#30363d", borderwidth=1),
    )
    fig.update_xaxes(showgrid=False, tickfont_size=10)
    fig.update_yaxes(showgrid=True, gridcolor="#21262d")

    Path(out_path).write_text(fig.to_html(full_html=True, include_plotlyjs="cdn"))
    log.info("wrote %s  (%d readings, %d sensors)", out_path, total_readings, n_sensors)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--db",      default=_DB_DEFAULT)
    parser.add_argument("--archive", default=_ARCHIVE_DEFAULT)
    parser.add_argument("--out",     default=_OUT_DEFAULT)
    args = parser.parse_args()
    build(args.db, args.archive, args.out)
