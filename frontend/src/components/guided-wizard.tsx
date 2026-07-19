"use client";

import { useState, useEffect, useCallback, useMemo } from "react";
import {
  Building2, Users, Briefcase, X, MapPin, ListTree, SlidersHorizontal,
} from "lucide-react";
import useSWR from "swr";
import { cn } from "@/lib/utils";
import { fetchCompanies } from "@/lib/api";
import type { NogaNode } from "@/lib/api";
import type { CompanyFilters } from "@/lib/types";

// ── Types ─────────────────────────────────────────────────────────────────────

type Scope = "companies" | "people" | "jobs";
type SizeBucket = "" | "small" | "medium" | "large";

interface WizardState {
  step: number;
  scope: Scope;
  cantons: string[];
  allSwitzerland: boolean;
  industryText: string;
  nogaCodes: string[];
  legalForm: string;
  sizeBucket: SizeBucket;
  roleCategories: string[];
  minCompanies: number;
}

export type WizardFilters = CompanyFilters & {
  role_category?: string;
  min_total_companies?: number;
};

export interface GuidedWizardProps {
  open: boolean;
  initialStep?: number;
  initialScope?: Scope;
  locale: string;
  nogaHierarchy: NogaNode[];
  cantons: string[];
  onComplete: (filters: WizardFilters, scope: string) => void;
  onClose: () => void;
}

// ── Constants ─────────────────────────────────────────────────────────────────

const POPULAR_INDUSTRIES = [
  { label: "Restaurants & food", code: "56" },
  { label: "IT & Software", code: "62" },
  { label: "Construction", code: "41" },
  { label: "Healthcare", code: "86" },
  { label: "Retail trade", code: "47" },
  { label: "Consulting", code: "702" },
  { label: "Real estate", code: "68" },
  { label: "Manufacturing", code: "10" },
  { label: "Finance & insurance", code: "64" },
];

const LEGAL_FORMS = ["AG", "GmbH", "Einzelfirma", "SA", "Sàrl"];

const SIZE_OPTIONS: Array<{ label: string; value: SizeBucket }> = [
  { label: "Any", value: "" },
  { label: "Small 1–9", value: "small" },
  { label: "Medium 10–249", value: "medium" },
  { label: "Large 250+", value: "large" },
];

const ROLE_CATEGORIES = [
  { label: "Director", value: "director" },
  { label: "Officer", value: "officer" },
];

const MIN_COMPANIES_OPTIONS = [
  { label: "Any", value: 0 },
  { label: "1+", value: 1 },
  { label: "3+", value: 3 },
  { label: "5+", value: 5 },
];

function stepLabels(scope: Scope): string[] {
  return scope === "people"
    ? ["WHAT", "WHERE", "ROLE", "REFINE", "REVIEW"]
    : ["WHAT", "WHERE", "INDUSTRY", "REFINE", "REVIEW"];
}
function stepQuestions(scope: Scope): string[] {
  return scope === "people"
    ? ["What are you looking for?", "Where in Switzerland?", "Role & involvement", "Refine your search", "Your search summary"]
    : ["What are you looking for?", "Where in Switzerland?", "Which industry?", "Refine your search", "Your search summary"];
}
function stepSubs(scope: Scope): string[] {
  return scope === "people"
    ? [
        "Choose the type of result you want to find.",
        "Select a canton or keep all of Switzerland.",
        "Filter by role and how many companies they've been linked to.",
        "Optional — narrow by legal form or company size.",
        "These become editable filters in the People view.",
      ]
    : [
        "Choose the type of result you want to find.",
        "Select a canton or keep all of Switzerland.",
        "Type a keyword or pick a popular category.",
        "Optional — narrow by legal form or company size.",
        "These become editable filters in the Companies view.",
      ];
}

// ── Helpers ───────────────────────────────────────────────────────────────────

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

function wizardToFilters(state: WizardState): WizardFilters {
  if (state.scope === "people") {
    return {
      canton: state.allSwitzerland ? undefined : (state.cantons.join(",") || undefined),
      role_category: state.roleCategories.join(",") || undefined,
      min_total_companies: state.minCompanies > 0 ? state.minCompanies : undefined,
      page: 1,
      page_size: 50,
    };
  }
  return {
    canton: state.allSwitzerland ? undefined : (state.cantons.join(",") || undefined),
    noga_code: state.nogaCodes.join(",") || undefined,
    legal_form: state.legalForm || undefined,
    sort: "-combined_score",
    page: 1,
    page_size: 50,
  };
}

// ── Sub-components ────────────────────────────────────────────────────────────

function Chip({ label, selected, onClick }: {
  label: string; selected: boolean; onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "px-3 py-1.5 rounded-full text-[13px] font-medium transition-colors shrink-0",
        selected
          ? "bg-[#eff4fe] border-[1.5px] border-blue-600 text-blue-700"
          : "bg-white border border-[#e6e8ec] text-[#3f4854] hover:bg-slate-50 hover:border-slate-300"
      )}
    >
      {label}
    </button>
  );
}

function ScopeTile({ icon: Icon, title, sub, selected, onClick }: {
  icon: React.ComponentType<{ size?: number; className?: string }>;
  title: string;
  sub: string;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex-1 flex flex-col items-center gap-3 p-5 rounded-2xl transition-all text-center",
        selected
          ? "bg-[#eff4fe] border-[1.5px] border-blue-600 shadow-[0_6px_20px_rgba(37,99,235,0.07)]"
          : "bg-white border border-[#e6e8ec] hover:border-slate-300 hover:bg-slate-50"
      )}
    >
      <div className={cn(
        "w-11 h-11 rounded-xl flex items-center justify-center",
        selected ? "bg-blue-600 text-white" : "bg-[#eff4fe] text-blue-600"
      )}>
        <Icon size={22} />
      </div>
      <div>
        <p className="text-[17px] font-semibold text-[#1f2733] leading-tight">{title}</p>
        <p className="text-[13px] text-[#6b7480] mt-0.5">{sub}</p>
      </div>
    </button>
  );
}

function ReviewRow({ icon: Icon, label, value, onEdit }: {
  icon: React.ComponentType<{ size?: number; className?: string }>;
  label: string;
  value: string;
  onEdit: () => void;
}) {
  return (
    <div className="flex items-center gap-3 px-4 py-3 rounded-xl border border-[#e6e8ec] bg-white">
      <div className="w-8 h-8 rounded-lg bg-[#eff4fe] flex items-center justify-center shrink-0">
        <Icon size={15} className="text-blue-600" />
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-[11px] font-bold text-[#6b7480] uppercase tracking-[0.06em]">{label}</p>
        <p className="text-[13px] font-medium text-[#1f2733] leading-snug truncate">{value}</p>
      </div>
      <button
        type="button"
        onClick={onEdit}
        className="text-[12px] font-medium text-blue-600 hover:text-blue-800 transition-colors shrink-0"
      >
        Edit
      </button>
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export function GuidedWizard({
  open,
  initialStep = 0,
  initialScope = "companies",
  nogaHierarchy,
  cantons,
  onComplete,
  onClose,
}: GuidedWizardProps) {
  const [state, setState] = useState<WizardState>({
    step: initialStep,
    scope: initialScope,
    cantons: [],
    allSwitzerland: true,
    industryText: "",
    nogaCodes: [],
    legalForm: "",
    sizeBucket: "",
    roleCategories: [],
    minCompanies: 0,
  });

  const [typeaheadQuery, setTypeaheadQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");


  // Debounce typeahead
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQuery(typeaheadQuery), 250);
    return () => clearTimeout(t);
  }, [typeaheadQuery]);

  // Esc to close
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", h);
    return () => document.removeEventListener("keydown", h);
  }, [open, onClose]);

  const flatNodes = useMemo(() => flattenNoga(nogaHierarchy), [nogaHierarchy]);

  const nogaLabelMap = useMemo(() => {
    const m: Record<string, string> = {};
    flatNodes.forEach(n => { m[n.code] = n.labels?.de || n.labels?.en || n.label; });
    return m;
  }, [flatNodes]);

  const typeaheadResults = useMemo(() => {
    if (!debouncedQuery.trim()) return [];
    return flatNodes
      .filter(n => matchesNoga(n, debouncedQuery))
      .filter(n => !state.nogaCodes.includes(n.code))
      .slice(0, 6);
  }, [debouncedQuery, flatNodes, state.nogaCodes]);

  // Live result count for review step (companies/jobs only — people count isn't a cheap fetch here)
  const reviewFilters = useMemo(() => wizardToFilters(state), [state]);
  const { data: countPage } = useSWR(
    state.step === 4 && state.scope !== "people" ? ["wizard-count", JSON.stringify(reviewFilters)] : null,
    () => fetchCompanies({ ...reviewFilters, page_size: 1 }),
    { keepPreviousData: true }
  );

  const toggleNogaCode = useCallback((code: string) => {
    setState(s => ({
      ...s,
      nogaCodes: s.nogaCodes.includes(code)
        ? s.nogaCodes.filter(c => c !== code)
        : [...s.nogaCodes, code],
    }));
  }, []);

  const toggleCanton = useCallback((c: string) => {
    setState(s => {
      const next = s.cantons.includes(c)
        ? s.cantons.filter(x => x !== c)
        : [...s.cantons, c];
      return { ...s, allSwitzerland: next.length === 0, cantons: next };
    });
  }, []);

  const toggleRoleCategory = useCallback((value: string) => {
    setState(s => ({
      ...s,
      roleCategories: s.roleCategories.includes(value)
        ? s.roleCategories.filter(c => c !== value)
        : [...s.roleCategories, value],
    }));
  }, []);

  const handleContinue = useCallback(() => {
    if (state.step === 4) {
      onComplete(wizardToFilters(state), state.scope);
      return;
    }
    // People scope has no Refine step (legal form / size don't apply to people).
    if (state.step === 2 && state.scope === "people") {
      setState(s => ({ ...s, step: 4 }));
      return;
    }
    setState(s => ({ ...s, step: s.step + 1 }));
  }, [state, onComplete]);

  const handleBack = useCallback(() => {
    if (state.step === 0) { onClose(); return; }
    if (state.step === 4 && state.scope === "people") {
      setState(s => ({ ...s, step: 2 }));
      return;
    }
    setState(s => ({ ...s, step: s.step - 1 }));
  }, [state.step, state.scope, onClose]);

  if (!open) return null;

  const { step, scope, cantons: selectedCantons, allSwitzerland, nogaCodes, legalForm, sizeBucket, roleCategories, minCompanies } = state;
  const labels = stepLabels(scope);
  const questions = stepQuestions(scope);
  const subs = stepSubs(scope);
  const resultCount = countPage?.total;
  const continueLabel = step === 4
    ? resultCount !== undefined ? `See ${resultCount.toLocaleString()} results →` : "See results →"
    : "Continue →";

  const scopeIcon = scope === "companies" ? Building2 : scope === "people" ? Users : Briefcase;
  const scopeLabel = scope === "companies" ? "Companies" : scope === "people" ? "People" : "Jobs (→ Companies)";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "rgba(23,32,46,0.34)" }}
      onClick={onClose}
    >
      <div
        className="w-[660px] max-w-[95vw] bg-white rounded-[20px] overflow-hidden flex flex-col max-h-[90vh]"
        style={{ boxShadow: "0 24px 70px rgba(23,32,46,0.30)" }}
        onClick={e => e.stopPropagation()}
      >
        {/* ── Header ─────────────────────────────────────────────────────────── */}
        <div className="px-6 pt-5 pb-4 border-b border-[#eef0f3] shrink-0">
          <div className="flex items-center gap-3 mb-3">
            <div className="flex gap-1 flex-1">
              {Array.from({ length: 5 }).map((_, i) => (
                <div
                  key={i}
                  className={cn(
                    "h-[3px] flex-1 rounded-full transition-colors",
                    i <= step ? "bg-blue-600" : "bg-[#eef0f3]"
                  )}
                />
              ))}
            </div>
            <span className="font-mono text-xs text-slate-400 tabular-nums shrink-0">{step + 1}/5</span>
            <button
              onClick={onClose}
              className="ml-1 w-7 h-7 flex items-center justify-center rounded-lg text-slate-400 hover:text-slate-600 hover:bg-slate-100 transition-colors shrink-0"
            >
              <X size={15} />
            </button>
          </div>

          <p className="font-mono text-[11px] font-bold tracking-[0.08em] uppercase text-blue-600 mb-3">
            STEP {step + 1} · {labels[step]}
          </p>
          <p
            className="text-[27px] font-bold text-[#1f2733] leading-tight"
            style={{ letterSpacing: "-0.02em" }}
          >
            {questions[step]}
          </p>
          <p className="text-[15px] text-[#6b7480] mt-1">{subs[step]}</p>
        </div>

        {/* ── Body ───────────────────────────────────────────────────────────── */}
        <div className="px-6 py-5 flex-1 overflow-y-auto">

          {/* Step 1 – What */}
          {step === 0 && (
            <div className="flex gap-3">
              <ScopeTile icon={Building2} title="Companies" sub="700,000+ registered" selected={scope === "companies"} onClick={() => setState(s => ({ ...s, scope: "companies" }))} />
              <ScopeTile icon={Users} title="People" sub="Founders, directors, owners" selected={scope === "people"} onClick={() => setState(s => ({ ...s, scope: "people" }))} />
            </div>
          )}

          {/* Step 2 – Where */}
          {step === 1 && (
            <div className="flex flex-wrap gap-2">
              <Chip
                label="All of Switzerland"
                selected={allSwitzerland}
                onClick={() => setState(s => ({ ...s, allSwitzerland: true, cantons: [] }))}
              />
              {cantons.map(c => (
                <Chip key={c} label={c} selected={selectedCantons.includes(c)} onClick={() => toggleCanton(c)} />
              ))}
            </div>
          )}

          {/* Step 3 – Role & Involvement (people) */}
          {step === 2 && scope === "people" && (
            <div className="space-y-6">
              <div>
                <p className="text-[13px] font-bold text-[#1f2733] mb-3">Role</p>
                <div className="flex flex-wrap gap-2">
                  <Chip label="Any" selected={roleCategories.length === 0} onClick={() => setState(s => ({ ...s, roleCategories: [] }))} />
                  {ROLE_CATEGORIES.map(({ label, value }) => (
                    <Chip key={value} label={label} selected={roleCategories.includes(value)} onClick={() => toggleRoleCategory(value)} />
                  ))}
                </div>
              </div>
              <div>
                <p className="text-[13px] font-bold text-[#1f2733] mb-3">Companies involved with (current + past)</p>
                <div className="flex flex-wrap gap-2">
                  {MIN_COMPANIES_OPTIONS.map(({ label, value }) => (
                    <Chip key={value} label={label} selected={minCompanies === value} onClick={() => setState(s => ({ ...s, minCompanies: value }))} />
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* Step 3 – Industry (companies/jobs) */}
          {step === 2 && scope !== "people" && (
            <div className="space-y-4">
              {nogaCodes.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {nogaCodes.map(code => (
                    <span key={code} className="flex items-center gap-1 pl-2.5 pr-1 py-0.5 rounded-full bg-blue-50 border border-blue-200 text-blue-700 text-[12px] font-medium">
                      {nogaLabelMap[code] || code}
                      <button
                        type="button"
                        onClick={() => toggleNogaCode(code)}
                        className="w-4 h-4 flex items-center justify-center rounded-full hover:bg-blue-100 transition-colors ml-0.5"
                      >
                        <X size={10} />
                      </button>
                    </span>
                  ))}
                </div>
              )}

              <input
                type="text"
                value={typeaheadQuery}
                onChange={e => setTypeaheadQuery(e.target.value)}
                placeholder="e.g. software, restaurants…"
                autoFocus
                className="w-full px-4 py-2.5 rounded-xl text-[14px] text-[#1f2733] placeholder:text-[#9aa2ad] focus:outline-none transition-colors bg-white"
                style={{
                  border: "1.5px solid #e6e8ec",
                  caretColor: "#2563eb",
                }}
                onFocus={e => { e.currentTarget.style.border = "1.5px solid #2563eb"; e.currentTarget.style.boxShadow = "0 0 0 4px rgba(37,99,235,0.10)"; }}
                onBlur={e => { e.currentTarget.style.border = "1.5px solid #e6e8ec"; e.currentTarget.style.boxShadow = "none"; }}
              />

              {typeaheadResults.length > 0 && (
                <div className="border border-[#e6e8ec] rounded-xl overflow-hidden">
                  {typeaheadResults.map(n => (
                    <button
                      key={n.code}
                      type="button"
                      onClick={() => {
                        toggleNogaCode(n.code);
                        setTypeaheadQuery("");
                        setDebouncedQuery("");
                      }}
                      className="w-full flex items-center gap-3 px-4 py-2.5 text-left hover:bg-blue-50/50 transition-colors border-b border-[#eef0f3] last:border-0"
                    >
                      <span className="font-mono text-[11px] font-bold text-blue-600 bg-blue-50 px-1.5 py-0.5 rounded shrink-0 min-w-[40px] text-center">
                        {n.code}
                      </span>
                      <span className="text-[13px] text-[#1f2733] truncate">
                        {n.labels?.de || n.labels?.en || n.label}
                      </span>
                    </button>
                  ))}
                </div>
              )}

              <div>
                <p className="text-[11px] font-bold text-[#6b7480] uppercase tracking-[0.08em] mb-2.5">
                  Popular categories
                </p>
                <div className="flex flex-wrap gap-2">
                  {POPULAR_INDUSTRIES.map(({ label, code }) => (
                    <Chip key={code} label={label} selected={nogaCodes.includes(code)} onClick={() => toggleNogaCode(code)} />
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* Step 4 – Refine */}
          {step === 3 && (
            <div className="space-y-6">
              <div>
                <p className="text-[13px] font-bold text-[#1f2733] mb-3">Legal form</p>
                <div className="flex flex-wrap gap-2">
                  <Chip label="Any" selected={legalForm === ""} onClick={() => setState(s => ({ ...s, legalForm: "" }))} />
                  {LEGAL_FORMS.map(f => (
                    <Chip key={f} label={f} selected={legalForm === f} onClick={() => setState(s => ({ ...s, legalForm: f }))} />
                  ))}
                </div>
              </div>
              <div>
                <p className="text-[13px] font-bold text-[#1f2733] mb-3">Company size</p>
                <div className="flex flex-wrap gap-2">
                  {SIZE_OPTIONS.map(({ label, value }) => (
                    <Chip key={value} label={label} selected={sizeBucket === value} onClick={() => setState(s => ({ ...s, sizeBucket: value }))} />
                  ))}
                </div>
                <p className="mt-2 text-[12px] text-[#9aa2ad]">
                  Size filtering is informational — exact headcount data is not yet available.
                </p>
              </div>
            </div>
          )}

          {/* Step 5 – Review */}
          {step === 4 && (
            <div className="space-y-2">
              <ReviewRow
                icon={scopeIcon}
                label="What"
                value={scopeLabel}
                onEdit={() => setState(s => ({ ...s, step: 0 }))}
              />
              <ReviewRow
                icon={MapPin}
                label="Where"
                value={allSwitzerland ? "All of Switzerland" : selectedCantons.join(", ")}
                onEdit={() => setState(s => ({ ...s, step: 1 }))}
              />
              {scope === "people" ? (
                <ReviewRow
                  icon={Users}
                  label="Role & involvement"
                  value={
                    [
                      roleCategories.length > 0
                        ? roleCategories.map(v => ROLE_CATEGORIES.find(r => r.value === v)?.label || v).join(", ")
                        : null,
                      minCompanies > 0 ? `${minCompanies}+ companies` : null,
                    ].filter(Boolean).join(" · ") || "Any"
                  }
                  onEdit={() => setState(s => ({ ...s, step: 2 }))}
                />
              ) : (
                <>
                  <ReviewRow
                    icon={ListTree}
                    label="Industry"
                    value={nogaCodes.length > 0 ? nogaCodes.map(c => nogaLabelMap[c] || c).join(", ") : "Any"}
                    onEdit={() => setState(s => ({ ...s, step: 2 }))}
                  />
                  <ReviewRow
                    icon={SlidersHorizontal}
                    label="Refine"
                    value={
                      [
                        legalForm ? `Legal form: ${legalForm}` : null,
                        sizeBucket ? `Size: ${SIZE_OPTIONS.find(o => o.value === sizeBucket)?.label}` : null,
                      ].filter(Boolean).join(" · ") || "Any"
                    }
                    onEdit={() => setState(s => ({ ...s, step: 3 }))}
                  />
                </>
              )}

              {scope === "companies" && resultCount !== undefined && (
                <div className="mt-4 flex items-center gap-2 px-4 py-3 bg-blue-50 rounded-xl border border-blue-100">
                  <span className="text-[13px] font-semibold text-blue-700">
                    {resultCount.toLocaleString()} companies match your filters
                  </span>
                </div>
              )}
            </div>
          )}
        </div>

        {/* ── Footer ─────────────────────────────────────────────────────────── */}
        <div className="px-6 py-4 border-t border-[#eef0f3] flex items-center justify-between shrink-0">
          {step === 0 ? (
            <button
              onClick={onClose}
              className="text-[13px] text-[#6b7480] hover:text-[#3f4854] transition-colors"
            >
              Skip the guide
            </button>
          ) : (
            <button
              onClick={handleBack}
              className="flex items-center gap-1.5 px-4 py-2 rounded-xl border border-[#e6e8ec] text-[13px] font-medium text-[#3f4854] hover:bg-slate-50 transition-colors"
            >
              ← Back
            </button>
          )}

          <div className="flex items-center gap-3 ml-auto">
            {step === 3 && (
              <button
                onClick={() => setState(s => ({ ...s, step: 4 }))}
                className="text-[13px] text-[#6b7480] hover:text-[#3f4854] transition-colors"
              >
                Skip →
              </button>
            )}
            <button
              onClick={handleContinue}
              className="flex items-center gap-1.5 px-5 py-2.5 rounded-xl bg-blue-600 text-white text-[13px] font-semibold hover:bg-blue-700 transition-colors"
            >
              {continueLabel}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
