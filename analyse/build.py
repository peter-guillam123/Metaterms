"""Build the page's data from what the fetch Action collected.

    python analyse/build.py

Runs the comparison, merges in the plain-English summaries from
data/summaries.json, and writes site/data.js and site/index.html (the
template with the data inlined, so the page works as a single file).
"""

import datetime
import hashlib
import json
import os
import re
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "fetch"))  # run.py, extract.py
sys.path.insert(0, os.path.join(ROOT, "analyse"))

import compare  # noqa: E402
import run  # noqa: E402  (fetch/run.py: month list, capture picking)

DATA = os.path.join(ROOT, "data")
SITE = os.path.join(ROOT, "site")

with open(os.path.join(ROOT, "analyse", "rules.json"), encoding="utf-8") as f:
    RULES = json.load(f)

# Meta shows a cookie consent banner only to visitors in the UK and EU, so a
# copy that has one came from one of the archive's European crawlers, and
# holds the European version of the page.
BANNER_END = re.compile(r"^(Allow all cookies|Only allow essential cookies|Decline optional cookies|"
                        r"Allow essential and optional cookies)$", re.I)
BANNER = re.compile(r"Allow (the use of )?cookies from (Facebook|Instagram|Meta)|Allow all cookies", re.I)


def clean(slug, text):
    """Return (text, region) ready to compare, or (None, reason) if the copy
    turns out not to be the document."""
    blocks = text.split("\n\n")
    region = "eu" if BANNER.search("\n".join(blocks[:15])) else "main"
    if region == "eu":
        ends = [i for i, b in enumerate(blocks[:20]) if BANNER_END.match(b.strip())]
        if ends:
            blocks = blocks[ends[-1] + 1:]
    rule = RULES.get(slug, {})
    if rule.get("start_at"):
        pat = re.compile(rule["start_at"], re.I)
        idx = next((i for i, b in enumerate(blocks) if pat.search(b.strip())), None)
        if idx is None:
            return None, "copy is not the document"
        blocks = blocks[idx:]
    if rule.get("stop_at"):
        pat = re.compile(rule["stop_at"], re.I)
        cut = next((i for i, b in enumerate(blocks) if i > len(blocks) * 0.2 and pat.search(b.strip())), None)
        if cut is not None:
            blocks = blocks[:cut]
    return "\n\n".join(blocks).strip() + "\n", region


def usable_months(slug, caps, texts):
    """Sort months into usable copies and the rest, with reasons. Cleaned
    texts go into `texts` (keyed by their own hash); each good month's
    text_hash is replaced with the cleaned text's hash."""
    months = caps["months"]
    good, bad, region = {}, {}, {}
    for mon, m in months.items():
        if not m.get("text_hash"):
            bad[mon] = "fetch failed"
            continue
        min_chars = RULES.get(slug, {}).get("min_chars")
        if m.get("usable") is False and not min_chars:
            bad[mon] = "copy is not the document"
            continue
        if min_chars:
            import extract
            raw = compare.load_text(slug, m["text_hash"])
            if m["chars"] < min_chars or not extract.is_english(raw):
                bad[mon] = "copy is not the document"
                continue
        text, info = clean(slug, compare.load_text(slug, m["text_hash"]))
        if text is None:
            bad[mon] = info
            continue
        h = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
        texts[h] = text
        good[mon] = dict(m, text_hash=h, chars=len(text))
        region[mon] = info
    lengths = [m["chars"] for m in good.values()]
    if lengths:
        typical = statistics.median(lengths)
        for mon in list(good):
            if good[mon]["chars"] < max(300, 0.4 * typical):
                bad[mon] = "copy is not the document"
                del good[mon]
    # Compare like with like: keep the region with the most copies.
    regions = [region[m] for m in good]
    if regions:
        main = max(set(regions), key=regions.count)
        for mon in list(good):
            if region[mon] != main:
                bad[mon] = ("European version of the page" if region[mon] == "eu"
                            else "US version of the page")
                del good[mon]
    return good, bad


def main():
    summaries = run.read_json(os.path.join(DATA, "summaries.json"), {})
    docs_out, changes_out = [], []
    for doc in run.load_terms():
        slug = doc["slug"]
        idx = run.read_json(os.path.join(DATA, "index", f"{slug}.json"))
        caps = run.read_json(os.path.join(DATA, "captures", f"{slug}.json"))
        picks = run.pick_month_captures(idx) if idx else {}
        texts = {}
        good, bad = usable_months(slug, caps, texts) if caps else ({}, {})
        months_list = [{"month": mon, **{k: v for k, v in m.items() if k in ("timestamp", "original", "text_hash")}}
                       for mon, m in sorted(good.items())]
        changes = compare.compare_doc({"slug": slug, "months": months_list}, load=texts.get) if months_list else []
        for c in changes:
            c["id"] = f"{slug}-{c['to']['month']}"
            s = summaries.get(c["id"], {})
            c["summary"] = s.get("summary")
            c["headline"] = s.get("headline")
            if s.get("size"):
                c["size"] = s["size"]
        changed_months = {c["to"]["month"]: c["id"] for c in changes}
        cells = {}
        first = min(good) if good else None
        for mon in run.months():
            if mon in changed_months:
                cells[mon] = {"s": "changed", "id": changed_months[mon]}
            elif mon in good:
                cells[mon] = {"s": "first" if mon == first else "same", "ts": good[mon]["timestamp"]}
            elif mon in bad:
                cells[mon] = {"s": "bad", "why": bad[mon]}
            elif mon in picks:
                cells[mon] = {"s": "pending"}
            else:
                cells[mon] = {"s": "none"}
        latest = max(good) if good else None
        docs_out.append({
            "slug": slug,
            "title": doc["title"],
            "product": doc["product"],
            "url": doc["url"],
            "also": [u for u in doc.get("also", "").split(";") if u],
            "archived_months": len(picks),
            "checked_months": len(good),
            "unusable_months": len(bad),
            "versions": len({m["text_hash"] for m in good.values()}),
            "latest": {"month": latest, "timestamp": good[latest]["timestamp"],
                       "original": good[latest]["original"],
                       "page_dates": compare.page_dates(texts[good[latest]["text_hash"]])}
            if latest else None,
            # Only real moves: not a dropped slash, a login page or a
            # page that redirects to an address we already check.
            "redirects": {k: v for k, v in (idx or {}).get("redirects", {}).items()
                          if "/login" not in v["target"]
                          and run.url_key(v["target"]) != run.url_key(k)
                          and run.url_key(v["target"]) not in {run.url_key(u) for u in [doc["url"]] + doc.get("also", "").split(";") if u}},
            "cells": cells,
            "changes": [c["id"] for c in changes],
        })
        changes_out.extend(changes)
    changes_out.sort(key=lambda c: c["to"]["timestamp"], reverse=True)
    ids = {c["id"] for c in changes_out}
    highlights = [h for h in run.read_json(os.path.join(DATA, "highlights.json"), [])
                  if all(i in ids for i in h["ids"])]
    out = {
        "highlights": highlights,
        "generated": datetime.date.today().isoformat(),
        "months": run.months(),
        "docs": docs_out,
        "changes": changes_out,
    }
    os.makedirs(SITE, exist_ok=True)
    blob = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(SITE, "data.json"), "w", encoding="utf-8") as f:
        f.write(blob)
    with open(os.path.join(SITE, "template.html"), encoding="utf-8") as f:
        tpl = f.read()
    page = tpl.replace("/*DATA*/null", blob.replace("</", "<\\/"))
    with open(os.path.join(SITE, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)
    by_size = {}
    for c in changes_out:
        by_size[c["size"]] = by_size.get(c["size"], 0) + 1
    print(f"{len(docs_out)} pages, {len(changes_out)} changes {by_size}; "
          f"{sum(1 for c in changes_out if not c['summary'])} without a summary")


if __name__ == "__main__":
    main()
