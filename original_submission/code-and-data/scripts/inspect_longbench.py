"""Inspect the LongBench multi-hop configs (hotpotqa / 2wikimqa / musique): files + one example."""

import json

from huggingface_hub import HfApi, hf_hub_download

REPO = "THUDM/LongBench"
api = HfApi()
files = api.list_repo_files(REPO, repo_type="dataset")
data_files = [f for f in files if any(k in f for k in ("hotpotqa", "2wikimqa", "musique"))]
print("MATCHING FILES:", data_files[:20])

# try to pull one hotpotqa example
cand = [f for f in data_files if f.endswith((".jsonl", ".json"))]
print("JSONL:", cand[:10])
if cand:
    path = hf_hub_download(REPO, cand[0], repo_type="dataset")
    print("DOWNLOADED:", path)
    with open(path, "r", encoding="utf-8") as fh:
        row = json.loads(fh.readline())
    print("KEYS:", list(row.keys()))
    for k, v in row.items():
        s = str(v)
        print(f"  {k}: {s[:300]}")
