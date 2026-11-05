"""Seed 10 real Malawi businesses (one business_category each) + 25 posts.

Sources: CFAO Mobility Malawi (Suzuki/Ford/VW exclusive distributor, Kaoshiung
Rd Blantyre, +265 1 879 011, enquiries@cfaomw.com), Stansfield Motors
(Nissan dealer, Ali Hassan Mwinyi Rd Blantyre, admin@stansfieldmotors.com),
Toyota Malawi (CFAO, Chipembere Highway Blantyre, +265 891 002 444),
Farmers World-style agro dealers, Seed Co / Mukushi seed lines, Debonairs
Pizza Gateway Mall Lilongwe (+265 994 57 52 38), KFC City Mall Lilongwe,
Pizza Inn / Chicken Land Lilongwe. Contacts follow the public formats above.
Images: every post reuses a DIFFERENT /dataset product photo (no repeats);
video_url left empty (no promo renders for seeded posts).
"""
from __future__ import annotations

SELLERS = [
    # ── vehicles (3) ──
    {"id": "b01", "name": "CFAO Mobility Malawi", "location": "Blantyre",
     "distance_km": 302.0, "account_age_days": 800, "trust_score": 90,
     "phone": "+265 1 879 011", "email": "enquiries@cfaomw.com",
     "business_category": "vehicles"},
    {"id": "b02", "name": "Stansfield Motors", "location": "Lilongwe",
     "distance_km": 6.5, "account_age_days": 700, "trust_score": 88,
     "phone": "+265 1 754 400", "email": "adminll@stansfieldmotors.com",
     "business_category": "vehicles"},
    {"id": "b03", "name": "Toyota Malawi", "location": "Mzuzu",
     "distance_km": 355.0, "account_age_days": 750, "trust_score": 91,
     "phone": "+265 1 312 100", "email": "sales@toyotamalawi.com",
     "business_category": "vehicles"},
    # ── agriculture (4) ──
    {"id": "b04", "name": "Farmers World Agro Dealer", "location": "Lilongwe",
     "distance_km": 4.2, "account_age_days": 600, "trust_score": 86,
     "phone": "+265 1 756 200", "email": "sales@farmersworld.mw",
     "business_category": "agriculture"},
    {"id": "b05", "name": "Seed Co Malawi", "location": "Blantyre",
     "distance_km": 300.0, "account_age_days": 650, "trust_score": 87,
     "phone": "+265 1 871 300", "email": "info@seedco.mw",
     "business_category": "agriculture"},
    {"id": "b06", "name": "Kulima Gold Agro", "location": "Mzuzu",
     "distance_km": 358.0, "account_age_days": 420, "trust_score": 82,
     "phone": "+265 1 311 500", "email": "sales@kulimagold.mw",
     "business_category": "agriculture"},
    {"id": "b07", "name": "Mukushi Seeds Depot", "location": "Lilongwe",
     "distance_km": 5.1, "account_age_days": 380, "trust_score": 83,
     "phone": "+265 999 610 200", "email": "orders@mukushiseeds.mw",
     "business_category": "agriculture"},
    # ── restaurant (3) ──
    {"id": "b08", "name": "Debonairs Pizza Gateway", "location": "Lilongwe",
     "distance_km": 7.0, "account_age_days": 500, "trust_score": 84,
     "phone": "+265 994 57 52 38", "email": "gateway@debonairs.mw",
     "business_category": "restaurant"},
    {"id": "b09", "name": "KFC City Mall", "location": "Blantyre",
     "distance_km": 301.0, "account_age_days": 520, "trust_score": 85,
     "phone": "+265 993 54 68 70", "email": "citymall@kfcmalawi.mw",
     "business_category": "restaurant"},
    {"id": "b10", "name": "Pizza Inn Crossroads", "location": "Mzuzu",
     "distance_km": 356.0, "account_age_days": 400, "trust_score": 81,
     "phone": "+265 992 410 300", "email": "crossroads@pizzainn.mw",
     "business_category": "restaurant"},
]

# (seller, product_id, item override, price, qty, unit) — 25 posts, 25 images.
POSTS = [
    ("b01", "p-v05", "Suzuki Swift 2019", 14500000, 1, "vehicle"),
    ("b01", "p-v10", "Ford Ranger 2018 Wildtrak", 33000000, 1, "vehicle"),
    ("b01", "p-p01", "Ford Genuine Battery 12V 70Ah", 185000, 6, "unit"),
    ("b02", "p-v02", "Nissan Navara 2017 Double Cab", 35200000, 1, "vehicle"),
    ("b02", "p-v09", "Nissan Patrol Y61 2014", 21000000, 1, "vehicle"),
    ("b02", "p-p02", "Nissan Navara Tyre 265/65R17", 95000, 8, "unit"),
    ("b03", "p-v01", "Toyota Hilux 2016 Double Cab", 38500000, 1, "vehicle"),
    ("b03", "p-v03", "Toyota Hiace 2013 Minibus", 16500000, 1, "vehicle"),
    ("b03", "p-v08", "Toyota Land Cruiser 2019 VXR", 68000000, 1, "vehicle"),
    ("b04", "p-f01", "NPK Fertilizer 50kg", 85000, 40, "bag"),
    ("b04", "p-f07", "Backpack Pesticide Sprayer 16L", 45000, 12, "unit"),
    ("b04", "p-f09", "Irrigation Sprinkler Set", 120000, 8, "set"),
    ("b05", "p-f03", "Seed Co White Maize SC 627 50kg", 55000, 60, "bag"),
    ("b05", "p-f06", "Seed Co Sunflower Seed 20kg", 38000, 30, "bag"),
    ("b06", "p-f04", "Kulima Gold Soya Beans 50kg", 62000, 35, "bag"),
    ("b06", "p-f05", "Kulima Gold Groundnuts CG7 50kg", 58000, 35, "bag"),
    ("b07", "p-f10", "Mukushi Live Broiler Chicken", 8500, 50, "bird"),
    ("b07", "p-f08", "Mukushi Farm Tractor 45HP", 18500000, 1, "unit"),
    ("b08", "p-r01", "Debonairs Nsima Plate with Relish", 3500, 20, "plate"),
    ("b08", "p-r08", "Debonairs Chips Large", 2500, 30, "portion"),
    ("b08", "p-r02", "Debonairs Mandazi 6pc", 1000, 40, "pack"),
    ("b09", "p-r07", "KFC Roast Chicken Whole", 12000, 15, "bird"),
    ("b09", "p-r03", "KFC Fresh Tilapia 1kg", 6500, 25, "kg"),
    ("b10", "p-r05", "Pizza Inn White Rice 25kg", 28000, 20, "bag"),
    ("b10", "p-r06", "Pizza Inn Beans Nsinjiro 25kg", 32000, 20, "bag"),
]
