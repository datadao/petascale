"""Unit tests for petascale.config."""

import pytest

from petascale.config import (
    CATS_LOCAL_PATH,
    CatProfile,
    cat_profile_overlaps,
    load_config,
)


class TestCatProfileWindow:
    def test_window_bounds(self):
        assert CatProfile("siri", 5000, 700).window_g == (4300, 5700)


class TestCatProfileOverlaps:
    def test_disjoint_windows_are_clean(self):
        cats = [
            CatProfile("siri", 5000, 700),   # [4300, 5700]
            CatProfile("dude", 7000, 1000),  # [6000, 8000]
        ]
        assert cat_profile_overlaps(cats) == []

    def test_overlap_is_reported_with_both_windows(self):
        cats = [
            CatProfile("siri", 5500, 700),   # [4800, 6200]
            CatProfile("dude", 7000, 1000),  # [6000, 8000]
        ]
        problems = cat_profile_overlaps(cats)
        assert len(problems) == 1
        assert "siri [4800, 6200]" in problems[0]
        assert "dude [6000, 8000]" in problems[0]

    def test_touching_windows_count_as_overlap(self):
        # identify_cat matches on <=, so a reading of exactly 6000 g would
        # land inside both windows.
        cats = [
            CatProfile("siri", 5000, 1000),  # [4000, 6000]
            CatProfile("dude", 7000, 1000),  # [6000, 8000]
        ]
        assert len(cat_profile_overlaps(cats)) == 1

    def test_one_gram_gap_is_clean(self):
        cats = [
            CatProfile("siri", 5000, 999),   # [4001, 5999]
            CatProfile("dude", 7000, 1000),  # [6000, 8000]
        ]
        assert cat_profile_overlaps(cats) == []

    def test_reports_every_offending_pair(self):
        cats = [
            CatProfile("a", 5000, 1000),
            CatProfile("b", 5200, 1000),
            CatProfile("c", 5400, 1000),
        ]
        assert len(cat_profile_overlaps(cats)) == 3

    def test_fewer_than_two_profiles(self):
        assert cat_profile_overlaps([]) == []
        assert cat_profile_overlaps([CatProfile("siri", 5000, 700)]) == []


@pytest.mark.skipif(
    not CATS_LOCAL_PATH.is_file(),
    reason="cats.local.toml is gitignored; only present on configured hosts",
)
class TestDeployedConfig:
    """Guards the real config on whatever host the suite runs on."""

    def test_no_overlapping_cat_profiles(self):
        cfg = load_config()
        assert cat_profile_overlaps(cfg.cats) == []
