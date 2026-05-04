"""Estimate a plateau weight from a potty segment (cat fully on the scale)."""

import polars as pl


def estimate_plateau(
    segment: pl.DataFrame,
    *,
    value_col: str = "value",
    round_g: float = 0.5,
    iqr_multiplier: float = 1.5,
) -> float | None:
    """Return the modal plateau value, or None if not estimable.

    Algorithm (matches algo.md §3.5, D3-D6):
      1. midpoint = (start_value + max_value) / 2
      2. high = samples strictly above midpoint
      3. drop outliers via 1.5×IQR rule (inclusive bounds)
      4. quantize to `round_g`, return the smallest tied mode
    """
    if segment.is_empty():
        return None
    values = segment.get_column(value_col)
    start_val = float(values.item(0))
    max_val = float(values.max())
    midpoint = (start_val + max_val) / 2.0

    high = values.filter(values > midpoint)
    if high.is_empty():
        return None

    q1 = float(high.quantile(0.25))
    q3 = float(high.quantile(0.75))
    iqr = q3 - q1
    lo, hi = q1 - iqr_multiplier * iqr, q3 + iqr_multiplier * iqr
    filt = high.filter((high >= lo) & (high <= hi))
    if filt.is_empty():
        return None

    buckets = (filt / round_g).round() * round_g
    mode_set = buckets.mode()
    if mode_set.is_empty():
        return None
    return float(mode_set.min())
