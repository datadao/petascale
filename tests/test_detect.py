"""Unit tests for petascale.detect.* modules."""

from datetime import datetime, timedelta

import polars as pl
import pytest

from petascale.config import CatProfile
from petascale.detect.anomaly import mark_anomalies
from petascale.detect.classify import classify_segment
from petascale.detect.identify import identify_cat
from petascale.detect.plateau import estimate_plateau
from petascale.detect.resample import resample_1hz_ffill
from petascale.detect.segments import extract_segments


def _frame(values: list[float], start: datetime | None = None, step_s: float = 1.0) -> pl.DataFrame:
    start = start or datetime(2026, 1, 1, 12, 0, 0)
    return pl.DataFrame({
        "ts": [start + timedelta(seconds=i * step_s) for i in range(len(values))],
        "value": values,
    })


class TestResample:
    def test_empty(self):
        df = pl.DataFrame({"ts": [], "value": []}, schema={"ts": pl.Datetime, "value": pl.Float64})
        out = resample_1hz_ffill(df)
        assert out.is_empty()

    def test_basic_1hz_grid(self):
        # Sub-second timestamps in seconds 0, 1, 3 — second 2 is a gap to fill
        start = datetime(2026, 1, 1, 12, 0, 0)
        df = pl.DataFrame({
            "ts": [
                start,
                start + timedelta(seconds=1, microseconds=500_000),
                start + timedelta(seconds=3, microseconds=200_000),
            ],
            "value": [10.0, 20.0, 30.0],
        })
        out = resample_1hz_ffill(df)
        assert out.height == 4  # seconds 0,1,2,3
        # Second 2 should be ffilled from second 1 (value 20.0)
        assert out.get_column("value").to_list() == [10.0, 20.0, 20.0, 30.0]

    def test_ffill_cap_drops_long_gap(self):
        start = datetime(2026, 1, 1, 12, 0, 0)
        df = pl.DataFrame({
            "ts": [start, start + timedelta(seconds=10)],
            "value": [10.0, 20.0],
        })
        out = resample_1hz_ffill(df, ffill_cap_s=3)
        # Only the first 4 seconds (0..3) get ffilled from t=0; seconds 4-9 stay null
        # and are dropped. Second 10 keeps its real value.
        assert out.height == 5
        assert out.get_column("value").to_list() == [10.0, 10.0, 10.0, 10.0, 20.0]

    def test_dedupes_same_second(self):
        start = datetime(2026, 1, 1, 12, 0, 0)
        df = pl.DataFrame({
            "ts": [
                start,
                start + timedelta(microseconds=100_000),
                start + timedelta(microseconds=900_000),  # last in second 0
                start + timedelta(seconds=1),
            ],
            "value": [10.0, 11.0, 12.0, 20.0],
        })
        out = resample_1hz_ffill(df)
        # Second 0 should keep the last raw reading (12.0); second 1 → 20.0
        assert out.get_column("value").to_list() == [12.0, 20.0]


class TestAnomaly:
    def test_flat_signal_all_normal(self):
        df = _frame([100.0] * 20)
        out = mark_anomalies(df, threshold_g=10.0)
        assert out.get_column("normal").all()

    def test_spike_marked_abnormal(self):
        df = _frame([100.0] * 5 + [200.0] + [100.0] * 5)
        out = mark_anomalies(df, threshold_g=10.0)
        assert out.get_column("normal").to_list()[5] is False  # spike sample

    def test_first_sample_normal_partial_window(self):
        # First sample's rolling mean is itself → diff=0 → normal=True
        df = _frame([500.0, 500.0])
        out = mark_anomalies(df, threshold_g=10.0)
        assert out.get_column("normal").item(0) is True


class TestSegments:
    def test_no_anomalies_no_segments(self):
        df = _frame([100.0] * 20)
        df = mark_anomalies(df, threshold_g=10.0)
        assert extract_segments(df) == []

    def test_two_close_anomalies_merge(self):
        # Two abnormal points 30s apart, buffer=60s → merged into one segment
        values = [100.0] * 100
        values[30] = 200.0
        values[60] = 200.0
        df = _frame(values)
        df = mark_anomalies(df)
        segs = extract_segments(df, buffer_s=60)
        assert len(segs) == 1

    def test_two_far_anomalies_separate(self):
        values = [100.0] * 300
        values[30] = 200.0
        values[250] = 200.0
        df = _frame(values)
        df = mark_anomalies(df)
        segs = extract_segments(df, buffer_s=60)
        assert len(segs) == 2


class TestPlateau:
    def test_empty_segment_returns_none(self):
        df = pl.DataFrame({"ts": [], "value": []}, schema={"ts": pl.Datetime, "value": pl.Float64})
        assert estimate_plateau(df) is None

    def test_no_high_samples_returns_none(self):
        # All values equal → max == start → midpoint == max → no values strictly above
        df = _frame([100.0] * 10)
        assert estimate_plateau(df) is None

    def test_basic_mode(self):
        # Start=100, peak=200 → midpoint=150. High vals dominated by 195
        values = [100.0] * 10 + [195.0] * 8 + [200.0, 198.0] + [100.0] * 5
        df = _frame(values)
        assert estimate_plateau(df) == 195.0

    def test_smallest_tied_mode_wins(self):
        # Two equally-frequent buckets at 195 and 196 → smallest wins (D6)
        values = [100.0] * 5 + [195.0, 196.0, 195.0, 196.0, 200.0] + [100.0] * 5
        df = _frame(values)
        assert estimate_plateau(df) == 195.0


class TestClassify:
    def _plateau_const(self, val: float):
        return lambda _seg: val

    def test_short_segment_filtered(self):
        df = _frame([100.0, 200.0])  # 1s duration < 3s default
        out = classify_segment(df, plateau_fn=self._plateau_const(200.0))
        assert out is None

    def test_potty_below_peak_threshold_filtered(self):
        # peak-start = 50g, below default 500g threshold (D8)
        df = _frame([100.0, 130.0, 150.0, 130.0, 120.0])
        out = classify_segment(df, plateau_fn=self._plateau_const(150.0))
        assert out is None

    def test_potty_emitted(self):
        # start=100, peak=900, end=120 → +20 shift > event_delta(10); peak-start=800 > 500
        df = _frame([100.0, 800.0, 900.0, 850.0, 120.0, 120.0])
        out = classify_segment(
            df, plateau_fn=self._plateau_const(880.0),
            min_event_duration_s=1.0,
        )
        assert out is not None
        assert out.type == "potty"
        assert out.cat_weight_g == 780  # 880 - 100

    def test_cleaning_below_drop_threshold_filtered(self):
        # start=500, end=470 → 30g drop, below default 100g (D9)
        df = _frame([500.0, 490.0, 480.0, 475.0, 470.0])
        out = classify_segment(df, plateau_fn=self._plateau_const(0.0))
        assert out is None

    def test_cleaning_emitted(self):
        df = _frame([500.0, 480.0, 460.0, 400.0, 350.0, 300.0])
        out = classify_segment(
            df, plateau_fn=self._plateau_const(0.0),
            min_event_duration_s=1.0,
        )
        assert out is not None
        assert out.type == "cleaning"
        assert out.cat_weight_g == -200

    def test_paranormal_not_emitted(self):
        # |end - start| = 5g, below event_delta of 10g → D15 drop
        df = _frame([500.0, 510.0, 505.0, 502.0, 498.0, 505.0])
        out = classify_segment(df, plateau_fn=self._plateau_const(510.0), min_event_duration_s=1.0)
        assert out is None


class TestIdentify:
    def test_single_match(self):
        profiles = [
            CatProfile("cat_a", 3000, 500),
            CatProfile("cat_b", 7000, 500),
        ]
        name, dist = identify_cat(3200, profiles)
        assert name == "cat_a"
        assert dist == 200

    def test_ambiguous_match_is_unattributed(self):
        profiles = [
            CatProfile("cat_a", 3000, 1000),
            CatProfile("cat_b", 3500, 1000),
        ]
        # 3200g falls inside both windows — crediting the first-declared cat
        # would silently corrupt both cats' weight history.
        name, dist = identify_cat(3200, profiles)
        assert name is None
        assert dist is None

    def test_overlapping_profiles_still_match_outside_the_overlap(self):
        profiles = [
            CatProfile("cat_a", 3000, 1000),
            CatProfile("cat_b", 3500, 1000),
        ]
        # windows are [2000, 4000] and [2500, 4500]; 2200g is below the
        # overlap so it still resolves cleanly to cat_a
        name, dist = identify_cat(2200, profiles)
        assert name == "cat_a"
        assert dist == 800

    def test_window_edge_is_inclusive(self):
        profiles = [CatProfile("cat_a", 5000, 700)]
        assert identify_cat(5700, profiles) == ("cat_a", 700)
        assert identify_cat(5701, profiles) == (None, None)

    def test_no_match(self):
        profiles = [CatProfile("cat_a", 3000, 500)]
        name, dist = identify_cat(5000, profiles)
        assert name is None
        assert dist is None

    def test_empty_profiles(self):
        name, dist = identify_cat(3000, [])
        assert name is None
        assert dist is None
