"""Shared types for the payments package."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


ProviderName = Literal["worldline"]


class PaymentConfigurationError(RuntimeError):
    """Raised when the requested payment provider is not configured."""


@dataclass(slots=True)
class CheckoutSession:
    provider: ProviderName
    checkout_url: str
    external_id: str | None = None
    order_reference: str | None = None


class PaymentProvider(Protocol):
    name: ProviderName

    def create_subscription_checkout(
        self,
        *,
        org_id: int,
        tier: str,
        billing_cycle: Literal["monthly", "yearly"],
        success_url: str,
        cancel_url: str,
        billing_address: dict[str, str] | None = None,
        save_payment_method: bool = False,
    ) -> CheckoutSession:
        ...

    def create_topup_checkout(
        self,
        *,
        org_id: int,
        credits: int,
        success_url: str,
        cancel_url: str,
        billing_address: dict[str, str] | None = None,
        save_payment_method: bool = False,
    ) -> CheckoutSession:
        ...
