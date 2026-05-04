"""Warm-path detection pipeline (cat scale segment-based event detection).

See docs/architecture.md and the algorithm spec in .private/algo/algo.md
for the full design. Modules are pure functions over polars DataFrames;
`pipeline.run` composes them.
"""

from petascale.detect.classify import SegmentClassification, classify_segment
from petascale.detect.identify import identify_cat
from petascale.detect.pipeline import DetectorEvent, run

__all__ = [
    "DetectorEvent",
    "SegmentClassification",
    "classify_segment",
    "identify_cat",
    "run",
]
