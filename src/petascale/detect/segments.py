"""Group consecutive anomalies into event segments using a time buffer."""

from datetime import datetime, timedelta

import polars as pl


def extract_segments(
    df: pl.DataFrame,
    *,
    ts_col: str = "ts",
    normal_col: str = "normal",
    buffer_s: int = 60,
) -> list[pl.DataFrame]:
    """Return one DataFrame slice per merged anomaly interval.

    For each abnormal timestamp t, build [t - buffer, t + buffer]; merge any
    overlapping pair; emit the rows of `df` whose timestamp falls in each
    merged interval (inclusive on both ends).
    """
    if df.is_empty():
        return []
    abn = df.filter(~pl.col(normal_col)).get_column(ts_col).to_list()
    if not abn:
        return []

    buf = timedelta(seconds=buffer_s)
    intervals: list[tuple[datetime, datetime]] = sorted(
        (t - buf, t + buf) for t in abn
    )
    merged: list[tuple[datetime, datetime]] = [intervals[0]]
    for start, end in intervals[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))

    segments: list[pl.DataFrame] = []
    for start, end in merged:
        seg = df.filter(pl.col(ts_col).is_between(start, end, closed="both"))
        if not seg.is_empty():
            segments.append(seg)
    return segments
