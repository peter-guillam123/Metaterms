"""Turn an archived page into plain text that is stable from month to month.

The output is light Markdown: '#' headings, '- ' list items and blank lines
between blocks. Menus, footers, scripts and other page furniture are
dropped, so that only changes to the document itself show up.
"""

import re
import unicodedata

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

DROP_TAGS = {
    "script", "style", "noscript", "svg", "template", "iframe", "head",
    "button", "input", "select", "textarea", "form", "img", "picture",
    "video", "audio", "canvas", "link", "meta", "nav", "footer", "header",
    "dialog",
}
BLOCK_TAGS = {
    "p", "div", "section", "article", "main", "aside", "blockquote", "li",
    "ul", "ol", "dl", "dt", "dd", "table", "tr", "td", "th", "h1", "h2",
    "h3", "h4", "h5", "h6", "pre", "figure", "figcaption", "br", "hr",
}
HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 4, "h6": 4}

# Where the document body lives, tried in order, per site.
ROOTS = [
    "main",
    "[role=main]",
    "article",
    "#content",
    "body",
]

QUOTES = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"',
    " ": " ", " ": " ", " ": " ", "​": "",
    "–": "-", "—": " - ", "−": "-", "…": "...",
})

BOILERPLATE = [
    re.compile(r"^©\s*\d{4}"),
    re.compile(r"^(Log in|Sign up|Create new account|Forgot password\??)$", re.I),
]


def clean_text(s):
    s = unicodedata.normalize("NFKC", s).translate(QUOTES)
    return re.sub(r"\s+", " ", s).strip()


def pick_root(soup):
    for sel in ROOTS:
        el = soup.select_one(sel)
        if el and len(el.get_text(strip=True)) > 400:
            return el
    return soup


def walk(node, out):
    """Collect (kind, text) blocks in document order."""
    buf = []

    def flush(kind="p"):
        text = clean_text("".join(buf))
        buf.clear()
        if text:
            out.append((kind, text))

    for child in node.children:
        if isinstance(child, Comment):
            continue
        if isinstance(child, NavigableString):
            buf.append(str(child))
            continue
        if not isinstance(child, Tag):
            continue
        name = child.name
        if name in HEADINGS:
            flush()
            text = clean_text(child.get_text(" "))
            if text:
                out.append((f"h{HEADINGS[name]}", text))
        elif name == "li":
            flush()
            inner = []
            walk(child, inner)
            for i, (k, t) in enumerate(inner):
                out.append(("li" if i == 0 and k == "p" else k, t))
        elif name in BLOCK_TAGS:
            flush()
            walk(child, out)
        else:
            buf.append(child.get_text(" ") if name not in ("a", "span", "strong", "em", "b", "i", "u") else "")
            if name in ("a", "span", "strong", "em", "b", "i", "u", "sup", "sub", "code", "abbr", "time", "mark", "small"):
                buf[-1] = _inline_text(child)
    flush()


def _inline_text(tag):
    return tag.get_text(" ")


def extract(html_bytes):
    html = html_bytes.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "lxml")
    for c in soup.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()
    for t in soup.find_all(DROP_TAGS):
        t.decompose()
    for t in soup.select('[aria-hidden="true"], [hidden], [role=navigation], [role=banner], [role=contentinfo]'):
        t.decompose()
    root = pick_root(soup)
    blocks = []
    walk(root, blocks)
    lines = []
    prev = None
    for kind, text in blocks:
        if any(p.search(text) for p in BOILERPLATE):
            continue
        if text == prev:
            continue
        prev = text
        if kind.startswith("h"):
            lines.append("#" * int(kind[1]) + " " + text)
        elif kind == "li":
            lines.append("- " + text)
        else:
            lines.append(text)
    return "\n\n".join(lines) + "\n"
