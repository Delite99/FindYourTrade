"""Which local dataset photo each seeded listing shows.

Two rules, both enforced by the integrity engine:

1. **One photo, one product.** Every listing points at a distinct dataset image.
   The only exception is the deliberate fraud pair (l39/l40), which exists to
   demonstrate the shared-photo check.
2. **The photo must actually show the listing.** We do not take a Ford Ranger
   photo and hang it on a Hilux listing. If the catalogue has no honest match
   for a listing, that listing keeps its existing photo rather than borrowing an
   unrelated product shot.

Listing ids that are absent here are intentionally unmapped.
"""

# listing id -> (product id, image filename in app/dataset/images)
LOCAL_PHOTO: dict[str, tuple[str, str]] = {
    # ── Vehicles ──────────────────────────────────────────────────────────────
    "l10": ("p-v01", "p-v01.jpg"),   # Toyota Hilux 2016 Double Cab
    "l21": ("p-v07", "p-v07.jpg"),   # Mazda Bongo van
    "l30": ("p-v03", "p-v03.jpg"),   # Toyota Hiace minibus
    # Fraud demos (already quarantined): the photo deliberately does not match
    # the claim, which is exactly what the vision check is meant to catch.
    "l38": ("p-v05", "p-v05.jpg"),   # post claims Tesla, photo is a Corolla
    "l41": ("p-v06", "p-v06.jpg"),   # post claims "Honda Hilux", photo is a Fit

    # ── Farming inputs ────────────────────────────────────────────────────────
    "l01": ("p-f01", "p-f01.jpg"),   # NPK Fertilizer 50kg
    "l07": ("p-f03", "p-f03.jpg"),   # White Maize Grain 50kg
    "l34": ("p-f04", "p-f04.jpg"),   # Soya Beans 50kg
    "l35": ("p-f05", "p-f05.jpg"),   # Groundnuts 50kg (CG7)
    "l36": ("p-f06", "p-f06.jpg"),   # Sunflower Seed 20kg
    "l37": ("p-r06", "p-r06.jpg"),   # Beans 25kg (Nsinjiro)

    # ── Equipment ─────────────────────────────────────────────────────────────
    "l16": ("p-f09", "p-f09.jpg"),   # 1-acre irrigation kit
    "l28": ("p-f07", "p-f07.jpg"),   # knapsack sprayer 16L
    # l26 (treadle pump) is deliberately NOT mapped: the catalogue only has a
    # motorised water pump, and live vision correctly flagged the swap.

    # ── Parts ─────────────────────────────────────────────────────────────────
    "l29": ("p-p02", "p-p02.jpg"),   # Hilux tyres
}


def local_photo(listing_id: str) -> tuple[str, str] | None:
    """(product_id, photo_url) for a listing, or None when unmapped."""
    hit = LOCAL_PHOTO.get(listing_id)
    if not hit:
        return None
    product_id, filename = hit
    return product_id, f"/dataset/images/{filename}"
