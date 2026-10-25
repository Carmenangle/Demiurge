# Source this file before cross-compiling chromadb rust bindings for Android.
# Tools (protoc / cmake / ninja) live under mobile/spike/tools and are gitignored.

SPIKE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export SPIKE_ROOT

export ANDROID_NDK="D:/tool/android-sdk/ndk/28.2.13676358"
export NDK_BIN="$ANDROID_NDK/toolchains/llvm/prebuilt/windows-x86_64/bin"
export ANDROID_API=24
export ANDROID_ABI=arm64-v8a
export ANDROID_PLATFORM="android-$ANDROID_API"
export ANDROID_NDK_HOME="$ANDROID_NDK"
export ANDROID_NDK_ROOT="$ANDROID_NDK"
export CMAKE_TOOLCHAIN_FILE="$ANDROID_NDK/build/cmake/android.toolchain.cmake"

export CC_aarch64_linux_android="$NDK_BIN/aarch64-linux-android${ANDROID_API}-clang.cmd"
export CXX_aarch64_linux_android="$NDK_BIN/aarch64-linux-android${ANDROID_API}-clang++.cmd"
export AR_aarch64_linux_android="$NDK_BIN/llvm-ar.exe"
export RANLIB_aarch64_linux_android="$NDK_BIN/llvm-ranlib.exe"
export CARGO_TARGET_AARCH64_LINUX_ANDROID_LINKER="$NDK_BIN/aarch64-linux-android${ANDROID_API}-clang.cmd"
export CARGO_TARGET_AARCH64_LINUX_ANDROID_AR="$NDK_BIN/llvm-ar.exe"
export TARGET_CC="$CC_aarch64_linux_android"
export TARGET_CXX="$CXX_aarch64_linux_android"
export TARGET_AR="$AR_aarch64_linux_android"
export CFLAGS_aarch64_linux_android="-O2 -fPIC"
export CXXFLAGS_aarch64_linux_android="-O2 -fPIC"

export PROTOC="$SPIKE_ROOT/tools/protoc/bin/protoc.exe"
export CMAKE="$SPIKE_ROOT/tools/cmake-3.31.6-windows-x86_64/bin/cmake.exe"
export PATH="$SPIKE_ROOT/tools/cmake-3.31.6-windows-x86_64/bin:$SPIKE_ROOT/tools/ninja:$NDK_BIN:$PATH"

# Windows system proxy (127.0.0.1:7897) is dead and github.com is unreachable.
export NO_PROXY='*'
export no_proxy='*'
export GIT_CONFIG_COUNT=1
export GIT_CONFIG_KEY_0='url.https://gh-proxy.com/https://github.com/.insteadOf'
export GIT_CONFIG_VALUE_0='https://github.com/'

# utoipa-swagger-ui 的 build.rs 会在编译期用 curl 抓 swagger-ui 发布包（GitHub 直连不通 ⇒ curl 56）。
# 它的 URL 可用 SWAGGER_UI_DOWNLOAD_URL 覆盖，且支持 file: 协议（该 crate 的 default features 带
# `url`，走 Url::to_file_path 解析），所以指到 vendor 里那份 zip 就能完全离线。
# 版本必须与 build.rs 里的 SWAGGER_UI_DOWNLOAD_URL_DEFAULT 一致（当前 v5.17.14）；
# 这份 zip 从首次 dev 编译的 OUT_DIR 缓存里取出（target/aarch64-linux-android/debug/build/
# utoipa-swagger-ui-*/out/v5.17.14.zip），⇒ 与 G1 产物用的是同一份资源（补丁 #8）。
# 路径写 Windows 绝对形式：url crate 的 to_file_path 只在 "<盘符>:/" 形态下才认驱动器。
export SWAGGER_UI_DOWNLOAD_URL="file:///D:/tool/Demiurge/mobile/spike/vendor/v5.17.14.zip"
