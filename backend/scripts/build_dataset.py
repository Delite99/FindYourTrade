"""Build the product dataset used for integrity checks.

Each product is anchored to a Wikipedia article, so the photo that comes back is
*labelled*: the picture of the "Toyota Hilux" page is a Hilux. That label is what
lets the platform catch a photo posted under the wrong name.

For every product we store:
  - the product metadata the catalogue needs (title, brand, model, category, price)
  - the photo file, its source URL and a 64-bit dHash fingerprint

The fingerprint is a perceptual hash: the same picture re-encoded, resized or
lightly cropped lands within a few bits, so a re-post can be recognised even when
the file bytes differ.

Run:  python scripts/build_dataset.py [--refresh]
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import time

import cv2
import httpx
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "..", "app", "dataset")
IMG_DIR = os.path.join(OUT_DIR, "images")
JSON_PATH = os.path.join(OUT_DIR, "products.json")

SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/{}"
HEADERS = {"User-Agent": "FindYourTrade-dataset/1.0 (demo; contact: dev@findyourtrade.local)"}

# ── The catalogue ────────────────────────────────────────────────────────────
# wiki  = Wikipedia article that supplies the photo and its ground-truth label
# brand / model = what the listing is allowed to claim
# attr  = extra structured facts a buyer would filter on
PRODUCTS = [
    # ── Vehicles ────────────────────────────────────────────────────────────
    dict(id="p-v01", wiki="Toyota_Hilux", title="Toyota Hilux 2016 Double Cab", brand="Toyota", model="Hilux",
         vertical="vehicles", category="vehicle", unit="vehicle", price_mwk=38500000,
         attr={"year": 2016, "body": "pickup", "fuel": "diesel"}),
    dict(id="p-v02", wiki="Nissan_Navara", title="Nissan Navara 2017 Double Cab", brand="Nissan", model="Navara",
         vertical="vehicles", category="vehicle", unit="vehicle", price_mwk=35200000,
         attr={"year": 2017, "body": "pickup", "fuel": "diesel"}),
    dict(id="p-v03", wiki="Toyota_HiAce", title="Toyota Hiace 2013 Minibus", brand="Toyota", model="Hiace",
         vertical="vehicles", category="vehicle", unit="vehicle", price_mwk=16500000,
         attr={"year": 2013, "body": "minibus", "fuel": "diesel"}),
    dict(id="p-v04", wiki="Tesla_Model_Y", title="Tesla Model Y 2023", brand="Tesla", model="Model Y",
         vertical="vehicles", category="vehicle", unit="vehicle", price_mwk=42000000,
         attr={"year": 2023, "body": "suv", "fuel": "electric"}),
    dict(id="p-v05", wiki="Toyota_Corolla", title="Toyota Corolla 2018", brand="Toyota", model="Corolla",
         vertical="vehicles", category="vehicle", unit="vehicle", price_mwk=18500000,
         attr={"year": 2018, "body": "sedan", "fuel": "petrol"}),
    dict(id="p-v06", wiki="Honda_Fit", title="Honda Fit 2015", brand="Honda", model="Fit",
         vertical="vehicles", category="vehicle", unit="vehicle", price_mwk=9800000,
         attr={"year": 2015, "body": "hatchback", "fuel": "petrol"}),
    dict(id="p-v07", wiki="Mazda_Bongo", title="Mazda Bongo 2012 Van", brand="Mazda", model="Bongo",
         vertical="vehicles", category="vehicle", unit="vehicle", price_mwk=7200000,
         attr={"year": 2012, "body": "van", "fuel": "petrol"}),
    dict(id="p-v08", wiki="Toyota_Land_Cruiser", title="Toyota Land Cruiser 2019", brand="Toyota", model="Land Cruiser",
         vertical="vehicles", category="vehicle", unit="vehicle", price_mwk=68000000,
         attr={"year": 2019, "body": "suv", "fuel": "diesel"}),
    dict(id="p-v09", wiki="Mitsubishi_Pajero", title="Mitsubishi Pajero 2014", brand="Mitsubishi", model="Pajero",
         vertical="vehicles", category="vehicle", unit="vehicle", price_mwk=21000000,
         attr={"year": 2014, "body": "suv", "fuel": "diesel"}),
    dict(id="p-v10", wiki="Ford_Ranger", title="Ford Ranger 2018", brand="Ford", model="Ranger",
         vertical="vehicles", category="vehicle", unit="vehicle", price_mwk=33000000,
         attr={"year": 2018, "body": "pickup", "fuel": "diesel"}),

    # ── Farming ─────────────────────────────────────────────────────────────
    dict(id="p-f01", wiki="Fertilizer", title="NPK Fertilizer 50kg", brand="Chitowe Local Blend", model="NPK 23-21-0",
         vertical="farming", category="fertilizer", unit="bag", price_mwk=85000,
         attr={"weight_kg": 50, "nutrients": "23-21-0"}),
    dict(id="p-f03", wiki="Maize", title="White Maize Grain 50kg", brand="Local white maize", model="SC 627",
         vertical="farming", category="maize", unit="bag", price_mwk=55000,
         attr={"weight_kg": 50, "grade": "food"}),
    dict(id="p-f04", wiki="Soybean", title="Soya Beans 50kg", brand="Local soya", model="TGX",
         vertical="farming", category="grain", unit="bag", price_mwk=72000,
         attr={"weight_kg": 50, "grade": "oilseed"}),
    dict(id="p-f05", wiki="Peanut", title="Groundnuts 50kg (CG7)", brand="CG7", model="CG7",
         vertical="farming", category="grain", unit="bag", price_mwk=88000,
         attr={"weight_kg": 50, "grade": "confectionery"}),
    dict(id="p-f06", wiki="Sunflower", title="Sunflower Seed 20kg", brand="Local hybrid", model="PAN 7049",
         vertical="farming", category="seed", unit="bag", price_mwk=52000,
         attr={"weight_kg": 20, "use": "oil"}),
    dict(id="p-f07", wiki="Pesticide_application", title="Backpack Pesticide Sprayer 16L", brand="AgroShield", model="16L Knapsack",
         vertical="farming", category="equipment", unit="unit", price_mwk=62000,
         attr={"tank_l": 16, "type": "knapsack sprayer"}),
    dict(id="p-f08", wiki="Tractor", title="Farm Tractor 45HP", brand="Massey Ferguson", model="MF 240",
         vertical="farming", category="equipment", unit="unit", price_mwk=9800000,
         attr={"power_hp": 45, "drive": "2WD"}),
    dict(id="p-f09", wiki="Irrigation_sprinkler", title="Irrigation Sprinkler Set", brand="Local assembly", model="1-acre kit",
         vertical="farming", category="equipment", unit="kit", price_mwk=480000,
         attr={"coverage": "1 acre", "type": "sprinkler"}),
    dict(id="p-f10", wiki="Chicken", title="Live Broiler Chicken", brand="Farm fresh", model="Broiler",
         vertical="farming", category="livestock", unit="bird", price_mwk=9500,
         attr={"weight_kg": 2, "type": "poultry"}),

    # ── Restaurant / food ───────────────────────────────────────────────────
    dict(id="p-r01", wiki="Nsima", title="Nsima Plate with Relish", brand="Local kitchen", model="Nsima",
         vertical="food", category="prepared_meal", unit="plate", price_mwk=3500,
         attr={"serves": 1, "hot": True}),
    dict(id="p-r02", wiki="Mandazi", title="Mandazi (6 pieces)", brand="Local kitchen", model="Mandazi",
         vertical="food", category="snack", unit="pack", price_mwk=1500,
         attr={"pieces": 6, "sweet": True}),
    dict(id="p-r03", wiki="Tilapia", title="Fresh Tilapia 1kg", brand="Lake Malawi", model="Tilapia",
         vertical="food", category="seafood", unit="kg", price_mwk=6500,
         attr={"weight_kg": 1, "fresh": True}),
    dict(id="p-r05", wiki="Rice", title="White Rice 25kg", brand="Kilombero", model="Long grain",
         vertical="food", category="grain", unit="bag", price_mwk=42000,
         attr={"weight_kg": 25, "grade": "long grain"}),
    dict(id="p-r06", wiki="Common_bean", title="Beans 25kg (Nsinjiro)", brand="Nsinjiro", model="Sugar bean",
         vertical="food", category="grain", unit="bag", price_mwk=38000,
         attr={"weight_kg": 25, "type": "sugar bean"}),
    dict(id="p-r07", wiki="Roast_chicken", title="Roast Chicken (whole)", brand="Local kitchen", model="Roast",
         vertical="food", category="prepared_meal", unit="bird", price_mwk=9000,
         attr={"weight_kg": 1.2, "hot": True}),
    dict(id="p-r08", wiki="French_fries", title="Chips (large portion)", brand="Local kitchen", model="Chips",
         vertical="food", category="prepared_meal", unit="portion", price_mwk=2500,
         attr={"size": "large", "hot": True}),
    dict(id="p-r09", wiki="Tea", title="Tea Leaves 500g", brand="Chombe", model="Black tea",
         vertical="food", category="beverage", unit="pack", price_mwk=4500,
         attr={"weight_g": 500, "type": "black"}),
    dict(id="p-r10", wiki="Cassava", title="Cassava Flour 10kg", brand="Local mill", model="Cassava",
         vertical="food", category="staple", unit="bag", price_mwk=12000,
         attr={"weight_kg": 10, "type": "flour"}),

    # ── Parts & equipment ───────────────────────────────────────────────────
    dict(id="p-p01", wiki="Automotive_battery", title="Car Battery 12V 70Ah", brand="Willard", model="12V 70Ah",
         vertical="parts", category="parts", unit="piece", price_mwk=145000,
         attr={"voltage": 12, "capacity_ah": 70}),
    dict(id="p-p02", wiki="Tire", title="Hilux Tyre 265/65R17", brand="Bridgestone", model="Dueler",
         vertical="parts", category="parts", unit="piece", price_mwk=185000,
         attr={"size": "265/65R17", "condition": "new"}),
    dict(id="p-p04", wiki="Solar_panel", title="Solar Panel 300W", brand="Jinko", model="300W mono",
         vertical="parts", category="equipment", unit="panel", price_mwk=210000,
         attr={"watts": 300, "type": "monocrystalline"}),
    dict(id="p-p05", wiki="Engine-generator", title="Diesel Generator 5kVA", brand="Kipor", model="5kVA",
         vertical="parts", category="equipment", unit="unit", price_mwk=1250000,
         attr={"kva": 5, "fuel": "diesel"}),
    dict(id="p-p06", wiki="Water_pump", title="Water Pump 2 inch", brand="Honda", model="WB20",
         vertical="parts", category="equipment", unit="unit", price_mwk=380000,
         attr={"size_in": 2, "fuel": "petrol"}),
    dict(id="p-p07", wiki="Wheelbarrow", title="Steel Wheelbarrow", brand="Local forge", model="Heavy duty",
         vertical="parts", category="equipment", unit="unit", price_mwk=65000,
         attr={"capacity_kg": 100, "material": "steel"}),
    dict(id="p-p08", wiki="Bicycle", title="Bicycle 26 inch", brand="Phoenix", model="26 inch",
         vertical="parts", category="equipment", unit="unit", price_mwk=135000,
         attr={"wheel_in": 26, "gears": 1}),

    # ── Building & trades ───────────────────────────────────────────────────
    dict(id="p-b01", wiki="Cement", title="Cement 50kg (Portland)", brand="Lafarge", model="PC 32.5",
         vertical="building", category="building", unit="bag", price_mwk=18000,
         attr={"weight_kg": 50, "grade": "32.5"}),
    dict(id="p-b02", wiki="Corrugated_galvanised_iron", title="Iron Sheet 3m (IBR)", brand="Batala", model="IBR 0.4mm",
         vertical="building", category="building", unit="sheet", price_mwk=22000,
         attr={"length_m": 3, "thickness_mm": 0.4}),
    dict(id="p-b03", wiki="Brick", title="Burnt Bricks (per 100)", brand="Local kiln", model="Clay",
         vertical="building", category="building", unit="100 pieces", price_mwk=45000,
         attr={"qty": 100, "material": "clay"}),
    dict(id="p-b04", wiki="Paint", title="Exterior Paint 20L", brand="Crown", model="Weatherguard",
         vertical="building", category="building", unit="bucket", price_mwk=95000,
         attr={"volume_l": 20, "type": "exterior"}),
    dict(id="p-b05", wiki="Lumber", title="Timber 2x4 (4m)", brand="Local sawmill", model="Pine 2x4",
         vertical="building", category="building", unit="piece", price_mwk=8500,
         attr={"length_m": 4, "species": "pine"}),
    dict(id="p-b06", wiki="Plumbing", title="PVC Pipe 50mm (6m)", brand="Local supplier", model="PVC 50",
         vertical="building", category="building", unit="length", price_mwk=12000,
         attr={"diameter_mm": 50, "length_m": 6}),
    dict(id="p-b07", wiki="Welding", title="Welding Machine 200A", brand="Lincoln", model="200A inverter",
         vertical="building", category="equipment", unit="unit", price_mwk=420000,
         attr={"amps": 200, "type": "inverter"}),
    dict(id="p-b08", wiki="Nail_(fastener)", title="Roofing Nails 5kg", brand="Local supplier", model="Umbrella head",
         vertical="building", category="building", unit="pack", price_mwk=9500,
         attr={"weight_kg": 5, "type": "roofing"}),

    # ── Electronics & household ─────────────────────────────────────────────
    dict(id="p-e01", wiki="Smartphone", title="Smartphone 128GB", brand="Samsung", model="Galaxy A15",
         vertical="electronics", category="electronics", unit="unit", price_mwk=285000,
         attr={"storage_gb": 128, "network": "4G"}),
    dict(id="p-e02", wiki="Laptop", title="Laptop 14 inch 8GB", brand="HP", model="ProBook 440",
         vertical="electronics", category="electronics", unit="unit", price_mwk=850000,
         attr={"ram_gb": 8, "storage_gb": 256}),
    dict(id="p-e03", wiki="Television", title="Television 32 inch", brand="Sony", model="Bravia 32",
         vertical="electronics", category="electronics", unit="unit", price_mwk=320000,
         attr={"size_in": 32, "panel": "LED"}),
    dict(id="p-e04", wiki="Refrigerator", title="Refrigerator 200L", brand="Hisense", model="200L",
         vertical="electronics", category="appliance", unit="unit", price_mwk=680000,
         attr={"volume_l": 200, "stars": 2}),
    dict(id="p-e05", wiki="Ceiling_fan", title="Ceiling Fan 56 inch", brand="Local supplier", model="56 inch",
         vertical="electronics", category="appliance", unit="unit", price_mwk=85000,
         attr={"size_in": 56, "blades": 3}),
    dict(id="p-e06", wiki="Clothes_iron", title="Steam Iron 1200W", brand="Philips", model="HD1172",
         vertical="electronics", category="appliance", unit="unit", price_mwk=45000,
         attr={"watts": 1200, "type": "steam"}),
    dict(id="p-e07", wiki="Mattress", title="Foam Mattress 4ft", brand="Local foam", model="4ft",
         vertical="electronics", category="household", unit="unit", price_mwk=145000,
         attr={"width_ft": 4, "thickness_in": 6}),
    dict(id="p-e08", wiki="Cookware_and_bakeware", title="Cooking Pot 20L", brand="Local supplier", model="Aluminium 20L",
         vertical="electronics", category="household", unit="unit", price_mwk=42000,
         attr={"volume_l": 20, "material": "aluminium"}),
]


def dhash(image_bytes: bytes, size: int = 8) -> str:
    """64-bit difference hash, returned as 16 hex chars."""
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError("could not decode image")
    img = cv2.resize(img, (size + 1, size), interpolation=cv2.INTER_AREA)
    diff = img[:, 1:] > img[:, :-1]
    bits = 0
    for bit in diff.flatten():
        bits = (bits << 1) | int(bit)
    return f"{bits:016x}"


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def original_url(thumb_url: str) -> str:
    """Turn a /thumb/.../330px-FILE.jpg URL into the full-size original."""
    u = thumb_url.split("?")[0]
    # The summary API serves thumbs from thumb.wikimedia.org; originals live on
    # upload.wikimedia.org, so swap the host as well as dropping the size segment.
    u = u.replace("//thumb.wikimedia.org/", "//upload.wikimedia.org/")
    if "/thumb/" not in u:
        return u
    head, tail = u.split("/thumb/", 1)
    parts = tail.split("/")
    return head + "/" + "/".join(parts[:-1])


def normalise(image_bytes: bytes, max_width: int = 900) -> tuple[bytes, int, int]:
    """Downscale to a web-friendly width and re-encode, so the repo stays small."""
    img = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("could not decode image")
    h, w = img.shape[:2]
    if w > max_width:
        img = cv2.resize(img, (max_width, round(h * max_width / w)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 82])
    if not ok:
        raise ValueError("could not encode image")
    return buf.tobytes(), img.shape[1], img.shape[0]


def fetch_one(client: httpx.Client, spec: dict) -> dict | None:
    r = client.get(SUMMARY_URL.format(spec["wiki"]))
    if r.status_code != 200:
        print(f"  ! no article for {spec['wiki']} ({r.status_code})")
        return None
    data = r.json()
    thumb = (data.get("thumbnail") or {}).get("source") or (data.get("originalimage") or {}).get("source")
    if not thumb:
        print(f"  ! no photo on {spec['wiki']}")
        return None

    # Wikimedia only pre-renders a few widths, so grab the original and resize here.
    url = original_url(thumb)
    img = client.get(url, follow_redirects=True, timeout=90.0)
    if img.status_code != 200 or not img.content:
        print(f"  ! photo download failed for {spec['wiki']} ({img.status_code})")
        return None

    try:
        content, width, height = normalise(img.content)
    except Exception as e:
        print(f"  ! {spec['wiki']}: {e}")
        return None

    os.makedirs(IMG_DIR, exist_ok=True)
    filename = f"{spec['id']}.jpg"
    with open(os.path.join(IMG_DIR, filename), "wb") as fh:
        fh.write(content)

    return {
        **{k: spec[k] for k in ("id", "title", "brand", "model", "vertical", "category", "unit", "price_mwk", "attr")},
        "photo_file": filename,
        "photo_hash": dhash(content),
        "photo_source": data.get("content_urls", {}).get("desktop", {}).get("page", ""),
        "photo_credit": "Wikimedia Commons via Wikipedia",
        "photo_width": width,
        "photo_height": height,
        "photo_bytes": len(content),
        "photo_sha1": hashlib.sha1(content).hexdigest(),
        "label": data.get("title", spec["wiki"].replace("_", " ")),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="re-download everything")
    args = ap.parse_args()

    existing = {}
    if os.path.exists(JSON_PATH) and not args.refresh:
        with open(JSON_PATH, encoding="utf-8") as fh:
            existing = {p["id"]: p for p in json.load(fh)}

    out: list[dict] = []
    with httpx.Client(headers=HEADERS, timeout=30.0) as client:
        for i, spec in enumerate(PRODUCTS, 1):
            if spec["id"] in existing and os.path.exists(os.path.join(IMG_DIR, existing[spec["id"]]["photo_file"])):
                out.append(existing[spec["id"]])
                continue
            print(f"[{i}/{len(PRODUCTS)}] {spec['title']}  <-  {spec['wiki']}")
            try:
                row = fetch_one(client, spec)
            except Exception as e:
                print(f"  ! {e}")
                row = None
            if row:
                out.append(row)
            time.sleep(0.25)

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(JSON_PATH, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)

    # Duplicate check across the dataset itself: every photo must be distinct.
    hashes = [(p["id"], p["photo_hash"]) for p in out]
    collisions = []
    for i in range(len(hashes)):
        for j in range(i + 1, len(hashes)):
            if hamming(hashes[i][1], hashes[j][1]) <= 6:
                collisions.append((hashes[i][0], hashes[j][0], hamming(hashes[i][1], hashes[j][1])))
    print(f"\nwrote {len(out)} products to {JSON_PATH}")
    print(f"near-duplicate photos inside the dataset: {len(collisions)}")
    for c in collisions:
        print("  ", c)


if __name__ == "__main__":
    sys.exit(main())
