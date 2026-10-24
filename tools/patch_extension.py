#!/usr/bin/env python3
"""Patch the upstream GameMaker extension metadata for prefixed exports.

The upstream repo ships ``extensions/libdlgmod/libdlgmod.yy`` (function metadata)
and ``libdlgmod.gml`` (extension init code) which still reference the original,
unprefixed function names.  A GameMaker project that uses the prefixed DLL
exports must use matching metadata, so this script writes:

    <out-dir>/libdlgmod.yy   function name + externalName -> ldm_<old>
    <out-dir>/libdlgmod.gml  init code calls -> ldm_<old>
    <out-dir>/libdlgmod.zip  copied upstream source archive (for reference)

The original file formatting is preserved (line based replacement), and the
result is validated by parsing the tolerant GameMaker JSON and checking every
DLL-side function name/externalName has the prefix.

Usage:
    python tools/patch_extension.py --repo . --map .build/windows/rename_map.json \
        --out-dir dist/extensions/libdlgmod
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path


def load_mapping(map_path: Path) -> dict[str, str]:
    data = json.loads(map_path.read_text(encoding="utf-8"))
    return {entry["old"]: entry["new"] for entry in data["functions"]}


def find_extension_dir(repo: Path) -> Path:
    preferred = repo / "extensions" / "libdlgmod"
    if (preferred / "libdlgmod.yy").is_file():
        return preferred
    candidates = [
        path.parent
        for path in repo.rglob("libdlgmod.yy")
        if ".build" not in path.parts and "dist" not in path.parts
    ]
    if not candidates:
        raise SystemExit(f"error: libdlgmod.yy not found under {repo}")
    return sorted(candidates)[0]


def tolerant_loads(text: str) -> dict:
    cleaned = re.sub(r",\s*([}\]])", r"\1", text)
    return json.loads(cleaned)


def patch_yy(yy_text: str, mapping: dict[str, str], prefix: str) -> tuple[str, int]:
    """Prefix name / externalName of every DLL-side function entry.

    Function entries may span multiple lines (the older ``.yy`` format puts
    ``externalName`` on the closing line), so patch whole ``{...}`` blocks
    instead of individual lines.
    """
    block_re = re.compile(r"\{[^{}]*\"resourceType\":\"GMExtensionFunction\"[^{}]*\}")
    patched = 0

    def replace_block(match: re.Match[str]) -> str:
        nonlocal patched
        block = match.group(0)
        name_match = re.search(r'"name":"([^"]+)"', block)
        if name_match is None:
            return block
        old_name = name_match.group(1)
        new_name = mapping.get(old_name)
        if new_name is None:
            # GML include functions (e.g. libdlgmod_init) are not DLL exports.
            return block
        block = block.replace(f'"name":"{old_name}"', f'"name":"{new_name}"', 1)
        block = block.replace(f'"%Name":"{old_name}"', f'"%Name":"{new_name}"', 1)
        ext_match = re.search(r'"externalName":"([^"]+)"', block)
        if ext_match is not None:
            old_ext = ext_match.group(1)
            new_ext = mapping.get(old_ext, prefix + old_ext)
            block = block.replace(
                f'"externalName":"{old_ext}"', f'"externalName":"{new_ext}"', 1
            )
        patched += 1
        return block

    return block_re.sub(replace_block, yy_text), patched


def patch_gml(gml_text: str, mapping: dict[str, str]) -> str:
    for old, new in sorted(mapping.items(), key=lambda item: -len(item[0])):
        gml_text = re.sub(rf"\b{re.escape(old)}\b", new, gml_text)
    return gml_text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="fork repository root",
    )
    parser.add_argument("--map", type=Path, required=True, help="rename_map.json")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--prefix", default="ldm_")
    args = parser.parse_args(argv)

    repo = args.repo.resolve()
    mapping = load_mapping(args.map.resolve())
    ext_dir = find_extension_dir(repo)
    yy_path = ext_dir / "libdlgmod.yy"
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    yy_text, patched = patch_yy(yy_path.read_text(encoding="utf-8-sig"), mapping, args.prefix)
    parsed = tolerant_loads(yy_text)
    dll_functions = 0
    unprefixed = []
    for file_entry in parsed.get("files", []):
        if file_entry.get("kind") != 1:  # DLL include
            continue
        for function in file_entry.get("functions", []):
            dll_functions += 1
            name = function.get("name", "")
            ext = function.get("externalName", "")
            if not name.startswith(args.prefix):
                unprefixed.append(name)
            if not ext.startswith(args.prefix):
                unprefixed.append(ext)
    if unprefixed:
        raise SystemExit(f"error: unprefixed function names remain: {unprefixed[:5]}")
    if dll_functions != len(mapping):
        raise SystemExit(
            f"error: .yy has {dll_functions} DLL functions but rename map has "
            f"{len(mapping)}"
        )
    (out_dir / "libdlgmod.yy").write_text(yy_text, encoding="utf-8")

    gml_path = ext_dir / "libdlgmod.gml"
    if gml_path.is_file():
        gml_text = patch_gml(gml_path.read_text(encoding="utf-8-sig"), mapping)
        (out_dir / "libdlgmod.gml").write_text(gml_text, encoding="utf-8")

    zip_path = ext_dir / "libdlgmod.zip"
    if zip_path.is_file():
        shutil.copy2(zip_path, out_dir / "libdlgmod.zip")

    print(f"[patch-extension] patched {patched} function entries -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
