"""Compose the warm-path detection pipeline end-to-end."""

from dataclasses import dataclass
from datetime import datetime
from functools import partial

import polars as pl

from petascale.config import CatProfile, DetectionConfig
from petascale.detect.anomaly import mark_anomalies
from petascale.detect.classify import classify_segment
from petascale.detect.identify import identify_cat
from petascale.detect.plateau import estimate_plateau
from petascale.detect.resample import resample_1hz_ffill
from petascale.detect.segments import extract_segments


@dataclass
class DetectorEvent:
    timestamp: datetime
    type: str  # "potty" | "cleaning"
    cat: str | None
    weight_g: int
    cat_distance_g: int | None
    segment_start: datetime
    segment_end: datetime


def run(
    df: pl.DataFrame,
    cfg: DetectionConfig,
    cats: list[CatProfile],
    *,
    ts_col: str = "ts",
    value_col: str = "value",
) -> list[DetectorEvent]:
    """Run resample → anomaly mask → segment merge → classify → identify.

    `df` must have one row per raw reading with a `ts` (datetime) column and
    a numeric `value` column (grams). Returns events in chronological order.
    """
    if df.is_empty():
        return []

    rs = resample_1hz_ffill(
        df, ts_col=ts_col, value_col=value_col,
        freq_s=cfg.resample_freq_s, ffill_cap_s=cfg.ffill_cap_s,
    )
    if rs.is_empty():
        return []

    rs = mark_anomalies(
        rs, ts_col=ts_col, value_col=value_col,
        window_s=cfg.baseline_window_s, threshold_g=cfg.noise_threshold_g,
    )
    segments = extract_segments(
        rs, ts_col=ts_col, buffer_s=cfg.segment_buffer_s,
    )

    plateau_fn = partial(
        estimate_plateau,
        value_col=value_col,
        round_g=cfg.plateau_round_g,
        iqr_multiplier=cfg.iqr_multiplier,
    )

    events: list[DetectorEvent] = []
    for seg in segments:
        cls = classify_segment(
            seg,
            ts_col=ts_col, value_col=value_col,
            event_delta_g=cfg.event_delta_g,
            min_potty_peak_delta_g=cfg.min_potty_peak_delta_g,
            min_cleaning_drop_g=cfg.min_cleaning_drop_g,
            min_event_duration_s=cfg.min_event_duration_s,
            plateau_fn=plateau_fn,
        )
        if cls is None:
            continue

        cat_name, distance = (None, None)
        if cls.type == "potty":
            cat_name, distance = identify_cat(cls.cat_weight_g, cats)

        events.append(DetectorEvent(
            timestamp=cls.segment_end,
            type=cls.type,
            cat=cat_name,
            weight_g=cls.cat_weight_g,
            cat_distance_g=distance,
            segment_start=cls.segment_start,
            segment_end=cls.segment_end,
        ))

    return events
