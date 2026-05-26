import { CorporateClient } from "./corporate-client";

interface Props {
  searchParams: Promise<{ q?: string; che?: string }>;
}

export default async function CorporatePage({ searchParams }: Props) {
  const params = await searchParams;
  return <CorporateClient initialQ={params.q} initialChe={params.che} />;
}
