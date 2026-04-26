"""K-Means multi-label clustering pipeline for zefix_analyzer.

Pipeline steps:
  1. Load companies from DB (with optional filters)
  2. Lemmatize purpose text with spaCy de_core_news_md
  3. TF-IDF vectorization
  4. Dimensionality reduction (TruncatedSVD + L2 normalize)
  5. K-Means clustering (MiniBatchKMeans for speed)
  6. Cluster labeling via c-TF-IDF with bigram deduplication
  7. Multi-label soft assignment: each company gets up to N clusters
     by cosine similarity to centroids; below threshold → "Undefined"
  8. Per-company keyword extraction from each document's own TF-IDF row
  9. Write tfidf_cluster (pipe-separated cluster labels) and
     purpose_keywords (comma-separated per-doc keywords) to DB

Standalone helper:
  analyze_cross_cluster_terms() — finds terms appearing across many
  cluster labels (stopword candidates), writes a .txt file.

All tunable parameters live in PipelineConfig at the top of this file.
"""

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────

@dataclass
class PipelineConfig:
    # ── spaCy preprocessing ──
    spacy_model: str = "de_core_news_md"
    spacy_batch_size: int = 500
    min_token_length: int = 3          # tokens with len <= this are dropped

    # ── TF-IDF ──
    ngram_range: tuple[int, int] = (1, 2)
    min_df: int = 5
    max_df: float = 0.4
    max_features: int = 15000

    # ── Dimensionality reduction ──
    n_components: int = 50
    svd_random_state: int = 42

    # ── K-Means ──
    n_clusters: int = 50
    kmeans_random_state: int = 42
    kmeans_max_iter: int = 300
    kmeans_n_init: int = 3

    # ── Multi-label assignment ──
    max_clusters_per_company: int = 3   # assign up to this many clusters per company
    min_similarity: float = 0.20        # cosine similarity threshold; below → Undefined
    label_dedup_threshold: float = 0.6  # Jaccard overlap above this → skip duplicate label

    # ── Labeling ──
    top_terms_per_cluster: int = 5      # terms in each cluster label
    top_keywords_per_company: int = 10  # stored in purpose_keywords
    min_keyword_score: float = 0.01     # TF-IDF score floor; terms below this are dropped
    bigram_penalty: float = 0.85        # score multiplier for bigrams before ranking;
                                        # <1.0 lets unigrams compete against inflated bigram IDF

    # ── Cross-cluster analysis ──
    analysis_top_clusters: int = 20
    analysis_top_terms: int = 10

    # ── DB write ──
    db_batch_size: int = 200

    # ── Cluster quality filter (Phase 2a) ──
    # Clusters whose mean IDF of top terms falls below this threshold are considered
    # generic/boilerplate (e.g. "gesellschaft verwaltung holding") and suppressed.
    # Companies assigned only to low-quality clusters get tfidf_cluster = None.
    min_cluster_specificity: float = 0.3

    # ── Extra stopwords merged with DB tfidf_stopwords ──
    extra_stopwords: list[str] = field(default_factory=lambda: [
        "gesellschaft", "zweck", "unternehmen", "dienstleistungen", "kunden",
        "erbringt", "betreibt", "sowie", "alle", "art", "insbesondere",
        "tätigkeiten", "erwerb", "verwaltung", "beteiligung", "holding",
        "bezweckt", "zwecke", "tätig", "firma",
    ])


# ── Boilerplate + stopword helpers ────────────────────────────────────────────

def _load_boilerplate_patterns():
    """Return active boilerplate regex patterns from DB (empty list if unavailable)."""
    try:
        from app import crud
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            return crud.get_active_boilerplate_patterns(db)
        finally:
            db.close()
    except Exception:
        return []


def strip_boilerplate(texts: list[str]) -> list[str]:
    """Strip boilerplate sentences from each purpose text using DB patterns.

    Applied before TF-IDF vectorization so generic legal boilerplate doesn't
    inflate IDF of terms like 'gesellschaft', 'bezweckt', 'insbesondere'.
    """
    from app.services.noga import _strip_purpose_boilerplate
    patterns = _load_boilerplate_patterns()
    if not patterns:
        return texts
    return [_strip_purpose_boilerplate(t, patterns) for t in texts]


def get_stopwords(cfg: PipelineConfig) -> set[str]:
    """Return the stopword set from DB (active tfidf_stopwords rows)."""
    custom: set[str] = set()
    try:
        from app import crud
        from app.database import SessionLocal

        db = SessionLocal()
        try:
            custom = crud.get_active_tfidf_stopwords(db)
        finally:
            db.close()
    except Exception:
        custom = set()

    return custom | set(cfg.extra_stopwords)


# ── Step 1: Text Preprocessing ────────────────────────────────────────────────

def preprocess_texts(
    texts: list[str],
    cfg: PipelineConfig,
    progress_cb: Callable[[int, int], None] | None = None,
) -> list[str]:
    """Lemmatize texts with spaCy German model.

    Removes punctuation, spaces, tokens shorter than min_token_length, and
    any token whose lowercase lemma is in the stopword set.
    Returns one cleaned string per input text.
    """
    try:
        import spacy
        from tqdm import tqdm
    except ImportError as exc:
        raise ImportError(
            "spacy and tqdm are required. Run: pip install spacy tqdm && "
            "python -m spacy download de_core_news_md"
        ) from exc

    nlp = spacy.load(cfg.spacy_model, disable=["ner", "parser"])
    stopwords = get_stopwords(cfg)
    cleaned: list[str] = []
    total = len(texts)

    docs = nlp.pipe(texts, batch_size=cfg.spacy_batch_size)
    for i, doc in enumerate(tqdm(docs, total=total, desc="Lemmatizing", unit="doc"), start=1):
        tokens = [
            tok.lemma_.lower()
            for tok in doc
            if not tok.is_punct
            and not tok.is_space
            and len(tok.text) > cfg.min_token_length
            and tok.lemma_.lower() not in stopwords
        ]
        cleaned.append(" ".join(tokens))
        if progress_cb and i % cfg.spacy_batch_size == 0:
            progress_cb(i, total)

    if progress_cb:
        progress_cb(total, total)
    return cleaned


# ── Step 2: TF-IDF Vectorization ──────────────────────────────────────────────

def vectorize(texts: list[str], cfg: PipelineConfig):
    """Fit TfidfVectorizer and return (vectorizer, sparse matrix X)."""
    from sklearn.feature_extraction.text import TfidfVectorizer

    vec = TfidfVectorizer(
        ngram_range=cfg.ngram_range,
        min_df=cfg.min_df,
        max_df=cfg.max_df,
        max_features=cfg.max_features,
    )
    X = vec.fit_transform(texts)
    return vec, X


# ── Step 3: Dimensionality Reduction ──────────────────────────────────────────

def reduce_dimensions(X, cfg: PipelineConfig):
    """TruncatedSVD + L2 normalisation (euclidean distance ≈ cosine similarity).

    Returns (svd, X_reduced) so the fitted SVD can be persisted for incremental use.
    """
    from sklearn.decomposition import TruncatedSVD
    from sklearn.preprocessing import normalize

    n = min(cfg.n_components, X.shape[1] - 1)
    svd = TruncatedSVD(n_components=n, random_state=cfg.svd_random_state)
    X_svd = svd.fit_transform(X)
    return svd, normalize(X_svd)


# ── Step 4: K-Means Clustering ────────────────────────────────────────────────

def cluster_kmeans(X_reduced, cfg: PipelineConfig):
    """Fit MiniBatchKMeans and return the fitted model."""
    from sklearn.cluster import MiniBatchKMeans

    km = MiniBatchKMeans(
        n_clusters=min(cfg.n_clusters, X_reduced.shape[0]),
        random_state=cfg.kmeans_random_state,
        max_iter=cfg.kmeans_max_iter,
        n_init=cfg.kmeans_n_init,
    )
    km.fit(X_reduced)
    return km


# ── Step 5: Multi-label soft assignment ───────────────────────────────────────

def assign_multi_label(X_reduced, km, cfg: PipelineConfig) -> list[list[int]]:
    """Assign each company up to max_clusters_per_company clusters.

    Computes cosine similarity between each company vector and all cluster
    centroids (both are already L2-normalised so dot product = cosine sim).
    Assigns the top-N clusters whose similarity >= min_similarity.
    Returns an empty list for companies that don't meet the threshold on any
    cluster (they will be labelled "Undefined").
    """
    import numpy as np
    from sklearn.preprocessing import normalize

    centers_norm = normalize(km.cluster_centers_)
    sim_matrix = X_reduced @ centers_norm.T          # (n_companies, n_clusters)

    k = min(cfg.max_clusters_per_company, sim_matrix.shape[1])
    # Vectorised top-k: argpartition is O(n_clusters) vs argsort O(n_clusters log n_clusters)
    top_k_unsorted = np.argpartition(sim_matrix, -k, axis=1)[:, -k:]           # (n, k)
    top_k_sims = sim_matrix[np.arange(sim_matrix.shape[0])[:, None], top_k_unsorted]
    order = np.argsort(top_k_sims, axis=1)[:, ::-1]                            # sort only k cols
    sorted_idx = top_k_unsorted[np.arange(top_k_unsorted.shape[0])[:, None], order]
    sorted_sims = top_k_sims[np.arange(top_k_sims.shape[0])[:, None], order]

    threshold = cfg.min_similarity
    assignments: list[list[int]] = [
        [int(sorted_idx[i, j]) for j in range(k) if sorted_sims[i, j] >= threshold]
        for i in range(sim_matrix.shape[0])
    ]
    return assignments


# ── Step 6: Cluster Labeling ──────────────────────────────────────────────────

def label_clusters(
    hard_labels,        # km.labels_ — hard assignment used only for c-TF-IDF
    X_tfidf,
    feature_names,
    n_clusters: int,
    cfg: PipelineConfig,
) -> dict[int, str]:
    """Label each cluster using c-TF-IDF with bigram deduplication.

    c-TF-IDF scores terms by how frequent they are *within* a cluster relative
    to how many other clusters also contain them — cluster-specific terms rank
    above generic ones like "handel" or "dienstleistung".

    Returns {cluster_id: "term1,term2,...,termN"}.
    """
    import numpy as np
    import scipy.sparse as sp

    _n_features = X_tfidf.shape[1]
    n_docs = len(hard_labels)

    # Build per-cluster term-sum matrix via sparse one-hot multiply —
    # one matmul instead of 150 sparse boolean-mask slices
    one_hot = sp.csr_matrix(
        (np.ones(n_docs, dtype=np.float32), (hard_labels, np.arange(n_docs))),
        shape=(n_clusters, n_docs),
    )
    cluster_term_sum = np.asarray((one_hot @ X_tfidf).todense())

    # c-IDF: penalise terms present in many clusters
    term_presence = (cluster_term_sum > 0).sum(axis=0)
    c_idf = np.log(n_clusters / (term_presence + 1) + 1)

    # c-TF: normalise each cluster's total weight to 1
    totals = cluster_term_sum.sum(axis=1, keepdims=True)
    totals = np.where(totals == 0, 1, totals)
    c_tf = cluster_term_sum / totals

    c_tfidf = c_tf * c_idf  # (n_clusters, n_features)

    # Select top terms with bigram deduplication
    labels_map: dict[int, str] = {}
    candidates = cfg.top_terms_per_cluster * 4

    for cid in range(n_clusters):
        ranked_idx = c_tfidf[cid].argsort()[::-1][:candidates]
        selected: list[str] = []
        covered: set[str] = set()

        for j in ranked_idx:
            if len(selected) == cfg.top_terms_per_cluster:
                break
            term = feature_names[j]
            words = set(term.split())
            if words.issubset(covered):
                continue
            selected.append(term)
            covered.update(words)

        labels_map[cid] = ",".join(selected) if selected else f"cluster_{cid}"

    return labels_map


# ── Step 6a: Cluster quality filter (Phase 2a) ────────────────────────────────

def score_cluster_specificity(
    labels_map: dict[int, str],
    vectorizer,
) -> dict[int, float]:
    """Return {cluster_id: mean_idf_of_top_terms} using the fitted TF-IDF vectorizer.

    High IDF = rare across corpus = specific/domain term.
    Low IDF  = common across corpus = generic/boilerplate term.
    """
    import numpy as np

    idf_values: dict[str, float] = dict(
        zip(vectorizer.get_feature_names_out(), vectorizer.idf_)
    )
    scores: dict[int, float] = {}
    for cid, label in labels_map.items():
        terms = [t.strip() for t in label.split(",") if t.strip()]
        if not terms:
            scores[cid] = 0.0
            continue
        idfs = [idf_values.get(t, 0.0) for t in terms]
        scores[cid] = float(np.mean(idfs))

    # Normalise to [0, 1] so threshold is scale-independent
    if scores:
        max_score = max(scores.values()) or 1.0
        scores = {cid: s / max_score for cid, s in scores.items()}

    return scores


# ── Step 6b: Per-document keyword extraction ──────────────────────────────────

def extract_company_keywords(
    X_tfidf,
    feature_names,
    cfg: PipelineConfig,
    progress_cb: Callable[[int, int], None] | None = None,
) -> list[str | None]:
    """Extract top-N TF-IDF keywords from each company's own purpose text.

    Uses the same bigram deduplication as label_clusters so results are clean.
    Returns one string (or None) per row in X_tfidf.

    Works directly on the CSR sparse structure to avoid O(n * n_features) dense
    allocations — only non-zero elements per row are sorted.
    """
    import numpy as np

    X_csr = X_tfidf.tocsr()
    results: list[str | None] = []
    candidates = cfg.top_keywords_per_company * 4
    n_keep = cfg.top_keywords_per_company
    min_score = cfg.min_keyword_score
    bigram_penalty = cfg.bigram_penalty
    total = X_csr.shape[0]

    for i in range(total):
        start, end = int(X_csr.indptr[i]), int(X_csr.indptr[i + 1])
        if start == end:
            results.append(None)
        else:
            col_indices = X_csr.indices[start:end]   # non-zero feature indices
            values = X_csr.data[start:end].copy()     # corresponding TF-IDF values

            # Apply bigram penalty before ranking so unigrams compete fairly
            # (bigrams get inflated IDF because they're rare, but unigrams are
            # often more meaningful in German where compounds are single tokens)
            for k, ci in enumerate(col_indices):
                if " " in feature_names[ci]:
                    values[k] *= bigram_penalty

            # Sort only the non-zero elements (typically ~50–200 vs 15 000 dense)
            order = np.argsort(values)[::-1][:candidates]
            selected: list[str] = []
            covered: set[str] = set()

            for j in order:
                if len(selected) == n_keep:
                    break
                if values[j] < min_score:
                    break  # remaining scores are even lower (sorted desc)
                term = feature_names[col_indices[j]]
                words = set(term.split())
                if words.issubset(covered):
                    continue
                selected.append(term)
                covered.update(words)

            results.append(",".join(selected) if selected else None)

        if progress_cb and i % 5000 == 0:
            progress_cb(i, total)

    if progress_cb:
        progress_cb(total, total)
    return results


# ── Step 7: Save Results to DB ────────────────────────────────────────────────

def save_results(
    db,
    companies: list,
    assignments: list[list[int]],
    labels_map: dict[int, str],
    company_keywords: list[str | None],
    cfg: PipelineConfig,
    progress_cb: Callable[[int, int, dict], None] | None = None,
    cluster_specificity: dict[int, float] | None = None,
) -> dict[str, Any]:
    """Write tfidf_cluster (pipe-separated cluster labels) and purpose_keywords to DB.

    tfidf_cluster format: "label_a|label_b|label_c" where each label is the
    comma-separated c-TF-IDF terms for that cluster.
    Companies with no cluster above the similarity threshold get tfidf_cluster=None.
    Companies assigned only to low-quality clusters (specificity < threshold) also
    get tfidf_cluster=None (Phase 2a).

    Uses bulk_update_mappings for a single commit instead of one commit per
    batch of 200 ORM objects, avoiding SQLAlchemy change-tracking overhead.
    """
    from app.models.company import Company

    stats: dict[str, Any] = {"classified": 0, "undefined": 0, "low_quality": 0, "skipped": 0, "errors": []}
    total = len(companies)
    mappings: list[dict] = []
    specificity_threshold = cfg.min_cluster_specificity

    for idx, (company, cluster_ids, kw) in enumerate(zip(companies, assignments, company_keywords)):
        try:
            if not cluster_ids:
                tfidf_cluster = None
                stats["undefined"] += 1
            else:
                parts: list[str] = []
                covered_terms: set[str] = set()
                threshold = cfg.label_dedup_threshold
                for cid in cluster_ids:
                    if cid not in labels_map:
                        continue
                    # Phase 2a: skip low-quality clusters
                    if cluster_specificity and cluster_specificity.get(cid, 1.0) < specificity_threshold:
                        continue
                    label = labels_map[cid]
                    label_terms = {t.strip() for t in label.split(",")}
                    if covered_terms:
                        overlap = len(label_terms & covered_terms) / len(label_terms)
                        if overlap >= threshold:
                            continue
                    parts.append(label)
                    covered_terms |= label_terms
                if parts:
                    tfidf_cluster = "|".join(parts)
                    stats["classified"] += 1
                else:
                    tfidf_cluster = None
                    stats["low_quality"] += 1
            kw_arr = [k.strip() for k in kw.split(",") if k.strip()] if kw else None
            mappings.append({
                "id": company.id,
                "tfidf_cluster": tfidf_cluster,
                "purpose_keywords": kw,
                "purpose_keywords_arr": kw_arr,
            })
        except Exception as exc:  # noqa: BLE001
            stats["errors"].append(f"{company.uid}: {exc}")
            stats["skipped"] += 1

        if len(mappings) >= cfg.db_batch_size:
            db.bulk_update_mappings(Company, mappings)
            db.commit()
            mappings.clear()
            if progress_cb:
                progress_cb(min(idx + 1, total), total, stats)

    if mappings:
        db.bulk_update_mappings(Company, mappings)
        db.commit()
        if progress_cb:
            progress_cb(total, total, stats)

    return stats


# ── Cross-Cluster Term Analysis ───────────────────────────────────────────────

def analyze_cross_cluster_terms(
    db,
    cfg: PipelineConfig | None = None,
    output_path: Path | None = None,
) -> Path:
    """Find terms appearing across many cluster labels (stopword candidates).

    Reads tfidf_cluster from DB, splits on '|' to get individual cluster labels,
    then on ',' for terms, and counts cross-cluster term frequency.
    Writes a tab-separated .txt file. Returns the output path.
    """
    from collections import Counter
    from sqlalchemy import func
    from app.models.company import Company

    if cfg is None:
        cfg = PipelineConfig()
    if output_path is None:
        output_path = Path(__file__).parent.parent / "static" / "cluster_analysis.txt"

    rows = (
        db.query(Company.tfidf_cluster, func.count(Company.id).label("cnt"))
        .filter(Company.tfidf_cluster.isnot(None))
        .filter(Company.tfidf_cluster != "Undefined")
        .group_by(Company.tfidf_cluster)
        .order_by(func.count(Company.id).desc())
        .all()
    )

    # Each row's tfidf_cluster is "label_a|label_b|..." — collect unique cluster labels
    all_labels: list[str] = []
    seen: set[str] = set()
    for full_value, _ in rows:
        for label in full_value.split("|"):
            label = label.strip()
            if label and label not in seen:
                seen.add(label)
                all_labels.append(label)

    top_labels = all_labels[: cfg.analysis_top_clusters]
    term_counter: Counter = Counter()
    for label in top_labels:
        for term in label.split(",")[: cfg.analysis_top_terms]:
            term = term.strip()
            if term:
                term_counter[term] += 1

    lines = [
        "# Cross-cluster term frequency analysis",
        f"# Top {cfg.analysis_top_clusters} unique cluster labels, top {cfg.analysis_top_terms} terms each",
        "# Terms appearing in many clusters are candidates to add to the stopword list",
        "# ---------------------------------------------------------------",
        "# term\tclusters_containing_term",
    ]
    for term, count in term_counter.most_common():
        lines.append(f"{term}\t{count}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"Cross-cluster analysis written to {output_path}")
    return output_path


# ── Keyword-only recompute ────────────────────────────────────────────────────

def recompute_keywords(
    db,
    cfg: PipelineConfig | None = None,
    *,
    canton: str | None = None,
    limit: int | None = None,
    progress_cb: Callable[[int, int, dict], None] | None = None,
) -> dict[str, Any]:
    """Recompute purpose_keywords without re-clustering.

    Runs only: load → lemmatize → TF-IDF → extract keywords → save.
    tfidf_cluster is left untouched.

    Returns a stats dict with keys: updated, skipped, errors.
    """
    from app.models.company import Company

    if cfg is None:
        cfg = PipelineConfig()

    stats: dict[str, Any] = {"updated": 0, "skipped": 0, "errors": []}
    t_total = time.time()

    # ── Load ──
    t0 = time.time()
    q = (
        db.query(Company.id, Company.uid, Company.purpose)
        .filter(Company.purpose.isnot(None))
    )
    if canton:
        q = q.filter(Company.canton == canton.upper())

    q = q.order_by(Company.id.asc())
    if limit:
        q = q.limit(limit)
    companies = q.all()
    logger.info(f"[1/3] Loaded {len(companies)} companies in {time.time()-t0:.1f}s")
    if not companies:
        return stats

    # ── Lemmatize ──
    t1 = time.time()

    def _prep_cb(done: int, total: int) -> None:
        if progress_cb:
            progress_cb(done, total, {**stats, "step": "lemmatizing"})

    cleaned = preprocess_texts([c.purpose or "" for c in companies], cfg, progress_cb=_prep_cb)
    logger.info(f"[2/3] Lemmatization done in {time.time()-t1:.1f}s")

    # ── TF-IDF + keyword extraction ──
    t2 = time.time()
    if progress_cb:
        progress_cb(0, len(companies), {**stats, "step": "keywords"})
    cleaned = strip_boilerplate(cleaned)
    vectorizer, X_tfidf = vectorize(cleaned, cfg)
    feature_names = vectorizer.get_feature_names_out()

    def _kw_cb(done: int, total: int) -> None:
        if progress_cb:
            progress_cb(done, total, {**stats, "step": "keywords"})

    company_keywords = extract_company_keywords(X_tfidf, feature_names, cfg, progress_cb=_kw_cb)
    logger.info(f"[3/3] Keywords extracted in {time.time()-t2:.1f}s")

    # ── Save (purpose_keywords only) ──
    mappings: list[dict] = []
    for company, kw in zip(companies, company_keywords):
        try:
            kw_arr = [k.strip() for k in kw.split(",") if k.strip()] if kw else None
            mappings.append({"id": company.id, "purpose_keywords": kw, "purpose_keywords_arr": kw_arr})
            stats["updated"] += 1
        except Exception as exc:  # noqa: BLE001
            stats["errors"].append(f"{company.uid}: {exc}")
            stats["skipped"] += 1

        if len(mappings) >= cfg.db_batch_size:
            db.bulk_update_mappings(Company, mappings)
            db.commit()
            mappings.clear()
            if progress_cb:
                progress_cb(stats["updated"], len(companies), stats)

    if mappings:
        db.bulk_update_mappings(Company, mappings)
        db.commit()
        if progress_cb:
            progress_cb(stats["updated"], len(companies), stats)

    logger.info(f"Keyword recompute done in {time.time()-t_total:.1f}s — {stats['updated']} updated")
    return stats


# ── Boilerplate entropy mining ────────────────────────────────────────────────

def mine_boilerplate_entropy(
    db,
    *,
    c_tfidf_matrix=None,
    feature_names=None,
    labels_map: dict | None = None,
    vectorizer=None,
    n_companies: int = 0,
    doc_freq_threshold: float = 0.15,
    entropy_threshold: float = 0.85,
    label_freq_threshold: float = 0.30,
) -> int:
    """Compute cross-cluster term entropy and upsert high-entropy terms as boilerplate candidates.

    Three signals are combined into a single confidence score:
      - doc_frequency: fraction of companies containing this term (high = generic)
      - cluster_entropy: Shannon entropy across cluster c-TF-IDF weights (high = uniform = generic)
      - cluster_label_frequency: fraction of cluster labels that include this term (high = generic)

    Returns the number of candidate rows written to the DB.
    """
    import numpy as np
    from app import crud

    if feature_names is None or labels_map is None or vectorizer is None:
        logger.debug("mine_boilerplate_entropy: missing inputs, skipping")
        return 0

    feature_names = list(feature_names)
    n_features = len(feature_names)
    if n_features == 0:
        return 0

    # ── Signal 1: document frequency from vectorizer IDF ──────────────────────
    # IDF = log((1 + n) / (1 + df)) + 1 → invert: df ≈ n / exp(IDF - 1) - 1
    # Simpler: use sklearn's raw df via the inverse transform of IDF.
    # sklearn TfidfVectorizer stores idf_ = log((1+n)/(1+df)) + 1
    idf_ = np.array(vectorizer.idf_)
    if n_companies > 0:
        # df_smooth = (1+n) / exp(idf_ - 1) - 1  (inverse of sklearn formula)
        df_approx = (1.0 + n_companies) / np.exp(idf_ - 1.0) - 1.0
        doc_freq = np.clip(df_approx / max(n_companies, 1), 0.0, 1.0)
    else:
        doc_freq = np.zeros(n_features, dtype=float)

    # ── Signal 2: cross-cluster entropy ───────────────────────────────────────
    # Build a per-cluster × per-feature weight matrix from labels_map.
    # Each cluster label contains comma-separated top terms; treat weight = 1/rank.
    n_clusters = len(labels_map)
    cluster_term_weights = np.zeros((n_clusters, n_features), dtype=float)
    feat_idx = {f: i for i, f in enumerate(feature_names)}

    for rank_idx, (cid, label) in enumerate(labels_map.items()):
        terms = [t.strip() for t in label.split(",") if t.strip()]
        for term_rank, term in enumerate(terms):
            fi = feat_idx.get(term)
            if fi is not None:
                cluster_term_weights[rank_idx, fi] = 1.0 / (term_rank + 1)

    # Shannon entropy per feature across clusters (uniform distribution = high entropy)
    col_sums = cluster_term_weights.sum(axis=0)
    nonzero_mask = col_sums > 0
    entropy = np.zeros(n_features, dtype=float)
    if nonzero_mask.any():
        p = cluster_term_weights[:, nonzero_mask] / col_sums[nonzero_mask]
        p_safe = np.where(p > 0, p, 1e-12)
        raw_entropy = -(p_safe * np.log2(p_safe)).sum(axis=0)
        max_entropy = np.log2(max(n_clusters, 1))
        entropy[nonzero_mask] = raw_entropy / max_entropy if max_entropy > 0 else 0.0

    # ── Signal 3: cluster label frequency ─────────────────────────────────────
    term_in_label_count = np.zeros(n_features, dtype=float)
    for label in labels_map.values():
        for term in [t.strip() for t in label.split(",") if t.strip()]:
            fi = feat_idx.get(term)
            if fi is not None:
                term_in_label_count[fi] += 1
    label_freq = term_in_label_count / max(n_clusters, 1)

    # ── Combined confidence ────────────────────────────────────────────────────
    # Weight: 40% doc_freq, 40% entropy, 20% label_freq
    confidence = 0.40 * doc_freq + 0.40 * entropy + 0.20 * label_freq

    # ── Filter to candidates worth storing ────────────────────────────────────
    mask = (
        (doc_freq >= doc_freq_threshold) |
        (entropy >= entropy_threshold) |
        (label_freq >= label_freq_threshold)
    )
    candidate_indices = np.where(mask)[0]

    if len(candidate_indices) == 0:
        return 0

    candidates = []
    for fi in candidate_indices:
        term = feature_names[fi]
        # Skip very short or purely numeric terms
        if len(term) < 4 or term.isdigit():
            continue
        candidates.append({
            "term": term,
            "doc_frequency": float(doc_freq[fi]),
            "cluster_entropy": float(entropy[fi]),
            "cluster_label_frequency": float(label_freq[fi]),
            "confidence": float(confidence[fi]),
            "source_type": "corpus_mined",
            "language": None,
        })

    if not candidates:
        return 0

    written = crud.upsert_boilerplate_candidates(db, candidates)

    # Auto-promote high-confidence candidates
    crud.auto_promote_candidates(db, threshold=0.90)

    return written


# ── Incremental cluster assignment for new companies ──────────────────────────

def assign_new_companies_to_clusters(
    db,
    company_ids: list[int],
    cfg: PipelineConfig | None = None,
) -> dict[str, Any]:
    """Assign a batch of newly added companies to existing clusters.

    Uses the trained pipeline artifacts from S3 (vectorizer, SVD, K-Means).
    Companies that don't meet the similarity threshold get tfidf_cluster=None.
    Returns a stats dict.
    """
    from app.models.company import Company

    if cfg is None:
        cfg = PipelineConfig()

    stats: dict[str, Any] = {"assigned": 0, "undefined": 0, "skipped": 0, "errors": [], "missing_artifacts": False}

    artifacts = _load_pipeline_artifacts()
    if artifacts is None:
        logger.warning("assign_new_companies: no pipeline artifacts in S3; skipping")
        stats["missing_artifacts"] = True
        return stats

    vectorizer, svd, km, canonical_labels_map = artifacts

    companies = (
        db.query(Company)
        .filter(Company.id.in_(company_ids), Company.purpose.isnot(None))
        .all()
    )
    if not companies:
        return stats

    purposes = [c.purpose or "" for c in companies]
    cleaned = strip_boilerplate(purposes)

    # No spaCy: use the same vectorizer the pipeline was trained with
    try:
        X_tfidf = vectorizer.transform(cleaned)
    except Exception as exc:
        logger.error("Vectorizer transform failed for new companies: %s", exc)
        stats["errors"].append(str(exc))
        return stats

    from sklearn.preprocessing import normalize
    X_svd = normalize(svd.transform(X_tfidf))
    assignments = assign_multi_label(X_svd, km, cfg)
    feature_names = vectorizer.get_feature_names_out()
    company_keywords = extract_company_keywords(X_tfidf, feature_names, cfg)

    mappings = []
    for company, cluster_ids, kw in zip(companies, assignments, company_keywords):
        try:
            if not cluster_ids:
                tfidf_cluster = None
                stats["undefined"] += 1
            else:
                parts = [canonical_labels_map[cid] for cid in cluster_ids if cid in canonical_labels_map]
                tfidf_cluster = "|".join(parts) if parts else None
                if tfidf_cluster:
                    stats["assigned"] += 1
                else:
                    stats["undefined"] += 1
            kw_arr = [k.strip() for k in kw.split(",") if k.strip()] if kw else None
            mappings.append({
                "id": company.id,
                "tfidf_cluster": tfidf_cluster,
                "purpose_keywords": kw,
                "purpose_keywords_arr": kw_arr,
            })
        except Exception as exc:  # noqa: BLE001
            stats["errors"].append(f"{company.uid}: {exc}")
            stats["skipped"] += 1

    if mappings:
        db.bulk_update_mappings(Company, mappings)
        db.commit()

    return stats


# ── Main Pipeline ─────────────────────────────────────────────────────────────

def run_pipeline(
    db,
    cfg: PipelineConfig | None = None,
    *,
    canton: str | None = None,
    min_zefix_score: int | None = None,
    max_zefix_score: int | None = None,
    limit: int | None = None,
    use_keywords: bool = False,
    progress_cb: Callable[[int, int, dict], None] | None = None,
) -> dict[str, Any]:
    """Run the full K-Means multi-label clustering pipeline end-to-end.

    use_keywords: if True, cluster on each company's pre-extracted purpose_keywords
      (comma-separated TF-IDF terms already stored in DB) instead of the raw purpose
      text. This skips spaCy lemmatization and produces cleaner, more distinct clusters
      because boilerplate legal language is already filtered out. Falls back to raw
      purpose text for companies where purpose_keywords is NULL.

    Returns a stats dict with keys:
      classified, undefined, skipped, n_clusters, errors, summary, analysis_file
    """
    from app.models.company import Company

    if cfg is None:
        cfg = PipelineConfig()

    stats: dict[str, Any] = {
        "classified": 0, "undefined": 0, "skipped": 0,
        "n_clusters": 0, "errors": [], "summary": [],
    }
    t_total = time.time()

    # ── Load companies — fetch only the fields we need ──
    t0 = time.time()
    q = (
        db.query(Company.id, Company.uid, Company.purpose, Company.purpose_keywords)
        .filter(Company.purpose.isnot(None))
    )
    if canton:
        q = q.filter(Company.canton == canton.upper())

    if min_zefix_score is not None:
        q = q.filter(Company.zefix_score >= min_zefix_score)
    if max_zefix_score is not None:
        q = q.filter(Company.zefix_score <= max_zefix_score)
    q = q.order_by(Company.id.asc())
    if limit:
        q = q.limit(limit)
    companies = q.all()   # list of (id, uid, purpose, purpose_keywords) named tuples
    logger.info(f"[1/7] Loaded {len(companies)} companies in {time.time()-t0:.1f}s")
    if not companies:
        return stats

    # ── Step 1: Build input texts ──
    t1 = time.time()
    if use_keywords:
        # Use pre-extracted per-company TF-IDF keywords (comma-separated terms).
        # These are already lemmatized and domain-stopword-filtered, so no spaCy needed.
        # Companies missing purpose_keywords fall back to raw purpose text.
        n_keywords = sum(1 for c in companies if c.purpose_keywords)
        n_fallback = len(companies) - n_keywords
        logger.info(
            f"[2/7] use_keywords=True — {n_keywords} from keywords, "
            f"{n_fallback} falling back to purpose text"
        )
        cleaned = [
            c.purpose_keywords.replace(",", " ") if c.purpose_keywords else (c.purpose or "")
            for c in companies
        ]
        logger.info(f"[2/7] Input texts ready in {time.time()-t1:.1f}s (no lemmatization)")
    else:
        purposes = [c.purpose or "" for c in companies]

        def _prep_cb(done: int, total: int) -> None:
            if progress_cb:
                progress_cb(done, total, {**stats, "step": "lemmatizing"})

        cleaned = preprocess_texts(purposes, cfg, progress_cb=_prep_cb)
        logger.info(f"[2/7] Lemmatization done in {time.time()-t1:.1f}s")

    # ── Step 2: TF-IDF ──
    t2 = time.time()
    cleaned = strip_boilerplate(cleaned)
    vectorizer, X_tfidf = vectorize(cleaned, cfg)
    feature_names = vectorizer.get_feature_names_out()
    logger.info(f"[3/7] TF-IDF done in {time.time()-t2:.1f}s — shape: {X_tfidf.shape}")

    # ── Step 3: Dimensionality reduction ──
    t3 = time.time()
    svd, X_reduced = reduce_dimensions(X_tfidf, cfg)
    logger.info(f"[4/7] SVD done in {time.time()-t3:.1f}s — shape: {X_reduced.shape}")

    # ── Step 4: K-Means ──
    t4 = time.time()
    if progress_cb:
        progress_cb(0, len(companies), {**stats, "step": "clustering"})
    km = cluster_kmeans(X_reduced, cfg)
    actual_k = km.n_clusters
    stats["n_clusters"] = actual_k
    logger.info(f"[5/7] K-Means done in {time.time()-t4:.1f}s — {actual_k} clusters")

    # ── Step 5: Label clusters ──
    t5 = time.time()
    labels_map = label_clusters(km.labels_, X_tfidf, feature_names, actual_k, cfg)
    cluster_specificity = score_cluster_specificity(labels_map, vectorizer)
    n_low = sum(1 for s in cluster_specificity.values() if s < cfg.min_cluster_specificity)
    logger.info(f"[6/7] Labeling done in {time.time()-t5:.1f}s — {n_low}/{actual_k} low-quality clusters")

    # ── Step 5d: Registry matching — replace raw labels with stable canonical names ──
    from app.crud.cluster_registry import deactivate_missing_clusters, get_or_create_registry_entry
    canonical_labels_map: dict[int, str] = {}
    seen_canonical: set[str] = set()
    for cid, label in labels_map.items():
        entry = get_or_create_registry_entry(db, label)
        canonical_labels_map[cid] = entry.canonical_name
        seen_canonical.add(entry.canonical_name)
    deactivate_missing_clusters(db, seen_canonical)
    db.commit()
    logger.info(f"[6d/7] Registry sync done — {len(seen_canonical)} active canonical clusters")

    # ── Step 5b: Multi-label assignment ──
    t5b = time.time()
    if progress_cb:
        progress_cb(0, len(companies), {**stats, "step": "assigning"})
    assignments = assign_multi_label(X_reduced, km, cfg)
    logger.info(f"[6b/7] Assignment done in {time.time()-t5b:.1f}s")

    # ── Step 5c: Per-doc keyword extraction ──
    # Skipped when use_keywords=True: the TF-IDF matrix was built from the
    # already-extracted keywords, so re-extracting from it would be circular
    # and would overwrite purpose_keywords with degraded values.
    t5c = time.time()
    if use_keywords:
        company_keywords = [c.purpose_keywords for c in companies]
        logger.info("[6c/7] Keyword extraction skipped (use_keywords=True — keeping existing purpose_keywords)")
    else:
        def _kw_cb(done: int, total: int) -> None:
            if progress_cb:
                progress_cb(done, total, {**stats, "step": "keywords"})

        if progress_cb:
            progress_cb(0, len(companies), {**stats, "step": "keywords"})
        company_keywords = extract_company_keywords(X_tfidf, feature_names, cfg, progress_cb=_kw_cb)
        logger.info(f"[6c/7] Keywords done in {time.time()-t5c:.1f}s")

    # Summary: count how many companies reference each cluster label
    from collections import Counter
    label_counter: Counter = Counter()
    for cluster_ids in assignments:
        for cid in cluster_ids:
            label_counter[canonical_labels_map[cid]] += 1
    stats["summary"] = [
        {"label": label, "company_count": count}
        for label, count in label_counter.most_common(50)
    ]
    stats["undefined"] = sum(1 for a in assignments if not a)

    # ── Step 6: Save to DB ──
    t6 = time.time()

    def _save_cb(done: int, total: int, s: dict) -> None:
        if progress_cb:
            progress_cb(done, total, s)

    save_stats = save_results(db, companies, assignments, canonical_labels_map, company_keywords, cfg, _save_cb, cluster_specificity=cluster_specificity)
    stats.update(save_stats)
    logger.info(f"[7/7] DB save done in {time.time()-t6:.1f}s")

    # ── Persist trained artifacts to S3 (Phase 2c) ──
    try:
        _save_pipeline_artifacts(vectorizer, svd, km, canonical_labels_map)
    except Exception as exc:  # noqa: BLE001
        logger.warning("S3 artifact upload failed (incremental assignment unavailable): %s", exc)

    # ── Cross-cluster analysis ──
    try:
        analysis_path = analyze_cross_cluster_terms(db, cfg)
        stats["analysis_file"] = str(analysis_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Cross-cluster analysis failed: {exc}")
        stats["analysis_file"] = None

    # ── Automatic boilerplate entropy mining ──
    try:
        n_candidates = mine_boilerplate_entropy(
            db,
            c_tfidf_matrix=None,  # recompute internally from saved vectorizer/km
            feature_names=feature_names,
            labels_map=canonical_labels_map,
            vectorizer=vectorizer,
            n_companies=len(companies),
        )
        stats["boilerplate_candidates"] = n_candidates
        logger.info("Boilerplate entropy mining wrote %d candidates", n_candidates)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Boilerplate entropy mining failed: %s", exc)

    logger.info(f"Total pipeline time: {time.time()-t_total:.1f}s")
    return stats


# ── Phase 2c: S3 artifact persistence ─────────────────────────────────────────

_S3_TFIDF_KEY = "models/tfidf_vectorizer.pkl"
_S3_SVD_KEY = "models/svd_transformer.pkl"
_S3_CENTROIDS_KEY = "models/kmeans_centroids.npy"
_S3_CENTROID_MAP_KEY = "models/centroid_registry_map.json"


def _save_pipeline_artifacts(vectorizer, svd, km, canonical_labels_map: dict[int, str]) -> None:
    """Pickle and upload TF-IDF vectorizer, SVD transformer, K-Means centroids and
    centroid→label map to S3."""
    import io
    import json
    import pickle

    from app.services import s3_client

    if not s3_client.is_models_bucket_configured():
        logger.debug("S3_BUCKET_MODELS not configured — skipping artifact upload")
        return

    for obj, key in ((vectorizer, _S3_TFIDF_KEY), (svd, _S3_SVD_KEY)):
        buf = io.BytesIO()
        pickle.dump(obj, buf)
        s3_client.upload_model_bytes(buf.getvalue(), key)

    # Centroids (normalised, float32)
    from sklearn.preprocessing import normalize
    centers_norm = normalize(km.cluster_centers_).astype("float32")
    s3_client.upload_model_bytes(centers_norm.tobytes(), _S3_CENTROIDS_KEY)

    # centroid index → canonical name mapping + shape metadata
    centroid_map = {str(k): v for k, v in canonical_labels_map.items()}
    shape_info = {"rows": centers_norm.shape[0], "cols": centers_norm.shape[1]}
    payload = {"shape": shape_info, "centroid_map": centroid_map}
    s3_client.upload_model_bytes(json.dumps(payload).encode("utf-8"), _S3_CENTROID_MAP_KEY)

    logger.info("Pipeline artifacts uploaded to S3 (%d centroids)", centers_norm.shape[0])


@dataclass
class _PipelineArtifacts:
    vectorizer: Any
    svd: Any             # TruncatedSVD fitted transformer
    centroids: Any       # np.ndarray (K, D) normalised float32 in SVD space
    centroid_map: dict[str, str]  # str(centroid_idx) → canonical_name


from functools import lru_cache

@lru_cache(maxsize=1)
def _load_pipeline_artifacts() -> _PipelineArtifacts | None:
    """Download and cache pipeline artifacts from S3. Returns None if unavailable."""
    import io
    import json
    import pickle

    import numpy as np

    from app.services import s3_client

    if not s3_client.is_models_bucket_configured():
        return None
    try:
        vec_bytes = s3_client.download_model_bytes(_S3_TFIDF_KEY)
        vectorizer = pickle.loads(vec_bytes)

        svd_bytes = s3_client.download_model_bytes(_S3_SVD_KEY)
        svd = pickle.loads(svd_bytes)

        map_bytes = s3_client.download_model_bytes(_S3_CENTROID_MAP_KEY)
        payload = json.loads(map_bytes.decode("utf-8"))
        shape = payload["shape"]
        centroid_map: dict[str, str] = payload["centroid_map"]

        cen_bytes = s3_client.download_model_bytes(_S3_CENTROIDS_KEY)
        centroids = np.frombuffer(cen_bytes, dtype="float32").reshape(shape["rows"], shape["cols"])

        logger.info("Pipeline artifacts loaded from S3 (%d centroids)", centroids.shape[0])
        return _PipelineArtifacts(vectorizer=vectorizer, svd=svd, centroids=centroids, centroid_map=centroid_map)
    except Exception as exc:
        logger.warning("Could not load pipeline artifacts from S3: %s", exc)
        return None


def _prepare_text_for_artifacts(company, cfg: PipelineConfig, stopwords: set[str]) -> str:
    """Return a clean, stopword-filtered text string ready for vectorizer.transform()."""
    text = (
        company.purpose_keywords.replace(",", " ")
        if company.purpose_keywords
        else (company.purpose or "")
    )
    tokens = [
        t for t in text.lower().split()
        if t not in stopwords and len(t) > cfg.min_token_length
    ]
    return " ".join(tokens)


def assign_cluster_incremental(db, company, cfg: PipelineConfig | None = None) -> bool:
    """Assign tfidf_cluster to a single company without running the full pipeline.

    Uses S3-cached TF-IDF vectorizer + SVD transformer + K-Means centroids from
    the last full run. Projects the document through the same pipeline the full
    run used (TF-IDF → SVD → L2 normalise → cosine to centroids).
    Returns True if a cluster was assigned, False if skipped/unavailable.
    """
    if cfg is None:
        cfg = PipelineConfig()

    if not company.purpose_keywords and not company.purpose:
        return False

    artifacts = _load_pipeline_artifacts()
    if artifacts is None:
        return False

    try:
        import numpy as np
        from sklearn.preprocessing import normalize

        stopwords = get_stopwords(cfg)
        clean_text = _prepare_text_for_artifacts(company, cfg, stopwords)
        if not clean_text.strip():
            return False

        X_tfidf = artifacts.vectorizer.transform([clean_text])
        X_svd = artifacts.svd.transform(X_tfidf)
        X_norm = normalize(X_svd, norm="l2")   # shape (1, D)

        # Cosine similarity to all centroids (centroids already normalised)
        sims = (X_norm @ artifacts.centroids.T)[0]  # shape (K,)
        best_idx = int(np.argmax(sims))
        best_sim = float(sims[best_idx])

        if best_sim < cfg.min_similarity:
            return False

        best_label = artifacts.centroid_map.get(str(best_idx))
        if not best_label:
            return False

        from app.models.company import Company as CompanyModel
        db.query(CompanyModel).filter(CompanyModel.id == company.id).update(
            {"tfidf_cluster": best_label}
        )
        db.commit()
        return True

    except Exception as exc:
        logger.warning("assign_cluster_incremental failed for company %s: %s", getattr(company, "uid", "?"), exc)
        return False


def extract_keywords_incremental(company, cfg: PipelineConfig | None = None) -> str | None:
    """Extract purpose_keywords for a single company using the S3-cached TF-IDF vectorizer.

    Applies the same IDF weights and bigram-deduplication logic as the full pipeline,
    but without refitting — uses the vectorizer from the last full run.
    Returns a comma-separated keyword string or None if unavailable/empty.
    """
    if cfg is None:
        cfg = PipelineConfig()

    if not company.purpose and not company.purpose_keywords:
        return None

    artifacts = _load_pipeline_artifacts()
    if artifacts is None:
        return None

    try:
        import numpy as np

        stopwords = get_stopwords(cfg)
        clean_text = _prepare_text_for_artifacts(company, cfg, stopwords)
        if not clean_text.strip():
            return None

        X = artifacts.vectorizer.transform([clean_text])
        X_csr = X.tocsr()
        feature_names = artifacts.vectorizer.get_feature_names_out()

        start, end = int(X_csr.indptr[0]), int(X_csr.indptr[1])
        if start == end:
            return None

        col_indices = X_csr.indices[start:end]
        values = X_csr.data[start:end].copy()

        # Apply bigram penalty (same as full pipeline)
        for k, ci in enumerate(col_indices):
            if " " in feature_names[ci]:
                values[k] *= cfg.bigram_penalty

        candidates = cfg.top_keywords_per_company * 4
        order = np.argsort(values)[::-1][:candidates]
        selected: list[str] = []
        covered: set[str] = set()

        for j in order:
            if len(selected) == cfg.top_keywords_per_company:
                break
            if values[j] < cfg.min_keyword_score:
                break
            term = feature_names[col_indices[j]]
            words = set(term.split())
            if words.issubset(covered):
                continue
            selected.append(term)
            covered.update(words)

        return ",".join(selected) if selected else None

    except Exception as exc:
        logger.warning("extract_keywords_incremental failed for company %s: %s", getattr(company, "uid", "?"), exc)
        return None


# ── Bulk incremental keyword re-extraction ─────────────────────────────────────

def reextract_keywords_all(
    db,
    cfg: PipelineConfig | None = None,
    *,
    only_missing: bool = False,
    canton: str | None = None,
    limit: int | None = None,
    progress_cb: Callable[[int, int, dict], None] | None = None,
) -> dict[str, Any]:
    """Re-extract purpose_keywords for all companies using the S3-cached TF-IDF vectorizer.

    Lighter than recompute_keywords: no corpus refit, no spaCy, no SVD.
    Uses the vectorizer from the last full tfidf_kmeans_cluster run.

    only_missing: if True, skip companies that already have purpose_keywords.
    """
    from app.models.company import Company as CompanyModel

    if cfg is None:
        cfg = PipelineConfig()

    stats: dict[str, Any] = {"updated": 0, "skipped_no_artifacts": 0, "skipped_no_purpose": 0, "errors": []}

    artifacts = _load_pipeline_artifacts()
    if artifacts is None:
        logger.warning("reextract_keywords_all: S3 artifacts not available — aborting")
        stats["skipped_no_artifacts"] = -1
        return stats

    q = db.query(CompanyModel).filter(CompanyModel.purpose.isnot(None))
    if only_missing:
        q = q.filter(CompanyModel.purpose_keywords.is_(None))
    if canton:
        q = q.filter(CompanyModel.canton == canton.upper())
    q = q.order_by(CompanyModel.id.asc())
    if limit:
        q = q.limit(limit)

    total = q.count()
    mappings: list[dict] = []

    for i, company in enumerate(q.yield_per(cfg.db_batch_size), start=1):
        try:
            kw = extract_keywords_incremental(company, cfg)
            if kw is None:
                stats["skipped_no_purpose"] += 1
            else:
                kw_arr = [k.strip() for k in kw.split(",") if k.strip()] if kw else None
                mappings.append({"id": company.id, "purpose_keywords": kw, "purpose_keywords_arr": kw_arr})
                stats["updated"] += 1
        except Exception as exc:  # noqa: BLE001
            stats["errors"].append(f"{company.uid}: {exc}")

        if len(mappings) >= cfg.db_batch_size:
            db.bulk_update_mappings(CompanyModel, mappings)
            db.commit()
            mappings.clear()
            if progress_cb:
                progress_cb(i, total, stats)

    if mappings:
        db.bulk_update_mappings(CompanyModel, mappings)
        db.commit()
        if progress_cb:
            progress_cb(total, total, stats)

    return stats
