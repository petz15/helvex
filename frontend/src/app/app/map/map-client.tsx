"use client";
import { useEffect, useRef, useState } from "react";
import type { Map, LayerGroup } from "leaflet";
import { fetchMapData, fetchMapClusters } from "@/lib/api";
import type { MapFeature, MapCluster } from "@/lib/types";
import "leaflet/dist/leaflet.css";

// Below this zoom level show grid-aggregated cluster circles; at/above show individual points.
const DETAIL_ZOOM = 12;

type WindowWithLeaflet = typeof window & { _L?: typeof import("leaflet") };

function scoreColor(score: number | null): string {
  if (score == null) return "#94a3b8";
  if (score >= 70) return "#22c55e";
  if (score >= 40) return "#f59e0b";
  if (score >= 10) return "#f97316";
  return "#94a3b8"; // unscored → neutral grey instead of alarming red
}

/** Cluster bubble color — count-based blue scale for a natural, non-alarming look. */
function clusterColor(count: number): string {
  if (count >= 500) return "#1e40af";
  if (count >= 100) return "#1d4ed8";
  if (count >= 20)  return "#3b82f6";
  return "#60a5fa";
}

function buildPopup(f: MapFeature): string {
  const parts = [
    `<strong><a href="/app/companies/${f.id}" style="color:#3b82f6;text-decoration:none">${f.name}</a></strong>`,
    f.canton ? `<span style="color:#64748b">${f.municipality ?? ""}, ${f.canton}</span>` : "",
    f.website ? `<a href="${f.website}" target="_blank" rel="noopener" style="color:#3b82f6">${f.website.replace(/^https?:\/\//, "")}</a>` : "",
    `Google: ${f.google_score ?? "—"} · Zefix: ${f.zefix_score ?? "—"} · Claude: ${f.claude_score ?? "—"}`,
    f.review ? `<em>${f.review.replace(/_/g, " ")}</em>` : "",
    `<a href="/app/companies/${f.id}" style="color:#3b82f6;font-size:11px">View profile →</a>`,
  ].filter(Boolean);
  return parts.join("<br>");
}

interface Filters {
  canton: string;
  review_status: string;
  min_combined_score: string;
  hide_cancelled: boolean;
}

const DEFAULT_FILTERS: Filters = { canton: "", review_status: "", min_combined_score: "", hide_cancelled: false };

export function MapClient() {
  const mapRef = useRef<HTMLDivElement>(null);
  const mapInstanceRef = useRef<Map | null>(null);
  const layerRef = useRef<LayerGroup | null>(null);
  // Ref mirrors state so Leaflet event handlers always read the latest filters
  const filtersRef = useRef<Filters>(DEFAULT_FILTERS);
  // Ref to the latest loadViewport so Leaflet event handlers call the current closure
  const loadViewportRef = useRef<((f: Filters) => Promise<void>) | null>(null);

  const [loading, setLoading] = useState(false);
  const [count, setCount] = useState(0);
  const [truncated, setTruncated] = useState(false);
  const [clustered, setClustered] = useState(true);
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);

  // Keep filtersRef in sync
  useEffect(() => { filtersRef.current = filters; }, [filters]);

  function renderClusters(cells: MapCluster[]) {
    const L = (window as WindowWithLeaflet)._L;
    if (!L || !mapInstanceRef.current) return;
    layerRef.current?.clearLayers();
    const layer = L.layerGroup();
    for (const cell of cells) {
      if (cell.count === 0) continue;
      const color = clusterColor(cell.count);
      const size = Math.max(32, Math.min(72, 22 + Math.sqrt(cell.count) * 1.8));
      const label = cell.count >= 1000 ? `${Math.round(cell.count / 1000)}k` : String(cell.count);
      const fs = size > 46 ? 12 : 10;
      const icon = L.divIcon({
        html: `<div style="width:${size}px;height:${size}px;border-radius:50%;background:${color};border:2.5px solid rgba(255,255,255,0.85);display:flex;align-items:center;justify-content:center;font-size:${fs}px;font-weight:700;color:#fff;box-shadow:0 2px 8px rgba(0,0,0,0.25);cursor:pointer;opacity:0.92">${label}</div>`,
        className: "",
        iconSize: [size, size] as [number, number],
        iconAnchor: [size / 2, size / 2] as [number, number],
      });
      layer.addLayer(L.marker([cell.lat, cell.lon], { icon }));
    }
    layer.addTo(mapInstanceRef.current);
    layerRef.current = layer;
  }

  function renderMarkers(features: MapFeature[]) {
    const L = (window as WindowWithLeaflet)._L;
    if (!L || !mapInstanceRef.current) return;
    layerRef.current?.clearLayers();
    const layer = L.layerGroup();

    // Group co-located companies (same lat/lon to 5 decimal places ≈ 1m precision)
    const byLocation = new Map<string, MapFeature[]>();
    for (const f of features) {
      const key = `${f.lat.toFixed(5)},${f.lon.toFixed(5)}`;
      const arr = byLocation.get(key);
      if (arr) arr.push(f); else byLocation.set(key, [f]);
    }

    for (const group of byLocation.values()) {
      const f = group[0];
      if (group.length === 1) {
        const color = scoreColor(f.claude_score ?? f.google_score ?? f.zefix_score ?? null);
        const marker = L.circleMarker([f.lat, f.lon], {
          radius: 6, fillColor: color, color: "#fff", weight: 1.5, fillOpacity: 0.85,
        });
        marker.bindPopup(buildPopup(f), { maxWidth: 300 });
        layer.addLayer(marker);
      } else {
        // Diamond icon with count for co-located companies
        const s = 28;
        const icon = L.divIcon({
          html: `<div style="width:${s}px;height:${s}px;background:#3b82f6;border:2px solid #fff;transform:rotate(45deg);display:flex;align-items:center;justify-content:center;box-shadow:0 2px 6px rgba(0,0,0,0.3)"><span style="transform:rotate(-45deg);font-size:10px;font-weight:700;color:#fff;line-height:1">${group.length}</span></div>`,
          className: "",
          iconSize: [s, s] as [number, number],
          iconAnchor: [s / 2, s / 2] as [number, number],
        });
        const marker = L.marker([f.lat, f.lon], { icon });
        const popupHtml = group.map(buildPopup).join('<hr style="margin:5px 0;border-color:#e2e8f0">');
        marker.bindPopup(popupHtml, { maxWidth: 320 });
        layer.addLayer(marker);
      }
    }

    layer.addTo(mapInstanceRef.current);
    layerRef.current = layer;
  }

  async function loadViewport(f: Filters) {
    const map = mapInstanceRef.current;
    if (!map) return;
    const zoom = Math.round(map.getZoom());
    const bounds = map.getBounds();

    const params: Record<string, string> = {};
    if (f.canton) params.canton = f.canton;
    if (f.review_status) params.review_status = f.review_status;
    if (f.min_combined_score) params.min_combined_score = f.min_combined_score;
    if (f.hide_cancelled) params.hide_cancelled = "true";
    params.min_lat = String(bounds.getSouth());
    params.max_lat = String(bounds.getNorth());
    params.min_lon = String(bounds.getWest());
    params.max_lon = String(bounds.getEast());

    setLoading(true);
    try {
      if (zoom < DETAIL_ZOOM) {
        params.zoom = String(zoom);
        const data = await fetchMapClusters(params);
        setCount(data.total);
        setTruncated(false);
        setClustered(true);
        renderClusters(data.cells);
      } else {
        const data = await fetchMapData(params);
        setCount(data.count);
        setTruncated(data.truncated);
        setClustered(false);
        renderMarkers(data.features);
      }
    } finally {
      setLoading(false);
    }
  }

  // Always keep the ref pointing at the latest closure
  loadViewportRef.current = loadViewport;

  useEffect(() => {
    if (!mapRef.current || mapInstanceRef.current) return;
    let mounted = true;
    (async () => {
      const L = await import("leaflet");
      if (!mounted) return;

      (window as WindowWithLeaflet)._L = L;

      const map = L.map(mapRef.current!).setView([46.8, 8.2], 8);
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
        maxZoom: 19,
      }).addTo(map);
      mapInstanceRef.current = map;

      // Reload whenever the viewport changes
      map.on("moveend zoomend", () => {
        loadViewportRef.current?.(filtersRef.current);
      });

      await loadViewportRef.current?.(filtersRef.current);
    })();
    return () => { mounted = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function handleFilter(e: React.FormEvent) {
    e.preventDefault();
    loadViewport(filters);
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
          {count.toLocaleString()} companies
          {clustered
            ? " — zoom in for individual points"
            : truncated
            ? ` (capped at 5 000 — zoom in further)`
            : " in view"}
        </span>
      </form>
      <div ref={mapRef} className="flex-1" />
    </div>
  );
}
