"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { Play, ChevronDown, ChevronUp } from "lucide-react";
import { triggerJob } from "@/lib/api";

function Section({ title, children, defaultOpen = false }: { title: string; children: React.ReactNode; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border border-slate-200 rounded-xl overflow-hidden">
      <button
        onClick={() => setOpen(v => !v)}
        className="w-full flex items-center justify-between px-5 py-3.5 bg-white hover:bg-slate-50 transition-colors text-left"
      >
        <span className="font-semibold text-slate-800">{title}</span>
        {open ? <ChevronUp size={16} className="text-slate-400" /> : <ChevronDown size={16} className="text-slate-400" />}
      </button>
      {open && <div className="px-5 pb-5 pt-3 bg-white border-t border-slate-100">{children}</div>}
    </div>
  );
}

function Field({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) {
  return (
    <div>
      <label className="block text-sm font-medium text-slate-700 mb-1">{label}</label>
      {children}
      {hint && <p className="mt-1 text-xs text-slate-400">{hint}</p>}
    </div>
  );
}

const inputCls = "w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-300 focus:border-transparent";
const checkCls = "rounded border-slate-300 text-blue-600 focus:ring-blue-300";

function SubmitBtn({ loading }: { loading: boolean }) {
  return (
    <button
      type="submit"
      disabled={loading}
      className="flex items-center gap-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-60 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors"
    >
      <Play size={14} />
      {loading ? "Queuing…" : "Start job"}
    </button>
  );
}

export function CollectionClient() {
  const router = useRouter();
  const [loading, setLoading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit(endpoint: string, body: object) {
    setLoading(endpoint);
    setError(null);
    try {
      await triggerJob(endpoint, body);
      router.push("/app/jobs");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(null);
    }
  }

  return (
    <div className="p-6 max-w-3xl mx-auto space-y-4">
      <div>
        <h1 className="text-xl font-semibold text-slate-900">Collection</h1>
        <p className="text-sm text-slate-500 mt-0.5">Trigger data collection and enrichment jobs</p>
      </div>

      {error && (
        <div className="sticky top-2 z-20 bg-red-50 border border-red-300 text-red-800 rounded-lg px-4 py-3 text-sm flex items-start gap-2 shadow-sm">
          <span className="font-semibold shrink-0">Error:</span>
          <span>{error}</span>
          <button onClick={() => setError(null)} className="ml-auto shrink-0 text-red-400 hover:text-red-600">✕</button>
        </div>
      )}

      <Section title="Bulk import from Zefix" defaultOpen>
        <form onSubmit={async e => {
          e.preventDefault();
          const fd = new FormData(e.currentTarget);
          const cantons = (fd.get("cantons") as string || "").split(",").map(c => c.trim().toUpperCase()).filter(Boolean);
          const startFrom = (fd.get("start_from_canton") as string || "").trim().toUpperCase() || null;
          const emptyAbort = parseInt(fd.get("empty_abort_threshold") as string) || 100;
          await submit("collection/bulk", {
            cantons: cantons.length ? cantons : null,
            start_from_canton: startFrom,
            active_only: fd.get("active_only") === "on",
            delay: parseFloat(fd.get("delay") as string) || 0.5,
            empty_abort_threshold: emptyAbort,
          });
        }} className="space-y-4">
          <Field label="Cantons" hint="Comma-separated codes (e.g. BE,ZH). Leave blank for all 26.">
            <input name="cantons" className={inputCls} placeholder="All cantons" />
          </Field>
          <Field label="Start from canton" hint="Resume a failed run by skipping cantons before this one (e.g. GL to restart from Glarus onwards).">
            <input name="start_from_canton" className={cn(inputCls, "w-24")} placeholder="e.g. GL" />
          </Field>
          <div className="flex gap-6">
            <label className="flex items-center gap-2 text-sm text-slate-700">
              <input type="checkbox" name="active_only" defaultChecked className={checkCls} />
              Active companies only
            </label>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <Field label="Request delay (seconds)">
              <input name="delay" type="number" step="0.1" min="0.1" defaultValue="0.5" className={inputCls} />
            </Field>
            <Field label="Empty abort threshold" hint="Stop if this many consecutive prefixes return no results (Zefix may be down).">
              <input name="empty_abort_threshold" type="number" min="1" defaultValue="100" className={inputCls} />
            </Field>
          </div>
          <SubmitBtn loading={loading === "collection/bulk"} />
        </form>
      </Section>

      <Section title="Batch enrichment (Google search)">
        <form onSubmit={async e => {
          e.preventDefault();
          const fd = new FormData(e.currentTarget);
          await submit("collection/batch", {
            limit: parseInt(fd.get("limit") as string) || 100,
            only_missing_website: fd.get("all_companies") !== "on",
            refresh_zefix: fd.get("refresh_zefix") === "on",
            run_google: true,
            canton: (fd.get("canton") as string)?.trim().toUpperCase() || null,
            min_flex_score: parseInt(fd.get("min_flex_score") as string) || null,
          });
        }} className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <Field label="Limit">
              <input name="limit" type="number" min="1" defaultValue="100" className={inputCls} />
            </Field>
            <Field label="Canton">
              <input name="canton" className={inputCls} placeholder="Any" />
            </Field>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <Field label="Min Flex score">
              <input name="min_flex_score" type="number" min="0" max="100" className={inputCls} placeholder="—" />
            </Field>
          </div>
          <div className="flex gap-6 flex-wrap">
            <label className="flex items-center gap-2 text-sm text-slate-700">
              <input type="checkbox" name="all_companies" className={checkCls} />
              Include companies already with website
            </label>
            <label className="flex items-center gap-2 text-sm text-slate-700">
              <input type="checkbox" name="refresh_zefix" className={checkCls} />
              Refresh Zefix data
            </label>
          </div>
          <SubmitBtn loading={loading === "collection/batch"} />
        </form>
      </Section>

      <Section title="Specific company search">
        <form onSubmit={async e => {
          e.preventDefault();
          const fd = new FormData(e.currentTarget);
          const names = (fd.get("names") as string || "").split("\n").map(n => n.trim()).filter(Boolean);
          const uids = (fd.get("uids") as string || "").split("\n").map(u => u.trim()).filter(Boolean);
          await submit("collection/initial", {
            names,
            uids,
            canton: (fd.get("canton") as string)?.trim().toUpperCase() || null,
            active_only: fd.get("include_inactive") !== "on",
            run_google: fd.get("skip_google") !== "on",
          });
        }} className="space-y-4">
          <Field label="Company names" hint="One per line">
            <textarea name="names" rows={4} className={inputCls} placeholder="Acme AG&#10;Example GmbH" />
          </Field>
          <Field label="UIDs" hint="One per line">
            <textarea name="uids" rows={2} className={inputCls} placeholder="CHE-123.456.789" />
          </Field>
          <div className="grid grid-cols-2 gap-4">
            <Field label="Canton">
              <input name="canton" className={inputCls} placeholder="Any" />
            </Field>
          </div>
          <div className="flex gap-6 flex-wrap">
            <label className="flex items-center gap-2 text-sm text-slate-700">
              <input type="checkbox" name="include_inactive" className={checkCls} />
              Include inactive companies
            </label>
            <label className="flex items-center gap-2 text-sm text-slate-700">
              <input type="checkbox" name="skip_google" className={checkCls} />
              Skip Google search
            </label>
          </div>
          <SubmitBtn loading={loading === "collection/initial"} />
        </form>
      </Section>

      <Section title="Zefix detail fetch">
        <form onSubmit={async e => {
          e.preventDefault();
          const fd = new FormData(e.currentTarget);
          const cantons = (fd.get("cantons") as string || "").split(",").map(c => c.trim().toUpperCase()).filter(Boolean);
          const uids = (fd.get("uids") as string || "").split("\n").map(u => u.trim()).filter(Boolean);
          await submit("collection/detail", {
            cantons: cantons.length ? cantons : null,
            uids: uids.length ? uids : null,
            only_missing_details: fd.get("only_missing_details") === "on",
            delay: parseFloat(fd.get("delay") as string) || 0.3,
          });
        }} className="space-y-4">
          <Field label="Cantons" hint="Comma-separated. Leave blank for all.">
            <input name="cantons" className={inputCls} placeholder="All" />
          </Field>
          <Field label="UIDs" hint="One per line — leave blank to use cantons filter">
            <textarea name="uids" rows={3} className={inputCls} />
          </Field>
          <Field label="Request delay (seconds)">
            <input name="delay" type="number" step="0.1" min="0.1" defaultValue="0.3" className={cn(inputCls, "w-32")} />
          </Field>
          <label className="flex items-center gap-2 text-sm text-slate-700">
            <input type="checkbox" name="only_missing_details" className={checkCls} />
            Only companies missing details
          </label>
          <SubmitBtn loading={loading === "collection/detail"} />
        </form>
      </Section>
    </div>
  );
}

function cn(...classes: (string | undefined | false)[]) {
  return classes.filter(Boolean).join(" ");
}
