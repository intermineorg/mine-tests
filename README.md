# mine-tests

End-to-end checks for deployed InterMine instances: the web service, the
nginx front end, BlueGenes report pages, the tool store and the services
behind it (JBrowse, the sequence aligner, RCSB). InterMine and BlueGenes
have their own unit tests; this covers what those cannot, a running mine as
users see it. Run it before and after changing the backend, BlueGenes, a
tool, or the Docker deployment.

HumanMine and FlyMine are configured in [`mines.toml`](mines.toml); add a
mine by adding a section there.

## Layout

| Path | What it checks | Needs |
|------|----------------|-------|
| `tests/service/test_webservice.py` | InterMine web service: version, model, a known gene by query and keyword search, templates, query schemas | Python only |
| `tests/service/test_frontend.py` | BlueGenes HTML, imjs available to tools, tool store list and files, favicon, classic webapp, no internal hostnames | Python only |
| `tests/service/test_jbrowse.py` | InterMine's JBrowse REST endpoints (track list, refseqs, features, sequence, stats, names) and the JBrowse site | Python only |
| `tests/service/test_tools.py` | Tool bundle patches, the `/align-sequence` route, RCSB v2 search for the fixture protein | Python only |
| `tests/browser/test_report_tools.py` | A gene report page in Chromium/Firefox/WebKit: every configured tool draws, no error text, no uncaught exceptions, RCSB v2 and aligner calls succeed, JBrowse iframe loads | Playwright |
| `tools/relay/relay.py` | Patch rehearsal: serve local files (e.g. a patched tool bundle) in place of a live site's, relay the rest | Python only |
| `.github/workflows/nightly.yml` | Runs everything daily against the configured mines | GitHub Actions |

Markers: `service`, `browser`, `external` (depends on a third party such as
RCSB), `patched` (checks the local bundle patches that docker_template
deployments apply; see that README, "BlueGenes tools that call dead
external services").

## Running

Service tests, from any machine with Python 3.11+:

```sh
git clone https://github.com/intermineorg/mine-tests.git
cd mine-tests
python3 -m venv venv && . venv/bin/activate
pip install -r requirements.txt
pytest tests/service                       # both mines, about a minute
pytest tests/service --mine flymine        # one mine
pytest tests/service -m "not external"     # skip third-party services
```

Browser tests, with Docker (no local browser installs):

```sh
./run-browser-tests.sh --browser chromium --browser firefox
./run-browser-tests.sh --browser chromium --mine humanmine -k jbrowse
```

Or natively after `playwright install chromium firefox`:

```sh
pytest tests/browser --browser chromium --browser firefox
```

Each report page takes one to two minutes to settle, once per mine and
browser; the individual tests then inspect what was captured. On failure a
screenshot and a Playwright trace are written under `test-results/` (open a
trace with `playwright show-trace <file>`).

Useful options:

| Option / variable | Effect |
|---|---|
| `--mine KEY` | test only this mine (repeatable) |
| `--config FILE` | another TOML file, e.g. for a staging mine |
| `--url-override KEY=URL` | replace a mine's public URL, e.g. to point at the relay or a local stack; the fixtures stay the same |
| `MINE_TESTS_PROXY=http://host:port` | browsers use this proxy (requests uses `HTTPS_PROXY` as usual) |
| `MINE_TESTS_IGNORE_TLS=1` | accept any certificate (proxied sandboxes, self-signed staging) |
| `MINE_TESTS_CDP=http://host:9222` | drive an already-running Chromium over the DevTools protocol instead of launching one |
| `MINE_TESTS_HTTP_TIMEOUT=120` | per-request timeout for the service tests, seconds |

## Interpreting failures

- `test_frontend.py::test_imjs_present_for_tools` failing means every tool
  panel will be blank (`imjs is not defined`); fix the front end before
  looking at tools.
- `test_report_tools.py::test_page_scripts_load` failing means a CDN script
  (Vega, jQuery, Bootstrap) did not load; tools that need it then fail with
  what looks like a tool bug. In a firewalled environment allow
  `cdn.jsdelivr.net`, `cdnjs.cloudflare.com` and `code.jquery.com`.
- Headless Firefox in the Playwright image has no WebGL, so the protein
  visualizer cannot draw there; the tests report it as skipped, not failed,
  and still check that its RCSB request was made. Chromium has software
  WebGL and checks the structure list.
- `external` tests failing while everything else passes points at the third
  party, not the mine.
- The `patched` tests fail on a mine whose tool bundles were reinstalled
  from npm without re-applying the patches; the message says which patch.

## Rehearsing a change with the relay

To try a modified tool bundle against live data before deploying it:

```sh
mkdir -p patched/tools/@intermine/bluegenes-msa-viewer/dist
cp fixed-bundle.js patched/tools/@intermine/bluegenes-msa-viewer/dist/bundle.js
python3 tools/relay/relay.py https://www.humanmine.org patched 8099 &
pytest tests/browser --browser chromium --mine humanmine --url-override humanmine=http://localhost:8099
```

Anything under `patched/` whose path matches a request is served from
there; everything else comes from the real site. A browser can be pointed
at http://localhost:8099 for a manual look. (Features that depend on the
exact origin, such as cookies and same-origin checks against the public
hostname, behave differently through the relay.)

## Adding to the suite

- New mine: a `[mines.<key>]` section in `mines.toml` with a fixture gene
  whose data you have checked by hand, and the list of tool panels that
  render for it. Omit `jbrowse` or set `frontend.*` to false for features
  the deployment lacks.
- New check: a test function taking `http, mine` (service) or `report`
  (browser). Use `feature(mine, ...)` from `conftest.py` to skip cleanly when
  a mine does not configure what the test needs.
- A check for a BlueGenes or InterMine change under development: run the
  suite against the development instance with `--url-override` or a second
  config file, before and after.

## Findings so far

- FlyMine `Ubx` (and `bcd`, `ey`, `lab`, `pb`, `prd`, `Sxl`...): the protein
  visualizer and the MSA viewer both fail with "Failed to query for protein
  sequences". Some of the gene's proteins have no `primaryAccession`, the
  tools build one query per protein with `Protein.primaryAccession = <acc>`,
  and InterMine rejects the empty constraint with HTTP 400. Found by the
  first run of `test_report_tools.py` (October 2026); the fixture gene was
  changed to `Scr` so the suite stays green while the tools are unfixed.

## Background

Started in October 2026 after repairing HumanMine and FlyMine report pages:
the imjs script tag removed from BlueGenes, the protein visualizer calling a
retired RCSB API, the MSA viewer posting to a dead server and crashing in
modern browsers, the Firefox scrolling bug in the bundled `msa` library.
None of those would have been caught by the projects' own test suites; all
of them are caught here.

License: MIT.
