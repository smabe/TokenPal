"""Human-readable durations for prompts and status text."""

from __future__ import annotations


def format_age(age_s: float) -> str:
    """Bucket *age_s* as 'just now', 'Nm ago', 'Nh ago', or 'Nd ago'."""
    if age_s < 60:
        return "just now"
    if age_s < 3600:
        return f"{int(age_s / 60)}m ago"
    if age_s < 86400:
        return f"{int(age_s / 3600)}h ago"
    return f"{int(age_s / 86400)}d ago"
