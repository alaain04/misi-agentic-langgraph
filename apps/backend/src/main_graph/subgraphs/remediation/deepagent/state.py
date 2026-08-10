from __future__ import annotations


def _merge_replace(current: dict, incoming: dict) -> dict:
    """Dict-keyed merge where the incoming write wins per key. Used for
    per-target accumulation so a retry round's fresh outcome for a target
    replaces its earlier attempt instead of appending a duplicate, and so
    two parallel group agents writing different target keys in the same
    superstep merge cleanly with no ordering requirement between them."""
    return {**current, **incoming}
