#!/bin/bash
# 手机底包专用入口：交叉编 chromadb 的 Rust 核心到 cargo [profile.mobile]（补丁 #7）。
#
# 为什么要落成脚本而不是一行命令——这条链上有两处「手写就会漏」的非显然前提，
# 本轮各踩了一次实锤：
#   补丁 #6：pyo3 开了 abi3-py39 且没开 extension-module ⇒ 链接器要 -lpython3.9，
#            得用 `cargo rustc ... -- -C link-arg=-L<pyrt jniLibs>` 只给最后一跳加搜索路径；
#            用 `cargo build` 会一路编到最后一个 crate 才报 `unable to find library -lpython3.9`（白编 4 分钟）。
#   补丁 #8：utoipa-swagger-ui 的 build.rs 编译期抓 GitHub 上的 swagger-ui zip，
#            本机 GitHub 不可达 ⇒ 由 env-android-build.sh 的 SWAGGER_UI_DOWNLOAD_URL 指到 vendor 里那份。
# 出口判据：.so 产出 + DT_NEEDED 记的是 libpython3.13.so（Chaquopy 运行时提供的那个名字）。
set -euo pipefail

SPIKE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE="${1:-mobile}"
ABI_DIR="aarch64-linux-android"

cd "$SPIKE/chromadb-1.5.9"
# shellcheck disable=SC1091
source "$SPIKE/env-android-build.sh"

PYRT="$SPIKE/pyrt-arm64/jniLibs/arm64-v8a"
if [ ! -f "$PYRT/libpython3.9.so" ]; then
  echo "缺 $PYRT/libpython3.9.so（补丁 #6）：它是 Chaquopy libpython3.13.so 的改名副本。" >&2
  echo "重取：cp <chaquopy>/python/3.13/arm64-v8a/libpython3.13.so $PYRT/libpython3.9.so" >&2
  exit 1
fi
# 链接器由 NDK 的 clang.cmd 驱动，只认 Windows 形态的 -L 路径
PYRT_WIN="$(cygpath -w "$PYRT" | sed 's#\\\\#/#g')"

cargo rustc -p chromadb_rust_bindings --profile "$PROFILE" --target "$ABI_DIR" --lib \
  -- "-C" "link-arg=-L$PYRT_WIN"

SO="target/$ABI_DIR/$PROFILE/libchromadb_rust_bindings.so"
echo "[build] $SO = $(stat -c %s "$SO") B"
echo "[build] 对照：dev 档同物约 230.7 MB（PLAN §15.5.1 账本）"
NEEDED="$( "$NDK_BIN/llvm-readelf.exe" -d "$SO" | grep -a NEEDED )"
echo "$NEEDED"
if ! grep -q "libpython3.13.so" <<<"$NEEDED"; then
  echo "🔴 DT_NEEDED 里没有 libpython3.13.so ⇒ 真机上 Chaquopy 运行时供不上，装了也加载不了" >&2
  exit 1
fi
echo "[build] PASS：产出可被 Chaquopy 运行时解析（补丁 #6 生效）"
