"""Match a measured cat weight to a known cat profile."""

from petascale.config import CatProfile


def identify_cat(
    cat_weight_g: int,
    profiles: list[CatProfile],
) -> tuple[str | None, int | None]:
    """Return (name, distance_g) for the first profile within slop, else (None, None).

    Iterates profiles in declared order — first match wins (D13). Caller is
    expected to keep `slop_g` tight enough that profiles don't overlap.
    """
    for p in profiles:
        d = abs(cat_weight_g - p.weight_g)
        if d <= p.slop_g:
            return p.name, d
    return None, None
