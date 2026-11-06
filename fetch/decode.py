"""Undo any compression left on an archived response body.

The Wayback Machine's raw mode (id_) can hand back the bytes exactly as the
site sent them, still gzip-, zstd- or brotli-compressed.
"""

import gzip
import zlib


def decode_body(body):
    for _ in range(3):  # occasionally compressed twice
        if body[:2] == b"\x1f\x8b":
            body = gzip.decompress(body)
        elif body[:4] == b"\x28\xb5\x2f\xfd":
            import zstandard
            body = zstandard.ZstdDecompressor().stream_reader(body).read()
        elif body[:1] in (b"\x78",) and body[1:2] in (b"\x01", b"\x5e", b"\x9c", b"\xda"):
            body = zlib.decompress(body)
        else:
            head = body[:200].lstrip().lower()
            if head.startswith((b"<", b"\xef\xbb\xbf<")) or b"<html" in head or b"<!doctype" in head:
                return body
            try:
                import brotli
                return decode_body(brotli.decompress(body))
            except Exception:
                return body
    return body
