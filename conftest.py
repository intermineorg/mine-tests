"""Shared fixtures: mine configuration, an HTTP session, and Playwright
launch options that honour a proxy (needed when the tests run inside a
sandbox).  See README.md."""
import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).parent
TIMEOUT = float(os.environ.get("MINE_TESTS_HTTP_TIMEOUT", "60"))


@dataclass
class Mine:
    key: str
    url: str
    webapp: str
    taxon: int
    jbrowse: str | None = None
    frontend: dict = field(default_factory=dict)
    gene: dict = field(default_factory=dict)
    tools: dict = field(default_factory=dict)

    @property
    def service(self) -> str:
        return f"{self.url}/{self.webapp}/service"

    def report(self, cls: str, obj_id) -> str:
        return f"{self.url}/{self.webapp}/report/{cls}/{obj_id}"

    def __str__(self) -> str:
        return self.key


def pytest_addoption(parser):
    parser.addoption("--config", default=str(ROOT / "mines.toml"),
                     help="TOML file describing the mines (default: mines.toml)")
    parser.addoption("--mine", action="append", default=[],
                     help="mine key from the config to test; repeatable; default all")
    parser.addoption("--url-override", action="append", default=[],
                     metavar="KEY=URL",
                     help="replace a mine's public url, e.g. humanmine=http://localhost:8099 "
                          "to point the tests at a relay or a staging copy")


def _load_mines(config) -> list[Mine]:
    with open(config.getoption("--config"), "rb") as fh:
        data = tomllib.load(fh)
    overrides = dict(o.split("=", 1) for o in config.getoption("--url-override"))
    wanted = config.getoption("--mine")
    mines = []
    for key, m in data["mines"].items():
        if wanted and key not in wanted:
            continue
        m = dict(m)
        if key in overrides:
            m["url"] = overrides[key].rstrip("/")
        mines.append(Mine(key=key, **m))
    unknown = set(wanted) - set(data["mines"])
    if unknown:
        raise pytest.UsageError(f"unknown mine(s) {sorted(unknown)}; config has {sorted(data['mines'])}")
    return mines


def pytest_generate_tests(metafunc):
    if "mine" in metafunc.fixturenames:
        mines = _load_mines(metafunc.config)
        metafunc.parametrize("mine", mines, ids=[m.key for m in mines], scope="session")


def pytest_collection_modifyitems(items):
    """Mark tests by directory so `-m service` / `-m browser` work."""
    for item in items:
        path = str(item.fspath).replace("\\", "/")
        if "/tests/service/" in path:
            item.add_marker(pytest.mark.service)
        elif "/tests/browser/" in path:
            item.add_marker(pytest.mark.browser)


@pytest.fixture(scope="session")
def http():
    s = requests.Session()
    s.headers["User-Agent"] = "mine-tests (https://github.com/intermineorg/mine-tests)"
    if os.environ.get("MINE_TESTS_IGNORE_TLS") == "1":
        s.verify = False
        import urllib3
        urllib3.disable_warnings()
    orig = s.request

    def request(method, url, **kw):
        kw.setdefault("timeout", TIMEOUT)
        return orig(method, url, **kw)
    s.request = request
    return s


def feature(mine: Mine, section: str, name: str | None = None):
    """Return a configured feature value, skipping the test when the mine's
    config lacks or disables it.  feature(mine, "jbrowse"),
    feature(mine, "frontend", "imjs_vendor"), feature(mine, "gene", "pdb")."""
    value = getattr(mine, section)
    if name is not None:
        value = (value or {}).get(name)
    if not value:
        label = section if name is None else f"{section}.{name}"
        pytest.skip(f"{mine.key}: {label} not configured")
    return value


# ---- Playwright (tests/browser) -------------------------------------------
# pytest-playwright reads --browser (repeatable: chromium, firefox, webkit).
# These overrides add a proxy and relaxed TLS from the environment so the
# same tests run from a developer machine, CI, or a proxied sandbox.

@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    args = dict(browser_type_launch_args)
    proxy = os.environ.get("MINE_TESTS_PROXY")
    if proxy:
        args["proxy"] = {"server": proxy, "bypass": "localhost,127.0.0.1"}
    # Headless Firefox has no WebGL unless forced; the protein visualizer
    # (pv) needs it.  Ignored by the other browser types.
    args["firefox_user_prefs"] = {"webgl.force-enabled": True, "webgl.disabled": False,
                                  "layers.acceleration.disabled": True}
    return args


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    args = dict(browser_context_args)
    args["viewport"] = {"width": 1400, "height": 2400}
    if os.environ.get("MINE_TESTS_IGNORE_TLS") == "1":
        args["ignore_https_errors"] = True
    return args


@pytest.fixture(scope="session")
def browser(playwright, browser_type, browser_type_launch_args):
    """Launch a browser, or attach to one over the Chrome DevTools Protocol
    when MINE_TESTS_CDP is set (e.g. a Chromium container started with
    --remote-debugging-port, for environments that cannot download
    Playwright's own browsers)."""
    cdp = os.environ.get("MINE_TESTS_CDP")
    if cdp:
        b = playwright.chromium.connect_over_cdp(cdp)
    else:
        b = browser_type.launch(**browser_type_launch_args)
    yield b
    b.close()
