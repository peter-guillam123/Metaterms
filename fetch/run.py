"""Fetch archived copies of each page in terms.csv from the Wayback Machine.

Usage: python fetch/run.py probe|full

probe: list every archived copy of each page (and of any page it redirects
       to), then download the oldest and newest copy as samples.
full:  pick one copy per month and download it (see later commits).
"""

import csv
import gzip
import json
import os
import sys
import urllib.parse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(__file__))
import wayback  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
INDEX = os.path.join(DATA, "index")
SAMPLES = os.path.join(ROOT, "samples")
WINDOW_START = "20241101"


def load_terms():
    with open(os.path.join(ROOT, "terms.csv"), newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def norm_path(url):
    p = urllib.parse.urlsplit(url if "://" in url else "https://" + url)
    host = p.netloc.lower().removeprefix("www.")
    return host + p.path.rstrip("/")


def discover(url):
    """Distinct archived URLs with the same host and path, any query string."""
    p = urllib.parse.urlsplit(url)
    base = p.netloc + p.path.rstrip("/")
    q = urllib.parse.urlencode({
        "url": base,
        "matchType": "prefix",
        "from": "20240501",
        "output": "json",
        "fl": "original",
        "collapse": "urlkey",
        "limit": "500",
    })
    status, _, body, _ = wayback.get(f"{wayback.CDX}?{q}")
    found = {url}
    if status == 200 and body.strip():
        rows = json.loads(body)[1:]
        want = norm_path(url)
        for (orig,) in rows:
            if norm_path(orig) == want:
                found.add(orig)
    return sorted(found)


def summarise(caps):
    st = Counter(c["statuscode"][:1] + "xx" for c in caps)
    months = sorted({c["timestamp"][:6] for c in caps
                     if c["statuscode"] == "200" and c["timestamp"] >= WINDOW_START})
    return {"total": len(caps), "status": dict(st), "months_200": months}


def index_doc(doc):
    slug = doc["slug"]
    print(f"[{slug}] indexing", flush=True)
    variants = {}
    redirects = {}
    queue = [doc["url"]]
    seen = set()
    depth = 0
    while queue and depth < 3:
        nxt = []
        for url in queue:
            for v in discover(url):
                key = v.split("://", 1)[-1].lower().removeprefix("www.")
                if key in seen:
                    continue
                seen.add(key)
                caps = wayback.cdx(v)
                if not caps:
                    continue
                variants[v] = caps
                recent = [c for c in caps if c["timestamp"] >= WINDOW_START]
                threes = [c for c in recent if c["statuscode"].startswith("3")]
                if threes:
                    last = threes[-1]
                    target = wayback.redirect_target(last["timestamp"], last["original"])
                    if target:
                        redirects[v] = {"timestamp": last["timestamp"], "target": target}
                        if norm_path(target) != norm_path(v):
                            nxt.append(target)
        queue = nxt
        depth += 1
    out = {
        "slug": slug,
        "url": doc["url"],
        "redirects": redirects,
        "variants": {v: {"summary": summarise(c), "captures": c} for v, c in variants.items()},
    }
    os.makedirs(INDEX, exist_ok=True)
    with open(os.path.join(INDEX, f"{slug}.json"), "w") as f:
        json.dump(out, f, indent=1)
    for v, d in out["variants"].items():
        s = d["summary"]
        print(f"[{slug}]   {v}  total={s['total']} {s['status']} months200={len(s['months_200'])}", flush=True)
    return out


def best_variant(idx):
    best = None
    for v, d in idx["variants"].items():
        score = len(d["summary"]["months_200"])
        if best is None or score > best[0]:
            best = (score, v)
    return best[1] if best and best[0] > 0 else None


def sample_doc(idx):
    slug = idx["slug"]
    v = best_variant(idx)
    if not v:
        print(f"[{slug}] no 200 captures in window", flush=True)
        return
    caps = [c for c in idx["variants"][v]["captures"]
            if c["statuscode"] == "200" and c["timestamp"] >= WINDOW_START]
    os.makedirs(SAMPLES, exist_ok=True)
    for label, c in (("early", caps[0]), ("late", caps[-1])):
        status, headers, body, final = wayback.get(wayback.snapshot_url(c["timestamp"], c["original"]))
        print(f"[{slug}] sample {label} {c['timestamp']} -> {status} {len(body)} bytes", flush=True)
        if status == 200 and body:
            with gzip.open(os.path.join(SAMPLES, f"{slug}.{label}.html.gz"), "wb") as f:
                f.write(body)


def probe():
    docs = load_terms()
    with ThreadPoolExecutor(max_workers=3) as ex:
        indexes = list(ex.map(index_doc, docs))
    with ThreadPoolExecutor(max_workers=3) as ex:
        list(ex.map(sample_doc, indexes))
    coverage = {}
    for idx in indexes:
        v = best_variant(idx)
        coverage[idx["slug"]] = {
            "best_variant": v,
            "months_200": idx["variants"][v]["summary"]["months_200"] if v else [],
            "redirects": idx["redirects"],
        }
    with open(os.path.join(DATA, "coverage.json"), "w") as f:
        json.dump(coverage, f, indent=1)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "probe"
    if mode == "probe":
        probe()
    else:
        sys.exit(f"unknown mode {mode}")
