"""BlueGenes tool-store tools that depend on something beyond BlueGenes:
local bundle patches, the sequence-alignment service, RCSB."""
import json

import pytest

from conftest import feature

DEAD_ALIGNER = "https://sequencealignment.apps.intermine.org"
RCSB_V1 = "rcsbsearch/v1/query"
ARRAY_AT_BUG = '!t instanceof Array||"at"in t'
LEGACY_WHEEL = 't.mousewheel="_onmousewheel"'


def bundle(http, mine, tool):
    r = http.get(f"{mine.url}/tools/@intermine/{tool}/dist/bundle.js")
    r.raise_for_status()
    return r.text


@pytest.mark.patched
def test_protein_visualizer_uses_rcsb_v2(http, mine):
    b = bundle(http, mine, "bluegenes-protein-visualizer")
    assert RCSB_V1 not in b, "bundle still calls the RCSB search API v1 (retired July 2022)"
    assert "rcsbsearch/v2/query" in b


@pytest.mark.patched
def test_msa_viewer_bundle_patches(http, mine):
    feature(mine, "frontend", "align_sequence")
    b = bundle(http, mine, "bluegenes-msa-viewer")
    assert DEAD_ALIGNER not in b, "bundle still posts to the dead InterMine alignment server"
    assert ARRAY_AT_BUG not in b, "msa library still misdetects arrays (Array.prototype.at)"
    assert LEGACY_WHEEL not in b, "msa library still cancels native scrolling in Firefox"


def test_align_sequence_route(http, mine):
    feature(mine, "frontend", "align_sequence")
    fasta = ">a\nMKTAYIAKQRQISFVKSHFSRQ\n>b\nMKTAYIAKQRQISFVK\n"
    r = http.post(f"{mine.url}/align-sequence", json={"sequence": fasta})
    assert r.status_code == 200, f"{r.status_code}: {r.text[:200]}"
    body = r.json()
    assert body["error"] is False, body
    assert body["data"].count(">") == 2
    assert "-" in body["data"], "no gaps: alignment did not run"


@pytest.mark.external
def test_rcsb_v2_finds_structure_for_fixture_protein(http, mine):
    pdb = feature(mine, "gene", "pdb")
    q = ('<query model="genomic" view="Gene.proteins.primaryAccession">'
         f'<constraint path="Gene.id" op="=" value="{mine.gene["id"]}"/></query>')
    accs = [row[0] for row in http.get(f"{mine.service}/query/results",
                                       params={"query": q, "format": "json"}).json()["results"] if row[0]]
    nodes = [{"type": "terminal", "service": "text", "parameters": {
        "attribute": "rcsb_polymer_entity_container_identifiers.reference_sequence_identifiers.database_accession",
        "operator": "in", "value": accs}},
        {"type": "terminal", "service": "text", "parameters": {
            "attribute": "rcsb_polymer_entity_container_identifiers.reference_sequence_identifiers.database_name",
            "operator": "exact_match", "value": "UniProt"}}]
    query = {"query": {"type": "group", "logical_operator": "and", "nodes": nodes},
             "return_type": "entry", "request_options": {"paginate": {"start": 0, "rows": 100}}}
    r = http.get("https://search.rcsb.org/rcsbsearch/v2/query", params={"json": json.dumps(query)},
                 headers={"Origin": mine.url})
    assert r.status_code == 200, r.text[:300]
    assert r.headers.get("access-control-allow-origin") in ("*", mine.url), "RCSB stopped sending CORS headers"
    ids = {hit["identifier"] for hit in r.json()["result_set"]}
    assert pdb in ids, f"{pdb} not in {sorted(ids)}"
