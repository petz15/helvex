"use client";
import { useState, useCallback, useEffect, useMemo } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import useSWR from "swr";
import { Search, Building2, Users, ListTree, ChevronRight, Loader2, ChevronLeft } from "lucide-react";
import { fetchCompanies, fetchCantons, fetchNogaHierarchy, searchPersonEntities } from "@/lib/api";
import type { NogaNode } from "@/lib/api";
import type { Company, SogcPersonEntity } from "@/lib/types";
import { HelvexMark } from "@/components/helvex-logo";
import { cn } from "@/lib/utils";

type Tab = "companies" | "people" | "noga";

function useDebounce<T>(value: T, delay: number): T {
  const [dv, setDv] = useState<T>(value);
  useEffect(() => {
    const h = setTimeout(() => setDv(value), delay);
    return () => clearTimeout(h);
  }, [value, delay]);
  return dv;
}

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

function Pagination({ page, total, pageSize, onPage }: { page: number; total: number; pageSize: number; onPage: (p: number) => void }) {
  const totalPages = Math.ceil(total / pageSize);
  if (totalPages <= 1) return null;
  return (
    <div className="flex items-center gap-2 px-4 py-3 border-t border-slate-100 text-sm">
      <button
        onClick={() => onPage(page - 1)}
        disabled={page <= 1}
        className="flex items-center gap-1 px-2 py-1 rounded text-slate-600 hover:bg-slate-100 disabled:opacity-40 disabled:cursor-not-allowed"
      >
        <ChevronLeft size={14} /> Prev
      </button>
      <span className="text-slate-500 text-xs">{page} / {totalPages} ({total.toLocaleString()} results)</span>
      <button
        onClick={() => onPage(page + 1)}
        disabled={page >= totalPages}
        className="flex items-center gap-1 px-2 py-1 rounded text-slate-600 hover:bg-slate-100 disabled:opacity-40 disabled:cursor-not-allowed"
      >
        Next <ChevronRight size={14} />
      </button>
    </div>
  );
}

function CompanyRow({ company, locale }: { company: Company; locale: string }) {
  return (
    <Link
      href={`/${locale}/app/companies/${company.id}`}
      className="flex items-center gap-3 px-4 py-3 border-b border-slate-50 hover:bg-slate-50 transition-colors group"
    >
      <div className="flex-1 min-w-0">
        <div className="font-medium text-slate-900 text-sm truncate group-hover:text-blue-700">{company.name}</div>
        <div className="text-xs text-slate-500 mt-0.5 flex items-center gap-2">
          {company.canton && <span>{company.canton}</span>}
          {company.legal_form && <span>· {company.legal_form}</span>}
          {company.status && company.status !== "ACTIVE" && (
            <span className="text-amber-600">· {company.status}</span>
          )}
        </div>
      </div>
      <ChevronRight size={14} className="text-slate-300 group-hover:text-slate-500 shrink-0" />
    </Link>
  );
}

function PersonRow({ person, locale }: { person: SogcPersonEntity; locale: string }) {
  const name = [person.firstname, person.lastname].filter(Boolean).join(" ") || "Unknown";
  return (
    <Link
      href={`/${locale}/app/people/${person.id}`}
      className="flex items-center gap-3 px-4 py-3 border-b border-slate-50 hover:bg-slate-50 transition-colors group"
    >
      <div className="flex-1 min-w-0">
        <div className="font-medium text-slate-900 text-sm truncate group-hover:text-blue-700">{name}</div>
        <div className="text-xs text-slate-500 mt-0.5 flex items-center gap-2">
          {person.hometown_municipality && <span>{person.hometown_municipality}</span>}
          <span>· {person.active_company_count} active {person.active_company_count === 1 ? "company" : "companies"}</span>
          <span className={cn(
            "capitalize",
            person.confidence_level === "high" ? "text-green-600" :
            person.confidence_level === "medium" ? "text-amber-600" : "text-slate-400"
          )}>{person.confidence_level}</span>
        </div>
      </div>
      <ChevronRight size={14} className="text-slate-300 group-hover:text-slate-500 shrink-0" />
    </Link>
  );
}

function NogaRow({ node, locale }: { node: NogaNode; locale: string }) {
  const label = node.labels?.de || node.labels?.en || node.label;
  return (
    <Link
      href={`/${locale}/app/companies?view=noga`}
      className="flex items-center gap-3 px-4 py-3 border-b border-slate-50 hover:bg-slate-50 transition-colors group"
    >
      <div className="flex-1 min-w-0">
        <div className="font-medium text-slate-900 text-sm truncate group-hover:text-blue-700">
          <span className="font-mono text-xs text-slate-400 mr-2">{node.code}</span>
          {label}
        </div>
        <div className="text-xs text-slate-500 mt-0.5">
          {node.count.toLocaleString()} companies · level {node.level}
        </div>
      </div>
      <ChevronRight size={14} className="text-slate-300 group-hover:text-slate-500 shrink-0" />
    </Link>
  );
}

function EmptyState({ message }: { message: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-20 text-slate-400">
      <Search size={32} className="mb-3 opacity-40" />
      <p className="text-sm">{message}</p>
    </div>
  );
}

interface SearchLandingClientProps {
  locale: string;
}

export function SearchLandingClient({ locale }: SearchLandingClientProps) {
  const router = useRouter();
  const searchParams = useSearchParams();

  const [query, setQuery] = useState(searchParams?.get("q") ?? "");
  const [tab, setTab] = useState<Tab>((searchParams?.get("tab") as Tab) || "companies");
  const [hasSearched, setHasSearched] = useState(!!(searchParams?.get("q")));
  const [canton, setCanton] = useState("");
  const [legalForm, setLegalForm] = useState("");
  const [nogaSection, setNogaSection] = useState("");
  const [page, setPage] = useState(1);

  const debouncedQuery = useDebounce(query, 300);

  useEffect(() => {
    if (!hasSearched) return;
    const params = new URLSearchParams();
    if (debouncedQuery) params.set("q", debouncedQuery);
    if (tab !== "companies") params.set("tab", tab);
    const qs = params.toString();
    router.replace(qs ? `/${locale}/app/search?${qs}` : `/${locale}/app/search`, { scroll: false });
  }, [debouncedQuery, tab, hasSearched, locale, router]);

  const { data: cantons = [] } = useSWR("cantons", fetchCantons);

  const { data: companiesPage, isLoading: loadingCompanies } = useSWR(
    hasSearched && tab === "companies" && debouncedQuery
      ? ["search-landing-companies", debouncedQuery, canton, legalForm, nogaSection, page]
      : null,
    () => fetchCompanies({
      q: debouncedQuery,
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
    hasSearched && tab === "people" && debouncedQuery
      ? ["search-landing-people", debouncedQuery, page]
      : null,
    () => searchPersonEntities({ q: debouncedQuery, limit: 25, offset: (page - 1) * 25 }),
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
    if (!debouncedQuery || nogaHierarchy.length === 0) return [];
    return flattenNoga(nogaHierarchy).filter(n => matchesNoga(n, debouncedQuery)).slice(0, 40);
  }, [debouncedQuery, nogaHierarchy]);

  const nogaSections = useMemo(
    () => filterNogaHierarchy.filter(n => n.level === "section"),
    [filterNogaHierarchy]
  );

  function handleSearch(e: React.FormEvent) {
    e.preventDefault();
    if (!query.trim()) return;
    setHasSearched(true);
    setPage(1);
  }

  const handleQueryChange = useCallback((v: string) => {
    setQuery(v);
    if (v.length >= 2) setHasSearched(true);
    setPage(1);
  }, []);

  const switchTab = useCallback((t: Tab) => {
    setTab(t);
    setPage(1);
  }, []);

  if (!hasSearched) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[calc(100vh-5rem)] bg-gradient-to-b from-white to-slate-50 px-4">
        <div className="flex items-center gap-3 mb-8">
          <HelvexMark size={40} />
          <span className="text-3xl font-bold text-slate-900 tracking-tight">Helvex</span>
        </div>
        <form onSubmit={handleSearch} className="w-full max-w-2xl">
          <div className="relative">
            <Search className="absolute left-4 top-1/2 -translate-y-1/2 text-slate-400" size={20} />
            <input
              autoFocus
              type="text"
              value={query}
              onChange={e => handleQueryChange(e.target.value)}
              placeholder="Search companies, people, NOGA…"
              className="w-full pl-12 pr-4 py-4 text-lg rounded-xl border border-slate-200 shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent bg-white"
            />
          </div>
        </form>
        <p className="mt-4 text-sm text-slate-500">700k+ Swiss companies from the commercial register</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-[calc(100vh-5rem)]">
      {/* Search bar + tabs */}
      <div className="bg-white border-b border-slate-200 px-4 py-3 shrink-0">
        <form onSubmit={handleSearch} className="relative max-w-2xl mb-3">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={16} />
          <input
            type="text"
            value={query}
            onChange={e => handleQueryChange(e.target.value)}
            placeholder="Search companies, people, NOGA…"
            className="w-full pl-9 pr-4 py-2 text-sm rounded-lg border border-slate-200 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
          />
        </form>
        <div className="flex gap-1">
          {(["companies", "people", "noga"] as Tab[]).map((id) => {
            const Icon = id === "companies" ? Building2 : id === "people" ? Users : ListTree;
            const label = id === "companies" ? "Companies" : id === "people" ? "People" : "NOGA classes";
            return (
              <button
                key={id}
                onClick={() => switchTab(id)}
                className={cn(
                  "flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium transition-colors",
                  tab === id ? "bg-blue-600 text-white" : "text-slate-600 hover:bg-slate-100"
                )}
              >
                <Icon size={14} />
                {label}
              </button>
            );
          })}
        </div>
      </div>

      {/* Body */}
      <div className="flex flex-1 overflow-hidden">
        {/* Filter sidebar — companies tab only */}
        {tab === "companies" && (
          <div className="w-52 shrink-0 border-r border-slate-200 bg-white overflow-y-auto p-3 space-y-4">
            <div>
              <label className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1.5 block">Canton</label>
              <select
                value={canton}
                onChange={e => { setCanton(e.target.value); setPage(1); }}
                className="w-full text-sm border border-slate-200 rounded-md px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-blue-500 bg-white"
              >
                <option value="">All cantons</option>
                {cantons.map(c => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1.5 block">Legal Form</label>
              <input
                type="text"
                value={legalForm}
                onChange={e => { setLegalForm(e.target.value); setPage(1); }}
                placeholder="e.g. AG, GmbH"
                className="w-full text-sm border border-slate-200 rounded-md px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-blue-500"
              />
            </div>
            <div>
              <label className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1.5 block">NOGA Section</label>
              <select
                value={nogaSection}
                onChange={e => { setNogaSection(e.target.value); setPage(1); }}
                className="w-full text-sm border border-slate-200 rounded-md px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-blue-500 bg-white"
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
                className="text-xs text-blue-600 hover:underline"
              >
                Clear filters
              </button>
            )}
            <div className="pt-2 border-t border-slate-100">
              <Link
                href={`/${locale}/app/companies?view=list${debouncedQuery ? `&q=${encodeURIComponent(debouncedQuery)}` : ""}`}
                className="text-xs text-blue-600 hover:underline flex items-center gap-1"
              >
                Full filter view <ChevronRight size={10} />
              </Link>
            </div>
          </div>
        )}

        {/* Results */}
        <div className="flex-1 overflow-y-auto">
          {tab === "companies" && (
            <>
              {loadingCompanies && !companiesPage && (
                <div className="flex justify-center py-12">
                  <Loader2 size={24} className="animate-spin text-slate-400" />
                </div>
              )}
              {!loadingCompanies && !debouncedQuery && (
                <EmptyState message="Type a company name to search" />
              )}
              {!loadingCompanies && debouncedQuery && companiesPage?.items.length === 0 && (
                <EmptyState message={`No companies found for "${debouncedQuery}"`} />
              )}
              {(companiesPage?.items ?? []).map(c => (
                <CompanyRow key={c.id} company={c} locale={locale} />
              ))}
              <Pagination
                page={page}
                total={companiesPage?.total ?? 0}
                pageSize={25}
                onPage={setPage}
              />
            </>
          )}

          {tab === "people" && (
            <>
              {loadingPeople && !people.length && (
                <div className="flex justify-center py-12">
                  <Loader2 size={24} className="animate-spin text-slate-400" />
                </div>
              )}
              {!loadingPeople && !debouncedQuery && (
                <EmptyState message="Type a name to search people" />
              )}
              {!loadingPeople && debouncedQuery && people.length === 0 && (
                <EmptyState message={`No people found for "${debouncedQuery}"`} />
              )}
              {people.map(p => <PersonRow key={p.id} person={p} locale={locale} />)}
              {people.length === 25 && (
                <div className="flex items-center gap-2 px-4 py-3 border-t border-slate-100 text-sm">
                  <button
                    onClick={() => setPage(p => p + 1)}
                    className="px-3 py-1 rounded text-slate-600 hover:bg-slate-100 text-sm"
                  >
                    Load more
                  </button>
                </div>
              )}
            </>
          )}

          {tab === "noga" && (
            <>
              {!debouncedQuery && <EmptyState message="Type to search NOGA classifications" />}
              {debouncedQuery && nogaHierarchy.length === 0 && (
                <div className="flex justify-center py-12">
                  <Loader2 size={24} className="animate-spin text-slate-400" />
                </div>
              )}
              {debouncedQuery && nogaHierarchy.length > 0 && nogaMatches.length === 0 && (
                <EmptyState message={`No NOGA classes found for "${debouncedQuery}"`} />
              )}
              {nogaMatches.map(n => <NogaRow key={n.code} node={n} locale={locale} />)}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
