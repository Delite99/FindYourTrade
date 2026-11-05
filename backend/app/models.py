"""Shared Pydantic models for FindYourTrade API."""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field


class ParsedRequest(BaseModel):
    item_type: str = Field(description="Normalized item, e.g. fertilizer, maize, vehicle")
    quantity: int = 1
    budget_mwk: float | None = None
    location: str = "Lilongwe"
    payment_preference: str = "any"
    raw_text: str = ""
    category: str = "general"


class ForexFactors(BaseModel):
    origin: str  # 'local' | 'imported'
    stock_status: str  # 'in_country' | 'new_import'
    distance_km: float
    reusability: str  # 'reusable' | 'new_only'


class ForexFootprint(BaseModel):
    score: int = Field(ge=0, le=100)
    reason: str
    factors: ForexFactors
    is_estimate: bool = True


class SellerOut(BaseModel):
    id: str
    name: str
    location: str
    distance_km: float
    account_age_days: int
    trust_score: int
    phone: str = ""
    business_category: str = ""
    email: str = ""


class ListingOut(BaseModel):
    id: str
    seller: SellerOut
    item: str
    category: str
    brand: str = ""
    model: str = ""
    price_mwk: float = 0
    quantity_available: int = 0
    unit: str = "bag"
    origin: str = "local"
    stock_status: str = "in_country"
    reusable: bool = False
    registration_status: str = ""
    odometer_km: int | None = None
    photo: str = ""
    video_url: str = ""
    photo_hash: str = ""
    photo_product_id: str = ""
    fraud_verdict: str = ""
    fraud_flags: list[dict] = []
    trust: int = 0
    trust_label: str = ""
    forex: ForexFootprint | None = None
    rank_explanation: str = ""


class MatchResponse(BaseModel):
    parsed: ParsedRequest
    results: list[ListingOut]
    note: str = ""
    trace: list[dict] = []


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    listing_id: str | None = None
    seller_id: str | None = None


class ChatResponse(BaseModel):
    answer: str
    reasoning: list[str]
    risk_flags: list[str] = []
    trace: list[dict] = []


class ConversationalResponse(BaseModel):
    answer: str
    spoken: str = ""
    results: list[ListingOut] = []
    parsed: ParsedRequest | None = None
    listing_id: str | None = None
    risk_flags: list[str] = []
    trace: list[dict] = []
    # True only when the assistant is recommending listings. The UI navigates
    # to the Results tab on True; clarification questions and refusals stay
    # in the chat so the user is never yanked to unrelated goods.
    show_results: bool = False


class CodeWordResponse(BaseModel):
    listing_id: str
    code_word: str
    valid_minutes: int = 30


class VerifyResult(BaseModel):
    listing_id: str
    verdict: Literal["Match", "Partial match", "Mismatch", "Mock-Match"]
    reasons: list[str]
    counted_items: int | None = None
    code_word_heard: bool = False
    transcript: str = ""
    is_mock: bool = True
    frames_analyzed: int = 0
    trace: list[dict] = []


class VehicleCompareItem(BaseModel):
    listing_id: str


class VehicleCompareResponse(BaseModel):
    items: list[ListingOut]
    comparison: str
    trace: list[dict] = []
    ownership_reminder: str = (
        "FindYourTrade verification shows the vehicle was present at recording time. "
        "It does not prove legal ownership — confirm with the official authority before paying."
    )


class DebtEdge(BaseModel):
    debtor: str
    creditor: str
    amount: float


class NettingRequest(BaseModel):
    debts: list[DebtEdge]


class NettingResponse(BaseModel):
    original_cash_movement: float
    netted_cash_movement: float
    cancellations: list[dict]
    explanation: str
    mermaid: str = ""
    trace: list[dict] = []


class CommentOut(BaseModel):
    id: str
    user_id: str = ""
    user_name: str = ""
    body: str = ""
    parent_id: str | None = None
    created_at: str = ""


class SocialOut(BaseModel):
    views: int = 0
    likes: int = 0
    liked: bool = False
    comments: list[CommentOut] = []


class RecommendationItem(BaseModel):
    listing: ListingOut
    reco_score: float
    why: list[str] = []
    legitimacy_score: float = 0.0
    social: SocialOut | None = None


class RecommendationResponse(BaseModel):
    user_id: str
    interests: list[str] = []
    recommendations: list[RecommendationItem]
    used_fallback: bool = False
    note: str = ""
