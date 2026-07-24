"""SSRF guard for the web crawler — blocks fetches to internal/metadata addresses."""

import asyncio

import httpx
import pytest

from app.services.enrichment.crawler_common import _ip_blocked
from app.services.enrichment.crawler_http import _ssrf_request_guard


def _run(coro):
    return asyncio.run(coro)


def test_ip_blocked_classification():
    for blocked in ("127.0.0.1", "10.0.0.5", "192.168.1.1", "172.16.0.1",
                    "169.254.169.254", "::1", "0.0.0.0"):
        assert _ip_blocked(blocked), blocked
    for public in ("8.8.8.8", "1.1.1.1", "93.184.216.34"):
        assert not _ip_blocked(public), public


def test_guard_blocks_localhost():
    with pytest.raises(httpx.RequestError):
        _run(_ssrf_request_guard(httpx.Request("GET", "http://localhost/")))


def test_guard_blocks_cloud_metadata_ip():
    with pytest.raises(httpx.RequestError):
        _run(_ssrf_request_guard(httpx.Request("GET", "http://169.254.169.254/latest/meta-data/")))


def test_guard_blocks_private_ip_literal():
    with pytest.raises(httpx.RequestError):
        _run(_ssrf_request_guard(httpx.Request("GET", "http://10.1.2.3/admin")))


def test_guard_blocks_non_http_scheme():
    with pytest.raises(httpx.RequestError):
        _run(_ssrf_request_guard(httpx.Request("GET", "ftp://example.com/")))
