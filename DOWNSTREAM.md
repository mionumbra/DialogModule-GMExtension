# Downstream build branch (`pcr`)

This branch builds the upstream [DialogModule-GMExtension] with **renamed DLL
exports** so the GameMaker project can call both the extension functions and the
GameMaker builtin functions they normally shadow.

It does **not** modify any upstream source file. All build customisation lives
in new files (`tools/`, `.github/`); the upstream tree is copied into `.build/`
and patched there at build time.

[DialogModule-GMExtension]: https://github.com/samuelvenable/DialogModule-GMExtension

## Why renaming the exports is necessary

GameMaker resolves function names at load time by name in **both** VM and YYC
builds. The lookup order is:

    scripts -> extensions -> builtins

An extension function matches by *both* its GML name and its `externalName`, and
extension lookup wins over builtins. So an extension exporting `show_message`
shadows the builtin `show_message` even if its GML function name is prefixed
(`ldm_show_message`).

The only fix is to rename the actual exports (externalName / exported symbol).
This branch builds every export as `ldm_<name>` (e.g. `show_message` ->
`ldm_show_message`), so:

* GameMaker builtins are no longer shadowed, and
* the extension is still fully callable through its `ldm_*` names.

All exports are prefixed by default (not only the names that currently collide
with a builtin), so future upstream additions are covered automatically.

## Branch layout

```
main   mirror of upstream/main. Never contains downstream changes.
pcr    this branch: adds .github/ + tools/ only, no upstream file edits.
fix/*  topic branches for pull requests to upstream, always based on
       upstream/main.
```

Keeping `main` clean means syncing upstream is a fast-forward and `pcr` can be
rebased without merge conflicts. PR branches started from `upstream/main` never
contain the downstream build changes.

## Files

| Path | Purpose |
| --- | --- |
| `tools/gen_renames.py` | scans `EXPORTED_FUNCTION` declarations, writes `renames.h` + `rename_map.json` |
| `tools/gm_builtin_functions.txt` | snapshot of GameMaker builtin function names (collision report) |
| `tools/build_windows.py` | copies tree, injects forced include, builds `libdlgmod.dll` with MSBuild, verifies PE exports |
| `tools/build_linux.sh` | same for `libdlgmod.so` (+ optional arm32/arm64) |
| `tools/build_macos.sh` | same for universal `libdlgmod.dylib` |
| `tools/verify_exports.py` | checks the binary exports exactly the renamed symbols |
| `.github/workflows/build-downstream.yml` | CI: builds all platforms, publishes release assets on `pcr-*` tags |
| `upstream-lock.txt` | upstream ref / GameMaker runtime used for the last build |

## Local builds

Requirements: Python 3.9+.

Windows (MSVC + MSBuild):

```bat
python tools\build_windows.py
```

The script auto-detects MSBuild and falls back to the newest installed MSVC
toolset when `v143` is not installed (e.g. it uses `v145` on VS 2026).
It additionally compiles with `/utf-8` because the upstream sources contain
UTF-8 string literals.

Linux:

```bash
sudo apt-get install -y build-essential pkg-config libx11-dev libxrandr-dev libdbus-1-dev
bash tools/build_linux.sh
```

arm32/arm64 builds are skipped unless their cross compilers are installed; set
`BUILD_ARM=1` to make missing compilers an error.

macOS (Xcode command line tools):

```bash
bash tools/build_macos.sh
```

Outputs (also produced by CI):

```
dist/windows/libdlgmod.dll
dist/linux/libdlgmod.so
dist/macos/libdlgmod.dylib
dist/rename_map.json
```

Each build script runs `verify_exports.py` and fails if any export is missing or
still uses an old (unprefixed, possibly builtin-shadowing) name.

## Build artifacts

`.build/`, `dist/`, `tools/__pycache__/` and (for standalone runs of
`gen_renames.py`) `renames.h` / `rename_map.json` are build outputs and must not
be committed. The build scripts keep generated files under `.build/` / `dist/`.
To hide them from `git status`, add them to `.git/info/exclude` rather than
modifying the upstream `.gitignore` — the fork branch stays addition-only, which
keeps upstream syncs conflict-free.

## CI / releases

Pushing to `pcr` builds all three platforms as workflow artifacts. Pushing a tag
like `pcr-<upstream-ref>` also packages them into a GitHub release:

```
libdlgmod-downstream-pcr-<upstream-ref>.zip
  windows/libdlgmod.dll
  linux/libdlgmod.so
  macos/libdlgmod.dylib
  rename_map.json
```

Consuming a release in the GameMaker project:

1. download / unpack the release zip;
2. copy the binaries for the current platform into the project extension;
3. update the extension `.yy`: `name` and `externalName` for every function
   should be `ldm_<upstream name>` (exactly what `rename_map.json` says);
4. keep the project-local `libdlgmod.gml` init changes (e.g. the
   `ldm_widget_set_owner(...)` call).

A project-side helper for step 3/4 can be added to the project repo
(`dev/update_libdlgmod.py`) and consume `rename_map.json` directly.

## Updating from upstream

```bash
git fetch upstream --tags
git switch main
git merge --ff-only upstream/main
git push origin main

git switch pcr
git rebase main
git push --force-with-lease origin pcr
```

Then bump `upstream_ref` in `upstream-lock.txt`, push/tag, and rebuild.

Only when upstream renames or restructures its build files do the tools need
adapting; the scripts locate the `.vcxproj` / `build-gm.sh` by name, so normal
file additions/renames are handled automatically.

## Pull requests to upstream

```bash
git fetch upstream
git switch -c fix/some-bug upstream/main
# edit upstream code
git push -u origin fix/some-bug
gh pr create --repo samuelvenable/DialogModule-GMExtension \
  --head <your-user>:fix/some-bug --base main
```

PR branches are based on `upstream/main` and must not include the downstream
build files.
