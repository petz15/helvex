"use client";
import { useEffect, useRef, useState } from "react";
import type { Map, LayerGroup } from "leaflet";
import useSWR from "swr";
import { fetchCantons, fetchMapData, fetchMapClusters, fetchTaxonomy, geocodeMapAddress } from "@/lib/api";
import type { CompanyFilters, MapFeature, MapCluster } from "@/lib/types";
import { FilterBar } from "@/components/dashboard/filter-bar";
import { MapTurnstileGate } from "@/components/map-turnstile-gate";
import { Loader2, MapPin, Search } from "lucide-react";
import { useI18n } from "@/i18n/context";
import "leaflet/dist/leaflet.css";

// Below this zoom level show grid-aggregated cluster circles; at/above show individual points.
const DETAIL_ZOOM = 15;

const DEFAULT_FILTERS: CompanyFilters = { sort: "-combined_score", page: 1, page_size: 50, status: "ACTIVE" };

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
  if (count >= 2000) return "#072179";
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
    `Web: ${f.web_score ?? "—"} · Flex: ${f.flex_score ?? "—"} · AI: ${f.ai_score ?? "—"}`,
    f.review ? `<em>${f.review.replace(/_/g, " ")}</em>` : "",
    `<a href="/app/companies/${f.id}" style="color:#3b82f6;font-size:11px">View profile →</a>`,
  ].filter(Boolean);
  return parts.join("<br>");
}

export function MapClient() {
  const { dict } = useI18n();
  const t = dict.app.map;
  const mapRef = useRef<HTMLDivElement>(null);
  const mapInstanceRef = useRef<Map | null>(null);
  const layerRef = useRef<LayerGroup | null>(null);
  // Ref mirrors state so Leaflet event handlers always read the latest filters
  const filtersRef = useRef<CompanyFilters>(DEFAULT_FILTERS);
  // Ref to the latest loadViewport so Leaflet event handlers call the current closure
  const loadViewportRef = useRef<((f: CompanyFilters) => Promise<void>) | null>(null);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [count, setCount] = useState(0);
  const [truncated, setTruncated] = useState(false);
  const [clustered, setClustered] = useState(true);
  const [filters, setFilters] = useState<CompanyFilters>(DEFAULT_FILTERS);
  const [addressQuery, setAddressQuery] = useState("");
  const [addressLabel, setAddressLabel] = useState<string | null>(null);
  const [addressError, setAddressError] = useState<string | null>(null);
  const [addressLoading, setAddressLoading] = useState(false);
  const pendingJumpRef = useRef<{ lat: number; lon: number } | null>(null);
  const { data: cantons = [] } = useSWR("map-cantons", fetchCantons);
  const { data: taxonomy = {} } = useSWR("map-taxonomy", fetchTaxonomy);

  // Keep filtersRef in sync
  useEffect(() => { filtersRef.current = filters; }, [filters]);

  function getOrCreateLayer() {
    const L = (window as WindowWithLeaflet)._L;
    if (!L || !mapInstanceRef.current) return null;
    if (!layerRef.current) {
      layerRef.current = L.layerGroup().addTo(mapInstanceRef.current);
    }
    layerRef.current.clearLayers();
    return layerRef.current;
  }

  function renderClusters(cells: MapCluster[]) {
    const L = (window as WindowWithLeaflet)._L;
    const layer = getOrCreateLayer();
    if (!L || !layer || !mapInstanceRef.current) return;
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
      const marker = L.marker([cell.lat, cell.lon], { icon });
      // Click to zoom in one step past DETAIL_ZOOM
      marker.on("click", () => {
        mapInstanceRef.current?.setView([cell.lat, cell.lon], DETAIL_ZOOM + 1);
      });
      layer.addLayer(marker);
    }
  }

  function renderMarkers(features: MapFeature[]) {
    const L = (window as WindowWithLeaflet)._L;
    const layer = getOrCreateLayer();
    if (!L || !layer || !mapInstanceRef.current) return;

    // Group co-located companies (same lat/lon to 5 decimal places ≈ 1m precision)
    const byLocation = new globalThis.Map<string, MapFeature[]>();
    for (const f of features) {
      const key = `${f.lat.toFixed(5)},${f.lon.toFixed(5)}`;
      const arr = byLocation.get(key);
      if (arr) arr.push(f); else byLocation.set(key, [f]);
    }

    for (const group of byLocation.values()) {
      const f = group[0];
      if (group.length === 1) {
        const color = scoreColor(f.ai_score ?? f.web_score ?? f.flex_score ?? null);
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
        const innerHtml = group.map(buildPopup).join('<hr style="margin:5px 0;border-color:#e2e8f0">');
        // Wrap in a scrollable container so a large group doesn't push the map viewport
        const popupHtml = `<div style="max-height:260px;overflow-y:auto;padding-right:4px">${innerHtml}</div>`;
        marker.bindPopup(popupHtml, { maxWidth: 320, autoPan: false });
        layer.addLayer(marker);
      }
    }

  }

  function buildMapParams(f: CompanyFilters): Record<string, string> {
    const params: Record<string, string> = {};
    for (const [key, value] of Object.entries(f)) {
      if (["page", "page_size", "sort"].includes(key)) continue;
      if (value === undefined || value === null || value === "") continue;
      // Map uses zefix_status; CompanyFilters uses status
      const paramKey = key === "status" ? "zefix_status" : key;
      params[paramKey] = String(value);
    }
    return params;
  }

  async function loadViewport(f: CompanyFilters) {
    const map = mapInstanceRef.current;
    if (!map) return;
    const zoom = Math.round(map.getZoom());
    const bounds = map.getBounds();

    const params = buildMapParams(f);
    params.min_lat = String(bounds.getSouth());
    params.max_lat = String(bounds.getNorth());
    params.min_lon = String(bounds.getWest());
    params.max_lon = String(bounds.getEast());

    setLoading(true);
    setError(null);
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
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load map data");
    } finally {
      setLoading(false);
    }
  }

  // Always keep the ref pointing at the latest closure
  loadViewportRef.current = loadViewport;

  useEffect(() => {
    if (!mapRef.current || mapInstanceRef.current) return;
    let mounted = true;
    let resizeObserver: ResizeObserver | null = null;
    (async () => {
      const L = await import("leaflet");
      if (!mounted) return;

      (window as WindowWithLeaflet)._L = L;

      const map = L.map(mapRef.current!).setView([46.8, 8.2], 8);
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
        maxZoom: 21,
      }).addTo(map);
      mapInstanceRef.current = map;
      resizeObserver = typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(() => {
            map.invalidateSize();
          });
      resizeObserver?.observe(mapRef.current!);

      // Reload whenever the viewport changes
      map.on("moveend zoomend", () => {
        loadViewportRef.current?.(filtersRef.current);
      });

      if (pendingJumpRef.current) {
        map.setView([pendingJumpRef.current.lat, pendingJumpRef.current.lon], 18);
        pendingJumpRef.current = null;
      }

      await loadViewportRef.current?.(filtersRef.current);
    })();
    return () => {
      mounted = false;
      resizeObserver?.disconnect();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleGoToAddress(e: React.FormEvent) {
    e.preventDefault();
    const query = addressQuery.trim();
    if (!query) return;

    setAddressLoading(true);
    setAddressError(null);
    try {
      const result = await geocodeMapAddress(query);
      setAddressLabel(result.address);
      const map = mapInstanceRef.current;
      if (map) {
        map.setView([result.lat, result.lon], 18);
      } else {
        pendingJumpRef.current = { lat: result.lat, lon: result.lon };
      }
    } catch (error) {
      setAddressError(error instanceof Error ? error.message : String(error));
    } finally {
      setAddressLoading(false);
    }
  }

  function handleFiltersChange(next: CompanyFilters) {
    setFilters(next);
    void loadViewport(next);
  }

  return (
    <>
      <MapTurnstileGate />
      <div className="flex min-h-0 flex-col h-[calc(100vh-3rem)] overflow-hidden bg-slate-50">
        <div className="shrink-0 border-b border-slate-200 bg-white px-4 py-3 shadow-sm">
          <form onSubmit={handleGoToAddress} className="flex flex-wrap items-center gap-3">
            <div className="flex-1 min-w-[240px]">
              <label className="mb-1.5 block text-xs font-semibold uppercase tracking-widest text-slate-400">{t.goToAddress}</label>
              <div className="flex items-center gap-2">
                <MapPin size={16} className="shrink-0 text-slate-400" />
                <input
                  value={addressQuery}
                  onChange={(e) => setAddressQuery(e.target.value)}
                  placeholder={t.placeholder}
                  className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-400"
                />
                <button
                  type="submit"
                  disabled={addressLoading}
                  className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700 disabled:opacity-60"
                >
                  {addressLoading ? <Loader2 size={14} className="animate-spin" /> : <Search size={14} />}
                  {t.go}
                </button>
              </div>
            </div>
            <div className="flex min-w-[220px] flex-1 items-center justify-between gap-3 text-xs text-slate-500">
              <span>
                {addressLabel ? t.centeredOn.replace("{address}", addressLabel) : t.searchHint}
              </span>
              <span>
                {count.toLocaleString()} ·{" "}
                {count === 0 && !loading
                  ? t.noGeocoded
                  : clustered
                  ? t.clickToZoom
                  : truncated
                  ? t.capped
                  : t.inView}
              </span>
            </div>
          </form>
          {addressError && <p className="mt-2 text-xs text-red-600">{addressError}</p>}
          {error && <p className="mt-2 text-xs text-red-600">{t.mapError.replace("{error}", error)}</p>}
        </div>

        <div className="shrink-0">
          <FilterBar
            filters={filters}
            cantons={cantons}
            taxonomy={taxonomy}
            onChange={handleFiltersChange}
            onClear={() => handleFiltersChange(DEFAULT_FILTERS)}
            resultCount={count}
          />
        </div>

        <div className="relative min-h-0 flex-1">
          <div ref={mapRef} className="h-full w-full" />
          {loading && (
            <div className="pointer-events-none absolute top-2 right-2 z-[400] flex items-center gap-1.5 rounded-lg bg-white/90 px-2.5 py-1.5 text-xs text-slate-600 shadow">
              <Loader2 size={12} className="animate-spin" /> {t.loading}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
