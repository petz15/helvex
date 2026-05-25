"use client";

import { useState, useMemo } from "react";
import Link from "next/link";
import useSWR from "swr";
import {
  ChevronLeft,
  CheckCircle,
  AlertCircle,
  ExternalLink,
  Clock,
  Network,
  X,
} from "lucide-react";
import { fetchPersonNetwork, reportPersonFlag } from "@/lib/api";
import type {
  SogcPersonEntity,
  PersonNetworkData,
  MandateItem,
  CoDirector,
} from "@/lib/types";

// ── Constants ──────────────────────────────────────────────────────────────────

const ROLE_COLORS: Record<string, { fill: string; label: string }> = {
  director: { fill: "#dc2626", label: "Board / Director" },
  officer:  { fill: "#2563eb", label: "Officer / Finance" },
  other:    { fill: "#d97706", label: "Other / Procuration" },
};
const FALLBACK_COLOR = "#94a3b8";

const CONFIDENCE_CHIP: Record<string, { bg: string; text: string; label: string }> = {
  high:   { bg: "bg-emerald-500/20", text: "text-emerald-300", label: "High confidence" },
  medium: { bg: "bg-amber-500/20",   text: "text-amber-300",   label: "Medium confidence" },
  low:    { bg: "bg-red-500/20",     text: "text-red-300",     label: "Low confidence" },
};

// ── Utilities ──────────────────────────────────────────────────────────────────

function dateToDecimal(dateStr: string): number {
  const d = new Date(dateStr);
  return d.getFullYear() + d.getMonth() / 12;
}

function clamp(v: number, lo: number, hi: number): number {
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
    } catch { /* ignore */ }
    finally { setSubmitting(false); }
  }

  const name = [entity.firstname, entity.lastname].filter(Boolean).join(" ") || entity.normalized_key;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-md p-6 mx-4">
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
    return <p className="text-xs text-slate-400 py-8 text-center">No mandate dates available.</p>;
  }

  const minYear = Math.floor(Math.min(...allDates.map(d => new Date(d).getFullYear())));
  const maxDecimal = dateToDecimal(today) + 0.4;
  const span = maxDecimal - minYear;

  function pos(dateStr: string): number {
    return clamp(((dateToDecimal(dateStr) - minYear) / span) * 100, 0, 100);
  }

  const todayPct = pos(today);
  const yearCount = Math.ceil(maxDecimal) - minYear;
  const tickEvery = yearCount > 20 ? 5 : yearCount > 10 ? 2 : 1;
  const years: number[] = [];
  for (let y = minYear; y <= Math.ceil(maxDecimal); y++) years.push(y);

  return (
    <div>
      {/* Legend */}
      <div className="flex flex-wrap gap-4 mb-4 text-[11px] text-slate-500">
        {Object.entries(ROLE_COLORS).map(([k, v]) => (
          <span key={k} className="flex items-center gap-1.5">
            <span className="w-3 h-2.5 inline-block rounded-sm" style={{ background: v.fill }} />
            {v.label}
          </span>
        ))}
        <span className="flex items-center gap-1.5 ml-auto">
          <span className="w-3 h-2.5 inline-block rounded-sm opacity-35" style={{ background: "#334155" }} />
          Past / inactive
        </span>
      </div>

      <div className="grid" style={{ gridTemplateColumns: "220px 1fr" }}>
        {/* Year axis */}
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
                <span className="text-[10px] text-slate-400 font-mono leading-none mt-0.5 select-none">{y}</span>
              )}
            </div>
          ))}
          <div className="absolute top-0 bottom-0 border-l border-red-400 border-dashed opacity-70" style={{ left: `${todayPct}%` }}>
            <span className="absolute -top-0 left-1 text-[9px] text-red-400 font-mono whitespace-nowrap leading-none">now</span>
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
              <div className="flex items-center gap-1.5 pr-4 py-2 border-b border-slate-100">
                <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: isActive ? "#10b981" : "#cbd5e1" }} />
                {m.company_id ? (
                  <Link
                    href={`/${locale}/app/companies/${m.company_id}`}
                    className="text-xs text-blue-600 hover:underline truncate leading-tight"
                    title={m.company_name ?? m.company_uid}
                  >
                    {m.company_name ?? m.company_uid}
                  </Link>
                ) : (
                  <span className="text-xs text-slate-600 truncate font-mono text-[10px]">{m.company_uid}</span>
                )}
              </div>

              <div className="relative h-10 border-b border-slate-100 bg-slate-50/20">
                {years.map(y => (
                  <div key={y} className="absolute top-0 bottom-0 border-l border-slate-100" style={{ left: `${pos(`${y}-01-01`)}%` }} />
                ))}
                <div className="absolute top-0 bottom-0 border-l border-red-300 border-dashed opacity-40" style={{ left: `${todayPct}%` }} />
                <div
                  className="absolute top-2.5 h-5 flex items-center px-2 overflow-hidden rounded-sm transition-opacity"
                  style={{
                    left: `${fromPct}%`,
                    width: `${widthPct}%`,
                    background: color,
                    opacity: isActive ? 1 : 0.45,
                  }}
                  title={`${m.company_name ?? m.company_uid} · ${m.role ?? ""} · ${m.date_from ?? "?"} → ${m.date_to ?? "present"}`}
                >
                  {widthPct > 5 && (
                    <span className="text-white text-[10px] font-mono truncate leading-none">
                      {barLabel.length > 34 ? barLabel.slice(0, 32) + "…" : barLabel}
                    </span>
                  )}
                </div>
                {!isActive && m.date_to && (
                  <div className="absolute top-1 bottom-1 w-px bg-slate-600" style={{ left: `${toPct}%` }} />
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Network graph (full-page, wider SVG) ──────────────────────────────────────

const SVG_W = 1100;
const SVG_H = 620;
const CX = SVG_W / 2;
const CY = SVG_H / 2;
const R_COMPANY = 230;
const R_CODIR = 90;

function NetworkGraph({
  entity,
  mandates,
  locale,
}: {
  entity: SogcPersonEntity;
  mandates: MandateItem[];
  locale: string;
}) {
  const name = [entity.firstname, entity.lastname].filter(Boolean).join(" ") || entity.normalized_key;
  const initials = name.split(" ").map(s => s[0] ?? "").join("").slice(0, 2).toUpperCase();
  const n = mandates.length;

  const companyNodes = useMemo(
    () => mandates.map((m, i) => {
      const angle = (i / n) * 2 * Math.PI - Math.PI / 2;
      return { mandate: m, x: CX + R_COMPANY * Math.cos(angle), y: CY + R_COMPANY * Math.sin(angle), angle };
    }),
    [mandates, n]
  );

  type CoDirNode = { cd: CoDirector; x: number; y: number; r: number; companyUid: string };

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
    return <p className="text-xs text-slate-400 py-8 text-center">No company mandates to display.</p>;
  }

  return (
    <div className="rounded-lg border border-slate-100 bg-slate-50/30 overflow-hidden">
      <svg
        viewBox={`0 0 ${SVG_W} ${SVG_H}`}
        className="w-full"
        style={{ maxHeight: 600, display: "block" }}
        aria-label={`Network graph for ${name}`}
      >
        <defs>
          <pattern id="hx-grid-dp" width="40" height="40" patternUnits="userSpaceOnUse">
            <path d="M40 0L0 0 0 40" fill="none" stroke="#f1f5f9" strokeWidth="0.6" />
          </pattern>
        </defs>
        <rect width={SVG_W} height={SVG_H} fill="url(#hx-grid-dp)" />

        {/* Edges center → companies */}
        {companyNodes.map(({ mandate, x, y }) => (
          <line
            key={`edge-${mandate.company_uid}`}
            x1={CX} y1={CY} x2={x} y2={y}
            stroke={!mandate.is_current ? "#e2e8f0" : "#94a3b8"}
            strokeWidth={!mandate.is_current ? 0.8 : 1.6}
            strokeDasharray={!mandate.is_current ? "4 3" : undefined}
          />
        ))}

        {/* Edges company → co-directors */}
        {coDirNodes.map((node, i) => {
          const compNode = companyNodes.find(c => c.mandate.company_uid === node.companyUid);
          if (!compNode) return null;
          return (
            <line
              key={`co-edge-${i}`}
              x1={compNode.x} y1={compNode.y} x2={node.x} y2={node.y}
              stroke={node.cd.is_current ? "#94a3b8" : "#e2e8f0"}
              strokeWidth={0.8}
              strokeDasharray={node.cd.is_current ? undefined : "3 2"}
            />
          );
        })}

        {/* Company rect nodes */}
        {companyNodes.map(({ mandate, x, y }) => {
          const isActive = !!mandate.is_current;
          const label = (mandate.company_name ?? mandate.company_uid ?? "").slice(0, 28);
          const sublabel = (mandate.role ?? "").slice(0, 26);
          const roleColor = ROLE_COLORS[mandate.role_category ?? ""]?.fill ?? FALLBACK_COLOR;
          return (
            <g key={`company-${mandate.company_uid}`}>
              <rect
                x={x - 86} y={y - 22} width={172} height={44}
                fill="white"
                stroke={isActive ? "#cbd5e1" : "#e2e8f0"}
                strokeWidth={isActive ? 1.2 : 0.8}
                rx={4}
              />
              {/* Role colour accent bar on left */}
              <rect
                x={x - 86} y={y - 22} width={4} height={44}
                fill={roleColor}
                rx={4}
                style={{ opacity: isActive ? 1 : 0.3 }}
              />
              <text
                x={x + 2} y={y - 6}
                textAnchor="middle" fontSize={10.5}
                fontFamily="ui-sans-serif,system-ui,sans-serif"
                fill={isActive ? "#1e293b" : "#94a3b8"}
                fontWeight={isActive ? "500" : "400"}
              >
                {label}
              </text>
              {sublabel && (
                <text
                  x={x + 2} y={y + 10}
                  textAnchor="middle" fontSize={9}
                  fontFamily="ui-monospace,SFMono-Regular,monospace"
                  fill="#94a3b8"
                >
                  {sublabel}
                </text>
              )}
              <title>{mandate.company_name ?? mandate.company_uid}{mandate.role ? ` · ${mandate.role}` : ""}</title>
            </g>
          );
        })}

        {/* Co-director circle nodes */}
        {coDirNodes.map((node, i) => {
          const initial = (node.cd.firstname?.[0] ?? "") + ".";
          const lastName = node.cd.lastname?.split(" ")[0]?.slice(0, 9) ?? "?";
          const coLabel = `${initial} ${lastName}`.trim();
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
                fontSize={Math.max(7, node.r * 0.6)}
                fontFamily="ui-sans-serif,system-ui,sans-serif"
                fill={isActive ? "#334155" : "#94a3b8"}
              >
                {coLabel}
              </text>
              {node.cd.active_company_count > 1 && (
                <>
                  <circle cx={node.x + node.r * 0.72} cy={node.y - node.r * 0.72} r={5.5} fill="#dc2626" />
                  <text
                    x={node.x + node.r * 0.72} y={node.y - node.r * 0.72 + 3.5}
                    textAnchor="middle" fontSize={6.5}
                    fontFamily="ui-monospace,monospace" fill="white" fontWeight="bold"
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

        {/* Center person node */}
        <circle cx={CX} cy={CY} r={38} fill="#0f172a" />
        <text x={CX} y={CY + 1} textAnchor="middle" dominantBaseline="middle" fontSize={16} fontFamily="ui-sans-serif,system-ui,sans-serif" fill="white" fontWeight="600">{initials}</text>
        <text x={CX} y={CY + 58} textAnchor="middle" fontSize={11} fontFamily="ui-sans-serif,system-ui,sans-serif" fill="#1e293b" fontWeight="500">
          {name.split(" ")[0]}
        </text>
        {name.split(" ").length > 1 && (
          <text x={CX} y={CY + 74} textAnchor="middle" fontSize={11} fontFamily="ui-sans-serif,system-ui,sans-serif" fill="#475569">
            {name.split(" ").slice(1).join(" ")}
          </text>
        )}

        {/* Legend */}
        <g transform="translate(14, 14)">
          <rect width={194} height={102} fill="white" stroke="#e2e8f0" strokeWidth={0.8} rx={4} />
          <text x={12} y={18} fontSize={8} fontFamily="ui-monospace,monospace" fill="#94a3b8" letterSpacing="1.2">LEGEND</text>
          {Object.entries(ROLE_COLORS).map(([k, v], i) => (
            <g key={k} transform={`translate(0, ${i * 19})`}>
              <rect x={12} y={26} width={14} height={10} fill="white" stroke={v.fill} strokeWidth={1.5} rx={1} />
              <text x={32} y={35} fontSize={9.5} fontFamily="ui-sans-serif,sans-serif" fill="#475569">{v.label}</text>
            </g>
          ))}
          <circle cx={18} cy={92} r={5.5} fill="white" stroke="#475569" strokeWidth={0.8} />
          <text x={30} y={96} fontSize={9.5} fontFamily="ui-sans-serif,sans-serif" fill="#475569">co-director (size = boards)</text>
        </g>

        {/* Stats */}
        <g transform={`translate(${SVG_W - 188}, 14)`}>
          <rect width={174} height={44} fill="white" stroke="#e2e8f0" strokeWidth={0.8} rx={4} />
          <text x={12} y={18} fontSize={8} fontFamily="ui-monospace,monospace" fill="#94a3b8" letterSpacing="1.2">EGO GRAPH · 1 HOP</text>
          <text x={12} y={36} fontSize={10.5} fontFamily="ui-sans-serif,sans-serif" fill="#334155">
            {n} {n === 1 ? "company" : "companies"} · {coDirNodes.length} co-dirs
          </text>
        </g>
      </svg>
    </div>
  );
}

// ── Main export ────────────────────────────────────────────────────────────────

export function PersonDetailClient({
  entity,
  locale,
}: {
  entity: SogcPersonEntity;
  locale: string;
}) {
  const [view, setView] = useState<"timeline" | "network">("timeline");
  const [includePast, setIncludePast] = useState(true);
  const [showFlag, setShowFlag] = useState(false);

  const { data: network, isLoading, error } = useSWR<PersonNetworkData>(
    `person-network-${entity.id}-${includePast}`,
    () => fetchPersonNetwork(entity.id, { include_past: includePast }),
    { revalidateOnFocus: false }
  );

  const name = [entity.firstname, entity.lastname].filter(Boolean).join(" ") || entity.normalized_key;
  const initials = name.split(" ").map(s => s[0] ?? "").join("").slice(0, 2).toUpperCase();
  const conf = CONFIDENCE_CHIP[entity.confidence_level] ?? CONFIDENCE_CHIP.medium;

  const roleBreakdown = useMemo(() => {
    if (!network?.mandates) return {} as Record<string, number>;
    const counts: Record<string, number> = {};
    for (const m of network.mandates) {
      const k = m.role_category ?? "other";
      counts[k] = (counts[k] ?? 0) + 1;
    }
    return counts;
  }, [network?.mandates]);

  const firstSeen = useMemo(() => {
    if (!network?.mandates) return null;
    const dates = network.mandates.map(m => m.date_from).filter(Boolean) as string[];
    return dates.length ? dates.sort()[0] : null;
  }, [network?.mandates]);

  return (
    <div className="min-h-screen bg-slate-50">
      {/* Back nav */}
      <div className="bg-white border-b border-slate-200">
        <div className="max-w-5xl mx-auto px-6 py-3">
          <Link
            href={`/${locale}/app/people`}
            className="inline-flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-800 transition-colors"
          >
            <ChevronLeft size={15} />
            People
          </Link>
        </div>
      </div>

      {/* Hero header */}
      <div className="bg-slate-900">
        <div className="max-w-5xl mx-auto px-6 py-8">
          <div className="flex items-start gap-6">
            {/* Initials avatar */}
            <div className="shrink-0 w-16 h-16 rounded-full bg-white/10 border border-white/20 flex items-center justify-center text-white text-xl font-bold tracking-tight select-none">
              {initials}
            </div>

            {/* Identity block */}
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2.5 flex-wrap">
                <h1 className="text-2xl font-semibold text-white tracking-tight">{name}</h1>
                {entity.is_verified && (
                  <span className="flex items-center gap-1 text-emerald-400 text-xs font-medium">
                    <CheckCircle size={12} /> Verified
                  </span>
                )}
                <span className={`text-[11px] font-medium px-2 py-0.5 rounded-full ${conf.bg} ${conf.text}`}>
                  {conf.label}
                </span>
                {entity.is_foreign && entity.nationality && (
                  <span className="text-xs text-slate-400 italic">{entity.nationality} national</span>
                )}
              </div>

              <div className="flex flex-wrap gap-x-4 mt-1.5 text-sm text-slate-400">
                {entity.hometown_municipality && <span>von {entity.hometown_municipality}</span>}
                {entity.current_residence_municipality && <span>in {entity.current_residence_municipality}</span>}
              </div>

              {entity.identity_notes && (
                <p className="mt-2.5 text-xs text-slate-400 bg-white/5 rounded-lg px-3 py-2 max-w-xl leading-relaxed border border-white/10">
                  {entity.identity_notes}
                </p>
              )}

              <div className="flex items-center gap-4 mt-3 flex-wrap">
                {entity.linkedin_url && (
                  <a
                    href={entity.linkedin_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1.5 text-xs text-blue-400 hover:text-blue-300 transition-colors"
                  >
                    <ExternalLink size={11} /> LinkedIn
                  </a>
                )}
                <button
                  onClick={() => setShowFlag(true)}
                  className="inline-flex items-center gap-1.5 text-xs text-slate-500 hover:text-amber-400 transition-colors"
                >
                  <AlertCircle size={11} /> Report issue
                </button>
              </div>
            </div>

            {/* Counts */}
            <div className="shrink-0 text-right space-y-1">
              <div>
                <span className="text-3xl font-bold text-white">{entity.active_company_count}</span>
                <p className="text-xs text-slate-400 mt-0.5">active {entity.active_company_count === 1 ? "company" : "companies"}</p>
              </div>
              <div className="mt-2">
                <span className="text-xl font-semibold text-slate-300">{entity.appearance_count}</span>
                <p className="text-xs text-slate-500">total appearances</p>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Stats strip */}
      {network && (
        <div className="bg-white border-b border-slate-200">
          <div className="max-w-5xl mx-auto px-6 py-2.5 flex flex-wrap gap-5 text-xs text-slate-500">
            <span>
              <span className="font-mono font-semibold text-slate-700">{network.mandates.length}</span>{" "}
              mandates shown
            </span>
            {Object.entries(roleBreakdown).map(([role, count]) => {
              const color = ROLE_COLORS[role]?.fill ?? FALLBACK_COLOR;
              return (
                <span key={role} className="flex items-center gap-1.5">
                  <span className="w-2 h-2 rounded-sm inline-block" style={{ background: color }} />
                  <span className="font-mono font-semibold" style={{ color }}>{count}</span>
                  {" "}{ROLE_COLORS[role]?.label ?? role}
                </span>
              );
            })}
            {firstSeen && (
              <span className="ml-auto text-slate-400">
                First seen <span className="font-mono font-medium text-slate-600">{firstSeen.slice(0, 7)}</span>
              </span>
            )}
          </div>
        </div>
      )}

      {/* Content */}
      <div className="max-w-5xl mx-auto px-6 py-6 space-y-4">
        {/* View controls */}
        <div className="flex items-center gap-3 flex-wrap">
          <div className="flex rounded-lg border border-slate-200 overflow-hidden text-sm bg-white shadow-sm">
            <button
              onClick={() => setView("timeline")}
              className={`flex items-center gap-1.5 px-3 py-2 transition-colors ${
                view === "timeline" ? "bg-slate-900 text-white" : "text-slate-500 hover:bg-slate-50"
              }`}
            >
              <Clock size={13} /> Timeline
            </button>
            <button
              onClick={() => setView("network")}
              className={`flex items-center gap-1.5 px-3 py-2 border-l border-slate-200 transition-colors ${
                view === "network" ? "bg-slate-900 text-white" : "text-slate-500 hover:bg-slate-50"
              }`}
            >
              <Network size={13} /> Network
            </button>
          </div>

          <label className="flex items-center gap-1.5 text-sm text-slate-600 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={includePast}
              onChange={e => setIncludePast(e.target.checked)}
              className="rounded border-slate-300 w-3.5 h-3.5 accent-slate-600"
            />
            Include past mandates
          </label>
        </div>

        {/* Visualization card */}
        <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-5">
          {isLoading ? (
            <div className="flex items-center gap-2 text-sm text-slate-400 py-12 justify-center">
              <div className="w-4 h-4 border-2 border-slate-300 border-t-slate-600 rounded-full animate-spin" />
              Loading mandate data…
            </div>
          ) : error ? (
            <p className="text-xs text-red-500 py-8 text-center">Failed to load — try refreshing.</p>
          ) : !network || network.mandates.length === 0 ? (
            <p className="text-xs text-slate-400 py-8 text-center">No mandate data found for this person.</p>
          ) : view === "timeline" ? (
            <TenureTimeline mandates={network.mandates} locale={locale} />
          ) : (
            <NetworkGraph entity={entity} mandates={network.mandates} locale={locale} />
          )}
        </div>
      </div>

      {showFlag && <FlagModal entity={entity} onClose={() => setShowFlag(false)} />}
    </div>
  );
}
