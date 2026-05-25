import { cookies } from "next/headers";
import { notFound } from "next/navigation";
import { PersonDetailClient } from "./person-detail-client";
import type { SogcPersonEntity } from "@/lib/types";

export const dynamic = "force-dynamic";

interface Props {
  params: Promise<{ locale: string; id: string }>;
}

export default async function PersonDetailPage({ params }: Props) {
  const { locale, id } = await params;
  const cookieStore = await cookies();
  const sessionCookie = cookieStore.get("session")?.value;

  const apiBase = process.env.FASTAPI_URL ?? "http://localhost:8000";
  const res = await fetch(`${apiBase}/api/v1/sogc/persons/${id}`, {
    headers: sessionCookie ? { Cookie: `session=${sessionCookie}` } : {},
    cache: "no-store",
  }).catch(() => null);

  const entity: SogcPersonEntity | null = res?.ok ? await res.json().catch(() => null) : null;
  if (!entity) notFound();

  return <PersonDetailClient entity={entity} locale={locale} />;
}
