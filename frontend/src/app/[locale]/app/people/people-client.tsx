"use client";

import { useState, useMemo } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import useSWR from "swr";
import {
  AlertCircle,
  CheckCircle,
  ChevronDown,
  ChevronRight,
  Search,
  Users,
  Building2,
  X,
  Clock,
  Network,
} from "lucide-react";
import {
  searchPersonEntities,
  fetchPersonNetwork,
  searchAuditors,
  reportPersonFlag,
} from "@/lib/api";
import type {
  SogcPersonEntity,
  SogcAuditor,
  MandateItem,
  CoDirector,
  PersonNetworkData,
} from "@/lib/types";

// ── Constants ──────────────────────────────────────────────────────────────────

const CONFIDENCE_STYLE: Record<string, string> = {
  high: "bg-emerald-50 text-emerald-700 border border-emerald-200",
  medium: "bg-amber-50 text-amber-700 border border-amber-200",
  low: "bg-red-50 text-red-700 border border-red-200",
};

const ROLE_COLORS: Record<string, { fill: string; label: string }> = {
  director: { fill: "#dc2626", label: "Board / Director" },
  officer:  { fill: "#2563eb", label: "Officer / Finance" },
  other:    { fill: "#d97706", label: "Other / Procuration" },
};
const FALLBACK_COLOR = "#94a3b8";

const LIMIT = 50;
const COLLAPSE_THRESHOLD = 10;

// ── Utility ────────────────────────────────────────────────────────────────────

function dateToDecimal(dateStr: string): number {
  const d = new Date(dateStr);
  return d.getFullYear() + d.getMonth() / 12;
}

function clamp(v: number, lo: number, hi: number) {
  return Math.max(lo, Math.min(hi, v));
}

// ── Flag modal ─────────────────────────────────────────────────────────────────

function FlagModal({ entity, onClose }: { entity: SogcPersonEntity; onClose: () => void }) {
  const [flagType, setFlagType] = useState<"should_merge" | "should_split">("should_merge");
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    try {
      await reportPersonFlag(entity.id, { flag_type: flagType, reason: reason || null });
      setDone(true);
    } catch {
      // ignore
    } finally {
      setSubmitting(false);
    }
  }

  const name = [entity.firstname, entity.lastname].filter(Boolean).join(" ") || entity.normalized_key;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-md p-6 mx-4">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-semibold text-slate-800">Report issue — {name}</h3>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600"><X size={16} /></button>
        </div>
        {done ? (
          <div className="flex items-center gap-2 text-emerald-700 text-sm">
            <CheckCircle size={16} /> Reported. Thank you.
          </div>
        ) : (
          <form onSubmit={submit} className="space-y-4">
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">Issue type</label>
              <select
                value={flagType}
                onChange={e => setFlagType(e.target.value as "should_merge" | "should_split")}
                className="w-full rounded border border-slate-200 px-3 py-1.5 text-sm"
              >
                <option value="should_merge">Two entries are the same person (should merge)</option>
                <option value="should_split">This entry contains different people (should split)</option>
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">Reason (optional)</label>
              <textarea
                value={reason}
                onChange={e => setReason(e.target.value)}
                rows={3}
                placeholder="Describe the issue…"
                className="w-full rounded border border-slate-200 px-3 py-1.5 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-blue-200"
              />
            </div>
            <div className="flex gap-2 justify-end">
              <button type="button" onClick={onClose} className="px-3 py-1.5 text-sm text-slate-600 hover:text-slate-800">Cancel</button>
              <button type="submit" disabled={submitting} className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50">
                {submitting ? "Sending…" : "Submit"}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}

// ── Tenure timeline (Gantt) ────────────────────────────────────────────────────

function TenureTimeline({ mandates, locale }: { mandates: MandateItem[]; locale: string }) {
  const today = new Date().toISOString().slice(0, 10);

  const allDates = mandates.flatMap(m =>
    [m.date_from, m.is_current ? today : m.date_to].filter(Boolean) as string[]
  );

  if (!allDates.length) {
    return <p className="text-xs text-slate-400 py-4 text-center">No mandate dates available.</p>;
  }

  const minYear = Math.floor(
    Math.min(...allDates.map(d => new Date(d).getFullYear()))
  );
  const maxDecimal = dateToDecimal(today) + 0.4;
  const span = maxDecimal - minYear;

  function pos(dateStr: string): number {
    return clamp(((dateToDecimal(dateStr) - minYear) / span) * 100, 0, 100);
  }

  const todayPct = pos(today);

  // Year axis ticks: every year, label every 2 or 5 depending on range
  const yearCount = Math.ceil(maxDecimal) - minYear;
  const tickEvery = yearCount > 20 ? 5 : yearCount > 10 ? 2 : 1;
  const years: number[] = [];
  for (let y = minYear; y <= Math.ceil(maxDecimal); y++) years.push(y);

  return (
    <div className="overflow-x-auto">
      <div style={{ minWidth: 580 }}>
        {/* Legend */}
        <div className="flex flex-wrap gap-4 mb-3 px-1 text-[11px] text-slate-500">
          {Object.entries(ROLE_COLORS).map(([k, v]) => (
            <span key={k} className="flex items-center gap-1.5">
              <span className="w-3 h-2.5 inline-block rounded-sm" style={{ background: v.fill }} />
              {v.label}
            </span>
          ))}
          <span className="flex items-center gap-1.5 ml-auto">
            <span className="w-3 h-2.5 inline-block rounded-sm" style={{ background: FALLBACK_COLOR }} />
            Other
          </span>
        </div>

        {/* Grid */}
        <div className="grid" style={{ gridTemplateColumns: "192px 1fr" }}>
          {/* Axis header row */}
          <div />
          <div className="relative h-7 border-b border-slate-200 mb-0.5">
            {years.map(y => (
              <div
                key={y}
                className="absolute bottom-0 flex flex-col items-center"
                style={{ left: `${pos(`${y}-01-01`)}%`, transform: "translateX(-50%)" }}
              >
                <div className="w-px h-2 bg-slate-300" />
                {y % tickEvery === 0 && (
                  <span className="text-[10px] text-slate-400 font-mono leading-none mt-0.5 select-none">
                    {y}
                  </span>
                )}
              </div>
            ))}
            {/* Now line header */}
            <div
              className="absolute top-0 bottom-0 border-l border-red-400 border-dashed opacity-70"
              style={{ left: `${todayPct}%` }}
            >
              <span className="absolute -top-0 left-1 text-[9px] text-red-400 font-mono whitespace-nowrap leading-none">
                now
              </span>
            </div>
          </div>

          {/* Mandate rows */}
          {mandates.map((m) => {
            const color = ROLE_COLORS[m.role_category ?? ""]?.fill ?? FALLBACK_COLOR;
            const fromPct = m.date_from ? pos(m.date_from) : 0;
            const toPct = m.is_current ? todayPct : (m.date_to ? pos(m.date_to) : todayPct);
            const widthPct = Math.max(0.4, toPct - fromPct);
            const isActive = !!m.is_current;
            const barLabel = m.signature_type ?? m.role ?? "";

            return (
              <div key={m.company_uid} className="contents">
                {/* Left: company name */}
                <div className="flex items-center gap-1.5 pr-3 py-1.5 border-b border-slate-100">
                  <span
                    className="w-1.5 h-1.5 rounded-full shrink-0"
                    style={{ background: isActive ? "#10b981" : "#cbd5e1" }}
                  />
                  {m.company_id ? (
                    <Link
                      href={`/${locale}/app/companies/${m.company_id}`}
                      className="text-xs text-blue-600 hover:underline truncate leading-tight"
                      title={m.company_name ?? m.company_uid}
                    >
                      {m.company_name ?? m.company_uid}
                    </Link>
                  ) : (
                    <span className="text-xs text-slate-600 truncate font-mono text-[10px]">
                      {m.company_uid}
                    </span>
                  )}
                </div>

                {/* Right: Gantt bar row */}
                <div className="relative h-9 border-b border-slate-100 bg-slate-50/20">
                  {/* Faint year gridlines */}
                  {years.map(y => (
                    <div
                      key={y}
                      className="absolute top-0 bottom-0 border-l border-slate-100"
                      style={{ left: `${pos(`${y}-01-01`)}%` }}
                    />
                  ))}
                  {/* Now line */}
                  <div
                    className="absolute top-0 bottom-0 border-l border-red-300 border-dashed opacity-40"
                    style={{ left: `${todayPct}%` }}
                  />
                  {/* Bar */}
                  <div
                    className="absolute top-2 h-5 flex items-center px-1.5 overflow-hidden transition-opacity"
                    style={{
                      left: `${fromPct}%`,
                      width: `${widthPct}%`,
                      background: color,
                      opacity: isActive ? 1 : 0.5,
                    }}
                    title={`${m.company_name ?? m.company_uid} · ${m.role ?? ""} · ${m.date_from ?? "?"} → ${m.date_to ?? "present"}`}
                  >
                    {widthPct > 7 && (
                      <span className="text-white text-[10px] font-mono truncate leading-none">
                        {barLabel.length > 28 ? barLabel.slice(0, 26) + "…" : barLabel}
                      </span>
                    )}
                  </div>
                  {/* Departure end-marker */}
                  {!isActive && m.date_to && (
                    <div
                      className="absolute top-1 bottom-1 w-px bg-slate-600"
                      style={{ left: `${toPct}%` }}
                    />
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// ── Network graph (SVG ego-graph) ──────────────────────────────────────────────

const SVG_W = 820;
const SVG_H = 560;
const CX = SVG_W / 2; // 410
const CY = SVG_H / 2; // 280
const R_COMPANY = 195;
const R_CODIR = 85;

function NetworkGraph({
  entity,
  mandates,
}: {
  entity: SogcPersonEntity;
  mandates: MandateItem[];
}) {
  const name = [entity.firstname, entity.lastname].filter(Boolean).join(" ") || entity.normalized_key;
  const initials = name
    .split(" ")
    .map(s => s[0] ?? "")
    .join("")
    .slice(0, 2)
    .toUpperCase();

  const n = mandates.length;

  // Company node positions
  const companyNodes = useMemo(
    () =>
      mandates.map((m, i) => {
        const angle = (i / n) * 2 * Math.PI - Math.PI / 2;
        return {
          mandate: m,
          x: CX + R_COMPANY * Math.cos(angle),
          y: CY + R_COMPANY * Math.sin(angle),
          angle,
        };
      }),
    [mandates, n]
  );

  // Co-director positions per company
  type CoDirNode = {
    cd: CoDirector;
    x: number;
    y: number;
    r: number;
    companyUid: string;
  };

  const coDirNodes = useMemo<CoDirNode[]>(() => {
    const nodes: CoDirNode[] = [];
    companyNodes.forEach(({ mandate, x: cx, y: cy, angle }) => {
      const k = mandate.co_directors.length;
      mandate.co_directors.forEach((cd, j) => {
        const coAngle = angle + (j - (k - 1) / 2) * 0.35;
        nodes.push({
          cd,
          x: cx + R_CODIR * Math.cos(coAngle),
          y: cy + R_CODIR * Math.sin(coAngle),
          r: clamp(6 + cd.active_company_count * 2, 8, 18),
          companyUid: mandate.company_uid,
        });
      });
    });
    return nodes;
  }, [companyNodes]);

  if (!n) {
    return <p className="text-xs text-slate-400 py-4 text-center">No company mandates to display.</p>;
  }

  return (
    <div className="overflow-auto rounded-lg border border-slate-100 bg-slate-50/30">
      <svg
        viewBox={`0 0 ${SVG_W} ${SVG_H}`}
        className="w-full"
        style={{ maxHeight: 520, display: "block" }}
        aria-label={`Network graph for ${name}`}
      >
        {/* Grid background */}
        <defs>
          <pattern id="hx-grid" width="40" height="40" patternUnits="userSpaceOnUse">
            <path d="M40 0L0 0 0 40" fill="none" stroke="#f1f5f9" strokeWidth="0.6" />
          </pattern>
        </defs>
        <rect width={SVG_W} height={SVG_H} fill="url(#hx-grid)" />

        {/* Edges: center → companies */}
        {companyNodes.map(({ mandate, x, y }) => {
          const isPast = !mandate.is_current;
          return (
            <line
              key={`edge-${mandate.company_uid}`}
              x1={CX} y1={CY} x2={x} y2={y}
              stroke={isPast ? "#e2e8f0" : "#94a3b8"}
              strokeWidth={isPast ? 0.8 : 1.6}
              strokeDasharray={isPast ? "4 3" : undefined}
            />
          );
        })}

        {/* Edges: company → co-directors */}
        {coDirNodes.map((node, i) => {
          const compNode = companyNodes.find(c => c.mandate.company_uid === node.companyUid);
          if (!compNode) return null;
          return (
            <line
              key={`co-edge-${i}`}
              x1={compNode.x} y1={compNode.y}
              x2={node.x} y2={node.y}
              stroke={node.cd.is_current ? "#94a3b8" : "#e2e8f0"}
              strokeWidth={0.8}
              strokeDasharray={node.cd.is_current ? undefined : "3 2"}
            />
          );
        })}

        {/* Company rect nodes */}
        {companyNodes.map(({ mandate, x, y }) => {
          const isActive = !!mandate.is_current;
          const label = (mandate.company_name ?? mandate.company_uid ?? "").slice(0, 24);
          const sublabel = (mandate.role ?? "").slice(0, 22);
          const stroke = isActive ? "#1e293b" : "#94a3b8";
          const textColor = isActive ? "#1e293b" : "#94a3b8";
          return (
            <g key={`company-${mandate.company_uid}`} className="cursor-pointer group">
              <rect
                x={x - 76} y={y - 20}
                width={152} height={40}
                fill="white"
                stroke={stroke}
                strokeWidth={isActive ? 1.2 : 0.8}
                rx={3}
              />
              <text
                x={x} y={y - 5}
                textAnchor="middle"
                fontSize={10.5}
                fontFamily="ui-sans-serif,system-ui,sans-serif"
                fill={textColor}
                fontWeight={isActive ? "500" : "400"}
              >
                {label}
              </text>
              {sublabel && (
                <text
                  x={x} y={y + 10}
                  textAnchor="middle"
                  fontSize={9}
                  fontFamily="ui-monospace,SFMono-Regular,monospace"
                  fill="#94a3b8"
                >
                  {sublabel}
                </text>
              )}
              {mandate.company_id && (
                <title>{mandate.company_name ?? mandate.company_uid} — click to open</title>
              )}
            </g>
          );
        })}

        {/* Co-director circle nodes */}
        {coDirNodes.map((node, i) => {
          const coName = node.cd.lastname?.split(" ")[0]?.slice(0, 10) ?? "?";
          const isActive = !!node.cd.is_current;
          return (
            <g key={`codir-${i}`}>
              <circle
                cx={node.x} cy={node.y} r={node.r}
                fill="white"
                stroke={isActive ? "#475569" : "#cbd5e1"}
                strokeWidth={0.8}
              />
              <text
                x={node.x} y={node.y + 3}
                textAnchor="middle"
                fontSize={Math.max(7, node.r * 0.62)}
                fontFamily="ui-sans-serif,system-ui,sans-serif"
                fill={isActive ? "#334155" : "#94a3b8"}
              >
                {coName}
              </text>
              {/* Multi-board star badge */}
              {node.cd.active_company_count > 1 && (
                <>
                  <circle
                    cx={node.x + node.r * 0.72}
                    cy={node.y - node.r * 0.72}
                    r={5.5}
                    fill="#dc2626"
                  />
                  <text
                    x={node.x + node.r * 0.72}
                    y={node.y - node.r * 0.72 + 3.5}
                    textAnchor="middle"
                    fontSize={6.5}
                    fontFamily="ui-monospace,monospace"
                    fill="white"
                    fontWeight="bold"
                  >
                    {node.cd.active_company_count}
                  </text>
                </>
              )}
              <title>
                {[node.cd.firstname, node.cd.lastname].filter(Boolean).join(" ")}
                {node.cd.role ? ` · ${node.cd.role}` : ""}
                {node.cd.active_company_count > 1 ? ` · ${node.cd.active_company_count} boards` : ""}
              </title>
            </g>
          );
        })}

        {/* Center: person node */}
        <circle cx={CX} cy={CY} r={32} fill="#1e293b" />
        <text
          x={CX} y={CY + 1}
          textAnchor="middle"
          dominantBaseline="middle"
          fontSize={15}
          fontFamily="ui-sans-serif,system-ui,sans-serif"
          fill="white"
          fontWeight="600"
        >
          {initials}
        </text>
        <text
          x={CX} y={CY + 48}
          textAnchor="middle"
          fontSize={11}
          fontFamily="ui-sans-serif,system-ui,sans-serif"
          fill="#1e293b"
          fontWeight="500"
        >
          {name.split(" ")[0]}
        </text>
        {name.split(" ").length > 1 && (
          <text
            x={CX} y={CY + 62}
            textAnchor="middle"
            fontSize={11}
            fontFamily="ui-sans-serif,system-ui,sans-serif"
            fill="#475569"
          >
            {name.split(" ").slice(1).join(" ")}
          </text>
        )}

        {/* Legend box */}
        <g transform="translate(12, 12)">
          <rect width={168} height={76} fill="white" stroke="#e2e8f0" strokeWidth={0.8} rx={3} />
          <text x={10} y={16} fontSize={8} fontFamily="ui-monospace,monospace" fill="#94a3b8" letterSpacing="1.2">
            LEGEND
          </text>
          <circle cx={20} cy={30} r={6} fill="white" stroke="#475569" strokeWidth={0.8} />
          <text x={32} y={34} fontSize={9.5} fontFamily="ui-sans-serif,sans-serif" fill="#475569">
            co-director · size = boards
          </text>
          <rect x={14} y={42} width={12} height={8} fill="white" stroke="#1e293b" strokeWidth={0.8} rx={1} />
          <text x={32} y={50} fontSize={9.5} fontFamily="ui-sans-serif,sans-serif" fill="#475569">
            company mandate
          </text>
          <line x1={14} y1={64} x2={26} y2={64} stroke="#e2e8f0" strokeDasharray="3 2" strokeWidth={1} />
          <text x={32} y={68} fontSize={9.5} fontFamily="ui-sans-serif,sans-serif" fill="#475569">
            past / inactive
          </text>
        </g>

        {/* Stats top-right */}
        <g transform={`translate(${SVG_W - 172}, 12)`}>
          <rect width={160} height={44} fill="white" stroke="#e2e8f0" strokeWidth={0.8} rx={3} />
          <text x={10} y={16} fontSize={8} fontFamily="ui-monospace,monospace" fill="#94a3b8" letterSpacing="1.2">
            EGO GRAPH · 1 HOP
          </text>
          <text x={10} y={34} fontSize={10.5} fontFamily="ui-sans-serif,sans-serif" fill="#334155">
            {n} {n === 1 ? "company" : "companies"} · {coDirNodes.length} co-directors
          </text>
        </g>
      </svg>
    </div>
  );
}

// ── Auditor clients timeline ───────────────────────────────────────────────────

interface AuditorGroup {
  key: string;
  name: string | null;
  uid: string | null;
  location: string | null;
  legal_form: string | null;
  companies: Array<{
    id: number | null;
    uid: string | null;
    name: string | null;
    is_current: boolean | null;
    pub_date: string | null;
  }>;
}

function AuditorClientsTimeline({ group, locale }: { group: AuditorGroup; locale: string }) {
  const today = new Date().toISOString().slice(0, 10);
  const companies = group.companies;

  const dates = companies.map(c => c.pub_date).filter(Boolean) as string[];
  if (!dates.length) {
    return <p className="text-xs text-slate-400 py-3">No date data available.</p>;
  }

  const minYear = Math.floor(Math.min(...dates.map(d => new Date(d).getFullYear())));
  const maxDecimal = dateToDecimal(today) + 0.4;
  const span = maxDecimal - minYear;

  function pos(dateStr: string): number {
    return clamp(((dateToDecimal(dateStr) - minYear) / span) * 100, 0, 100);
  }

  const todayPct = pos(today);
  const years: number[] = [];
  const yearCount = Math.ceil(maxDecimal) - minYear;
  const tickEvery = yearCount > 20 ? 5 : yearCount > 10 ? 2 : 1;
  for (let y = minYear; y <= Math.ceil(maxDecimal); y++) years.push(y);

  return (
    <div className="overflow-x-auto">
      <div style={{ minWidth: 520 }}>
        <div className="grid" style={{ gridTemplateColumns: "192px 1fr" }}>
          <div />
          <div className="relative h-7 border-b border-slate-200 mb-0.5">
            {years.map(y => (
              <div
                key={y}
                className="absolute bottom-0 flex flex-col items-center"
                style={{ left: `${pos(`${y}-01-01`)}%`, transform: "translateX(-50%)" }}
              >
                <div className="w-px h-2 bg-slate-300" />
                {y % tickEvery === 0 && (
                  <span className="text-[10px] text-slate-400 font-mono leading-none mt-0.5 select-none">
                    {y}
                  </span>
                )}
              </div>
            ))}
            <div
              className="absolute top-0 bottom-0 border-l border-red-400 border-dashed opacity-70"
              style={{ left: `${todayPct}%` }}
            >
              <span className="absolute -top-0 left-1 text-[9px] text-red-400 font-mono whitespace-nowrap leading-none">now</span>
            </div>
          </div>

          {companies.map((c, i) => {
            if (!c.pub_date) return null;
            const p = pos(c.pub_date);
            // Each bar represents the point of audit engagement; show ~1 month width
            const barWidth = Math.max(1, (1 / (12 * span)) * 100);
            const isActive = !!c.is_current;

            return (
              <div key={i} className="contents">
                <div className="flex items-center gap-1.5 pr-3 py-1.5 border-b border-slate-100">
                  <span
                    className="w-1.5 h-1.5 rounded-full shrink-0"
                    style={{ background: isActive ? "#10b981" : "#cbd5e1" }}
                  />
                  {c.id ? (
                    <Link
                      href={`/${locale}/app/companies/${c.id}`}
                      className="text-xs text-blue-600 hover:underline truncate"
                    >
                      {c.name ?? c.uid}
                    </Link>
                  ) : (
                    <span className="text-xs text-slate-600 truncate">{c.name ?? c.uid ?? "—"}</span>
                  )}
                </div>
                <div className="relative h-8 border-b border-slate-100 bg-slate-50/20">
                  {years.map(y => (
                    <div
                      key={y}
                      className="absolute top-0 bottom-0 border-l border-slate-100"
                      style={{ left: `${pos(`${y}-01-01`)}%` }}
                    />
                  ))}
                  <div
                    className="absolute top-0 bottom-0 border-l border-red-300 border-dashed opacity-40"
                    style={{ left: `${todayPct}%` }}
                  />
                  <div
                    className="absolute top-1.5 h-5"
                    style={{
                      left: `${p}%`,
                      width: `${Math.max(barWidth, 2)}%`,
                      background: isActive ? "#2563eb" : "#94a3b8",
                      opacity: isActive ? 1 : 0.55,
                    }}
                    title={`${c.name ?? c.uid} · ${c.pub_date}`}
                  />
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// ── Person detail panel ────────────────────────────────────────────────────────

function PersonDetailPanel({
  entity,
  locale,
  onClose,
}: {
  entity: SogcPersonEntity;
  locale: string;
  onClose: () => void;
}) {
  const [view, setView] = useState<"timeline" | "network">("timeline");
  const [includePast, setIncludePast] = useState(true);

  const { data: network, isLoading, error } = useSWR<PersonNetworkData>(
    `person-network-${entity.id}-${includePast}`,
    () => fetchPersonNetwork(entity.id, { include_past: includePast }),
    { revalidateOnFocus: false }
  );

  const name = [entity.firstname, entity.lastname].filter(Boolean).join(" ") || entity.normalized_key;

  return (
    <div className="bg-white rounded-xl border border-slate-200 shadow-md overflow-hidden">
      {/* Panel header */}
      <div className="px-4 py-3 border-b border-slate-200 bg-slate-50/50 flex items-start gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-semibold text-slate-800">{name}</span>
            {entity.is_verified && (
              <CheckCircle size={12} className="text-emerald-500 shrink-0" />
            )}
            <span
              className={`text-[10px] font-medium px-1.5 py-0.5 rounded ${CONFIDENCE_STYLE[entity.confidence_level] ?? CONFIDENCE_STYLE.medium}`}
            >
              {entity.confidence_level}
            </span>
            {entity.is_foreign && entity.nationality && (
              <span className="text-[10px] text-slate-400 italic">{entity.nationality}</span>
            )}
          </div>
          <div className="flex flex-wrap gap-x-3 mt-0.5 text-xs text-slate-500">
            {entity.hometown_municipality && <span>von {entity.hometown_municipality}</span>}
            {entity.current_residence_municipality && (
              <span>in {entity.current_residence_municipality}</span>
            )}
          </div>
        </div>

        {/* Controls */}
        <div className="flex items-center gap-2 shrink-0 flex-wrap justify-end">
          <label className="flex items-center gap-1 text-xs text-slate-500 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={includePast}
              onChange={e => setIncludePast(e.target.checked)}
              className="rounded border-slate-300 w-3 h-3 accent-slate-600"
            />
            Past mandates
          </label>
          <div className="flex rounded-md border border-slate-200 overflow-hidden text-xs">
            <button
              onClick={() => setView("timeline")}
              className={`flex items-center gap-1 px-2.5 py-1 transition-colors ${
                view === "timeline"
                  ? "bg-slate-800 text-white"
                  : "text-slate-500 hover:bg-slate-50"
              }`}
            >
              <Clock size={11} />
              Timeline
            </button>
            <button
              onClick={() => setView("network")}
              className={`flex items-center gap-1 px-2.5 py-1 border-l border-slate-200 transition-colors ${
                view === "network"
                  ? "bg-slate-800 text-white"
                  : "text-slate-500 hover:bg-slate-50"
              }`}
            >
              <Network size={11} />
              Network
            </button>
          </div>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-slate-600 p-0.5"
            title="Close"
          >
            <X size={14} />
          </button>
        </div>
      </div>

      {/* Stats strip */}
      <div className="px-4 py-2 border-b border-slate-100 flex gap-5 text-xs text-slate-500 bg-slate-50/30">
        <span>
          <span className="font-mono font-semibold text-slate-700">{entity.active_company_count}</span>{" "}
          active {entity.active_company_count === 1 ? "company" : "companies"}
        </span>
        <span>
          <span className="font-mono font-semibold text-slate-700">{entity.appearance_count}</span>{" "}
          total appearances
        </span>
        {network && (
          <span>
            <span className="font-mono font-semibold text-slate-700">{network.mandates.length}</span>{" "}
            mandates shown
          </span>
        )}
      </div>

      {/* Content area */}
      <div className="p-4">
        {isLoading ? (
          <div className="flex items-center gap-2 text-sm text-slate-400 py-6 justify-center">
            <div className="w-3.5 h-3.5 border-2 border-slate-300 border-t-slate-500 rounded-full animate-spin" />
            Loading network data…
          </div>
        ) : error ? (
          <p className="text-xs text-red-500 py-4 text-center">
            Failed to load — try refreshing.
          </p>
        ) : !network || network.mandates.length === 0 ? (
          <p className="text-xs text-slate-400 py-4 text-center">No mandate data found.</p>
        ) : view === "timeline" ? (
          <TenureTimeline mandates={network.mandates} locale={locale} />
        ) : (
          <NetworkGraph entity={entity} mandates={network.mandates} />
        )}
      </div>
    </div>
  );
}

// ── Auditor detail panel ───────────────────────────────────────────────────────

function AuditorDetailPanel({
  group,
  locale,
  onClose,
}: {
  group: AuditorGroup;
  locale: string;
  onClose: () => void;
}) {
  return (
    <div className="bg-white rounded-xl border border-slate-200 shadow-md overflow-hidden">
      <div className="px-4 py-3 border-b border-slate-200 bg-slate-50/50 flex items-start gap-3">
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-slate-800">{group.name ?? "—"}</p>
          <div className="flex flex-wrap gap-2 mt-0.5 text-xs text-slate-500">
            {group.location && <span>{group.location}</span>}
            {group.uid && <span className="font-mono text-[10px]">{group.uid}</span>}
            {group.legal_form && <span>{group.legal_form}</span>}
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span className="text-xs text-slate-500">
            <span className="font-mono font-semibold text-slate-700">{group.companies.length}</span>{" "}
            {group.companies.length === 1 ? "client" : "clients"}
          </span>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600 p-0.5" title="Close">
            <X size={14} />
          </button>
        </div>
      </div>
      <div className="p-4">
        <p className="text-[11px] font-medium text-slate-500 uppercase tracking-wide mb-3">
          Client timeline
        </p>
        <AuditorClientsTimeline group={group} locale={locale} />
      </div>
    </div>
  );
}

// ── Person entity card ─────────────────────────────────────────────────────────

function PersonEntityCard({
  entity,
  locale,
  isSelected,
  onSelect,
}: {
  entity: SogcPersonEntity;
  locale: string;
  isSelected: boolean;
  onSelect: (id: number) => void;
}) {
  const [showFlag, setShowFlag] = useState(false);

  const name = [entity.firstname, entity.lastname].filter(Boolean).join(" ") || entity.normalized_key;

  return (
    <>
      <div
        className={`bg-white rounded-xl border shadow-sm overflow-hidden cursor-pointer transition-colors ${
          isSelected ? "border-blue-300 ring-1 ring-blue-200" : "border-slate-200 hover:border-slate-300"
        }`}
        onClick={() => onSelect(entity.id)}
      >
        <div className="px-4 py-3 flex items-start gap-3">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-sm font-semibold text-slate-800">{name}</span>
              {entity.is_verified && (
                <CheckCircle size={13} className="text-emerald-500 shrink-0" aria-label="Verified identity" />
              )}
              <span
                className={`text-[10px] font-medium px-1.5 py-0.5 rounded ${CONFIDENCE_STYLE[entity.confidence_level] ?? CONFIDENCE_STYLE.medium}`}
              >
                {entity.confidence_level}
              </span>
              {entity.is_foreign && entity.nationality && (
                <span className="text-[9px] text-slate-400 italic">{entity.nationality} national</span>
              )}
            </div>

            <div className="flex flex-wrap gap-x-3 mt-0.5">
              {entity.hometown_municipality && (
                <p className="text-xs text-slate-500">von {entity.hometown_municipality}</p>
              )}
              {entity.current_residence_municipality && (
                <p className="text-xs text-slate-500">in {entity.current_residence_municipality}</p>
              )}
            </div>

            {entity.confidence_level === "low" && (
              <p className="text-[9px] text-amber-600/80 mt-0.5 flex items-center gap-1">
                <AlertCircle size={9} />
                Identity match approximate
              </p>
            )}

            <div className="flex flex-wrap gap-1.5 mt-2">
              {entity.active_company_count > 0 && (
                <span className="text-[11px] bg-blue-50 text-blue-700 px-2 py-0.5 rounded-full border border-blue-100">
                  {entity.active_company_count} active {entity.active_company_count === 1 ? "company" : "companies"}
                </span>
              )}
              {entity.appearance_count > entity.active_company_count && (
                <span className="text-[11px] bg-slate-100 text-slate-500 px-2 py-0.5 rounded-full">
                  {entity.appearance_count} total
                </span>
              )}
            </div>
          </div>

          <div className="flex items-center gap-1 shrink-0">
            <button
              onClick={e => { e.stopPropagation(); setShowFlag(true); }}
              className="text-slate-300 hover:text-amber-500 transition-colors p-1"
              title="Report identity issue"
            >
              <AlertCircle size={14} />
            </button>
            <span className="text-slate-400 p-1">
              {isSelected ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            </span>
          </div>
        </div>
      </div>

      {showFlag && <FlagModal entity={entity} onClose={() => setShowFlag(false)} />}
    </>
  );
}

// ── Auditor card ───────────────────────────────────────────────────────────────

function groupAuditors(rows: SogcAuditor[]): AuditorGroup[] {
  const map = new Map<string, AuditorGroup>();
  for (const r of rows) {
    const key = r.auditor_uid ?? r.auditor_name_normalized ?? r.auditor_name ?? String(r.id);
    if (!map.has(key)) {
      map.set(key, {
        key,
        name: r.auditor_name,
        uid: r.auditor_uid,
        location: r.auditor_location,
        legal_form: r.auditor_legal_form,
        companies: [],
      });
    }
    const group = map.get(key)!;
    if (r.company_uid || r.company_id) {
      const alreadyIn = group.companies.some(c => c.uid === r.company_uid && c.id === r.company_id);
      if (!alreadyIn) {
        group.companies.push({
          id: r.company_id,
          uid: r.company_uid,
          name: r.company_name,
          is_current: r.is_current,
          pub_date: r.pub_date,
        });
      }
    }
  }
  return Array.from(map.values());
}

function AuditorCard({
  group,
  locale,
  isSelected,
  onSelect,
}: {
  group: AuditorGroup;
  locale: string;
  isSelected: boolean;
  onSelect: (key: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const showToggle = group.companies.length > COLLAPSE_THRESHOLD;
  const visible =
    showToggle && !expanded ? group.companies.slice(0, COLLAPSE_THRESHOLD) : group.companies;

  return (
    <div
      className={`bg-white rounded-xl border shadow-sm overflow-hidden cursor-pointer transition-colors ${
        isSelected ? "border-blue-300 ring-1 ring-blue-200" : "border-slate-200 hover:border-slate-300"
      }`}
      onClick={() => onSelect(group.key)}
    >
      <div className="px-4 py-3">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="text-sm font-semibold text-slate-800">{group.name ?? "—"}</p>
            <div className="flex flex-wrap gap-2 mt-0.5 text-[11px] text-slate-500">
              {group.location && <span>{group.location}</span>}
              {group.uid && <span className="font-mono text-[10px]">{group.uid}</span>}
              {group.legal_form && <span>{group.legal_form}</span>}
            </div>
          </div>
          <div className="flex items-center gap-1.5 shrink-0">
            <span className="text-[11px] bg-slate-100 text-slate-500 px-2 py-0.5 rounded-full">
              {group.companies.length} {group.companies.length === 1 ? "client" : "clients"}
            </span>
            <span className="text-slate-400 p-0.5">
              {isSelected ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
            </span>
          </div>
        </div>

        {/* Compact client list (only when not selected) */}
        {!isSelected && group.companies.length > 0 && (
          <div className="mt-2 space-y-1">
            {visible.map((c, i) => (
              <div key={i} className="flex items-center gap-2 text-xs">
                <span
                  className={`w-1.5 h-1.5 rounded-full shrink-0 ${c.is_current ? "bg-emerald-400" : "bg-slate-300"}`}
                />
                {c.id ? (
                  <Link
                    href={`/${locale}/app/companies/${c.id}`}
                    className="text-blue-600 hover:underline truncate"
                    onClick={e => e.stopPropagation()}
                  >
                    {c.name ?? c.uid ?? String(c.id)}
                  </Link>
                ) : (
                  <span className="text-slate-600 truncate">{c.name ?? c.uid ?? "—"}</span>
                )}
                {c.pub_date && (
                  <span className="text-slate-400 shrink-0 ml-auto">{c.pub_date.slice(0, 7)}</span>
                )}
              </div>
            ))}
            {showToggle && (
              <button
                onClick={e => { e.stopPropagation(); setExpanded(v => !v); }}
                className="flex items-center gap-1 text-[11px] text-blue-600 hover:text-blue-800 mt-1"
              >
                {expanded ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
                {expanded ? "Show less" : `Show ${group.companies.length - COLLAPSE_THRESHOLD} more`}
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────

export function PeopleClient({
  initialQ,
  initialTab,
}: {
  initialQ?: string;
  initialTab?: "persons" | "auditors";
} = {}) {
  const params = useParams();
  const locale = (params?.locale as string) ?? "de";

  const [tab, setTab] = useState<"persons" | "auditors">(initialTab ?? "persons");

  // Person filters
  const [q, setQ] = useState(initialTab !== "auditors" ? (initialQ ?? "") : "");
  const [hometown, setHometown] = useState("");
  const [nationality, setNationality] = useState("");
  const [minCompanies, setMinCompanies] = useState("");
  const [confidenceFilter, setConfidenceFilter] = useState("");
  const [sortBy, setSortBy] = useState("companies");
  const [offset, setOffset] = useState(0);
  const [selectedPersonId, setSelectedPersonId] = useState<number | null>(null);

  // Auditor filters
  const [auditorQ, setAuditorQ] = useState(initialTab === "auditors" ? (initialQ ?? "") : "");
  const [auditorLocation, setAuditorLocation] = useState("");
  const [auditorLegalForm, setAuditorLegalForm] = useState("");
  const [auditorCurrentOnly, setAuditorCurrentOnly] = useState(false);
  const [auditorSortBy, setAuditorSortBy] = useState("clients");
  const [auditorOffset, setAuditorOffset] = useState(0);
  const [selectedAuditorKey, setSelectedAuditorKey] = useState<string | null>(null);

  const { data: persons = [], isLoading: personsLoading } = useSWR(
    tab === "persons"
      ? ["people-search", q, hometown, nationality, minCompanies, confidenceFilter, sortBy, offset]
      : null,
    () =>
      searchPersonEntities({
        q: q || undefined,
        hometown: hometown || undefined,
        nationality: nationality || undefined,
        min_active_companies: minCompanies ? parseInt(minCompanies, 10) : undefined,
        confidence_level: confidenceFilter || undefined,
        sort_by: sortBy,
        limit: LIMIT,
        offset,
      }),
    { keepPreviousData: true }
  );

  const { data: auditors = [], isLoading: auditorsLoading } = useSWR(
    tab === "auditors"
      ? ["auditors-search", auditorQ, auditorLocation, auditorLegalForm, auditorCurrentOnly, auditorOffset]
      : null,
    () =>
      searchAuditors({
        q: auditorQ || undefined,
        location: auditorLocation || undefined,
        legal_form: auditorLegalForm || undefined,
        is_current: auditorCurrentOnly ? true : undefined,
        limit: LIMIT,
        offset: auditorOffset,
      }),
    { keepPreviousData: true }
  );

  const resetOffset = () => setOffset(0);
  const resetAuditorOffset = () => setAuditorOffset(0);

  function handlePersonSelect(id: number) {
    setSelectedPersonId(prev => (prev === id ? null : id));
  }

  function handleAuditorSelect(key: string) {
    setSelectedAuditorKey(prev => (prev === key ? null : key));
  }

  const selectedPerson = persons.find(p => p.id === selectedPersonId) ?? null;

  const auditorGroups = useMemo(
    () =>
      groupAuditors(auditors).sort((a, b) =>
        auditorSortBy === "name"
          ? (a.name ?? "").localeCompare(b.name ?? "")
          : b.companies.length - a.companies.length
      ),
    [auditors, auditorSortBy]
  );
  const selectedAuditorGroup = auditorGroups.find(g => g.key === selectedAuditorKey) ?? null;

  return (
    <div className="max-w-4xl mx-auto p-6 space-y-5">
      <div className="flex items-center gap-3">
        <Users size={20} className="text-slate-400" />
        <h1 className="text-xl font-semibold text-slate-800">People</h1>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-slate-200">
        {(["persons", "auditors"] as const).map(t => (
          <button
            key={t}
            onClick={() => { setTab(t); setSelectedPersonId(null); setSelectedAuditorKey(null); }}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              tab === t
                ? "border-blue-500 text-blue-700"
                : "border-transparent text-slate-500 hover:text-slate-700"
            }`}
          >
            {t === "persons" ? (
              <><Users size={13} className="inline mr-1.5" />Persons</>
            ) : (
              <><Building2 size={13} className="inline mr-1.5" />Auditors</>
            )}
          </button>
        ))}
      </div>

      {/* ── Persons tab ──────────────────────────────────────────────────────── */}
      {tab === "persons" && (
        <>
          <div className="flex flex-wrap gap-2 items-center">
            <div className="relative">
              <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
              <input
                type="search"
                placeholder="Search by name…"
                value={q}
                onChange={e => { setQ(e.target.value); resetOffset(); setSelectedPersonId(null); }}
                className="pl-8 pr-3 py-1.5 rounded-lg border border-slate-200 text-sm focus:outline-none focus:ring-2 focus:ring-blue-200 w-52"
              />
            </div>
            <input
              type="text"
              placeholder="Hometown…"
              value={hometown}
              onChange={e => { setHometown(e.target.value); resetOffset(); }}
              className="px-3 py-1.5 rounded-lg border border-slate-200 text-sm focus:outline-none focus:ring-2 focus:ring-blue-200 w-36"
            />
            <input
              type="text"
              placeholder="Nationality…"
              value={nationality}
              onChange={e => { setNationality(e.target.value); resetOffset(); }}
              className="px-3 py-1.5 rounded-lg border border-slate-200 text-sm focus:outline-none focus:ring-2 focus:ring-blue-200 w-32"
            />
            <input
              type="number"
              placeholder="Min companies"
              value={minCompanies}
              min={0}
              onChange={e => { setMinCompanies(e.target.value); resetOffset(); }}
              className="px-3 py-1.5 rounded-lg border border-slate-200 text-sm focus:outline-none focus:ring-2 focus:ring-blue-200 w-32"
            />
            <select
              value={confidenceFilter}
              onChange={e => { setConfidenceFilter(e.target.value); resetOffset(); }}
              className="px-3 py-1.5 rounded-lg border border-slate-200 text-sm text-slate-600 bg-white"
            >
              <option value="">All confidence</option>
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low (foreign)</option>
            </select>
            <select
              value={sortBy}
              onChange={e => { setSortBy(e.target.value); resetOffset(); }}
              className="px-3 py-1.5 rounded-lg border border-slate-200 text-sm text-slate-600 bg-white"
            >
              <option value="companies">Sort: Companies</option>
              <option value="confidence">Sort: Confidence</option>
              <option value="appearances">Sort: Appearances</option>
            </select>
          </div>

          {personsLoading ? (
            <p className="text-sm text-slate-400">Loading…</p>
          ) : persons.length === 0 ? (
            <p className="text-sm text-slate-400">No results.</p>
          ) : (
            <div className="space-y-2">
              {persons.map(entity => (
                <div key={entity.id}>
                  <PersonEntityCard
                    entity={entity}
                    locale={locale}
                    isSelected={selectedPersonId === entity.id}
                    onSelect={handlePersonSelect}
                  />
                  {selectedPersonId === entity.id && selectedPerson && (
                    <div className="mt-2">
                      <PersonDetailPanel
                        entity={selectedPerson}
                        locale={locale}
                        onClose={() => setSelectedPersonId(null)}
                      />
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          <div className="flex gap-2">
            <button
              disabled={offset === 0}
              onClick={() => { setOffset(Math.max(0, offset - LIMIT)); setSelectedPersonId(null); }}
              className="text-sm px-3 py-1.5 rounded border border-slate-200 disabled:opacity-40 hover:bg-slate-50"
            >
              Previous
            </button>
            <button
              disabled={persons.length < LIMIT}
              onClick={() => { setOffset(offset + LIMIT); setSelectedPersonId(null); }}
              className="text-sm px-3 py-1.5 rounded border border-slate-200 disabled:opacity-40 hover:bg-slate-50"
            >
              Next
            </button>
          </div>
        </>
      )}

      {/* ── Auditors tab ─────────────────────────────────────────────────────── */}
      {tab === "auditors" && (
        <>
          <div className="flex flex-wrap gap-2 items-center">
            <div className="relative">
              <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
              <input
                type="search"
                placeholder="Search auditor name…"
                value={auditorQ}
                onChange={e => { setAuditorQ(e.target.value); resetAuditorOffset(); setSelectedAuditorKey(null); }}
                className="pl-8 pr-3 py-1.5 rounded-lg border border-slate-200 text-sm focus:outline-none focus:ring-2 focus:ring-blue-200 w-52"
              />
            </div>
            <input
              type="text"
              placeholder="Location…"
              value={auditorLocation}
              onChange={e => { setAuditorLocation(e.target.value); resetAuditorOffset(); }}
              className="px-3 py-1.5 rounded-lg border border-slate-200 text-sm focus:outline-none focus:ring-2 focus:ring-blue-200 w-32"
            />
            <input
              type="text"
              placeholder="Legal form…"
              value={auditorLegalForm}
              onChange={e => { setAuditorLegalForm(e.target.value); resetAuditorOffset(); }}
              className="px-3 py-1.5 rounded-lg border border-slate-200 text-sm focus:outline-none focus:ring-2 focus:ring-blue-200 w-28"
            />
            <select
              value={auditorSortBy}
              onChange={e => setAuditorSortBy(e.target.value)}
              className="px-3 py-1.5 rounded-lg border border-slate-200 text-sm text-slate-600 bg-white"
            >
              <option value="clients">Sort: Most clients</option>
              <option value="name">Sort: Name A–Z</option>
            </select>
            <label className="flex items-center gap-1.5 text-sm text-slate-600 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={auditorCurrentOnly}
                onChange={e => { setAuditorCurrentOnly(e.target.checked); resetAuditorOffset(); }}
                className="rounded border-slate-300"
              />
              Current only
            </label>
          </div>

          {auditorsLoading ? (
            <p className="text-sm text-slate-400">Loading…</p>
          ) : auditorGroups.length === 0 ? (
            <p className="text-sm text-slate-400">No results.</p>
          ) : (
            <div className="space-y-2">
              {auditorGroups.map(group => (
                <div key={group.key}>
                  <AuditorCard
                    group={group}
                    locale={locale}
                    isSelected={selectedAuditorKey === group.key}
                    onSelect={handleAuditorSelect}
                  />
                  {selectedAuditorKey === group.key && selectedAuditorGroup && (
                    <div className="mt-2">
                      <AuditorDetailPanel
                        group={selectedAuditorGroup}
                        locale={locale}
                        onClose={() => setSelectedAuditorKey(null)}
                      />
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          <div className="flex gap-2">
            <button
              disabled={auditorOffset === 0}
              onClick={() => { setAuditorOffset(Math.max(0, auditorOffset - LIMIT)); setSelectedAuditorKey(null); }}
              className="text-sm px-3 py-1.5 rounded border border-slate-200 disabled:opacity-40 hover:bg-slate-50"
            >
              Previous
            </button>
            <button
              disabled={auditors.length < LIMIT}
              onClick={() => { setAuditorOffset(auditorOffset + LIMIT); setSelectedAuditorKey(null); }}
              className="text-sm px-3 py-1.5 rounded border border-slate-200 disabled:opacity-40 hover:bg-slate-50"
            >
              Next
            </button>
          </div>
        </>
      )}
    </div>
  );
}
