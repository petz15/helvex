export const dynamic = "force-dynamic";

function normalizePublisherId(rawClientId: string): string {
  const trimmed = rawClientId.trim();
  if (!trimmed) return "";
  return trimmed.startsWith("ca-pub-") ? trimmed : `ca-pub-${trimmed}`;
}

export async function GET(): Promise<Response> {
  const publisherId = normalizePublisherId(process.env.NEXT_PUBLIC_ADSENSE_CLIENT_ID || "");
  if (!publisherId) {
    return new Response("", { status: 404 });
  }

  const body = `google.com, ${publisherId}, DIRECT, f08c47fec0942fa0\n`;
  return new Response(body, {
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "Cache-Control": "public, max-age=3600",
    },
  });
}