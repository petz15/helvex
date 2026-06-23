import { SimapSearchClient } from "./simap-client";

export const dynamic = "force-dynamic";

export default async function SimapPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  return <SimapSearchClient locale={locale} />;
}
