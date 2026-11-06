"""Print every change as a compact plain-text redline, for writing summaries.

    python analyse/report.py [slug ...] > report.txt

Removed text is shown as [-...-], added text as {+...+}.
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def render(c, limit=6000):
    out = [f"=== {c['id']}  {c['size']}  +{c['added_words']}/-{c['removed_words']} words  "
           f"{c['from']['timestamp'][:8]} -> {c['to']['timestamp'][:8]}  dates: {c['page_dates_before']} -> {c['page_dates_after']}"]
    for h in c["hunks"]:
        if h["section"]:
            out.append(f"  § {h['section']}")
        for it in h["items"]:
            line = "".join(t if op == "=" else (f"[-{t}-]" if op == "-" else f"{{+{t}+}}") for op, t in it["segs"])
            out.append(f"  {it['kind'][:3]}: {line}")
    text = "\n".join(out)
    return text if len(text) <= limit else text[:limit] + f"\n  ... ({len(text) - limit} more characters)"


def main():
    with open(os.path.join(ROOT, "site", "data.json"), encoding="utf-8") as f:
        data = json.load(f)
    want = set(sys.argv[1:])
    for c in sorted(data["changes"], key=lambda c: (c["slug"], c["to"]["timestamp"])):
        if want and c["slug"] not in want and c["id"] not in want:
            continue
        print(render(c))
        print()


if __name__ == "__main__":
    main()
