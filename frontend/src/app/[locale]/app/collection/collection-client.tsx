"use client";
import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { Play, ChevronDown, ChevronUp, BrainCircuit } from "lucide-react";
import useSWR from "swr";
import { triggerJob, fetchCurrentUser } from "@/lib/api";
import { useI18n } from "@/i18n/context";
import { useApiErrorHandler } from "@/lib/use-api-error";

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


// ── ML Job Reference ──────────────────────────────────────────────────────────

const ML_JOB_DOCS: { job_type: string; label: string; description: string; when: string; prereq?: string }[] = [
  {
    job_type: "embed_purpose_full",
    label: "Embed Purpose (Full)",
    description:
      "Embeds raw company purpose text using the multilingual sentence-transformer model and stores the vectors in purpose_embedding. Used by semantic search and semantic clustering. embed_purpose_clean is preferred for classification tasks.",
    when: "Run after bulk import to enable semantic search. Re-run after large imports.",
    prereq: "Companies must have a purpose field.",
  },
  {
    job_type: "embed_purpose_clean",
    label: "Embed Purpose (Clean)",
    description:
      "Embeds boilerplate-stripped, keyword-enriched purpose text and stores the vectors in purpose_embedding_clean. Higher quality than embed_purpose_full for classification. Used by semantic clustering.",
    when: "Run after recompute_keywords. Re-run after large imports or when keywords are refreshed.",
    prereq: "Requires purpose_keywords. Run recompute_keywords first.",
  },
  {
    job_type: "tfidf_kmeans_cluster",
    label: "TF-IDF K-Means",
    description:
      "Clusters companies by shared vocabulary using TF-IDF bag-of-words. With use_keywords=true (default) it clusters on pre-extracted keywords rather than raw purpose text, which improves coherence significantly. This is the only clustering method.",
    when: "Run after recompute_keywords when purpose text has changed significantly.",
    prereq: "Requires purpose_keywords.",
  },
  {
    job_type: "recompute_keywords",
    label: "Recompute Keywords",
    description:
      "Extracts fresh TF-IDF keywords from purpose text and writes them to purpose_keywords. Prerequisite for clustering and the best input for NOGA classification.",
    when: "Run after a fresh import or whenever purpose text has changed significantly.",
    prereq: "Use this before tfidf_kmeans_cluster.",
  },
  {
    job_type: "reextract_keywords",
    label: "Re-extract Keywords",
    description:
      "Re-extracts keywords using the cached TF-IDF vectorizer from S3 artifacts. Faster than recompute_keywords because it skips lemmatization. Only useful when the model artifacts are still valid.",
    when: "Use to backfill purpose_keywords for newly imported companies without rerunning the full pipeline.",
    prereq: "Requires S3 artifacts from a previous cluster run.",
  },
  {
    job_type: "build_noga_embeddings",
    label: "Build NOGA Embeddings",
    description:
      "Embeds all ~2000 NOGA level-5 categories in DE/FR/IT/EN using the multilingual sentence-transformer model and stores the vectors in PostgreSQL (pgvector). One-time setup step — re-run only when the NOGA taxonomy file changes.",
    when: "Run once after initial deployment, before any NOGA classification.",
    prereq: "Requires migration 0069 (pgvector extension + noga_embeddings table).",
  },
  {
    job_type: "detect_language_bulk",
    label: "Detect Purpose Language",
    description:
      "Detects the written language (DE/FR/IT/EN) of each company's purpose text using lingua and stores it in purpose_language. The NOGA classifier uses this to match each company against same-language NOGA embeddings, improving accuracy.",
    when: "Run once after build_noga_embeddings, and after large bulk imports.",
    prereq: "Run before reclassify_noga for best accuracy.",
  },
  {
    job_type: "reclassify_noga",
    label: "NOGA Classification",
    description:
      "Classifies each company into the official Swiss NOGA industry taxonomy using a hybrid of pgvector embedding similarity (60%) and token matching (40%). Language-aware: queries embeddings in the company's detected language. Produces noga_code, noga_label, noga_confidence (0–1), and a full ancestry path.",
    when: "Run after detect_language_bulk. Re-run after large imports or when NOGA embeddings are rebuilt.",
    prereq: "Requires purpose_keywords and noga_embeddings table to be populated.",
  },
  {
    job_type: "reclassify_low_conf_noga",
    label: "Re-run Low Confidence NOGA",
    description:
      "Re-classifies only companies whose noga_confidence is below the configured threshold (default 0.80). Useful after rebuilding embeddings to lift scores on previously uncertain results without re-processing the entire dataset.",
    when: "Run after rebuilding NOGA embeddings to improve borderline results.",
    prereq: "Requires noga_embeddings table to be populated.",
  },
  {
    job_type: "claude_classify",
    label: "Claude AI Classification",
    description:
      "Sends company data to Claude (Haiku model) to assign a short, human-readable business category and a quality score (0–100). Categories and system prompt are customisable per org.",
    when: "Run after scoring to enrich the browse/filter experience with readable categories.",
  },
  {
    job_type: "cluster_analysis",
    label: "Cluster Analysis",
    description:
      "Finds terms that appear across many different cluster labels — these are stopword candidates that dilute cluster specificity. Writes results to a .txt file for review.",
    when: "Run after clustering to audit label quality and discover new stopwords to add.",
  },
  {
    job_type: "cluster_drift_check",
    label: "Cluster Drift Check",
    description:
      "Checks what fraction of recently imported companies lack a cluster assignment. If the ratio exceeds 30% it raises a warning, indicating the clustering models may be stale.",
    when: "Run periodically (e.g. weekly) or after bulk imports to verify model freshness.",
  },
  {
    job_type: "extract_sogc_persons",
    label: "Extract SOGC Persons & Auditors",
    description:
      "Parses sogc_changes rows (personnel, director, auditor change types) into structured person entities, appearances, and auditor records. Deduplicates across companies using a normalized key (lastname + firstname + hometown). Also parses structured bisher fields (prior name, residence, nationality) from mutation annotations. Produces sogc_person_entities, sogc_person_appearances, and sogc_auditors.",
    when: "Run after SOGC Preprocess. Re-run after large SHAB backfills or when the SOGC change corpus has grown significantly. Always follow with Resolve Bisher Links.",
    prereq: "Requires sogc_changes to be populated via SOGC Preprocess first.",
  },
  {
    job_type: "resolve_bisher_links",
    label: "Resolve Bisher Links",
    description:
      "Bisher-first entity resolution: merges person entities that belong to the same person but have different normalized keys because the person changed their name (e.g. Müller → Müller-Schneider after marriage). Uses the parsed bisher fields from appearance mutations as hard links, finds the prior appearance at the same company, and unions the two entity IDs. Also re-evaluates confidence levels — entities with at least one confirmed bisher link are promoted to 'high' confidence.",
    when: "Run after Extract SOGC Persons. Required after any full re-extraction (mode=all) to merge name-change entities.",
    prereq: "Requires extract_sogc_persons to have run with bisher fields populated (migration 0085+).",
  },
];

function MlJobReference() {
  const { dict } = useI18n();
  const [open, setOpen] = useState(false);
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between text-left"
      >
        <div className="flex items-center gap-2">
          <BrainCircuit size={15} className="text-violet-500" />
          <span className="text-sm font-medium text-slate-800">{dict.app.jobs.mlDocsTitle}</span>
          <span className="text-xs text-slate-400">— what each job does and when to use it</span>
        </div>
        {open ? <ChevronUp size={14} className="text-slate-400" /> : <ChevronDown size={14} className="text-slate-400" />}
      </button>

      {open && (
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          {ML_JOB_DOCS.map((doc) => (
            <div key={doc.job_type} className="rounded-lg border border-slate-100 bg-slate-50 p-3 space-y-1">
              <div className="flex items-center gap-2">
                <span className="font-medium text-slate-800 text-sm">{doc.label}</span>
                <code className="text-[10px] text-slate-400 bg-slate-200 px-1.5 py-0.5 rounded font-mono">{doc.job_type}</code>
              </div>
              <p className="text-xs text-slate-600 leading-relaxed">{doc.description}</p>
              <p className="text-xs text-slate-400 italic">
                <span className="font-medium not-italic text-slate-500">When: </span>{doc.when}
              </p>
              {doc.prereq && (
                <p className="text-xs text-amber-600">
                  <span className="font-medium">Requires: </span>{doc.prereq}
                </p>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function GroupHeader({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-3 pt-2">
      <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider whitespace-nowrap">{label}</span>
      <div className="flex-1 h-px bg-slate-200" />
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
  const { dict } = useI18n();
  const router = useRouter();
  const handleApiError = useApiErrorHandler();
  const [loading, setLoading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // ML/job admin is superadmin-only — sales reps shouldn't see the dispatcher.
  const { data: me, isLoading: meLoading } = useSWR("me", fetchCurrentUser);
  useEffect(() => {
    if (!meLoading && me && !me.is_superadmin) {
      router.replace("/app/explorer");
    }
  }, [me, meLoading, router]);
  if (meLoading || !me) return null;
  if (!me.is_superadmin) return null;

  async function submit(endpoint: string, body: object) {
    setLoading(endpoint);
    setError(null);
    try {
      await triggerJob(endpoint, body);
      router.push("/app/jobs");
    } catch (e: unknown) {
      if (!handleApiError(e)) {
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setLoading(null);
    }
  }

  return (
    <div className="p-6 max-w-3xl mx-auto space-y-4">
      <div>
        <h1 className="text-xl font-semibold text-slate-900">{dict.app.collection.title}</h1>
        <p className="text-sm text-slate-500 mt-0.5">Trigger data collection and enrichment jobs</p>
      </div>

      <MlJobReference />

      {error && (
        <div className="sticky top-2 z-20 bg-red-50 border border-red-300 text-red-800 rounded-lg px-4 py-3 text-sm flex items-start gap-2 shadow-sm">
          <span className="font-semibold shrink-0">Error:</span>
          <span>{error}</span>
          <button onClick={() => setError(null)} className="ml-auto shrink-0 text-red-400 hover:text-red-600">✕</button>
        </div>
      )}

      <GroupHeader label="Zefix Import" />

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

      <GroupHeader label="SHAB / SOGC" />

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

      <Section title="SOGC Preprocess">
        <form onSubmit={async e => {
          e.preventDefault();
          const fd = new FormData(e.currentTarget);
          const mode = fd.get("sogc_mode") as string || "missing";
          const uidsRaw = (fd.get("sogc_uids") as string || "").trim();
          const uids = uidsRaw ? uidsRaw.split(/[\s,]+/).map(u => u.trim()).filter(Boolean) : [];
          const batchSize = parseInt(fd.get("sogc_batch_size") as string) || 500;
          await submit("collection/sogc-preprocess", {
            mode,
            uids: uids.length ? uids : undefined,
            batch_size: batchSize,
          });
        }} className="space-y-4">
          <div>
            <h2 className="text-sm font-semibold text-slate-800">SOGC publication preprocess</h2>
            <p className="mt-1 text-xs text-slate-500">
              Explodes the <code className="font-mono">sogc_pub</code> / <code className="font-mono">zefix_raw</code> JSON blobs into
              structured <code className="font-mono">sogc_publications</code> and <code className="font-mono">sogc_changes</code> rows.
              Use <em>missing</em> mode for initial backfill, <em>all</em> to reprocess every company.
              Optionally restrict to specific companies by CHE UID.
            </p>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <Field label="Mode">
              <select name="sogc_mode" className={inputCls}>
                <option value="missing">missing — only unprocessed companies</option>
                <option value="all">all — reprocess every company</option>
                <option value="publications">publications — fix encoding + regenerate changes from existing rows</option>
              </select>
            </Field>
            <Field label="Batch size" hint="DB commit interval">
              <input name="sogc_batch_size" type="number" min="10" defaultValue="500" className={inputCls} />
            </Field>
          </div>
          <Field label="CHE UIDs (optional)" hint="Only applies to missing/all modes. One per line.">
            <textarea name="sogc_uids" rows={3} placeholder="CHE-123.456.789&#10;CHE-987.654.321" className={cn(inputCls, "font-mono text-xs")} />
          </Field>
          <SubmitBtn loading={loading === "collection/sogc-preprocess"} />
        </form>
      </Section>

      <Section title="Extract SOGC Persons &amp; Auditors">
        <form onSubmit={async e => {
          e.preventDefault();
          const fd = new FormData(e.currentTarget);
          await submit("scoring/extract-sogc-persons", {
            mode: fd.get("persons_mode") as string || "missing",
            batch_size: parseInt(fd.get("persons_batch_size") as string) || 1000,
          });
        }} className="space-y-4">
          <div>
            <h2 className="text-sm font-semibold text-slate-800">Extract persons &amp; auditors from SOGC changes</h2>
            <p className="mt-1 text-xs text-slate-500">
              Parses <code className="font-mono">sogc_changes</code> rows (role, director, auditor sections) into structured
              <code className="font-mono"> sogc_person_entities</code>, <code className="font-mono">sogc_person_appearances</code>,
              and <code className="font-mono">sogc_auditors</code> tables. Run after SOGC Preprocess.
              Use <em>missing</em> for incremental updates, <em>all</em> to rebuild from scratch.
            </p>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <Field label="Mode">
              <select name="persons_mode" className={inputCls}>
                <option value="missing">missing — only unprocessed changes</option>
                <option value="all">all — reprocess all SOGC changes</option>
              </select>
            </Field>
            <Field label="Batch size" hint="DB commit interval">
              <input name="persons_batch_size" type="number" min="100" defaultValue="1000" className={inputCls} />
            </Field>
          </div>
          <SubmitBtn loading={loading === "scoring/extract-sogc-persons"} />
        </form>
      </Section>

      <GroupHeader label="Text &amp; Keywords" />

      <Section title="Re-extract Zefix Raw Fields">
        <form onSubmit={async e => {
          e.preventDefault();
          const fd = new FormData(e.currentTarget);
          await submit("scoring/reextract-zefix-raw", {
            mode: fd.get("reextract_mode") === "on" ? "all" : "missing",
          });
        }} className="space-y-4">
          <div>
            <h2 className="text-sm font-semibold text-slate-800">Re-extract columns from zefix_raw</h2>
            <p className="mt-1 text-xs text-slate-500">
              Repopulates JSON blob columns (branch offices, old names, SOGC pub, etc.) from the
              cached <code>zefix_raw</code> blob — no API calls needed.
            </p>
          </div>
          <label className="flex items-center gap-2 text-sm text-slate-700">
            <input type="checkbox" name="reextract_mode" className={checkCls} />
            Overwrite all rows (default: only NULL rows)
          </label>
          <SubmitBtn loading={loading === "scoring/reextract-zefix-raw"} />
        </form>
      </Section>

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

      <Section title="Recompute Keywords">
        <form onSubmit={async e => {
          e.preventDefault();
          const fd = new FormData(e.currentTarget);
          await submit("scoring/recompute-keywords", {
            top_keywords_per_company: parseInt(fd.get("recompute_top_keywords") as string) || 10,
            canton: (fd.get("recompute_canton") as string)?.trim().toUpperCase() || null,
            limit: parseInt(fd.get("recompute_limit") as string) || null,
          });
        }} className="space-y-4">
          <div>
            <h2 className="text-sm font-semibold text-slate-800">Recompute keywords from purpose text</h2>
            <p className="mt-1 text-xs text-slate-500">
              Refresh purpose_keywords from the current corpus. This is the recommended prerequisite before Semantic K-Means.
            </p>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <Field label="Top keywords/company">
              <input name="recompute_top_keywords" type="number" min="1" defaultValue="10" className={inputCls} />
            </Field>
            <Field label="Limit">
              <input name="recompute_limit" type="number" min="1" className={inputCls} placeholder="All" />
            </Field>
          </div>
          <Field label="Canton" hint="Optional: restrict recomputation to one canton">
            <input name="recompute_canton" className={inputCls} placeholder="Any" />
          </Field>
          <SubmitBtn loading={loading === "scoring/recompute-keywords"} />
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

      <GroupHeader label="NOGA Classification" />

      <Section title="NOGA Classification">
        <div className="space-y-6">

          {/* Step 1 — Build embeddings */}
          <div className="rounded-lg border border-slate-200 p-4 space-y-3">
            <div>
              <div className="flex items-center gap-2">
                <span className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-blue-600 text-white text-[10px] font-bold">1</span>
                <h3 className="text-sm font-semibold text-slate-800">Build NOGA Embeddings</h3>
              </div>
              <p className="mt-1 text-xs text-slate-500 ml-7">
                Embed all NOGA level-5 categories in DE/FR/IT/EN and store in pgvector. One-time setup — re-run only when the NOGA taxonomy changes.
              </p>
            </div>
            <form onSubmit={async e => {
              e.preventDefault();
              const fd = new FormData(e.currentTarget);
              await submit("scoring/build-noga-embeddings", {
                batch_size: parseInt(fd.get("batch_size") as string) || 256,
              });
            }} className="space-y-3 ml-7">
              <Field label="Batch size" hint="Texts embedded per forward pass. Lower = less RAM.">
                <input name="batch_size" type="number" min="32" max="512" defaultValue="256" className={inputCls} />
              </Field>
              <SubmitBtn loading={loading === "scoring/build-noga-embeddings"} />
            </form>
          </div>

          {/* Step 2 — Detect language */}
          <div className="rounded-lg border border-slate-200 p-4 space-y-3">
            <div>
              <div className="flex items-center gap-2">
                <span className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-blue-600 text-white text-[10px] font-bold">2</span>
                <h3 className="text-sm font-semibold text-slate-800">Detect Purpose Language</h3>
              </div>
              <p className="mt-1 text-xs text-slate-500 ml-7">
                Detect DE/FR/IT/EN for each company purpose and store in <code className="text-xs bg-slate-100 px-1 rounded">purpose_language</code>. Used to match companies to same-language NOGA embeddings.
              </p>
            </div>
            <form onSubmit={async e => {
              e.preventDefault();
              const fd = new FormData(e.currentTarget);
              await submit("scoring/detect-language", {
                only_missing: fd.get("only_missing") === "on",
              });
            }} className="space-y-3 ml-7">
              <label className="flex items-center gap-2 text-sm text-slate-700">
                <input type="checkbox" name="only_missing" defaultChecked className={checkCls} />
                Only companies without a detected language
              </label>
              <SubmitBtn loading={loading === "scoring/detect-language"} />
            </form>
          </div>

          {/* Step 3 — Classify */}
          <div className="rounded-lg border border-slate-200 p-4 space-y-3">
            <div>
              <div className="flex items-center gap-2">
                <span className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-blue-600 text-white text-[10px] font-bold">3</span>
                <h3 className="text-sm font-semibold text-slate-800">Reclassify NOGA</h3>
              </div>
              <p className="mt-1 text-xs text-slate-500 ml-7">
                Classify companies using hybrid pgvector embedding + token matching. Stores <code className="text-xs bg-slate-100 px-1 rounded">noga_code</code>, <code className="text-xs bg-slate-100 px-1 rounded">noga_confidence</code>, and full ancestry path.
              </p>
            </div>
            <form onSubmit={async e => {
              e.preventDefault();
              const fd = new FormData(e.currentTarget);
              await submit("scoring/reclassify-noga", {
                only_missing_noga: fd.get("only_missing_noga") === "on",
                only_detailed_raw: fd.get("only_detailed_raw") === "on",
                embed_mode: fd.get("embed_mode") as string,
              });
            }} className="space-y-3 ml-7">
              <div className="flex gap-6 flex-wrap">
                <label className="flex items-center gap-2 text-sm text-slate-700">
                  <input type="checkbox" name="only_missing_noga" className={checkCls} />
                  Only companies missing NOGA
                </label>
                <label className="flex items-center gap-2 text-sm text-slate-700">
                  <input type="checkbox" name="only_detailed_raw" defaultChecked className={checkCls} />
                  Only with detailed Zefix raw data
                </label>
              </div>
              <div className="space-y-1">
                <p className="text-xs font-medium text-slate-600">Purpose embedding</p>
                <div className="flex flex-col gap-1.5">
                  <label className="flex items-center gap-2 text-sm text-slate-700">
                    <input type="radio" name="embed_mode" value="clean" defaultChecked className={checkCls} />
                    Update <code className="text-xs bg-slate-100 px-1 rounded">purpose_clean</code> (default — boilerplate-stripped)
                  </label>
                  <label className="flex items-center gap-2 text-sm text-slate-700">
                    <input type="radio" name="embed_mode" value="full_and_clean" className={checkCls} />
                    Update <code className="text-xs bg-slate-100 px-1 rounded">purpose_clean</code> + <code className="text-xs bg-slate-100 px-1 rounded">purpose_full</code> (raw)
                  </label>
                  <label className="flex items-center gap-2 text-sm text-slate-700">
                    <input type="radio" name="embed_mode" value="none" className={checkCls} />
                    Skip embedding update
                  </label>
                </div>
              </div>
              <SubmitBtn loading={loading === "scoring/reclassify-noga"} />
            </form>
          </div>

          {/* Step 4 — Re-run low confidence */}
          <div className="rounded-lg border border-amber-200 bg-amber-50/40 p-4 space-y-3">
            <div>
              <div className="flex items-center gap-2">
                <span className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-amber-500 text-white text-[10px] font-bold">4</span>
                <h3 className="text-sm font-semibold text-slate-800">Re-run Low Confidence</h3>
              </div>
              <p className="mt-1 text-xs text-slate-500 ml-7">
                Re-classify companies whose <code className="text-xs bg-slate-100 px-1 rounded">noga_confidence</code> is below the threshold. These are candidates for API-based refinement.
              </p>
            </div>
            <form onSubmit={async e => {
              e.preventDefault();
              const fd = new FormData(e.currentTarget);
              await submit("scoring/reclassify-low-conf-noga", {
                confidence_threshold: parseFloat(fd.get("confidence_threshold") as string) || 0.80,
              });
            }} className="space-y-3 ml-7">
              <Field label="Confidence threshold" hint="Companies below this score are re-classified.">
                <input name="confidence_threshold" type="number" min="0" max="1" step="0.05" defaultValue="0.80" className={inputCls} />
              </Field>
              <SubmitBtn loading={loading === "scoring/reclassify-low-conf-noga"} />
            </form>
          </div>

        </div>
      </Section>

      <GroupHeader label="Purpose Embeddings" />

      <Section title="Embed Purpose Text">
        <div className="space-y-6">

          {/* embed_purpose_full */}
          <div className="rounded-lg border border-slate-200 p-4 space-y-3">
            <div>
              <h3 className="text-sm font-semibold text-slate-800">Embed Purpose (Full)</h3>
              <p className="mt-1 text-xs text-slate-500">
                Embeds raw company purpose text using the multilingual sentence-transformer and stores vectors in <code className="text-xs bg-slate-100 px-1 rounded">purpose_embedding</code>. Required for semantic search.
              </p>
            </div>
            <form onSubmit={async e => {
              e.preventDefault();
              const fd = new FormData(e.currentTarget);
              await submit("scoring/embed-purpose-full", {
                only_missing: fd.get("only_missing_full") === "on",
              });
            }} className="space-y-3">
              <label className="flex items-center gap-2 text-sm text-slate-700">
                <input type="checkbox" name="only_missing_full" defaultChecked className={checkCls} />
                Only companies missing embedding
              </label>
              <SubmitBtn loading={loading === "scoring/embed-purpose-full"} />
            </form>
          </div>

          {/* embed_purpose_clean */}
          <div className="rounded-lg border border-slate-200 p-4 space-y-3">
            <div>
              <h3 className="text-sm font-semibold text-slate-800">Embed Purpose (Clean)</h3>
              <p className="mt-1 text-xs text-slate-500">
                Embeds boilerplate-stripped, keyword-enriched purpose text and stores vectors in <code className="text-xs bg-slate-100 px-1 rounded">purpose_embedding_clean</code>. Higher quality than Full for clustering. Requires <code className="text-xs bg-slate-100 px-1 rounded">purpose_keywords</code>.
              </p>
            </div>
            <form onSubmit={async e => {
              e.preventDefault();
              const fd = new FormData(e.currentTarget);
              await submit("scoring/embed-purpose-clean", {
                only_missing: fd.get("only_missing_clean") === "on",
              });
            }} className="space-y-3">
              <label className="flex items-center gap-2 text-sm text-slate-700">
                <input type="checkbox" name="only_missing_clean" defaultChecked className={checkCls} />
                Only companies missing embedding
              </label>
              <SubmitBtn loading={loading === "scoring/embed-purpose-clean"} />
            </form>
          </div>

        </div>
      </Section>

      <GroupHeader label="Clustering" />

      <Section title="Semantic K-Means clustering">
        <form onSubmit={async e => {
          e.preventDefault();
          const fd = new FormData(e.currentTarget);
          await submit("scoring/semantic-cluster", {
            n_clusters: parseInt(fd.get("semantic_n_clusters") as string) || 150,
            max_clusters_per_company: parseInt(fd.get("semantic_max_clusters_per_company") as string) || 3,
            min_similarity: parseFloat(fd.get("semantic_min_similarity") as string) || 0.2,
            n_components: parseInt(fd.get("semantic_n_components") as string) || 50,
            top_terms: parseInt(fd.get("semantic_top_terms") as string) || 5,
            canton: (fd.get("semantic_canton") as string)?.trim().toUpperCase() || null,
            min_zefix_score: parseInt(fd.get("semantic_min_zefix_score") as string) || null,
            max_zefix_score: parseInt(fd.get("semantic_max_zefix_score") as string) || null,
            limit: parseInt(fd.get("semantic_limit") as string) || null,
            embedding_batch_size: parseInt(fd.get("semantic_embedding_batch_size") as string) || 512,
          });
        }} className="space-y-4">
          <div>
            <h2 className="text-sm font-semibold text-slate-800">Semantic K-Means clustering</h2>
            <p className="mt-1 text-xs text-slate-500">
              Clusters companies by meaning using multilingual embeddings. Run Recompute Keywords first so the model has clean inputs.
            </p>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <Field label="Clusters">
              <input name="semantic_n_clusters" type="number" min="1" defaultValue="150" className={inputCls} />
            </Field>
            <Field label="Max clusters/company">
              <input name="semantic_max_clusters_per_company" type="number" min="1" defaultValue="3" className={inputCls} />
            </Field>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <Field label="Min similarity">
              <input name="semantic_min_similarity" type="number" min="0" max="1" step="0.01" defaultValue="0.2" className={inputCls} />
            </Field>
            <Field label="Components">
              <input name="semantic_n_components" type="number" min="2" defaultValue="50" className={inputCls} />
            </Field>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <Field label="Top cluster terms">
              <input name="semantic_top_terms" type="number" min="1" defaultValue="5" className={inputCls} />
            </Field>
            <Field label="Embedding batch size">
              <input name="semantic_embedding_batch_size" type="number" min="1" defaultValue="512" className={inputCls} />
            </Field>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <Field label="Canton">
              <input name="semantic_canton" className={inputCls} placeholder="Any" />
            </Field>
            <Field label="Limit">
              <input name="semantic_limit" type="number" min="1" className={inputCls} placeholder="All" />
            </Field>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <Field label="Min Zefix score">
              <input name="semantic_min_zefix_score" type="number" min="0" max="100" className={inputCls} placeholder="—" />
            </Field>
            <Field label="Max Zefix score">
              <input name="semantic_max_zefix_score" type="number" min="0" max="100" className={inputCls} placeholder="—" />
            </Field>
          </div>
          <SubmitBtn loading={loading === "scoring/semantic-cluster"} />
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
    </div>
  );
}

function cn(...classes: (string | undefined | false)[]) {
  return classes.filter(Boolean).join(" ");
}
