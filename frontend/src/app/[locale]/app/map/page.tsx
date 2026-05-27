import { redirect } from "next/navigation";

export default async function MapPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  redirect(`/${locale}/app/companies?view=map`);
}
