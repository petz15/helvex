import NogaClient from "./noga-client";

export const dynamic = "force-dynamic";

export default async function NogaPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  return <NogaClient locale={locale} />;
}
