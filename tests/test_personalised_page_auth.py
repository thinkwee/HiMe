"""
Regression tests for serving personalised pages when API auth is enabled.

Background
----------
``/api/personalised-pages/{id}/`` is behind ``BearerAuthMiddleware`` like every
other ``/api`` route, and an ``<iframe src>`` cannot send an Authorization
header. Rather than putting the token in the URL — where the sandboxed,
agent-generated page could read it straight out of ``location.search`` — the
dashboard fetches the markup with the header and injects it into the frame with
``srcdoc``.

A ``srcdoc`` document is not an HTTP response, so it never sees the
Content-Security-Policy *header*. These tests pin the two properties that make
that safe:

  * the same policy is also emitted as a ``<meta http-equiv>`` inside the
    markup, early enough in ``<head>`` to govern the page's own content;
  * the shared CSS/JS stay inlined, because a srcdoc page in an opaque origin
    cannot load a subresource by URL at all.
"""
from __future__ import annotations

import re

import pytest

from backend.api import page_routes

# =========================================================================
# _inject_head — where the meta tags land
# =========================================================================

class TestInjectHead:
    def test_inserts_immediately_after_head(self):
        html = "<html><head><title>t</title></head><body>x</body></html>"
        out = page_routes._inject_head(html, "<meta id=m>")
        assert out.index("<meta id=m>") < out.index("<title>")

    def test_case_insensitive_head_tag(self):
        out = page_routes._inject_head("<HTML><HEAD><title>t</title>", "<meta id=m>")
        assert "<meta id=m>" in out
        assert out.index("<meta id=m>") < out.index("<title>")

    def test_falls_back_to_after_the_html_tag(self):
        out = page_routes._inject_head('<html lang="en"><body>x</body></html>', "<meta id=m>")
        assert out.startswith('<html lang="en">')
        assert out.index("<meta id=m>") < out.index("<body>")

    def test_falls_back_to_the_very_front_for_a_bare_fragment(self):
        out = page_routes._inject_head("<div>hi</div>", "<meta id=m>")
        assert out.startswith("<meta id=m>")


# =========================================================================
# The meta policy mirrors the header policy
# =========================================================================

class TestMetaCspMirrorsHeader:
    def test_meta_and_header_agree_on_every_directive_but_frame_ancestors(self):
        header = {d.split(" ")[0]: d for d in page_routes._PAGE_CSP.split("; ")}
        meta = {d.split(" ")[0]: d for d in page_routes._PAGE_CSP_META.split("; ")}
        assert set(header) - set(meta) == {"frame-ancestors"}
        for name, directive in meta.items():
            assert directive == header[name], name

    def test_meta_drops_directives_browsers_ignore_in_a_meta_tag(self):
        # Both are ignored in a meta policy and log a console warning; keeping
        # them would be noise that also implies protection that is not there.
        assert "frame-ancestors" not in page_routes._PAGE_CSP_META
        assert "sandbox" not in page_routes._PAGE_CSP_META

    def test_meta_still_closes_the_exfiltration_directives(self):
        for directive in ("connect-src 'self'", "form-action 'self'", "base-uri 'none'"):
            assert directive in page_routes._PAGE_CSP_META

    def test_meta_content_needs_no_quote_escaping(self):
        # The policy is interpolated into content="…", so a double quote in it
        # would break out of the attribute.
        assert '"' not in page_routes._PAGE_CSP_META


# =========================================================================
# The served document
# =========================================================================

_PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <link rel="stylesheet" href="/api/personalised-pages/_shared/hime-ui.css">
  <script src="/api/personalised-pages/_shared/hime-ui.js"></script>
</head>
<body><div class="hime-page"></div><script>HimeUI.fetchData('p1')</script></body>
</html>
"""


@pytest.fixture
async def served_page(tmp_path, monkeypatch):
    """Serve one page out of a temporary pages directory."""
    pages = tmp_path / "personalised_pages"
    shared = pages / "_shared"
    shared.mkdir(parents=True)
    (shared / "hime-ui.css").write_text(".hime-page{padding:0}", encoding="utf-8")
    (shared / "hime-ui.js").write_text("window.HimeUI={};", encoding="utf-8")
    (pages / "p1").mkdir()
    (pages / "p1" / "index.html").write_text(_PAGE_HTML, encoding="utf-8")

    monkeypatch.setattr(page_routes, "_PAGES_DIR", pages)
    monkeypatch.setattr(page_routes, "_SHARED_DIR", shared)
    # Invalidate the module-level asset caches for this temp directory.
    monkeypatch.setattr(page_routes, "_shared_assets_mtime", -1.0)
    monkeypatch.setattr(page_routes, "_shared_version_mtime", 0.0)

    return await page_routes.serve_page_frontend("p1")


class TestServedDocument:
    def test_csp_header_is_still_set(self, served_page):
        assert served_page.headers["content-security-policy"] == page_routes._PAGE_CSP

    def test_body_carries_the_meta_csp(self, served_page):
        body = served_page.body.decode()
        m = re.search(
            r'<meta http-equiv="Content-Security-Policy" content="([^"]+)">', body
        )
        assert m, "no meta CSP in the served markup"
        assert m.group(1) == page_routes._PAGE_CSP_META

    def test_meta_csp_precedes_the_pages_own_script(self, served_page):
        body = served_page.body.decode()
        assert body.index("Content-Security-Policy") < body.index("HimeUI.fetchData")

    def test_meta_csp_precedes_any_inlined_asset(self, served_page):
        # The inlined <style>/<script> are content too — the policy has to be
        # in front of them, not merely somewhere in <head>.
        body = served_page.body.decode()
        assert body.index("Content-Security-Policy") < body.index("<style>")

    def test_referrer_policy_travels_in_the_markup_too(self, served_page):
        assert '<meta name="referrer" content="no-referrer">' in served_page.body.decode()

    def test_shared_assets_are_inlined_not_linked(self, served_page):
        # A srcdoc document in an opaque origin cannot load ANY subresource by
        # URL, in any engine — so neither tag may survive as a reference.
        body = served_page.body.decode()
        assert "window.HimeUI={};" in body
        assert ".hime-page{padding:0}" in body
        assert '<link rel="stylesheet"' not in body
        assert "<script src=" not in body

    def test_charset_declaration_stays_within_the_first_1024_bytes(self, served_page):
        # HTML requires it there; the injected metas push it back, so pin it.
        assert served_page.body.decode().index("<meta charset=") < 1024
