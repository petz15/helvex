import { SearchLandingClient } from "./search-landing-client";

export const dynamic = "force-dynamic";

export default async function SearchPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  return <SearchLandingClient locale={locale} />;
}
