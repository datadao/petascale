"""Algorithm registry for the detection pipeline.

Register a new algorithm with the @algo decorator:

    from petascale.detect.registry import algo

    @algo("v2")
    def run(df, cfg, cats, *, ts_col="ts", value_col="value"):
        ...

The warm daemon iterates `AppConfig.active_algos` and dispatches each
tick through the corresponding registered function.
"""

from collections.abc import Callable
from typing import Any

AlgoFn = Callable[..., Any]
_registry: dict[str, AlgoFn] = {}


def algo(name: str) -> Callable[[AlgoFn], AlgoFn]:
    """Decorator that registers a pipeline function under a versioned name."""
    def _wrap(fn: AlgoFn) -> AlgoFn:
        if name in _registry:
            raise ValueError(f"Algorithm {name!r} is already registered")
        _registry[name] = fn
        return fn
    return _wrap


def get(name: str) -> AlgoFn:
    """Return the pipeline function for *name*, raising KeyError if not found."""
    try:
        return _registry[name]
    except KeyError:
        avail = ", ".join(sorted(_registry)) or "(none)"
        raise KeyError(f"Unknown algorithm {name!r}. Available: {avail}") from None


def available() -> list[str]:
    """Return sorted list of registered algorithm names."""
    return sorted(_registry)
