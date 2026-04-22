"use client";

import { PRICING_TIERS, PLAN_COMPARISON_GROUPS, type CellValue } from "@/lib/marketing-data";
import type { Dictionary } from "@/i18n/dictionaries";

type ComparisonDict = Dictionary["app"]["pricing"]["comparison"];

function Check() {
  return (
    <svg className="mx-auto h-5 w-5 text-emerald-500" viewBox="0 0 20 20" fill="currentColor">
      <path fillRule="evenodd" d="M16.704 4.153a.75.75 0 0 1 .143 1.052l-8 10.5a.75.75 0 0 1-1.127.075l-4.5-4.5a.75.75 0 0 1 1.06-1.06l3.894 3.893 7.48-9.817a.75.75 0 0 1 1.05-.143Z" clipRule="evenodd" />
    </svg>
  );
}

function Cross() {
  return (
    <svg className="mx-auto h-4 w-4 text-slate-300" viewBox="0 0 20 20" fill="currentColor">
      <path d="M6.28 5.22a.75.75 0 0 0-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 1 0 1.06 1.06L10 11.06l3.72 3.72a.75.75 0 1 0 1.06-1.06L11.06 10l3.72-3.72a.75.75 0 0 0-1.06-1.06L10 8.94 6.28 5.22Z" />
    </svg>
  );
}

function Cell({ value, dark }: { value: CellValue; dark?: boolean }) {
  if (value === "yes") return <Check />;
  if (value === "no") return <Cross />;
  return (
    <span className={`text-xs font-medium ${dark ? "text-slate-300" : "text-slate-600"}`}>
      {value}
    </span>
  );
}

function Tooltip({ text }: { text: string }) {
  return (
    <span
      title={text}
      className="ml-1 inline-flex h-3.5 w-3.5 cursor-help items-center justify-center rounded-full bg-slate-200 text-[9px] font-bold text-slate-500 leading-none"
    >
      ?
    </span>
  );
}

interface PlanComparisonTableProps {
  comparison: ComparisonDict;
}

export function PlanComparisonTable({ comparison }: PlanComparisonTableProps) {
  return (
    <div className="space-y-3">
      <h2 className="text-xl font-semibold text-slate-900">{comparison.heading}</h2>
      <div className="overflow-x-auto rounded-xl border border-slate-200 shadow-sm">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-200 bg-slate-100">
              <th className="px-4 py-3 text-left font-semibold text-slate-600 w-52">
                {comparison.featureCol}
              </th>
              {PRICING_TIERS.map((t) => (
                <th
                  key={t.id}
                  className={`px-3 py-3 text-center font-semibold text-sm w-28 ${
                    "dark" in t && t.dark
                      ? "bg-slate-900 text-white"
                      : t.popular
                      ? "bg-blue-50 text-blue-800"
                      : "text-slate-700"
                  }`}
                >
                  {t.name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {PLAN_COMPARISON_GROUPS.map((group) => (
              <>
                <tr key={`group-${group.headingKey}`} className="bg-slate-50 border-y border-slate-200">
                  <td colSpan={6} className="px-4 py-2 text-xs font-semibold uppercase tracking-wider text-slate-400">
                    {comparison.groups[group.headingKey]}
                  </td>
                </tr>
                {group.rows.map((row, rowIdx) => (
                  <tr
                    key={row.labelKey}
                    className={`border-b border-slate-100 ${rowIdx % 2 === 0 ? "bg-white" : "bg-slate-50/50"}`}
                  >
                    <td className="px-4 py-3 text-slate-700 font-medium">
                      {comparison.rows[row.labelKey]}
                      {row.hasTip && (
                        <Tooltip text={comparison.rows[`${row.labelKey}Tip` as keyof typeof comparison.rows]} />
                      )}
                    </td>
                    {row.values.map((val, colIdx) => {
                      const tier = PRICING_TIERS[colIdx];
                      const isDark = "dark" in tier && tier.dark;
                      return (
                        <td
                          key={colIdx}
                          className={`px-3 py-3 text-center ${
                            isDark ? "bg-slate-900" : tier.popular ? "bg-blue-100/40" : ""
                          }`}
                        >
                          <Cell value={val} dark={isDark} />
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
