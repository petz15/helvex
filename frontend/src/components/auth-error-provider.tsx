"use client";
import { useCallback } from "react";
import { useRouter, usePathname } from "next/navigation";
import { SWRConfig } from "swr";
import { createFetcher, ApiError } from "@/lib/api";
import { useNotifications } from "@/components/notification-provider";

export function AuthErrorProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const { notify } = useNotifications();

  const onError = useCallback(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (error: any) => {
      if (!(error instanceof ApiError)) return;

      if (error.status === 401) {
        router.push("/login");
        return;
      }

      if (error.status === 402) {
        notify({
          kind: "error",
          title: "Insufficient credits",
          message: error.detail ?? "Your organisation does not have enough credits for this action.",
          duration: 0, // stay until dismissed
          action: {
            label: "Top up credits",
            href: pathname?.replace(/\/[^/]+$/, "/billing") ?? "/billing",
          },
        });
        return;
      }

      if (error.status === 429) {
        const retryMin = error.retryAfter ? Math.ceil(error.retryAfter / 60) : null;
        notify({
          kind: "warning",
          title: "Too many requests",
          message: error.detail ?? (retryMin ? `Please wait ${retryMin} minute${retryMin > 1 ? "s" : ""} before trying again.` : "You've hit the rate limit. Please slow down."),
          duration: 8000,
        });
        return;
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
      }
    },
    [router, pathname, notify],
  );

  return (
    <SWRConfig
      value={{
        fetcher: createFetcher,
        onError,
        revalidateOnFocus: false,
        revalidateOnReconnect: false,
        dedupingInterval: 60000,
      }}
    >
      {children}
    </SWRConfig>
  );
}
