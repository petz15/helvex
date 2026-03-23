import { cookies } from "next/headers";
import { notFound } from "next/navigation";
import { CompanyDetailClient } from "./company-detail-client";
import type { Company } from "@/lib/types";

export const dynamic = "force-dynamic";

interface Props {
  params: Promise<{ id: string }>;
}

export default async function CompanyDetailPage({ params }: Props) {
  const { id } = await params;
  const cookieStore = await cookies();
  const sessionCookie = cookieStore.get("session")?.value;

  // Fetch directly from FastAPI with the session cookie forwarded.
  // A relative fetch in a server component does NOT carry browser cookies,
  // so we bypass the rewrite and call the backend directly.
  const apiBase = process.env.FASTAPI_URL ?? "http://localhost:8000";
  const res = await fetch(`${apiBase}/api/v1/companies/${id}`, {
    headers: sessionCookie ? { Cookie: `session=${sessionCookie}` } : {},
    cache: "no-store",
  }).catch(() => null);

  const company: Company | null = res?.ok ? await res.json().catch(() => null) : null;
  if (!company) notFound();
  return <CompanyDetailClient company={company!} />;
}
