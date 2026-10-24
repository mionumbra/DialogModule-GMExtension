#!/usr/bin/env bash
#
# Build the macOS universal dylib of libdlgmod with prefixed exports.
#
# The upstream repo is a GameMaker project; the C++ sources/build scripts live
# inside extensions/libdlgmod/libdlgmod.zip. They are extracted into
# .build/macos and the forced include (renames.h) is injected there only.
#
# Usage:
#   bash tools/build_macos.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="${ROOT}/.build/macos"
DIST="${ROOT}/dist/macos"

rm -rf "${WORK}" "${DIST}"
mkdir -p "${DIST}"

SRC_ROOT="$(python3 "${ROOT}/tools/extract_source.py" --repo "${ROOT}" --dest "${WORK}/src")"
UPSTREAM="$(dirname "$(find "${SRC_ROOT}" -name build-gm.sh -print -quit)")"
if [ ! -f "${UPSTREAM}/build-gm.sh" ]; then
    echo "error: extracted upstream build-gm.sh not found" >&2
    exit 1
fi
echo "[build-macos] upstream root: ${UPSTREAM}"

python3 "${ROOT}/tools/gen_renames.py" \
    --source-dir "${UPSTREAM}" \
    --out-header "${UPSTREAM}/renames.h" \
    --out-map "${UPSTREAM}/rename_map.json" \
    --builtin-list "${ROOT}/tools/gm_builtin_functions.txt" \
    --mode all

# BSD sed (macOS): inject the forced include into the extracted build script.
sed -i '' -E \
    's#(^|[[:space:]])(clang\+\+|g\+\+)#\1\2 -include renames.h#g' \
    "${UPSTREAM}/build-gm.sh"

echo "[build-macos] build-gm.sh"
( cd "${UPSTREAM}" && bash build-gm.sh )

cp "${UPSTREAM}/libdlgmod-gm.dylib" "${DIST}/libdlgmod.dylib"
cp "${UPSTREAM}/rename_map.json" "${ROOT}/dist/rename_map.json"
python3 "${ROOT}/tools/patch_extension.py" \
    --repo "${ROOT}" \
    --map "${UPSTREAM}/rename_map.json" \
    --out-dir "${ROOT}/dist/extensions/libdlgmod"

python3 "${ROOT}/tools/verify_exports.py" \
    --binary "${DIST}/libdlgmod.dylib" \
    --map "${UPSTREAM}/rename_map.json"

echo "[build-macos] outputs:"
ls -l "${DIST}"
