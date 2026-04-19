"use client";
import { useCallback } from "react";
import { useRouter } from "next/navigation";
import { SWRConfig } from "swr";
import { createFetcher } from "@/lib/api";

export function AuthErrorProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();

  const onError = useCallback(
    (error: any) => {
      if (error?.status === 401) {
        router.push("/login");
      }
    },
    [router]
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
