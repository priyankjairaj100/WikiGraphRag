"""Create a deterministic ZIP and per-file SHA-256 manifest for this project."""
from pathlib import Path
import argparse
import hashlib
import json
import zipfile

ROOT = Path(__file__).resolve().parents[1]
EXTERNAL_SOURCE_DIRECTORY = Path('data/correction_pilot_v05/source_capture')
EXTERNAL_SOURCE_SUFFIXES = ('.response.bin', '.embedded.html', '.derived.txt')


def is_external_input(path: Path) -> bool:
    """Exclude only full source representations in the v0.5 capture folder."""
    relative = path.relative_to(ROOT)
    return (relative.is_relative_to(EXTERNAL_SOURCE_DIRECTORY)
            and path.name.endswith(EXTERNAL_SOURCE_SUFFIXES))


def file_entry(path: Path) -> dict:
    return {'path': str(path.relative_to(ROOT)), 'bytes': path.stat().st_size,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    candidates = sorted(p for p in ROOT.rglob('*') if p.is_file()
                        and not any(x in {'__pycache__', '.git'} for x in p.parts)
                        and p.suffix != '.pyc' and p.name != 'CHECKPOINT_MANIFEST.json')
    excluded = [p for p in candidates if is_external_input(p)]
    files = [p for p in candidates if not is_external_input(p)]
    entries = [file_entry(p) for p in files]
    manifest = ROOT / 'CHECKPOINT_MANIFEST.json'
    manifest.write_text(json.dumps({
        'schema_version': 2,
        'files': entries,
        'excluded_external_inputs': [file_entry(p) for p in excluded],
        'external_data_policy': {
            'excluded_directory': str(EXTERNAL_SOURCE_DIRECTORY),
            'excluded_filename_suffixes': list(EXTERNAL_SOURCE_SUFFIXES),
            'scope': 'Only matching files within the specified directory and its descendants.',
            'reason': 'Full externally sourced responses and their full-text derivatives remain analysis-only working data.',
            'retained': 'Source metadata, capture receipts, short evidence excerpts, extraction and download scripts.',
            'reproduction': 'See data/correction_pilot_v05/source_manifest.json and source_capture/capture_public.py.',
            'availability_limit': 'Fresh downloads may differ from recorded hashes. Exact offset verification requires the matching full derived text; it cannot be independently repeated from excerpts alone.'
        }
    }, indent=2) + '\n')
    files.append(manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            info = zipfile.ZipInfo('acl2027_temporal_state/' + str(path.relative_to(ROOT)))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())
    print(json.dumps({'path': str(args.output.resolve()), 'files': len(files),
                      'excluded_external_inputs': len(excluded),
                      'bytes': args.output.stat().st_size,
                      'sha256': hashlib.sha256(args.output.read_bytes()).hexdigest()}, indent=2))


if __name__ == '__main__':
    main()
