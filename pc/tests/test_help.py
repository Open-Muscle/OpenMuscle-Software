"""In-app Help: the Markdown renderer, page discovery, and the /help routes.

The guides must be readable with no internet, so these check that the pages come
from disk (a docs folder or the copy packaged in the wheel), that nothing in a
page needs a network, and that a doc cannot make the server read other files.
"""

import re
from pathlib import Path

import pytest

from openmuscle.web import help as H
from openmuscle.web.app import create_app

from tests.asgi_helper import call

STATIC = Path(H.__file__).parent / "static"


def md(src, pages=("wifi-setup", "troubleshooting")):
    return H.render_markdown(src, pages)[0]


# ---- renderer -----------------------------------------------------------------

def test_headings_get_ids_and_duplicates_are_numbered():
    body, heads = H.render_markdown("# Hello, World!\n\n## Step\n\n## Step\n")
    assert '<h1 id="hello-world">Hello, World!</h1>' in body
    assert [h[2] for h in heads] == ["hello-world", "step", "step-1"]


def test_paragraph_inline_markup():
    out = md("Some **bold**, *soft*, _also soft_ and `code <b>` here.")
    assert "<strong>bold</strong>" in out
    assert out.count("<em>") == 2
    assert "<code>code &lt;b&gt;</code>" in out


def test_snake_case_words_are_not_italicised():
    assert "<em>" not in md("Use wifi_setup_page and file_name_here.")


def test_raw_html_is_escaped_and_comments_hidden():
    out = md("<script>alert(1)</script>\n\n<!-- authoring note -->\n\nAfter")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert "authoring note" not in out
    assert "<p>After</p>" in out


def test_fenced_code_is_verbatim():
    out = md("```powershell\npip install -e .\n# not a heading\n  indented **raw**\n```\n")
    assert '<pre><code class="lang-powershell">' in out
    assert "# not a heading" in out
    assert "<strong>" not in out
    assert "<h1" not in out


def test_unordered_list_and_nested_list():
    out = md("- one\n- two\n  - inner a\n  - inner b\n- three\n")
    assert out.count("<ul>") == 2
    assert out.count("<li>") == 5
    assert "<li>one</li>" in out


def test_ordered_list_with_code_block_stays_one_list():
    src = ("1. Open PowerShell.\n\n"
           "   ```\n   py -m venv .venv\n   ```\n\n"
           "2. Install it.\n3. Run it.\n")
    out = md(src)
    assert out.count("<ol>") == 1
    assert out.count("<li>") == 3
    assert "py -m venv .venv" in out
    assert out.index("py -m venv") < out.index("Install it.")


def test_ordered_list_start_number():
    assert '<ol start="4">' in md("4. four\n5. five\n")


def test_loose_list_items_become_paragraphs():
    out = md("1. First thought.\n\n   More about the first.\n\n2. Second.\n")
    assert "<li><p>First thought.</p>" in out


def test_table_with_alignment_and_inline_code():
    out = md("| Flag | Effect |\n|:--|--:|\n| `--lan` | Listen on **all** |\n")
    assert "<table>" in out and "<th" in out
    assert "<code>--lan</code>" in out
    assert "text-align:right" in out
    assert "<strong>all</strong>" in out


def test_blockquote_and_rule():
    out = md("> **Note** careful\n> second line\n\n---\n\nafter\n")
    assert "<blockquote>" in out and "<hr>" in out


def test_internal_page_links_are_rewritten():
    out = md("See [Wi-Fi](wifi-setup.md#password) and [x](../docs/troubleshooting.md).")
    assert 'href="/help/wifi-setup#password"' in out
    assert 'href="/help/troubleshooting"' in out
    assert "target=" not in out


def test_unknown_and_unsafe_links_are_not_clickable():
    out = md("[repo](../README.md) [bad](javascript:alert(1)) [src](../pc/src/x.py)")
    assert "<a " not in out
    assert out.count('class="nolink"') == 3
    assert "repo" in out


def test_external_links_open_in_new_tab():
    out = md("[site](https://example.org/a_b_c) and https://example.org/z.")
    assert out.count('target="_blank" rel="noopener noreferrer"') == 2
    assert 'href="https://example.org/a_b_c"' in out
    assert 'href="https://example.org/z"' in out


def test_bold_around_bare_url_does_not_swallow_asterisks():
    out = md("Open **http://localhost:8000** now")
    assert 'href="http://localhost:8000"' in out
    assert "<strong><a" in out


def test_images_only_from_the_docs_folder():
    out = md("![the LED](img/led.png) ![x](http://evil.example/p.png) ![y](../../etc/p.png)")
    assert '<img src="/help/files/img/led.png" alt="the LED"' in out
    assert out.count("<img") == 1


def test_stray_markers_never_leak():
    out = md("[`code` link](wifi-setup.md) and **[bold link](wifi-setup.md)**")
    assert "\x00" not in out
    assert out.count('href="/help/wifi-setup"') == 2


# ---- discovery and routes -----------------------------------------------------

@pytest.fixture
def docs(tmp_path, monkeypatch):
    d = tmp_path / "docs"
    d.mkdir()
    (d / "getting-started.md").write_text(
        "# Getting started\n\nGo to [Wi-Fi](wifi-setup.md).\n", encoding="utf-8")
    (d / "wifi-setup.md").write_text("# Join Wi-Fi\n\n1. Do it.\n2. Done.\n", encoding="utf-8")
    (d / "troubleshooting.md").write_text("# Troubleshooting\n\nText.\n", encoding="utf-8")
    (d / "zeta-notes.md").write_text("# Zeta notes\n", encoding="utf-8")
    (d / "README.md").write_text("# not a page\n", encoding="utf-8")
    (d / "notes.txt").write_text("NOPEDATA", encoding="utf-8")
    (d / "img").mkdir()
    (d / "img" / "led.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    (tmp_path / "secret.png").write_bytes(b"TOPSECRETDATA")
    monkeypatch.setenv("OPENMUSCLE_DOCS_DIR", str(d))
    return d


@pytest.fixture
def app(tmp_path):
    return create_app(udp_port=53971, captures_dir=str(tmp_path / "captures"))


def test_pages_are_discovered_and_ordered(docs):
    names = H.page_names(docs)
    assert "README" not in names and "notes" not in names
    guides, rest = H.ordered_pages(names)
    assert guides == ["getting-started", "wifi-setup", "troubleshooting"]
    assert rest == ["zeta-notes"]


def test_help_index_serves_getting_started(app, docs):
    status, headers, body = call(app, "GET", "/help")
    text = body.decode("utf-8")
    assert status == 200
    assert headers["content-type"].startswith("text/html")
    assert '<h1 id="getting-started">Getting started</h1>' in text
    assert 'href="/help/wifi-setup"' in text
    assert 'aria-current="page"' in text
    assert "no-store" in headers.get("cache-control", "")


def test_new_doc_appears_without_restart(app, docs):
    (docs / "band-fit.md").write_text("# Fit the band\n", encoding="utf-8")
    status, _h, body = call(app, "GET", "/help/band-fit")
    assert status == 200 and b"Fit the band" in body
    _s, _h, index = call(app, "GET", "/help")
    assert b'href="/help/band-fit"' in index


def test_pages_use_no_external_resources(app, docs):
    for path in ("/help", "/help/wifi-setup", "/help/troubleshooting"):
        _s, _h, body = call(app, "GET", path)
        text = body.decode("utf-8")
        assert not re.search(r'(src|href)="https?://', text)
        assert 'href="/static/styles.css"' in text and 'href="/static/help.css"' in text
        assert "\u2014" not in text


def test_unknown_page_is_404_with_the_page_list(app, docs):
    status, _h, body = call(app, "GET", "/help/nope")
    assert status == 404
    assert b'href="/help/wifi-setup"' in body


@pytest.mark.parametrize("path", [
    "/help/..%2f..%2fsecret", "/help/README", "/help/notes", "/help/%3Cscript%3E",
    "/help/files/../../secret.png", "/help/files/..%2fsecret.png",
    "/help/files/img/led.txt", "/help/files//etc/passwd",
])
def test_help_cannot_read_outside_the_docs_folder(app, docs, path):
    status, _h, body = call(app, "GET", path)
    assert status == 404
    assert b"TOPSECRETDATA" not in body and b"NOPEDATA" not in body


def test_help_images_are_served(app, docs):
    status, headers, body = call(app, "GET", "/help/files/img/led.png")
    assert status == 200
    assert headers["content-type"] == "image/png"
    assert body.startswith(b"\x89PNG")


def test_page_name_is_escaped_in_404(app, docs):
    status, _h, body = call(app, "GET", "/help/%3Cb%3Ex")
    assert status == 404
    assert b"<b>x" not in body


def test_missing_docs_gives_a_plain_message(app, monkeypatch):
    monkeypatch.setattr(H, "docs_dir", lambda: None)
    status, _h, body = call(app, "GET", "/help")
    assert status == 200 and b"not installed" in body
    assert call(app, "GET", "/help/anything")[0] == 404


def test_docs_dir_order_env_then_repo_then_package(tmp_path, monkeypatch):
    env_dir = tmp_path / "envdocs"
    env_dir.mkdir()
    monkeypatch.setenv("OPENMUSCLE_DOCS_DIR", str(env_dir))
    assert H.docs_dir() == env_dir

    # An installed copy: no repository around it, so the packaged folder is used.
    monkeypatch.delenv("OPENMUSCLE_DOCS_DIR")
    site = tmp_path / "site" / "lib" / "openmuscle" / "web"
    site.mkdir(parents=True)
    packaged = site / "help_docs"
    packaged.mkdir()
    monkeypatch.setattr(H, "__file__", str(site / "help.py"))
    monkeypatch.setattr(H, "PACKAGE_DOCS", packaged)
    assert H.docs_dir() == packaged
    packaged.rmdir()
    assert H.docs_dir() is None


def test_real_docs_all_render(app):
    """Whatever guides exist in this checkout render and keep their sidebar."""
    root = H.docs_dir()
    assert root is not None, "docs/ not found next to pc/"
    names = H.page_names(root)
    assert "webcam-labeling" in names
    for n in names:
        status, _h, body = call(app, "GET", "/help/" + n)
        text = body.decode("utf-8")
        assert status == 200, n
        assert "\x00" not in text, n
        assert "**" not in re.sub(r"<pre>.*?</pre>", "", text, flags=re.S), n
        assert not re.search(r'(src|href)="https?://(cdn|unpkg)', text), n


def test_build_hook_packages_the_docs():
    """pyproject must ship the copied guides, and setup.py must copy docs/."""
    pc = Path(__file__).resolve().parents[1]
    assert "help_docs" in (pc / "pyproject.toml").read_text(encoding="utf-8")
    hook = (pc / "setup.py").read_text(encoding="utf-8")
    assert "help_docs" in hook and "build_py" in hook and "sdist" in hook


# ---- the Studio links to it ---------------------------------------------------

def _read(*parts):
    return STATIC.joinpath(*parts).read_text(encoding="utf-8")


def test_dashboard_has_a_help_link_and_actionable_empty_states():
    html = _read("index.html")
    js = _read("app.js")
    assert re.search(r'<a href="/help"[^>]*class="help-link"', html)
    for text in (html, js):
        assert "/help/troubleshooting" in text
        assert "No devices found yet" not in text
    assert "Waiting for a flexgrid" not in _read("stream.html") + _read("stream.js")
    assert "Add a device by IP address" in html and "Add a device by IP address" in js
    assert "add source" not in html
    assert "Tory" not in html + js + _read("stream.html") + _read("stream.js")


def test_wizard_and_webcam_pages_link_to_help():
    assert "/help/wifi-setup" in _read("setup", "index.html")
    assert "/help/webcam-labeling" in _read("webcam", "index.html")
