"use client";
import { useCallback } from "react";
import { usePathname } from "next/navigation";
import { ApiError } from "@/lib/api";
import { useNotifications } from "@/components/notification-provider";

/**
 * Returns a stable `handleApiError` function that converts ApiErrors into
 * user-visible notifications. Use this in components that call API functions
 * manually (not via SWR) so error feedback is consistent across the app.
 *
 * Returns false so callers can write: `catch (e) { if (handleApiError(e)) return; }`
 */
export function useApiErrorHandler(): (error: unknown) => boolean {
  const { notify } = useNotifications();
  const pathname = usePathname();

  return useCallback(
    (error: unknown): boolean => {
      if (!(error instanceof ApiError)) return false;

      if (error.status === 402) {
        notify({
          kind: "error",
          title: "Insufficient credits",
          message: error.detail ?? "Your organisation does not have enough credits for this action.",
          duration: 0,
          action: {
            label: "Top up credits",
            href: pathname?.replace(/\/[^/]+$/, "/billing") ?? "/billing",
          },
        });
        return true;
      }

      if (error.status === 429) {
        const retryMin = error.retryAfter ? Math.ceil(error.retryAfter / 60) : null;
        notify({
          kind: "warning",
          title: "Too many requests",
          message:
            error.detail ??
            (retryMin
              ? `Please wait ${retryMin} minute${retryMin > 1 ? "s" : ""} before trying again.`
              : "You've hit the rate limit. Please slow down."),
          duration: 8000,
        });
        return true;
      }

      if (error.status === 403) {
        notify({
          kind: "warning",
          title: "Access restricted",
          message: error.detail ?? "This feature is not available on your current plan.",
          duration: 8000,
          action: {
            label: "View plans",
            href: pathname?.replace(/\/[^/]+$/, "/pricing") ?? "/pricing",
          },
        });
        return true;
      }

      return false;
    },
    [notify, pathname],
  );
}
