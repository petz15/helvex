"use client";
import { useState, useMemo, useCallback } from "react";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import { fetchNogaHierarchy, fetchNogaDescription, semanticSearch } from "@/lib/api";
import type { NogaNode, NogaAnnotation, SemanticSearchResult } from "@/lib/api";
import { useI18n } from "@/i18n/context";

type NogaDict = ReturnType<typeof useI18n>["dict"]["app"]["nogaClient"];

// App colour palette — matches the rest of the product
const T = {
  paper:    "#ffffff",
  paper2:   "#f8fafc",
  paper3:   "#f1f5f9",
  ink:      "#0f172a",
  ink2:     "#1e293b",
  ink3:     "#475569",
  ink4:     "#94a3b8",
  rule:     "#e2e8f0",
  rule2:    "#f1f5f9",
  accent:   "#2563eb",
  accentBg: "#eff6ff",
  green:    "#16a34a",
} as const;

const MONO  = "'JetBrains Mono','IBM Plex Mono',ui-monospace,monospace";
const SERIF = "'Instrument Serif',Georgia,'Times New Roman',serif";
const SANS  = "ui-sans-serif,system-ui,-apple-system,sans-serif";

const LEVEL_META = [
  { de: "Abschnitt", en: "section",  hintKey: "hint1letter" },
  { de: "Abteilung", en: "division", hintKey: "hint2digits" },
  { de: "Gruppe",    en: "group",    hintKey: "hint3digits" },
  { de: "Klasse",    en: "class",    hintKey: "hint4digits" },
  { de: "Art",       en: "type",     hintKey: "hint5digits" },
] as const satisfies readonly { de: string; en: string; hintKey: keyof NogaDict }[];

const ANNOT_LABEL_KEYS: Record<string, keyof NogaDict> = {
  INCLUDES: "annotIncludes",
  INCLUDES_ALSO: "annotAlsoIncludes",
  EXCLUDES: "annotExcludes",
};
const SHOWN_ANNOT_TYPES = new Set(["INCLUDES", "INCLUDES_ALSO", "EXCLUDES"]);

// ── Helpers ───────────────────────────────────────────────────────────────────

function lbl(node: NogaNode, locale: string): string {
  return node.labels?.[locale] || node.labels?.de || node.labels?.fr || node.labels?.it || node.labels?.en || node.label;
}

function findNode(nodes: NogaNode[], code: string): NogaNode | null {
  for (const n of nodes) {
    if (n.code === code) return n;
    const hit = findNode(n.children, code);
    if (hit) return hit;
  }
  return null;
}

function matches(node: NogaNode, q: string, locale: string): boolean {
  return node.code.toLowerCase().includes(q) || lbl(node, locale).toLowerCase().includes(q);
}

function onlyAlpha(nodes: NogaNode[]): NogaNode[] {
  return nodes.filter((n) => /^[A-Za-z]$/.test(n.code));
}

// ── SectionStrip ──────────────────────────────────────────────────────────────

function SectionStrip({
  sections, selected, locale, onSelect,
}: {
  sections: NogaNode[];
  selected: string | null;
  locale: string;
  onSelect: (c: string) => void;
}) {
  const { dict } = useI18n();
  const t = dict.app.nogaClient;
  const total = sections.reduce((s, n) => s + n.count, 0);
  return (
    <div style={{ display: "flex", alignItems: "stretch", height: 26, marginTop: 10 }}>
      <div style={{ display: "flex", flex: 1, border: `1px solid ${T.rule}`, overflow: "hidden" }}>
        {sections.map((s, i) => {
          const on = s.code === selected;
          return (
            <div
              key={s.code}
              title={`${s.code} · ${lbl(s, locale)} · ${s.count.toLocaleString()}`}
              onClick={() => onSelect(s.code)}
              style={{
                flex: s.count || 1,
                borderRight: i < sections.length - 1 ? `1px solid ${on ? T.accent : T.rule}` : "none",
                background: on ? T.accent : i % 2 ? T.paper3 : T.paper2,
                color: on ? "#fff" : T.ink3,
                display: "flex", alignItems: "center", justifyContent: "center",
                fontFamily: MONO, fontSize: 10,
                cursor: "pointer", position: "relative",
                userSelect: "none",
              }}
            >
              {s.code}
              {on && (
                <span style={{
                  position: "absolute", bottom: 0, left: 0, right: 0, height: 2,
                  background: "#1d4ed8",
                }} />
              )}
            </div>
          );
        })}
      </div>
      <div style={{
        display: "flex", alignItems: "center", padding: "0 10px",
        fontFamily: MONO, fontSize: 9.5, color: T.ink3, whiteSpace: "nowrap",
        background: T.paper2, border: `1px solid ${T.rule}`, borderLeft: "none",
      }}>
        {total.toLocaleString()} · {sections.length} {t.sections}
      </div>
    </div>
  );
}

// ── MillerRow ─────────────────────────────────────────────────────────────────

function MillerRow({
  node, on, isLeaf, max, locale, onClick,
}: {
  node: NogaNode;
  on: boolean;
  isLeaf: boolean;
  max: number;
  locale: string;
  onClick: () => void;
}) {
  const bar = Math.max(2, Math.round((node.count / Math.max(max, 1)) * 100));
  const label = lbl(node, locale);
  const spineOpacity = on ? 1 : Math.min(1, 0.15 + node.count / Math.max(max, 1));
  return (
    <div
      onClick={onClick}
      style={{
        position: "relative",
        padding: "7px 8px 7px 12px",
        borderBottom: `1px solid ${T.rule2}`,
        background: on ? T.accent : "transparent",
        cursor: "pointer", userSelect: "none",
      }}
    >
      <span style={{
        position: "absolute", left: 0, top: 0, bottom: 0, width: 2,
        background: on ? "rgba(255,255,255,0.5)" : T.accent, opacity: spineOpacity,
      }} />
      <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
        <span style={{
          fontFamily: MONO, fontSize: 10.5,
          color: on ? "rgba(255,255,255,0.7)" : T.ink3,
          minWidth: node.code.length > 4 ? 58 : 36, flexShrink: 0,
        }}>
          {node.code}
        </span>
        <span style={{
          flex: 1, fontSize: 12, lineHeight: 1.25,
          color: on ? "#fff" : T.ink,
          overflow: "hidden", textOverflow: "ellipsis",
          display: "-webkit-box",
          WebkitLineClamp: 2,
          WebkitBoxOrient: "vertical" as const,
        }}>
          {label}
        </span>
        {!isLeaf && (
          <span style={{
            fontFamily: MONO, fontSize: 11,
            color: on ? "rgba(255,255,255,0.6)" : T.ink4, marginLeft: 2, flexShrink: 0,
          }}>›</span>
        )}
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 5 }}>
        <span style={{
          fontFamily: MONO, fontSize: 9.5,
          color: on ? "rgba(255,255,255,0.7)" : T.ink3, minWidth: 44, flexShrink: 0,
        }}>
          {node.count.toLocaleString()}
        </span>
        <div style={{ flex: 1, height: 3, background: on ? "rgba(255,255,255,.25)" : T.rule2 }}>
          <div style={{ height: "100%", width: bar + "%", background: on ? "#fff" : T.ink3 }} />
        </div>
      </div>
    </div>
  );
}

// ── MillerColumn ──────────────────────────────────────────────────────────────

function MillerColumn({
  depth, items, selected, locale, searchQuery, onSelect,
}: {
  depth: number;
  items: NogaNode[];
  selected: string | null;
  locale: string;
  searchQuery: string;
  onSelect: (c: string) => void;
}) {
  const { dict } = useI18n();
  const t = dict.app.nogaClient;
  const meta      = LEVEL_META[depth];
  const q         = searchQuery.toLowerCase();
  const shown     = q ? items.filter((n) => matches(n, q, locale)) : items;
  const max       = Math.max(...items.map((n) => n.count), 1);
  const isLeafLevel = depth === 4;

  return (
    <div style={{
      borderRight: `1px solid ${T.rule}`,
      display: "flex", flexDirection: "column", minHeight: 0,
      background: T.paper,
    }}>
      <div style={{
        padding: "8px 10px 8px 12px",
        borderBottom: `1px solid ${T.rule}`,
        display: "flex", alignItems: "baseline", justifyContent: "space-between",
        background: T.paper2, flexShrink: 0,
      }}>
        <div>
          <div style={{ fontFamily: MONO, fontSize: 9.5, color: T.ink3, letterSpacing: "0.1em" }}>
            L{depth + 1} · {meta.de.toUpperCase()}
          </div>
          <div style={{ fontFamily: MONO, fontSize: 9, color: T.ink4 }}>{t[meta.hintKey]}</div>
        </div>
        <span style={{ fontFamily: MONO, fontSize: 9.5, color: T.ink3 }}>{shown.length}</span>
      </div>
      <div style={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
        {shown.map((node) => (
          <MillerRow
            key={node.code}
            node={node}
            on={node.code === selected}
            isLeaf={isLeafLevel || node.children.length === 0}
            max={max}
            locale={locale}
            onClick={() => onSelect(node.code)}
          />
        ))}
        {isLeafLevel && shown.length > 0 && (
          <div style={{
            padding: "8px 12px", color: T.ink4,
            fontFamily: MONO, fontSize: 10,
            borderTop: `1px dashed ${T.rule}`,
          }}>
            {t.leafLevel}
          </div>
        )}
        {shown.length === 0 && (
          <div style={{
            padding: "16px 12px", color: T.ink4,
            fontFamily: MONO, fontSize: 10, textAlign: "center",
          }}>
            {items.length === 0 ? t.selectParent : t.noMatches}
          </div>
        )}
      </div>
    </div>
  );
}

// ── DetailPane ────────────────────────────────────────────────────────────────

function DetailPane({
  pathNodes, locale, onBrowse,
}: {
  pathNodes: (NogaNode | null)[];
  locale: string;
  onBrowse: (code: string) => void;
}) {
  const { dict } = useI18n();
  const t = dict.app.nogaClient;
  const deepest = [...pathNodes].reverse().find(Boolean) ?? null;

  const { data: descData } = useSWR(
    deepest ? ["noga-desc", deepest.code] : null,
    () => fetchNogaDescription(deepest!.code),
    { dedupingInterval: 3_600_000 },
  );

  if (!deepest) {
    return (
      <div style={{
        borderLeft: `1px solid ${T.rule}`, background: T.paper2,
        display: "flex", flexDirection: "column",
        alignItems: "center", justifyContent: "center",
        padding: 32, color: T.ink4, fontFamily: MONO, fontSize: 10, textAlign: "center",
      }}>
        <div style={{ color: T.ink3, marginBottom: 8, letterSpacing: "0.1em" }}>{t.selectNode}</div>
        {t.clickToExplore}
      </div>
    );
  }

  const crumbs = pathNodes
    .map((n, i) => (n ? { code: n.code, label: lbl(n, locale), i } : null))
    .filter(Boolean) as { code: string; label: string; i: number }[];

  const leafLabel = lbl(deepest, locale);

  const altLangs = Object.entries(deepest.labels ?? {})
    .filter(([lang]) => lang !== locale && lang !== "de")
    .slice(0, 2)
    .map(([lang, v]) => `${lang} "${v}"`)
    .join("  ·  ");

  const annots = (descData?.annotations ?? []).filter(
    (a: NogaAnnotation) => SHOWN_ANNOT_TYPES.has(a.type),
  );

  return (
    <div style={{
      borderLeft: `1px solid ${T.rule}`, background: T.paper2,
      display: "flex", flexDirection: "column", minHeight: 0, overflowY: "auto",
    }}>
      {/* PATH breadcrumb + heading */}
      <div style={{
        padding: "12px 18px 10px",
        borderBottom: `1px solid ${T.rule}`,
        background: T.paper, flexShrink: 0,
      }}>
        <div style={{ fontFamily: MONO, fontSize: 9.5, color: T.ink3, letterSpacing: "0.08em" }}>
          PATH
        </div>
        <div style={{ marginTop: 6, display: "flex", flexWrap: "wrap", gap: 5, alignItems: "baseline" }}>
          {crumbs.map((c, ci, arr) => (
            <span key={c.code} style={{ display: "inline-flex", alignItems: "baseline", gap: 5 }}>
              <span style={{
                display: "inline-flex", alignItems: "baseline", gap: 5,
                padding: "2px 6px 3px",
                background: ci === arr.length - 1 ? T.accent : "transparent",
                color: ci === arr.length - 1 ? "#fff" : T.ink2,
                border: `1px solid ${ci === arr.length - 1 ? T.accent : T.rule}`,
                fontFamily: MONO, fontSize: 10,
              }}>
                <span style={{ color: ci === arr.length - 1 ? "rgba(255,255,255,0.7)" : T.ink4 }}>{c.code}</span>
                <span style={{
                  fontSize: 10.5,
                  maxWidth: ci === arr.length - 1 ? 200 : 100,
                  overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                }}>{c.label}</span>
              </span>
              {ci < arr.length - 1 && (
                <span style={{ fontFamily: MONO, color: T.ink4, fontSize: 11 }}>›</span>
              )}
            </span>
          ))}
        </div>
        <h2 style={{
          fontFamily: SERIF, fontStyle: "italic", fontWeight: 400,
          fontSize: 24, lineHeight: 1.05, letterSpacing: "-0.015em",
          margin: "10px 0 0", color: T.ink,
        }}>
          {leafLabel}.
        </h2>
        {altLangs && (
          <div style={{ fontFamily: MONO, fontSize: 10, color: T.ink3, marginTop: 6 }}>
            {altLangs}
          </div>
        )}
      </div>

      {/* Stats row */}
      <div style={{ display: "flex", borderBottom: `1px solid ${T.rule}`, background: T.paper, flexShrink: 0 }}>
        {[
          { k: t.companies, v: deepest.count.toLocaleString(), sub: t.inCode.replace("{code}", deepest.code), accent: false },
          { k: t.freshLabel, v: "—",  sub: t.notWired, accent: true },
          { k: t.avgScore,  v: "—",  sub: t.combined, accent: false },
        ].map((s, si) => (
          <div key={s.k} style={{
            flex: 1, padding: "10px 12px",
            borderRight: si < 2 ? `1px solid ${T.rule}` : "none",
          }}>
            <div style={{ fontFamily: MONO, fontSize: 9.5, color: T.ink3, letterSpacing: "0.08em" }}>{s.k}</div>
            <div style={{
              fontFamily: MONO, fontWeight: 500, fontSize: 22, lineHeight: 1.1,
              color: s.accent ? T.accent : s.v === "—" ? T.ink4 : T.ink,
              marginTop: 2, letterSpacing: "-0.02em",
            }}>{s.v}</div>
            <div style={{ fontFamily: MONO, fontSize: 9.5, color: T.ink4, marginTop: 2 }}>{s.sub}</div>
          </div>
        ))}
      </div>

      {/* Browse CTA */}
      <div style={{ padding: "12px 18px", borderBottom: `1px solid ${T.rule}`, flexShrink: 0 }}>
        <button
          onClick={() => onBrowse(deepest.code)}
          style={{
            display: "inline-flex", alignItems: "center", gap: 6,
            padding: "6px 12px",
            border: `1px solid ${T.accent}`, background: T.accent, color: "#fff",
            fontFamily: MONO, fontSize: 11, cursor: "pointer", borderRadius: 2,
          }}
        >
          {t.openAllInResults.replace("{count}", deepest.count.toLocaleString())}
        </button>
      </div>

      {/* NOGA annotations */}
      {annots.length > 0 ? (
        annots.map((a: NogaAnnotation, i: number) => {
          const text = a.text?.[locale] || a.text?.de || a.text?.en || "";
          return (
            <div key={i} style={{ padding: "12px 18px", borderBottom: `1px solid ${T.rule}` }}>
              <div style={{
                fontFamily: MONO, fontSize: 9.5, color: T.ink3, letterSpacing: "0.1em",
              }}>
                {ANNOT_LABEL_KEYS[a.type] ? t[ANNOT_LABEL_KEYS[a.type]] : a.type}
              </div>
              <div style={{ fontSize: 12, color: T.ink2, marginTop: 5, lineHeight: 1.55 }}>
                {text}
              </div>
            </div>
          );
        })
      ) : (
        <div style={{ padding: "12px 18px", color: T.ink4, fontFamily: MONO, fontSize: 10 }}>
          —
        </div>
      )}
    </div>
  );
}

// ── Main ──────────────────────────────────────────────────────────────────────

export default function NogaClient({ locale }: { locale: string }) {
  const { dict } = useI18n();
  const t = dict.app.nogaClient;
  const router = useRouter();
  const [path, setPath]                     = useState<(string | null)[]>([null, null, null, null, null]);
  const [search, setSearch]                 = useState("");
  const [semanticQuery, setSemanticQuery]   = useState("");
  const [semanticLoading, setSemanticLoading] = useState(false);
  const [semanticResults, setSemanticResults] = useState<SemanticSearchResult[]>([]);

  const { data: hierarchy = [], isLoading } = useSWR(
    "noga-hierarchy", fetchNogaHierarchy, { dedupingInterval: 3_600_000 }
  );

  const sections = useMemo(() => onlyAlpha(hierarchy), [hierarchy]);
  const total    = useMemo(() => sections.reduce((s, n) => s + n.count, 0), [sections]);

  // code → ordered ancestor codes, built once from the full tree
  const ancestorMap = useMemo(() => {
    const map = new Map<string, string[]>();
    function walk(nodes: NogaNode[], ancestors: string[]) {
      for (const n of nodes) {
        map.set(n.code, ancestors);
        walk(n.children, [...ancestors, n.code]);
      }
    }
    walk(hierarchy, []);
    return map;
  }, [hierarchy]);

  // global search: max 25 matches across all 5 levels
  const globalResults = useMemo(() => {
    if (search.length < 2) return [];
    const q = search.toLowerCase();
    const out: { node: NogaNode; crumb: string }[] = [];
    function walk(nodes: NogaNode[]) {
      for (const n of nodes) {
        if (out.length >= 25) return;
        if (matches(n, q, locale)) {
          out.push({ node: n, crumb: (ancestorMap.get(n.code) ?? []).join(" › ") });
        }
        walk(n.children);
      }
    }
    walk(hierarchy);
    return out;
  }, [search, hierarchy, locale, ancestorMap]);

  const navigateToCode = useCallback((code: string) => {
    const ancestors = ancestorMap.get(code) ?? [];
    const full = [...ancestors, code];
    const next: (string | null)[] = [null, null, null, null, null];
    full.forEach((c, i) => { next[i] = c; });
    setPath(next);
    setSearch("");
    setSemanticQuery("");
    setSemanticResults([]);
  }, [ancestorMap]);

  const columns = useMemo(() =>
    Array.from({ length: 5 }, (_, d): NogaNode[] => {
      if (d === 0) return sections;
      const parentCode = path[d - 1];
      if (!parentCode) return [];
      return findNode(hierarchy, parentCode)?.children ?? [];
    }),
  [sections, hierarchy, path]);

  const pathNodes = useMemo(
    () => path.map((code) => (code ? findNode(hierarchy, code) : null)),
    [hierarchy, path]
  );

  const selectAt = useCallback((depth: number, code: string) => {
    setPath((prev) => {
      const next = [...prev];
      next[depth] = code;
      for (let i = depth + 1; i < 5; i++) next[i] = null;
      return next;
    });
  }, []);

  async function runSemanticSearch() {
    const q = semanticQuery.trim();
    if (!q) return;
    setSemanticLoading(true);
    setSemanticResults([]);
    try {
      const resp = await semanticSearch(q, 8);
      setSemanticResults((resp.noga_codes ?? []).slice(0, 5));
    } catch {
      // silent — user can retry
    } finally {
      setSemanticLoading(false);
    }
  }

  function browse(code: string) {
    router.push(`/${locale}/app/explorer?noga_code=${encodeURIComponent(code)}`);
  }

  if (isLoading) {
    return (
      <div style={{
        height: "calc(100vh - 5rem)", background: T.paper,
        display: "flex", alignItems: "center", justifyContent: "center",
        fontFamily: MONO, fontSize: 12, color: T.ink3,
      }}>
        {t.loadingHierarchy}
      </div>
    );
  }

  const showGlobalDropdown   = globalResults.length > 0;
  const showSemanticDropdown = semanticResults.length > 0;

  return (
    <div style={{
      height: "calc(100vh - 5rem)", background: T.paper,
      display: "flex", flexDirection: "column",
      fontFamily: SANS, fontSize: 13, color: T.ink,
      lineHeight: 1.4, letterSpacing: "-0.005em",
      WebkitFontSmoothing: "antialiased",
    }}>

      {/* ── HEADER ─────────────────────────────────────────────────────────── */}
      <div style={{ padding: "18px 28px 14px", borderBottom: `1px solid ${T.rule}`, flexShrink: 0 }}>
        <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: 24 }}>
          <div>
            <div style={{
              fontFamily: MONO, fontSize: 10, color: T.ink3,
              letterSpacing: "0.12em", textTransform: "uppercase", marginBottom: 6,
            }}>
              {t.classificationHeader}
            </div>
            <h1 style={{
              fontFamily: SERIF, fontStyle: "italic", fontWeight: 400,
              fontSize: 30, lineHeight: 1, letterSpacing: "-0.02em",
              margin: 0, color: T.ink,
            }}>
              {t.heroTitle}{" "}
              <span style={{ color: T.ink3 }}>{t.heroTitleAccent}</span>
            </h1>
          </div>
          <div style={{ fontFamily: MONO, fontSize: 10.5, color: T.ink3, textAlign: "right", flexShrink: 0 }}>
            {total.toLocaleString()} {t.classified}<br />
            <span style={{ color: T.ink4 }}>{t.levelsSections.replace("{count}", String(sections.length))}</span>
          </div>
        </div>
        <SectionStrip
          sections={sections}
          selected={path[0]}
          locale={locale}
          onSelect={(c) => selectAt(0, c)}
        />
      </div>

      {/* ── SEARCH ROW ─────────────────────────────────────────────────────── */}
      <div style={{
        padding: "10px 28px",
        borderBottom: `1px solid ${T.rule}`,
        display: "grid", gridTemplateColumns: "1.1fr 1.4fr auto",
        gap: 14, alignItems: "center", flexShrink: 0, zIndex: 20, position: "relative",
      }}>

        {/* Cell 1 — Code / Name with global dropdown */}
        <div style={{ position: "relative" }}>
          <div style={{
            border: `1px solid ${T.rule}`, background: T.paper,
            padding: "7px 12px", display: "flex", alignItems: "center", gap: 10,
          }}>
            <span style={{ fontFamily: MONO, fontSize: 10, color: T.ink3, letterSpacing: "0.1em", flexShrink: 0 }}>
              {t.codeName}
            </span>
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t.codeNamePlaceholder}
              style={{
                flex: 1, border: "none", outline: "none",
                background: "transparent", fontFamily: SANS, fontSize: 13, color: T.ink,
              }}
            />
            {search && (
              <button onClick={() => setSearch("")} style={{
                border: "none", background: "none", cursor: "pointer",
                fontFamily: MONO, fontSize: 12, color: T.ink4, padding: 0,
              }}>×</button>
            )}
          </div>
          {showGlobalDropdown && (
            <div style={{
              position: "absolute", top: "100%", left: 0, right: 0,
              background: T.paper, border: `1px solid ${T.rule}`, borderTop: "none",
              maxHeight: 288, overflowY: "auto", zIndex: 50,
              boxShadow: "0 4px 12px rgba(0,0,0,.08)",
            }}>
              {globalResults.map(({ node, crumb }) => (
                <div
                  key={node.code}
                  onClick={() => navigateToCode(node.code)}
                  style={{
                    padding: "8px 12px", cursor: "pointer",
                    borderBottom: `1px solid ${T.rule2}`,
                    background: T.paper,
                  }}
                  onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.background = T.accentBg; }}
                  onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.background = T.paper; }}
                >
                  <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
                    <span style={{ fontFamily: MONO, fontSize: 10, color: T.ink3 }}>{node.code}</span>
                    <span style={{ fontSize: 12, color: T.ink }}>{lbl(node, locale)}</span>
                  </div>
                  {crumb && (
                    <div style={{ fontFamily: MONO, fontSize: 9.5, color: T.ink4, marginTop: 2 }}>{crumb}</div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Cell 2 — Semantic search */}
        <div style={{ position: "relative" }}>
          <div style={{
            border: `1.5px solid ${T.accent}`, background: T.paper,
            padding: "7px 12px", display: "flex", alignItems: "center", gap: 10,
          }}>
            <span style={{ fontFamily: MONO, fontSize: 10, color: T.accent, letterSpacing: "0.1em", flexShrink: 0 }}>
              {t.askSemantic}
            </span>
            <input
              value={semanticQuery}
              onChange={(e) => setSemanticQuery(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") runSemanticSearch(); }}
              placeholder={t.semanticPlaceholder}
              style={{
                flex: 1, border: "none", outline: "none",
                background: "transparent", fontFamily: SANS, fontSize: 13, color: T.ink,
              }}
            />
            {semanticLoading ? (
              <span style={{ fontFamily: MONO, fontSize: 10, color: T.ink4 }}>…</span>
            ) : (
              <span
                onClick={runSemanticSearch}
                style={{ fontFamily: MONO, fontSize: 10, color: T.accent, cursor: "pointer" }}
              >↵</span>
            )}
          </div>
          {showSemanticDropdown && (
            <div style={{
              position: "absolute", top: "100%", left: 0, right: 0,
              background: T.paper, border: `1px solid ${T.rule}`, borderTop: "none",
              zIndex: 50, boxShadow: "0 4px 12px rgba(0,0,0,.08)",
            }}>
              {semanticResults.map((r) => {
                const node = findNode(hierarchy, r.value);
                const simPct = Math.round(r.similarity * 100);
                return (
                  <div
                    key={r.value}
                    onClick={() => navigateToCode(r.value)}
                    style={{
                      padding: "8px 12px", cursor: "pointer",
                      borderBottom: `1px solid ${T.rule2}`,
                      background: T.paper,
                    }}
                    onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.background = T.accentBg; }}
                    onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.background = T.paper; }}
                  >
                    <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
                      <span style={{ fontFamily: MONO, fontSize: 10, color: T.ink3 }}>{r.value}</span>
                      <span style={{ fontSize: 12, color: T.ink }}>
                        {node ? lbl(node, locale) : r.value}
                      </span>
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 4 }}>
                      <div style={{ flex: 1, height: 2, background: T.rule }}>
                        <div style={{ width: simPct + "%", height: "100%", background: T.accent }} />
                      </div>
                      <span style={{ fontFamily: MONO, fontSize: 9.5, color: T.ink3, flexShrink: 0 }}>{simPct}%</span>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Cell 3 — Reset */}
        <button
          onClick={() => {
            setPath([null, null, null, null, null]);
            setSearch("");
            setSemanticQuery("");
            setSemanticResults([]);
          }}
          style={{
            padding: "6px 12px",
            border: `1px solid ${T.rule}`, background: "transparent",
            fontFamily: MONO, fontSize: 10, color: T.ink4, cursor: "pointer",
            letterSpacing: "0.05em",
          }}
        >
          {t.reset}
        </button>
      </div>

      {/* ── BODY: miller columns + detail pane ─────────────────────────────── */}
      <div style={{
        display: "grid", gridTemplateColumns: "1fr 460px",
        flex: 1, minHeight: 0,
      }}>
        <div style={{
          display: "grid", gridTemplateColumns: "repeat(5, 1fr)",
          minHeight: 0, borderRight: `1px solid ${T.rule}`,
        }}>
          {columns.map((items, depth) => (
            <MillerColumn
              key={depth}
              depth={depth}
              items={items}
              selected={path[depth]}
              locale={locale}
              searchQuery={showGlobalDropdown ? "" : search}
              onSelect={(c) => selectAt(depth, c)}
            />
          ))}
        </div>

        <DetailPane pathNodes={pathNodes} locale={locale} onBrowse={browse} />
      </div>
    </div>
  );
}
