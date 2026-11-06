"""Fetch archived copies of each page in terms.csv from the Wayback Machine.

Usage: python fetch/run.py probe|full|reextract

probe:     index each page's archived copies and save the oldest and newest
           copy as samples, for tuning the text extractor.
full:      fetch one copy per month and turn each into text. Works in
           passes: every third month first, then the months between any two
           that differ (to pin down when a change happened), then the rest.
           Stops after BUDGET_MINUTES; the next run carries on.
reextract: rebuild the text from pages already downloaded (no fetching).

Everything is resumable: what's already in data/ is not fetched again.
"""

import csv
import glob
import gzip
import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import wayback  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
INDEX = os.path.join(DATA, "index")
CAPS = os.path.join(DATA, "captures")
TEXT = os.path.join(DATA, "text")
RAW = os.path.join(ROOT, "raw")
SAMPLES = os.path.join(ROOT, "samples")

STARTED = [time.monotonic()]
FIRST_MONTH = (2024, 11)
LAST_MONTH = (2026, 11)


def months():
    y, m = FIRST_MONTH
    out = []
    while (y, m) <= LAST_MONTH:
        out.append(f"{y}{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def load_terms():
    with open(os.path.join(ROOT, "terms.csv"), newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def read_json(path, default=None):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def url_key(u):
    return u.split("://", 1)[-1].lower().removeprefix("www.").rstrip("/")


# ---------- index ----------

def index_doc(doc):
    """One exact-URL index lookup, plus one for a redirect target if the
    page has moved. Wayback treats a trailing slash as the same URL."""
    slug = doc["slug"]
    path = os.path.join(INDEX, f"{slug}.json")
    idx = read_json(path)
    if idx:
        return idx
    if wayback.out_of_time():
        return None
    wayback.log(f"[{slug}] indexing {doc['url']}")
    urls = {}
    redirects = {}
    todo = [doc["url"]] + [u for u in doc.get("also", "").split(";") if u]
    while todo and len(urls) < 5:
        url = todo.pop(0)
        caps = wayback.cdx(url)
        if caps is None:  # failed, not empty
            wayback.log(f"[{slug}] index lookup failed; will retry next run")
            return None
        urls[url] = caps
        recent = [c for c in caps if c["timestamp"] >= "20241101"]
        ok = {c["timestamp"][:6] for c in recent if c["statuscode"] == "200"}
        threes = [c for c in recent if c["statuscode"].startswith("3")]
        if threes and len(ok) < 20:
            last = threes[-1]
            target = wayback.redirect_target(last["timestamp"], last["original"])
            if target:
                redirects[url] = {"timestamp": last["timestamp"], "target": target}
                if url_key(target) not in {url_key(u) for u in list(urls) + todo}:
                    todo.append(target)
    idx = {"slug": slug, "url": doc["url"], "redirects": redirects, "urls": urls}
    for u, caps in urls.items():
        ok = {c["timestamp"][:6] for c in caps if c["statuscode"] == "200" and c["timestamp"] >= "20241101"}
        wayback.log(f"[{slug}]   {u}: {len(caps)} captures, 200s in {len(ok)} months")
    write_json(path, idx)
    maybe_checkpoint("indexing")
    return idx


def pick_month_captures(idx):
    """For each month, the best HTML capture: from the URL with the most
    months of coverage where possible, closest to the 15th."""
    pool = []
    cover = {}
    for u, caps in idx["urls"].items():
        good = [c for c in caps if c["statuscode"] == "200" and "html" in c.get("mimetype", "html")]
        # Skip copies far smaller than usual: these are mostly "browser not
        # supported" or login pages served to the archive's crawler.
        good = [c for c in good if not c.get("length", "-").isdigit() or int(c["length"]) >= 4000]
        sizes = sorted(int(c["length"]) for c in good if c.get("length", "-").isdigit())
        typical = sizes[len(sizes) // 2] if sizes else 0
        for c in good:
            n = int(c["length"]) if c.get("length", "-").isdigit() else typical
            c["_small"] = n < 0.25 * typical
        cover[url_key(u)] = len({c["timestamp"][:6] for c in good})
        pool.extend(good)
    rank = {k: i for i, k in enumerate(sorted(cover, key=lambda k: -cover[k]))}
    picks = {}
    for mon in months():
        cands = [c for c in pool if c["timestamp"][:6] == mon]
        if not cands:
            continue
        mid = int(mon + "15120000")
        # Biggest copy first: the real document is bigger than the "browser
        # not supported" and login pages Facebook often serves the crawler.
        cands.sort(key=lambda c: (rank.get(url_key(c["original"]), 9),
                                  -int(c["length"]) if c.get("length", "-").isdigit() else 0,
                                  abs(int(c["timestamp"]) - mid)))
        clean = [{k: v for k, v in c.items() if not k.startswith("_")} for c in cands[:3]]
        picks[mon] = dict(clean[0], alts=clean[1:])
    return picks


# ---------- fetch and extract ----------

def raw_path(slug, ts):
    return os.path.join(RAW, slug, f"{ts}.html.gz")


def fetch_raw(slug, cap):
    p = raw_path(slug, cap["timestamp"])
    if os.path.exists(p):
        from decode import decode_body
        with gzip.open(p, "rb") as f:
            return decode_body(f.read())
    if wayback.out_of_time():
        return None
    status, _, body, final = wayback.get(wayback.snapshot_url(cap["timestamp"], cap["original"]))
    if status != 200 or not body:
        return None
    from decode import decode_body
    body = decode_body(body)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with gzip.open(p, "wb") as f:
        f.write(body)
    return body


EXTRACTOR_VERSION = 3


def to_text(slug, body):
    """Extract, save and describe the text: (hash, length, usable)."""
    import extract
    text = extract.extract(body)
    h = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
    p = os.path.join(TEXT, slug, f"{h}.txt")
    if not os.path.exists(p):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
    return h, len(text), len(text) >= MIN_CHARS and extract.is_english(text)


def load_caps(doc):
    return read_json(os.path.join(CAPS, f"{doc['slug']}.json"), {
        "slug": doc["slug"], "title": doc["title"], "product": doc["product"],
        "url": doc["url"], "months": {},
    })


def save_caps(caps):
    caps["months"] = dict(sorted(caps["months"].items()))
    write_json(os.path.join(CAPS, f"{caps['slug']}.json"), caps)


MIN_CHARS = 800  # less than this is a login wall or "browser not supported"


def fetch_month(doc, caps, mon, pick):
    """Fetch the month's best copy; if it isn't the document, try the
    month's next-best copies. Returns False only if we ran out of time."""
    entry = None
    for cap in [pick] + pick.get("alts", []):
        body = fetch_raw(doc["slug"], cap)
        if body is None and wayback.out_of_time():
            return False
        entry = {"timestamp": cap["timestamp"], "original": cap["original"], "digest": cap["digest"]}
        if body is None:
            entry["error"] = "fetch failed"
            continue
        h, n, ok = to_text(doc["slug"], body)
        entry.update(text_hash=h, chars=n, usable=ok, extractor=EXTRACTOR_VERSION)
        if ok:
            break
    caps["months"][mon] = entry
    save_caps(caps)
    return True


def next_needed(picks, caps):
    """Which month to fetch next for one page, or None."""
    avail = [m for m in months() if m in picks]
    if not avail:
        return None
    done = caps["months"]

    def fetched(m):
        return m in done

    # Pass 1: newest month first (so every page's latest wording arrives
    # early), then the oldest, then every third month between.
    coarse = [avail[-1]] + avail[::3]
    for m in coarse:
        if not fetched(m):
            return m
    # Pass 2: between two fetched months whose text differs, fetch the middle.
    have = [m for m in avail if fetched(m) and done[m].get("usable")]
    for a, b in zip(have, have[1:]):
        if done[a]["text_hash"] != done[b]["text_hash"]:
            between = [m for m in avail if a < m < b and not fetched(m)]
            if between:
                return between[len(between) // 2]
    # Pass 3: everything else, to confirm nothing changed and changed back.
    for m in avail:
        if not fetched(m):
            return m
    return None


def full():
    reextract(only_old=True)
    links()
    docs = load_terms()
    plan = []
    for doc in docs:
        idx = index_doc(doc)
        if idx:
            plan.append((doc, pick_month_captures(idx)))
    # Round-robin across pages so every page gets its coarse pass early.
    progress = True
    last_push = time.monotonic()
    while progress and not wayback.out_of_time():
        progress = False
        for doc, picks in plan:
            if wayback.out_of_time():
                break
            caps = load_caps(doc)
            m = next_needed(picks, caps)
            if m is None:
                continue
            if fetch_month(doc, caps, m, picks[m]):
                progress = True
                save_sample(doc["slug"], picks, m)
            if maybe_checkpoint("fetching"):
                write_status(plan)
    write_status(plan)


LAST_PUSH = [time.monotonic()]


def maybe_checkpoint(stage):
    """Push progress at most every ten minutes."""
    if time.monotonic() - LAST_PUSH[0] < 600:
        return False
    LAST_PUSH[0] = time.monotonic()
    checkpoint(f"{stage}, {(time.monotonic() - STARTED[0]) / 60:.0f} minutes in")
    return True


def save_sample(slug, picks, mon):
    """Keep the newest copy of each page in the repo, for checking the
    text extraction by eye."""
    if mon != max(picks):
        return
    src = raw_path(slug, load_caps({"slug": slug, "title": "", "product": "", "url": ""})["months"][mon]["timestamp"])
    out = os.path.join(SAMPLES, f"{slug}.late.html.gz")
    if os.path.exists(src):
        os.makedirs(SAMPLES, exist_ok=True)
        with open(src, "rb") as a, open(out, "wb") as b:
            b.write(a.read())


def checkpoint(msg):
    """Commit and push what we have so far, so progress shows on GitHub."""
    if os.environ.get("CHECKPOINT") != "1":
        return
    import subprocess
    cmds = [
        ["git", "add", "data", "samples"],
        ["git", "commit", "-q", "-m", f"Fetch results: {msg}"],
        ["git", "pull", "-q", "--rebase", "origin", os.environ.get("GITHUB_REF_NAME", "HEAD")],
        ["git", "push", "-q", "origin", f"HEAD:{os.environ.get('GITHUB_REF_NAME', 'HEAD')}"],
    ]
    for c in cmds:
        r = subprocess.run(c, cwd=ROOT, capture_output=True, text=True)
        if r.returncode and c[1] != "commit":
            wayback.log(f"checkpoint: {' '.join(c[:2])} failed: {r.stderr.strip()[:200]}")
            return
    wayback.log("checkpoint pushed")


def write_status(plan):
    summary = {}
    for doc, picks in plan:
        caps = load_caps(doc)
        done = caps["months"]
        summary[doc["slug"]] = {
            "months_archived": len(picks),
            "months_fetched": sum(1 for m in done if done[m].get("text_hash")),
            "versions": len({d.get("text_hash") for d in done.values() if d.get("text_hash")}),
            "complete": next_needed(picks, caps) is None,
        }
    write_json(os.path.join(DATA, "status.json"), summary)
    left = [s for s, v in summary.items() if not v["complete"]]
    wayback.log(f"pages still to finish: {len(left)}")


def reextract(only_old=False):
    """Rebuild text from downloaded pages after the extractor changes.
    A month whose copy turns out unusable is dropped, so it gets fetched
    again (trying the month's other copies)."""
    n = 0
    for doc in load_terms():
        caps = load_caps(doc)
        for mon, entry in list(caps["months"].items()):
            if only_old and entry.get("extractor") == EXTRACTOR_VERSION:
                continue
            p = raw_path(doc["slug"], entry["timestamp"])
            if not os.path.exists(p):
                continue
            with gzip.open(p, "rb") as f:
                from decode import decode_body
                h, chars, ok = to_text(doc["slug"], decode_body(f.read()))
            entry.update(text_hash=h, chars=chars, usable=ok, extractor=EXTRACTOR_VERSION)
            if not ok:
                del caps["months"][mon]
            n += 1
        save_caps(caps)
    wayback.log(f"re-extracted {n} downloaded pages")


def links():
    """List the links in every downloaded copy of the Terms of Service
    that point at other terms (for finding the EU messaging terms)."""
    import re
    from decode import decode_body
    found = {}
    for path in sorted(glob.glob(os.path.join(RAW, "terms-of-service", "*.html.gz"))):
        with gzip.open(path, "rb") as f:
            html = decode_body(f.read()).decode("utf-8", "replace")
        for href in re.findall(r'href="([^"]+)"', html):
            href = href.replace("&amp;", "&")
            if re.search(r"(legal|terms|messag|eecc|contract|summary)", href, re.I) and "static" not in href:
                found.setdefault(href, []).append(os.path.basename(path)[:8])
    write_json(os.path.join(DATA, "terms-links.json"), {k: sorted(set(v)) for k, v in sorted(found.items())})
    wayback.log(f"found {len(found)} links")


def probe():
    os.makedirs(SAMPLES, exist_ok=True)
    for doc in load_terms():
        idx = index_doc(doc)
        if not idx:
            continue
        picks = pick_month_captures(idx)
        if not picks:
            wayback.log(f"[{doc['slug']}] no usable captures since Nov 2024")
            continue
        ms = sorted(picks)
        for label, m in (("early", ms[0]), ("late", ms[-1])):
            out = os.path.join(SAMPLES, f"{doc['slug']}.{label}.html.gz")
            if os.path.exists(out):
                continue
            body = fetch_raw(doc["slug"], picks[m])
            if body:
                with gzip.open(out, "wb") as f:
                    f.write(body)


def write_log(mode, started):
    wayback.log(f"done: mode={mode} lines={len(wayback.LOG)} "
                f"minutes={(time.monotonic() - started) / 60:.1f}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "probe"
    budget = float(os.environ.get("BUDGET_MINUTES", "20"))
    started = time.monotonic()
    STARTED[0] = started
    LAST_PUSH[0] = started
    os.makedirs(DATA, exist_ok=True)
    wayback.LOGFILE[0] = open(os.path.join(DATA, "last-run.log"), "w", buffering=1)
    wayback.log(f"start: mode={mode} budget={budget} minutes")
    wayback.DEADLINE[0] = started + budget * 60
    try:
        {"probe": probe, "full": full, "reextract": reextract, "links": links}[mode]()
    finally:
        write_log(mode, started)
