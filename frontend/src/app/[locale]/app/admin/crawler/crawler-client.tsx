"use client";
import { useState, useCallback } from "react";
import useSWR from "swr";
import {
  Globe, RefreshCw, Loader2, AlertTriangle, CheckCircle2,
  Clock, ShieldAlert, ChevronLeft, ChevronRight, RotateCcw, ListPlus, ShieldBan, Flag,
} from "lucide-react";
import {
  fetchAdminCrawlerStats,
  fetchAdminCrawlerFailures,
  crawlerResetHttp,
  crawlerResetPlaywright,
  crawlerPopulateUrls,
  crawlerRecomputeWebsiteStatus,
  crawlerCrawlContentPlaywright,
  crawlerReopenIdentity,
  crawlerCrawlExternal,
  cleanupJobRuns,
  crawlerEnrichPurposeSim,
  crawlerDirectoryCrawl,
  crawlerDiscoverDirectoryDomains,
  fetchDirectoryCrawlDomains,
  approveDirectoryCrawlDomain,
  rejectDirectoryCrawlDomain,
  fetchCandidateDomainStats,
  blockDomain,
  fetchCrawlerReviewFlags,
  type AdminCrawlerStats,
  type AdminCrawlerFailure,
  type CandidateDomainStat,
  type DirectoryCrawlDomain,
  type ReviewFlagItem,
} from "@/lib/api";

const STATUS_LABELS: Record<string, string> = {
  pending: "Pending",
  in_progress: "In progress",
  crawled: "Crawled",
  bot_blocked: "Bot blocked",
  js_required: "JS required",
  http_error: "HTTP error",
  timeout: "Timeout",
  no_content: "No content",
  no_website: "No website",
};

const STATUS_COLOURS: Record<string, string> = {
  crawled: "bg-green-100 text-green-800",
  pending: "bg-blue-100 text-blue-700",
  in_progress: "bg-blue-100 text-blue-700",
  js_required: "bg-amber-100 text-amber-800",
  bot_blocked: "bg-red-100 text-red-800",
  http_error: "bg-red-100 text-red-800",
  timeout: "bg-orange-100 text-orange-800",
  no_content: "bg-slate-100 text-slate-600",
  no_website: "bg-slate-100 text-slate-500",
};

function Badge({ status }: { status: string }) {
  return (
    <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${STATUS_COLOURS[status] ?? "bg-slate-100 text-slate-600"}`}>
      {STATUS_LABELS[status] ?? status}
    </span>
  );
}

function KpiCard({ label, value, sub, icon: Icon, colour }: {
  label: string; value: string | number; sub?: string;
  icon: React.ElementType; colour: string;
}) {
  return (
    <div className="bg-white rounded-xl border border-slate-200 p-4 flex items-start gap-3">
      <div className={`mt-0.5 p-2 rounded-lg ${colour}`}>
        <Icon size={16} className="text-current" />
      </div>
      <div>
        <p className="text-xs text-slate-500">{label}</p>
        <p className="text-xl font-semibold text-slate-900 tabular-nums">{value}</p>
        {sub && <p className="text-xs text-slate-400 mt-0.5">{sub}</p>}
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
      <div className="px-4 py-3 border-b border-slate-100">
        <h2 className="text-sm font-semibold text-slate-700">{title}</h2>
      </div>
      <div className="p-4">{children}</div>
    </div>
  );
}

const TERMINAL = ["bot_blocked", "http_error", "timeout", "no_content", "no_website"] as const;
type TerminalStatus = (typeof TERMINAL)[number] | "";

export function CrawlerAdminClient() {
  const [banner, setBanner] = useState<{ kind: "success" | "error"; msg: string } | null>(null);
  const [acting, setActing] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [statusFilter, setStatusFilter] = useState<TerminalStatus>("");
  const [domainThreshold, setDomainThreshold] = useState(30);
  const [blockingDomain, setBlockingDomain] = useState<string | null>(null);
  const [reviewPage, setReviewPage] = useState(1);

  const flash = useCallback((kind: "success" | "error", msg: string) => {
    setBanner({ kind, msg });
    setTimeout(() => setBanner(null), 5000);
  }, []);

  const { data: stats, isLoading: statsLoading, error: statsError, mutate: mutateStats } =
    useSWR<AdminCrawlerStats>("admin-crawler-stats", fetchAdminCrawlerStats);

  const swrFailuresKey = `admin-crawler-failures-${page}-${statusFilter}`;
  const { data: failures, isLoading: failuresLoading, mutate: mutateFailures } =
    useSWR(swrFailuresKey, () =>
      fetchAdminCrawlerFailures({ page, page_size: 50, status_filter: statusFilter || undefined })
    );

  const { data: domainStats, isLoading: domainLoading, mutate: mutateDomains } =
    useSWR<CandidateDomainStat[]>(
      `admin-domain-stats-${domainThreshold}`,
      () => fetchCandidateDomainStats(domainThreshold),
    );

  const { data: reviewFlags, isLoading: reviewLoading } = useSWR(
    `admin-review-flags-${reviewPage}`,
    () => fetchCrawlerReviewFlags({ page: reviewPage, page_size: 50 }),
  );

  const { data: pendingDomains, isLoading: pendingDomainsLoading, mutate: mutatePendingDomains } =
    useSWR<DirectoryCrawlDomain[]>("admin-pending-dir-domains", () =>
      fetchDirectoryCrawlDomains("pending_review"),
    );

  async function handleDomainReview(id: number, action: "approve" | "reject") {
    try {
      if (action === "approve") await approveDirectoryCrawlDomain(id);
      else await rejectDirectoryCrawlDomain(id);
      void mutatePendingDomains();
    } catch (e) {
      flash("error", e instanceof Error ? e.message : "Action failed");
    }
  }

  const mutateAll = useCallback(() => {
    void mutateStats();
    void mutateFailures();
  }, [mutateStats, mutateFailures]);

  async function handleBlockDomain(domain: string) {
    setBlockingDomain(domain);
    try {
      await blockDomain(domain);
      flash("success", `${domain} added to blocklist.`);
      void mutateDomains();
    } catch (e) {
      flash("error", e instanceof Error ? e.message : "Failed to block domain");
    } finally {
      setBlockingDomain(null);
    }
  }

  async function doAction(key: string, fn: () => Promise<{ reset?: number; flagged?: number; job_id?: number }>) {
    setActing(key);
    try {
      const res = await fn();
      if ("reset" in res && res.reset != null) flash("success", `Reset ${res.reset} rows to pending.`);
      else if ("flagged" in res && res.flagged != null) flash("success", `Flagged ${res.flagged.toLocaleString()} pages — job #${res.job_id} enqueued.`);
      else flash("success", `Job #${res.job_id} enqueued.`);
      mutateAll();
    } catch (e) {
      flash("error", e instanceof Error ? e.message : "Action failed");
    } finally {
      setActing(null);
    }
  }

  const s = stats;
  const crawled = s?.status_counts["crawled"] ?? 0;
  const pending = (s?.status_counts["pending"] ?? 0) + (s?.status_counts["in_progress"] ?? 0);
  const jsRequired = s?.status_counts["js_required"] ?? 0;
  const totalFailed = TERMINAL.reduce((n, k) => n + (s?.status_counts[k] ?? 0), 0);
  const totalCandidates = Object.values(s?.candidate_counts ?? {}).reduce((a, b) => a + b, 0);

  const failureItems: AdminCrawlerFailure[] = failures?.items ?? [];
  const totalFailures = failures?.total ?? 0;
  const totalPages = Math.ceil(totalFailures / 50);

  return (
    <div className="p-6 max-w-6xl space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-slate-800 flex items-center gap-2">
            <Globe size={18} className="text-purple-600" /> Web Crawler Health
          </h1>
          <p className="text-xs text-slate-400 mt-0.5">Pipeline status · superadmin only</p>
        </div>
        <button
          onClick={mutateAll}
          className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-700 px-2.5 py-1.5 rounded-lg border border-slate-200 hover:bg-slate-50 transition-colors"
        >
          <RefreshCw size={12} /> Refresh
        </button>
      </div>

      {/* Banner */}
      {banner && (
        <div className={`rounded-lg border px-4 py-2.5 text-sm ${
          banner.kind === "success"
            ? "border-green-200 bg-green-50 text-green-800"
            : "border-red-200 bg-red-50 text-red-800"
        }`}>
          {banner.msg}
        </div>
      )}

      {statsLoading && (
        <div className="flex items-center justify-center h-32 text-slate-400">
          <Loader2 size={20} className="animate-spin" />
        </div>
      )}

      {statsError && (
        <div className="p-6 text-sm text-red-500">
          Failed to load crawler stats.{" "}
          <button onClick={() => void mutateStats()} className="underline">Retry</button>
        </div>
      )}

      {s && (
        <>
          {/* KPI row */}
          <div className="grid grid-cols-2 md:grid-cols-6 gap-3">
            <KpiCard label="Crawled" value={crawled.toLocaleString()} icon={CheckCircle2} colour="bg-green-50 text-green-600" />
            <KpiCard label="Pending" value={pending.toLocaleString()} icon={Clock} colour="bg-blue-50 text-blue-600" />
            <KpiCard label="Playwright queue" value={jsRequired.toLocaleString()} icon={Globe} colour="bg-amber-50 text-amber-600" />
            <KpiCard label="Terminal failures" value={totalFailed.toLocaleString()} icon={AlertTriangle} colour="bg-red-50 text-red-500" />
            <KpiCard label="Extracted" value={s.companies_extracted.toLocaleString()} sub={s.avg_confidence != null ? `avg conf ${(s.avg_confidence * 100).toFixed(0)}%` : undefined} icon={ShieldAlert} colour="bg-purple-50 text-purple-600" />
            <KpiCard label="Flagged for review" value={(s.review_flag_count ?? 0).toLocaleString()} icon={Flag} colour="bg-amber-50 text-amber-600" />
          </div>

          <div className="grid md:grid-cols-3 gap-4">
            {/* Status breakdown */}
            <Section title="Crawl status">
              <table className="w-full text-sm">
                <tbody>
                  {Object.entries(STATUS_LABELS).map(([key, label]) => {
                    const count = s.status_counts[key] ?? 0;
                    if (count === 0 && !["crawled", "pending", "bot_blocked"].includes(key)) return null;
                    return (
                      <tr key={key} className="border-b border-slate-50 last:border-0">
                        <td className="py-1.5"><Badge status={key} /></td>
                        <td className="py-1.5 text-right font-mono font-medium text-slate-800 tabular-nums">
                          {count.toLocaleString()}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </Section>

            {/* URL candidates */}
            <Section title="URL candidates">
              <table className="w-full text-sm">
                <tbody>
                  {[["pending", "Pending"], ["selected", "Selected"], ["crawled", "Crawled"], ["rejected", "Rejected"]].map(([key, label]) => (
                    <tr key={key} className="border-b border-slate-50 last:border-0">
                      <td className="py-1.5 text-slate-600">{label}</td>
                      <td className="py-1.5 text-right font-mono font-medium text-slate-800 tabular-nums">
                        {(s.candidate_counts[key] ?? 0).toLocaleString()}
                      </td>
                    </tr>
                  ))}
                  <tr className="border-t border-slate-200">
                    <td className="pt-2 text-xs text-slate-400">Total</td>
                    <td className="pt-2 text-right font-mono text-xs text-slate-500 tabular-nums">{totalCandidates.toLocaleString()}</td>
                  </tr>
                </tbody>
              </table>
            </Section>

            {/* Page / S3 coverage */}
            <Section title="Page storage">
              <table className="w-full text-sm">
                <tbody>
                  {[
                    ["Pages in DB", s.pages_total],
                    ["Pages in S3", s.pages_in_s3],
                    ["Awaiting extraction", s.pages_needing_extraction],
                  ].map(([label, val]) => (
                    <tr key={label as string} className="border-b border-slate-50 last:border-0">
                      <td className="py-1.5 text-slate-600">{label}</td>
                      <td className="py-1.5 text-right font-mono font-medium text-slate-800 tabular-nums">
                        {(val as number).toLocaleString()}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Section>
          </div>

          {/* Extraction field coverage */}
          <Section title="Extraction field coverage">
            {s.companies_extracted === 0 ? (
              <p className="text-sm text-slate-400">No extractions yet.</p>
            ) : (
              <>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                  {([
                    ["Email", "emails"], ["Phone", "phones"], ["UID", "uid"], ["Address", "address"],
                    ["Description", "description"], ["Keywords", "service_keywords"], ["Persons", "persons"], ["Socials", "socials"],
                  ] as const).map(([label, key]) => {
                    const n = s.field_coverage?.[key] ?? 0;
                    const pct = s.companies_extracted > 0 ? Math.round((n / s.companies_extracted) * 100) : 0;
                    const barColour = pct >= 60 ? "bg-green-500" : pct >= 30 ? "bg-amber-500" : "bg-red-400";
                    return (
                      <div key={key}>
                        <div className="flex items-center justify-between mb-1">
                          <span className="text-xs text-slate-500">{label}</span>
                          <span className="text-xs font-medium text-slate-700 tabular-nums">{pct}%</span>
                        </div>
                        <div className="h-1.5 rounded-full bg-slate-100 overflow-hidden">
                          <div className={`h-full ${barColour}`} style={{ width: `${pct}%` }} />
                        </div>
                        <p className="text-[10px] text-slate-400 mt-0.5 tabular-nums">{n.toLocaleString()} / {s.companies_extracted.toLocaleString()}</p>
                      </div>
                    );
                  })}
                </div>
                {/* UID verification — the strongest correctness signal */}
                <div className="flex flex-wrap gap-4 mt-4 pt-3 border-t border-slate-100 text-xs">
                  <span className="inline-flex items-center gap-1.5 text-green-700">
                    <CheckCircle2 size={13} /> UID verified: <strong className="tabular-nums">{s.uid_match.toLocaleString()}</strong>
                  </span>
                  <span className="inline-flex items-center gap-1.5 text-red-600">
                    <AlertTriangle size={13} /> UID mismatch (likely wrong site): <strong className="tabular-nums">{s.uid_mismatch.toLocaleString()}</strong>
                  </span>
                  <span className="inline-flex items-center gap-1.5 text-green-700">
                    <CheckCircle2 size={13} /> Name+address verified (no UID): <strong className="tabular-nums">{s.name_address_verified.toLocaleString()}</strong>
                  </span>
                  <span className="text-slate-400">
                    Avg confidence: <strong className="text-slate-600">{s.avg_confidence != null ? `${(s.avg_confidence * 100).toFixed(0)}%` : "—"}</strong>
                  </span>
                </div>
              </>
            )}
          </Section>

          {/* Actions */}
          <Section title="Actions">
            <div className="flex flex-wrap gap-3">
              <button
                onClick={() => doAction("reset-http", crawlerResetHttp)}
                disabled={acting !== null}
                className="flex items-center gap-2 px-3 py-2 text-sm rounded-lg border border-slate-200 hover:bg-slate-50 disabled:opacity-50 transition-colors"
              >
                {acting === "reset-http" ? <Loader2 size={14} className="animate-spin" /> : <RotateCcw size={14} />}
                Reset HTTP failures
              </button>
              <button
                onClick={() => doAction("reset-playwright", crawlerResetPlaywright)}
                disabled={acting !== null}
                className="flex items-center gap-2 px-3 py-2 text-sm rounded-lg border border-slate-200 hover:bg-slate-50 disabled:opacity-50 transition-colors"
              >
                {acting === "reset-playwright" ? <Loader2 size={14} className="animate-spin" /> : <RotateCcw size={14} />}
                Reset Playwright failures
              </button>
              <button
                onClick={() => doAction("populate", crawlerPopulateUrls)}
                disabled={acting !== null}
                className="flex items-center gap-2 px-3 py-2 text-sm rounded-lg border border-amber-200 bg-amber-50 hover:bg-amber-100 text-amber-800 disabled:opacity-50 transition-colors"
              >
                {acting === "populate" ? <Loader2 size={14} className="animate-spin" /> : <ListPlus size={14} />}
                Backfill URL candidates
              </button>
              <button
                onClick={() => doAction("website-status", crawlerRecomputeWebsiteStatus)}
                disabled={acting !== null}
                className="flex items-center gap-2 px-3 py-2 text-sm rounded-lg border border-indigo-200 bg-indigo-50 hover:bg-indigo-100 text-indigo-800 disabled:opacity-50 transition-colors"
              >
                {acting === "website-status" ? <Loader2 size={14} className="animate-spin" /> : <Globe size={14} />}
                Recompute website status
              </button>
              <button
                onClick={() => doAction("purpose-sim", crawlerEnrichPurposeSim)}
                disabled={acting !== null}
                className="flex items-center gap-2 px-3 py-2 text-sm rounded-lg border border-violet-200 bg-violet-50 hover:bg-violet-100 text-violet-800 disabled:opacity-50 transition-colors"
              >
                {acting === "purpose-sim" ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
                Enrich purpose similarity (ML)
              </button>
              <button
                onClick={() => doAction("reopen-identity", crawlerReopenIdentity)}
                disabled={acting !== null}
                title="Re-open identity resolution for companies retired while an untried URL candidate remained. Rejects PDF/asset candidates first, then re-queues them for the identity crawler."
                className="flex items-center gap-2 px-3 py-2 text-sm rounded-lg border border-emerald-200 bg-emerald-50 hover:bg-emerald-100 text-emerald-800 disabled:opacity-50 transition-colors"
              >
                {acting === "reopen-identity" ? <Loader2 size={14} className="animate-spin" /> : <RotateCcw size={14} />}
                Reopen exhausted identities
              </button>
              <button
                onClick={() => doAction("content-playwright", crawlerCrawlContentPlaywright)}
                disabled={acting !== null}
                title="Phase B for sites the HTTP content crawl could not read (JS / bot wall). Normally auto-enqueued."
                className="flex items-center gap-2 px-3 py-2 text-sm rounded-lg border border-sky-200 bg-sky-50 hover:bg-sky-100 text-sky-800 disabled:opacity-50 transition-colors"
              >
                {acting === "content-playwright" ? <Loader2 size={14} className="animate-spin" /> : <Globe size={14} />}
                Content crawl (Playwright)
              </button>
              <button
                onClick={() => doAction("external", () => crawlerCrawlExternal(100))}
                disabled={acting !== null}
                title="PAID: ScrapingDog residential proxy. Only companies that already defeated httpx and Playwright. Capped at 100 companies × 2 pages per run."
                className="flex items-center gap-2 px-3 py-2 text-sm rounded-lg border border-rose-300 bg-rose-50 hover:bg-rose-100 text-rose-800 disabled:opacity-50 transition-colors"
              >
                {acting === "external" ? <Loader2 size={14} className="animate-spin" /> : <AlertTriangle size={14} />}
                External scrape (paid, max 100)
              </button>
              <button
                onClick={() => doAction("cleanup-jobs", () => cleanupJobRuns())}
                disabled={acting !== null}
                title="Delete terminal job runs older than 30 days, keeping the 20 most recent per type. Job events cascade."
                className="flex items-center gap-2 px-3 py-2 text-sm rounded-lg border border-slate-200 hover:bg-slate-50 disabled:opacity-50 transition-colors"
              >
                {acting === "cleanup-jobs" ? <Loader2 size={14} className="animate-spin" /> : <RotateCcw size={14} />}
                Prune job history
              </button>
              <button
                onClick={() => doAction("directory-crawl", crawlerDirectoryCrawl)}
                disabled={acting !== null}
                className="flex items-center gap-2 px-3 py-2 text-sm rounded-lg border border-teal-200 bg-teal-50 hover:bg-teal-100 text-teal-800 disabled:opacity-50 transition-colors"
              >
                {acting === "directory-crawl" ? <Loader2 size={14} className="animate-spin" /> : <Globe size={14} />}
                Crawl directory profiles
              </button>
              <button
                onClick={() => doAction("discover-dirs", () => crawlerDiscoverDirectoryDomains({ min_companies: 30 }).then(r => ({ job_id: r.job_id })))}
                disabled={acting !== null}
                className="flex items-center gap-2 px-3 py-2 text-sm rounded-lg border border-cyan-200 bg-cyan-50 hover:bg-cyan-100 text-cyan-800 disabled:opacity-50 transition-colors"
              >
                {acting === "discover-dirs" ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
                Discover new directories
              </button>
            </div>
            <p className="text-xs text-slate-400 mt-3">
              {'"'}Reset HTTP failures{'"'} moves bot_blocked / http_error / timeout / no_content rows (HTTP tier) back to pending so they are re-crawled.
              {'"'}Reset Playwright failures{'"'} does the same for the Playwright tier.
              {'"'}Backfill URL candidates{'"'} enqueues a job that reads stored Google results and populates company_url_candidates for companies that were enriched before auto-populate was added.
              {'"'}Crawl directory profiles{'"'} fetches profile pages from moneyhouse.ch, local.ch, northdata.com and similar directories — extracted text feeds into Claude AI scoring as additional context.
              To trigger extraction or re-extract all HTML, use the Collection page.
            </p>
          </Section>
        </>
      )}

      {/* Directory domain review queue */}
      {((pendingDomains?.length ?? 0) > 0 || pendingDomainsLoading) && (
        <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
          <div className="px-4 py-3 border-b border-slate-100 flex items-center justify-between gap-3">
            <h2 className="text-sm font-semibold text-slate-700 flex items-center gap-1.5">
              <Globe size={14} className="text-cyan-500" /> Directory domains pending review
              {(pendingDomains?.length ?? 0) > 0 && (
                <span className="ml-1 px-1.5 py-0.5 rounded-full bg-cyan-100 text-cyan-700 text-xs font-semibold">
                  {pendingDomains!.length}
                </span>
              )}
            </h2>
            <p className="text-xs text-slate-400">
              Auto-discovered via frequency analysis. Approve to include in directory crawl; reject to ignore.
            </p>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 border-b border-slate-200">
                <tr>
                  <th className="text-left px-4 py-2.5 text-xs font-semibold text-slate-500 uppercase tracking-wide">Domain</th>
                  <th className="text-right px-4 py-2.5 text-xs font-semibold text-slate-500 uppercase tracking-wide">Companies</th>
                  <th className="text-left px-4 py-2.5 text-xs font-semibold text-slate-500 uppercase tracking-wide">Source</th>
                  <th className="text-right px-4 py-2.5 text-xs font-semibold text-slate-500 uppercase tracking-wide">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {pendingDomainsLoading && (
                  <tr><td colSpan={4} className="px-4 py-6 text-center text-slate-400"><Loader2 size={16} className="animate-spin inline" /></td></tr>
                )}
                {(pendingDomains ?? []).map((d) => (
                  <tr key={d.id} className="hover:bg-slate-50">
                    <td className="px-4 py-2.5 font-mono text-slate-800 text-xs">{d.value}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums text-slate-600 text-xs">
                      {d.company_count != null ? d.company_count.toLocaleString() : "—"}
                    </td>
                    <td className="px-4 py-2.5 text-slate-400 text-xs">{d.source === "auto_discovered" ? "Auto" : "Manual"}</td>
                    <td className="px-4 py-2.5 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <button
                          onClick={() => handleDomainReview(d.id, "approve")}
                          className="px-2 py-1 text-xs rounded border border-green-200 bg-green-50 hover:bg-green-100 text-green-700 transition-colors"
                        >
                          Approve
                        </button>
                        <button
                          onClick={() => handleDomainReview(d.id, "reject")}
                          className="px-2 py-1 text-xs rounded border border-red-200 bg-red-50 hover:bg-red-100 text-red-700 transition-colors"
                        >
                          Reject
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Review flags table */}
      {(s?.review_flag_count ?? 0) > 0 && (
        <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
          <div className="px-4 py-3 border-b border-slate-100 flex items-center justify-between gap-3">
            <h2 className="text-sm font-semibold text-slate-700 flex items-center gap-1.5">
              <Flag size={14} className="text-amber-500" /> Extracts flagged for review
            </h2>
            <p className="text-xs text-slate-400">
              UID found on crawled page belongs to a different company. Review and promote/discard from the company&apos;s Website tab.
            </p>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 border-b border-slate-200">
                <tr>
                  <th className="text-left px-4 py-2.5 text-xs font-semibold text-slate-500 uppercase tracking-wide">Company</th>
                  <th className="text-left px-4 py-2.5 text-xs font-semibold text-slate-500 uppercase tracking-wide">Crawled URL</th>
                  <th className="text-left px-4 py-2.5 text-xs font-semibold text-slate-500 uppercase tracking-wide">Found UID</th>
                  <th className="text-right px-4 py-2.5 text-xs font-semibold text-slate-500 uppercase tracking-wide">Conf</th>
                  <th className="text-left px-4 py-2.5 text-xs font-semibold text-slate-500 uppercase tracking-wide">Flag</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {reviewLoading && (
                  <tr><td colSpan={5} className="px-4 py-6 text-center text-slate-400"><Loader2 size={16} className="animate-spin inline" /></td></tr>
                )}
                {!reviewLoading && (reviewFlags?.items ?? []).map((f: ReviewFlagItem) => (
                  <tr key={`${f.company_id}-${f.url_candidate_id}`} className="hover:bg-slate-50 transition-colors">
                    <td className="px-4 py-2.5">
                      <a href={`/app/companies/${f.company_id}`} className="font-medium text-slate-800 hover:text-blue-600 truncate block max-w-[180px]">{f.company_name}</a>
                      <p className="text-xs text-slate-400 font-mono">{f.company_uid}</p>
                    </td>
                    <td className="px-4 py-2.5 max-w-[200px]">
                      {f.candidate_url
                        ? <a href={f.candidate_url} target="_blank" rel="noopener noreferrer" className="text-xs text-blue-600 hover:underline truncate block">{f.candidate_url}</a>
                        : <span className="text-xs text-slate-400">—</span>
                      }
                    </td>
                    <td className="px-4 py-2.5 text-xs font-mono text-slate-700">{f.found_uid ?? "—"}</td>
                    <td className="px-4 py-2.5 text-right text-xs text-slate-600 tabular-nums">
                      {f.confidence != null ? `${(f.confidence * 100).toFixed(0)}%` : "—"}
                    </td>
                    <td className="px-4 py-2.5">
                      <span className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-full px-2 py-0.5">{f.review_flag}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {(reviewFlags?.total ?? 0) > 50 && (
            <div className="px-4 py-3 border-t border-slate-100 flex items-center justify-between text-xs text-slate-500">
              <span>{reviewFlags!.total.toLocaleString()} flagged · page {reviewPage}/{Math.ceil(reviewFlags!.total / 50)}</span>
              <div className="flex gap-1">
                <button onClick={() => setReviewPage(p => Math.max(1, p - 1))} disabled={reviewPage === 1} className="p-1.5 rounded hover:bg-slate-100 disabled:opacity-40"><ChevronLeft size={14} /></button>
                <button onClick={() => setReviewPage(p => Math.min(Math.ceil(reviewFlags!.total / 50), p + 1))} disabled={reviewPage === Math.ceil(reviewFlags!.total / 50)} className="p-1.5 rounded hover:bg-slate-100 disabled:opacity-40"><ChevronRight size={14} /></button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* High-frequency candidate domains */}
      <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
        <div className="px-4 py-3 border-b border-slate-100 flex items-center justify-between gap-3 flex-wrap">
          <div>
            <h2 className="text-sm font-semibold text-slate-700 flex items-center gap-1.5">
              <ShieldBan size={14} className="text-orange-500" /> High-frequency candidate domains
            </h2>
            <p className="text-xs text-slate-400 mt-0.5">
              Domains appearing as URL candidates for many companies — potential aggregators/directories to block.
            </p>
          </div>
          <div className="flex items-center gap-2 text-xs text-slate-600">
            <label className="whitespace-nowrap">Min companies:</label>
            <select
              value={domainThreshold}
              onChange={e => setDomainThreshold(Number(e.target.value))}
              className="border border-slate-200 rounded-lg px-2 py-1 text-slate-600 bg-white"
            >
              {[10, 20, 30, 50, 100, 200].map(n => (
                <option key={n} value={n}>{n}</option>
              ))}
            </select>
            <button
              onClick={() => void mutateDomains()}
              className="flex items-center gap-1 px-2 py-1 rounded border border-slate-200 hover:bg-slate-50"
            >
              <RefreshCw size={11} /> Reload
            </button>
          </div>
        </div>
        {domainLoading ? (
          <div className="p-6 flex justify-center text-slate-400"><Loader2 size={18} className="animate-spin" /></div>
        ) : !domainStats || domainStats.length === 0 ? (
          <p className="px-4 py-6 text-xs text-slate-400">No domains above threshold.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 border-b border-slate-200">
                <tr>
                  <th className="text-left px-4 py-2.5 text-xs font-semibold text-slate-500 uppercase tracking-wide">Domain</th>
                  <th className="text-right px-4 py-2.5 text-xs font-semibold text-slate-500 uppercase tracking-wide">Companies</th>
                  <th className="text-right px-4 py-2.5 text-xs font-semibold text-slate-500 uppercase tracking-wide">Status</th>
                  <th className="px-4 py-2.5" />
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {domainStats.map(d => (
                  <tr key={d.domain} className="hover:bg-slate-50 transition-colors">
                    <td className="px-4 py-2.5 font-mono text-slate-800 text-xs">{d.domain}</td>
                    <td className="px-4 py-2.5 text-right font-mono font-semibold text-slate-700 tabular-nums">{d.company_count.toLocaleString()}</td>
                    <td className="px-4 py-2.5 text-right">
                      {d.already_blocked ? (
                        <span className="inline-flex items-center gap-1 text-xs text-green-700 bg-green-50 border border-green-200 rounded-full px-2 py-0.5">
                          <CheckCircle2 size={11} /> Blocked
                        </span>
                      ) : (
                        <span className="text-xs text-slate-400">Not blocked</span>
                      )}
                    </td>
                    <td className="px-4 py-2.5 text-right">
                      {!d.already_blocked && (
                        <button
                          onClick={() => handleBlockDomain(d.domain)}
                          disabled={blockingDomain !== null}
                          className="flex items-center gap-1 px-2 py-1 text-xs rounded border border-red-200 text-red-700 hover:bg-red-50 disabled:opacity-50 transition-colors"
                        >
                          {blockingDomain === d.domain
                            ? <Loader2 size={11} className="animate-spin" />
                            : <ShieldBan size={11} />
                          }
                          Block
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Terminal failures table */}
      <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
        <div className="px-4 py-3 border-b border-slate-100 flex items-center justify-between gap-3">
          <h2 className="text-sm font-semibold text-slate-700">Terminal failures</h2>
          <select
            value={statusFilter}
            onChange={e => { setStatusFilter(e.target.value as TerminalStatus); setPage(1); }}
            className="text-xs border border-slate-200 rounded-lg px-2 py-1 text-slate-600 bg-white"
          >
            <option value="">All failure types</option>
            {TERMINAL.map(s => (
              <option key={s} value={s}>{STATUS_LABELS[s]}</option>
            ))}
          </select>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 border-b border-slate-200">
              <tr>
                <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wide">Company</th>
                <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wide">URL</th>
                <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wide">Status</th>
                <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wide">Detail</th>
                <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wide">Tier / Fails</th>
                <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wide">Last crawled</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {failuresLoading && (
                <tr><td colSpan={6} className="px-4 py-8 text-center text-slate-400"><Loader2 size={16} className="animate-spin inline" /></td></tr>
              )}
              {!failuresLoading && failureItems.length === 0 && (
                <tr><td colSpan={6} className="px-4 py-8 text-center text-xs text-slate-400">No terminal failures.</td></tr>
              )}
              {failureItems.map(f => (
                <tr key={f.company_id} className="hover:bg-slate-50 transition-colors">
                  <td className="px-4 py-3">
                    <p className="font-medium text-slate-800 truncate max-w-[180px]">{f.company_name}</p>
                    <p className="text-xs text-slate-400 font-mono">{f.company_uid}</p>
                  </td>
                  <td className="px-4 py-3 max-w-[200px]">
                    {f.url
                      ? <a href={f.url} target="_blank" rel="noopener noreferrer" className="text-xs text-blue-600 hover:underline truncate block">{f.url}</a>
                      : <span className="text-xs text-slate-400">—</span>
                    }
                  </td>
                  <td className="px-4 py-3"><Badge status={f.crawl_status} /></td>
                  <td className="px-4 py-3 max-w-[220px]">
                    <span className="text-xs text-slate-500 truncate block" title={f.crawl_error_detail ?? undefined}>
                      {f.bot_protection_type ? <span className="text-orange-600 font-medium">{f.bot_protection_type} · </span> : null}
                      {f.crawl_error_detail ?? "—"}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <span className="text-xs font-mono text-slate-600">{f.tier}</span>
                    {f.consecutive_failures > 0 && (
                      <span className="ml-1.5 text-xs text-red-500">×{f.consecutive_failures}</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-xs text-slate-400 whitespace-nowrap">
                    {f.last_crawled_at
                      ? new Date(f.last_crawled_at).toLocaleDateString("de-CH", { day: "2-digit", month: "2-digit", year: "2-digit", hour: "2-digit", minute: "2-digit" })
                      : "—"
                    }
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {totalPages > 1 && (
          <div className="px-4 py-3 border-t border-slate-100 flex items-center justify-between text-xs text-slate-500">
            <span>{totalFailures.toLocaleString()} failures · page {page}/{totalPages}</span>
            <div className="flex gap-1">
              <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1} className="p-1.5 rounded hover:bg-slate-100 disabled:opacity-40">
                <ChevronLeft size={14} />
              </button>
              <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page === totalPages} className="p-1.5 rounded hover:bg-slate-100 disabled:opacity-40">
                <ChevronRight size={14} />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
