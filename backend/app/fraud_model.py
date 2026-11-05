"""Fraud-risk scorer in PyTorch: a small neural network that learns from the
platform's own verdicts.

Problem: the rule engine (vision, hashes, web facts) convicts with evidence,
but new posts arrive with no verdict yet. The scorer answers "how much does
this post look like the ones we already caught?" — a triage score in [0, 1]
used to order the integrity queue, never to convict on its own.

Model: logistic regression as torch.nn.Module (Linear + sigmoid), trained
with BCEWithLogitsLoss + L2, class-weighted for the fraud minority. Features
are text/metadata signals available before any vision call:

 1. brand_model_hit ..... model name in the claim contradicts web knowledge (0/1)
 2. has_photo ........... post carries any photo reference (0/1)
 3. photo_fingerprinted . photo resolves to a hash (0/1)
 4. photo_reused ........ photo hash already appears on another post (0/1)
 5. unverified .......... seller verification is pending (0/1)
 6. new_account .......... seller account < 90 days (0/1)
 7. low_trust ............ seller trust score < 60 (0/1)
 8. import_flag .......... origin is imported (0/1)
 9. price_z .............. |log(price / vertical median)|, capped — absurd
                          pricing is a classic fraud tell

Labels come from the ledger itself: fraud-severity verdicts are positives,
clean scanned posts are negatives, unknown_photo posts are excluded (no
teacher). Retraining is one call — `train()` rebuilds from current DB state
so every confirmed/disputed flag makes the next model smarter (the RL loop
feeds the network, not just the cache).

Persistence: state_dict + feature medians + min/max are saved to
app/fraud_model.pt; /api/integrity/model exposes metrics, and every score
carries the model version so the UI can say what judged it.
"""
from __future__ import annotations

import math
import os

MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fraud_model.pt")
MODEL_VERSION = "torch-lr-v1"
FEATURE_NAMES = [
    "brand_model_hit", "has_photo", "photo_fingerprinted", "photo_reused",
    "unverified", "new_account", "low_trust", "import_flag", "price_z",
]

try:
    import torch
    import torch.nn as nn
    _TORCH = True
except (ImportError, OSError):  # missing pkg OR missing native CRT DLLs
    # torch installs on first backend setup; rules work without it.
    # OSError = native DLLs unloadable (no VC++ redist on this box yet).
    torch, nn, _TORCH = None, None, False


if _TORCH:
    class FraudRiskModel(nn.Module):
        """Single-layer logistic regression: interpretable, auditable weights."""

        def __init__(self, n_features: int = len(FEATURE_NAMES)):
            super().__init__()
            self.linear = nn.Linear(n_features, 1)

        def forward(self, x):
            return self.linear(x).squeeze(-1)


def _price_medians(listings: list[dict]) -> dict[str, float]:
    med: dict[str, list[float]] = {}
    for l in listings:
        try:
            p = float(l.get("price_mwk") or 0)
        except (TypeError, ValueError):
            continue
        if p > 0:
            med.setdefault(l.get("category", "general"), []).append(p)
    out = {}
    for cat, ps in med.items():
        ps.sort()
        out[cat] = ps[len(ps) // 2]
    return out


def featurize(listing: dict, sellers: dict, hash_counts: dict,
              medians: dict, brand_facts: dict) -> list[float]:
    """Feature vector for one post. Pure function — no network, no DB."""
    from . import integrity  # lazy: keeps import graph acyclic
    claim = {"item": listing.get("item", ""), "brand": listing.get("brand", ""),
             "model": listing.get("model", "")}
    photo = listing.get("photo", "") or ""
    phash = listing.get("photo_hash", "") or ""
    seller = sellers.get(listing.get("seller_id", ""), {})
    try:
        price = float(listing.get("price_mwk") or 0)
    except (TypeError, ValueError):
        price = 0.0
    med = medians.get(listing.get("category", "general"), 0) or 0
    price_z = 0.0
    if price > 0 and med > 0:
        price_z = min(abs(math.log(price / med)), 5.0) / 5.0
    return [
        1.0 if integrity.web_knowledge_conflict(claim, brand_facts) else 0.0,
        1.0 if photo else 0.0,
        1.0 if phash else 0.0,
        1.0 if (phash and hash_counts.get(phash, 0) > 1) else 0.0,
        1.0 if (listing.get("verification_status") or "") != "verified" else 0.0,
        1.0 if int(seller.get("account_age_days") or 0) < 90 else 0.0,
        1.0 if int(seller.get("trust_score") or 0) < 60 else 0.0,
        1.0 if (listing.get("origin") or "") == "imported" else 0.0,
        price_z,
    ]


def build_dataset(listings: list[dict], sellers: list[dict],
                  brand_facts: dict) -> tuple | None:
    """Labelled matrix from current ledger state. None when nothing to learn."""
    if not _TORCH:
        return None
    smap = {s["id"]: s for s in sellers}
    counts: dict[str, int] = {}
    for l in listings:
        if l.get("photo_hash"):
            counts[l["photo_hash"]] = counts.get(l["photo_hash"], 0) + 1
    medians = _price_medians(listings)
    rows, labels, ids = [], [], []
    for l in listings:
        v = (l.get("fraud_verdict") or "")
        if v in ("", "unknown_photo"):
            continue  # no teacher signal — skip, don't guess
        fraud = any(f.get("severity") == "fraud" for f in (l.get("fraud_flags") or []))
        rows.append(featurize(l, smap, counts, medians, brand_facts))
        labels.append(1.0 if fraud else 0.0)
        ids.append(l["id"])
    if not rows or sum(labels) == 0 or sum(labels) == len(labels):
        return None  # need both classes to train
    X = torch.tensor(rows, dtype=torch.float32)
    y = torch.tensor(labels, dtype=torch.float32)
    return X, y, ids, medians, counts


def _no_grad(f):
    return torch.no_grad()(f) if _TORCH else f


@_no_grad
def _metrics(model, X, y) -> dict:
    probs = torch.sigmoid(model(X))
    pred = (probs >= 0.5).float()
    tp = int(((pred == 1) & (y == 1)).sum())
    fp = int(((pred == 1) & (y == 0)).sum())
    fn = int(((pred == 0) & (y == 1)).sum())
    tn = int(((pred == 0) & (y == 0)).sum())
    n = len(y)
    return {
        "n": n, "positives": int(y.sum()), "negatives": n - int(y.sum()),
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "accuracy": round((tp + tn) / max(1, n), 3),
        "precision": round(tp / max(1, tp + fp), 3),
        "recall": round(tp / max(1, tp + fn), 3),
    }


def train(listings: list[dict], sellers: list[dict], brand_facts: dict,
          epochs: int = 500, lr: float = 0.1, seed: int = 7) -> dict:
    """Fit the network on the current ledger. Returns metrics + weights."""
    if not _TORCH:
        return {"ok": False, "error": "torch is not installed"}
    data = build_dataset(listings, sellers, brand_facts)
    if data is None:
        return {"ok": False, "error": "not enough labelled posts (need fraud + clean)"}
    X, y, ids, medians, counts = data

    torch.manual_seed(seed)
    model = FraudRiskModel(X.shape[1])
    opt = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=1e-3)
    n_pos = int(y.sum().item())
    pos_weight = torch.tensor([(len(y) - n_pos) / max(1, n_pos)])
    loss_fn = torch.nn.BCEWithLogitsLoss(pos_weight=pos_weight)
    for _ in range(epochs):
        opt.zero_grad()
        loss = loss_fn(model(X), y)
        loss.backward()
        opt.step()

    metrics = _metrics(model, X, y)
    weights = {name: round(float(w), 4) for name, w in
               zip(FEATURE_NAMES, model.linear.weight.detach().flatten().tolist())}
    payload = {"version": MODEL_VERSION, "feature_names": FEATURE_NAMES,
               "state_dict": {k: v.cpu() for k, v in model.state_dict().items()},
               "bias": round(float(model.linear.bias.item()), 4),
               "weights": weights, "medians": medians,
               "metrics": {**metrics, "train_loss": round(float(loss.item()), 4)},
               "trained_on": ids}
    torch.save(payload, MODEL_PATH)
    return {"ok": True, "version": MODEL_VERSION, **payload["metrics"],
            "weights": weights, "model_path": MODEL_PATH}


def load_model() -> dict | None:
    """Saved weights + metadata, or None when never trained."""
    if not _TORCH or not os.path.exists(MODEL_PATH):
        return None
    try:
        payload = torch.load(MODEL_PATH, map_location="cpu", weights_only=True)
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


@_no_grad
def score(listing: dict, sellers: dict, hash_counts: dict,
          saved: dict, brand_facts: dict) -> dict:
    """Risk score for one post from the trained network. No network calls."""
    model = FraudRiskModel(len(FEATURE_NAMES))
    model.load_state_dict(saved["state_dict"])
    model.eval()
    feats = featurize(listing, sellers, hash_counts,
                      saved.get("medians", {}), brand_facts)
    x = torch.tensor([feats], dtype=torch.float32)
    prob = float(torch.sigmoid(model(x)).item())
    contribs = {name: round(float(w) * f, 4) for name, w, f
                in zip(FEATURE_NAMES, model.linear.weight.detach().flatten().tolist(), feats)}
    top = sorted(contribs.items(), key=lambda kv: -abs(kv[1]))[:3]
    return {"risk": round(prob, 3),
            "band": "high" if prob >= 0.7 else ("review" if prob >= 0.4 else "low"),
            "model_version": saved.get("version", MODEL_VERSION),
            "top_signals": [{"feature": k, "contribution": v} for k, v in top if v != 0],
            "features": dict(zip(FEATURE_NAMES, feats))}
