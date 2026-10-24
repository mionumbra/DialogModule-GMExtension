#!/usr/bin/env python3
"""Extract the upstream libdlgmod C++ source archive from the GameMaker repo.

The upstream GitHub repository is a GameMaker project; the C++ sources and
build scripts are shipped inside ``extensions/libdlgmod/libdlgmod.zip``.  This
helper extracts that archive into a build workspace and prints the directory
that contains the build scripts (``build-gm.sh`` / ``*.vcxproj``).

Usage:
    python tools/extract_source.py --repo . --dest .build/windows/src
"""

from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from pathlib import Path

SKIP_PARTS = {".git", ".build", "dist", "node_modules", "__pycache__"}


def find_zip(repo: Path) -> Path:
    preferred = repo / "extensions" / "libdlgmod" / "libdlgmod.zip"
    if preferred.is_file():
        return preferred
    candidates = [
        path
        for path in repo.rglob("libdlgmod.zip")
        if not any(part in SKIP_PARTS for part in path.relative_to(repo).parts)
    ]
    if not candidates:
        raise SystemExit(f"error: libdlgmod.zip not found under {repo}")
    return sorted(candidates)[0]


def extract(zip_path: Path, dest: Path) -> Path:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(dest)
    build_scripts = list(dest.rglob("build-gm.sh"))
    if not build_scripts:
        raise SystemExit(f"error: build-gm.sh not found inside {zip_path.name}")
    return build_scripts[0].parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="fork repository root",
    )
    parser.add_argument("--dest", type=Path, required=True, help="extraction directory")
    args = parser.parse_args(argv)

    repo = args.repo.resolve()
    zip_path = find_zip(repo)
    upstream_root = extract(zip_path, args.dest.resolve())
    print(f"[extract-source] {zip_path} -> {upstream_root}", file=sys.stderr)
    print(upstream_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
