"""HDBSCAN-based company clustering pipeline for zefix_analyzer.

Pipeline steps:
  1. Load companies from DB
  2. Lemmatize purpose text with spaCy de_core_news_md
  3. TF-IDF vectorization
  4. Dimensionality reduction (TruncatedSVD + L2 normalize)
  5. HDBSCAN clustering
  6. Cluster labeling (top-7 TF-IDF terms per cluster)
  7. Write labels back to Company.tfidf_cluster

Standalone helper:
  analyze_cross_cluster_terms() — finds terms that appear across many
  cluster labels (candidates for the stopword list) and writes a .txt file.

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
    n_components: int = 150
    svd_random_state: int = 42

    # ── HDBSCAN ──
    min_cluster_size: int = 75
    min_samples: int = 10
    hdbscan_metric: str = "euclidean"
    hdbscan_algorithm: str = "boruvka_kdtree"
    core_dist_n_jobs: int = -1

    # ── Labeling ──
    top_terms_per_cluster: int = 7

    # ── Cross-cluster analysis ──
    analysis_top_clusters: int = 20
    analysis_top_terms: int = 10

    # ── DB write ──
    db_batch_size: int = 200

    # ── Extra domain stopwords merged with _TFIDF_STOPWORDS ──
    # These are loaded at runtime so the set is always current.
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
    import numpy as np
    from sklearn.decomposition import TruncatedSVD
    from sklearn.preprocessing import normalize

    n = min(cfg.n_components, X.shape[1] - 1)
    svd = TruncatedSVD(n_components=n, random_state=cfg.svd_random_state)
    X_svd = svd.fit_transform(X)
    return normalize(X_svd)


# ── Step 4: HDBSCAN Clustering ────────────────────────────────────────────────

def cluster_hdbscan(X_reduced, cfg: PipelineConfig):
    """Return integer cluster labels; -1 = noise/outlier."""
    try:
        import hdbscan
    except ImportError as exc:
        raise ImportError("hdbscan is required. Run: pip install hdbscan") from exc

    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=cfg.min_cluster_size,
        min_samples=cfg.min_samples,
        metric=cfg.hdbscan_metric,
        algorithm=cfg.hdbscan_algorithm,
        core_dist_n_jobs=cfg.core_dist_n_jobs,
    )
    return clusterer.fit_predict(X_reduced)


# ── Step 5: Cluster Labeling ──────────────────────────────────────────────────

def label_clusters(
    cluster_labels,
    X_tfidf,
    feature_names,
    cfg: PipelineConfig,
) -> dict[int, str]:
    """For each cluster compute mean TF-IDF vector and extract top-N terms.

    Returns {cluster_id: "term1,term2,...,termN"}.
    """
    import numpy as np

    labels_map: dict[int, str] = {}
    unique_ids = sorted(set(cluster_labels) - {-1})

    for cid in unique_ids:
        mask = cluster_labels == cid
        mean_vec = np.asarray(X_tfidf[mask].mean(axis=0)).flatten()
        top_idx = mean_vec.argsort()[-cfg.top_terms_per_cluster:][::-1]
        terms = [feature_names[i] for i in top_idx]
        labels_map[cid] = ",".join(terms)

    return labels_map


# ── Step 6: Save Results to DB ────────────────────────────────────────────────

def save_results(
    db,
    companies: list,
    cluster_labels,
    labels_map: dict[int, str],
    cfg: PipelineConfig,
    progress_cb: Callable[[int, int, dict], None] | None = None,
) -> dict[str, Any]:
    """Write tfidf_cluster labels to DB in batches.

    Noise companies (cluster == -1) get tfidf_cluster = None.
    """
    stats: dict[str, Any] = {"classified": 0, "noise": 0, "skipped": 0, "errors": []}
    total = len(companies)

    for idx in range(0, total, cfg.db_batch_size):
        batch = companies[idx: idx + cfg.db_batch_size]
        batch_cids = cluster_labels[idx: idx + cfg.db_batch_size]

        for company, cid in zip(batch, batch_cids):
            try:
                cid_int = int(cid)
                if cid_int == -1:
                    company.tfidf_cluster = None
                    stats["noise"] += 1
                else:
                    company.tfidf_cluster = labels_map.get(cid_int)
                    stats["classified"] += 1
            except Exception as exc:  # noqa: BLE001
                stats["errors"].append(f"{company.uid}: {exc}")
                stats["skipped"] += 1

        db.commit()
        if progress_cb:
            progress_cb(min(idx + cfg.db_batch_size, total), total, stats)

    return stats


# ── Step 7: Cross-Cluster Term Analysis ───────────────────────────────────────

def analyze_cross_cluster_terms(
    db,
    cfg: PipelineConfig | None = None,
    output_path: Path | None = None,
) -> Path:
    """Find terms that appear across many cluster labels (stopword candidates).

    Reads cluster labels already stored in Company.tfidf_cluster, groups by
    label, takes the top-N largest clusters, splits each label into its
    component terms, and counts how many clusters each term appears in.

    Writes a tab-separated .txt file sorted by appearance frequency.
    Returns the output path.
    """
    from collections import Counter
    from sqlalchemy import func
    from app.models.company import Company

    if cfg is None:
        cfg = PipelineConfig()
    if output_path is None:
        output_path = Path(__file__).parent.parent / "static" / "cluster_analysis.txt"

    # cluster label → member count
    rows = (
        db.query(Company.tfidf_cluster, func.count(Company.id).label("cnt"))
        .filter(Company.tfidf_cluster.isnot(None))
        .group_by(Company.tfidf_cluster)
        .order_by(func.count(Company.id).desc())
        .all()
    )

    top_labels = [label for label, _ in rows[: cfg.analysis_top_clusters]]
    term_counter: Counter = Counter()
    for label in top_labels:
        for term in label.split(",")[: cfg.analysis_top_terms]:
            term = term.strip()
            if term:
                term_counter[term] += 1

    lines = [
        "# Cross-cluster term frequency analysis",
        f"# Top {cfg.analysis_top_clusters} clusters by size, top {cfg.analysis_top_terms} terms each",
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
    progress_cb: Callable[[int, int, dict], None] | None = None,
) -> dict[str, Any]:
    """Run the full HDBSCAN clustering pipeline end-to-end.

    Returns a stats dict with keys:
      classified, noise, skipped, n_clusters, errors, summary, analysis_file
    """
    import numpy as np
    from app.models.company import Company

    if cfg is None:
        cfg = PipelineConfig()

    stats: dict[str, Any] = {
        "classified": 0, "noise": 0, "skipped": 0,
        "n_clusters": 0, "errors": [], "summary": [],
    }
    t_total = time.time()

    # ── Load companies ──
    t0 = time.time()
    companies = (
        db.query(Company)
        .filter(Company.purpose.isnot(None))
        .order_by(Company.id.asc())
        .all()
    )
    logger.info(f"[1/6] Loaded {len(companies)} companies in {time.time()-t0:.1f}s")
    if not companies:
        return stats

    purposes = [c.purpose or "" for c in companies]

    # ── Step 1: Preprocessing ──
    t1 = time.time()

    def _prep_cb(done: int, total: int) -> None:
        if progress_cb:
            msg_stats = {**stats, "step": "lemmatizing"}
            progress_cb(done, total, msg_stats)

    cleaned = preprocess_texts(purposes, cfg, progress_cb=_prep_cb)
    logger.info(f"[2/6] Lemmatization done in {time.time()-t1:.1f}s")

    # ── Step 2: TF-IDF ──
    t2 = time.time()
    vectorizer, X_tfidf = vectorize(cleaned, cfg)
    feature_names = vectorizer.get_feature_names_out()
    logger.info(f"[3/6] TF-IDF done in {time.time()-t2:.1f}s — shape: {X_tfidf.shape}")

    # ── Step 3: Dimensionality reduction ──
    t3 = time.time()
    X_reduced = reduce_dimensions(X_tfidf, cfg)
    logger.info(f"[4/6] SVD done in {time.time()-t3:.1f}s — shape: {X_reduced.shape}")

    # ── Step 4: Clustering ──
    t4 = time.time()
    if progress_cb:
        progress_cb(0, len(companies), {**stats, "step": "clustering"})
    cluster_labels = cluster_hdbscan(X_reduced, cfg)
    unique_ids = sorted(set(cluster_labels) - {-1})
    stats["n_clusters"] = len(unique_ids)
    stats["noise"] = int((cluster_labels == -1).sum())
    logger.info(
        f"[5/6] HDBSCAN done in {time.time()-t4:.1f}s — "
        f"{len(unique_ids)} clusters, {stats['noise']} noise"
    )

    # ── Step 5: Labeling ──
    t5 = time.time()
    labels_map = label_clusters(cluster_labels, X_tfidf, feature_names, cfg)
    cluster_counts = {
        cid: int((cluster_labels == cid).sum()) for cid in unique_ids
    }
    logger.info(f"[6/6] Labeling done in {time.time()-t5:.1f}s")

    # Build human-readable summary (largest clusters first)
    stats["summary"] = [
        {"cluster_id": cid, "label": labels_map[cid], "company_count": cluster_counts[cid]}
        for cid in sorted(cluster_counts, key=lambda k: -cluster_counts[k])
    ]

    # ── Step 6: Save to DB ──
    t6 = time.time()

    def _save_cb(done: int, total: int, s: dict) -> None:
        if progress_cb:
            progress_cb(done, total, s)

    save_stats = save_results(db, companies, cluster_labels, labels_map, cfg, _save_cb)
    stats.update(save_stats)
    logger.info(f"DB save done in {time.time()-t6:.1f}s")

    # ── Cross-cluster analysis (auto-run, output to static/) ──
    try:
        analysis_path = analyze_cross_cluster_terms(db, cfg)
        stats["analysis_file"] = str(analysis_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Cross-cluster analysis failed: {exc}")
        stats["analysis_file"] = None

    logger.info(f"Total pipeline time: {time.time()-t_total:.1f}s")
    return stats
