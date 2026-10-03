"""Inspect the Yova/templama mirror: list files and preview the first records."""

import json

from huggingface_hub import HfApi, hf_hub_download

REPO = "Yova/templama"
api = HfApi()
files = api.list_repo_files(REPO, repo_type="dataset")
print("FILES:", files)

# preview the first data-looking file
cand = [f for f in files if f.endswith((".json", ".jsonl", ".csv", ".parquet"))]
print("DATA FILES:", cand)
if cand:
    path = hf_hub_download(REPO, cand[0], repo_type="dataset")
    print("DOWNLOADED:", path)
    if path.endswith((".json", ".jsonl")):
        with open(path, "r", encoding="utf-8") as fh:
            for i, line in enumerate(fh):
                if i >= 3:
                    break
                print("ROW", i, line.strip()[:400])
