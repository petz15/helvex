"use client";

import useSWR from "swr";
import Image from "next/image";
import { fetchCurrentUser, fetchOrg } from "@/lib/api";
import { hasNoAds } from "@/lib/entitlements";
import baloghIcon from "./balogh_cons_icon.svg";

interface BaloghAdCardProps {
  className?: string;
}

export function BaloghAdCard({ className = "" }: BaloghAdCardProps) {
  const { data: me } = useSWR("me", fetchCurrentUser);
  const orgId = me?.org?.id ?? null;
  const { data: org } = useSWR(orgId ? ["org-detail", orgId] : null, () => fetchOrg(orgId!));

  if (!me || !org) return null;
  if (hasNoAds({ tier: org.tier, customFeatures: org.custom_features })) return null;

  return (
    <div className={`${className}`}>
      <a
        href="https://balogh-consulting.ch"
        target="_blank"
        rel="noopener noreferrer"
      >
        <Image
          src={baloghIcon}
          alt="Balogh Consulting"
          className="cursor-pointer"
        />
      </a>
    </div>
  );
}