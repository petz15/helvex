"use client";

import { useState, useMemo, useEffect, useRef } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import * as d3 from "d3";
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
} from "@/lib/types";

// ── Constants ──────────────────────────────────────────────────────────────────

const ROLE_COLORS: Record<string, { fill: string; label: string }> = {
  director: { fill: "#dc2626", label: "Director" },
  officer:  { fill: "#2563eb", label: "Officer" },
  other:    { fill: "#d97706", label: "Other" },
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

  // Build legend from actual SOGC publication titles (signature_type ?? role), coloured by role_category
  // eslint-disable-next-line react-hooks/preserve-manual-memoization
  const roleLegend = useMemo(() => {
    const map = new Map<string, string>(); // title → fill colour
    for (const m of mandates) {
      const title = m.signature_type?.trim() || m.role?.trim();
      if (!title || map.has(title)) continue;
      map.set(title, ROLE_COLORS[m.role_category ?? ""]?.fill ?? FALLBACK_COLOR);
    }
    return [...map.entries()];
  }, [mandates]);

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
      {/* Legend — actual SOGC titles, coloured by role category */}
      <div className="flex flex-wrap gap-x-4 gap-y-1.5 mb-4 text-[11px] text-slate-500">
        {roleLegend.map(([title, fill]) => (
          <span key={title} className="flex items-center gap-1.5">
            <span className="w-3 h-2.5 inline-block rounded-sm shrink-0" style={{ background: fill }} />
            {title}
          </span>
        ))}
        <span className="flex items-center gap-1.5 ml-auto">
          <span className="w-3 h-2.5 inline-block rounded-sm opacity-35 shrink-0" style={{ background: "#334155" }} />
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

// ── D3 graph types ────────────────────────────────────────────────────────────

interface GNode extends d3.SimulationNodeDatum {
  id: string;
  type: "person" | "company" | "codir";
  label: string;
  sublabel?: string;
  href?: string;
  color?: string;
  isActive: boolean;
  r?: number;
  _dragMoved?: boolean;
}

interface GLink extends d3.SimulationLinkDatum<GNode> {
  isActive: boolean;
}

// ── Network graph (D3 force-directed, interactive) ────────────────────────────

function NetworkGraph({
  entity,
  mandates,
  locale,
}: {
  entity: SogcPersonEntity;
  mandates: MandateItem[];
  locale: string;
}) {
  const router = useRouter();
  const routerRef = useRef(router);
  useEffect(() => { routerRef.current = router; }, [router]);

  const svgRef = useRef<SVGSVGElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [tooltip, setTooltip] = useState<{
    x: number; y: number; label: string; sublabel?: string;
  } | null>(null);

  const name = [entity.firstname, entity.lastname].filter(Boolean).join(" ") || entity.normalized_key;
  const initials = name.split(" ").map(s => s[0] ?? "").join("").slice(0, 2).toUpperCase();
  const n = mandates.length;

  useEffect(() => {
    if (!svgRef.current || !n) return;

    const svgEl = svgRef.current;
    const W = svgEl.parentElement?.clientWidth ?? 800;
    const H = 560;

    d3.select(svgEl).selectAll("*").remove();
    svgEl.setAttribute("viewBox", `0 0 ${W} ${H}`);

    // Build graph data
    const nodes: GNode[] = [];
    const links: GLink[] = [];
    const personId = `person-${entity.id}`;

    nodes.push({ id: personId, type: "person", label: name, isActive: true, fx: W / 2, fy: H / 2 });

    const R0 = Math.min(W * 0.36, H * 0.36, 190);

    mandates.forEach((m, i) => {
      const angle = (i / n) * 2 * Math.PI - Math.PI / 2;
      const cid = `company-${m.company_uid}`;
      nodes.push({
        id: cid, type: "company",
        label: (m.company_name ?? m.company_uid ?? "").slice(0, 28),
        sublabel: (m.role ?? "").slice(0, 26),
        href: m.company_id ? `/${locale}/app/companies/${m.company_id}` : undefined,
        color: ROLE_COLORS[m.role_category ?? ""]?.fill ?? FALLBACK_COLOR,
        isActive: !!m.is_current,
        x: W / 2 + R0 * Math.cos(angle),
        y: H / 2 + R0 * Math.sin(angle),
      });
      links.push({ source: personId, target: cid, isActive: !!m.is_current });

      m.co_directors.forEach((cd, j) => {
        const coAngle = angle + (j - (m.co_directors.length - 1) / 2) * 0.4;
        const cdId = `codir-${i}-${j}`;
        nodes.push({
          id: cdId, type: "codir",
          label: `${cd.firstname?.[0] ?? ""}. ${cd.lastname?.split(" ")[0]?.slice(0, 9) ?? "?"}`.trim(),
          href: cd.entity_id ? `/${locale}/app/people/${cd.entity_id}` : undefined,
          isActive: !!cd.is_current,
          r: clamp(6 + (cd.active_company_count ?? 0) * 2, 8, 18),
          x: W / 2 + R0 * Math.cos(angle) + 80 * Math.cos(coAngle),
          y: H / 2 + R0 * Math.sin(angle) + 80 * Math.sin(coAngle),
        });
        links.push({ source: cid, target: cdId, isActive: !!cd.is_current });
      });
    });

    // SVG + zoom
    const svg = d3.select(svgEl);

    svg.append("defs")
      .append("pattern").attr("id", "d3bg").attr("width", 40).attr("height", 40)
      .attr("patternUnits", "userSpaceOnUse")
      .append("path").attr("d", "M40 0L0 0 0 40")
      .attr("fill", "none").attr("stroke", "#f1f5f9").attr("stroke-width", 0.6);

    const g = svg.append("g");
    g.append("rect").attr("width", W).attr("height", H).attr("fill", "url(#d3bg)");

    const zoom = d3.zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.25, 4])
      .on("zoom", ev => g.attr("transform", ev.transform.toString()));
    svg.call(zoom);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    svg.on("dblclick.zoom", () => svg.transition().duration(400).call(zoom.transform as any, d3.zoomIdentity));

    // Simulation
    const sim = d3.forceSimulation<GNode>(nodes)
      .force("link", d3.forceLink<GNode, GLink>(links).id(d => d.id)
        .distance(l => (l.source as GNode).type === "person" || (l.target as GNode).type === "person" ? 160 : 80)
        .strength(0.7))
      .force("charge", d3.forceManyBody<GNode>().strength(d =>
        d.type === "person" ? -900 : d.type === "company" ? -400 : -150))
      .force("center", d3.forceCenter(W / 2, H / 2).strength(0.04))
      .force("collide", d3.forceCollide<GNode>().radius(d => d.type === "company" ? 100 : (d.r ?? 10) + 10))
      .alphaDecay(0.025);

    // Links
    const linkSel = g.append("g")
      .selectAll<SVGLineElement, GLink>("line")
      .data(links).join("line")
      .attr("stroke", d => d.isActive ? "#94a3b8" : "#e2e8f0")
      .attr("stroke-width", d => d.isActive ? 1.6 : 0.8)
      .attr("stroke-dasharray", d => d.isActive ? null : "4 3");

    // Drag factory — click-to-navigate if no drag movement occurred
    function makeDrag(releasePin: boolean) {
      return d3.drag<SVGGElement, GNode>()
        .on("start", (ev, d) => {
          d._dragMoved = false;
          if (!ev.active) sim.alphaTarget(0.3).restart();
          d.fx = d.x; d.fy = d.y;
        })
        .on("drag", (ev, d) => { d._dragMoved = true; d.fx = ev.x; d.fy = ev.y; })
        .on("end", (ev, d) => {
          if (!ev.active) sim.alphaTarget(0);
          if (!d._dragMoved && d.href) routerRef.current.push(d.href);
          if (releasePin) { d.fx = null; d.fy = null; }
        });
    }

    // Company nodes
    const companySel = g.append("g")
      .selectAll<SVGGElement, GNode>("g")
      .data(nodes.filter(d => d.type === "company"))
      .join("g")
      .style("cursor", d => d.href ? "pointer" : "default")
      .call(makeDrag(true));

    companySel.append("rect")
      .attr("x", -86).attr("y", -22).attr("width", 172).attr("height", 44)
      .attr("fill", "white")
      .attr("stroke", d => d.isActive ? "#cbd5e1" : "#e2e8f0")
      .attr("stroke-width", d => d.isActive ? 1.2 : 0.8)
      .attr("rx", 4);
    companySel.append("rect") // role accent bar
      .attr("x", -86).attr("y", -22).attr("width", 4).attr("height", 44)
      .attr("fill", d => d.color ?? FALLBACK_COLOR).attr("rx", 4)
      .attr("opacity", d => d.isActive ? 1 : 0.3);
    companySel.append("text")
      .attr("text-anchor", "middle").attr("y", -6)
      .attr("font-size", 10.5).attr("font-family", "ui-sans-serif,system-ui,sans-serif")
      .attr("fill", d => d.isActive ? "#1e293b" : "#94a3b8")
      .attr("font-weight", d => d.isActive ? "500" : "400")
      .text(d => d.label);
    companySel.append("text")
      .attr("text-anchor", "middle").attr("y", 10)
      .attr("font-size", 9).attr("font-family", "ui-monospace,SFMono-Regular,monospace")
      .attr("fill", "#94a3b8").text(d => d.sublabel ?? "");
    companySel
      .on("mouseenter", (ev, d) => {
        const cr = containerRef.current?.getBoundingClientRect();
        if (cr) setTooltip({ x: ev.clientX - cr.left, y: ev.clientY - cr.top - 12, label: d.label, sublabel: d.sublabel });
      })
      .on("mouseleave", () => setTooltip(null));

    // Co-director nodes
    const codirSel = g.append("g")
      .selectAll<SVGGElement, GNode>("g")
      .data(nodes.filter(d => d.type === "codir"))
      .join("g")
      .style("cursor", d => d.href ? "pointer" : "default")
      .call(makeDrag(true));

    codirSel.append("circle")
      .attr("r", d => d.r ?? 10).attr("fill", "white")
      .attr("stroke", d => d.isActive ? "#475569" : "#cbd5e1").attr("stroke-width", 0.8);
    codirSel.append("text")
      .attr("text-anchor", "middle").attr("y", 3)
      .attr("font-size", d => Math.max(7, (d.r ?? 10) * 0.6))
      .attr("font-family", "ui-sans-serif,system-ui,sans-serif")
      .attr("fill", d => d.isActive ? "#334155" : "#94a3b8")
      .text(d => d.label);
    codirSel
      .on("mouseenter", (ev, d) => {
        const cr = containerRef.current?.getBoundingClientRect();
        if (cr) setTooltip({ x: ev.clientX - cr.left, y: ev.clientY - cr.top - 12, label: d.label });
      })
      .on("mouseleave", () => setTooltip(null));

    // Center person node (pin persists after drag)
    const personNode = nodes[0];
    const personSel = g.append("g").datum(personNode).call(makeDrag(false));
    personSel.append("circle").attr("r", 38).attr("fill", "#0f172a");
    personSel.append("text")
      .attr("text-anchor", "middle").attr("dominant-baseline", "middle")
      .attr("font-size", 16).attr("font-family", "ui-sans-serif,system-ui,sans-serif")
      .attr("fill", "white").attr("font-weight", "600").text(initials);
    const nameParts = name.split(" ");
    personSel.append("text")
      .attr("text-anchor", "middle").attr("y", 54)
      .attr("font-size", 11).attr("font-family", "ui-sans-serif,system-ui,sans-serif")
      .attr("fill", "#1e293b").attr("font-weight", "500").text(nameParts[0] ?? "");
    if (nameParts.length > 1) {
      personSel.append("text")
        .attr("text-anchor", "middle").attr("y", 68)
        .attr("font-size", 11).attr("font-family", "ui-sans-serif,system-ui,sans-serif")
        .attr("fill", "#475569").text(nameParts.slice(1).join(" "));
    }

    // Tick
    sim.on("tick", () => {
      linkSel
        .attr("x1", d => (d.source as GNode).x ?? 0).attr("y1", d => (d.source as GNode).y ?? 0)
        .attr("x2", d => (d.target as GNode).x ?? 0).attr("y2", d => (d.target as GNode).y ?? 0);
      companySel.attr("transform", d => `translate(${d.x ?? 0},${d.y ?? 0})`);
      codirSel.attr("transform", d => `translate(${d.x ?? 0},${d.y ?? 0})`);
      personSel.attr("transform", `translate(${personNode.x ?? W / 2},${personNode.y ?? H / 2})`);
    });

    // Release person pin after 1.5 s so it can float freely
    const t = setTimeout(() => { personNode.fx = null; personNode.fy = null; }, 1500);
    return () => { sim.stop(); clearTimeout(t); };
  }, [entity.id, locale, mandates, name, initials]);

  if (!n) return <p className="text-xs text-slate-400 py-8 text-center">No company mandates to display.</p>;

  return (
    <div
      ref={containerRef}
      className="relative rounded-lg border border-slate-100 bg-slate-50/30 overflow-hidden"
      style={{ height: 560 }}
    >
      <svg ref={svgRef} className="w-full h-full" />

      {tooltip && (
        <div
          className="absolute pointer-events-none z-10 bg-slate-900 text-white text-xs rounded-lg px-3 py-2 shadow-xl max-w-xs -translate-x-1/2 -translate-y-full"
          style={{ left: tooltip.x, top: tooltip.y }}
        >
          <div className="font-medium">{tooltip.label}</div>
          {tooltip.sublabel && <div className="text-slate-400 mt-0.5">{tooltip.sublabel}</div>}
        </div>
      )}

      <div className="absolute bottom-3 right-3 text-[10px] text-slate-400 bg-white/80 backdrop-blur-sm rounded px-2 py-1 border border-slate-100 select-none">
        Scroll to zoom · drag to pan/move · double-click to reset
      </div>

      <div className="absolute top-3 left-3 bg-white/90 backdrop-blur-sm rounded-lg border border-slate-200 px-3 py-2.5 text-[11px] text-slate-500 space-y-1.5 shadow-sm">
        <div className="text-[9px] font-mono text-slate-400 uppercase tracking-widest mb-1.5">Legend</div>
        {Object.entries(ROLE_COLORS).map(([k, v]) => (
          <div key={k} className="flex items-center gap-1.5">
            <span className="w-3 h-2.5 inline-block rounded-sm" style={{ background: "white", borderLeft: `3px solid ${v.fill}`, border: `1px solid ${v.fill}` }} />
            {v.label}
          </div>
        ))}
        <div className="flex items-center gap-1.5">
          <span className="w-3 h-3 inline-block rounded-full border border-slate-400 bg-white" />
          co-director (size = boards)
        </div>
      </div>
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

  // eslint-disable-next-line react-hooks/preserve-manual-memoization
  const roleBreakdown = useMemo(() => {
    if (!network?.mandates) return {} as Record<string, number>;
    const counts: Record<string, number> = {};
    for (const m of network.mandates) {
      const k = m.role_category ?? "other";
      counts[k] = (counts[k] ?? 0) + 1;
    }
    return counts;
  }, [network?.mandates]);

  // eslint-disable-next-line react-hooks/preserve-manual-memoization
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
