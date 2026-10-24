#!/usr/bin/env bash
#
# Build the Linux GameMaker shared objects of libdlgmod with prefixed exports.
#
# The upstream repo is a GameMaker project; the C++ sources/build scripts live
# inside extensions/libdlgmod/libdlgmod.zip. They are extracted into
# .build/linux and the forced include (renames.h) is injected there only.
#
# Usage:
#   bash tools/build_linux.sh
#
# Environment:
#   BUILD_ARM=1   require arm32/arm64 cross compilers (fail if missing)
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="${ROOT}/.build/linux"
DIST="${ROOT}/dist/linux"

rm -rf "${WORK}" "${DIST}"
mkdir -p "${DIST}"

SRC_ROOT="$(python3 "${ROOT}/tools/extract_source.py" --repo "${ROOT}" --dest "${WORK}/src")"
UPSTREAM="$(dirname "$(find "${SRC_ROOT}" -name build-gm.sh -print -quit)")"
if [ ! -f "${UPSTREAM}/build-gm.sh" ]; then
    echo "error: extracted upstream build-gm.sh not found" >&2
    exit 1
fi
echo "[build-linux] upstream root: ${UPSTREAM}"

python3 "${ROOT}/tools/gen_renames.py" \
    --source-dir "${UPSTREAM}" \
    --out-header "${UPSTREAM}/renames.h" \
    --out-map "${UPSTREAM}/rename_map.json" \
    --builtin-list "${ROOT}/tools/gm_builtin_functions.txt" \
    --mode all

# Some distros only ship plain g++/clang++; the upstream scripts call
# Debian-style cross compiler names. Provide symlinks when needed.
SHIM="${WORK}/toolchain-shim"
mkdir -p "${SHIM}"
shim_compiler() {
    local name="$1" fallback="$2"
    if ! command -v "${name}" >/dev/null 2>&1 && command -v "${fallback}" >/dev/null 2>&1; then
        ln -sf "$(command -v "${fallback}")" "${SHIM}/${name}"
    fi
}
shim_compiler x86_64-linux-gnu-g++ g++
# Do NOT alias the arm cross compilers to the host g++: that would silently
# produce x86_64 objects named libdlgmod_arm*.so. Arm builds are skipped unless
# a real cross toolchain is installed.
export PATH="${SHIM}:${PATH}"

for script in build-gm-x86_64.sh build-gm-x86_32.sh build-gm-arm32.sh build-gm-arm64.sh; do
    [ -f "${UPSTREAM}/${script}" ] || continue
    sed -i -E \
        's#(^|[[:space:]])(x86_64-linux-gnu-g\+\+|i686-linux-gnu-g\+\+|aarch64-linux-gnu-g\+\+|arm-linux-gnueabihf-g\+\+|g\+\+|clang\+\+)#\1\2 -include renames.h#g' \
        "${UPSTREAM}/${script}"
done

echo "[build-linux] build-gm-x86_64.sh"
( cd "${UPSTREAM}" && bash build-gm-x86_64.sh )
cp "${UPSTREAM}/libdlgmod-gm-x86_64.so" "${DIST}/libdlgmod.so"

build_arm() {
    local script="$1" compiler="$2" output="$3" dest="$4"
    if ! command -v "${compiler}" >/dev/null 2>&1; then
        if [ "${BUILD_ARM:-0}" = "1" ]; then
            echo "error: ${compiler} is required but not installed" >&2
            exit 1
        fi
        echo "[build-linux] skip ${dest} (${compiler} not installed)"
        return 0
    fi
    echo "[build-linux] ${script}"
    ( cd "${UPSTREAM}" && bash "${script}" )
    cp "${UPSTREAM}/${output}" "${DIST}/${dest}"
    python3 "${ROOT}/tools/verify_exports.py" \
        --binary "${DIST}/${dest}" \
        --map "${UPSTREAM}/rename_map.json"
}

build_arm build-gm-arm32.sh arm-linux-gnueabihf-g++ libdlgmod-gm-arm32.so libdlgmod_arm.so
build_arm build-gm-arm64.sh aarch64-linux-gnu-g++ libdlgmod-gm-arm64.so libdlgmod_arm64.so

cp "${UPSTREAM}/rename_map.json" "${ROOT}/dist/rename_map.json"
python3 "${ROOT}/tools/patch_extension.py" \
    --repo "${ROOT}" \
    --map "${UPSTREAM}/rename_map.json" \
    --out-dir "${ROOT}/dist/extensions/libdlgmod"

python3 "${ROOT}/tools/verify_exports.py" \
    --binary "${DIST}/libdlgmod.so" \
    --map "${UPSTREAM}/rename_map.json"

echo "[build-linux] outputs:"
ls -l "${DIST}"
