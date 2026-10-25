"""JBrowse 2 (intermineorg/jbrowse2) as deployed for a mine: the site, its
generated config, the plugin bundle, and the mine services the plugin reads
beyond the JBrowse 1 REST endpoints (test_jbrowse.py covers those)."""
from urllib.parse import urljoin, urlsplit

import pytest

from conftest import feature


@pytest.fixture
def site(mine):
    return feature(mine, "jbrowse2")


@pytest.fixture
def config(http, site):
    r = http.get(urljoin(site, "config.json"))
    r.raise_for_status()
    return r.json()


def assembly_for(config, mine):
    """The assembly whose sequence comes from this mine's service."""
    base = f"{mine.service}/jbrowse/{mine.taxon}"
    found = [a for a in config["assemblies"] if a["sequence"]["adapter"].get("baseUrl", "").rstrip("/") == base]
    assert found, f"no assembly reads {base}; assemblies: {[a['name'] for a in config['assemblies']]}"
    return found[0]


def test_jbrowse2_site(http, site):
    r = http.get(site)
    r.raise_for_status()
    assert "<title>JBrowse</title>" in r.text
    assert "x-frame-options" not in {k.lower() for k in r.headers}, "X-Frame-Options would block the report-page iframe"


def test_jbrowse2_config_has_this_mine(http, mine, config):
    """The assembly is named as the BlueGenes tool names it
    (<project title>-<taxon>, lower case), and has a Gene track drawn as gene
    models; a mismatch opens the tool on an empty view."""
    asm = assembly_for(config, mine)
    props = http.get(f"{mine.service}/web-properties")
    props.raise_for_status()
    title = props.json()["web-properties"]["project"]["title"]
    assert asm["name"] == f"{title}-{mine.taxon}".lower(), \
        f"assembly {asm['name']} is not what the BlueGenes tool will ask for"
    tracks = {t["trackId"]: t for t in config["tracks"]}
    gene = tracks.get(f"{asm['name']}-Gene")
    assert gene, f"no {asm['name']}-Gene track"
    assert gene["adapter"]["type"] == "InterMineGeneModelAdapter"
    assert gene["adapter"]["baseUrl"].startswith(mine.url), f"Gene track reads {gene['adapter']['baseUrl']}"


def test_jbrowse2_plugin_bundle(http, site, config):
    for plugin in config["plugins"]:
        url = urljoin(site, plugin["url"])
        r = http.get(url)
        assert r.status_code == 200, f"{url} -> {r.status_code}"
        assert "JBrowsePluginInterMine" in r.text, f"{url} is not the InterMine plugin bundle"


def test_query_service_allows_jbrowse2_origin(http, mine, site):
    """Gene models come from the query service, POSTed as a form (no
    preflight); the browser only accepts the answer if it carries CORS
    headers for the JBrowse 2 origin, which matters once JBrowse 2 has its
    own hostname."""
    parts = urlsplit(site)
    origin = f"{parts.scheme}://{parts.netloc}"
    r = http.post(f"{mine.service}/query/results", headers={"Origin": origin}, data={
        "format": "json",
        "query": '<query model="genomic" view="Gene.id"><constraint path="Gene.id" op="=" value="%s"/></query>'
                 % mine.gene["id"],
    })
    r.raise_for_status()
    allowed = r.headers.get("Access-Control-Allow-Origin")
    assert allowed in (origin, "*"), f"Access-Control-Allow-Origin {allowed!r} for {origin}"


def test_gene_model_query(http, mine, site):
    """The query the plugin's gene-model adapter runs for a region (genes
    overlapping it, outer-joined to transcripts and exons) finds the fixture
    gene with exon structure."""
    g = mine.gene
    q = (
        '<query model="genomic" view="Gene.symbol Gene.transcripts.primaryIdentifier '
        'Gene.transcripts.exons.chromosomeLocation.start Gene.transcripts.exons.chromosomeLocation.end">'
        '<join path="Gene.transcripts" style="OUTER"/><join path="Gene.transcripts.exons" style="OUTER"/>'
        f'<constraint path="Gene.chromosomeLocation" op="OVERLAPS"><value>{g["chromosome"]}:{g["start"]}..{g["end"]}</value></constraint>'
        f'<constraint path="Gene.organism.taxonId" op="=" value="{mine.taxon}"/></query>'
    )
    r = http.post(f"{mine.service}/query/results", data={"format": "json", "query": q})
    r.raise_for_status()
    rows = [row for row in r.json()["results"] if row[0] == g["symbol"]]
    assert rows, f"{g['symbol']} not among genes overlapping its own location"
    exons_by_transcript = {}
    for _, tx, start, end in rows:
        if tx and start is not None:
            exons_by_transcript.setdefault(tx, set()).add((start, end))
    assert exons_by_transcript, f"{g['symbol']} has no transcripts with exons; gene models would be flat boxes"
    assert max(map(len, exons_by_transcript.values())) > 1, "no transcript has more than one exon"
