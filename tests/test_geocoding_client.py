import pytest


def test_geocode_uses_postal_code_segment_not_postfach(monkeypatch):
    """Regression: a 4-digit Postfach number must not be treated as the PLZ."""
    from app.api import geocoding_client as gc

    # Stub the PLZ table so the test is deterministic and offline.
    monkeypatch.setattr(
        gc,
        "_load_plz_table",
        lambda: {
            "1234": (46.0, 7.0),
            "8000": (47.0, 8.0),
        },
    )

    # A plausible building result close to the real PLZ centroid (8000).
    monkeypatch.setattr(gc, "_lookup_building", lambda _addr: (47.01, 8.01))

    addr = "ACME AG, Postfach 1234, 8000 Zürich"
    assert gc.geocode_address(addr) == (47.01, 8.01)


def test_geocode_falls_back_to_plz_centroid_when_too_far(monkeypatch):
    from app.api import geocoding_client as gc

    monkeypatch.setattr(gc, "_load_plz_table", lambda: {"8000": (47.0, 8.0)})

    # Force a building match that is clearly wrong (>15 km from centroid).
    monkeypatch.setattr(gc, "_lookup_building", lambda _addr: (46.0, 7.0))

    addr = "ACME AG, Bahnhofstrasse 1, 8000 Zürich"
    assert gc.geocode_address(addr) == (47.0, 8.0)
