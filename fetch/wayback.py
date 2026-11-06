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
# Seconds between requests. The index (CDX) server refuses connections if
# pushed, so it gets a wider gap than ordinary page fetches.
GAP_CDX = 5.0
GAP_PAGE = 2.0


def _wait_turn(url):
    gap_wanted = GAP_CDX if "/cdx/" in url else GAP_PAGE
    with _lock:
        now = time.monotonic()
        gap = _last[0] + gap_wanted - now
        if gap > 0:
            time.sleep(gap)
        _last[0] = time.monotonic()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_follow = urllib.request.build_opener()
_nofollow = urllib.request.build_opener(NoRedirect)


LOG = []  # (seconds, status, url) for every request, written out by run.py
DEADLINE = [None]  # monotonic time after which we stop retrying


def log(msg):
    print(msg, flush=True)
    LOG.append(msg)


def out_of_time():
    return DEADLINE[0] is not None and time.monotonic() > DEADLINE[0]


def get(url, follow=True, tries=4, timeout=45):
    """Return (status, headers, body bytes, final url)."""
    opener = _follow if follow else _nofollow
    delay = 15
    for attempt in range(tries):
        if out_of_time():
            break
        _wait_turn(url)
        t0 = time.monotonic()
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with opener.open(req, timeout=timeout) as r:
                body = r.read()
                log(f"{time.monotonic() - t0:5.1f}s {r.status} {url}")
                return r.status, dict(r.headers), body, r.geturl()
        except urllib.error.HTTPError as e:
            log(f"{time.monotonic() - t0:5.1f}s {e.code} {url}")
            if e.code in (301, 302, 303, 307, 308) and not follow:
                return e.code, dict(e.headers), b"", url
            if e.code == 404:
                return 404, dict(e.headers), b"", url
            if e.code in (429, 500, 502, 503, 504, 520, 522, 524):
                time.sleep(delay)
                delay = min(delay * 2, 120)
                continue
            return e.code, dict(e.headers), b"", url
        except Exception as e:  # timeouts, resets
            log(f"{time.monotonic() - t0:5.1f}s {type(e).__name__} ({str(e)[:80]}) {url}")
            time.sleep(delay)
            delay = min(delay * 2, 120)
    return None, {}, b"", url


def cdx(url, start="20240501", end="20261231"):
    """All captures of one exact URL, one per day at most.
    Returns [] if there are none, None if the lookup failed."""
    q = urllib.parse.urlencode({
        "url": url,
        "from": start,
        "to": end,
        "output": "json",
        "fl": "timestamp,original,statuscode,digest,mimetype,length",
        "collapse": "timestamp:8",
    })
    status, _, body, _ = get(f"{CDX}?{q}", tries=5, timeout=90)
    if status is None:
        return None
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
