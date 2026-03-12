#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

APP_NAME="SlaytheSpire2DrawingMac"
ENTRY_SCRIPT="spire_painter_mac.py"
DIST_DIR="dist"
WORK_DIR="build"
SPEC_DIR="build/spec"
PY_BIN="${PYTHON_BIN:-python3}"
REQUESTED_ARCH="${TARGET_ARCH:-universal2}"

install_deps() {
  "${PY_BIN}" -m pip install -r requirements.txt pyinstaller
}

native_arch() {
  case "$(uname -m)" in
    arm64|aarch64)
      echo "arm64"
      ;;
    x86_64)
      echo "x86_64"
      ;;
    *)
      echo ""
      ;;
  esac
}

fix_internal_runtime() {
  local internal_dir="$1"
  local build_cache_dir="$2"

  if [[ ! -d "${internal_dir}" ]]; then
    return 0
  fi

  local framework_python
  framework_python="$(find "${internal_dir}" -maxdepth 5 -type f -path '*/Python.framework/*/Python' | head -n 1 || true)"

  if [[ -n "${framework_python}" && ! -e "${internal_dir}/Python" ]]; then
    local rel_path
    rel_path="${framework_python#${internal_dir}/}"
    ln -sfn "${rel_path}" "${internal_dir}/Python"
  fi

  if [[ ! -f "${internal_dir}/base_library.zip" && -f "${build_cache_dir}/base_library.zip" ]]; then
    cp -f "${build_cache_dir}/base_library.zip" "${internal_dir}/base_library.zip"
  fi
}

run_pyinstaller() {
  local target_arch="$1"
  local log_file="$2"

  "${PY_BIN}" -m PyInstaller \
    --noconfirm \
    --clean \
    --windowed \
    --onedir \
    --name "${APP_NAME}" \
    --target-arch "${target_arch}" \
    --distpath "${DIST_DIR}" \
    --workpath "${WORK_DIR}" \
    --specpath "${SPEC_DIR}" \
    "${ENTRY_SCRIPT}" >"${log_file}" 2>&1
}

# Clean old artifacts to prevent mixed runtime files.
rm -rf "${DIST_DIR}" "${WORK_DIR}"
mkdir -p "${SPEC_DIR}"

install_deps

BUILD_ARCH="${REQUESTED_ARCH}"
LOG_FILE="${WORK_DIR}/pyinstaller-${BUILD_ARCH}.log"

if ! run_pyinstaller "${BUILD_ARCH}" "${LOG_FILE}"; then
  if [[ "${BUILD_ARCH}" == "universal2" ]] && grep -q "IncompatibleBinaryArchError" "${LOG_FILE}"; then
    FALLBACK_ARCH="$(native_arch)"
    if [[ -z "${FALLBACK_ARCH}" ]]; then
      echo "ERROR: 无法识别当前机器架构，且 universal2 构建失败。" >&2
      tail -n 80 "${LOG_FILE}" >&2
      exit 1
    fi

    echo "universal2 构建失败（依赖非 fat binary），自动回退到 ${FALLBACK_ARCH}..."

    rm -rf "${DIST_DIR}" "${WORK_DIR}"
    mkdir -p "${SPEC_DIR}"

    BUILD_ARCH="${FALLBACK_ARCH}"
    LOG_FILE="${WORK_DIR}/pyinstaller-${BUILD_ARCH}.log"

    if ! run_pyinstaller "${BUILD_ARCH}" "${LOG_FILE}"; then
      echo "ERROR: ${BUILD_ARCH} 构建也失败。" >&2
      tail -n 120 "${LOG_FILE}" >&2
      exit 1
    fi
  else
    echo "ERROR: 构建失败。" >&2
    tail -n 120 "${LOG_FILE}" >&2
    exit 1
  fi
fi

APP_BUNDLE="${DIST_DIR}/${APP_NAME}.app"
ONE_DIR="${DIST_DIR}/${APP_NAME}"
BUILD_CACHE="${WORK_DIR}/${APP_NAME}"

# Repair known PyInstaller 6 packaging edge-cases observed on this project.
fix_internal_runtime "${ONE_DIR}/_internal" "${BUILD_CACHE}"
fix_internal_runtime "${APP_BUNDLE}/Contents/Frameworks" "${BUILD_CACHE}"

if [[ ! -d "${APP_BUNDLE}" ]]; then
  echo "ERROR: 未生成 .app 文件: ${APP_BUNDLE}" >&2
  exit 1
fi

if [[ ! -x "${APP_BUNDLE}/Contents/MacOS/${APP_NAME}" ]]; then
  echo "ERROR: .app 主可执行文件缺失: ${APP_BUNDLE}/Contents/MacOS/${APP_NAME}" >&2
  exit 1
fi

echo
echo "打包完成（架构: ${BUILD_ARCH}）: ${APP_BUNDLE}"
echo "启动方式: open \"${APP_BUNDLE}\""
