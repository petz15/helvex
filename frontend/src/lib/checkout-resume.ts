export type CheckoutIntent =
  | {
      kind: "subscription";
      sourcePath: string;
      tier: string;
      billing_cycle: "monthly" | "yearly";
      success_path: string;
      cancel_path: string;
    }
  | {
      kind: "topup";
      sourcePath: string;
      credits: number;
      success_path: string;
      cancel_path: string;
    };

const STORAGE_KEY = "helvex_pending_checkout_intent";

export function saveCheckoutIntent(intent: CheckoutIntent): void {
  if (typeof window === "undefined") return;
  window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(intent));
}

export function readCheckoutIntent(): CheckoutIntent | null {
  if (typeof window === "undefined") return null;
  const raw = window.sessionStorage.getItem(STORAGE_KEY);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as CheckoutIntent;
    if (!parsed || typeof parsed !== "object" || !("kind" in parsed)) return null;
    return parsed;
  } catch {
    return null;
  }
}

export function clearCheckoutIntent(): void {
  if (typeof window === "undefined") return;
  window.sessionStorage.removeItem(STORAGE_KEY);
}

export function buildAddressReturnUrl(sourcePath: string): string {
  const separator = sourcePath.includes("?") ? "&" : "?";
  const resumePath = `${sourcePath}${separator}resume_checkout=1`;
  return `/app/addresses?return_to=${encodeURIComponent(resumePath)}`;
}
