"""Classify a segment as potty, cleaning, or noise (filtered)."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import polars as pl


@dataclass
class SegmentClassification:
    segment_start: datetime
    segment_end: datetime
    start_value: float
    end_value: float
    peak_value: float
    type: str  # "potty" | "cleaning"
    cat_weight_g: int  # potty: positive; cleaning: negative drop


def classify_segment(
    segment: pl.DataFrame,
    *,
    ts_col: str = "ts",
    value_col: str = "value",
    event_delta_g: float = 10.0,
    min_potty_peak_delta_g: float = 500.0,
    min_cleaning_drop_g: float = 100.0,
    min_event_duration_s: float = 3.0,
    plateau_fn: Callable[[pl.DataFrame], float | None],
) -> SegmentClassification | None:
    """Return a classification or None if the segment should be filtered.

    Filters (algo.md D7-D11, D15):
      - too short: duration < min_event_duration_s
      - paranormal (|delta| <= event_delta_g): not emitted
      - potty without real cat: peak - start < min_potty_peak_delta_g
      - cleaning noise: start - end < min_cleaning_drop_g
      - potty with non-positive cat weight (sign assertion, D16)
    """
    if segment.is_empty():
        return None
    values = segment.get_column(value_col)
    times = segment.get_column(ts_col)

    start_val = float(values.item(0))
    end_val = float(values.item(-1))
    peak_val = float(values.max())
    seg_start = times.item(0)
    seg_end = times.item(-1)
    duration = (seg_end - seg_start).total_seconds()
    if duration < min_event_duration_s:
        return None

    # Potty
    if end_val > start_val + event_delta_g:
        if (peak_val - start_val) < min_potty_peak_delta_g:
            return None
        plateau = plateau_fn(segment)
        if plateau is None:
            return None
        cat_weight_g = int(round(plateau - start_val))
        if cat_weight_g <= 0:
            return None
        return SegmentClassification(
            segment_start=seg_start, segment_end=seg_end,
            start_value=start_val, end_value=end_val, peak_value=peak_val,
            type="potty", cat_weight_g=cat_weight_g,
        )

    # Cleaning
    if end_val < start_val - event_delta_g:
        drop = start_val - end_val
        if drop < min_cleaning_drop_g:
            return None
        return SegmentClassification(
            segment_start=seg_start, segment_end=seg_end,
            start_value=start_val, end_value=end_val, peak_value=peak_val,
            type="cleaning", cat_weight_g=int(round(end_val - start_val)),
        )

    # Paranormal: not emitted (D15)
    return None
