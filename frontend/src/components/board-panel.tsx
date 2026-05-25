"use client";

import { useState, useEffect, useRef } from "react";
import Link from "next/link";
import useSWR from "swr";
import { useParams, useRouter } from "next/navigation";
import * as d3 from "d3";
import { Users, Building2, AlertCircle, CheckCircle, X, BarChart2, Network } from "lucide-react";
import { fetchCompanyPersons, fetchCompanyAuditors, reportPersonFlag, fetchCurrentUser } from "@/lib/api";
import type { SogcPersonAppearance, SogcAuditor } from "@/lib/types";

// ── Role colours ──────────────────────────────────────────────────────────────

const ROLE_COLORS: Record<string, string> = {
  director: "#dc2626",
  officer:  "#2563eb",
  other:    "#d97706",
};
const FALLBACK_COLOR = "#94a3b8";

// ── Confidence badge (superadmin only) ────────────────────────────────────────

const CONFIDENCE_BADGE: Record<string, string> = {
  high:   "bg-emerald-50 text-emerald-700 border-emerald-200",
  medium: "bg-amber-50 text-amber-700 border-amber-200",
  low:    "bg-red-50 text-red-700 border-red-200",
};
const CONFIDENCE_LABEL: Record<string, string> = {
  high: "High", medium: "Medium", low: "Low — approx.",
};

function ConfidenceBadge({ level }: { level: string }) {
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded border font-medium ${CONFIDENCE_BADGE[level] ?? CONFIDENCE_BADGE.medium}`}>
      {CONFIDENCE_LABEL[level] ?? level}
    </span>
  );
}

// ── Flag modal ────────────────────────────────────────────────────────────────

function FlagModal({ entityId, personName, onClose }: { entityId: number; personName: string; onClose: () => void }) {
  const [flagType, setFlagType] = useState<"should_merge" | "should_split">("should_merge");
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    try {
      await reportPersonFlag(entityId, { flag_type: flagType, reason: reason || null });
      setDone(true);
    } catch { /* ignore */ }
    finally { setSubmitting(false); }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-md p-6 mx-4">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-semibold text-slate-800">Report identity issue — {personName}</h3>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600"><X size={16} /></button>
        </div>
        {done ? (
          <div className="flex items-center gap-2 text-emerald-700 text-sm"><CheckCircle size={16} /> Reported. Thank you.</div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4">
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

// ── Person card (list fallback) ───────────────────────────────────────────────

function PersonCard({
  appearance, locale, isSuperAdmin,
}: {
  appearance: SogcPersonAppearance;
  locale: string;
  isSuperAdmin?: boolean;
}) {
  const [showFlag, setShowFlag] = useState(false);
  const displayName = appearance.raw_excerpt
    ? appearance.raw_excerpt.split(",").slice(0, 2).join(",").trim()
    : String(appearance.person_entity_id);

  return (
    <>
      <div className="flex flex-col gap-1 rounded-lg border border-slate-200 px-3 py-2 bg-slate-50/50 group">
        <div className="flex items-start justify-between gap-1">
          <Link
            href={`/${locale}/app/people/${appearance.person_entity_id}`}
            className="text-sm font-medium text-slate-800 hover:text-blue-600 leading-tight truncate"
          >
            {displayName}
          </Link>
          <button
            onClick={() => setShowFlag(true)}
            className="opacity-0 group-hover:opacity-100 transition-opacity text-slate-300 hover:text-amber-500 shrink-0"
            title="Report identity issue"
          >
            <AlertCircle size={13} />
          </button>
        </div>
        {appearance.role && (
          <p className="text-xs text-slate-500 leading-tight truncate">{appearance.role}</p>
        )}
        {isSuperAdmin && (
          <div className="mt-0.5"><ConfidenceBadge level="medium" /></div>
        )}
      </div>
      {showFlag && (
        <FlagModal entityId={appearance.person_entity_id} personName={displayName} onClose={() => setShowFlag(false)} />
      )}
    </>
  );
}

// ── Role bar chart ────────────────────────────────────────────────────────────

function RoleBarChart({ persons }: { persons: SogcPersonAppearance[] }) {
  const roleCounts = new Map<string, { count: number; active: number; category: string }>();
  for (const p of persons) {
    const role = p.role?.trim() || p.role_category || "—";
    const ex = roleCounts.get(role);
    if (ex) { ex.count++; if (p.is_current) ex.active++; }
    else roleCounts.set(role, { count: 1, active: p.is_current ? 1 : 0, category: p.role_category ?? "other" });
  }

  const entries = [...roleCounts.entries()].sort((a, b) => b[1].count - a[1].count);
  const max = Math.max(...entries.map(([, v]) => v.count), 1);

  if (!entries.length) return null;

  return (
    <div>
      <div className="space-y-2.5">
        {entries.map(([role, { count, active, category }]) => {
          const color = ROLE_COLORS[category] ?? FALLBACK_COLOR;
          return (
            <div key={role} className="flex items-center gap-3 min-w-0">
              <span
                className="text-xs text-slate-600 shrink-0 text-right"
                style={{ width: 200 }}
                title={role}
              >
                {role.length > 30 ? role.slice(0, 28) + "…" : role}
              </span>
              <div className="flex-1 h-5 rounded overflow-hidden relative" style={{ background: `${color}20` }}>
                <div className="h-full rounded" style={{ width: `${(active / max) * 100}%`, background: color }} />
                {count > active && (
                  <div
                    className="absolute inset-y-0 rounded"
                    style={{
                      left: `${(active / max) * 100}%`,
                      width: `${((count - active) / max) * 100}%`,
                      background: color,
                      opacity: 0.25,
                    }}
                  />
                )}
              </div>
              <span className="text-[11px] font-mono text-slate-500 shrink-0 w-10 text-right">
                {active !== count ? `${active}/${count}` : String(count)}
              </span>
            </div>
          );
        })}
      </div>
      <div className="flex flex-wrap items-center gap-4 mt-4 pt-3 border-t border-slate-100 text-[11px] text-slate-400">
        {Object.entries(ROLE_COLORS).map(([k, color]) => (
          <span key={k} className="flex items-center gap-1.5">
            <span className="w-3 h-2.5 inline-block rounded-sm" style={{ background: color }} />
            {k}
          </span>
        ))}
        <span className="ml-auto italic">active / total</span>
      </div>
    </div>
  );
}

// ── D3 types ──────────────────────────────────────────────────────────────────

interface BPNode extends d3.SimulationNodeDatum {
  id: string;
  type: "company" | "person";
  label: string;
  sublabel?: string;
  color?: string;
  isActive: boolean;
  href?: string;
  _dragMoved?: boolean;
}
interface BPLink extends d3.SimulationLinkDatum<BPNode> {
  isActive: boolean;
  color: string;
}

// ── D3 board network graph ────────────────────────────────────────────────────

function BoardNetworkGraph({
  persons, companyName, locale,
}: {
  persons: SogcPersonAppearance[];
  companyName: string;
  locale: string;
}) {
  const router = useRouter();
  const routerRef = useRef(router);
  useEffect(() => { routerRef.current = router; }, [router]);

  const svgRef = useRef<SVGSVGElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [tooltip, setTooltip] = useState<{ x: number; y: number; label: string; sublabel?: string } | null>(null);

  useEffect(() => {
    if (!svgRef.current || !persons.length) return;

    const svgEl = svgRef.current;
    const W = svgEl.parentElement?.clientWidth ?? 600;
    const H = 420;
    d3.select(svgEl).selectAll("*").remove();
    svgEl.setAttribute("viewBox", `0 0 ${W} ${H}`);

    const nodes: BPNode[] = [];
    const links: BPLink[] = [];

    nodes.push({ id: "company", type: "company", label: companyName.slice(0, 26), isActive: true, fx: W / 2, fy: H / 2 });

    const n = persons.length;
    const R0 = Math.min(W * 0.36, H * 0.38, 160);

    persons.forEach((p, i) => {
      const angle = (i / n) * 2 * Math.PI - Math.PI / 2;
      const nid = `person-${p.id}`;
      const label = p.raw_excerpt
        ? p.raw_excerpt.split(",").slice(0, 2).join(",").trim().slice(0, 22)
        : `#${p.person_entity_id}`;
      nodes.push({
        id: nid, type: "person",
        label,
        sublabel: p.role?.slice(0, 24),
        color: ROLE_COLORS[p.role_category ?? ""] ?? FALLBACK_COLOR,
        isActive: !!p.is_current,
        href: `/${locale}/app/people/${p.person_entity_id}`,
        x: W / 2 + R0 * Math.cos(angle),
        y: H / 2 + R0 * Math.sin(angle),
      });
      links.push({
        source: "company", target: nid,
        isActive: !!p.is_current,
        color: ROLE_COLORS[p.role_category ?? ""] ?? FALLBACK_COLOR,
      });
    });

    const svg = d3.select(svgEl);

    svg.append("defs")
      .append("pattern").attr("id", "bp-grid").attr("width", 40).attr("height", 40)
      .attr("patternUnits", "userSpaceOnUse")
      .append("path").attr("d", "M40 0L0 0 0 40")
      .attr("fill", "none").attr("stroke", "#f1f5f9").attr("stroke-width", 0.6);

    const g = svg.append("g");
    g.append("rect").attr("width", W).attr("height", H).attr("fill", "url(#bp-grid)");

    const zoom = d3.zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.3, 4])
      .on("zoom", ev => g.attr("transform", ev.transform.toString()));
    svg.call(zoom);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    svg.on("dblclick.zoom", () => svg.transition().duration(400).call(zoom.transform as any, d3.zoomIdentity));

    const sim = d3.forceSimulation<BPNode>(nodes)
      .force("link", d3.forceLink<BPNode, BPLink>(links).id(d => d.id).distance(140).strength(0.7))
      .force("charge", d3.forceManyBody<BPNode>().strength(d => d.type === "company" ? -700 : -200))
      .force("center", d3.forceCenter(W / 2, H / 2).strength(0.05))
      .force("collide", d3.forceCollide<BPNode>().radius(55))
      .alphaDecay(0.025);

    const linkSel = g.append("g")
      .selectAll<SVGLineElement, BPLink>("line")
      .data(links).join("line")
      .attr("stroke", d => d.isActive ? d.color : "#e2e8f0")
      .attr("stroke-width", d => d.isActive ? 1.6 : 0.8)
      .attr("stroke-dasharray", d => d.isActive ? null : "4 3")
      .attr("stroke-opacity", d => d.isActive ? 0.55 : 0.3);

    function makeDrag() {
      return d3.drag<SVGGElement, BPNode>()
        .on("start", (ev, d) => {
          d._dragMoved = false;
          if (!ev.active) sim.alphaTarget(0.3).restart();
          d.fx = d.x; d.fy = d.y;
        })
        .on("drag", (ev, d) => { d._dragMoved = true; d.fx = ev.x; d.fy = ev.y; })
        .on("end", (ev, d) => {
          if (!ev.active) sim.alphaTarget(0);
          if (!d._dragMoved && d.href) routerRef.current.push(d.href);
          if (d.type !== "company") { d.fx = null; d.fy = null; }
        });
    }

    // Person nodes
    const personSel = g.append("g")
      .selectAll<SVGGElement, BPNode>("g")
      .data(nodes.filter(d => d.type === "person"))
      .join("g")
      .style("cursor", "pointer")
      .call(makeDrag());

    personSel.append("rect")
      .attr("x", -58).attr("y", -18).attr("width", 116).attr("height", 36)
      .attr("fill", "white")
      .attr("stroke", d => d.isActive ? (d.color ?? FALLBACK_COLOR) : "#e2e8f0")
      .attr("stroke-width", d => d.isActive ? 1.5 : 0.8)
      .attr("rx", 4)
      .attr("opacity", d => d.isActive ? 1 : 0.65);
    personSel.append("rect") // role accent bar
      .attr("x", -58).attr("y", -18).attr("width", 4).attr("height", 36)
      .attr("fill", d => d.color ?? FALLBACK_COLOR)
      .attr("rx", 4)
      .attr("opacity", d => d.isActive ? 1 : 0.3);
    personSel.append("text")
      .attr("text-anchor", "middle").attr("y", -4)
      .attr("font-size", 9.5).attr("font-family", "ui-sans-serif,system-ui,sans-serif")
      .attr("fill", d => d.isActive ? "#1e293b" : "#94a3b8")
      .attr("font-weight", d => d.isActive ? "500" : "400")
      .text(d => d.label);
    personSel.append("text")
      .attr("text-anchor", "middle").attr("y", 9)
      .attr("font-size", 7.5).attr("font-family", "ui-monospace,SFMono-Regular,monospace")
      .attr("fill", "#94a3b8")
      .text(d => d.sublabel ? (d.sublabel.length > 22 ? d.sublabel.slice(0, 20) + "…" : d.sublabel) : "");
    personSel
      .on("mouseenter", (ev, d) => {
        const cr = containerRef.current?.getBoundingClientRect();
        if (cr) setTooltip({ x: ev.clientX - cr.left, y: ev.clientY - cr.top - 12, label: d.label, sublabel: d.sublabel });
      })
      .on("mouseleave", () => setTooltip(null));

    // Company center node
    const companyNode = nodes[0];
    const companySel = g.append("g").datum(companyNode).call(makeDrag());
    companySel.append("rect")
      .attr("x", -64).attr("y", -24).attr("width", 128).attr("height", 48)
      .attr("fill", "#0f172a").attr("rx", 6);
    companySel.append("text")
      .attr("text-anchor", "middle").attr("dominant-baseline", "middle")
      .attr("font-size", 10).attr("font-family", "ui-sans-serif,system-ui,sans-serif")
      .attr("fill", "white").attr("font-weight", "600")
      .text(companyNode.label);

    sim.on("tick", () => {
      linkSel
        .attr("x1", d => (d.source as BPNode).x ?? 0).attr("y1", d => (d.source as BPNode).y ?? 0)
        .attr("x2", d => (d.target as BPNode).x ?? 0).attr("y2", d => (d.target as BPNode).y ?? 0);
      personSel.attr("transform", d => `translate(${d.x ?? 0},${d.y ?? 0})`);
      companySel.attr("transform", `translate(${companyNode.x ?? W / 2},${companyNode.y ?? H / 2})`);
    });

    const t = setTimeout(() => { companyNode.fx = null; companyNode.fy = null; }, 1500);
    return () => { sim.stop(); clearTimeout(t); };
  }, [persons, locale, companyName]);

  if (!persons.length) return <p className="text-xs text-slate-400 py-6 text-center">No board members.</p>;

  return (
    <div ref={containerRef} className="relative overflow-hidden rounded-lg bg-slate-50/30 border border-slate-100" style={{ height: 420 }}>
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
      <div className="absolute bottom-2 right-2 text-[10px] text-slate-400 bg-white/80 rounded px-2 py-1 border border-slate-100 select-none">
        Scroll to zoom · drag · double-click to reset
      </div>
      <div className="absolute top-2 left-2 bg-white/90 backdrop-blur-sm rounded-lg border border-slate-200 px-2.5 py-2 text-[11px] text-slate-500 space-y-1 shadow-sm">
        {Object.entries(ROLE_COLORS).map(([k, color]) => (
          <div key={k} className="flex items-center gap-1.5">
            <span className="w-2.5 h-2 inline-block rounded-sm" style={{ background: color }} />
            {k}
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Auditor card ──────────────────────────────────────────────────────────────

function AuditorCard({ auditor }: { auditor: SogcAuditor }) {
  return (
    <div className="flex flex-col gap-1 rounded-lg border border-slate-200 px-3 py-2 bg-slate-50/50">
      <p className="text-sm font-medium text-slate-800 leading-tight">{auditor.auditor_name}</p>
      <div className="flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
        {auditor.auditor_location && <span>{auditor.auditor_location}</span>}
        {auditor.auditor_uid && <span className="font-mono text-[10px] text-slate-400">{auditor.auditor_uid}</span>}
        {auditor.auditor_legal_form && <span>{auditor.auditor_legal_form}</span>}
      </div>
    </div>
  );
}

// ── Board panel ───────────────────────────────────────────────────────────────

export function BoardPanel({ companyUid }: { companyUid: string }) {
  const params = useParams();
  const locale = (params?.locale as string) ?? "de";
  const [view, setView] = useState<"bar" | "network">("bar");

  const { data: persons = [] } = useSWR(
    `company-persons-${companyUid}`,
    () => fetchCompanyPersons(companyUid, true),
    { revalidateOnFocus: false },
  );
  const { data: auditors = [] } = useSWR(
    `company-auditors-${companyUid}`,
    () => fetchCompanyAuditors(companyUid, true),
    { revalidateOnFocus: false },
  );
  const { data: me } = useSWR("me", fetchCurrentUser, { revalidateOnFocus: false });
  const isSuperAdmin = !!me?.is_superadmin;

  if (persons.length === 0 && auditors.length === 0) return null;

  const companyName = persons[0]?.company_name ?? companyUid;

  return (
    <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
      {/* Header + toggle */}
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-sm font-semibold text-slate-700 flex items-center gap-1.5">
          <Users size={15} className="text-slate-400" />
          Board &amp; Officers
          <span className="ml-1.5 text-[11px] font-normal text-slate-400">
            {persons.filter(p => p.is_current).length} active · {persons.length} total
          </span>
        </h2>
        {persons.length > 0 && (
          <div className="flex rounded-lg border border-slate-200 overflow-hidden text-xs bg-white">
            <button
              onClick={() => setView("bar")}
              className={`flex items-center gap-1.5 px-2.5 py-1.5 transition-colors ${view === "bar" ? "bg-slate-900 text-white" : "text-slate-500 hover:bg-slate-50"}`}
            >
              <BarChart2 size={12} /> Bar
            </button>
            <button
              onClick={() => setView("network")}
              className={`flex items-center gap-1.5 px-2.5 py-1.5 border-l border-slate-200 transition-colors ${view === "network" ? "bg-slate-900 text-white" : "text-slate-500 hover:bg-slate-50"}`}
            >
              <Network size={12} /> Network
            </button>
          </div>
        )}
      </div>

      {/* Visualization */}
      {persons.length > 0 && (
        <div className="mb-4">
          {view === "bar"
            ? <RoleBarChart persons={persons} />
            : <BoardNetworkGraph persons={persons} companyName={companyName} locale={locale} />
          }
        </div>
      )}

      {/* Auditors */}
      {auditors.length > 0 && (
        <div className={persons.length > 0 ? "pt-4 border-t border-slate-100" : ""}>
          <p className="text-[11px] font-semibold text-slate-400 uppercase tracking-wide mb-2 flex items-center gap-1">
            <Building2 size={11} />
            Auditors
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {auditors.map(a => <AuditorCard key={a.id} auditor={a} />)}
          </div>
        </div>
      )}
    </div>
  );
}
