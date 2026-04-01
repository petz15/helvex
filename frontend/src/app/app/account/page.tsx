import { AccountClient } from "./account-client";

type AccountPageProps = {
  searchParams?: {
    checkout?: string | string[];
    kind?: string | string[];
    tier?: string | string[];
    credits?: string | string[];
  };
};

function firstValue(value: string | string[] | undefined): string | undefined {
  if (Array.isArray(value)) return value[0];
  return value;
}

export default function AccountPage({ searchParams }: AccountPageProps) {
  return (
    <AccountClient
      checkout={firstValue(searchParams?.checkout)}
      kind={firstValue(searchParams?.kind)}
      tier={firstValue(searchParams?.tier)}
      credits={firstValue(searchParams?.credits)}
    />
  );
}
