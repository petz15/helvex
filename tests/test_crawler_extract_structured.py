"""Structured content extraction (team/services pages) — web-pipeline holistic
rework, Layer A.4/C. Deterministic, no API cost."""
from app.services.enrichment import crawler_extract
from app.services.enrichment.crawler_common import parse_soup
from app.services.enrichment.crawler_extract import (
    _extract_services_struct,
    _extract_team_struct,
    extract_page,
    resolve_company_extract,
)

_TEAM_HTML = """
<html><body>
<h1>Unser Team</h1>
<h2>Peter Meier</h2>
<p>Geschäftsführer</p>
<h2>Anna Muster</h2>
<p>Leiterin Marketing</p>
<h2>Info</h2>
<p>Kontaktieren Sie uns jederzeit.</p>
</body></html>
""".encode("utf-8")

_SERVICES_HTML = """
<html><body>
<h1>Unsere Leistungen</h1>
<h2>Beratung</h2>
<p>Wir beraten Unternehmen bei der digitalen Transformation und Prozessoptimierung.</p>
<h2>Umsetzung</h2>
<p>Wir setzen Projekte von der Idee bis zur Produktion um, inklusive Support danach.</p>
<h2>Kontakt</h2>
<p>Rufen Sie uns an.</p>
</body></html>
""".encode("utf-8")


def test_extract_team_struct_finds_names_and_roles():
    soup = parse_soup(_TEAM_HTML)
    entries = _extract_team_struct(soup)
    names = {e["name"] for e in entries}
    assert "Peter Meier" in names
    assert "Anna Muster" in names
    assert "Info" not in names  # not a name shape

    by_name = {e["name"]: e["role"] for e in entries}
    assert by_name["Peter Meier"] and "geschäftsführer" in by_name["Peter Meier"].lower()
    assert by_name["Anna Muster"] and "leiterin" in by_name["Anna Muster"].lower()


def test_extract_services_struct_finds_titled_entries():
    soup = parse_soup(_SERVICES_HTML)
    entries = _extract_services_struct(soup)
    titles = {e["title"] for e in entries}
    assert "Beratung" in titles
    assert "Umsetzung" in titles
    assert "Kontakt" not in titles  # excluded nav-like heading

    by_title = {e["title"]: e["summary"] for e in entries}
    assert "digitalen Transformation" in by_title["Beratung"]


def test_extract_page_wires_structured_extraction_by_page_type():
    team_sig = extract_page(_TEAM_HTML, "team")
    assert len(team_sig.persons_struct) >= 2
    assert team_sig.services_struct == []

    services_sig = extract_page(_SERVICES_HTML, "services")
    assert len(services_sig.services_struct) >= 2
    assert services_sig.persons_struct == []

    # Other page types don't run structured extraction at all.
    homepage_sig = extract_page(_TEAM_HTML, "homepage")
    assert homepage_sig.persons_struct == []


def test_resolve_company_extract_aggregates_structured_fields(monkeypatch):
    # _main_text() depends on trafilatura, whose extraction quality/availability
    # (transitively justext) varies by environment — stub it so about_text's
    # aggregation logic is tested independently of that.
    monkeypatch.setattr(
        crawler_extract, "_main_text",
        lambda html_str: "Wir sind ein Schweizer Unternehmen fuer Beratung und mehr.",
    )

    pages = [
        ("homepage", b"<html><body><h1>Muster AG</h1><p>Wir sind ein Schweizer Unternehmen fuer Beratung und mehr.</p></body></html>"),
        ("team", _TEAM_HTML),
        ("services", _SERVICES_HTML),
    ]
    result = resolve_company_extract(pages, company_name="Muster AG")
    assert result != {}

    persons_struct = result["persons_struct"]
    assert persons_struct is not None
    names = {p["name"] for p in persons_struct}
    assert "Peter Meier" in names

    services_struct = result["services_struct"]
    assert services_struct is not None
    titles = {s["title"] for s in services_struct}
    assert "Beratung" in titles

    assert result["about_text"] == "Wir sind ein Schweizer Unternehmen fuer Beratung und mehr."


def test_resolve_company_extract_dedupes_across_pages():
    """Same team-page content crawled twice (e.g. duplicate URL candidates
    merged) shouldn't double the persons_struct list."""
    pages = [("team", _TEAM_HTML), ("team", _TEAM_HTML)]
    result = resolve_company_extract(pages)
    names = [p["name"] for p in result["persons_struct"]]
    assert names.count("Peter Meier") == 1
