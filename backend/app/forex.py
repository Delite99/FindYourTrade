"""PRD section 7 — Forex Footprint: transparent rule-based 0–100 estimate.

Weights: origin 40% | stock status 30% | distance 15% | reusability 15%.
Always labelled an estimate in the interface and demo.
"""
from __future__ import annotations
from .models import ForexFactors, ForexFootprint


def score_footprint(origin: str, stock_status: str, distance_km: float, reusable: bool) -> ForexFootprint:
    origin_pts = 0 if origin == "local" else 40
    stock_pts = 0 if stock_status == "in_country" else 30
    if distance_km < 5:
        dist_pts, dist_label = 0, "under 5 km"
    elif distance_km <= 50:
        dist_pts, dist_label = 8, f"{distance_km:.0f} km away"
    else:
        dist_pts, dist_label = 15, f"over 50 km ({distance_km:.0f} km)"
    reuse_pts = 0 if reusable else 15
    total = min(100, origin_pts + stock_pts + dist_pts + reuse_pts)

    bits: list[str] = []
    bits.append("made/grown locally" if origin == "local" else "imported")
    bits.append("already in-country" if stock_status == "in_country" else "needs a new import")
    bits.append(dist_label)
    bits.append("reusable/repairable" if reusable else "new only")
    reason = f"Estimate {total}/100 — " + ", ".join(bits) + "."
    return ForexFootprint(
        score=total,
        reason=reason,
        factors=ForexFactors(
            origin=origin, stock_status=stock_status,
            distance_km=distance_km, reusability="reusable" if reusable else "new_only",
        ),
    )
