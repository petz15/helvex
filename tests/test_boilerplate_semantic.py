"""Semantic (embedding-similarity) purpose_clean stripping.

See app.services.ml.boilerplate_semantic — validated via
scripts/validate_boilerplate_similarity.py against the full DE corpus before
being wired into NOGA/Claude classification.
"""
import numpy as np
import pytest

from app.models.company import Company
from app.services.ml import boilerplate_semantic as bs


def _fake_embed_texts(texts, **kwargs):
    """Deterministic toy embedding: sentences containing 'GENERIC' align with
    the (fixed) exemplar vector; everything else is orthogonal to it."""
    return np.array(
        [[1.0, 0.0] if "GENERIC" in t else [0.0, 1.0] for t in texts],
        dtype=np.float32,
    )


@pytest.fixture(autouse=True)
def _patch_embeddings(monkeypatch):
    monkeypatch.setattr("app.services.ml.embeddings.embed_texts", _fake_embed_texts)
    monkeypatch.setattr(bs, "_get_exemplar_vecs", lambda lang: np.array([[1.0, 0.0]], dtype=np.float32))


def test_get_purpose_clean_prefers_stored_column(monkeypatch):
    company = Company(
        id=1, uid="CHE-1.000.000", name="Test AG",
        purpose="irrelevant raw text with kann in it",
        purpose_language="de",
        purpose_clean="already computed",
    )

    def _boom(*a, **kw):
        raise AssertionError("embed_texts should not be called when purpose_clean is already set")

    monkeypatch.setattr("app.services.ml.embeddings.embed_texts", _boom)

    assert bs.get_purpose_clean(company, boilerplate_patterns=[]) == "already computed"


def test_regex_fallback_when_trigger_only_in_first_sentence():
    # "kann" appears in the (only) first sentence -> never a semantic candidate
    # (find_trigger_window explicitly skips index 0) -> falls back to the regex
    # patterns list, which is empty here so _strip_purpose_boilerplate returns
    # the text unchanged.
    company = Company(
        id=2, uid="CHE-2.000.000", name="Test AG",
        purpose="Die Gesellschaft bezweckt X und kann Y tun.",
        purpose_language="de",
    )
    result = bs.get_purpose_clean(company, boilerplate_patterns=[])
    assert result == company.purpose


def test_semantic_cutoff_lands_at_first_generic_sentence():
    purpose = (
        "Die Gesellschaft bezweckt den Betrieb eines Ladens. "
        "Die Gesellschaft kann spezielle Waren verkaufen. "
        "GENERIC ancillary powers sentence here."
    )
    company = Company(
        id=3, uid="CHE-3.000.000", name="Test AG",
        purpose=purpose, purpose_language="de",
    )
    result = bs.get_purpose_clean(company, boilerplate_patterns=[])
    assert "Betrieb eines Ladens" in result
    assert "spezielle Waren verkaufen" in result
    assert "GENERIC" not in result


def test_non_semantic_language_uses_regex_fallback_only():
    # 'it' is not in SEMANTIC_LANGS -> always regex fallback, regardless of content.
    company = Company(
        id=4, uid="CHE-4.000.000", name="Test AG",
        purpose="La società può fare qualsiasi cosa in relazione al suo scopo.",
        purpose_language="it",
    )
    result = bs.get_purpose_clean(company, boilerplate_patterns=[])
    assert result == company.purpose
