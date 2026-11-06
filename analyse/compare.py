"""Compare monthly versions of each page and list the changes.

Reads data/captures/<slug>.json and data/text/<slug>/<hash>.txt (written by
the fetch Action) and writes data/changes.json. Run locally:

    python analyse/compare.py
"""

import difflib
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

TOKEN = re.compile(r"\s+|[\w'’-]+|[^\w\s]", re.UNICODE)
WORD = re.compile(r"[\w'’-]+", re.UNICODE)
DATE_LINE = re.compile(
    r"(?:last\s+(?:updated|revised|modified)|effective(?:\s+date|\s+as\s+of|\s+from)?|"
    r"date\s+of\s+last\s+revision|updated)\s*:?\s*"
    r"((?:[A-Z][a-z]+\s+\d{1,2},?\s+\d{4})|(?:\d{1,2}\s+[A-Z][a-z]+,?\s+\d{4})|(?:\d{4}-\d{2}-\d{2}))",
    re.I,
)


SENTENCE_END = re.compile(r'(?<=[.!?;])\s+(?=["(\[]?[A-Z0-9])')


INVISIBLE = dict.fromkeys(map(ord, "\u2060\u200b\u200c\u200d\ufeff\u00ad"), None)


def tidy(s):
    """Undo spacing left by the page's markup: 'Standards .' -> 'Standards.'"""
    s = s.translate(INVISIBLE)
    s = re.sub(r"\s+([.,;:!?)\]])", r"\1", s)
    s = re.sub(r"([(\[])\s+", r"\1", s)
    s = re.sub(r'"\s+([^"]{1,80}?)\s+"', r'"\1"', s)
    return s


def blocks(text):
    """Split into comparable units: headings and list items whole, other
    paragraphs into sentences. Comparing sentences, not paragraphs, keeps a
    change of layout (one long paragraph becoming a list, say) from showing
    up as a rewrite."""
    out = []
    for b in text.split("\n\n"):
        b = tidy(b.strip())
        if not b:
            continue
        if b.startswith("#"):
            out.append(b)
            continue
        prefix = ""
        if b.startswith("- "):
            prefix, b = "- ", b[2:]
        parts = SENTENCE_END.split(b)
        out.append(prefix + parts[0])
        out.extend(parts[1:])
    return out


def key(unit):
    """What counts as 'the same sentence' (ignores list and heading marks)."""
    return re.sub(r"^(#+ |- )", "", unit).strip().lower()


def words(s):
    return len(WORD.findall(s))


def word_diff(a, b):
    """Segments [[op, text], ...] where op is '=', '-' or '+'."""
    ta, tb = TOKEN.findall(a), TOKEN.findall(b)
    sm = difflib.SequenceMatcher(None, ta, tb, autojunk=False)
    segs = []

    def add(op, toks):
        text = "".join(toks)
        if not text:
            return
        if segs and segs[-1][0] == op:
            segs[-1][1] += text
        else:
            segs.append([op, text])

    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            add("=", ta[i1:i2])
        else:
            add("-", ta[i1:i2])
            add("+", tb[j1:j2])
    # Merge edits separated only by spaces or a short common word into one
    # removed phrase and one added phrase, so "January 1" -> "March 3" reads
    # as a single swap.
    out = []
    i = 0
    while i < len(segs):
        if segs[i][0] == "=":
            out.append(segs[i])
            i += 1
            continue
        rem, add_, run = [], [], []
        has_rem = has_add = False
        while i < len(segs):
            op, text = segs[i]
            if op == "-":
                rem.append(text)
                has_rem = True
            elif op == "+":
                add_.append(text)
                has_add = True
            elif (len(text) <= 4 and i + 1 < len(segs)
                  and segs[i + 1][0] != "=" and (rem or add_)):
                rem.append(text)
                add_.append(text)
            else:
                break
            run.append(segs[i])
            i += 1
        if has_rem and has_add:
            out.append(["-", "".join(rem)])
            out.append(["+", "".join(add_)])
        else:
            out.extend(run)
    return out


def similar(a, b):
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def pair_blocks(old, new):
    """Within a replaced run, pair each new block with its closest old one."""
    pairs = []
    used = set()
    for j, nb in enumerate(new):
        best, score = None, 0.0
        for i, ob in enumerate(old):
            if i in used:
                continue
            r = similar(key(ob), key(nb))
            if r > score:
                best, score = i, r
        if best is not None and score >= 0.5:
            used.add(best)
            pairs.append((best, j))
    return pairs


def heading_before(blks, idx):
    for k in range(min(idx, len(blks) - 1), -1, -1):
        if blks[k].startswith("#"):
            return blks[k].lstrip("# ").strip()
    return None


def diff_versions(a_text, b_text, context=1):
    a, b = blocks(a_text), blocks(b_text)
    sm = difflib.SequenceMatcher(None, [key(x) for x in a], [key(x) for x in b], autojunk=False)
    hunks = []
    added = removed = 0
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        items = []
        old, new = a[i1:i2], b[j1:j2]
        if op == "replace":
            pairs = pair_blocks(old, new)
            paired_old = {i for i, _ in pairs}
            pair_of_new = {j: i for i, j in pairs}
            # Removed blocks with no partner come first, then the new run
            # in order, each either reworded (paired) or wholly new.
            for i, ob in enumerate(old):
                if i not in paired_old:
                    items.append({"kind": "removed", "segs": [["-", ob]]})
                    removed += words(ob)
            for j, nb in enumerate(new):
                if j in pair_of_new:
                    segs = word_diff(old[pair_of_new[j]], nb)
                    items.append({"kind": "changed", "segs": segs})
                    removed += sum(words(t) for o, t in segs if o == "-")
                    added += sum(words(t) for o, t in segs if o == "+")
                else:
                    items.append({"kind": "added", "segs": [["+", nb]]})
                    added += words(nb)
        elif op == "delete":
            for ob in old:
                items.append({"kind": "removed", "segs": [["-", ob]]})
                removed += words(ob)
        elif op == "insert":
            for nb in new:
                items.append({"kind": "added", "segs": [["+", nb]]})
                added += words(nb)
        before = a[max(0, i1 - context):i1]
        after = a[i2:i2 + context]
        hunks.append({
            "section": heading_before(b, j1) or heading_before(a, i1),
            "before": before,
            "items": items,
            "after": after,
        })
    return hunks, added, removed


def word_change(a_text, b_text):
    """Words added and removed, comparing the running text only, so that
    headings, lists and paragraph breaks don't count."""
    wa = WORD.findall(tidy(a_text).lower())
    wb = WORD.findall(tidy(b_text).lower())
    sm = difflib.SequenceMatcher(None, wa, wb, autojunk=False)
    added = removed = 0
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op != "equal":
            removed += i2 - i1
            added += j2 - j1
    return added, removed


def page_dates(text):
    """Dates the page itself gives for its last update, if any."""
    found = []
    for m in DATE_LINE.finditer(text[:6000]):
        d = m.group(1)
        if d not in found:
            found.append(d)
    return found[:2]


def only_dates_changed(hunks):
    for h in hunks:
        for it in h["items"]:
            for op, t in it["segs"]:
                if op == "=":
                    continue
                if re.sub(r"[\d\s,./-]|January|February|March|April|May|June|July|August|"
                          r"September|October|November|December", "", t, flags=re.I).strip():
                    return False
    return True


def size_of(added, removed, hunks):
    total = added + removed
    if total == 0:
        return "formatting"
    if only_dates_changed(hunks):
        return "date only"
    if total < 25:
        return "minor"
    if total < 250:
        return "moderate"
    return "major"


def load_text(slug, h):
    with open(os.path.join(DATA, "text", slug, f"{h}.txt"), encoding="utf-8") as f:
        return f.read()


def compare_doc(cap):
    slug = cap["slug"]
    months = cap["months"]
    changes = []
    prev = None
    for m in months:
        h = m.get("text_hash")
        if not h:
            continue
        if prev and h != prev["text_hash"]:
            a, b = load_text(slug, prev["text_hash"]), load_text(slug, h)
            hunks, _, _ = diff_versions(a, b)
            added, removed = word_change(a, b)
            if hunks:
                changes.append({
                    "slug": slug,
                    "from": {k: prev[k] for k in ("month", "timestamp", "original")},
                    "to": {k: m[k] for k in ("month", "timestamp", "original")},
                    "url_changed": prev["original"] != m["original"],
                    "added_words": added,
                    "removed_words": removed,
                    "size": size_of(added, removed, hunks),
                    "page_dates_before": page_dates(a),
                    "page_dates_after": page_dates(b),
                    "hunks": hunks,
                })
        prev = m
    return changes


def main():
    capdir = os.path.join(DATA, "captures")
    out = []
    for name in sorted(os.listdir(capdir)):
        with open(os.path.join(capdir, name), encoding="utf-8") as f:
            cap = json.load(f)
        ch = compare_doc(cap)
        out.extend(ch)
        sizes = [c["size"] for c in ch]
        print(f"{cap['slug']:45} {len(ch):3} changes  {sizes}")
    with open(os.path.join(DATA, "changes.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)


if __name__ == "__main__":
    main()
