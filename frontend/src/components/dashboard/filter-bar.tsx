"use client";
import { useState, useCallback, useRef, useEffect } from "react";
import { X, SlidersHorizontal, ChevronDown, Bookmark, BookmarkCheck, Trash2, Bell, BellOff } from "lucide-react";
import { cn, formatClusterLabel } from "@/lib/utils";
import { Combobox } from "./combobox";
import { MultiSelectFilter } from "./multi-select-filter";
import type { CompanyFilters, SavedView } from "@/lib/types";
import { REVIEW_STATUSES, CONTACT_STATUSES } from "@/lib/types";
import { useI18n } from "@/i18n/context";

interface FilterBarProps {
  filters: CompanyFilters;
  cantons: string[];
  taxonomy?: Record<string, [string, number][]>;
  onChange: (filters: CompanyFilters) => void;
  onClear: () => void;
  resultCount: number;
  savedViews?: SavedView[];
  onSaveView?: (name: string) => void;
  onLoadView?: (filters: CompanyFilters) => void;
  onDeleteView?: (id: number) => void;
  onToggleAlert?: (id: number, enabled: boolean) => void;
}

const inputCls =
  "w-full rounded border border-slate-200 bg-white px-2 py-1.5 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent";

const selectCls = cn(inputCls, "appearance-none pr-6");

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-xs font-semibold text-slate-600 uppercase tracking-widest mb-2 mt-3 first:mt-0">
      {children}
    </div>
  );
}

function Label({ children }: { children: React.ReactNode }) {
  return <div className="text-xs font-medium text-slate-700 mb-1">{children}</div>;
}

function ProTag() {
  const { dict } = useI18n();
  const t = dict.app.filters;
  return (
    <span
      className="inline-flex items-center text-[9px] uppercase tracking-wide font-semibold text-amber-700 bg-amber-50 border border-amber-200 rounded px-1 py-px ml-1.5 align-middle"
      title={t.proTitle}
    >
      {t.proTag}
    </span>
  );
}

function CantonMultiSelect({
  value, options, onChange, placeholder,
}: {
  value: string | undefined;
  options: string[];
  onChange: (value: string | undefined) => void;
  placeholder: string;
}) {
  const { dict } = useI18n();
  const t = dict.app.filters;
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const selected = value ? value.split(",").map((s) => s.trim()).filter(Boolean) : [];

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  const toggle = (c: string) => {
    const next = selected.includes(c) ? selected.filter((x) => x !== c) : [...selected, c];
    onChange(next.length > 0 ? next.join(",") : undefined);
  };

  const label = selected.length === 0 ? placeholder : selected.length === 1 ? selected[0] : t.cantonsCount.replace("{count}", String(selected.length));

  return (
    <div ref={ref} className="relative flex-1 min-w-[5.5rem] sm:flex-none">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className={cn(selectCls, "w-full sm:w-32 text-left truncate", selected.length === 0 && "text-slate-500")}
      >
        {label}
      </button>
      <ChevronDown size={13} className={cn("pointer-events-none absolute right-2 top-2 text-slate-400 transition-transform", open && "rotate-180")} />
      {open && (
        <div className="absolute z-50 mt-1 w-44 max-h-60 overflow-y-auto rounded border border-slate-200 bg-white shadow-lg text-sm">
          {options.map((c) => (
            <label key={c} className="flex items-center gap-2 px-2 py-1.5 hover:bg-blue-50 cursor-pointer">
              <input
                type="checkbox"
                checked={selected.includes(c)}
                onChange={() => toggle(c)}
                className="accent-blue-600"
              />
              {c}
            </label>
          ))}
        </div>
      )}
    </div>
  );
}

function ScoreRange({
  label, minKey, maxKey, filters, set,
}: {
  label: string;
  minKey: keyof CompanyFilters;
  maxKey: keyof CompanyFilters;
  filters: CompanyFilters;
  set: (key: keyof CompanyFilters, value: number | undefined) => void;
}) {
  return (
    <div>
      <Label>{label}</Label>
      <div className="flex gap-1 items-center">
        <input
          type="number" min={0} max={100} placeholder="Min"
          className={cn(inputCls, "w-full")}
          value={(filters[minKey] as number | undefined) ?? ""}
          onChange={(e) => set(minKey, e.target.value ? Number(e.target.value) : undefined)}
        />
        <span className="text-slate-500 text-xs shrink-0">–</span>
        <input
          type="number" min={0} max={100} placeholder="Max"
          className={cn(inputCls, "w-full")}
          value={(filters[maxKey] as number | undefined) ?? ""}
          onChange={(e) => set(maxKey, e.target.value ? Number(e.target.value) : undefined)}
        />
      </div>
    </div>
  );
}

const CHIP_LABEL_KEYS: Partial<Record<keyof CompanyFilters, string>> = {
  q: "q", uid: "uid", canton: "canton", review_status: "review_status", contact_status: "contact_status",
  has_website: "has_website",
  google_searched: "google_searched", tags: "tags", ai_category: "ai_category",
  tfidf_cluster: "tfidf_cluster", purpose_keywords: "purpose_keywords",
  noga_code: "noga_code", noga_label: "noga_label", noga_level: "noga_level",
  purpose_language: "purpose_language",
  business_model: "business_model",
  legal_form: "legal_form", registered_after: "registered_after", registered_before: "registered_before",
  min_web_score: "min_web_score", max_web_score: "max_web_score",
  min_flex_score: "min_flex_score", max_flex_score: "max_flex_score",
  min_ai_score: "min_ai_score", max_ai_score: "max_ai_score",
  min_combined_score: "min_combined_score", max_combined_score: "max_combined_score",
  exclude_review_status: "exclude_review_status", exclude_contact_status: "exclude_contact_status",
  exclude_canton: "exclude_canton", exclude_tags: "exclude_tags",
  exclude_tfidf_cluster: "exclude_tfidf_cluster", exclude_purpose_keywords: "exclude_purpose_keywords",
  exclude_ai_category: "exclude_ai_category",
  exclude_noga_code: "exclude_noga_code", exclude_noga_label: "exclude_noga_label", exclude_noga_level: "exclude_noga_level",
};

const ZEFIX_STATUS_VALUES = ["ACTIVE", "CANCELLED", "BEING_CANCELLED"] as const;

export function FilterBar({
  filters, cantons, taxonomy, onChange, onClear, resultCount,
  savedViews = [], onSaveView, onLoadView, onDeleteView, onToggleAlert,
}: FilterBarProps) {
  const [expanded, setExpanded] = useState(false);
  const [saveViewName, setSaveViewName] = useState("");
  const [showSaveInput, setShowSaveInput] = useState(false);
  const [showViewsMenu, setShowViewsMenu] = useState(false);

  const categories = taxonomy?.categories ?? [];
  const nogaCodes = taxonomy?.noga_codes ?? [];
  const nogaLevels = taxonomy?.noga_levels ?? [];
  const legalForms = taxonomy?.legal_forms ?? [];

  // Taxonomy returns noga entries as "62 — Information Technology"; API expects bare code "62".
  // Strip the label for the stored value but keep a map so the chip and dropdown show the full label.
  const nogaOptions: [string, number][] = nogaCodes.map(([entry, cnt]) => {
    const code = entry.split(" — ")[0]?.trim() ?? entry;
    return [code, cnt] as [string, number];
  });
  const nogaLabelMap = new Map<string, string>(
    nogaCodes.map(([entry]) => {
      const code = entry.split(" — ")[0]?.trim() ?? entry;
      return [code, entry];
    })
  );
  const formatNogaValue = (code: string) => nogaLabelMap.get(code) ?? code;

  const { dict } = useI18n();
  const t = dict.app.filters;

  const set = useCallback(
    (key: keyof CompanyFilters, value: string | number | undefined) =>
      onChange({ ...filters, [key]: value || undefined, page: 1 }),
    [filters, onChange]
  );
  const setNum = useCallback(
    (key: keyof CompanyFilters, value: number | undefined) =>
      onChange({ ...filters, [key]: value, page: 1 }),
    [filters, onChange]
  );
  const unset = useCallback(
    (key: keyof CompanyFilters) => onChange({ ...filters, [key]: undefined, page: 1 }),
    [filters, onChange]
  );

  const activeEntries = Object.entries(filters).filter(
    ([k, v]) => !["page", "page_size", "sort", "active_only"].includes(k) && v !== undefined && v !== ""
  ) as [keyof CompanyFilters, string | number][];

  const activeCount = activeEntries.length;

  function handleSaveView() {
    if (saveViewName.trim() && onSaveView) {
      onSaveView(saveViewName.trim());
      setSaveViewName("");
      setShowSaveInput(false);
    }
  }

  return (
    <div className="border-b border-slate-200 bg-slate-50 text-sm">
      {/* ── Always-visible core row ── */}
      <div className="flex items-center gap-2 px-3 py-2 flex-wrap bg-white border-b border-slate-100">
        {/* Core inputs */}
        <input
          type="text"
          className={cn(inputCls, "flex-1 min-w-[7rem] sm:flex-none sm:w-36")}
          placeholder={t.companyname}
          value={filters.q ?? ""}
          onChange={(e) => set("q", e.target.value)}
        />
        <CantonMultiSelect
          value={filters.canton}
          options={cantons}
          onChange={(v) => set("canton", v)}
          placeholder={t.canton}
        />
        <div className="relative flex-1 min-w-[7rem] sm:flex-none">
          <select className={cn(selectCls, "w-full sm:w-36")} value={filters.status ?? ""}
            onChange={(e) => set("status" as keyof CompanyFilters, e.target.value)}>
            <option value="">{t.status}</option>
            {ZEFIX_STATUS_VALUES.map((v) => (
              <option key={v} value={v}>
                {v === "ACTIVE" ? t.statusActive : v === "CANCELLED" ? t.statusCancelled : t.statusBeingCancelled}
              </option>
            ))}
          </select>
          <ChevronDown size={13} className="pointer-events-none absolute right-2 top-2 text-slate-400" />
        </div>
        <input
          type="text"
          className={cn(inputCls, "flex-1 min-w-[5.5rem] sm:flex-none sm:w-36")}
          placeholder={t.cheuid}
          value={filters.uid ?? ""}
          onChange={(e) => set("uid", e.target.value)}
        />
        {/* Expand/collapse advanced filters */}
        <button
          type="button"
          onClick={() => setExpanded((e) => !e)}
          className={cn(
            "flex items-center gap-1.5 rounded px-2.5 py-1 text-sm font-medium transition-colors shrink-0",
            expanded
              ? "bg-blue-600 text-white"
              : "bg-slate-50 border border-slate-200 text-slate-600 hover:bg-slate-100"
          )}
        >
          <SlidersHorizontal size={13} />
          {t.filtersBtn}
          {activeCount > 0 && (
            <span className={cn(
              "rounded-full px-1.5 py-0.5 text-xs font-semibold",
              expanded ? "bg-blue-500 text-white" : "bg-blue-100 text-blue-700"
            )}>
              {activeCount}
            </span>
          )}
          <ChevronDown size={12} className={cn("transition-transform", expanded && "rotate-180")} />
        </button>

        {/* Active filter chips — multi-value fields split into individual chips */}
        <div className="flex flex-wrap gap-1.5 w-full sm:flex-1 sm:w-auto min-w-0">
          {activeEntries
            .filter(([k]) => !["q", "uid", "canton", "status"].includes(String(k)))
            .flatMap(([k, v]) => {
              const chipKey = CHIP_LABEL_KEYS[k];
              const label = chipKey ? t.chipLabels[chipKey as keyof typeof t.chipLabels] : String(k).replace(/_/g, " ");
              const isExclude = String(k).startsWith("exclude_");
              const strVal = String(v);
              // Split comma-separated multi-select fields into individual chips
              const MULTI_KEYS: (keyof CompanyFilters)[] = [
                "ai_category", "tfidf_cluster", "purpose_keywords", "noga_code", "noga_label", "noga_level",
                "exclude_ai_category", "exclude_tfidf_cluster", "exclude_purpose_keywords",
                "exclude_noga_code", "exclude_noga_label", "exclude_noga_level",
              ];
              const CLUSTER_KEYS: (keyof CompanyFilters)[] = ["tfidf_cluster", "exclude_tfidf_cluster"];
              if (MULTI_KEYS.includes(k) && strVal.includes(",")) {
                return strVal.split(",").map((part) => part.trim()).filter(Boolean).map((part, i) => (
                  <span
                    key={`${String(k)}-${i}`}
                    className={cn(
                      "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs",
                      isExclude
                        ? "bg-red-50 border-red-200 text-red-700"
                        : "bg-white border-slate-200 text-slate-700"
                    )}
                  >
                    <span className="text-slate-600">{label}:</span>
                    <span>{CLUSTER_KEYS.includes(k) ? formatClusterLabel(part) : part}</span>
                    <button
                      type="button"
                      onClick={() => {
                        const remaining = strVal.split(",").map((s) => s.trim()).filter((s) => s !== part);
                        if (remaining.length > 0) onChange({ ...filters, [k]: remaining.join(", "), page: 1 });
                        else unset(k);
                      }}
                      className="text-slate-400 hover:text-slate-700"
                    >
                      <X size={11} />
                    </button>
                  </span>
                ));
              }
              return [(
                <span
                  key={String(k)}
                  className={cn(
                    "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs",
                    isExclude
                      ? "bg-red-50 border-red-200 text-red-700"
                      : "bg-white border-slate-200 text-slate-700"
                  )}
                >
                  <span className="text-slate-400">{label}:</span>
                  <span>
                    {k === "has_website"
                      ? (strVal === "true" ? t.yes : t.no)
                      : CLUSTER_KEYS.includes(k) ? formatClusterLabel(strVal) : strVal.replace(/^_none$/, t.none).replace(/^_any$/, t.any)}
                  </span>
                  <button type="button" onClick={() => unset(k)} className="text-slate-400 hover:text-slate-700">
                    <X size={11} />
                  </button>
                </span>
              )];
            })}
          {activeCount > 0 && (
            <button onClick={onClear} className="text-xs text-slate-500 hover:text-slate-700 flex items-center gap-0.5">
              <X size={11} /> {t.clearAll}
            </button>
          )}
        </div>

        {/* Saved views + result count */}
        <div className="flex items-center gap-1.5 shrink-0 w-full sm:w-auto sm:ml-auto">
          {savedViews.length > 0 && (
            <div className="relative">
              <button
                type="button"
                onClick={() => { setShowViewsMenu((v) => !v); setShowSaveInput(false); }}
                className="flex items-center gap-1 text-xs text-slate-500 hover:text-slate-700 px-2 py-1 rounded border border-slate-200 bg-white hover:bg-slate-50"
              >
                <BookmarkCheck size={13} /> {t.views.replace("{count}", String(savedViews.length))}
              </button>
              {showViewsMenu && (
                <div className="absolute right-0 top-full mt-1 z-50 bg-white border border-slate-200 rounded-lg shadow-lg min-w-[180px]">
                  {savedViews.map((v) => (
                    <div key={v.id} className="flex items-center justify-between px-3 py-2 hover:bg-slate-50 group">
                      <button
                        type="button"
                        className="text-sm text-slate-700 truncate flex-1 text-left"
                        onClick={() => { onLoadView?.(v.filters); setShowViewsMenu(false); }}
                      >
                        {v.name}
                      </button>
                      <div className="flex items-center gap-1 ml-2 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
                        <button
                          type="button"
                          onClick={() => onToggleAlert?.(v.id, !v.alert_enabled)}
                          title={v.alert_enabled ? t.disableAlert : t.enableAlert}
                          className={cn(
                            "transition-colors",
                            v.alert_enabled ? "text-amber-500 hover:text-amber-700" : "text-slate-300 hover:text-slate-500"
                          )}
                        >
                          {v.alert_enabled ? <Bell size={13} /> : <BellOff size={13} />}
                        </button>
                        <button
                          type="button"
                          onClick={() => onDeleteView?.(v.id)}
                          className="text-slate-300 hover:text-red-500 transition-colors"
                        >
                          <Trash2 size={13} />
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
          {showSaveInput ? (
            <div className="flex items-center gap-1">
              <input
                autoFocus
                type="text"
                placeholder={t.viewNamePlaceholder}
                value={saveViewName}
                onChange={(e) => setSaveViewName(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") handleSaveView(); if (e.key === "Escape") setShowSaveInput(false); }}
                className="text-xs border border-slate-200 rounded px-2 py-1 w-32 focus:outline-none focus:ring-2 focus:ring-blue-400"
              />
              <button
                type="button"
                onClick={handleSaveView}
                disabled={!saveViewName.trim()}
                className="text-xs bg-blue-600 text-white px-2 py-1 rounded hover:bg-blue-700 disabled:opacity-50"
              >
                {t.save}
              </button>
              <button type="button" onClick={() => setShowSaveInput(false)} className="text-slate-400 hover:text-slate-600">
                <X size={13} />
              </button>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => { setShowSaveInput(true); setShowViewsMenu(false); }}
              className="flex items-center gap-1 text-xs text-slate-500 hover:text-slate-700 px-2 py-1 rounded border border-slate-200 bg-white hover:bg-slate-50"
            >
              <Bookmark size={13} /> {t.saveView}
            </button>
          )}
          <span className="text-xs text-slate-600 px-1">
            {(resultCount === 1 ? t.result : t.results).replace("{count}", resultCount.toLocaleString())}
          </span>
        </div>
      </div>

      {/* ── Advanced filters (expanded) ── */}
      {expanded && (
        <div className="px-3 pb-3 border-t border-slate-200 bg-white">

          {/* ── WORKFLOW ── */}
          <SectionLabel>{t.workflow}</SectionLabel>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-x-4 gap-y-3">
            <div>
              <Label>{t.reviewstatus}</Label>
              <div className="relative">
                <select className={cn(selectCls)} value={filters.review_status ?? ""}
                  onChange={(e) => set("review_status", e.target.value)}>
                  <option value="">{t.all}</option>
                  <option value="_none">{t.pendingNone}</option>
                  {REVIEW_STATUSES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
                </select>
                <ChevronDown size={13} className="pointer-events-none absolute right-2 top-2 text-slate-400" />
              </div>
            </div>
            <div>
              <Label>{t.contactstatus}</Label>
              <div className="relative">
                <select className={cn(selectCls)} value={filters.contact_status ?? ""}
                  onChange={(e) => set("contact_status", e.target.value)}>
                  <option value="">{t.all}</option>
                  <option value="_none">{t.notSentNone}</option>
                  {CONTACT_STATUSES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
                </select>
                <ChevronDown size={13} className="pointer-events-none absolute right-2 top-2 text-slate-400" />
              </div>
            </div>
          </div>

          {/* ── CATEGORY (INCLUDE) ── */}
          <SectionLabel>{t.categoryinclude}</SectionLabel>
          <div className="grid grid-cols-1 sm:grid-cols-3 lg:grid-cols-6 gap-x-4 gap-y-3">
            <div>
              <Label>{t.aiclassification}<ProTag /></Label>
              <MultiSelectFilter
                value={filters.ai_category === "_none" ? undefined : filters.ai_category}
                onChange={(v) => set("ai_category", v)}
                placeholder={t.search.replace("{input}", t.aiclassification)}
                options={categories}
                extraOptions={[{ value: "_none", label: t.noneUnset }]}
                variant="include"
              />
            </div>
            <div>
              <Label>{t.noga}</Label>
              <MultiSelectFilter
                value={filters.noga_code === "_none" || filters.noga_code === "_any" ? undefined : filters.noga_code}
                onChange={(v) => set("noga_code", v)}
                placeholder={t.search.replace("{input}", t.noga)}
                options={nogaOptions}
                formatValue={formatNogaValue}
                extraOptions={[{ value: "_none", label: t.noneUnset }, { value: "_any", label: t.anySet }]}
                variant="include"
              />
            </div>
            <div>
              <Label>{t.legalform}</Label>
              <Combobox
                options={legalForms}
                value={filters.legal_form}
                onChange={(v) => set("legal_form", v)}
                placeholder={t.search.replace("{input}", t.legalform)}
              />
            </div>
            <div>
              <Label>{t.language}</Label>
              <div className="relative">
                <select
                  className={cn(selectCls)}
                  value={filters.purpose_language ?? ""}
                  onChange={(e) => set("purpose_language", e.target.value)}
                >
                  <option value="">{t.all}</option>
                  <option value="de">🇩🇪 {t.langDe}</option>
                  <option value="fr">🇫🇷 {t.langFr}</option>
                  <option value="it">🇮🇹 {t.langIt}</option>
                  <option value="en">🇬🇧 {t.langEn}</option>
                  <option value="rm">{t.langRm}</option>
                </select>
                <ChevronDown size={13} className="pointer-events-none absolute right-2 top-2 text-slate-400" />
              </div>
            </div>
            <div>
              <Label>{t.website}</Label>
              <div className="relative">
                <select
                  className={cn(selectCls)}
                  value={filters.has_website === undefined ? "" : String(filters.has_website)}
                  onChange={(e) =>
                    onChange({
                      ...filters,
                      has_website: e.target.value === "" ? undefined : e.target.value === "true",
                      page: 1,
                    })
                  }
                >
                  <option value="">{t.any}</option>
                  <option value="true">{t.hasWebsite}</option>
                  <option value="false">{t.noWebsite}</option>
                </select>
                <ChevronDown size={13} className="pointer-events-none absolute right-2 top-2 text-slate-400" />
              </div>
            </div>
            <div>
              <Label>{t.firstsogcafter}</Label>
              <input
                type="date"
                className={inputCls}
                value={filters.registered_after ?? ""}
                onChange={(e) => set("registered_after", e.target.value || undefined)}
              />
            </div>
            <div>
              <Label>{t.firstsogcbefore}</Label>
              <input
                type="date"
                className={inputCls}
                value={filters.registered_before ?? ""}
                onChange={(e) => set("registered_before", e.target.value || undefined)}
              />
            </div>
          </div>

          {/* ── SCORES ── */}
          <SectionLabel>{t.scores}</SectionLabel>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-x-4 gap-y-3">
            <ScoreRange label={t.webscore} minKey="min_web_score" maxKey="max_web_score" filters={filters} set={setNum} />
            <ScoreRange label={t.flexscore} minKey="min_flex_score" maxKey="max_flex_score" filters={filters} set={setNum} />
            <ScoreRange label={t.aiscore} minKey="min_ai_score" maxKey="max_ai_score" filters={filters} set={setNum} />
            <ScoreRange label={t.combinedscore} minKey="min_combined_score" maxKey="max_combined_score" filters={filters} set={setNum} />
          </div>

          {/* ── CATEGORY (EXCLUDE) ── */}
          <SectionLabel>{t.categoryexclude}</SectionLabel>
          <div className="grid grid-cols-1 sm:grid-cols-3 lg:grid-cols-6 gap-x-4 gap-y-3">
            <div>
              <Label>{t.aiclassification}<ProTag /></Label>
              <MultiSelectFilter
                value={filters.exclude_ai_category}
                onChange={(v) => set("exclude_ai_category", v)}
                placeholder={t.search.replace("{input}", t.aiclassification)}
                options={categories}
                variant="exclude"
              />
            </div>
            <div>
              <Label>{t.noga}</Label>
              <MultiSelectFilter
                value={filters.exclude_noga_code}
                onChange={(v) => set("exclude_noga_code", v)}
                placeholder={t.search.replace("{input}", t.noga)}
                options={nogaOptions}
                formatValue={formatNogaValue}
                variant="exclude"
              />
            </div>
          </div>

          {/* ── Collapse button (full width) ── */}
          <div className="mt-4 pt-3 border-t border-slate-100">
            <button
              type="button"
              onClick={() => setExpanded(false)}
              className="w-full flex items-center justify-center gap-1.5 rounded-lg border border-slate-200 bg-slate-50 py-2 text-xs font-medium text-slate-500 hover:bg-slate-100 hover:text-slate-700 transition-colors"
            >
              <ChevronDown size={13} className="rotate-180" /> {t.collapsefilters}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
