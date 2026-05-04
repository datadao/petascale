"""Mark samples that diverge from a local rolling-mean baseline."""

import polars as pl


def mark_anomalies(
    df: pl.DataFrame,
    *,
    ts_col: str = "ts",
    value_col: str = "value",
    window_s: int = 5,
    threshold_g: float = 10.0,
) -> pl.DataFrame:
    """Add `normal` boolean column.

    A sample is normal when |value - rolling_mean| <= threshold_g, where the
    rolling mean is a trailing time-based window of `window_s` seconds
    (right-closed, inclusive of the current sample). Partial windows allowed
    at the leading edge (min_samples=1).
    """
    if df.is_empty():
        return df.with_columns(pl.lit(value=True).alias("normal"))
    rolling = pl.col(value_col).rolling_mean_by(
        ts_col, window_size=f"{window_s}s", closed="right", min_samples=1,
    )
    return df.with_columns(
        ((pl.col(value_col) - rolling).abs() <= threshold_g).alias("normal")
    )
