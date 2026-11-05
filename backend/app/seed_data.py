"""Seeded marketplace users (sellers) with clean listings."""
from __future__ import annotations

SELLERS = [
    {"id": "s01", "name": "Chikondi Agro", "location": "Lilongwe", "distance_km": 3.2, "account_age_days": 620, "trust_score": 92, "phone": "+265 991 100 101"},
    {"id": "s02", "name": "Mlimi Pride Stores", "location": "Lilongwe", "distance_km": 4.8, "account_age_days": 410, "trust_score": 88, "phone": "+265 991 100 102"},
    {"id": "s03", "name": "Green Valley Agro", "location": "Dedza", "distance_km": 28.0, "account_age_days": 300, "trust_score": 81, "phone": "+265 991 100 103"},
    {"id": "s04", "name": "NewHope Imports", "location": "Lilongwe", "distance_km": 2.1, "account_age_days": 45, "trust_score": 52, "phone": "+265 991 100 104"},
    {"id": "s05", "name": "FreshStock Traders", "location": "Blantyre", "distance_km": 305.0, "account_age_days": 12, "trust_score": 35, "phone": "+265 991 100 105"},
    {"id": "s06", "name": "Mzuzu Motors", "location": "Mzuzu", "distance_km": 355.0, "account_age_days": 700, "trust_score": 90, "phone": "+265 991 100 106"},
    {"id": "s07", "name": "Capital Auto Yard", "location": "Lilongwe", "distance_km": 6.5, "account_age_days": 500, "trust_score": 86, "phone": "+265 991 100 107"},
    {"id": "s08", "name": "LakeSide Parts", "location": "Blantyre", "distance_km": 302.0, "account_age_days": 260, "trust_score": 74, "phone": "+265 991 100 108"},
    {"id": "s09", "name": "Thyolo Fresh Produce", "location": "Thyolo", "distance_km": 41.0, "account_age_days": 180, "trust_score": 79, "phone": "+265 991 100 109"},
    {"id": "s10", "name": "Zomba Farm Supply", "location": "Zomba", "distance_km": 62.0, "account_age_days": 540, "trust_score": 84, "phone": "+265 991 100 110"},
]

SELLER_BY_ID = {s["id"]: s for s in SELLERS}

# Clean marketplace: zero items or listings seeded, users preserved
LISTINGS = []
LISTING_PHOTO = {}
LISTING_BY_ID = {}

# Trade-circle debts for F7 demo: A→B 100, B→C 100, C→A 100 (plus a second ring)
DEMO_DEBTS = [
    {"debtor": "Chikondi Agro", "creditor": "Mlimi Pride Stores", "amount": 100},
    {"debtor": "Mlimi Pride Stores", "creditor": "Green Valley Agro", "amount": 100},
    {"debtor": "Green Valley Agro", "creditor": "Chikondi Agro", "amount": 100},
    {"debtor": "Chikondi Agro", "creditor": "Capital Auto Yard", "amount": 60},
    {"debtor": "Capital Auto Yard", "creditor": "LakeSide Parts", "amount": 60},
    {"debtor": "LakeSide Parts", "creditor": "Chikondi Agro", "amount": 60},
]
