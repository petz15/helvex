"use client";
import { useState, useCallback, useEffect, useMemo } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import useSWR from "swr";
import { Search, Building2, Users, ListTree, ChevronRight, ChevronLeft, Globe, MapPin, ArrowRight } from "lucide-react";
import { fetchCompanies, fetchCantons, fetchNogaHierarchy, searchPersonEntities } from "@/lib/api";
import type { NogaNode } from "@/lib/api";
import type { Company, SogcPersonEntity } from "@/lib/types";
import { HelvexMark } from "@/components/helvex-logo";
import { cn } from "@/lib/utils";

type Tab = "companies" | "people" | "noga";

// ── helpers ──────────────────────────────────────────────────────────────────

function flattenNoga(nodes: NogaNode[]): NogaNode[] {
  return nodes.flatMap(n => [n, ...flattenNoga(n.children)]);
}

function matchesNoga(node: NogaNode, q: string): boolean {
  const lq = q.toLowerCase();
  return (
    node.code.toLowerCase().includes(lq) ||
    node.label.toLowerCase().includes(lq) ||
    Object.values(node.labels ?? {}).some(l => l.toLowerCase().includes(lq))
  );
}

function domainFromUrl(url: string | null): string | null {
  if (!url) return null;
  try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return null; }
}

function companyInitial(name: string): string {
  return name.trim().charAt(0).toUpperCase();
}

function personInitials(first: string | null, last: string | null): string {
  return ((first ?? "").charAt(0) + (last ?? "").charAt(0)).toUpperCase() || "?";
}

// ── sub-components ────────────────────────────────────────────────────────────

function SkeletonRows() {
  return (
    <div className="divide-y divide-slate-50">
      {[0.4, 0.6, 0.5, 0.65, 0.45, 0.55].map((w, i) => (
        <div key={i} className="flex items-center gap-3 px-4 py-4 animate-pulse">
          <div className="w-9 h-9 rounded-lg bg-slate-100 shrink-0" />
          <div className="flex-1 space-y-2">
            <div className="h-3 bg-slate-100 rounded" style={{ width: `${w * 60 + 20}%` }} />
            <div className="h-2.5 bg-slate-50 rounded" style={{ width: `${w * 50 + 15}%` }} />
          </div>
          <div className="w-6 h-3 bg-slate-100 rounded" />
        </div>
      ))}
    </div>
  );
}

function ResultsHeader({ total, query }: { total: number; query: string }) {
  return (
    <div className="px-4 py-2.5 bg-slate-50/80 border-b border-slate-100 flex items-center justify-between">
      <p className="text-xs text-slate-500">
        <span className="font-semibold text-slate-700 tabular-nums">{total.toLocaleString()}</span>
        {" "}result{total !== 1 ? "s" : ""} for{" "}
        <span className="font-semibold text-slate-700">&ldquo;{query}&rdquo;</span>
      </p>
    </div>
  );
}

function Pagination({ page, total, pageSize, onPage }: {
  page: number; total: number; pageSize: number; onPage: (p: number) => void;
}) {
  const totalPages = Math.ceil(total / pageSize);
  if (totalPages <= 1) return null;
  const from = (page - 1) * pageSize + 1;
  const to = Math.min(page * pageSize, total);
  return (
    <div className="flex items-center justify-between px-4 py-3 border-t border-slate-100 bg-white sticky bottom-0">
      <span className="text-xs text-slate-400 tabular-nums">{from}–{to} of {total.toLocaleString()}</span>
      <div className="flex items-center gap-1">
        <button
          onClick={() => onPage(page - 1)} disabled={page <= 1}
          className="flex items-center gap-1 px-2.5 py-1.5 rounded-md text-xs font-medium text-slate-600 border border-slate-200 hover:bg-slate-50 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
        >
          <ChevronLeft size={11} /> Prev
        </button>
        <span className="px-2.5 text-xs text-slate-400 tabular-nums min-w-[56px] text-center">
          {page} / {totalPages}
        </span>
        <button
          onClick={() => onPage(page + 1)} disabled={page >= totalPages}
          className="flex items-center gap-1 px-2.5 py-1.5 rounded-md text-xs font-medium text-slate-600 border border-slate-200 hover:bg-slate-50 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
        >
          Next <ChevronRight size={11} />
        </button>
      </div>
    </div>
  );
}

function StatusBadge({ status }: { status: string | null }) {
  if (!status) return null;
  const cfg = {
    ACTIVE: { dot: "bg-emerald-400", pill: "bg-emerald-50 text-emerald-700 border-emerald-200", label: "Active" },
    CANCELLED: { dot: "bg-red-400", pill: "bg-red-50 text-red-600 border-red-200", label: "Cancelled" },
    BEING_CANCELLED: { dot: "bg-amber-400", pill: "bg-amber-50 text-amber-700 border-amber-200", label: "Pending" },
  }[status] ?? { dot: "bg-slate-300", pill: "bg-slate-50 text-slate-500 border-slate-200", label: status };
  return (
    <span className={cn("inline-flex items-center gap-1 text-[10px] font-semibold px-1.5 py-0.5 rounded-full border", cfg.pill)}>
      <span className={cn("w-1.5 h-1.5 rounded-full shrink-0", cfg.dot)} />
      {cfg.label}
    </span>
  );
}

function ScoreBadge({ score }: { score: number | null }) {
  if (score == null || score <= 0) return null;
  const n = Math.round(score);
  return (
    <span className={cn(
      "text-[11px] font-bold tabular-nums px-1.5 py-0.5 rounded-md",
      n >= 70 ? "text-emerald-700 bg-emerald-50" :
      n >= 40 ? "text-amber-700 bg-amber-50" :
      "text-slate-500 bg-slate-100"
    )}>
      {n}
    </span>
  );
}

function CompanyRow({ company, locale }: { company: Company; locale: string }) {
  const domain = domainFromUrl(company.website_url);
  return (
    <Link
      href={`/${locale}/app/companies/${company.id}`}
      className="group flex items-start gap-3 px-4 py-3.5 border-b border-slate-50 hover:bg-blue-50/50 transition-colors relative"
    >
      <div className="absolute inset-y-0 left-0 w-0.5 bg-blue-500 origin-center scale-y-0 group-hover:scale-y-100 transition-transform duration-150 rounded-r-full" />

      <div className="w-9 h-9 rounded-lg bg-blue-50 border border-blue-100/80 flex items-center justify-center shrink-0 text-[13px] font-bold text-blue-600 group-hover:bg-blue-100 transition-colors mt-0.5">
        {companyInitial(company.name)}
      </div>

      <div className="flex-1 min-w-0">
        <div className="flex items-baseline gap-2 flex-wrap">
          <span className="font-semibold text-[13.5px] text-slate-900 group-hover:text-blue-700 transition-colors leading-snug">
            {company.name}
          </span>
          <StatusBadge status={company.status} />
        </div>

        <div className="flex items-center gap-1.5 mt-1.5 flex-wrap">
          {company.canton && (
            <span className="text-[11px] font-semibold text-slate-600 bg-slate-100 px-1.5 py-0.5 rounded-[4px] leading-none">
              {company.canton}
            </span>
          )}
          {company.legal_form && (
            <span className="text-[11px] text-slate-400 leading-none">{company.legal_form}</span>
          )}
          {company.municipality && (
            <span className="text-[11px] text-slate-400 flex items-center gap-0.5 leading-none">
              <MapPin size={9} className="opacity-50 shrink-0" />
              {company.municipality}
            </span>
          )}
          {domain && (
            <span className="text-[11px] text-blue-500/80 flex items-center gap-0.5 leading-none truncate max-w-[150px]">
              <Globe size={9} className="opacity-60 shrink-0" />
              {domain}
            </span>
          )}
        </div>

        {company.purpose && (
          <p className="mt-1.5 text-[11px] text-slate-400 leading-relaxed line-clamp-1">
            {company.purpose}
          </p>
        )}
      </div>

      <div className="flex flex-col items-end gap-1.5 shrink-0 ml-2">
        <ScoreBadge score={company.combined_score} />
        <ChevronRight size={13} className="text-slate-300 group-hover:text-blue-400 transition-colors" />
      </div>
    </Link>
  );
}

function PersonRow({ person, locale }: { person: SogcPersonEntity; locale: string }) {
  const name = [person.firstname, person.lastname].filter(Boolean).join(" ") || "Unknown";
  const confidenceCfg = {
    high: "text-emerald-700 bg-emerald-50 border-emerald-200",
    medium: "text-amber-700 bg-amber-50 border-amber-200",
    low: "text-slate-500 bg-slate-50 border-slate-200",
  }[person.confidence_level] ?? "text-slate-500 bg-slate-50 border-slate-200";

  return (
    <Link
      href={`/${locale}/app/people/${person.id}`}
      className="group flex items-start gap-3 px-4 py-3.5 border-b border-slate-50 hover:bg-blue-50/50 transition-colors relative"
    >
      <div className="absolute inset-y-0 left-0 w-0.5 bg-blue-500 origin-center scale-y-0 group-hover:scale-y-100 transition-transform duration-150 rounded-r-full" />

      <div className="w-9 h-9 rounded-full bg-slate-100 border border-slate-200 flex items-center justify-center shrink-0 text-[11px] font-bold text-slate-600 group-hover:bg-slate-200 transition-colors mt-0.5">
        {personInitials(person.firstname, person.lastname)}
      </div>

      <div className="flex-1 min-w-0">
        <div className="flex items-baseline gap-2 flex-wrap">
          <span className="font-semibold text-[13.5px] text-slate-900 group-hover:text-blue-700 transition-colors leading-snug">
            {name}
          </span>
          <span className={cn("text-[10px] font-semibold px-1.5 py-0.5 rounded-full border uppercase tracking-wide", confidenceCfg)}>
            {person.confidence_level}
          </span>
        </div>
        <div className="flex items-center gap-2 mt-1.5">
          {person.hometown_municipality && (
            <span className="text-[11px] text-slate-400 flex items-center gap-0.5 leading-none">
              <MapPin size={9} className="opacity-50 shrink-0" />
              {person.hometown_municipality}
            </span>
          )}
          <span className={cn(
            "text-[11px] font-semibold leading-none",
            person.active_company_count > 0 ? "text-blue-600" : "text-slate-400"
          )}>
            {person.active_company_count} {person.active_company_count === 1 ? "company" : "companies"}
          </span>
        </div>
      </div>

      <ChevronRight size={13} className="text-slate-300 group-hover:text-blue-400 transition-colors mt-1.5 shrink-0" />
    </Link>
  );
}

function NogaRow({ node, locale }: { node: NogaNode; locale: string }) {
  const label = node.labels?.de || node.labels?.en || node.label;
  const isTopLevel = node.level === "section" || node.level === "0";
  const levelLabel: Record<string, string> = {
    section: "Section", division: "Division", group: "Group", class: "Class", type: "Type",
  };

  return (
    <Link
      href={`/${locale}/app/companies?view=noga`}
      className="group flex items-start gap-3 px-4 py-3.5 border-b border-slate-50 hover:bg-blue-50/50 transition-colors relative"
    >
      <div className="absolute inset-y-0 left-0 w-0.5 bg-blue-500 origin-center scale-y-0 group-hover:scale-y-100 transition-transform duration-150 rounded-r-full" />

      <div className={cn(
        "shrink-0 font-mono text-xs font-bold px-2 py-1.5 rounded-md border min-w-[46px] text-center leading-none mt-0.5",
        isTopLevel
          ? "text-blue-700 bg-blue-50 border-blue-200"
          : "text-slate-600 bg-slate-50 border-slate-200"
      )}>
        {node.code}
      </div>

      <div className="flex-1 min-w-0">
        <span className="font-semibold text-[13.5px] text-slate-900 group-hover:text-blue-700 transition-colors leading-snug block">
          {label}
        </span>
        <div className="flex items-center gap-2 mt-1.5">
          <span className="text-[11px] text-slate-400">{levelLabel[node.level] ?? node.level}</span>
          <span className="text-slate-200">·</span>
          <span className="text-[11px] font-semibold text-slate-500 tabular-nums">
            {node.count.toLocaleString()} companies
          </span>
        </div>
      </div>

      <ChevronRight size={13} className="text-slate-300 group-hover:text-blue-400 transition-colors mt-1.5 shrink-0" />
    </Link>
  );
}

function EmptyState({ title, sub }: { title: string; sub?: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 px-6 text-center">
      <div className="w-11 h-11 rounded-xl bg-slate-100 flex items-center justify-center mb-3 border border-slate-200">
        <Search size={18} className="text-slate-400" />
      </div>
      <p className="text-sm font-medium text-slate-600">{title}</p>
      {sub && <p className="text-xs text-slate-400 mt-1 max-w-xs leading-relaxed">{sub}</p>}
    </div>
  );
}

// ── main component ─────────────────────────────────────────────────────────────

interface SearchLandingClientProps {
  locale: string;
}

export function SearchLandingClient({ locale }: SearchLandingClientProps) {
  const router = useRouter();
  const searchParams = useSearchParams();

  const [query, setQuery] = useState(searchParams?.get("q") ?? "");
  const [submittedQuery, setSubmittedQuery] = useState(searchParams?.get("q") ?? "");
  const [tab, setTab] = useState<Tab>((searchParams?.get("tab") as Tab) || "companies");
  const [hasSearched, setHasSearched] = useState(!!(searchParams?.get("q")));
  const [canton, setCanton] = useState("");
  const [legalForm, setLegalForm] = useState("");
  const [nogaSection, setNogaSection] = useState("");
  const [page, setPage] = useState(1);

  useEffect(() => {
    if (!hasSearched) return;
    const params = new URLSearchParams();
    if (submittedQuery) params.set("q", submittedQuery);
    if (tab !== "companies") params.set("tab", tab);
    const qs = params.toString();
    router.replace(qs ? `/${locale}/app/search?${qs}` : `/${locale}/app/search`, { scroll: false });
  }, [submittedQuery, tab, hasSearched, locale, router]);

  const { data: cantons = [] } = useSWR("cantons", fetchCantons);

  const { data: companiesPage, isLoading: loadingCompanies } = useSWR(
    hasSearched && tab === "companies" && submittedQuery
      ? ["search-landing-companies", submittedQuery, canton, legalForm, nogaSection, page]
      : null,
    () => fetchCompanies({
      q: submittedQuery,
      canton: canton || undefined,
      legal_form: legalForm || undefined,
      noga_code: nogaSection || undefined,
      sort: "name",
      page,
      page_size: 25,
    }),
    { keepPreviousData: true }
  );

  const { data: people = [], isLoading: loadingPeople } = useSWR(
    hasSearched && tab === "people" && submittedQuery
      ? ["search-landing-people", submittedQuery, page]
      : null,
    () => searchPersonEntities({ q: submittedQuery, limit: 25, offset: (page - 1) * 25 }),
    { keepPreviousData: true }
  );

  const { data: nogaHierarchy = [] } = useSWR(
    hasSearched && tab === "noga" ? "noga-hierarchy" : null,
    fetchNogaHierarchy
  );

  const { data: filterNogaHierarchy = [] } = useSWR(
    hasSearched && tab === "companies" ? "noga-hierarchy" : null,
    fetchNogaHierarchy
  );

  const nogaMatches = useMemo(() => {
    if (!submittedQuery || nogaHierarchy.length === 0) return [];
    return flattenNoga(nogaHierarchy).filter(n => matchesNoga(n, submittedQuery)).slice(0, 40);
  }, [submittedQuery, nogaHierarchy]);

  const nogaSections = useMemo(
    () => filterNogaHierarchy.filter(n => n.level === "section"),
    [filterNogaHierarchy]
  );

  function handleSearch(e: React.FormEvent) {
    e.preventDefault();
    if (!query.trim()) return;
    setSubmittedQuery(query.trim());
    setHasSearched(true);
    setPage(1);
  }

  const handleQueryChange = useCallback((v: string) => { setQuery(v); }, []);

  const switchTab = useCallback((t: Tab) => { setTab(t); setPage(1); }, []);

  // ── blank state ─────────────────────────────────────────────────────────────
  if (!hasSearched) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[calc(100vh-5rem)] px-4"
        style={{ background: "radial-gradient(ellipse 80% 50% at 50% 0%, #eff6ff 0%, #f8fafc 55%, #f8fafc 100%)" }}
      >
        {/* Logo — identical to header: blue-600 mark + bold tracking-tight text */}
        <Link
          href={`/${locale}/app/search`}
          className="flex items-center gap-2 text-blue-600 font-bold tracking-tight mb-10 select-none"
        >
          <HelvexMark size={32} className="text-blue-600" />
          <span className="text-2xl">Helvex</span>
        </Link>

        <form onSubmit={handleSearch} className="w-full max-w-xl">
          <div className="relative group">
            <Search
              size={17}
              className="absolute left-4 top-1/2 -translate-y-1/2 text-slate-400 group-focus-within:text-blue-500 transition-colors pointer-events-none"
            />
            <input
              autoFocus
              type="text"
              value={query}
              onChange={e => handleQueryChange(e.target.value)}
              placeholder="Search companies, people, NOGA…"
              className="w-full pl-11 pr-28 py-3.5 text-sm rounded-xl border border-slate-200 bg-white shadow-sm
                focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent
                placeholder:text-slate-400 text-slate-900 transition-shadow"
            />
            <button
              type="submit"
              className="absolute right-2 top-1/2 -translate-y-1/2 flex items-center gap-1.5 bg-blue-600 hover:bg-blue-700 text-white text-xs font-semibold px-3 py-2 rounded-lg transition-colors"
            >
              Search <ArrowRight size={12} />
            </button>
          </div>
        </form>

        <p className="mt-5 text-xs text-slate-400 text-center">
          700,000+ Swiss companies · press <kbd className="px-1.5 py-0.5 bg-white border border-slate-200 rounded text-[10px] font-mono shadow-sm">Enter</kbd> to search
        </p>
      </div>
    );
  }

  // ── active search state ──────────────────────────────────────────────────────
  const TAB_CONFIG = [
    { id: "companies" as Tab, label: "Companies", Icon: Building2,
      count: tab === "companies" ? companiesPage?.total : undefined },
    { id: "people" as Tab, label: "People", Icon: Users,
      count: tab === "people" ? people.length : undefined },
    { id: "noga" as Tab, label: "NOGA", Icon: ListTree,
      count: tab === "noga" ? nogaMatches.length : undefined },
  ];

  return (
    <div className="flex flex-col h-[calc(100vh-5rem)]">
      {/* Search + tab strip */}
      <div className="bg-white border-b border-slate-200 px-4 pt-3 pb-0 shrink-0">
        <form onSubmit={handleSearch} className="relative max-w-2xl mb-3 group">
          <Search
            size={15}
            className="absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400 group-focus-within:text-blue-500 transition-colors pointer-events-none"
          />
          <input
            type="text"
            value={query}
            onChange={e => handleQueryChange(e.target.value)}
            placeholder="Search companies, people, NOGA…"
            className="w-full pl-9 pr-20 py-2.5 text-sm rounded-lg border border-slate-200 bg-white
              focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent
              placeholder:text-slate-400 text-slate-900 transition-shadow"
          />
          <button
            type="submit"
            className="absolute right-1.5 top-1/2 -translate-y-1/2 bg-blue-600 hover:bg-blue-700 text-white text-xs font-semibold px-3 py-1.5 rounded-md transition-colors"
          >
            Search
          </button>
        </form>

        {/* Tabs — underline indicator style */}
        <div className="flex gap-0">
          {TAB_CONFIG.map(({ id, label, Icon, count }) => (
            <button
              key={id}
              onClick={() => switchTab(id)}
              className={cn(
                "flex items-center gap-1.5 px-3.5 py-2.5 text-sm font-medium border-b-2 transition-colors relative -mb-px",
                tab === id
                  ? "border-blue-600 text-blue-700"
                  : "border-transparent text-slate-500 hover:text-slate-800 hover:border-slate-300"
              )}
            >
              <Icon size={13} />
              {label}
              {count != null && count > 0 && (
                <span className={cn(
                  "text-[10px] font-bold px-1.5 py-0.5 rounded-full tabular-nums",
                  tab === id ? "bg-blue-100 text-blue-700" : "bg-slate-100 text-slate-500"
                )}>
                  {count > 999 ? `${Math.floor(count / 1000)}k+` : count}
                </span>
              )}
            </button>
          ))}
        </div>
      </div>

      {/* Body */}
      <div className="flex flex-1 overflow-hidden">
        {/* Filter sidebar — companies tab only */}
        {tab === "companies" && (
          <div className="w-52 shrink-0 border-r border-slate-200 bg-white overflow-y-auto flex flex-col">
            <div className="p-3 border-b border-slate-100">
              <p className="text-[10px] font-semibold text-slate-400 uppercase tracking-widest">Filters</p>
            </div>
            <div className="p-3 space-y-4 flex-1">
              <div>
                <label className="text-[11px] font-semibold text-slate-600 mb-1.5 block">Canton</label>
                <select
                  value={canton}
                  onChange={e => { setCanton(e.target.value); setPage(1); }}
                  className="w-full text-xs border border-slate-200 rounded-md px-2 py-1.5 bg-white text-slate-700 focus:outline-none focus:ring-1 focus:ring-blue-500 focus:border-blue-400 transition-colors"
                >
                  <option value="">All cantons</option>
                  {cantons.map(c => <option key={c} value={c}>{c}</option>)}
                </select>
              </div>

              <div>
                <label className="text-[11px] font-semibold text-slate-600 mb-1.5 block">Legal Form</label>
                <input
                  type="text"
                  value={legalForm}
                  onChange={e => { setLegalForm(e.target.value); setPage(1); }}
                  placeholder="AG, GmbH, …"
                  className="w-full text-xs border border-slate-200 rounded-md px-2 py-1.5 text-slate-700 focus:outline-none focus:ring-1 focus:ring-blue-500 focus:border-blue-400 placeholder:text-slate-400 transition-colors"
                />
              </div>

              <div>
                <label className="text-[11px] font-semibold text-slate-600 mb-1.5 block">NOGA Section</label>
                <select
                  value={nogaSection}
                  onChange={e => { setNogaSection(e.target.value); setPage(1); }}
                  className="w-full text-xs border border-slate-200 rounded-md px-2 py-1.5 bg-white text-slate-700 focus:outline-none focus:ring-1 focus:ring-blue-500 focus:border-blue-400 transition-colors"
                >
                  <option value="">All sections</option>
                  {nogaSections.map(n => (
                    <option key={n.code} value={n.code}>
                      {n.code} – {n.labels?.de || n.label}
                    </option>
                  ))}
                </select>
              </div>

              {(canton || legalForm || nogaSection) && (
                <button
                  onClick={() => { setCanton(""); setLegalForm(""); setNogaSection(""); setPage(1); }}
                  className="text-[11px] text-blue-600 hover:text-blue-800 hover:underline font-medium transition-colors"
                >
                  ✕ Clear filters
                </button>
              )}
            </div>

            <div className="p-3 border-t border-slate-100">
              <Link
                href={`/${locale}/app/companies?view=list${submittedQuery ? `&q=${encodeURIComponent(submittedQuery)}` : ""}`}
                className="flex items-center gap-1 text-[11px] text-blue-600 hover:text-blue-800 font-medium transition-colors group"
              >
                Advanced filters
                <ArrowRight size={10} className="group-hover:translate-x-0.5 transition-transform" />
              </Link>
            </div>
          </div>
        )}

        {/* Results panel */}
        <div className="flex-1 overflow-y-auto bg-white">

          {/* Companies */}
          {tab === "companies" && (
            <>
              {loadingCompanies && !companiesPage && <SkeletonRows />}
              {!loadingCompanies && !submittedQuery && (
                <EmptyState title="Enter a company name above" sub="Press Enter or click Search to see results" />
              )}
              {!loadingCompanies && submittedQuery && companiesPage?.items.length === 0 && (
                <EmptyState
                  title={`No companies found for "${submittedQuery}"`}
                  sub="Try a different name, or remove filters on the left"
                />
              )}
              {companiesPage && companiesPage.total > 0 && (
                <ResultsHeader total={companiesPage.total} query={submittedQuery} />
              )}
              {(companiesPage?.items ?? []).map(c => (
                <CompanyRow key={c.id} company={c} locale={locale} />
              ))}
              <Pagination page={page} total={companiesPage?.total ?? 0} pageSize={25} onPage={setPage} />
            </>
          )}

          {/* People */}
          {tab === "people" && (
            <>
              {loadingPeople && !people.length && <SkeletonRows />}
              {!loadingPeople && !submittedQuery && (
                <EmptyState title="Enter a name above" sub="Press Enter or click Search to see results" />
              )}
              {!loadingPeople && submittedQuery && people.length === 0 && (
                <EmptyState
                  title={`No people found for "${submittedQuery}"`}
                  sub="Try searching by last name"
                />
              )}
              {people.length > 0 && (
                <ResultsHeader total={people.length + (people.length === 25 ? 1 : 0)} query={submittedQuery} />
              )}
              {people.map(p => <PersonRow key={p.id} person={p} locale={locale} />)}
              {people.length === 25 && (
                <div className="px-4 py-3 border-t border-slate-100 sticky bottom-0 bg-white">
                  <button
                    onClick={() => setPage(p => p + 1)}
                    className="text-xs font-medium text-blue-600 hover:text-blue-800 transition-colors flex items-center gap-1"
                  >
                    Load more <ChevronRight size={11} />
                  </button>
                </div>
              )}
            </>
          )}

          {/* NOGA */}
          {tab === "noga" && (
            <>
              {!submittedQuery && (
                <EmptyState title="Search NOGA classifications" sub="Enter a term above and press Enter — e.g. 'Finanz', 'retail', 'agriculture'" />
              )}
              {submittedQuery && nogaHierarchy.length === 0 && <SkeletonRows />}
              {submittedQuery && nogaHierarchy.length > 0 && nogaMatches.length === 0 && (
                <EmptyState
                  title={`No NOGA classes match "${submittedQuery}"`}
                  sub="Try a broader term or a different language"
                />
              )}
              {nogaMatches.length > 0 && (
                <ResultsHeader total={nogaMatches.length} query={submittedQuery} />
              )}
              {nogaMatches.map(n => <NogaRow key={n.code} node={n} locale={locale} />)}
            </>
          )}

        </div>
      </div>
    </div>
  );
}
