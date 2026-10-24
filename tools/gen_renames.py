#!/usr/bin/env python3
"""Generate renames.h / rename_map.json for the downstream libdlgmod build.

Why this exists
---------------
GameMaker resolves function names at load time by name (both VM and YYC builds
register a name->function-id table at startup). The runtime lookup order is:

    scripts  ->  extensions  ->  builtins

An extension function matches by *both* its GML name and its externalName, and
extension lookup wins over builtins.  So an extension that exports
``show_message`` shadows the builtin ``show_message`` even if its GML function
name is prefixed (``ldm_show_message``).  The only way to keep both callable is
to rename the actual DLL exports (externalName) as well, e.g. to
``ldm_show_message``.

This script scans the upstream sources for functions declared with the
upstream ``EXPORTED_FUNCTION`` macro and writes two build artefacts:

    renames.h        C header consumed via ``/FI`` (MSVC) or ``-include``
                     (clang/gcc); maps every export to its prefixed name
    rename_map.json  machine readable map, used by verify_exports.py and by
                     the GameMaker-project-side update script

It does not modify any upstream source, so syncing the fork is conflict-free.

Usage:
    python tools/gen_renames.py                     # all exports -> ldm_*
    python tools/gen_renames.py --mode collisions   # only builtin collisions
    python tools/gen_renames.py --source-dir path --prefix dm_
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

EXPORTED_FUNCTION_RE = re.compile(
    r"\bEXPORTED_FUNCTION\b\s+[^;{()]*?\b([A-Za-z_]\w*)\s*\("
)
SOURCE_SUFFIXES = {".h", ".hpp", ".c", ".cc", ".cpp", ".cxx", ".mm"}
SKIP_DIRS = {".git", ".build", "dist", "node_modules", "__pycache__"}
IDENTIFIER_RE = re.compile(r"^[A-Za-z_]\w*$")


def strip_c_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", "", text)
    return text


def iter_source_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        rel_parts = path.relative_to(root).parts
        if any(part in SKIP_DIRS for part in rel_parts):
            continue
        yield path


def collect_exported_names(root: Path) -> list[str]:
    """Return sorted, unique export names found in ``root``."""
    names: set[str] = set()
    for path in iter_source_files(root):
        text = strip_c_comments(path.read_text(encoding="utf-8", errors="replace"))
        # Drop preprocessor lines: macro definitions would otherwise be parsed.
        lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
        for match in EXPORTED_FUNCTION_RE.finditer("\n".join(lines)):
            name = match.group(1)
            if name in {"__declspec", "__attribute__"}:
                continue
            if not IDENTIFIER_RE.match(name):
                continue
            names.add(name)
    return sorted(names)


def load_builtin_names(path: Path) -> set[str]:
    names: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        names.add(line)
    return names


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Path.write_text(newline=...) needs Python >= 3.10; runners may be older,
    # so normalise line endings ourselves instead.
    path.write_text(text.replace("\r\n", "\n"), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=repo_root,
        help="upstream source tree to scan (default: fork repo root)",
    )
    parser.add_argument(
        "--builtin-list",
        type=Path,
        default=repo_root / "tools" / "gm_builtin_functions.txt",
        help="GameMaker builtin function snapshot",
    )
    parser.add_argument(
        "--out-header",
        type=Path,
        default=None,
        help="output C header (default: <source-dir>/renames.h)",
    )
    parser.add_argument(
        "--out-map",
        type=Path,
        default=None,
        help="output JSON map (default: <source-dir>/rename_map.json)",
    )
    parser.add_argument("--prefix", default="ldm_", help="export name prefix")
    parser.add_argument(
        "--mode",
        choices=["all", "collisions"],
        default="all",
        help="rename all exports, or only names that collide with a builtin",
    )
    parser.add_argument(
        "--upstream-ref",
        default="",
        help="free-form upstream ref recorded in the map (tag/commit)",
    )
    args = parser.parse_args(argv)

    source_dir: Path = args.source_dir.resolve()
    if not source_dir.is_dir():
        print(f"error: source dir not found: {source_dir}", file=sys.stderr)
        return 2

    builtin_path: Path = args.builtin_list.resolve()
    if not builtin_path.is_file():
        print(f"error: builtin list not found: {builtin_path}", file=sys.stderr)
        return 2

    header_path = (args.out_header or source_dir / "renames.h").resolve()
    map_path = (args.out_map or source_dir / "rename_map.json").resolve()

    exports = collect_exported_names(source_dir)
    if not exports:
        print(
            f"error: no EXPORTED_FUNCTION declarations found under {source_dir}",
            file=sys.stderr,
        )
        return 1

    builtins = load_builtin_names(builtin_path)
    collisions = sorted(name for name in exports if name in builtins)
    selected = collisions if args.mode == "collisions" else exports

    dupes = sorted({name for name in selected if selected.count(name) > 1})
    if dupes:  # pragma: no cover - defensive, collect() already dedupes
        print(f"error: duplicate export names: {dupes}", file=sys.stderr)
        return 1

    functions = [
        {
            "old": name,
            "new": args.prefix + name,
            "builtin_collision": name in builtins,
        }
        for name in selected
        if not name.startswith(args.prefix)
    ]

    header_lines = [
        "/* Auto-generated by tools/gen_renames.py -- do not edit.",
        " *",
        " * Renames libdlgmod DLL exports so they cannot shadow GameMaker",
        " * builtin function names at runtime.",
        " */",
        "#ifndef LIBDLGMOD_EXPORT_RENAMES_H",
        "#define LIBDLGMOD_EXPORT_RENAMES_H",
        "",
    ]
    header_lines += [f"#define {fn['old']} {fn['new']}" for fn in functions]
    header_lines += ["", "#endif /* LIBDLGMOD_EXPORT_RENAMES_H */", ""]
    write_text(header_path, "\n".join(header_lines))

    rename_map = {
        "generated_by": "tools/gen_renames.py",
        "mode": args.mode,
        "prefix": args.prefix,
        "upstream_ref": args.upstream_ref,
        "source_dir": str(source_dir),
        "builtin_list": str(builtin_path),
        "export_count": len(exports),
        "collision_count": len(collisions),
        "collisions": collisions,
        "functions": functions,
    }
    write_text(map_path, json.dumps(rename_map, indent=2) + "\n")

    print(f"upstream exports : {len(exports)}")
    print(f"builtin collisions: {len(collisions)}")
    for name in collisions:
        print(f"  - {name} -> {args.prefix + name}")
    print(f"renamed exports  : {len(functions)} ({args.mode} mode)")
    print(f"wrote {header_path}")
    print(f"wrote {map_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
