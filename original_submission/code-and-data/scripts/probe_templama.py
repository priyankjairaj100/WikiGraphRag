"""Probe HuggingFace Hub for a usable TempLAMA mirror (SPARQL generation is blocked)."""

import json

from huggingface_hub import HfApi

api = HfApi()
try:
    hits = list(api.list_datasets(search="templama", limit=30))
    rows = [{"id": d.id, "downloads": getattr(d, "downloads", None)} for d in hits]
    print(json.dumps(rows, indent=2))
except Exception as e:  # noqa: BLE001
    print("ERR", type(e).__name__, str(e)[:300])
