import { PeopleClient } from "./people-client";

interface Props {
  searchParams: Promise<{ q?: string; tab?: string }>;
}

export default async function PeoplePage({ searchParams }: Props) {
  const params = await searchParams;
  return <PeopleClient initialQ={params.q} initialTab={params.tab as "persons" | "auditors" | undefined} />;
}
