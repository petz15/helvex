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

      <Section title="Re-extract Purpose">
        <form onSubmit={async e => {
          e.preventDefault();
          const fd = new FormData(e.currentTarget);
          await submit("scoring/reextract-purpose", {
            only_missing_purpose: fd.get("only_missing_purpose") === "on",
          });
        }} className="space-y-4">
          <div>
            <h2 className="text-sm font-semibold text-slate-800">Re-extract purpose</h2>
            <p className="mt-1 text-xs text-slate-500">Refresh company purpose text from detailed Zefix raw data.</p>
          </div>
          <label className="flex items-center gap-2 text-sm text-slate-700">
            <input type="checkbox" name="only_missing_purpose" defaultChecked className={checkCls} />
            Only companies missing purpose text
          </label>
          <SubmitBtn loading={loading === "scoring/reextract-purpose"} />
        </form>
      </Section>

      <Section title="Reclassify NOGA">
          <form onSubmit={async e => {
            e.preventDefault();
            const fd = new FormData(e.currentTarget);
            await submit("scoring/reclassify-noga", {
              only_missing_noga: fd.get("only_missing_noga") === "on",
              only_detailed_raw: fd.get("only_detailed_raw") === "on",
            });
          }} className="space-y-4">
            <div>
              <h2 className="text-sm font-semibold text-slate-800">Reclassify NOGA</h2>
              <p className="mt-1 text-xs text-slate-500">Recompute NOGA labels and hierarchy paths from the local taxonomy.</p>
            </div>
            <div className="flex gap-6 flex-wrap">
              <label className="flex items-center gap-2 text-sm text-slate-700">
                <input type="checkbox" name="only_missing_noga" className={checkCls} />
                Only companies missing NOGA data
              </label>
              <label className="flex items-center gap-2 text-sm text-slate-700">
                <input type="checkbox" name="only_detailed_raw" defaultChecked className={checkCls} />
                Only companies with detailed raw Zefix data
              </label>
            </div>
            <SubmitBtn loading={loading === "scoring/reclassify-noga"} />
          </form>
      </Section>
        
      <Section title="TF-IDF + KMeans pipeline">
          <form onSubmit={async e => {
            e.preventDefault();
            const fd = new FormData(e.currentTarget);
            await submit("scoring/cluster", {
              n_clusters: parseInt(fd.get("n_clusters") as string) || 150,
              max_clusters_per_company: parseInt(fd.get("max_clusters_per_company") as string) || 7,
              min_similarity: parseFloat(fd.get("min_similarity") as string) || 0.1,
              n_components: parseInt(fd.get("n_components") as string) || 50,
              top_terms: parseInt(fd.get("top_terms") as string) || 5,
              top_keywords_per_company: parseInt(fd.get("top_keywords_per_company") as string) || 10,
              canton: (fd.get("canton") as string)?.trim().toUpperCase() || null,
              min_zefix_score: parseInt(fd.get("min_zefix_score") as string) || null,
              max_zefix_score: parseInt(fd.get("max_zefix_score") as string) || null,
              limit: parseInt(fd.get("limit") as string) || null,
              use_keywords: fd.get("use_keywords") === "on",
            });
          }} className="space-y-4">
            <div>
              <h2 className="text-sm font-semibold text-slate-800">TF-IDF + KMeans pipeline</h2>
              <p className="mt-1 text-xs text-slate-500">Production clustering job: TF-IDF, SVD, MiniBatchKMeans, then keyword/label writes.</p>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <Field label="Clusters">
                <input name="n_clusters" type="number" min="1" defaultValue="150" className={inputCls} />
              </Field>
              <Field label="Max clusters/company">
                <input name="max_clusters_per_company" type="number" min="1" defaultValue="7" className={inputCls} />
              </Field>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <Field label="Min similarity">
                <input name="min_similarity" type="number" min="0" max="1" step="0.01" defaultValue="0.1" className={inputCls} />
              </Field>
              <Field label="Components">
                <input name="n_components" type="number" min="2" defaultValue="50" className={inputCls} />
              </Field>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <Field label="Top cluster terms">
                <input name="top_terms" type="number" min="1" defaultValue="5" className={inputCls} />
              </Field>
              <Field label="Top keywords/company">
                <input name="top_keywords_per_company" type="number" min="1" defaultValue="10" className={inputCls} />
              </Field>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <Field label="Canton">
                <input name="canton" className={inputCls} placeholder="Any" />
              </Field>
              <Field label="Limit">
                <input name="limit" type="number" min="1" className={inputCls} placeholder="All" />
              </Field>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <Field label="Min Zefix score">
                <input name="min_zefix_score" type="number" min="0" max="100" className={inputCls} placeholder="—" />
              </Field>
              <Field label="Max Zefix score">
                <input name="max_zefix_score" type="number" min="0" max="100" className={inputCls} placeholder="—" />
              </Field>
            </div>
            <label className="flex items-center gap-2 text-sm text-slate-700">
              <input type="checkbox" name="use_keywords" className={checkCls} />
              Use existing purpose keywords during clustering
            </label>
            <SubmitBtn loading={loading === "scoring/cluster"} />
          </form>

      </Section>

      <Section title="HDBSCAN pipeline">

        <form onSubmit={async e => {
          e.preventDefault();
          const fd = new FormData(e.currentTarget);
          await submit("scoring/hdbscan", {
            min_cluster_size: parseInt(fd.get("hdbscan_min_cluster_size") as string) || 30,
            min_samples: parseInt(fd.get("hdbscan_min_samples") as string) || null,
            cluster_selection_epsilon: parseFloat(fd.get("hdbscan_epsilon") as string) || 0,
            n_components: parseInt(fd.get("hdbscan_n_components") as string) || 50,
            top_terms: parseInt(fd.get("hdbscan_top_terms") as string) || 5,
            top_keywords_per_company: parseInt(fd.get("hdbscan_top_keywords") as string) || 10,
            canton: (fd.get("hdbscan_canton") as string)?.trim().toUpperCase() || null,
            min_zefix_score: parseInt(fd.get("hdbscan_min_zefix_score") as string) || null,
            max_zefix_score: parseInt(fd.get("hdbscan_max_zefix_score") as string) || null,
            limit: parseInt(fd.get("hdbscan_limit") as string) || null,
            use_keywords: fd.get("hdbscan_use_keywords") === "on",
            use_batch_merge: fd.get("hdbscan_use_batch_merge") === "on",
          });
        }} className="space-y-4">
          <div>
            <h2 className="text-sm font-semibold text-slate-800">HDBSCAN pipeline (separate wire)</h2>
            <p className="mt-1 text-xs text-slate-500">Density-based clustering experimentation. ⚠️ Memory-intensive for large datasets.</p>
          </div>

          {/* Memory warning and recommendations */}
          <div className="bg-amber-50 border border-amber-300 rounded-lg px-4 py-3 space-y-2">
            <div className="text-xs font-semibold text-amber-900">⚠️ Memory constraints (16 GB pod limit)</div>
            <ul className="text-xs text-amber-800 space-y-1 ml-3">
              <li>• <strong>Full dataset (763K companies):</strong> Requires ~2.3 TB for distance matrix → <span className="font-semibold">Will fail with OOM after ~1 hour</span></li>
              <li>• <strong>Safe limits (standard):</strong> &lt;30K companies (use <code className="bg-amber-100 px-1">--canton</code> or <code className="bg-amber-100 px-1">--limit</code>)</li>
              <li>• ✓ <strong>For full dataset:</strong> Enable <code className="bg-amber-100 px-1">Batch+Merge mode</code> below (slower but complete: 30–40 min)</li>
            </ul>
          </div>

          {/* Recommended alternatives */}
          <div className="bg-blue-50 border border-blue-200 rounded-lg px-4 py-3 space-y-2">
            <div className="text-xs font-semibold text-blue-900">💡 Recommended alternatives</div>
            <ul className="text-xs text-blue-800 space-y-1 ml-3">
              <li>• <strong>For full dataset:</strong> Use <code className="bg-blue-100 px-1">TF-IDF + KMeans</code> (above) or HDBSCAN with Batch+Merge — no size limit</li>
              <li>• <strong>For standard HDBSCAN (30K only):</strong> Batch by canton — 26 separate runs (~28K each), ~5–8 min per batch</li>
              <li>• <strong>Quick test:</strong> Set limit=10000 or choose a single canton below</li>
            </ul>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <Field label="Min cluster size">
              <input name="hdbscan_min_cluster_size" type="number" min="2" defaultValue="30" className={inputCls} />
            </Field>
            <Field label="Min samples">
              <input name="hdbscan_min_samples" type="number" min="1" className={inputCls} placeholder="Auto" />
            </Field>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <Field label="Selection epsilon">
              <input name="hdbscan_epsilon" type="number" min="0" step="0.01" defaultValue="0" className={inputCls} />
            </Field>
            <Field label="Components">
              <input name="hdbscan_n_components" type="number" min="2" defaultValue="50" className={inputCls} />
            </Field>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <Field label="Top cluster terms">
              <input name="hdbscan_top_terms" type="number" min="1" defaultValue="5" className={inputCls} />
            </Field>
            <Field label="Top keywords/company">
              <input name="hdbscan_top_keywords" type="number" min="1" defaultValue="10" className={inputCls} />
            </Field>
          </div>

          {/* Dataset filters */}
          <div className="bg-slate-50 rounded-lg px-4 py-3 border border-slate-200 space-y-4">
            <div className="text-xs font-semibold text-slate-700">Dataset filters (leave blank for all)</div>
            <div className="grid grid-cols-2 gap-4">
              <Field 
                label="Canton (e.g., ZH, BE, LU)" 
                hint="Only cluster this canton (28K companies avg)"
              >
                <input name="hdbscan_canton" className={inputCls} placeholder="Any" />
              </Field>
              <Field 
                label="Limit companies" 
                hint="Max companies to cluster"
              >
                <input name="hdbscan_limit" type="number" min="1" className={inputCls} placeholder="All" />
              </Field>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <Field label="Min Zefix score">
                <input name="hdbscan_min_zefix_score" type="number" min="0" max="100" className={inputCls} placeholder="—" />
              </Field>
              <Field label="Max Zefix score">
                <input name="hdbscan_max_zefix_score" type="number" min="0" max="100" className={inputCls} placeholder="—" />
              </Field>
            </div>
          </div>

          <label className="flex items-center gap-2 text-sm text-slate-700">
            <input type="checkbox" name="hdbscan_use_keywords" className={checkCls} />
            Use existing purpose keywords
          </label>

          <div className="bg-green-50 border border-green-200 rounded-lg px-4 py-3">
            <label className="flex items-center gap-2 text-sm text-green-900">
              <input type="checkbox" name="hdbscan_use_batch_merge" className={checkCls} />
              <span><strong>Batch+Merge mode</strong> — For large datasets (30K–763K companies)</span>
            </label>
            <p className="ml-6 text-xs text-green-800 mt-1">
              Splits dataset into 100K batches, clusters independently, merges via hierarchical clustering. 
              <br />Slower but handles unlimited dataset size. ~5–8 min per batch (763K ≈ 30–40 min total).
            </p>
          </div>

          <SubmitBtn loading={loading === "scoring/hdbscan"} />
        </form>

      </Section>

      <Section title="BIRCH clustering (memory-efficient full dataset)">

        <form onSubmit={async e => {
          e.preventDefault();
          const fd = new FormData(e.currentTarget);
          await submit("scoring/birch", {
            n_clusters: parseInt(fd.get("birch_n_clusters") as string) || 150,
            n_components: parseInt(fd.get("birch_n_components") as string) || 50,
            top_terms: parseInt(fd.get("birch_top_terms") as string) || 5,
            top_keywords_per_company: parseInt(fd.get("birch_top_keywords") as string) || 10,
            canton: (fd.get("birch_canton") as string)?.trim().toUpperCase() || null,
            min_zefix_score: parseInt(fd.get("birch_min_zefix_score") as string) || null,
            max_zefix_score: parseInt(fd.get("birch_max_zefix_score") as string) || null,
            limit: parseInt(fd.get("birch_limit") as string) || null,
            use_keywords: fd.get("birch_use_keywords") === "on",
          });
        }} className="space-y-4">
          <div>
            <h2 className="text-sm font-semibold text-slate-800">BIRCH pipeline (single-pass clustering)</h2>
            <p className="mt-1 text-xs text-slate-500">Balanced Iterative Reducing and Clustering — designed for large datasets with limited memory.</p>
          </div>

          {/* BIRCH characteristics */}
          <div className="bg-green-50 border border-green-300 rounded-lg px-4 py-3 space-y-2">
            <div className="text-xs font-semibold text-green-900">✓ Memory-efficient for full dataset</div>
            <ul className="text-xs text-green-800 space-y-1 ml-3">
              <li>• <strong>Full dataset (763K):</strong> ~8-15 minutes, ~500 MB memory ✓ Safe</li>
              <li>• <strong>Algorithm:</strong> Incremental CF-tree (Clustering Feature tree)</li>
              <li>• <strong>Trade-off:</strong> No density estimation (less sophisticated than HDBSCAN, but works on any scale)</li>
              <li>• <strong>Recommended:</strong> Use for full dataset when HDBSCAN memory issues occur</li>
            </ul>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <Field label="Target clusters">
              <input name="birch_n_clusters" type="number" min="1" defaultValue="150" className={inputCls} />
            </Field>
            <Field label="Components">
              <input name="birch_n_components" type="number" min="2" defaultValue="50" className={inputCls} />
            </Field>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <Field label="Top cluster terms">
              <input name="birch_top_terms" type="number" min="1" defaultValue="5" className={inputCls} />
            </Field>
            <Field label="Top keywords/company">
              <input name="birch_top_keywords" type="number" min="1" defaultValue="10" className={inputCls} />
            </Field>
          </div>

          {/* Dataset filters */}
          <div className="bg-slate-50 rounded-lg px-4 py-3 border border-slate-200 space-y-4">
            <div className="text-xs font-semibold text-slate-700">Dataset filters (optional)</div>
            <div className="grid grid-cols-2 gap-4">
              <Field label="Canton">
                <input name="birch_canton" className={inputCls} placeholder="Any" />
              </Field>
              <Field label="Limit">
                <input name="birch_limit" type="number" min="1" className={inputCls} placeholder="All" />
              </Field>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <Field label="Min Zefix score">
                <input name="birch_min_zefix_score" type="number" min="0" max="100" className={inputCls} placeholder="—" />
              </Field>
              <Field label="Max Zefix score">
                <input name="birch_max_zefix_score" type="number" min="0" max="100" className={inputCls} placeholder="—" />
              </Field>
            </div>
          </div>

          <label className="flex items-center gap-2 text-sm text-slate-700">
            <input type="checkbox" name="birch_use_keywords" className={checkCls} />
            Use existing purpose keywords
          </label>
          <SubmitBtn loading={loading === "scoring/birch"} />
        </form>

      </Section>

      <Section title="Re-extract keywords">

        <form onSubmit={async e => {
          e.preventDefault();
          const fd = new FormData(e.currentTarget);
          await submit("scoring/reextract-keywords", {
            only_missing: fd.get("only_missing_keywords") === "on",
            canton: (fd.get("keywords_canton") as string)?.trim().toUpperCase() || null,
            limit: parseInt(fd.get("keywords_limit") as string) || null,
          });
        }} className="space-y-4">
          <div>
            <h2 className="text-sm font-semibold text-slate-800">Re-extract keywords</h2>
            <p className="mt-1 text-xs text-slate-500">Refresh purpose keywords from the cached TF-IDF vectorizer and cluster artifacts.</p>
          </div>
          <div className="flex gap-6 flex-wrap">
            <label className="flex items-center gap-2 text-sm text-slate-700">
              <input type="checkbox" name="only_missing_keywords" className={checkCls} />
              Only companies missing keywords
            </label>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <Field label="Canton">
              <input name="keywords_canton" className={inputCls} placeholder="Any" />
            </Field>
            <Field label="Limit">
              <input name="keywords_limit" type="number" min="1" className={inputCls} placeholder="All" />
            </Field>
          </div>
          <SubmitBtn loading={loading === "scoring/reextract-keywords"} />
        </form>

      </Section>

      <Section title="Cluster analysis">

        <form onSubmit={async e => {
          e.preventDefault();
          const fd = new FormData(e.currentTarget);
          await submit("scoring/cluster-analysis", {
            top_n_clusters: parseInt(fd.get("analysis_top_n_clusters") as string) || 20,
            top_n_terms: parseInt(fd.get("analysis_top_n_terms") as string) || 10,
          });
        }} className="space-y-4">
          <div>
            <h2 className="text-sm font-semibold text-slate-800">Cross-cluster analysis</h2>
            <p className="mt-1 text-xs text-slate-500">Generate a summary of terms shared across cluster labels.</p>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <Field label="Top clusters">
              <input name="analysis_top_n_clusters" type="number" min="1" defaultValue="20" className={inputCls} />
            </Field>
            <Field label="Top terms">
              <input name="analysis_top_n_terms" type="number" min="1" defaultValue="10" className={inputCls} />
            </Field>
          </div>
          <SubmitBtn loading={loading === "scoring/cluster-analysis"} />
        </form>

      </Section>

      <Section title="Cluster drift check">

        <form onSubmit={async e => {
          e.preventDefault();
          const fd = new FormData(e.currentTarget);
          await submit("scoring/cluster-drift-check", {
            days: parseInt(fd.get("drift_days") as string) || 7,
            warn_threshold: parseFloat(fd.get("drift_threshold") as string) || 0.3,
          });
        }} className="space-y-4">
          <div>
            <h2 className="text-sm font-semibold text-slate-800">Cluster drift check</h2>
            <p className="mt-1 text-xs text-slate-500">Check whether recent companies are falling through without cluster labels.</p>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <Field label="Days">
              <input name="drift_days" type="number" min="1" defaultValue="7" className={inputCls} />
            </Field>
            <Field label="Warn threshold">
              <input name="drift_threshold" type="number" min="0" max="1" step="0.01" defaultValue="0.3" className={inputCls} />
            </Field>
          </div>
          <SubmitBtn loading={loading === "scoring/cluster-drift-check"} />
        </form>
      
      </Section>  

      <Section title="SHAB Daily Import">
        <form onSubmit={async e => {
          e.preventDefault();
          const fd = new FormData(e.currentTarget);
          const date = (fd.get("shab_date") as string)?.trim() || null;
          await submit("collection/shab-daily", {
            date: date || undefined,
            request_delay: parseFloat(fd.get("shab_delay") as string) || 0.15,
          });
        }} className="space-y-4">
          <div>
            <h2 className="text-sm font-semibold text-slate-800">SHAB daily import</h2>
            <p className="mt-1 text-xs text-slate-500">
              Imports HR01 (new), HR02 (mutations) and HR03 (deletions) from the SHAB public API for a single day.
              Leave date empty to import yesterday. New registrations automatically trigger a Zefix detail fetch.
            </p>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <Field label="Date (YYYY-MM-DD)" hint="Leave empty for yesterday">
              <input name="shab_date" type="date" className={inputCls} />
            </Field>
            <Field label="Request delay (seconds)" hint="Between SHAB detail API calls">
              <input name="shab_delay" type="number" step="0.05" min="0.05" defaultValue="0.15" className={inputCls} />
            </Field>
          </div>
          <SubmitBtn loading={loading === "collection/shab-daily"} />
        </form>
      </Section>

      <Section title="SHAB Historical Backfill">
        <form onSubmit={async e => {
          e.preventDefault();
          const fd = new FormData(e.currentTarget);
          const fromDate = (fd.get("shab_from_date") as string)?.trim();
          const toDate = (fd.get("shab_to_date") as string)?.trim() || undefined;
          if (!fromDate) { setError("From date is required for SHAB backfill"); return; }
          await submit("collection/shab-backfill", {
            from_date: fromDate,
            to_date: toDate,
            request_delay: parseFloat(fd.get("shab_backfill_delay") as string) || 0.15,
          });
        }} className="space-y-4">
          <div>
            <h2 className="text-sm font-semibold text-slate-800">SHAB historical backfill</h2>
            <p className="mt-1 text-xs text-slate-500">
              Fetches all SHAB HR publications across a date range. Use this to import past data.
              Leave &quot;to date&quot; empty to backfill through yesterday.
            </p>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <Field label="From date (YYYY-MM-DD)" hint="Required — earliest date to import">
              <input name="shab_from_date" type="date" required className={inputCls} />
            </Field>
            <Field label="To date (YYYY-MM-DD)" hint="Leave empty for yesterday">
              <input name="shab_to_date" type="date" className={inputCls} />
            </Field>
          </div>
          <Field label="Request delay (seconds)">
            <input name="shab_backfill_delay" type="number" step="0.05" min="0.05" defaultValue="0.15" className={cn(inputCls, "w-32")} />
          </Field>
          <SubmitBtn loading={loading === "collection/shab-backfill"} />
        </form>
      </Section>

      <Section title="Bulk import from Zefix">
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
