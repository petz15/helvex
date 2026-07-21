import { CreditCard, Wallet } from "lucide-react";
import type { LucideProps } from "lucide-react";
import type { PaymentMethod } from "@/lib/api";

/** Subset of PaymentMethod fields needed to render a saved payment method. */
type MethodLike = Pick<
  PaymentMethod,
  "method_type" | "display_text" | "brand" | "masked_number"
>;

/** Method types that get the generic wallet glyph instead of the card glyph. */
const WALLET_METHOD_TYPES = new Set([
  "twint",
  "paypal",
  "klarna",
  "alipay",
  "bank_transfer",
  "direct_debit",
]);

/**
 * Icon for a saved payment method. Cards get the card glyph; everything else
 * (TWINT, PayPal, Klarna, bank transfer, …) gets a generic wallet glyph, since
 * we don't ship per-brand logos. Renders the icon directly (rather than
 * resolving to a variable) so the lookup doesn't count as creating a
 * component during render.
 */
export function PaymentMethodIcon({
  methodType,
  ...props
}: { methodType: string | null | undefined } & LucideProps) {
  if (methodType && WALLET_METHOD_TYPES.has(methodType)) {
    return <Wallet {...props} />;
  }
  return <CreditCard {...props} />;
}

function cap(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1).toLowerCase();
}

/** Friendly display names per method type. Brand names, so not translated. */
const METHOD_NAMES: Record<string, string> = {
  card: "Card",
  twint: "TWINT",
  paypal: "PayPal",
  klarna: "Klarna",
  alipay: "Alipay",
  bank_transfer: "Bank transfer",
  direct_debit: "Direct debit",
};

/** A DisplayText we can show to a user — a plain string, not a stringified object. */
function cleanDisplayText(dt: string | null | undefined): string | null {
  const s = (dt || "").trim();
  if (!s || s.includes("{") || s.includes("}") || s.includes("':")) return null;
  return s;
}

/**
 * Human label for a saved payment method.
 * - Cards → "Visa •••• 4242" (clean brand + last4).
 * - Non-card → a clean Worldline DisplayText if present ("PayPal user@ex.com"),
 *   otherwise a friendly method name ("TWINT"). Provider junk like
 *   "{'paymentmethod': 'twint'}" is never shown.
 * - Nothing usable → the caller-provided generic fallback.
 */
export function paymentMethodLabel(m: MethodLike, genericFallback: string): string {
  const last4 = m.masked_number ? m.masked_number.slice(-4) : null;
  const brand = m.brand ? cap(m.brand) : null;
  const cardLabel = [brand, last4 ? `•••• ${last4}` : null].filter(Boolean).join(" ");
  const isCard = m.method_type === "card" || (m.method_type == null && !!cardLabel);
  if (isCard && cardLabel) return cardLabel;

  const dt = cleanDisplayText(m.display_text);
  if (dt) return dt;
  if (m.method_type && METHOD_NAMES[m.method_type]) return METHOD_NAMES[m.method_type];
  if (cardLabel) return cardLabel;
  return genericFallback;
}
