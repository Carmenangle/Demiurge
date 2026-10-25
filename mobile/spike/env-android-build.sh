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
