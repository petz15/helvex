"use client";
import { useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import { fetchCurrentUser } from "@/lib/api";
import { useI18n } from "@/i18n/context";

interface LandingCtaButtonProps {
  locale: string;
  variant: "hero" | "features" | "pricing";
  className: string;
}

export function LandingCtaButton({ locale, variant, className }: LandingCtaButtonProps) {
  const { dict } = useI18n();
  const t = dict.landing;
  const router = useRouter();

  const { data: user } = useSWR("me", fetchCurrentUser, {
    shouldRetryOnError: false,
    revalidateOnFocus: false,
  });

  useEffect(() => {
    if (user) {
      router.replace(`/${locale}/app/search`);
    }
  }, [user, locale, router]);

  const ctaText = {
    hero: t.hero.cta,
    features: t.features.cta,
    pricing: t.pricing.cta,
  }[variant];

  const href = user ? `/${locale}/app/search` : `/${locale}/register`;
  const label = user ? t.goToPlatform : ctaText;

  return (
    <Link href={href} className={className}>
      {label}
    </Link>
  );
}
