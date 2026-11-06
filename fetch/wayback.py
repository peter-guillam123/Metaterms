"""Small, polite client for the Wayback Machine.

Only the standard library, so the GitHub Action needs no installs for this
part. All requests share one rate limiter, and back off on 429 and 5xx.
"""

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "metaterms-research/0.1 (+https://github.com/peter-guillam123/Metaterms)"
CDX = "https://web.archive.org/cdx/search/cdx"

_lock = threading.Lock()
_last = [0.0]
MIN_GAP = 1.2  # seconds between any two requests, across threads


def _wait_turn():
    with _lock:
        now = time.monotonic()
        gap = _last[0] + MIN_GAP - now
        if gap > 0:
            time.sleep(gap)
        _last[0] = time.monotonic()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_follow = urllib.request.build_opener()
_nofollow = urllib.request.build_opener(NoRedirect)


def get(url, follow=True, tries=6, timeout=60):
    """Return (status, headers, body bytes, final url)."""
    opener = _follow if follow else _nofollow
    delay = 10
    for attempt in range(tries):
        _wait_turn()
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with opener.open(req, timeout=timeout) as r:
                return r.status, dict(r.headers), r.read(), r.geturl()
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308) and not follow:
                return e.code, dict(e.headers), b"", url
            if e.code == 404:
                return 404, dict(e.headers), b"", url
            if e.code in (429, 500, 502, 503, 504, 520, 522, 524):
                print(f"  {e.code} on {url}; waiting {delay}s", flush=True)
                time.sleep(delay)
                delay = min(delay * 2, 300)
                continue
            return e.code, dict(e.headers), b"", url
        except Exception as e:  # timeouts, resets
            print(f"  {type(e).__name__} on {url}; waiting {delay}s", flush=True)
            time.sleep(delay)
            delay = min(delay * 2, 300)
    return None, {}, b"", url


def cdx(url, start="20240501", end="20261231"):
    """All captures of one exact URL, one per day at most."""
    q = urllib.parse.urlencode({
        "url": url,
        "from": start,
        "to": end,
        "output": "json",
        "fl": "timestamp,original,statuscode,digest,mimetype,length",
        "collapse": "timestamp:8",
    })
    status, _, body, _ = get(f"{CDX}?{q}")
    if status != 200 or not body.strip():
        return []
    rows = json.loads(body)
    if not rows:
        return []
    head, *rest = rows
    return [dict(zip(head, r)) for r in rest]


def snapshot_url(ts, original):
    return f"https://web.archive.org/web/{ts}id_/{original}"


def redirect_target(ts, original):
    """Where an archived 3xx capture pointed, or None."""
    status, headers, _, _ = get(snapshot_url(ts, original), follow=False)
    loc = headers.get("Location") or headers.get("location")
    if not loc:
        return None
    m = re.search(r"/web/\d+(?:id_)?/(.+)$", loc)
    target = m.group(1) if m else loc
    if target.startswith("//"):
        target = "https:" + target
    if target.startswith("/"):
        p = urllib.parse.urlsplit(original)
        target = f"{p.scheme}://{p.netloc}{target}"
    return target
