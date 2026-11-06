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

# Where the document body lives: the biggest of these wins.
ROOTS = ["[role=main]", "main", "article", "#content"]

QUOTES = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"',
    " ": " ", " ": " ", " ": " ", "​": "",
    "–": "-", "—": " - ", "−": "-", "…": "...",
})

BOILERPLATE = [
    re.compile(r"^©\s*\d{4}"),
    re.compile(r"^(Log in|Sign up|Create new account|Forgot password\??)$", re.I),
    re.compile(r"^(Copy for LLM|View as Markdown|Back to top|Was this helpful\??|Yes|No)$", re.I),
]
# Facebook's footer starts with a list of languages, then a run of links
# such as "Sign UpLog InMessenger...". Everything from there on is dropped.
FOOTER_START = re.compile(r"^(English \(US\)|English \(UK\)|Sign Up ?Log In|Log In ?Sign Up)")


def clean_text(s):
    s = unicodedata.normalize("NFKC", s).translate(QUOTES)
    return re.sub(r"\s+", " ", s).strip()


def pick_root(soup):
    best, size = None, 0
    for sel in ROOTS:
        for el in soup.select(sel):
            n = len(el.get_text(strip=True))
            if n > size:
                best, size = el, n
    if best is not None and size > 400:
        return best
    return soup.body or soup


HTML_BLOB = re.compile(r'"__html":("(?:[^"\\]|\\.)*")')


def embedded_html(raw):
    """Some Facebook pages ship the document as escaped HTML inside a
    script, for the browser to insert. Return the biggest such chunk."""
    import json
    best = ""
    for m in HTML_BLOB.finditer(raw):
        if len(m.group(1)) < 2000:
            continue
        try:
            frag = json.loads(m.group(1))
        except ValueError:
            continue
        if len(frag) > len(best) and ("role=\"main\"" in m.group(1) or len(frag) > 20000):
            best = frag
    return best


# ---- documents shipped as a JSON tree (Instagram and Facebook help centres,
# some Facebook legal pages): {"type": ..., "props": ..., "children": [...]}

CMS_BLOCK = re.compile(r"(Paragraph|PTag|Heading|H[1-6]Tag|Header|Title|LiTag|ListItem|Galaxy(LI|UL|OL)$|UlTag|OlTag|BoxTag|GalaxyBox|DivTag|TableRow|TrTag|Callout|Answer$)")
CMS_HEAD = re.compile(r"(Heading|H([1-6])Tag|Header|Title)")
CMS_LI = re.compile(r"(LiTag|ListItem|GalaxyLI$)")
CMS_BR = re.compile(r"(BrTagView|GalaxyBR)$")


def cms_blocks(node, out, buf):
    """Flatten a JSON document tree into (kind, text) blocks."""
    if isinstance(node, str):
        buf.append(node)
        return
    if isinstance(node, list):
        for n in node:
            cms_blocks(n, out, buf)
        return
    if not isinstance(node, dict):
        return
    t = str(node.get("type", ""))
    kids = node.get("children") or []
    if CMS_BR.search(t):
        _flush(buf, out)
        return
    if not CMS_BLOCK.search(t):
        cms_blocks(kids, out, buf)
        return
    _flush(buf, out)
    inner, ibuf = [], []
    cms_blocks(kids, inner, ibuf)
    _flush(ibuf, inner)
    h = CMS_HEAD.search(t)
    for i, (k, txt) in enumerate(inner):
        if i == 0 and k == "p" and h:
            level = int(h.group(2)) if h.group(2) else 2
            out.append((f"h{min(level, 4)}", txt))
        elif i == 0 and k == "p" and CMS_LI.search(t):
            out.append(("li", txt))
        else:
            out.append((k, txt))


def _flush(buf, out):
    text = clean_text("".join(buf))
    buf.clear()
    if text:
        out.append(("p", text))


def cms_documents(raw):
    """Find JSON-encoded document trees inside the page's scripts."""
    import json
    docs = []
    for m in re.finditer(r'"json_cms_content":("(?:[^"\\]|\\.)*")', raw):
        try:
            docs.append(json.loads(json.loads(m.group(1))))
        except ValueError:
            continue
    return docs


def cms_text_blocks(raw):
    best = []
    for doc in cms_documents(raw):
        out, buf = [], []
        cms_blocks(doc, out, buf)
        _flush(buf, out)
        if sum(len(t) for _, t in out) > sum(len(t) for _, t in best):
            best = out
    return best


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


def clean_soup(html):
    soup = BeautifulSoup(html, "lxml")
    for c in soup.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()
    for t in soup.find_all(DROP_TAGS):
        t.decompose()
    # Not [hidden]: Facebook streams the real page into hidden blocks.
    for t in soup.select('[aria-hidden="true"], [role=navigation], [role=banner], [role=contentinfo]'):
        t.decompose()
    return soup


def extract(html_bytes):
    html = html_bytes.decode("utf-8", errors="replace")
    soup = clean_soup(html)
    root = pick_root(soup)
    if len(root.get_text(strip=True)) < 1500:
        frag = embedded_html(html)
        if frag:
            soup = clean_soup(frag)
            root = pick_root(soup)
    blocks = []
    walk(root, blocks)
    if sum(len(t) for _, t in blocks) < 1500:
        cms = cms_text_blocks(html)
        if sum(len(t) for _, t in cms) > sum(len(t) for _, t in blocks):
            blocks = cms
    # Cut the footer, if it comes in the last third of the page.
    for i, (kind, text) in enumerate(blocks):
        if i > len(blocks) * 0.66 and FOOTER_START.match(text):
            blocks = blocks[:i]
            break
    # Drop a run of short links before the main heading (breadcrumbs).
    first_h1 = next((i for i, (k, _) in enumerate(blocks) if k == "h1"), None)
    if first_h1 and first_h1 <= 8 and all(len(t) < 40 for _, t in blocks[:first_h1]):
        blocks = blocks[first_h1:]
    # Drop a run of short lines at the very end (footer links).
    while blocks and len(blocks[-1][1]) < 30 and not blocks[-1][0].startswith("h") and not blocks[-1][1].endswith("."):
        blocks.pop()
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


COMMON = {"the", "and", "of", "to", "you", "your", "we", "our", "or", "in",
          "for", "a", "is", "that", "with", "this", "are", "be", "on", "not",
          "may", "if", "as", "by", "any", "it", "an", "use", "us"}


def is_english(text):
    """The archive's crawlers sometimes get a page in another language.
    Compare like with like: only English copies count."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    if sum(1 for c in letters if ord(c) > 127) / len(letters) > 0.15:
        return False
    words = re.findall(r"[a-z]+", text.lower())
    if len(words) < 50:
        return False
    return sum(1 for w in words if w in COMMON) / len(words) > 0.12
