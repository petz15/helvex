"use client";

import { useState, useCallback } from "react";
import Link from "next/link";
import {
  Award, Building2, Search, Download, ChevronLeft, ChevronRight,
  SlidersHorizontal, X, ExternalLink,
} from "lucide-react";

interface SimapResult {
  id: number;
  simap_project_id: string;
  source: "api" | "archive";
  publication_date: string | null;
  award_decision_date: string | null;
  title: string;
  authority: string;
  cpv_code: string | null;
  process_type: string | null;
  number_of_submissions: number | null;
  vendor_name: string | null;
  vendor_uid: string | null;
  vendor_city: string | null;
  vendor_postal_code: string | null;
  vendor_country: string | null;
  price: number | null;
  price_currency: string | null;
  company_id: number | null;
  company_name: string | null;
  company_uid: string | null;
}

interface SearchResponse {
  total: number;
  page: number;
  per_page: number;
  pages: number;
  results: SimapResult[];
}

const inputCls = "w-full text-sm border border-slate-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white";
const btnCls = "inline-flex items-center gap-1.5 text-sm font-medium rounded-lg px-3 py-2 transition-colors";

function fmt(n: number | null, currency: string | null): string | null {
  if (n == null || !currency) return null;
  return `${currency.toUpperCase()} ${n.toLocaleString("de-CH", { maximumFractionDigits: 0 })}`;
}

function fmtDate(d: string | null): string {
  if (!d) return "—";
  return new Date(d).toLocaleDateString("de-CH");
}

async function searchSimap(params: URLSearchParams): Promise<SearchResponse> {
  const res = await fetch(`/api/v1/simap/search?${params}`, { credentials: "include" });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export function SimapSearchClient({ locale }: { locale: string }) {
  // Filter state
  const [q, setQ] = useState("");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [cpv, setCpv] = useState("");
  const [minPrice, setMinPrice] = useState("");
  const [maxPrice, setMaxPrice] = useState("");
  const [matchedOnly, setMatchedOnly] = useState(false);
  const [source, setSource] = useState<"" | "api" | "archive">("");
  const [vendorName, setVendorName] = useState("");
  const [showFilters, setShowFilters] = useState(false);

  // Pagination + sort
  const [page, setPage] = useState(1);
  const [perPage] = useState(25);
  const [sortBy, setSortBy] = useState("date");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  // Results
  const [results, setResults] = useState<SearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasSearched, setHasSearched] = useState(false);

  const buildParams = useCallback((overridePage?: number): URLSearchParams => {
    const p = new URLSearchParams();
    if (q.trim()) p.set("q", q.trim());
    if (fromDate) p.set("from_date", fromDate);
    if (toDate) p.set("to_date", toDate);
    if (cpv.trim()) p.set("cpv", cpv.trim());
    if (minPrice) p.set("min_price", minPrice);
    if (maxPrice) p.set("max_price", maxPrice);
    if (matchedOnly) p.set("matched_only", "true");
    if (source) p.set("source", source);
    if (vendorName.trim()) p.set("vendor_name", vendorName.trim());
    p.set("page", String(overridePage ?? page));
    p.set("per_page", String(perPage));
    p.set("sort_by", sortBy);
    p.set("sort_dir", sortDir);
    return p;
  }, [q, fromDate, toDate, cpv, minPrice, maxPrice, matchedOnly, source, vendorName, page, perPage, sortBy, sortDir]);

  const runSearch = useCallback(async (pg = 1) => {
    setLoading(true);
    setError(null);
    setHasSearched(true);
    try {
      const data = await searchSimap(buildParams(pg));
      setResults(data);
      setPage(pg);
    } catch (e: any) {
      setError(e.message || "Search failed");
    } finally {
      setLoading(false);
    }
  }, [buildParams]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    runSearch(1);
  };

  const handleSort = (col: string) => {
    if (sortBy === col) {
      setSortDir(d => d === "asc" ? "desc" : "asc");
    } else {
      setSortBy(col);
      setSortDir("desc");
    }
    runSearch(1);
  };

  const exportCsv = () => {
    const p = buildParams(1);
    p.delete("page");
    p.delete("per_page");
    p.delete("sort_by");
    p.delete("sort_dir");
    window.open(`/api/v1/simap/export.csv?${p}`, "_blank");
  };

  const SortBtn = ({ col, label }: { col: string; label: string }) => (
    <button
      onClick={() => handleSort(col)}
      className="flex items-center gap-0.5 hover:text-blue-600 transition-colors"
    >
      {label}
      {sortBy === col && (
        <span className="text-blue-500 ml-0.5">{sortDir === "asc" ? "↑" : "↓"}</span>
      )}
    </button>
  );

  return (
    <div className="max-w-7xl mx-auto px-4 py-6 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Award size={20} className="text-slate-500" />
          <h1 className="text-xl font-semibold text-slate-800">Public Contracts (SIMAP)</h1>
          {results && (
            <span className="text-sm text-slate-400 ml-1">{results.total.toLocaleString()} results</span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowFilters(f => !f)}
            className={`${btnCls} border ${showFilters ? "bg-blue-50 border-blue-300 text-blue-700" : "border-slate-200 text-slate-600 hover:bg-slate-50"}`}
          >
            <SlidersHorizontal size={14} />
            Filters
          </button>
          {results && results.total > 0 && (
            <button
              onClick={exportCsv}
              className={`${btnCls} border border-slate-200 text-slate-600 hover:bg-slate-50`}
            >
              <Download size={14} />
              CSV
            </button>
          )}
        </div>
      </div>

      {/* Search bar */}
      <form onSubmit={handleSubmit} className="flex gap-2">
        <div className="relative flex-1">
          <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none" />
          <input
            type="text"
            value={q}
            onChange={e => setQ(e.target.value)}
            placeholder="Search contract title or authority… (e.g. Strassenbau, Kanalisation)"
            className={`${inputCls} pl-9`}
          />
          {q && (
            <button type="button" onClick={() => setQ("")} className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600">
              <X size={14} />
            </button>
          )}
        </div>
        <button
          type="submit"
          disabled={loading}
          className={`${btnCls} bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 shrink-0`}
        >
          {loading ? "Searching…" : "Search"}
        </button>
      </form>

      {/* Advanced filters panel */}
      {showFilters && (
        <div className="bg-slate-50 border border-slate-200 rounded-xl p-4 grid grid-cols-2 md:grid-cols-3 gap-3">
          <div>
            <label className="text-xs font-medium text-slate-500 block mb-1">From date</label>
            <input type="date" value={fromDate} onChange={e => setFromDate(e.target.value)} className={inputCls} />
          </div>
          <div>
            <label className="text-xs font-medium text-slate-500 block mb-1">To date</label>
            <input type="date" value={toDate} onChange={e => setToDate(e.target.value)} className={inputCls} />
          </div>
          <div>
            <label className="text-xs font-medium text-slate-500 block mb-1">CPV code prefix</label>
            <input type="text" value={cpv} onChange={e => setCpv(e.target.value)} placeholder="e.g. 451" className={inputCls} />
          </div>
          <div>
            <label className="text-xs font-medium text-slate-500 block mb-1">Min price (CHF)</label>
            <input type="number" value={minPrice} onChange={e => setMinPrice(e.target.value)} placeholder="0" className={inputCls} />
          </div>
          <div>
            <label className="text-xs font-medium text-slate-500 block mb-1">Max price (CHF)</label>
            <input type="number" value={maxPrice} onChange={e => setMaxPrice(e.target.value)} placeholder="unlimited" className={inputCls} />
          </div>
          <div>
            <label className="text-xs font-medium text-slate-500 block mb-1">Vendor name (fuzzy)</label>
            <input type="text" value={vendorName} onChange={e => setVendorName(e.target.value)} placeholder="e.g. Müller Bau" className={inputCls} />
          </div>
          <div>
            <label className="text-xs font-medium text-slate-500 block mb-1">Data source</label>
            <select value={source} onChange={e => setSource(e.target.value as any)} className={inputCls}>
              <option value="">All</option>
              <option value="api">Post-2024 (simap.ch)</option>
              <option value="archive">Pre-2024 archive</option>
            </select>
          </div>
          <div className="flex items-end">
            <label className="flex items-center gap-2 text-sm text-slate-700 cursor-pointer">
              <input
                type="checkbox"
                checked={matchedOnly}
                onChange={e => setMatchedOnly(e.target.checked)}
                className="rounded"
              />
              Matched to our companies only
            </label>
          </div>
        </div>
      )}

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">{error}</div>
      )}

      {/* Results table */}
      {results && results.results.length > 0 ? (
        <div className="bg-white rounded-xl border border-slate-200 overflow-hidden shadow-sm">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 border-b border-slate-200">
                <tr>
                  <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">
                    <SortBtn col="title" label="Contract" />
                  </th>
                  <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider hidden md:table-cell">
                    Authority
                  </th>
                  <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">
                    Vendor
                  </th>
                  <th className="text-right px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">
                    <SortBtn col="price" label="Price" />
                  </th>
                  <th className="text-right px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider hidden lg:table-cell">
                    <SortBtn col="date" label="Date" />
                  </th>
                  <th className="px-4 py-3 hidden lg:table-cell"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {results.results.map((row) => (
                  <tr key={`${row.id}-${row.vendor_name}`} className="hover:bg-slate-50 transition-colors">
                    <td className="px-4 py-3 max-w-xs">
                      <div className="flex items-start gap-1.5">
                        <div>
                          <p className="font-medium text-slate-800 line-clamp-2 leading-snug">{row.title || "—"}</p>
                          {row.cpv_code && (
                            <p className="text-xs text-slate-400 mt-0.5">CPV {row.cpv_code}</p>
                          )}
                        </div>
                      </div>
                    </td>
                    <td className="px-4 py-3 text-slate-600 text-xs max-w-[160px] hidden md:table-cell">
                      <span className="line-clamp-2">{row.authority || "—"}</span>
                    </td>
                    <td className="px-4 py-3">
                      {row.company_id ? (
                        <Link
                          href={`/${locale}/app/companies/${row.company_id}`}
                          className="text-blue-600 hover:underline font-medium text-xs"
                        >
                          {row.vendor_name || row.company_name}
                        </Link>
                      ) : (
                        <span className="text-xs text-slate-600">{row.vendor_name || "—"}</span>
                      )}
                      {row.vendor_city && (
                        <p className="text-xs text-slate-400">{row.vendor_postal_code} {row.vendor_city}</p>
                      )}
                    </td>
                    <td className="px-4 py-3 text-right">
                      {fmt(row.price, row.price_currency) ? (
                        <span className="text-xs font-semibold text-emerald-700">
                          {fmt(row.price, row.price_currency)}
                        </span>
                      ) : (
                        <span className="text-slate-300 text-xs">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-right text-xs text-slate-400 whitespace-nowrap hidden lg:table-cell">
                      {fmtDate(row.publication_date)}
                    </td>
                    <td className="px-4 py-3 hidden lg:table-cell">
                      <div className="flex items-center gap-2 justify-end">
                        {row.source === "archive" ? (
                          <span className="text-xs px-1.5 py-0.5 rounded bg-slate-100 text-slate-500">archive</span>
                        ) : null}
                        {!row.simap_project_id.startsWith("arch-") && (
                          <a
                            href={`https://www.simap.ch/de/project-detail/${row.simap_project_id}`}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-slate-300 hover:text-blue-500 transition-colors"
                            title="View on simap.ch"
                          >
                            <ExternalLink size={13} />
                          </a>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          {results.pages > 1 && (
            <div className="border-t border-slate-100 px-4 py-3 flex items-center justify-between bg-slate-50">
              <p className="text-xs text-slate-500">
                Page {results.page} of {results.pages} ({results.total.toLocaleString()} results)
              </p>
              <div className="flex items-center gap-1">
                <button
                  disabled={results.page <= 1 || loading}
                  onClick={() => runSearch(results.page - 1)}
                  className="p-1.5 rounded hover:bg-slate-100 disabled:opacity-30 transition-colors"
                >
                  <ChevronLeft size={16} />
                </button>
                <button
                  disabled={results.page >= results.pages || loading}
                  onClick={() => runSearch(results.page + 1)}
                  className="p-1.5 rounded hover:bg-slate-100 disabled:opacity-30 transition-colors"
                >
                  <ChevronRight size={16} />
                </button>
              </div>
            </div>
          )}
        </div>
      ) : hasSearched && !loading && results ? (
        <div className="text-center py-16 text-slate-400">
          <Award size={32} className="mx-auto mb-3 opacity-30" />
          <p className="text-sm">No contracts found for this query.</p>
          <p className="text-xs mt-1">Try different keywords or remove filters.</p>
        </div>
      ) : !hasSearched ? (
        <div className="text-center py-16 text-slate-400">
          <Award size={32} className="mx-auto mb-3 opacity-30" />
          <p className="text-sm font-medium">Search 116k+ public procurement contracts</p>
          <p className="text-xs mt-1">
            Covers 2007–2023 (archive) and July 2024–present (live). Vendors linked to company profiles where possible.
          </p>
          <div className="mt-4 flex flex-wrap gap-2 justify-center text-xs text-slate-500">
            {["Strassenbau", "Reinigungsarbeiten", "Softwareentwicklung", "Beratung"].map(ex => (
              <button
                key={ex}
                onClick={() => { setQ(ex); }}
                className="px-2 py-1 border border-slate-200 rounded-full hover:border-blue-400 hover:text-blue-600 transition-colors"
              >
                {ex}
              </button>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}
