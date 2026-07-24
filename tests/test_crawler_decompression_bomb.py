"""Decompression ("zip") bomb defense for the web crawler.

httpx's automatic decoder (used by Response.aiter_bytes) calls
zlib.decompressobj().decompress(data) with no output-size bound, so a small
crafted gzip/deflate body can force a single call to materialize gigabytes in
memory before any size check runs. crawler_common.read_bounded_body avoids
this by reading raw (undecoded) bytes and decompressing in small increments
via decompress(chunk, max_length=remaining), which zlib guarantees never
returns more than `remaining` bytes per call.
"""
import gzip
import io
import zlib

import pytest

from app.services.enrichment.crawler_common import (
    MAX_PAGE_BYTES,
    DecompressionBombError,
    _make_bounded_decoder,
)


def _make_gzip_bomb(decompressed_size: int) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as f:
        f.write(b"A" * decompressed_size)
    return buf.getvalue()


def _bounded_decode(compressed: bytes, encoding: str, cap: int, chunk_size: int = 8192) -> tuple[bytes, int]:
    """Mirror read_bounded_body's decode loop without needing an httpx transport."""
    decoder = _make_bounded_decoder(encoding)
    out = bytearray()
    peak_single_call = 0
    for i in range(0, len(compressed), chunk_size):
        remaining = cap - len(out)
        if remaining <= 0:
            break
        piece = decoder.decompress(compressed[i:i + chunk_size], remaining)
        peak_single_call = max(peak_single_call, len(piece))
        out.extend(piece)
        if len(out) >= cap:
            break
    return bytes(out), peak_single_call


def test_bounded_decode_caps_a_real_200mb_gzip_bomb():
    bomb = _make_gzip_bomb(200 * 1024 * 1024)
    assert len(bomb) < 300_000  # tiny on the wire

    out, peak_single_call = _bounded_decode(bomb, "gzip", MAX_PAGE_BYTES)

    assert len(out) == MAX_PAGE_BYTES
    assert peak_single_call <= MAX_PAGE_BYTES
    assert out == b"A" * MAX_PAGE_BYTES


def test_unbounded_decompress_is_the_vulnerability_this_replaces():
    """Documents *why* the bound matters: without max_length, one call
    materializes the full decompressed size regardless of how tiny the input was."""
    bomb = _make_gzip_bomb(50 * 1024 * 1024)
    unbounded = zlib.decompressobj(zlib.MAX_WBITS | 16)
    full = unbounded.decompress(bomb)
    assert len(full) == 50 * 1024 * 1024


def test_deflate_bomb_also_bounded():
    raw = b"B" * (50 * 1024 * 1024)
    compressed = zlib.compress(raw, 9)
    out, peak_single_call = _bounded_decode(compressed, "deflate", MAX_PAGE_BYTES)
    assert len(out) == MAX_PAGE_BYTES
    assert peak_single_call <= MAX_PAGE_BYTES


def test_unsupported_encoding_fails_closed():
    with pytest.raises(DecompressionBombError):
        _make_bounded_decoder("br")
    with pytest.raises(DecompressionBombError):
        _make_bounded_decoder("zstd")


def test_identity_encoding_is_passthrough():
    assert _make_bounded_decoder("") is None
    assert _make_bounded_decoder("identity") is None
