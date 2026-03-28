"use client";
import Link from "next/link";
import useSWR from "swr";
import { ExternalLink, MapPin, Building2, FileText } from "lucide-react";
import { SogcTimeline, SignersPanel } from "@/components/sogc-history";
import { Badge } from "@/components/ui/badge";
import { scoreColor, cn } from "@/lib/utils";
import type { Company } from "@/lib/types";

async function fetchDemoCompany(): Promise<Company> {
  const res = await fetch("/api/v1/companies/demo");
  if (!res.ok) throw new Error("Demo company unavailable");
  return res.json();
}

function ScorePill({ label, value }: { label: string; value: number | null }) {
  if (value == null) return null;
  return (
    <div className="flex flex-col items-center gap-0.5 min-w-[52px]">
      <span className={cn("text-xl font-bold tabular-nums", scoreColor(value))}>{value}</span>
      <span className="text-[10px] text-slate-400 uppercase tracking-wide">{label}</span>
    </div>
  );
}

export default function DemoClient() {
  const { data: company, isLoading, error } = useSWR("demo-company", fetchDemoCompany, {
    shouldRetryOnError: false,
  });

  return (
    <div className="max-w-4xl mx-auto px-4 py-6">
      {/* Sign-up banner */}
      <div className="mb-6 rounded-xl bg-blue-50 border border-blue-200 px-4 py-3 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-blue-800">You&apos;re viewing a guest demo</p>
          <p className="text-xs text-blue-600 mt-0.5">
            Sign up free to search and track all 700,000+ Swiss registered companies.
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <Link
            href="/login"
            className="px-3 py-1.5 rounded-lg border border-blue-300 text-sm text-blue-700 hover:bg-blue-100 transition-colors"
          >
            Sign in
          </Link>
          <Link
            href="/register"
            className="px-3 py-1.5 rounded-lg bg-blue-600 text-white text-sm font-medium hover:bg-blue-700 transition-colors"
          >
            Sign up free →
          </Link>
        </div>
      </div>

      {isLoading && (
        <div className="text-center py-20 text-slate-400 text-sm">Loading company data…</div>
      )}

      {error && (
        <div className="text-center py-20 text-slate-400 text-sm">
          Demo data is currently unavailable.
        </div>
      )}

      {company && (
        <div className="space-y-4">
          {/* Header */}
          <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm border-l-4 border-l-green-500">
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <h1 className="text-xl font-bold text-slate-900 truncate">{company.name}</h1>
                <div className="flex flex-wrap items-center gap-1.5 mt-1.5">
                  <span className="font-mono text-xs bg-slate-100 text-slate-500 px-2 py-0.5 rounded">
                    {company.uid}
                  </span>
                  {company.status && (
                    <Badge
                      className={
                        company.status === "ACTIVE"
                          ? "bg-emerald-100 text-emerald-700"
                          : "bg-red-50 text-red-600"
                      }
                    >
                      {company.status}
                    </Badge>
                  )}
                  {company.legal_form && (
                    <Badge className="bg-slate-100 text-slate-500">{company.legal_form}</Badge>
                  )}
                </div>
              </div>

              {/* Score pills */}
              <div className="flex items-center gap-4 shrink-0">
                <ScorePill label="Register" value={company.flex_score} />
                <ScorePill label="Web" value={company.web_score} />
                <ScorePill label="AI" value={company.ai_score} />
              </div>
            </div>
          </div>

          {/* Details grid */}
          <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
            <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-8 gap-y-3 text-sm">
              {company.municipality && (
                <div className="flex items-start gap-2">
                  <MapPin size={14} className="text-slate-400 mt-0.5 shrink-0" />
                  <div>
                    <dt className="text-xs text-slate-400 mb-0.5">Seat</dt>
                    <dd className="text-slate-700 font-medium">
                      {company.municipality}{company.canton ? `, ${company.canton}` : ""}
                    </dd>
                  </div>
                </div>
              )}
              {(company.capital_nominal || company.capital_currency) && (
                <div className="flex items-start gap-2">
                  <Building2 size={14} className="text-slate-400 mt-0.5 shrink-0" />
                  <div>
                    <dt className="text-xs text-slate-400 mb-0.5">Capital</dt>
                    <dd className="text-slate-700 font-medium">
                      {[company.capital_currency, company.capital_nominal].filter(Boolean).join(" ")}
                    </dd>
                  </div>
                </div>
              )}
              {company.website_url && (
                <div className="flex items-start gap-2">
                  <ExternalLink size={14} className="text-slate-400 mt-0.5 shrink-0" />
                  <div>
                    <dt className="text-xs text-slate-400 mb-0.5">Website</dt>
                    <dd>
                      <a
                        href={company.website_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-blue-600 hover:underline text-sm"
                      >
                        {company.website_url.replace(/^https?:\/\//, "")}
                      </a>
                    </dd>
                  </div>
                </div>
              )}
              {company.ai_category && (
                <div className="flex items-start gap-2">
                  <Building2 size={14} className="text-slate-400 mt-0.5 shrink-0" />
                  <div>
                    <dt className="text-xs text-slate-400 mb-0.5">AI Category</dt>
                    <dd className="text-slate-700 font-medium">{company.ai_category}</dd>
                  </div>
                </div>
              )}
            </dl>

            {company.purpose && (
              <div className="mt-4 pt-4 border-t border-slate-100">
                <div className="flex items-start gap-2">
                  <FileText size={14} className="text-slate-400 mt-0.5 shrink-0" />
                  <div>
                    <dt className="text-xs text-slate-400 mb-1">Purpose</dt>
                    <dd className="text-sm text-slate-600 leading-relaxed">{company.purpose}</dd>
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* SHAB signers + timeline */}
          <SignersPanel sogcPubJson={company.sogc_pub} />
          <SogcTimeline sogcPubJson={company.sogc_pub} />

          {/* Bottom CTA */}
          <div className="text-center py-8">
            <p className="text-slate-500 text-sm mb-3">
              Want to search all 700,000+ Swiss companies?
            </p>
            <Link
              href="/register"
              className="inline-block px-5 py-2.5 rounded-lg bg-blue-600 text-white font-semibold text-sm hover:bg-blue-700 transition-colors"
            >
              Create your free account →
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}
