import { redirect } from "next/navigation";

// Redirect legacy /app/dashboard URLs to /app/search
export default async function DashboardRedirect({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  const sp = await searchParams;
  const qs = new URLSearchParams(sp).toString();
  redirect(qs ? `/app/search?${qs}` : "/app/search");
}
