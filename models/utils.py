def _safe_float(val, default: float = 0.0) -> float:
    try:
        f = float(val)
        return default if (f != f) else f  # NaN check: NaN != NaN
    except (TypeError, ValueError):
        return default
