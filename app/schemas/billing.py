from __future__ import annotations

from pydantic import BaseModel, Field


class BillingAddress(BaseModel):
    first_name: str
    last_name: str
    street: str
    number: str
    postal_code: str
    city: str
    country: str
    company_name: str | None = None


class BillingTierRead(BaseModel):
    id: int
    slug: str
    display_name: str
    description: str
    monthly_price_chf: float
    yearly_multiplier: float
    yearly_price_chf: float
    topup_bonus_rate: float
    sort_order: int
    is_active: bool
    is_public: bool
