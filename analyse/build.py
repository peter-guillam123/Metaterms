"""Build the page's data from what the fetch Action collected.

    python analyse/build.py

Runs the comparison, merges in the plain-English summaries from
data/summaries.json, and writes site/data.js and site/index.html (the
template with the data inlined, so the page works as a single file).
"""

import datetime
import json
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "fetch"))
sys.path.insert(0, os.path.join(ROOT, "analyse"))

import compare  # noqa: E402
import run  # noqa: E402  (fetch/run.py: month list, capture picking)

DATA = os.path.join(ROOT, "data")
SITE = os.path.join(ROOT, "site")


def usable_months(caps):
    """Drop copies that are clearly not the document (login walls, error
    pages): much shorter than the page usually is."""
    months = caps["months"]
    lengths = [m["chars"] for m in months.values() if m.get("chars")]
    if not lengths:
        return {}, {}
    typical = statistics.median(lengths)
    good, bad = {}, {}
    for mon, m in months.items():
        if not m.get("text_hash"):
            bad[mon] = "fetch failed"
        elif m["chars"] < max(300, 0.4 * typical):
            bad[mon] = "copy is not the document"
        else:
            good[mon] = m
    return good, bad


def main():
    summaries = run.read_json(os.path.join(DATA, "summaries.json"), {})
    docs_out, changes_out = [], []
    for doc in run.load_terms():
        slug = doc["slug"]
        idx = run.read_json(os.path.join(DATA, "index", f"{slug}.json"))
        caps = run.read_json(os.path.join(DATA, "captures", f"{slug}.json"))
        picks = run.pick_month_captures(idx) if idx else {}
        good, bad = usable_months(caps) if caps else ({}, {})
        months_list = [{"month": mon, **{k: v for k, v in m.items() if k in ("timestamp", "original", "text_hash")}}
                       for mon, m in sorted(good.items())]
        changes = compare.compare_doc({"slug": slug, "months": months_list}) if months_list else []
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
            "archived_months": len(picks),
            "checked_months": len(good),
            "versions": len({m["text_hash"] for m in good.values()}),
            "latest": {"month": latest, "timestamp": good[latest]["timestamp"],
                       "original": good[latest]["original"],
                       "page_dates": compare.page_dates(compare.load_text(slug, good[latest]["text_hash"]))}
            if latest else None,
            "redirects": (idx or {}).get("redirects", {}),
            "cells": cells,
            "changes": [c["id"] for c in changes],
        })
        changes_out.extend(changes)
    changes_out.sort(key=lambda c: c["to"]["timestamp"], reverse=True)
    out = {
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
