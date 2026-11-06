"""Small, polite client for the Wayback Machine.

Only the standard library. Requests go one at a time over a single kept-open
connection, spaced out, with back-off on refusals, 429 and 5xx.
"""

import http.client
import json
import re
import ssl
import threading
import time
import urllib.parse

UA = "metaterms-research/0.1 (+https://github.com/peter-guillam123/Metaterms)"
CDX = "https://web.archive.org/cdx/search/cdx"

_lock = threading.Lock()
_last = [0.0]
# Seconds between requests. The index (CDX) server refuses connections if
# pushed, so it gets a wider gap than ordinary page fetches.
GAP_CDX = 8.0
GAP_PAGE = 8.0


def _wait_turn(url):
    gap_wanted = GAP_CDX if "/cdx/" in url else GAP_PAGE
    with _lock:
        now = time.monotonic()
        gap = _last[0] + gap_wanted - now
        if gap > 0:
            time.sleep(gap)
        _last[0] = time.monotonic()


LOG = []  # (seconds, status, url) for every request, written out by run.py
DEADLINE = [None]  # monotonic time after which we stop retrying


LOGFILE = [None]  # run.py opens data/last-run.log here; lines go in as they happen


def log(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    LOG.append(line)
    if LOGFILE[0]:
        LOGFILE[0].write(line + "\n")


def out_of_time():
    return DEADLINE[0] is not None and time.monotonic() > DEADLINE[0]


# One connection, kept open between requests. The archive refuses new
# connections opened in quick succession, and reusing one is lighter on
# its servers anyway.
HOST = "web.archive.org"
_conn = [None]


def _drop():
    if _conn[0] is not None:
        try:
            _conn[0].close()
        except Exception:
            pass
    _conn[0] = None


def _fetch_once(path, timeout):
    if _conn[0] is None:
        _conn[0] = http.client.HTTPSConnection(HOST, timeout=timeout, context=ssl.create_default_context())
    c = _conn[0]
    c.timeout = timeout
    if c.sock is not None:
        c.sock.settimeout(timeout)
    c.request("GET", path, headers={"User-Agent": UA, "Connection": "keep-alive"})
    r = c.getresponse()
    body = r.read()
    headers = {k: v for k, v in r.getheaders()}
    if (r.getheader("Connection") or "").lower() == "close":
        _drop()
    return r.status, headers, body


def get(url, follow=True, tries=3, timeout=30):
    """Return (status, headers, body bytes, final url)."""
    delay = 15
    for attempt in range(tries):
        if out_of_time():
            break
        current = url
        t0 = time.monotonic()
        try:
            for hop in range(6):
                parts = urllib.parse.urlsplit(current)
                if parts.netloc != HOST:
                    return 302, {"Location": current}, b"", current
                path = parts.path + (f"?{parts.query}" if parts.query else "")
                _wait_turn(current)
                t0 = time.monotonic()
                status, headers, body = _fetch_once(path, timeout)
                log(f"{time.monotonic() - t0:5.1f}s {status} {current}")
                loc = headers.get("Location") or headers.get("location")
                if status in (301, 302, 303, 307, 308) and follow and loc:
                    current = urllib.parse.urljoin(current, loc)
                    continue
                break
        except Exception as e:  # refused, reset, timed out: usually the
            # archive asking us to slow down, so give it a full minute.
            _drop()
            log(f"{time.monotonic() - t0:5.1f}s {type(e).__name__} ({str(e)[:80]}) {current}")
            time.sleep(75)
            continue
        if status in (429, 500, 502, 503, 504, 520, 522, 524):
            time.sleep(delay)
            delay = min(delay * 2, 120)
            continue
        return status, headers, body, current
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
