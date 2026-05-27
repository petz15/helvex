"use client";
import { useState, useMemo, useCallback } from "react";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import { fetchNogaHierarchy } from "@/lib/api";
import type { NogaNode } from "@/lib/api";

// ── Design tokens (editorial-meets-terminal palette from the spec) ────────────
const T = {
  paper:  "#f3f0e9",
  paper2: "#ebe7dd",
  paper3: "#e2ddd0",
  ink:    "#161514",
  ink2:   "#3a3835",
  ink3:   "#6d6a64",
  ink4:   "#9b978f",
  rule:   "#c9c4b6",
  rule2:  "#dad5c7",
  accent: "#b8362c",
  green:  "#2f5e3a",
} as const;

const MONO  = "'JetBrains Mono','IBM Plex Mono',ui-monospace,monospace";
const SERIF = "'Instrument Serif',Georgia,'Times New Roman',serif";
const SANS  = "ui-sans-serif,system-ui,-apple-system,sans-serif";

const LEVEL_META = [
  { de: "Abschnitt", en: "section",  hint: "1 letter" },
  { de: "Abteilung", en: "division", hint: "2 digits" },
  { de: "Gruppe",    en: "group",    hint: "3 digits" },
  { de: "Klasse",    en: "class",    hint: "4 digits" },
  { de: "Art",       en: "type",     hint: "5 digits · CH" },
] as const;

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
  sections,
  selected,
  locale,
  onSelect,
}: {
  sections: NogaNode[];
  selected: string | null;
  locale: string;
  onSelect: (c: string) => void;
}) {
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
                borderRight: i < sections.length - 1 ? `1px solid ${on ? T.ink : T.paper}` : "none",
                background: on ? T.ink : i % 2 ? T.paper3 : T.paper2,
                color: on ? T.paper : T.ink2,
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
                  background: T.accent,
                }} />
              )}
            </div>
          );
        })}
      </div>
      <div style={{
        display: "flex", alignItems: "center", padding: "0 10px",
        fontFamily: MONO, fontSize: 9.5, color: T.ink3, whiteSpace: "nowrap",
        background: T.paper, border: `1px solid ${T.rule}`, borderLeft: "none",
      }}>
        {total.toLocaleString()} · {sections.length} sections
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
        background: on ? T.ink : "transparent",
        cursor: "pointer", userSelect: "none",
      }}
    >
      {/* Density spine */}
      <span style={{
        position: "absolute", left: 0, top: 0, bottom: 0, width: 2,
        background: on ? T.accent : T.rule, opacity: spineOpacity,
      }} />

      {/* Code · label · chevron */}
      <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
        <span style={{
          fontFamily: MONO, fontSize: 10.5,
          color: on ? T.paper3 : T.ink3,
          minWidth: node.code.length > 4 ? 58 : 36, flexShrink: 0,
        }}>
          {node.code}
        </span>
        <span style={{
          flex: 1, fontSize: 12, lineHeight: 1.25,
          color: on ? T.paper : T.ink,
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
            color: on ? T.paper3 : T.ink4, marginLeft: 2, flexShrink: 0,
          }}>›</span>
        )}
      </div>

      {/* Count + density bar */}
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 5 }}>
        <span style={{
          fontFamily: MONO, fontSize: 9.5,
          color: on ? T.paper3 : T.ink3, minWidth: 44, flexShrink: 0,
        }}>
          {node.count.toLocaleString()}
        </span>
        <div style={{ flex: 1, height: 3, background: on ? "rgba(243,240,233,.18)" : T.rule2 }}>
          <div style={{
            height: "100%", width: bar + "%",
            background: on ? T.paper : T.ink3,
          }} />
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
  const meta  = LEVEL_META[depth];
  const q     = searchQuery.toLowerCase();
  const shown = q ? items.filter((n) => matches(n, q, locale)) : items;
  const max   = Math.max(...items.map((n) => n.count), 1);
  const isLeafLevel = depth === 4;

  return (
    <div style={{
      borderRight: `1px solid ${T.rule2}`,
      display: "flex", flexDirection: "column", minHeight: 0,
      background: T.paper,
    }}>
      {/* Header */}
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
          <div style={{ fontFamily: MONO, fontSize: 9, color: T.ink4 }}>{meta.hint}</div>
        </div>
        <span style={{ fontFamily: MONO, fontSize: 9.5, color: T.ink3 }}>{shown.length}</span>
      </div>

      {/* Body */}
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
            leaf level · companies →
          </div>
        )}
        {shown.length === 0 && (
          <div style={{
            padding: "16px 12px", color: T.ink4,
            fontFamily: MONO, fontSize: 10, textAlign: "center",
          }}>
            {items.length === 0 ? "select parent →" : "no matches"}
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
  const deepest = [...pathNodes].reverse().find(Boolean) ?? null;

  if (!deepest) {
    return (
      <div style={{
        borderLeft: `1px solid ${T.rule}`, background: T.paper2,
        display: "flex", flexDirection: "column",
        alignItems: "center", justifyContent: "center",
        padding: 32, color: T.ink4, fontFamily: MONO, fontSize: 10, textAlign: "center",
      }}>
        <div style={{ color: T.ink3, marginBottom: 8, letterSpacing: "0.1em" }}>
          SELECT A NODE
        </div>
        click any section to explore the hierarchy
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
                background: ci === arr.length - 1 ? T.ink : "transparent",
                color: ci === arr.length - 1 ? T.paper : T.ink2,
                border: `1px solid ${ci === arr.length - 1 ? T.ink : T.rule}`,
                fontFamily: MONO, fontSize: 10,
              }}>
                <span style={{ color: ci === arr.length - 1 ? T.paper3 : T.ink4 }}>{c.code}</span>
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
          { k: "COMPANIES", v: deepest.count.toLocaleString(), sub: `in ${deepest.code}`, accent: false },
          { k: "FRESH · 30d", v: "—",  sub: "not yet wired", accent: true },
          { k: "AVG SCORE",  v: "—",  sub: "combined", accent: false },
        ].map((s, si) => (
          <div key={s.k} style={{
            flex: 1, padding: "10px 12px",
            borderRight: si < 2 ? `1px solid ${T.rule2}` : "none",
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
            border: `1px solid ${T.ink}`, background: T.ink, color: T.paper,
            fontFamily: MONO, fontSize: 11, cursor: "pointer",
          }}
        >
          open all {deepest.count.toLocaleString()} in results →
        </button>
      </div>

      {/* Placeholder sections for data not yet wired */}
      {[
        { title: "classification confidence · noga_confidence", note: "confidence bucketing requires a node-detail API endpoint" },
        { title: "cantons · top 10  +  top companies · in branch", note: "canton distribution requires a node-detail API endpoint" },
        { title: "semantic neighbours · noga_embeddings", note: "requires /api/noga/neighbours/<code> — pgvector cosine similarity" },
        { title: "misclassified candidates · review", note: "requires /api/noga/misclassified/<code> — company-to-NOGA embedding distance" },
      ].map(({ title, note }) => (
        <div key={title} style={{ padding: "12px 18px", borderBottom: `1px solid ${T.rule}` }}>
          <div style={{
            fontFamily: MONO, fontSize: 9.5, color: T.ink3,
            letterSpacing: "0.1em", textTransform: "uppercase",
          }}>
            {title}
          </div>
          <div style={{ fontFamily: MONO, fontSize: 10, color: T.ink4, marginTop: 5 }}>
            — {note}
          </div>
        </div>
      ))}
    </div>
  );
}

// ── Main ──────────────────────────────────────────────────────────────────────

export default function NogaClient({ locale }: { locale: string }) {
  const router  = useRouter();
  const [path,  setPath] = useState<(string | null)[]>([null, null, null, null, null]);
  const [search, setSearch] = useState("");

  const { data: hierarchy = [], isLoading } = useSWR(
    "noga-hierarchy", fetchNogaHierarchy, { dedupingInterval: 3_600_000 }
  );

  const sections = useMemo(() => onlyAlpha(hierarchy), [hierarchy]);
  const total    = useMemo(() => sections.reduce((s, n) => s + n.count, 0), [sections]);

  // Items visible in each column
  const columns = useMemo(() =>
    Array.from({ length: 5 }, (_, d): NogaNode[] => {
      if (d === 0) return sections;
      const parentCode = path[d - 1];
      if (!parentCode) return [];
      return findNode(hierarchy, parentCode)?.children ?? [];
    }),
  [sections, hierarchy, path]);

  // NogaNode objects for the current path
  const pathNodes = useMemo(
    () => path.map((code) => (code ? findNode(hierarchy, code) : null)),
    [hierarchy, path]
  );

  const deepest = useMemo(
    () => [...pathNodes].reverse().find(Boolean) ?? null,
    [pathNodes]
  );

  const selectAt = useCallback((depth: number, code: string) => {
    setPath((prev) => {
      const next = [...prev];
      next[depth] = code;
      for (let i = depth + 1; i < 5; i++) next[i] = null;
      return next;
    });
  }, []);

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
        loading hierarchy…
      </div>
    );
  }

  return (
    <div style={{
      height: "calc(100vh - 5rem)", background: T.paper,
      display: "flex", flexDirection: "column",
      fontFamily: SANS, fontSize: 13, color: T.ink,
      lineHeight: 1.4, letterSpacing: "-0.005em",
      WebkitFontSmoothing: "antialiased",
    }}>

      {/* ── HEADER ─────────────────────────────────────────────────────── */}
      <div style={{ padding: "18px 28px 14px", borderBottom: `1px solid ${T.rule}`, flexShrink: 0 }}>
        <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: 24 }}>
          <div>
            <div style={{
              fontFamily: MONO, fontSize: 10, color: T.ink3,
              letterSpacing: "0.12em", textTransform: "uppercase", marginBottom: 6,
            }}>
              classification · NOGA 2025 · CH
            </div>
            <h1 style={{
              fontFamily: SERIF, fontStyle: "italic", fontWeight: 400,
              fontSize: 30, lineHeight: 1, letterSpacing: "-0.02em",
              margin: 0, color: T.ink,
            }}>
              The Swiss federal taxonomy,{" "}
              <span style={{ color: T.ink3 }}>walked one level at a time.</span>
            </h1>
          </div>
          <div style={{
            fontFamily: MONO, fontSize: 10.5, color: T.ink3,
            textAlign: "right", flexShrink: 0,
          }}>
            {total.toLocaleString()} classified<br />
            <span style={{ color: T.ink4 }}>5 levels · {sections.length} sections</span>
          </div>
        </div>
        <SectionStrip
          sections={sections}
          selected={path[0]}
          locale={locale}
          onSelect={(c) => selectAt(0, c)}
        />
      </div>

      {/* ── SEARCH ROW ─────────────────────────────────────────────────── */}
      <div style={{
        padding: "10px 28px",
        borderBottom: `1px solid ${T.rule}`,
        display: "grid", gridTemplateColumns: "1.1fr 1.4fr auto",
        gap: 14, alignItems: "center", flexShrink: 0,
      }}>
        {/* Code / name filter */}
        <div style={{
          border: `1px solid ${T.rule}`, background: T.paper,
          padding: "7px 12px", display: "flex", alignItems: "center", gap: 10,
        }}>
          <span style={{ fontFamily: MONO, fontSize: 10, color: T.ink3, letterSpacing: "0.1em", flexShrink: 0 }}>
            CODE / NAME
          </span>
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="43.22 · Wärmepumpe · heat pump…"
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

        {/* Semantic — placeholder */}
        <div style={{
          border: `1.5px solid ${T.ink}`, background: T.paper,
          padding: "7px 12px", display: "flex", alignItems: "center", gap: 10,
        }}>
          <span style={{ fontFamily: MONO, fontSize: 10, color: T.accent, letterSpacing: "0.1em", flexShrink: 0 }}>
            ASK · SEMANTIC
          </span>
          <span style={{ flex: 1, fontSize: 13, color: T.ink3 }}>
            describe an industry to light up the branch
          </span>
          <span style={{ fontFamily: MONO, fontSize: 10, color: T.ink4 }}>⌘K</span>
        </div>

        {/* Reset */}
        <button
          onClick={() => { setPath([null, null, null, null, null]); setSearch(""); }}
          style={{
            padding: "6px 12px",
            border: `1px solid ${T.rule}`, background: "transparent",
            fontFamily: MONO, fontSize: 10, color: T.ink4, cursor: "pointer",
            letterSpacing: "0.05em",
          }}
        >
          reset
        </button>
      </div>

      {/* ── BODY: miller columns + detail pane ─────────────────────────── */}
      <div style={{
        display: "grid", gridTemplateColumns: "1fr 460px",
        flex: 1, minHeight: 0,
      }}>
        {/* 5 Miller columns */}
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
              searchQuery={search}
              onSelect={(c) => selectAt(depth, c)}
            />
          ))}
        </div>

        <DetailPane pathNodes={pathNodes} locale={locale} onBrowse={browse} />
      </div>

      {/* ── FOOTER MICROBAR ─────────────────────────────────────────────── */}
      <div style={{
        padding: "7px 28px",
        borderTop: `1px solid ${T.rule}`, background: T.paper2,
        display: "flex", justifyContent: "space-between", alignItems: "baseline",
        fontFamily: MONO, fontSize: 10, color: T.ink3, flexShrink: 0,
      }}>
        <span>
          {deepest
            ? `${path.filter(Boolean).join(" › ")}  ·  ${deepest.count.toLocaleString()} companies in branch`
            : "select a section to begin"}
        </span>
        <span style={{ display: "flex", gap: 16, alignItems: "baseline" }}>
          {deepest && (
            <button
              onClick={() => browse(deepest.code)}
              style={{
                border: "none", background: "none", cursor: "pointer",
                fontFamily: MONO, fontSize: 10, color: T.ink,
                borderBottom: `1px solid ${T.rule}`, padding: 0,
              }}
            >
              open all in results →
            </button>
          )}
          <span style={{ color: T.ink4 }}>multi-select ⇧</span>
        </span>
      </div>
    </div>
  );
}
