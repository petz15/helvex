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
    n_clusters: int = 150
    kmeans_random_state: int = 42
    kmeans_max_iter: int = 300
    kmeans_n_init: int = 3

    # ── Multi-label assignment ──
    max_clusters_per_company: int = 7   # assign up to this many clusters per company
    min_similarity: float = 0.10        # cosine similarity threshold; below → Undefined

    # ── Labeling ──
    top_terms_per_cluster: int = 5      # terms in each cluster label
    top_keywords_per_company: int = 10  # stored in purpose_keywords

    # ── Cross-cluster analysis ──
    analysis_top_clusters: int = 20
    analysis_top_terms: int = 10

    # ── DB write ──
    db_batch_size: int = 200

    # ── Extra domain stopwords merged with _TFIDF_STOPWORDS ──
    extra_stopwords: list[str] = field(default_factory=lambda: [
        "gesellschaft", "zweck", "unternehmen", "dienstleistungen", "kunden",
        "erbringt", "betreibt", "sowie", "alle", "art", "insbesondere",
        "tätigkeiten", "erwerb", "verwaltung", "beteiligung", "holding",
        "bezweckt", "zwecke", "tätig", "firma",
    ])


# ── Stopword helper ───────────────────────────────────────────────────────────

def get_stopwords(cfg: PipelineConfig) -> set[str]:
    """Return the combined stopword set: domain list from collection.py + extras."""
    try:
        from app.services.collection import _TFIDF_STOPWORDS
        base: set[str] = set(_TFIDF_STOPWORDS)
    except ImportError:
        base = set()
    return base | set(cfg.extra_stopwords)


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
    """TruncatedSVD + L2 normalisation (euclidean distance ≈ cosine similarity)."""
    from sklearn.decomposition import TruncatedSVD
    from sklearn.preprocessing import normalize

    n = min(cfg.n_components, X.shape[1] - 1)
    svd = TruncatedSVD(n_components=n, random_state=cfg.svd_random_state)
    X_svd = svd.fit_transform(X)
    return normalize(X_svd)


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

    assignments: list[list[int]] = []
    for i in range(sim_matrix.shape[0]):
        sims = sim_matrix[i]
        ranked = sims.argsort()[::-1][: cfg.max_clusters_per_company]
        assigned = [int(idx) for idx in ranked if sims[idx] >= cfg.min_similarity]
        assignments.append(assigned)

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

    n_features = X_tfidf.shape[1]

    # Build per-cluster term-sum matrix using hard K-Means assignments
    cluster_term_sum = np.zeros((n_clusters, n_features))
    for cid in range(n_clusters):
        mask = hard_labels == cid
        if mask.sum() > 0:
            cluster_term_sum[cid] = np.asarray(X_tfidf[mask].sum(axis=0)).flatten()

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


# ── Step 6b: Per-document keyword extraction ──────────────────────────────────

def extract_company_keywords(
    X_tfidf,
    feature_names,
    cfg: PipelineConfig,
) -> list[str | None]:
    """Extract top-N TF-IDF keywords from each company's own purpose text.

    Uses the same bigram deduplication as label_clusters so results are clean.
    Returns one string (or None) per row in X_tfidf.
    """
    import numpy as np

    results: list[str | None] = []
    candidates = cfg.top_keywords_per_company * 4

    for i in range(X_tfidf.shape[0]):
        row = np.asarray(X_tfidf[i].todense()).flatten()
        if row.max() == 0:
            results.append(None)
            continue

        ranked_idx = row.argsort()[::-1][:candidates]
        selected: list[str] = []
        covered: set[str] = set()

        for j in ranked_idx:
            if row[j] == 0:
                break
            if len(selected) == cfg.top_keywords_per_company:
                break
            term = feature_names[j]
            words = set(term.split())
            if words.issubset(covered):
                continue
            selected.append(term)
            covered.update(words)

        results.append(",".join(selected) if selected else None)

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
) -> dict[str, Any]:
    """Write tfidf_cluster (pipe-separated cluster labels) and purpose_keywords to DB.

    tfidf_cluster format: "label_a|label_b|label_c" where each label is the
    comma-separated c-TF-IDF terms for that cluster.
    Companies with no cluster above the similarity threshold get "Undefined".
    """
    stats: dict[str, Any] = {"classified": 0, "undefined": 0, "skipped": 0, "errors": []}
    total = len(companies)

    for idx in range(0, total, cfg.db_batch_size):
        batch = companies[idx: idx + cfg.db_batch_size]
        batch_assignments = assignments[idx: idx + cfg.db_batch_size]
        batch_kws = company_keywords[idx: idx + cfg.db_batch_size]

        for company, cluster_ids, kw in zip(batch, batch_assignments, batch_kws):
            try:
                company.purpose_keywords = kw
                if not cluster_ids:
                    company.tfidf_cluster = "Undefined"
                    stats["undefined"] += 1
                else:
                    parts = [labels_map[cid] for cid in cluster_ids if cid in labels_map]
                    company.tfidf_cluster = "|".join(parts) if parts else "Undefined"
                    stats["classified"] += 1
            except Exception as exc:  # noqa: BLE001
                stats["errors"].append(f"{company.uid}: {exc}")
                stats["skipped"] += 1

        db.commit()
        if progress_cb:
            progress_cb(min(idx + cfg.db_batch_size, total), total, stats)

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


# ── Main Pipeline ─────────────────────────────────────────────────────────────

def run_pipeline(
    db,
    cfg: PipelineConfig | None = None,
    *,
    canton: str | None = None,
    industry: str | None = None,
    min_zefix_score: int | None = None,
    max_zefix_score: int | None = None,
    limit: int | None = None,
    progress_cb: Callable[[int, int, dict], None] | None = None,
) -> dict[str, Any]:
    """Run the full K-Means multi-label clustering pipeline end-to-end.

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

    # ── Load companies ──
    t0 = time.time()
    q = db.query(Company).filter(Company.purpose.isnot(None))
    if canton:
        q = q.filter(Company.canton == canton.upper())
    if industry:
        q = q.filter(Company.industry.ilike(f"%{industry}%"))
    if min_zefix_score is not None:
        q = q.filter(Company.zefix_score >= min_zefix_score)
    if max_zefix_score is not None:
        q = q.filter(Company.zefix_score <= max_zefix_score)
    q = q.order_by(Company.id.asc())
    if limit:
        q = q.limit(limit)
    companies = q.all()
    logger.info(f"[1/7] Loaded {len(companies)} companies in {time.time()-t0:.1f}s")
    if not companies:
        return stats

    purposes = [c.purpose or "" for c in companies]

    # ── Step 1: Preprocessing ──
    t1 = time.time()

    def _prep_cb(done: int, total: int) -> None:
        if progress_cb:
            progress_cb(done, total, {**stats, "step": "lemmatizing"})

    cleaned = preprocess_texts(purposes, cfg, progress_cb=_prep_cb)
    logger.info(f"[2/7] Lemmatization done in {time.time()-t1:.1f}s")

    # ── Step 2: TF-IDF ──
    t2 = time.time()
    vectorizer, X_tfidf = vectorize(cleaned, cfg)
    feature_names = vectorizer.get_feature_names_out()
    logger.info(f"[3/7] TF-IDF done in {time.time()-t2:.1f}s — shape: {X_tfidf.shape}")

    # ── Step 3: Dimensionality reduction ──
    t3 = time.time()
    X_reduced = reduce_dimensions(X_tfidf, cfg)
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
    logger.info(f"[6/7] Labeling done in {time.time()-t5:.1f}s")

    # ── Step 5b: Multi-label assignment + per-doc keywords ──
    t5b = time.time()
    if progress_cb:
        progress_cb(0, len(companies), {**stats, "step": "assigning"})
    assignments = assign_multi_label(X_reduced, km, cfg)
    company_keywords = extract_company_keywords(X_tfidf, feature_names, cfg)
    logger.info(f"[6b/7] Assignment + keywords done in {time.time()-t5b:.1f}s")

    # Summary: count how many companies reference each cluster label
    from collections import Counter
    label_counter: Counter = Counter()
    for cluster_ids in assignments:
        for cid in cluster_ids:
            label_counter[labels_map[cid]] += 1
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

    save_stats = save_results(db, companies, assignments, labels_map, company_keywords, cfg, _save_cb)
    stats.update(save_stats)
    logger.info(f"[7/7] DB save done in {time.time()-t6:.1f}s")

    # ── Cross-cluster analysis ──
    try:
        analysis_path = analyze_cross_cluster_terms(db, cfg)
        stats["analysis_file"] = str(analysis_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Cross-cluster analysis failed: {exc}")
        stats["analysis_file"] = None

    logger.info(f"Total pipeline time: {time.time()-t_total:.1f}s")
    return stats
