from huggingface_hub import HfApi

for repo in ("THUDM/LongBench",):
    files = HfApi().list_repo_files(repo, repo_type="dataset")
    print(repo, "->", len(files), "files")
    for f in files[:60]:
        print("  ", f)
