"""Tests for the Serper.dev search client (no real network calls)."""

from unittest.mock import MagicMock, patch

import pytest

from app.api.google_search_client import search_website


class TestSearchWebsite:
    def test_raises_when_no_api_key(self, monkeypatch):
        monkeypatch.setattr("app.api.google_search_client.settings.serper_api_key", "")
        with pytest.raises(ValueError, match="SERPER_API_KEY"):
            search_website("Test AG")

    def test_returns_results(self, monkeypatch):
        monkeypatch.setattr("app.api.google_search_client.settings.serper_api_key", "key123")

        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {
            "organic": [
                {"title": "Test AG – Official", "link": "https://test-ag.ch", "snippet": "We are Test AG"},
                {"title": "Test AG on LinkedIn", "link": "https://linkedin.com/company/test-ag"},
            ]
        }

        with patch("app.api.google_search_client.httpx.Client") as mock_client_cls:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.post.return_value = mock_response
            mock_client_cls.return_value = mock_ctx

            results = search_website("Test AG")

        assert len(results) == 2
        assert results[0].link == "https://test-ag.ch"
        assert results[0].snippet == "We are Test AG"
        assert results[1].snippet is None

    def test_empty_response(self, monkeypatch):
        monkeypatch.setattr("app.api.google_search_client.settings.serper_api_key", "key123")

        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {}

        with patch("app.api.google_search_client.httpx.Client") as mock_client_cls:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.post.return_value = mock_response
            mock_client_cls.return_value = mock_ctx

            results = search_website("Unknown Corp")

        assert results == []
