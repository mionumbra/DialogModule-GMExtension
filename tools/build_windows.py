#!/usr/bin/env python3
"""Build the Windows GameMaker DLL of libdlgmod with prefixed exports.

The upstream repository is a GameMaker project: C++ sources and build scripts
are shipped inside ``extensions/libdlgmod/libdlgmod.zip``.  This script:

1. extracts that archive into ``.build/windows/src``
2. runs ``gen_renames.py`` (all exports -> ``ldm_<name>``)
3. injects ``renames.h`` + ``/utf-8`` into the extracted ``libdlgmod-gm.vcxproj``
4. builds Release x64 with MSBuild
5. publishes to ``dist/``:
   ``windows/libdlgmod.dll``, ``rename_map.json`` and a patched
   ``extensions/libdlgmod/{libdlgmod.yy,libdlgmod.gml,libdlgmod.zip}``
6. verifies the PE export table

The fork tree itself is never modified.

Usage:
    python tools/build_windows.py
    python tools/build_windows.py --platform-toolset v143 --keep-build
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
SKIP_PARTS = {".git", ".build", "dist", "node_modules", "__pycache__"}


def log(message: str) -> None:
    print(f"[build-windows] {message}", flush=True)


def run(cmd: list[str], cwd: Path | None = None) -> None:
    log("$ " + " ".join(str(part) for part in cmd))
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=True)


def find_source_zip(repo: Path) -> Path:
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


def extract_source(zip_path: Path, dest: Path) -> Path:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(dest)
    log(f"extracted {zip_path.name} -> {dest}")
    return dest


def find_msbuild(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise SystemExit(f"error: MSBuild not found: {path}")
        return path

    for candidate in ("msbuild", "MSBuild.exe"):
        found = shutil.which(candidate)
        if found:
            return Path(found)

    vswhere = Path(
        r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"
    )
    if vswhere.is_file():
        proc = subprocess.run(
            [
                str(vswhere),
                "-latest",
                "-products",
                "*",
                "-requires",
                "Microsoft.Component.MSBuild",
                "-find",
                r"MSBuild\**\Bin\MSBuild.exe",
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                if line.strip() and Path(line.strip()).is_file():
                    return Path(line.strip())

    import glob as glob_module

    for pattern in (
        r"C:\Program Files\Microsoft Visual Studio\*\*\MSBuild\Current\Bin\MSBuild.exe",
        r"D:\Program Files\Microsoft Visual Studio\*\*\MSBuild\Current\Bin\MSBuild.exe",
        r"C:\Program Files (x86)\Microsoft Visual Studio\*\*\MSBuild\Current\Bin\MSBuild.exe",
    ):
        matches = [Path(p) for p in glob_module.glob(pattern)]
        if matches:
            return sorted(matches)[-1]

    raise SystemExit("error: MSBuild not found; pass --msbuild <path to MSBuild.exe>")


def vs_root_from_msbuild(msbuild: Path) -> Path | None:
    for parent in msbuild.parents:
        if (parent / "MSBuild" / "Microsoft" / "VC").is_dir():
            return parent
    return None


def resolve_platform_toolset(msbuild: Path, requested: str, platform: str) -> str:
    root = vs_root_from_msbuild(msbuild)
    if root is None:
        log(f"could not detect VS root; using requested toolset {requested}")
        return requested
    platform_dir = "x64" if platform.lower() == "x64" else "Win32"
    available: set[str] = set()
    for version_dir in (root / "MSBuild" / "Microsoft" / "VC").glob("v*"):
        toolsets = version_dir / "Platforms" / platform_dir / "PlatformToolsets"
        if toolsets.is_dir():
            available.update(
                entry.name
                for entry in toolsets.iterdir()
                if entry.is_dir() and entry.name.startswith("v")
            )
    if not available:
        log(f"no toolsets detected; using requested toolset {requested}")
        return requested
    if requested in available:
        return requested

    def version_key(name: str) -> tuple[int, ...]:
        return tuple(int(part) for part in re.findall(r"\d+", name)) or (0,)

    fallback = max(available, key=version_key)
    log(
        f"requested toolset {requested} is not installed "
        f"(available: {', '.join(sorted(available, key=version_key))}); "
        f"using {fallback}"
    )
    return fallback


def patch_vcxproj(vcxproj: Path) -> None:
    text = vcxproj.read_text(encoding="utf-8-sig")
    if "ForcedIncludeFiles" in text or "LIBDLGMOD_EXPORT_RENAMES_H" in text:
        log("vcxproj already patched")
        return
    marker = '<Import Project="$(VCTargetsPath)\\Microsoft.Cpp.targets" />'
    if marker not in text:
        raise SystemExit(f"error: cannot find target import in {vcxproj}")
    # - ForcedIncludeFiles injects renames.h without touching upstream sources.
    # - /utf-8 is required because the upstream sources contain UTF-8 literals
    #   while MSVC otherwise assumes the system codepage.
    patch = (
        "  <ItemDefinitionGroup>\n"
        "    <ClCompile>\n"
        "      <ForcedIncludeFiles>"
        "$(MSBuildProjectDirectory)\\renames.h;%(ForcedIncludeFiles)"
        "</ForcedIncludeFiles>\n"
        "      <AdditionalOptions>/utf-8 %(AdditionalOptions)</AdditionalOptions>\n"
        "    </ClCompile>\n"
        "  </ItemDefinitionGroup>\n"
    )
    text = text.replace(marker, patch + marker, 1)
    vcxproj.write_text(text, encoding="utf-8")
    log(f"patched {vcxproj.name} with ForcedIncludeFiles + /utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        type=Path,
        default=TOOLS_DIR.parent,
        help="fork repository root (default: parent of tools/)",
    )
    parser.add_argument("--configuration", default="Release")
    parser.add_argument("--platform", default="x64")
    parser.add_argument(
        "--platform-toolset",
        default="v143",
        help="MSVC platform toolset (falls back to the newest installed one)",
    )
    parser.add_argument("--msbuild", default=None, help="path to MSBuild.exe")
    parser.add_argument(
        "--mode",
        choices=["all", "collisions"],
        default="all",
        help="rename all exports or only builtin collisions (debug)",
    )
    parser.add_argument("--keep-build", action="store_true")
    args = parser.parse_args(argv)

    repo = args.repo.resolve()
    if not (repo / "tools" / "gen_renames.py").is_file():
        raise SystemExit(f"error: {repo} does not look like the fork repo root")

    build = repo / ".build" / "windows"
    if build.exists():
        shutil.rmtree(build)
    build.mkdir(parents=True)

    zip_path = find_source_zip(repo)
    src = extract_source(zip_path, build / "src")
    vcxproj = next(src.rglob("libdlgmod-gm.vcxproj"), None)
    if vcxproj is None:
        raise SystemExit("error: libdlgmod-gm.vcxproj not found in source archive")
    source_root = vcxproj.parent
    log(f"upstream root: {source_root}")

    renames_h = source_root / "renames.h"
    rename_map = source_root / "rename_map.json"
    run(
        [
            sys.executable,
            str(TOOLS_DIR / "gen_renames.py"),
            "--source-dir",
            str(source_root),
            "--out-header",
            str(renames_h),
            "--out-map",
            str(rename_map),
            "--mode",
            args.mode,
        ]
    )
    patch_vcxproj(vcxproj)

    msbuild = find_msbuild(args.msbuild)
    toolset = resolve_platform_toolset(msbuild, args.platform_toolset, args.platform)
    log(f"MSBuild : {msbuild}")
    log(f"toolset : {toolset}")

    vc_out = build / "vc_out"
    vc_out.mkdir(parents=True, exist_ok=True)
    run(
        [
            str(msbuild),
            str(vcxproj),
            "/nologo",
            "/m",
            "/v:minimal",
            f"/p:Configuration={args.configuration}",
            f"/p:Platform={args.platform}",
            f"/p:PlatformToolset={toolset}",
            f"/p:SolutionDir={source_root}\\",
            f"/p:OutDir={vc_out}\\",
            "/p:TargetName=libdlgmod",
        ]
    )

    dll = vc_out / "libdlgmod.dll"
    if not dll.is_file():
        candidates = sorted(vc_out.rglob("*.dll"))
        if not candidates:
            raise SystemExit("error: build produced no DLL")
        dll = candidates[0]
    log(f"built: {dll}")

    dist = repo / "dist"
    windows_dist = dist / "windows"
    windows_dist.mkdir(parents=True, exist_ok=True)
    final_dll = windows_dist / "libdlgmod.dll"
    shutil.copy2(dll, final_dll)
    shutil.copy2(rename_map, dist / "rename_map.json")
    log(f"copied: {final_dll}")

    run(
        [
            sys.executable,
            str(TOOLS_DIR / "patch_extension.py"),
            "--repo",
            str(repo),
            "--map",
            str(rename_map),
            "--out-dir",
            str(dist / "extensions" / "libdlgmod"),
        ]
    )

    verify_cmd = [
        sys.executable,
        str(TOOLS_DIR / "verify_exports.py"),
        "--binary",
        str(final_dll),
        "--map",
        str(rename_map),
    ]
    if args.mode == "collisions":
        verify_cmd.append("--allow-extra")
    run(verify_cmd)

    if not args.keep_build:
        shutil.rmtree(build, ignore_errors=True)
        log("removed build workspace")
    log("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
