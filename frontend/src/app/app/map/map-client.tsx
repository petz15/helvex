"use client";
import { useEffect, useRef, useState } from "react";
import type { Map, LayerGroup } from "leaflet";
import { fetchMapData } from "@/lib/api";
import type { MapFeature } from "@/lib/types";
import "leaflet/dist/leaflet.css";

function scoreColor(score: number | null): string {
  if (score == null) return "#94a3b8";
  if (score >= 70) return "#22c55e";
  if (score >= 40) return "#f59e0b";
  return "#ef4444";
}

interface Filters {
  canton: string;
  review_status: string;
  min_combined_score: string;
  hide_cancelled: boolean;
}

export function MapClient() {
  const mapRef = useRef<HTMLDivElement>(null);
  const mapInstanceRef = useRef<Map | null>(null);
  const layerRef = useRef<LayerGroup | null>(null);
  const [loading, setLoading] = useState(false);
  const [count, setCount] = useState(0);
  const [truncated, setTruncated] = useState(false);
  const [filters, setFilters] = useState<Filters>({
    canton: "", review_status: "", min_combined_score: "", hide_cancelled: false,
  });

  async function loadData(f: Filters) {
    if (!mapInstanceRef.current) return;
    setLoading(true);
    try {
      const params: Record<string, string> = {};
      if (f.canton) params.canton = f.canton;
      if (f.review_status) params.review_status = f.review_status;
      if (f.min_combined_score) params.min_combined_score = f.min_combined_score;
      if (f.hide_cancelled) params.hide_cancelled = "true";
      const data = await fetchMapData(params);
      setCount(data.count);
      setTruncated(data.truncated);
      renderMarkers(data.features);
    } finally {
      setLoading(false);
    }
  }

  function renderMarkers(features: MapFeature[]) {
    const L = (window as typeof window & { _L?: typeof import("leaflet") })._L;
    if (!L || !mapInstanceRef.current) return;
    layerRef.current?.clearLayers();
    const layer = L.layerGroup();
    for (const f of features) {
      const color = scoreColor(f.claude_score ?? f.google_score ?? f.zefix_score ?? null);
      const marker = L.circleMarker([f.lat, f.lon], {
        radius: 6, fillColor: color, color: "#fff", weight: 1.5, fillOpacity: 0.85,
      });
      const parts = [
        `<strong>${f.name}</strong>`,
        f.canton ? `<span style="color:#64748b">${f.municipality ?? ""}, ${f.canton}</span>` : "",
        f.website ? `<a href="${f.website}" target="_blank" rel="noopener" style="color:#3b82f6">${f.website.replace(/^https?:\/\//, "")}</a>` : "",
        `Google: ${f.google_score ?? "—"} · Zefix: ${f.zefix_score ?? "—"} · Claude: ${f.claude_score ?? "—"}`,
        f.review ? `<em>${f.review.replace(/_/g, " ")}</em>` : "",
      ].filter(Boolean);
      marker.bindPopup(parts.join("<br>"), { maxWidth: 300 });
      layer.addLayer(marker);
    }
    layer.addTo(mapInstanceRef.current!);
    layerRef.current = layer;
  }

  useEffect(() => {
    if (!mapRef.current || mapInstanceRef.current) return;
    let mounted = true;
    (async () => {
      const L = await import("leaflet");
      if (!mounted) return;

      // Store on window so renderMarkers can access it synchronously
      (window as typeof window & { _L?: typeof L })._L = L;

      const map = L.map(mapRef.current!).setView([46.8, 8.2], 8);
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
        maxZoom: 19,
      }).addTo(map);
      mapInstanceRef.current = map;

      await loadData(filters);
    })();
    return () => { mounted = false; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function handleFilter(e: React.FormEvent) {
    e.preventDefault();
    loadData(filters);
  }

  return (
    <div className="flex flex-col h-[calc(100vh-3rem)]">
      <form onSubmit={handleFilter} className="flex items-center gap-3 px-4 py-2.5 border-b border-slate-200 bg-white flex-wrap">
        <input
          placeholder="Canton (e.g. BE)"
          value={filters.canton}
          onChange={e => setFilters(f => ({ ...f, canton: e.target.value.toUpperCase() }))}
          className="px-2.5 py-1.5 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-300 w-24"
        />
        <select
          value={filters.review_status}
          onChange={e => setFilters(f => ({ ...f, review_status: e.target.value }))}
          className="px-2.5 py-1.5 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-300"
        >
          <option value="">All statuses</option>
          <option value="potential_proposal">Potential proposal</option>
          <option value="confirmed_proposal">Confirmed proposal</option>
          <option value="potential_generic">Potential generic</option>
          <option value="confirmed_generic">Confirmed generic</option>
          <option value="interesting">Interesting</option>
          <option value="rejected">Rejected</option>
        </select>
        <input
          type="number"
          placeholder="Min combined score"
          value={filters.min_combined_score}
          onChange={e => setFilters(f => ({ ...f, min_combined_score: e.target.value }))}
          className="px-2.5 py-1.5 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-300 w-40"
        />
        <label className="flex items-center gap-1.5 text-sm text-slate-600 cursor-pointer">
          <input
            type="checkbox"
            checked={filters.hide_cancelled}
            onChange={e => setFilters(f => ({ ...f, hide_cancelled: e.target.checked }))}
            className="rounded border-slate-300 text-blue-600"
          />
          Hide cancelled
        </label>
        <button type="submit" className="bg-blue-600 hover:bg-blue-700 text-white px-3 py-1.5 rounded-lg text-sm font-medium transition-colors">
          {loading ? "Loading…" : "Apply"}
        </button>
        <span className="text-xs text-slate-400 ml-auto">
          {count.toLocaleString()} companies{truncated ? " (truncated to 20k)" : ""}
        </span>
      </form>
      <div ref={mapRef} className="flex-1" />
    </div>
  );
}
