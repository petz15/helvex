"""Handlers for NOGA classification and language detection jobs."""

from __future__ import annotations

from app.services.jobs.job_handlers import JobContext


def handle_reclassify_noga(ctx: JobContext) -> tuple[dict, str]:
    from app.services.ingestion.collection import reclassify_noga

    def _progress(done: int, total: int, stats: dict) -> None:
        ctx.assert_not_cancelled()
        msg = (
            f"Processed {done}/{total} — {stats.get('updated', 0)} updated, "
            f"{stats.get('skipped_no_match', 0)} no match, "
            f"{stats.get('skipped_not_detailed', 0)} not-detailed"
        )
        ctx.progress(done, total, stats, msg)

    stats = reclassify_noga(
        ctx.db,
        resume_from=ctx.resume_from,
        only_missing_noga=bool(ctx.params.get("only_missing_noga", False)),
        include_stale=bool(ctx.params.get("include_stale", False)),
        only_detailed_raw=bool(ctx.params.get("only_detailed_raw", True)),
        progress_cb=_progress,
    )
    done_msg = (
        f"Done — {stats.get('updated', 0)} reclassified, "
        f"{stats.get('skipped_existing', 0)} skipped existing, "
        f"{stats.get('skipped_not_detailed', 0)} skipped not-detailed, "
        f"{stats.get('skipped_no_match', 0)} skipped no-match, "
        f"{len(stats.get('errors', []))} errors"
    )
    if ctx.resume_from:
        done_msg += f" (resumed from {ctx.resume_from})"
    return stats, done_msg


def handle_build_noga_embeddings(ctx: JobContext) -> tuple[dict, str]:
    from scripts.build_noga_embeddings_pg import run as _build_noga_emb
    from app import crud

    batch_size = int(ctx.params.get("batch_size", 256))
    crud.update_progress(ctx.db, ctx.job, message="Embedding NOGA taxonomy…", done=0, total=1, stats={})
    _build_noga_emb(batch_size=batch_size, dry_run=False)
    return {"batch_size": batch_size}, "NOGA embeddings built and stored in pgvector"


def handle_detect_language_bulk(ctx: JobContext) -> tuple[dict, str]:
    from app.services.ingestion.collection import detect_language_bulk

    def _progress(done: int, total: int, stats: dict) -> None:
        ctx.assert_not_cancelled()
        msg = (
            f"Processed {done}/{total} — {stats.get('updated', 0)} updated, "
            f"{stats.get('skipped_no_purpose', 0)} no purpose"
        )
        ctx.progress_no_event(done, total, stats, msg)

    stats = detect_language_bulk(
        ctx.db,
        only_missing=bool(ctx.params.get("only_missing", True)),
        progress_cb=_progress,
    )
    return stats, (
        f"Done — {stats.get('updated', 0)} languages detected, "
        f"{stats.get('skipped_existing', 0)} skipped existing, "
        f"{len(stats.get('errors', []))} errors"
    )


def handle_noga_v2_explain(ctx: JobContext) -> tuple[dict, str]:
    from app.services.ml.noga import classify_company_noga_v2
    from app import crud

    company_id = int(ctx.params["company_id"])
    company = crud.get_company(ctx.db, company_id)
    if company is None:
        return {"error": f"Company {company_id} not found"}, "Company not found"

    ctx.status(f"Running NOGA explain for {company.name} ({company.uid})…")

    result = classify_company_noga_v2(ctx.db, company)
    result["company_id"] = company_id
    result["company_uid"] = company.uid
    result["company_name"] = company.name
    result["stored_noga_code"] = company.noga_code
    result["stored_noga_label"] = company.noga_label
    result["stored_noga_confidence"] = company.noga_confidence
    result["stored_noga_path_labels"] = company.noga_path_labels

    return result, f"NOGA explain complete for {company.name}"


def handle_reclassify_low_conf_noga(ctx: JobContext) -> tuple[dict, str]:
    from app.services.ingestion.collection import reclassify_low_confidence_noga

    threshold = float(ctx.params.get("confidence_threshold", 0.80))

    def _progress(done: int, total: int, stats: dict) -> None:
        ctx.assert_not_cancelled()
        msg = (
            f"Processed {done}/{total} — {stats.get('improved', 0)} improved, "
            f"{stats.get('still_low', 0)} still low confidence"
        )
        ctx.progress_no_event(done, total, stats, msg)

    stats = reclassify_low_confidence_noga(
        ctx.db,
        confidence_threshold=threshold,
        progress_cb=_progress,
    )
    return stats, (
        f"Done — {stats.get('updated', 0)} reclassified, "
        f"{stats.get('improved', 0)} now above threshold, "
        f"{stats.get('still_low', 0)} still low, "
        f"{len(stats.get('errors', []))} errors"
    )


def handle_enrich_web_purpose_sim(ctx: JobContext) -> tuple[dict, str]:
    """Compute purpose↔site semantic similarity for company_web_extract rows.

    For each extract that has a description or service_keywords but no purpose_sim,
    embeds both the company's Zefix purpose text and the site's description/keywords
    using paraphrase-multilingual-mpnet-base-v2 (same model as NOGA), then stores
    cosine similarity (dot product of L2-normalised vectors) as purpose_sim (0–1).

    This is a 4th confidence layer consumed by website_status._extract_tier() to
    lift borderline extracts when the site's content semantically matches the company.

    Requires sentence-transformers — runs on the ML worker.
    """
    import numpy as np
    from sqlalchemy import text

    from app.services.ml.embeddings import embed_texts

    batch_size = int(ctx.params.get("batch_size", 500))
    only_missing = bool(ctx.params.get("only_missing", True))
    stats: dict = {"updated": 0, "skipped_no_content": 0, "skipped_no_purpose": 0, "errors": []}

    missing_cond = "AND e.purpose_sim IS NULL" if only_missing else ""
    total_q = ctx.db.execute(
        text(
            f"SELECT COUNT(*) FROM company_web_extract e "
            f"WHERE (e.description IS NOT NULL OR e.service_keywords IS NOT NULL) {missing_cond}"
        )
    ).scalar() or 0
    total = int(total_q)
    done = 0

    while True:
        ctx.assert_not_cancelled()

        rows = ctx.db.execute(
            text(
                f"SELECT e.company_id, e.url_candidate_id, e.description, e.service_keywords, "
                f"c.purpose "
                f"FROM company_web_extract e "
                f"JOIN companies c ON c.id = e.company_id "
                f"WHERE (e.description IS NOT NULL OR e.service_keywords IS NOT NULL) {missing_cond} "
                f"ORDER BY e.company_id "
                f"LIMIT :lim OFFSET :off"
            ),
            {"lim": batch_size, "off": done},
        ).fetchall()
        if not rows:
            break

        texts_purpose: list[str] = []
        texts_site: list[str] = []
        valid_rows: list = []
        for row in rows:
            purpose = (row[4] or "").strip()
            desc = (row[2] or "").strip()
            keywords = " ".join(row[3] or []) if row[3] else ""
            site_text = f"{desc} {keywords}".strip()
            if not purpose:
                stats["skipped_no_purpose"] += 1
                continue
            if not site_text:
                stats["skipped_no_content"] += 1
                continue
            texts_purpose.append(purpose)
            texts_site.append(site_text)
            valid_rows.append(row)

        if texts_purpose:
            try:
                emb_purpose = embed_texts(texts_purpose, batch_size=256)
                emb_site = embed_texts(texts_site, batch_size=256)
                # L2-normalised → dot product = cosine similarity
                sims = np.sum(emb_purpose * emb_site, axis=1)

                for (row, sim) in zip(valid_rows, sims):
                    sim_val = float(np.clip(sim, 0.0, 1.0))
                    try:
                        ctx.db.execute(
                            text(
                                "UPDATE company_web_extract SET purpose_sim = :sim "
                                "WHERE company_id = :cid AND url_candidate_id = :ucid"
                            ),
                            {"sim": sim_val, "cid": row[0], "ucid": row[1]},
                        )
                        stats["updated"] += 1
                    except Exception as exc:  # noqa: BLE001
                        stats["errors"].append(f"company {row[0]}: {exc}")

                ctx.db.commit()
            except Exception as exc:  # noqa: BLE001
                stats["errors"].append(f"batch at offset {done}: {exc}")
                ctx.db.rollback()

        done += len(rows)
        msg = (
            f"Processed {done}/{total} — {stats['updated']} updated, "
            f"{stats['skipped_no_purpose']} no purpose, "
            f"{stats['skipped_no_content']} no content, "
            f"{len(stats['errors'])} errors"
        )
        from app import crud
        crud.update_progress(ctx.db, ctx.job, message=msg, done=done, total=total, stats=dict(stats))
        crud.create_event(ctx.db, job_id=ctx.job.id, level="debug", message=msg)

    return stats, (
        f"Purpose-sim done — {stats['updated']} extracts enriched, "
        f"{stats['skipped_no_purpose']} skipped (no purpose), "
        f"{stats['skipped_no_content']} skipped (no site content), "
        f"{len(stats['errors'])} errors"
    )


def _handle_embed_purpose(ctx: JobContext, batch_fn) -> tuple[dict, str]:
    only_missing = bool(ctx.params.get("only_missing", True))

    def _progress(done: int, total: int, stats: dict) -> None:
        ctx.assert_not_cancelled()
        msg = (
            f"Processed {done}/{total} — {stats.get('updated', 0)} embedded, "
            f"{stats.get('skipped_no_purpose', 0)} no purpose"
        )
        ctx.progress(done, total, stats, msg)

    stats = batch_fn(
        ctx.db,
        resume_from=ctx.resume_from,
        only_missing=only_missing,
        progress_cb=_progress,
    )
    done_msg = (
        f"Done — {stats.get('updated', 0)} embeddings stored, "
        f"{stats.get('skipped_no_purpose', 0)} skipped (no purpose), "
        f"{len(stats.get('errors', []))} errors"
    )
    if ctx.resume_from:
        done_msg += f" (resumed from {ctx.resume_from})"
    return stats, done_msg


def handle_embed_purpose_full(ctx: JobContext) -> tuple[dict, str]:
    from app.services.ingestion.collection import embed_purpose_full_batch
    return _handle_embed_purpose(ctx, embed_purpose_full_batch)


def handle_embed_purpose_clean(ctx: JobContext) -> tuple[dict, str]:
    from app.services.ingestion.collection import embed_purpose_clean_batch
    return _handle_embed_purpose(ctx, embed_purpose_clean_batch)
