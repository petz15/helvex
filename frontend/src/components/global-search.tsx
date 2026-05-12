"use client";

import { useState, useRef, useEffect } from "react";
import Link from "next/link";
import useSWR from "swr";
import { Search, Building2, Users, Shield, ChevronRight, X } from "lucide-react";
import { globalSearch } from "@/lib/api";
import type { GlobalSearchResult } from "@/lib/types";

function useDebounce<T>(value: T, delay: number): T {
  const [debouncedValue, setDebouncedValue] = useState<T>(value);
  useEffect(() => {
    const handler = setTimeout(() => setDebouncedValue(value), delay);
    return () => clearTimeout(handler);
  }, [value, delay]);
  return debouncedValue;
}

function ResultSection({
  title,
  icon,
  seeAllHref,
  onNavigate,
  children,
}: {
  title: string;
  icon: React.ReactNode;
  seeAllHref: string;
  onNavigate: () => void;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="flex items-center justify-between px-4 py-2 bg-slate-50/60 border-b border-slate-100">
        <span className="flex items-center gap-1.5 text-[11px] font-semibold text-slate-500 uppercase tracking-wide">
          {icon}
          {title}
        </span>
        <Link
          href={seeAllHref}
          onClick={onNavigate}
          className="text-[11px] text-blue-600 hover:text-blue-800 transition-colors"
        >
          See all
        </Link>
      </div>
      {children}
    </div>
  );
}

export function GlobalSearchTrigger({ locale }: { locale: string }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const debouncedQ = useDebounce(query, 300);
  const inputRef = useRef<HTMLInputElement>(null);
  const overlayRef = useRef<HTMLDivElement>(null);

  const { data, isLoading } = useSWR<GlobalSearchResult>(
    debouncedQ.length >= 2 ? ["global-search", debouncedQ] : null,
    () => globalSearch(debouncedQ),
    { revalidateOnFocus: false, keepPreviousData: true },
  );

  useEffect(() => {
    if (open) {
      const t = setTimeout(() => inputRef.current?.focus(), 30);
      return () => clearTimeout(t);
    } else {
      setQuery("");
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function onMouseDown(e: MouseEvent) {
      if (overlayRef.current && !overlayRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onMouseDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onMouseDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const close = () => setOpen(false);
  const hasResults = data && (data.companies.length > 0 || data.persons.length > 0 || data.auditors.length > 0);
  const showEmpty = debouncedQ.length >= 2 && !isLoading && !hasResults;

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="hidden md:flex items-center gap-2 px-3 py-1.5 rounded-lg border border-slate-200 bg-slate-50 text-slate-400 text-sm hover:border-slate-300 hover:bg-white transition-colors w-52 shrink-0"
      >
        <Search size={13} />
        <span>Search…</span>
      </button>

      {open && (
        <div className="fixed inset-0 z-50 flex items-start justify-center pt-20 px-4 bg-black/30">
          <div
            ref={overlayRef}
            className="w-full max-w-2xl bg-white rounded-xl shadow-2xl border border-slate-200 overflow-hidden"
          >
            {/* Input */}
            <div className="flex items-center gap-2 px-4 py-3 border-b border-slate-100">
              <Search size={15} className="text-slate-400 shrink-0" />
              <input
                ref={inputRef}
                type="text"
                placeholder="Search companies, people, auditors…"
                value={query}
                onChange={e => setQuery(e.target.value)}
                className="flex-1 text-sm text-slate-800 placeholder:text-slate-400 focus:outline-none bg-transparent"
              />
              {query ? (
                <button onClick={() => setQuery("")} className="text-slate-400 hover:text-slate-600 transition-colors">
                  <X size={14} />
                </button>
              ) : (
                <span className="text-[11px] text-slate-300 select-none">Esc to close</span>
              )}
            </div>

            {/* Body */}
            {debouncedQ.length < 2 ? (
              <div className="px-4 py-8 text-center text-sm text-slate-400">
                Type at least 2 characters…
              </div>
            ) : isLoading ? (
              <div className="space-y-2 p-4">
                {[0, 1, 2].map(i => (
                  <div key={i} className="space-y-1">
                    <div className="h-2.5 w-16 bg-slate-100 rounded animate-pulse" />
                    {[0, 1].map(j => (
                      <div key={j} className="h-9 bg-slate-50 rounded animate-pulse" />
                    ))}
                  </div>
                ))}
              </div>
            ) : showEmpty ? (
              <div className="px-4 py-10 text-center text-sm text-slate-400">
                No results for &ldquo;{debouncedQ}&rdquo;
              </div>
            ) : data ? (
              <div className="max-h-[60vh] overflow-y-auto divide-y divide-slate-100">
                {data.companies.length > 0 && (
                  <ResultSection
                    title="Companies"
                    icon={<Building2 size={11} />}
                    seeAllHref={`/${locale}/app/search?q=${encodeURIComponent(debouncedQ)}`}
                    onNavigate={close}
                  >
                    {data.companies.map(c => (
                      <Link
                        key={c.id}
                        href={`/${locale}/app/companies/${c.id}`}
                        onClick={close}
                        className="flex items-center gap-3 px-4 py-2.5 hover:bg-slate-50 transition-colors group"
                      >
                        <div className="flex-1 min-w-0">
                          <p className="text-sm font-medium text-slate-800 truncate">{c.name}</p>
                          <p className="text-[11px] text-slate-400 truncate">
                            {[c.canton, c.legal_form, c.status].filter(Boolean).join(" · ")}
                          </p>
                        </div>
                        <ChevronRight size={12} className="text-slate-300 shrink-0 group-hover:text-slate-500 transition-colors" />
                      </Link>
                    ))}
                  </ResultSection>
                )}

                {data.persons.length > 0 && (
                  <ResultSection
                    title="People"
                    icon={<Users size={11} />}
                    seeAllHref={`/${locale}/app/people?q=${encodeURIComponent(debouncedQ)}`}
                    onNavigate={close}
                  >
                    {data.persons.map(p => {
                      const name = [p.firstname, p.lastname].filter(Boolean).join(" ");
                      return (
                        <Link
                          key={p.id}
                          href={`/${locale}/app/people?q=${encodeURIComponent(name)}`}
                          onClick={close}
                          className="flex items-center gap-3 px-4 py-2.5 hover:bg-slate-50 transition-colors group"
                        >
                          <div className="flex-1 min-w-0">
                            <p className="text-sm font-medium text-slate-800 truncate">{name}</p>
                            <p className="text-[11px] text-slate-400 truncate">
                              {[
                                p.hometown_municipality ? `von ${p.hometown_municipality}` : null,
                                `${p.active_company_count} ${p.active_company_count === 1 ? "company" : "companies"}`,
                                p.confidence_level,
                              ]
                                .filter(Boolean)
                                .join(" · ")}
                            </p>
                          </div>
                          <ChevronRight size={12} className="text-slate-300 shrink-0 group-hover:text-slate-500 transition-colors" />
                        </Link>
                      );
                    })}
                  </ResultSection>
                )}

                {data.auditors.length > 0 && (
                  <ResultSection
                    title="Auditors"
                    icon={<Shield size={11} />}
                    seeAllHref={`/${locale}/app/people?tab=auditors&q=${encodeURIComponent(debouncedQ)}`}
                    onNavigate={close}
                  >
                    {data.auditors.map(a => (
                      <Link
                        key={a.key}
                        href={`/${locale}/app/people?tab=auditors&q=${encodeURIComponent(a.name)}`}
                        onClick={close}
                        className="flex items-center gap-3 px-4 py-2.5 hover:bg-slate-50 transition-colors group"
                      >
                        <div className="flex-1 min-w-0">
                          <p className="text-sm font-medium text-slate-800 truncate">{a.name}</p>
                          <p className="text-[11px] text-slate-400 truncate">
                            {[a.location, `${a.client_count} ${a.client_count === 1 ? "client" : "clients"}`]
                              .filter(Boolean)
                              .join(" · ")}
                          </p>
                        </div>
                        <ChevronRight size={12} className="text-slate-300 shrink-0 group-hover:text-slate-500 transition-colors" />
                      </Link>
                    ))}
                  </ResultSection>
                )}
              </div>
            ) : null}
          </div>
        </div>
      )}
    </>
  );
}
