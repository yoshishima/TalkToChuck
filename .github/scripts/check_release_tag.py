"""Check a proposed release tag against the version in this checkout."""
import argparse
from pathlib import Path
from runpy import run_path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tag', help='proposed tag, including the v prefix')
    args = parser.parse_args()
    version_file = Path(__file__).resolve().parents[2] / 'schwab' / 'version.py'
    expected = 'v' + run_path(str(version_file))['version']
    if args.tag != expected:
        parser.exit(1, f'Tag {args.tag!r} disagrees with {version_file.name}. Expected {expected!r}.\n')
