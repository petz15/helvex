import type { AppSettings, BoilerplatePattern, Company, CompanyFilters, CompanyPage, CompanyStats, Job, JobEvent } from "./types";

const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";

function buildUrl(path: string, params?: Record<string, string | number | undefined | null>): string {
  const url = new URL(BASE + path, typeof window !== "undefined" ? window.location.href : "http://localhost:3000");
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && v !== "") {
        url.searchParams.set(k, String(v));
      }
    }
  }
  return url.pathname + url.search;
}

export async function fetchCompanies(filters: CompanyFilters = {}): Promise<CompanyPage> {
  const { page = 1, page_size = 50, sort = "-updated", ...rest } = filters;
  const url = buildUrl("/api/v1/companies", { page, page_size, sort, ...rest });
  const res = await fetch(url, { credentials: "include" });
  if (!res.ok) throw new Error(`Failed to fetch companies: ${res.status}`);
  return res.json();
}

export async function fetchCompany(id: number): Promise<Company> {
  const res = await fetch(`/api/v1/companies/${id}`, { credentials: "include" });
  if (!res.ok) throw new Error(`Company ${id} not found`);
  return res.json();
}

export async function fetchStats(): Promise<CompanyStats> {
  const res = await fetch("/api/v1/companies/stats", { credentials: "include" });
  if (!res.ok) throw new Error("Failed to fetch stats");
  return res.json();
}

export async function fetchCantons(): Promise<string[]> {
  const res = await fetch("/api/v1/companies/cantons", { credentials: "include" });
  if (!res.ok) return [];
  return res.json();
}

export async function fetchTaxonomy(): Promise<Record<string, [string, number][]>> {
  const res = await fetch("/api/v1/companies/taxonomy", { credentials: "include" });
  if (!res.ok) return {};
  return res.json();
}

export async function createNote(companyId: number, content: string): Promise<import("./types").Note> {
  const res = await fetch(`/api/v1/companies/${companyId}/notes`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  if (!res.ok) throw new Error("Failed to create note");
  return res.json();
}

export async function deleteNote(companyId: number, noteId: number): Promise<void> {
  await fetch(`/api/v1/companies/${companyId}/notes/${noteId}`, {
    method: "DELETE",
    credentials: "include",
  });
}

export async function updateCompany(id: number, data: Partial<Company>): Promise<Company> {
  const res = await fetch(`/api/v1/companies/${id}`, {
    method: "PATCH",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error("Failed to update company");
  return res.json();
}

// ── Jobs ──────────────────────────────────────────────────────────────────────

export async function fetchJobs(): Promise<Job[]> {
  const res = await fetch("/api/v1/jobs", { credentials: "include" });
  if (!res.ok) throw new Error("Failed to fetch jobs");
  return res.json();
}

export async function fetchJobEvents(jobId: number): Promise<JobEvent[]> {
  const res = await fetch(`/api/v1/jobs/${jobId}/events`, { credentials: "include" });
  if (!res.ok) return [];
  return res.json();
}

export async function cancelJob(id: number): Promise<void> {
  await fetch(`/api/v1/jobs/${id}/cancel`, { method: "POST", credentials: "include" });
}

export async function pauseJob(id: number): Promise<void> {
  await fetch(`/api/v1/jobs/${id}/pause`, { method: "POST", credentials: "include" });
}

export async function resumeJob(id: number): Promise<void> {
  await fetch(`/api/v1/jobs/${id}/resume`, { method: "POST", credentials: "include" });
}

export async function triggerJob(endpoint: string, body?: object): Promise<Job> {
  const res = await fetch(`/api/v1/${endpoint}`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : "{}",
  });
  if (!res.ok) throw new Error(`Failed to trigger job: ${res.status}`);
  return res.json();
}

// ── Settings ──────────────────────────────────────────────────────────────────

export async function fetchSettings(): Promise<AppSettings> {
  const res = await fetch("/api/v1/settings", { credentials: "include" });
  if (!res.ok) throw new Error("Failed to fetch settings");
  return res.json();
}

export async function saveSettings(data: Partial<AppSettings>): Promise<void> {
  const res = await fetch("/api/v1/settings", {
    method: "PUT",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error("Failed to save settings");
}

export async function fetchBoilerplate(): Promise<BoilerplatePattern[]> {
  const res = await fetch("/api/v1/boilerplate", { credentials: "include" });
  if (!res.ok) return [];
  return res.json();
}

export async function createBoilerplate(data: { pattern: string; description?: string; example?: string }): Promise<BoilerplatePattern> {
  const res = await fetch("/api/v1/boilerplate", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error("Failed to create boilerplate pattern");
  return res.json();
}

export async function toggleBoilerplate(id: number): Promise<void> {
  await fetch(`/api/v1/boilerplate/${id}/toggle`, { method: "PATCH", credentials: "include" });
}

export async function deleteBoilerplate(id: number): Promise<void> {
  await fetch(`/api/v1/boilerplate/${id}`, { method: "DELETE", credentials: "include" });
}

// ── Map ───────────────────────────────────────────────────────────────────────

export async function fetchMapData(params?: Record<string, string>): Promise<{ features: import("./types").MapFeature[]; truncated: boolean; count: number }> {
  const url = buildUrl("/api/v1/map", params as Record<string, string | number | undefined | null>);
  const res = await fetch(url, { credentials: "include" });
  if (!res.ok) throw new Error("Failed to fetch map data");
  return res.json();
}
