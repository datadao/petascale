"""Match a measured cat weight to a known cat profile."""

import logging

from petascale.config import CatProfile

log = logging.getLogger(__name__)


def identify_cat(
    cat_weight_g: int,
    profiles: list[CatProfile],
) -> tuple[str | None, int | None]:
    """Return (name, distance_g) for the one profile within slop, else (None, None).

    A reading that lands inside *two* profiles' windows is left unattributed
    rather than credited to whichever cat happens to be declared first. An
    unattributed event keeps its weight and can be re-attributed later; a wrong
    attribution silently corrupts the weight trend and potty-gap history of
    both cats, and nothing downstream can tell it happened.

    Overlapping windows are a config bug — `cat_profile_overlaps` reports them
    at load time.
    """
    matches = [
        (p.name, abs(cat_weight_g - p.weight_g))
        for p in profiles
        if abs(cat_weight_g - p.weight_g) <= p.slop_g
    ]
    if len(matches) > 1:
        log.warning(
            "Ambiguous weight %d g matches %s — leaving unattributed",
            cat_weight_g, ", ".join(name for name, _ in matches),
        )
        return None, None
    if not matches:
        return None, None
    return matches[0]
