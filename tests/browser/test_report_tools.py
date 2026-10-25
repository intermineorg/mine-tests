"""A gene report page in a real browser: every configured BlueGenes tool
draws something, no tool shows an error, and the tools' network calls
(RCSB, the aligner, JBrowse) succeed.

The page is loaded once per mine and browser (module-scoped fixture); the
tests then inspect what was captured.  Run with --browser chromium
--browser firefox [--browser webkit] to cover several engines."""
import re
import time
from types import SimpleNamespace

import pytest

from conftest import feature

PAGE_TIMEOUT_MS = 90_000
SETTLE_MS = 90_000
# JBrowse 1 probes for an optional tracks.conf next to trackList.json; InterMine
# does not serve one and JBrowse carries on, so that 404 is expected.
BENIGN_404 = ("/tracks.conf",)
# Tools that draw with WebGL.  Some headless browsers have no WebGL context;
# those tools are then reported as skipped rather than broken.
WEBGL_TOOLS = {"bluegenesProteinVisualizer"}
WEBGL_ERROR = re.compile(r"createBuffer|WebGL|getContext", re.I)
ERROR_TEXT = re.compile(r"Failed to|not defined|is not a function|Something went wrong|try again later", re.I)

# A tool panel counts as rendered once it holds a drawing surface or an
# embedded document; plain text alone is usually an error or a placeholder.
JS_RENDERED = """
(classes) => classes.every(cls => {
  const el = document.querySelector('.' + cls);
  return el && el.querySelector('canvas, svg, iframe, table, select, img');
})
"""
JS_TOOL_PANELS = """
() => {
  const out = {};
  for (const el of document.querySelectorAll('[class]')) {
    const cls = [...el.classList].find(c => /^blu(e)?genes/i.test(c));
    if (!cls || out[cls]) continue;
    out[cls] = {
      rendered: !!el.querySelector('canvas, svg, iframe, table, select, img'),
      text: el.textContent.replace(/\\s+/g, ' ').trim().slice(0, 200),
    };
  }
  return out;
}
"""


@pytest.fixture(scope="module")
def report(browser, browser_context_args, mine):
    ctx = browser.new_context(**browser_context_args)
    page = ctx.new_page()
    cap = SimpleNamespace(mine=mine, page=page, errors=[], console_errors=[], responses=[], failed=[], bad_posts=[])
    page.on("pageerror", lambda e: cap.errors.append(str(e)))
    page.on("console", lambda m: cap.console_errors.append(m.text) if m.type == "error" else None)
    page.on("response", lambda r: cap.responses.append((r.request.method, r.status, r.url)))
    # keep the body of failed POSTs (InterMine queries) so a failure message says which query broke
    page.on("response", lambda r: cap.bad_posts.append((r.url, (r.request.post_data or "")[:400]))
            if r.status >= 400 and r.request.method == "POST" else None)
    page.on("requestfailed", lambda r: cap.failed.append((r.url, r.failure)))
    page.goto(mine.report("Gene", mine.gene["id"]), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
    expected = list(mine.tools.get("rendered", []))
    try:
        for cls in expected:
            page.wait_for_selector(f".{cls}", state="attached", timeout=PAGE_TIMEOUT_MS)
        page.wait_for_function(JS_RENDERED, arg=expected, timeout=SETTLE_MS)
    except Exception:
        pass  # the tests below report precisely what is missing
    time.sleep(3)  # let late network calls (aligner, RCSB) finish
    cap.panels = page.evaluate(JS_TOOL_PANELS)
    cap.webgl = page.evaluate("(() => { try { return !!document.createElement('canvas').getContext('webgl') } catch (e) { return false } })()")
    yield cap
    ctx.close()


def test_imjs_is_available(report):
    assert report.page.evaluate("typeof imjs") == "object", "window.imjs missing: every tool-store tool will fail"


def test_expected_tools_present(report):
    missing = [cls for cls in report.mine.tools.get("rendered", []) if cls not in report.panels]
    assert not missing, f"tool panels absent from the page: {missing}; present: {sorted(report.panels)}"


def test_expected_tools_render(report):
    expected = set(report.mine.tools.get("rendered", []))
    skipped = set()
    if not report.webgl:
        skipped = expected & WEBGL_TOOLS
        expected -= skipped
    not_drawn = {cls: p["text"] for cls, p in report.panels.items() if cls in expected and not p["rendered"]}
    assert not not_drawn, f"tools did not draw anything: {not_drawn}"
    if skipped:
        pytest.skip(f"no WebGL in this browser; not checked: {sorted(skipped)} (the rest rendered)")


def test_no_tool_shows_error_text(report):
    bad = {cls: p["text"] for cls, p in report.panels.items() if ERROR_TEXT.search(p["text"])}
    assert not bad, f"tool panels showing an error: {bad}"


def test_no_uncaught_exceptions(report):
    errors = report.errors
    if not report.webgl:
        errors = [e for e in errors if not WEBGL_ERROR.search(e)]
    assert not errors, "uncaught exceptions on the report page:\n" + "\n".join(errors)


def test_no_failed_requests_to_the_mine(report):
    mine_url = report.mine.url
    bad = [(u, f) for u, f in report.failed if u.startswith(mine_url) and "ABORTED" not in str(f).upper()]
    assert not bad, f"requests to the mine failed: {bad}"
    errors = [(m, s, u) for m, s, u in report.responses
              if u.startswith(mine_url) and s >= 400 and not u.split("?")[0].endswith(BENIGN_404)]
    assert not errors, f"error responses from the mine: {errors}; failed POST bodies: {report.bad_posts}"


def test_page_scripts_load(report):
    """Scripts BlueGenes loads from CDNs (jQuery, Bootstrap, Vega for the
    heatmap tools).  If one is blocked, the tools that need it fail with
    errors that look like tool bugs; check this first."""
    srcs = report.page.evaluate("[...document.scripts].map(s => s.src).filter(Boolean)")
    status = {u.split("?")[0]: s for _, s, u in report.responses}
    failed = {u.split("?")[0]: str(f) for u, f in report.failed}
    bad = {}
    for src in srcs:
        key = src.split("?")[0]
        if key in failed:
            bad[src] = failed[key]
        elif status.get(key, 200) >= 400:
            bad[src] = status[key]
    assert not bad, f"page scripts that did not load: {bad}"


def test_protein_visualizer_uses_rcsb_v2(report):
    feature(report.mine, "gene", "expect")
    if not report.mine.gene["expect"].get("rcsb"):
        pytest.skip("no RCSB call expected for this gene")
    v1 = [u for _, _, u in report.responses if "rcsbsearch/v1" in u]
    assert not v1, "protein visualizer still calls the retired RCSB v1 API"
    v2 = [(s, u) for _, s, u in report.responses if "rcsbsearch/v2" in u]
    assert v2, "no RCSB v2 search request was made"
    assert all(s == 200 for s, _ in v2), f"RCSB v2 responses: {v2}"
    pdb = report.mine.gene.get("pdb")
    if pdb and not report.webgl:
        pytest.skip("RCSB v2 query made; structure list not checked (no WebGL in this browser)")
    if pdb:
        assert pdb in report.panels.get("bluegenesProteinVisualizer", {}).get("text", ""), \
            f"{pdb} not listed in the protein visualizer"


def test_msa_viewer_calls_the_aligner(report):
    feature(report.mine, "gene", "expect")
    if not report.mine.gene["expect"].get("align"):
        pytest.skip("no alignment call expected for this gene")
    calls = [(s, u) for m, s, u in report.responses if m == "POST" and u.endswith("/align-sequence")]
    assert calls, "MSA viewer made no POST to /align-sequence"
    assert all(s == 200 for s, _ in calls), f"aligner responses: {calls}"
    assert all(u.startswith(report.mine.url) for _, u in calls), f"aligner called off-site: {calls}"
    assert "Remember" in report.panels.get("bluegenesMSAViewer", {}).get("text", ""), \
        "MSA viewer did not reach its rendered state"


def test_jbrowse_iframe_loads(report):
    jb = feature(report.mine, "jbrowse")
    if "bluegenesJBrowseTool" not in report.mine.tools.get("rendered", []):
        pytest.skip(f"{report.mine.key}: the JBrowse 1 tool is not on the report page")
    page = report.page
    frame_el = page.locator(".bluegenesJBrowseTool iframe")
    assert frame_el.count() == 1, "no JBrowse iframe in the tool panel"
    src = frame_el.get_attribute("src")
    assert src.startswith(jb), f"iframe points at {src}, expected {jb}..."
    assert report.mine.service + "/jbrowse/config/" in src.replace("%2F", "/").replace("%3A", ":"), \
        f"iframe data URL does not point at this mine's service: {src}"
    frame = next((f for f in page.frames if f.url.startswith(jb)), None)
    assert frame is not None, f"frame for {jb} not found among {[f.url for f in page.frames]}"
    frame.wait_for_selector("#GenomeBrowser", timeout=60_000)
    frame.wait_for_selector(".track", timeout=60_000)
    body = frame.evaluate("document.body.innerText")
    assert not re.search(r"Error|could not", body), body[:300]


# True once JBrowse 2 has drawn something: a canvas with at least one
# non-transparent pixel (feature tracks, the sequence) inside the iframe.
JS_JBROWSE2_DRAWN = """
() => [...document.querySelectorAll('canvas')].some(c => {
  try {
    const ctx = c.getContext('2d');
    if (!ctx || !c.width || !c.height) return false;
    const d = ctx.getImageData(0, 0, c.width, c.height).data;
    for (let i = 3; i < d.length; i += 4) if (d[i] !== 0) return true;
  } catch (e) {}
  return false;
})
"""


def test_jbrowse2_iframe_loads(report):
    """The JBrowse 2 tool (intermineorg/jbrowse2) embeds the mine's JBrowse 2
    on the fixture gene, and a track draws."""
    jb = feature(report.mine, "jbrowse2")
    if "bluegenesJBrowse2Tool" not in report.mine.tools.get("rendered", []):
        pytest.skip(f"{report.mine.key}: the JBrowse 2 tool is not on the report page")
    page = report.page
    frame_el = page.locator(".bluegenesJBrowse2Tool iframe")
    assert frame_el.count() == 1, "no JBrowse 2 iframe in the tool panel"
    src = frame_el.get_attribute("src")
    assert src.startswith(jb), f"iframe points at {src}, expected {jb}..."
    g = report.mine.gene
    assert f"loc={g['chromosome']}%3A" in src or f"loc={g['chromosome']}:" in src, \
        f"iframe does not open chromosome {g['chromosome']}: {src}"
    assert f"-{report.mine.taxon}" in src, f"iframe does not name this mine's assembly: {src}"
    frame = next((f for f in page.frames if f.url.startswith(jb)), None)
    assert frame is not None, f"frame for {jb} not found among {[f.url for f in page.frames]}"
    frame.wait_for_function(JS_JBROWSE2_DRAWN, timeout=60_000)
    body = frame.evaluate("document.body.innerText")
    assert not re.search(r"Error|Failed to fetch|could not", body), body[:300]
