"""BlueGenes and the nginx front end: what a browser receives before any
JavaScript runs."""
import re

import pytest

from conftest import feature


def test_home_page_is_bluegenes(http, mine):
    r = http.get(mine.url + "/")
    r.raise_for_status()
    assert "text/html" in r.headers["content-type"]
    assert re.search(r"/js/compiled/app[^\"']*\.js", r.text), "BlueGenes app bundle not referenced"


def test_report_page_served(http, mine):
    r = http.get(mine.report("Gene", mine.gene["id"]))
    r.raise_for_status()
    assert "text/html" in r.headers["content-type"]


def test_imjs_present_for_tools(http, mine):
    """Every tool-store tool needs window.imjs.  Either BlueGenes ships it or
    (docker_template) nginx injects /vendor/imjs/im.min.js."""
    page = http.get(mine.url + "/")
    page.raise_for_status()
    srcs = re.findall(r'<script[^>]+src="([^"]+)"', page.text)
    imjs = [s for s in srcs if re.search(r"im(js)?[.-]|imjs", s)]
    assert imjs, f"no imjs script tag in BlueGenes HTML; scripts: {srcs}"
    for s in imjs:
        url = s if s.startswith("http") else mine.url + s
        r = http.get(url)
        assert r.ok, f"{url} -> {r.status_code}"
        assert "intermine" in r.text[:200000].lower(), f"{url} does not look like imjs"


def test_vendor_imjs_route(http, mine):
    feature(mine, "frontend", "imjs_vendor")
    r = http.get(mine.url + "/vendor/imjs/im.min.js")
    r.raise_for_status()
    assert "javascript" in r.headers["content-type"]
    assert r.headers.get("expires") or "max-age" in r.headers.get("cache-control", ""), \
        "vendor bundle should be cacheable"


def test_tool_store_lists_tools_and_serves_their_files(http, mine):
    r = http.get(mine.url + "/api/tools/all")
    r.raise_for_status()
    tools = r.json()["tools"]
    assert tools, "no tools installed"
    names = {t["config"]["toolName"]["cljs"] for t in tools}
    missing = set(mine.tools.get("rendered", [])) - names
    assert not missing, f"expected tools not installed: {sorted(missing)}"
    for t in tools:
        pkg = t["package"]["name"]
        for kind, path in t["config"]["files"].items():
            url = f"{mine.url}/tools/{pkg}/{path}"
            f = http.head(url, allow_redirects=True)
            assert f.ok, f"{pkg} {kind}: {url} -> {f.status_code}"


def test_favicon(http, mine):
    r = http.get(mine.url + "/favicon.ico", allow_redirects=False)
    assert r.status_code == 200, f"favicon -> {r.status_code} {r.headers.get('location')}"
    assert r.headers["content-type"].startswith("image/")


def test_classic_webapp(http, mine):
    path = feature(mine, "frontend", "classic_webapp")
    r = http.get(f"{mine.url}{path}/begin.do")
    r.raise_for_status()
    assert "text/html" in r.headers["content-type"]
    assert "intermine" in r.text.lower()


def test_no_internal_hostnames_leak(http, mine):
    """Redirects or links to http://tomcat:8080 or http://bluegenes:5000 mean
    the public URL is not configured (webapp.baseurl / nginx headers)."""
    for url in (mine.url + "/", mine.url + "/favicon.ico", mine.service + "/version"):
        r = http.get(url, allow_redirects=False)
        loc = r.headers.get("location", "")
        assert not re.search(r"https?://(tomcat|bluegenes|jbrowse|aligner)[:/]", loc), f"{url} redirects to {loc}"
    r = http.get(mine.url + "/")
    assert not re.search(r"https?://(tomcat|bluegenes):\d+", r.text), "internal hostname in HTML"
