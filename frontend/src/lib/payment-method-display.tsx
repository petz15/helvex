import { CreditCard, Wallet } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { PaymentMethod } from "@/lib/api";

/** Subset of PaymentMethod fields needed to render a saved payment method. */
type MethodLike = Pick<
  PaymentMethod,
  "method_type" | "display_text" | "brand" | "masked_number"
>;

/**
 * Icon for a saved payment method. Cards get the card glyph; everything else
 * (TWINT, PayPal, Klarna, bank transfer, …) gets a generic wallet glyph, since
 * we don't ship per-brand logos.
 */
export function paymentMethodIcon(methodType: string | null | undefined): LucideIcon {
  switch (methodType) {
    case "twint":
    case "paypal":
    case "klarna":
    case "alipay":
    case "bank_transfer":
    case "direct_debit":
      return Wallet;
    case "card":
    default:
      return CreditCard;
  }
}

function cap(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1).toLowerCase();
}

/**
 * Human label for a saved payment method.
 * - Cards → "Visa •••• 4242" (clean brand + last4).
 * - Non-card → Worldline's DisplayText ("TWINT", "PayPal (user@ex.com)"), which
 *   already names the method.
 * - Nothing usable → the caller-provided generic fallback (e.g. "Saved payment method").
 */
export function paymentMethodLabel(m: MethodLike, genericFallback: string): string {
  const last4 = m.masked_number ? m.masked_number.slice(-4) : null;
  const brand = m.brand ? cap(m.brand) : null;
  const isCard = m.method_type === "card" || (m.method_type == null && (last4 || brand));
  if (isCard && (last4 || brand)) {
    return [brand, last4 ? `•••• ${last4}` : null].filter(Boolean).join(" ");
  }
  const dt = (m.display_text || "").trim();
  if (dt) return dt;
  if (last4 || brand) return [brand, last4 ? `•••• ${last4}` : null].filter(Boolean).join(" ");
  return genericFallback;
}
