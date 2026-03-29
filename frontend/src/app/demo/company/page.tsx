import { notFound } from "next/navigation";
import { CompanyDetailClient } from "@/app/app/companies/[id]/company-detail-client";
import type { Company } from "@/lib/types";

export const metadata = { title: "Helvex — Full Profile Demo" };
export const dynamic = "force-dynamic";

export default async function DemoCompanyPage() {
  const apiBase = process.env.FASTAPI_URL ?? "http://localhost:8000";
  const res = await fetch(`${apiBase}/api/v1/companies/demo`, { cache: "no-store" }).catch(() => null);
  const company: Company | null = res?.ok ? await res.json().catch(() => null) : null;

  if (!company) notFound();

  return <CompanyDetailClient company={company} readOnlyDemo />;
}
