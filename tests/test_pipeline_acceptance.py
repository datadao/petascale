"""End-to-end acceptance test for the warm-path detection pipeline.

Runs the pipeline over a 2.5h slice of real sensor data and asserts that
the emitted events match the audited ground truth in expected_events.json
within tolerance (timestamp ±2s, weight ±20g; type and cat must match).

This is the D20 acceptance gate for any change to the detection pipeline.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from petascale.config import CatProfile, DetectionConfig
from petascale.detect import run

FIXTURES = Path(__file__).parent / "fixtures"
RAW_CSV = FIXTURES / "raw_window.csv"
EXPECTED_JSON = FIXTURES / "expected_events.json"


def _load_raw(path: Path) -> pl.DataFrame:
    """Load raw HA dump → (ts, value) DataFrame, dropping NULL/unavailable rows."""
    df = pl.read_csv(path, schema_overrides={"state": pl.Utf8})
    df = df.with_columns(
        pl.col("last_changed").str.to_datetime(time_unit="us").alias("ts"),
        pl.col("state").cast(pl.Float64, strict=False).alias("value"),
    ).select(["ts", "value"])
    return df.drop_nulls()


@pytest.mark.skipif(not RAW_CSV.exists(), reason="fixture missing")
def test_acceptance_window():
    expected = json.loads(EXPECTED_JSON.read_text())
    cats = [CatProfile(**c) for c in expected["cat_profiles"]]
    cfg = DetectionConfig()  # defaults match algo.md design decisions

    df = _load_raw(RAW_CSV)
    events = run(df, cfg, cats)

    # Compare to expected
    expected_events = expected["events"]
    ts_tol = timedelta(seconds=expected["comparison_tolerance"]["timestamp_seconds"])
    weight_tol = expected["comparison_tolerance"]["weight_grams"]

    assert len(events) == len(expected_events), (
        f"Got {len(events)} events, expected {len(expected_events)}: "
        f"{[(e.timestamp.isoformat(), e.type, e.cat, e.weight_g) for e in events]}"
    )

    for actual, exp in zip(events, expected_events):
        exp_ts = datetime.fromisoformat(exp["timestamp"])
        # Both naive: strip tz for comparison
        actual_ts = actual.timestamp.replace(tzinfo=None)
        assert abs(actual_ts - exp_ts) <= ts_tol, (
            f"timestamp drift > {ts_tol}: {actual_ts} vs {exp_ts}"
        )
        assert actual.type == exp["type"], (
            f"type mismatch: {actual.type} vs {exp['type']} at {actual_ts}"
        )
        assert actual.cat == exp["cat"], (
            f"cat mismatch: {actual.cat} vs {exp['cat']} at {actual_ts}"
        )
        assert abs(actual.weight_g - exp["weight_g"]) <= weight_tol, (
            f"weight drift > {weight_tol}g: "
            f"{actual.weight_g} vs {exp['weight_g']} at {actual_ts}"
        )


@pytest.mark.skipif(not RAW_CSV.exists(), reason="fixture missing")
def test_filtered_segments_not_emitted():
    """The 6 noise/paranormal segments listed in expected_events.json must NOT appear."""
    expected = json.loads(EXPECTED_JSON.read_text())
    cats = [CatProfile(**c) for c in expected["cat_profiles"]]
    cfg = DetectionConfig()

    df = _load_raw(RAW_CSV)
    events = run(df, cfg, cats)

    forbidden_ts = {
        datetime.fromisoformat(s["timestamp"])
        for s in expected["filtered_segments"]
    }
    ts_tol = timedelta(seconds=expected["comparison_tolerance"]["timestamp_seconds"])

    for evt in events:
        actual_ts = evt.timestamp.replace(tzinfo=None)
        for forbidden in forbidden_ts:
            assert abs(actual_ts - forbidden) > ts_tol, (
                f"Filtered segment {forbidden} should NOT have been emitted, "
                f"but got {evt}"
            )
