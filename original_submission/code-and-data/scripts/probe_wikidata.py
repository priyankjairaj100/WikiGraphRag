import json
import urllib.parse
import urllib.request

UA = "WikiGraphRAG-repro/0.1 (research reproduction; contact: local)"
ENDPOINT = "https://query.wikidata.org/sparql"
Q = "SELECT ?x WHERE { wd:Q145 wdt:P36 ?x. } LIMIT 1"

url = ENDPOINT + "?" + urllib.parse.urlencode({"query": Q, "format": "json"})
req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/sparql-results+json"})
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    b = data["results"]["bindings"]
    print("OK", len(b), b[0]["x"]["value"] if b else "")
except Exception as e:  # noqa: BLE001
    print("ERR", type(e).__name__, str(e)[:200])
