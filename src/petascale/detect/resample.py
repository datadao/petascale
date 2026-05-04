"""Resample irregular sensor stream to a uniform 1 Hz grid with capped ffill."""

import polars as pl


def resample_1hz_ffill(
    df: pl.DataFrame,
    *,
    ts_col: str = "ts",
    value_col: str = "value",
    freq_s: int = 1,
    ffill_cap_s: int = 300,
) -> pl.DataFrame:
    """Resample to a uniform grid; forward-fill up to `ffill_cap_s` then leave NaN.

    Drops NaN rows after fill, including the leading-NaN block before the first
    real reading (D2 in algo.md). De-dupes on `ts_col` (keep last) before upsample.
    """
    if df.is_empty():
        return df
    # Snap to a `freq_s`-aligned grid so polars `upsample` matches every row.
    # When multiple raw samples fall in the same bucket, keep the last one
    # (matches HA "latest known value at second X" semantics).
    df = (
        df.drop_nulls([value_col])
        .sort(ts_col)
        .with_columns(pl.col(ts_col).dt.truncate(f"{freq_s}s"))
        .unique(subset=[ts_col], keep="last", maintain_order=True)
    )
    if df.is_empty():
        return df
    upsampled = df.upsample(time_column=ts_col, every=f"{freq_s}s").with_columns(
        pl.col(value_col).forward_fill(limit=ffill_cap_s)
    )
    return upsampled.drop_nulls(subset=[value_col])
