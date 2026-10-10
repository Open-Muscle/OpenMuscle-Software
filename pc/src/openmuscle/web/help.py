"""In-app Help: the guides in docs/*.md, rendered to HTML and served at /help.

The band's own Wi-Fi has no internet, so the guide has to be readable from the
installed package. Pages are discovered from a docs folder at run time, so a new
docs/<name>.md appears in the sidebar with no further wiring. The folder is, in
order: $OPENMUSCLE_DOCS_DIR, the repository's docs/ (a clone or an editable
install), or the copy packaged inside the wheel (web/help_docs, filled in at
build time by setup.py).

The Markdown converter is deliberately small (headings, paragraphs, lists,
fenced code, tables, quotes, rules, links, images, emphasis, inline code) so the
package gains no dependency. Everything is HTML-escaped; raw HTML in a doc is
shown as text, and only http(s) and mailto links stay clickable.
"""

import html
import os
import posixpath
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

PACKAGE_DOCS = Path(__file__).parent / "help_docs"

# Reading order for the guides a new user needs. Anything else in the folder is
# listed after them as reference material, alphabetically.
GUIDE_ORDER = [
    "getting-started", "install", "wifi-setup", "studio-tour", "band-fit",
    "webcam-labeling", "first-model", "your-data", "troubleshooting",
    "glossary", "device-reference",
]
DEFAULT_PAGE = "getting-started"
IMAGE_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".svg": "image/svg+xml", ".webp": "image/webp",
}
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


# ---------------------------------------------------------------- locating docs

def docs_dir() -> Optional[Path]:
    """The folder holding the guides, or None when this copy has none."""
    env = os.environ.get("OPENMUSCLE_DOCS_DIR")
    if env and Path(env).is_dir():
        return Path(env)
    here = Path(__file__).resolve()
    try:
        pc_root, repo_root = here.parents[3], here.parents[4]
        if (pc_root / "pyproject.toml").is_file() and (repo_root / "docs").is_dir():
            return repo_root / "docs"
    except IndexError:
        pass
    if PACKAGE_DOCS.is_dir():
        return PACKAGE_DOCS
    return None


def page_names(root: Optional[Path]) -> List[str]:
    """Stems of the .md files in the docs folder (README excluded), unordered."""
    if root is None:
        return []
    out = []
    for p in root.glob("*.md"):
        if p.is_file() and _NAME_RE.match(p.stem) and p.stem.lower() != "readme":
            out.append(p.stem)
    return out


def ordered_pages(names: List[str]) -> Tuple[List[str], List[str]]:
    """-> (guides in reading order, reference pages alphabetically)."""
    have = set(names)
    guides = [n for n in GUIDE_ORDER if n in have]
    rest = sorted(have - set(guides))
    return guides, rest


# ------------------------------------------------------------------- inline text

_CODE_RE = re.compile(r"(`+)(.+?)\1", re.S)
_IMAGE_RE = re.compile(r'!\[([^\]]*)\]\(\s*([^)\s]+)(?:\s+"[^"]*")?\s*\)')
_LINK_RE = re.compile(r'\[([^\]]+)\]\(\s*([^)\s]+)(?:\s+"[^"]*")?\s*\)')
_AUTOLINK_RE = re.compile(r"&lt;(https?://[^\s&]+)&gt;")
_BARE_URL_RE = re.compile(
    r"(?<![\"'>=\w\x00])(https?://[^\s<>&\"')\]*\x00]+[^\s<>&\"')\]*_\x00\.,;:!?])")
_BOLD_RE = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1", re.S)
_ITAL_STAR_RE = re.compile(r"(?<![\*\w])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\*\w])", re.S)
_ITAL_UND_RE = re.compile(r"(?<![_\w])_(?=[^\s_])(.+?)(?<=[^\s_])_(?![_\w])", re.S)
_STASH = "\x00%d\x00"


def slugify(text: str) -> str:
    """GitHub-style heading anchor: lower case, punctuation dropped, spaces to -."""
    s = re.sub(r"<[^>]*>", "", text).lower()
    s = re.sub(r"[^\w\- ]", "", s)
    return re.sub(r"\s", "-", s.strip())


def resolve_href(url: str, pages: set, subdir: str = "") -> Tuple[str, str]:
    """Map a Markdown link target to (href, kind).

    kind is "page" (another guide), "anchor", "external", "file" (an image or
    file under the docs folder) or "dead" (cannot be followed offline; the
    caller renders the text without a link).
    """
    url = url.strip()
    if url.startswith("#"):
        return url, "anchor"
    low = url.lower()
    if low.startswith(("http://", "https://")):
        return url, "external"
    if low.startswith("mailto:"):
        return url, "external"
    if re.match(r"^[a-z][a-z0-9+.-]*:", low) or url.startswith("//"):
        return "", "dead"                      # javascript:, data:, file:, ...
    path, _, frag = url.partition("#")
    path = path.split("?")[0]
    if not path:
        return "", "dead"
    norm = posixpath.normpath(posixpath.join(subdir, path))
    stem = posixpath.basename(norm)
    suffix = ("#" + frag) if frag else ""
    if stem.lower().endswith(".md"):
        name = stem[:-3]
        if name in pages:
            return "/help/" + name + suffix, "page"
        return "", "dead"
    if posixpath.splitext(norm)[1].lower() in IMAGE_TYPES and not norm.startswith(".."):
        return "/help/files/" + norm.lstrip("/"), "file"
    return "", "dead"


def render_inline(text: str, pages: set, subdir: str = "") -> str:
    stash: List[str] = []

    def keep(fragment: str) -> str:
        stash.append(fragment)
        return _STASH % (len(stash) - 1)

    def code(m):
        return keep("<code>" + html.escape(m.group(2).strip(), quote=False) + "</code>")

    text = _CODE_RE.sub(code, text)
    text = html.escape(text, quote=False)

    def image(m):
        href, kind = resolve_href(html.unescape(m.group(2)), pages, subdir)
        alt = html.escape(html.unescape(m.group(1)), quote=True)
        if kind == "file":
            return keep('<img src="%s" alt="%s" loading="lazy">' % (html.escape(href, quote=True), alt))
        return keep('<span class="nolink">%s</span>' % (alt or "image"))

    text = _IMAGE_RE.sub(image, text)

    def link(m):
        href, kind = resolve_href(html.unescape(m.group(2)), pages, subdir)
        if kind == "dead":
            return keep('<span class="nolink">') + m.group(1) + keep("</span>")
        extra = ' target="_blank" rel="noopener noreferrer"' if kind == "external" else ""
        return keep('<a href="%s"%s>' % (html.escape(href, quote=True), extra))             + m.group(1) + keep("</a>")

    text = _LINK_RE.sub(link, text)

    def autolink(m):
        u = html.unescape(m.group(1))
        return keep('<a href="%s" target="_blank" rel="noopener noreferrer">%s</a>'
                    % (html.escape(u, quote=True), html.escape(u, quote=False)))

    text = _AUTOLINK_RE.sub(autolink, text)
    text = _BARE_URL_RE.sub(autolink, text)
    text = _BOLD_RE.sub(lambda m: "<strong>%s</strong>" % m.group(2), text)
    text = _ITAL_STAR_RE.sub(lambda m: "<em>%s</em>" % m.group(1), text)
    text = _ITAL_UND_RE.sub(lambda m: "<em>%s</em>" % m.group(1), text)

    # Stashed fragments can themselves contain stash markers (a link around
    # code); resolve until none are left.
    for _ in range(4):
        new = re.sub("\x00(\\d+)\x00", lambda m: stash[int(m.group(1))], text)
        if new == text:
            break
        text = new
    return text


# ------------------------------------------------------------------- block text

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^(\s*)(```+|~~~+)\s*([\w+-]*)")
_RULE_RE = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
_ULI_RE = re.compile(r"^(\s*)([-*+])\s+(.*)$")
_OLI_RE = re.compile(r"^(\s*)(\d{1,3})[.)]\s+(.*)$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def _split_row(line: str) -> List[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", line)]


def _is_block_start(line: str) -> bool:
    return bool(_HEADING_RE.match(line) or _FENCE_RE.match(line) or _RULE_RE.match(line)
                or _ULI_RE.match(line) or _OLI_RE.match(line)
                or line.lstrip().startswith(">") or line.lstrip().startswith("<!--"))


class Renderer:
    def __init__(self, pages, subdir: str = ""):
        self.pages = set(pages)
        self.subdir = subdir
        self.headings: List[Tuple[int, str, str]] = []   # (level, text, id)
        self._ids: Dict[str, int] = {}

    def inline(self, text: str) -> str:
        return render_inline(text, self.pages, self.subdir)

    def _heading_id(self, text: str) -> str:
        base = slugify(text) or "section"
        n = self._ids.get(base, 0)
        self._ids[base] = n + 1
        return base if n == 0 else "%s-%d" % (base, n)

    def blocks(self, lines: List[str]) -> str:
        out: List[str] = []
        i, n = 0, len(lines)
        while i < n:
            line = lines[i].rstrip("\r\n")
            if not line.strip():
                i += 1
                continue

            if line.lstrip().startswith("<!--"):          # authoring note, never shown
                while i < n and "-->" not in lines[i]:
                    i += 1
                i += 1
                continue

            m = _FENCE_RE.match(line)
            if m:
                indent, fence, lang = len(m.group(1)), m.group(2), m.group(3)
                body: List[str] = []
                i += 1
                while i < n and not lines[i].strip().startswith(fence[:3]):
                    body.append(lines[i].rstrip("\r\n")[indent:] if lines[i][:indent].strip() == ""
                                else lines[i].rstrip("\r\n"))
                    i += 1
                i += 1
                cls = ' class="lang-%s"' % html.escape(lang, quote=True) if lang else ""
                out.append("<pre><code%s>%s</code></pre>"
                           % (cls, html.escape("\n".join(body), quote=False)))
                continue

            m = _HEADING_RE.match(line)
            if m:
                level, text = len(m.group(1)), m.group(2)
                hid = self._heading_id(re.sub(r"[`*_]", "", text))
                self.headings.append((level, re.sub(r"[`*_]", "", text), hid))
                out.append('<h%d id="%s">%s</h%d>' % (level, hid, self.inline(text), level))
                i += 1
                continue

            if _RULE_RE.match(line):
                out.append("<hr>")
                i += 1
                continue

            if line.lstrip().startswith(">"):
                quote: List[str] = []
                while i < n and lines[i].lstrip().startswith(">"):
                    quote.append(re.sub(r"^\s*>\s?", "", lines[i].rstrip("\r\n")))
                    i += 1
                out.append("<blockquote>%s</blockquote>" % self.blocks(quote))
                continue

            if "|" in line and i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1]) and "-" in lines[i + 1]:
                head = _split_row(line)
                aligns = []
                for c in _split_row(lines[i + 1]):
                    aligns.append("center" if c.startswith(":") and c.endswith(":")
                                  else "right" if c.endswith(":") else "")
                i += 2
                rows = []
                while i < n and lines[i].strip() and "|" in lines[i]:
                    rows.append(_split_row(lines[i]))
                    i += 1

                def cell(tag, text, k):
                    a = aligns[k] if k < len(aligns) and aligns[k] else ""
                    return "<%s%s>%s</%s>" % (tag, ' style="text-align:%s"' % a if a else "",
                                              self.inline(text), tag)

                html_rows = ["<thead><tr>%s</tr></thead>"
                             % "".join(cell("th", c, k) for k, c in enumerate(head))]
                body_rows = ["<tr>%s</tr>" % "".join(cell("td", c, k) for k, c in enumerate(r))
                             for r in rows]
                out.append('<div class="table-wrap"><table>%s<tbody>%s</tbody></table></div>'
                           % (html_rows[0], "".join(body_rows)))
                continue

            if _ULI_RE.match(line) or _OLI_RE.match(line):
                chunk, i = self._list(lines, i)
                out.append(chunk)
                continue

            para = [line.strip()]
            br = line.endswith("  ") or line.endswith("\\")
            i += 1
            while i < n and lines[i].strip() and not _is_block_start(lines[i]) \
                    and not ("|" in lines[i] and i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1])):
                para.append(lines[i].strip())
                i += 1
            joined = " ".join(p.rstrip("\\").rstrip() for p in para)
            if br and len(para) > 1:
                joined = "<br>".join(self.inline(p.rstrip("\\").rstrip()) for p in para)
                out.append("<p>%s</p>" % joined)
            else:
                out.append("<p>%s</p>" % self.inline(joined))
        return "\n".join(out)

    def _list(self, lines: List[str], i: int) -> Tuple[str, int]:
        n = len(lines)
        first = lines[i]
        ordered = bool(_OLI_RE.match(first)) and not _ULI_RE.match(first)
        marker_re = _OLI_RE if ordered else _ULI_RE
        base_indent = len(first) - len(first.lstrip())
        start = int(_OLI_RE.match(first).group(2)) if ordered else 1
        items: List[str] = []

        def indent_of(s):
            return len(s) - len(s.lstrip())

        while i < n:
            line = lines[i].rstrip("\r\n")
            m = marker_re.match(line)
            if not m or len(m.group(1)) != base_indent:
                break
            content_col = len(line) - len(m.group(3))
            body = [m.group(3)]
            i += 1
            loose = False
            while i < n:
                nxt = lines[i].rstrip("\r\n")
                if not nxt.strip():
                    j = i
                    while j < n and not lines[j].strip():
                        j += 1
                    if j < n and indent_of(lines[j]) > base_indent:
                        sub = lines[j].strip()
                        if not (_FENCE_RE.match(lines[j]) or _ULI_RE.match(lines[j])
                                or _OLI_RE.match(lines[j]) or sub.startswith("|")):
                            loose = True
                        body.extend([""] * (j - i))
                        i = j
                        continue
                    break
                ind = indent_of(nxt)
                if (_ULI_RE.match(nxt) or _OLI_RE.match(nxt)) and ind <= base_indent:
                    break
                if ind > base_indent:
                    body.append(nxt[min(ind, content_col):])
                    i += 1
                    continue
                if body and body[-1].strip() and not _is_block_start(nxt):
                    body.append(nxt.strip())          # lazy continuation line
                    i += 1
                    continue
                break
            inner = self.blocks(body)
            if not loose and inner.startswith("<p>"):
                end = inner.find("</p>")
                inner = inner[3:end] + inner[end + 4:]
            items.append("<li>%s</li>" % inner)
            j = i
            while j < n and not lines[j].strip():
                j += 1
            if j > i and j < n:
                mm = marker_re.match(lines[j])
                if mm and len(mm.group(1)) == base_indent:
                    i = j
        tag = "ol" if ordered else "ul"
        attr = ' start="%d"' % start if ordered and start != 1 else ""
        return "<%s%s>%s</%s>" % (tag, attr, "".join(items), tag), i


def render_markdown(source: str, pages=(), subdir: str = "") -> Tuple[str, List[Tuple[int, str, str]]]:
    """-> (HTML body, headings as (level, text, id))."""
    r = Renderer(pages, subdir)
    body = r.blocks(source.replace("\r\n", "\n").replace("\r", "\n").split("\n"))
    return body, r.headings


# ------------------------------------------------------------------- page chrome

_title_cache: Dict[Tuple[str, int, int], str] = {}


def page_title(root: Path, name: str) -> str:
    """The first heading of docs/<name>.md, else the file name made readable."""
    p = root / (name + ".md")
    try:
        st = p.stat()
        key = (str(p), st.st_mtime_ns, st.st_size)
        if key in _title_cache:
            return _title_cache[key]
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            m = _HEADING_RE.match(line)
            if m:
                title = re.sub(r"[`*_]", "", m.group(2)).strip()
                _title_cache[key] = title
                return title
    except OSError:
        pass
    return name.replace("-", " ").replace("_", " ").capitalize()


def _sidebar(root: Path, names: List[str], current: str) -> str:
    guides, rest = ordered_pages(names)

    def group(title, items):
        if not items:
            return ""
        lis = []
        for n in items:
            cur = ' class="current" aria-current="page"' if n == current else ""
            lis.append('<li><a href="/help/%s"%s>%s</a></li>'
                       % (html.escape(n, quote=True), cur,
                          html.escape(page_title(root, n), quote=False)))
        return '<h2>%s</h2><ul>%s</ul>' % (html.escape(title), "".join(lis))

    return group("Guides", guides) + group("Reference", rest)


def wrap_page(title: str, sidebar: str, body: str) -> str:
    return (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<title>OpenMuscle Help: %s</title>\n'
        '<link rel="stylesheet" href="/static/styles.css">\n'
        '<link rel="stylesheet" href="/static/help.css">\n'
        '</head>\n<body class="help">\n'
        '<header class="help-top">\n'
        '  <a class="help-brand" href="/help">OpenMuscle <span>Help</span></a>\n'
        '  <span class="help-top-links">'
        '<a href="/setup">Set up a device</a>'
        '<a href="/">Back to the Studio</a></span>\n'
        '</header>\n'
        '<div class="help-layout">\n'
        '<nav class="help-nav" aria-label="Help pages">%s</nav>\n'
        '<main class="help-page">\n%s\n'
        '<p class="help-offline">These pages are stored on this computer, so they work '
        'without an internet connection.</p>\n'
        '</main>\n</div>\n</body>\n</html>\n'
        % (html.escape(title, quote=False), sidebar, body))


def render_page(name: Optional[str]) -> Tuple[int, str]:
    """-> (HTTP status, HTML) for /help (name None) or /help/<name>."""
    root = docs_dir()
    names = page_names(root)
    if not names:
        body = ("<h1>Help is not installed with this copy</h1>"
                "<p>The guide pages were not found. If you installed OpenMuscle from a "
                "downloaded folder, the guides are in its <code>docs</code> folder. "
                "Reinstalling from the complete download adds them to this window.</p>")
        return (200 if name is None else 404), wrap_page("Not found", "", body)
    if name is None:
        name = DEFAULT_PAGE if DEFAULT_PAGE in names else ordered_pages(names)[0][0] \
            if ordered_pages(names)[0] else sorted(names)[0]
    if name not in names:
        body = ('<h1>Page not found</h1><p>There is no help page called '
                '<code>%s</code>. Pick one from the list on the left.</p>'
                % html.escape(name[:80], quote=False))
        return 404, wrap_page("Not found", _sidebar(root, names, ""), body)
    try:
        source = (root / (name + ".md")).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 404, wrap_page("Not found", _sidebar(root, names, ""),
                              "<h1>Page not found</h1>")
    body, _ = render_markdown(source, names)
    return 200, wrap_page(page_title(root, name), _sidebar(root, names, name), body)


def find_file(rel: str) -> Optional[Path]:
    """An image under the docs folder, or None. Never leaves the folder."""
    root = docs_dir()
    if root is None or not rel or "\\" in rel or "\x00" in rel:
        return None
    if posixpath.splitext(rel)[1].lower() not in IMAGE_TYPES:
        return None
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return None
    p = (root / Path(*parts)).resolve()
    try:
        p.relative_to(root.resolve())
    except ValueError:
        return None
    return p if p.is_file() else None
