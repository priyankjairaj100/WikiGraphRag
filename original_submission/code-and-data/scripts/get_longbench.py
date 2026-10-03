"""Download LongBench data.zip, extract the multi-hop jsonls, and inspect one HotpotQA row."""

import io
import json
import os
import zipfile

from huggingface_hub import hf_hub_download

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "longbench")
os.makedirs(OUT, exist_ok=True)
WANT = ["hotpotqa", "2wikimqa", "musique"]

path = hf_hub_download("THUDM/LongBench", "data.zip", repo_type="dataset")
print("ZIP:", path)
with zipfile.ZipFile(path) as z:
    names = z.namelist()
    print("MEMBERS (sample):", [n for n in names if any(w in n for w in WANT)][:10])
    for w in WANT:
        member = next((n for n in names if n == f"data/{w}.jsonl" or n.endswith(f"/{w}.jsonl")
                       or n == f"{w}.jsonl"), None)
        if member:
            data = z.read(member).decode("utf-8")
            with open(os.path.join(OUT, f"{w}.jsonl"), "w", encoding="utf-8") as fh:
                fh.write(data)
            n = data.count("\n")
            print(f"WROTE {w}.jsonl  rows~{n}")

# inspect one hotpotqa row
hp = os.path.join(OUT, "hotpotqa.jsonl")
if os.path.exists(hp):
    with open(hp, "r", encoding="utf-8") as fh:
        row = json.loads(fh.readline())
    print("KEYS:", list(row.keys()))
    for k, v in row.items():
        print(f"  {k}: {str(v)[:200]}")
