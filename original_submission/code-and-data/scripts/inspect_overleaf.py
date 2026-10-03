"""Extract the Overleaf bundle and inventory it (tex structure, bib, figures, arch diagram)."""

import os
import re
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZIP = os.path.join(ROOT, "wikigraphrag.zip")
OUT = os.path.join(ROOT, "overleaf_src")

os.makedirs(OUT, exist_ok=True)
with zipfile.ZipFile(ZIP) as z:
    z.extractall(OUT)
    names = z.namelist()

print("=== ALL FILES ===")
for n in sorted(names):
    print("  ", n)

# categorise
exts = {}
for n in names:
    e = os.path.splitext(n)[1].lower()
    exts.setdefault(e, []).append(n)
print("\n=== BY EXTENSION ===")
for e, fs in sorted(exts.items()):
    print(f"  {e or '(none)'}: {len(fs)}")

# find the main tex (has \documentclass)
tex_files = [os.path.join(OUT, n) for n in names if n.lower().endswith(".tex")]
main = None
for t in tex_files:
    try:
        s = open(t, "r", encoding="utf-8", errors="ignore").read()
    except OSError:
        continue
    if "\\documentclass" in s:
        main = t
        break
print("\n=== MAIN TEX ===", main)
if main:
    s = open(main, "r", encoding="utf-8", errors="ignore").read()
    print("chars:", len(s))
    print("\n--- sections ---")
    for m in re.finditer(r"\\(section|subsection|paragraph)\{([^}]*)\}", s):
        print(f"  {m.group(1)}: {m.group(2)}")
    print("\n--- figure includes ---")
    for m in re.finditer(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]*)\}", s):
        print("  ", m.group(1))
    print("\n--- labels ---")
    print("  ", [m.group(1) for m in re.finditer(r"\\label\{([^}]*)\}", s)])
    print("\n--- bib ---")
    for m in re.finditer(r"\\(bibliography|addbibresource)\{([^}]*)\}", s):
        print("  ", m.group(2))

# bib entry count
for n in names:
    if n.lower().endswith(".bib"):
        s = open(os.path.join(OUT, n), "r", encoding="utf-8", errors="ignore").read()
        count = len(re.findall(r"@\w+\{", s))
        print(f"\n=== BIB {n}: {count} entries ===")
