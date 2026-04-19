"""Quick local dashboard — reads petascale.db, renders to dashboard.html.

Usage:
    uv run python scripts/dashboard.py [--db petascale.db] [--out dashboard.html]
"""

import argparse
from pathlib import Path

import duckdb
import plotly.graph_objects as go
from plotly.subplots import make_subplots

DB_DEFAULT = "petascale.db"
OUT_DEFAULT = "dashboard.html"


def build(db_path: str, out_path: str) -> None:
    con = duckdb.connect()
    # DuckDB can query SQLite files directly
    con.execute(f"ATTACH '{db_path}' AS cats (TYPE sqlite)")

    # ── 1. History heatmap: readings per hour across all time ──────────────────
    history = con.execute("""
        SELECT
            date_trunc('day',  to_timestamp(timestamp / 1000)) AS day,
            date_part('hour',  to_timestamp(timestamp / 1000)) AS hour,
            count(*) AS n
        FROM cats.raw_measurements
        GROUP BY 1, 2
        ORDER BY 1, 2
    """).fetchall()

    # ── 2. Raw weight — last 24 hours ─────────────────────────────────────────
    weight_24h = con.execute("""
        SELECT
            to_timestamp(timestamp / 1000) AS ts,
            value
        FROM cats.raw_measurements
        WHERE sensor_type = 'weight'
          AND timestamp >= epoch_ms(now()) - 86400000
        ORDER BY ts
    """).fetchall()

    # ── 3. Summary stats ──────────────────────────────────────────────────────
    stats = con.execute("""
        SELECT
            count(*)                                             AS total_readings,
            count(DISTINCT sensor_id)                           AS sensors,
            min(to_timestamp(timestamp / 1000))::varchar        AS oldest,
            max(to_timestamp(timestamp / 1000))::varchar        AS newest
        FROM cats.raw_measurements
    """).fetchone()

    con.close()

    total_readings, n_sensors, oldest, newest = stats

    # ── Build figure ──────────────────────────────────────────────────────────
    fig = make_subplots(
        rows=2, cols=1,
        row_heights=[0.45, 0.55],
        subplot_titles=[
            "Data density — readings per hour (all time)",
            "Raw weight — last 24 hours",
        ],
        vertical_spacing=0.12,
    )

    # Heatmap
    if history:
        days  = sorted({str(r[0].date()) for r in history})
        hours = list(range(24))
        grid  = {(str(r[0].date()), int(r[1])): r[2] for r in history}
        z     = [[grid.get((d, h), 0) for d in days] for h in hours]

        fig.add_trace(
            go.Heatmap(
                z=z,
                x=days,
                y=[f"{h:02d}:00" for h in hours],
                colorscale=[[0, "#161b22"], [0.01, "#0e4429"], [0.25, "#006d32"],
                            [0.6, "#26a641"], [1, "#39d353"]],
                showscale=False,
                hovertemplate="%{x} %{y}: %{z} readings<extra></extra>",
            ),
            row=1, col=1,
        )

    # Weight line
    if weight_24h:
        ts     = [r[0] for r in weight_24h]
        values = [r[1] for r in weight_24h]
        fig.add_trace(
            go.Scatter(
                x=ts, y=values,
                mode="lines",
                line=dict(color="#26a641", width=1),
                hovertemplate="%{x|%H:%M:%S}: %{y:.0f}g<extra></extra>",
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
    )
    fig.update_xaxes(showgrid=False, tickfont_size=10)
    fig.update_yaxes(showgrid=True, gridcolor="#21262d")

    Path(out_path).write_text(fig.to_html(full_html=True, include_plotlyjs="cdn"))
    print(f"Wrote {out_path}  ({total_readings:,} readings, {n_sensors} sensors)")
    print(f"Open with:  open {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db",  default=DB_DEFAULT)
    parser.add_argument("--out", default=OUT_DEFAULT)
    args = parser.parse_args()
    build(args.db, args.out)
