import { notFound } from "next/navigation";
import { fetchCompany } from "@/lib/api";
import { CompanyDetailClient } from "./company-detail-client";

export const dynamic = "force-dynamic";

interface Props {
  params: Promise<{ id: string }>;
}

export default async function CompanyDetailPage({ params }: Props) {
  const { id } = await params;
  const company = await fetchCompany(Number(id)).catch(() => null);
  if (!company) notFound();
  return <CompanyDetailClient company={company} />;
}
