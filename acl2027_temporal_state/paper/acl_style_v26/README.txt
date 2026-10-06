Official ACL build dependency

Upstream: https://github.com/acl-org/acl-style-files
Pinned commit: d5adc823ff0f80f98c80405ca0ab66c68e684409
UPSTREAM.json records exact file hashes.

acl_natbib.bst is unchanged.
Its header permits LPPL redistribution.
LPPL-1.3c.txt contains the unmodified license.
The license source is recorded in UPSTREAM.json.

No explicit redistribution license was found for acl.sty or the example documents.
They remain in the external upstream cache.
Fetch the required files from the research directory:

python scripts/fetch_acl_style_v26.py --cache-dir /path/to/acl-style-cache

The script verifies exact file hashes before writing.
It refuses a cache inside this Git repository.
It refuses mismatched existing files.
An existing upstream clone can be supplied through --source-dir.
Alternatively, fetch the complete pinned upstream repository:

git clone https://github.com/acl-org/acl-style-files.git /path/to/acl-style-cache
git -C /path/to/acl-style-cache checkout d5adc823ff0f80f98c80405ca0ab66c68e684409

Set TEXINPUTS and BSTINPUTS to the cache directory, followed by the platform path separator.
The trailing separator preserves the standard TeX paths.
Then run latexmk from the directory containing the manuscript:

latexmk -pdf -interaction=nonstopmode -halt-on-error manuscript.tex

Use the review option when loading acl.
Do not modify upstream style files.
BUILD_CHECK.json records a successful minimal compile in the current environment.
It does not establish manuscript readiness.
