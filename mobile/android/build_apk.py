"""不依赖 Gradle 的 APK 构建：aapt2 编译资源 → javac → d8 → zipalign → apksigner。

用法（Windows / Git Bash 均可）：
    python mobile/android/build_apk.py

环境变量：ANDROID_HOME（默认 D:/tool/android-sdk）、JAVA_HOME（需 JDK 17+）。
产物：mobile/android/build/demiurge-shell-debug.apk
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP = ROOT / "app" / "src" / "main"
BUILD = ROOT / "build"
APK_NAME = "demiurge-shell-debug.apk"

SDK = Path(os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT") or r"D:\tool\android-sdk")
JAVA_HOME = Path(os.environ.get("JAVA_HOME") or r"D:\tool\JAVA\JDK_21")


def newest_dir(parent: Path) -> Path:
    dirs = [p for p in parent.iterdir() if p.is_dir()]
    if not dirs:
        raise SystemExit(f"目录为空: {parent}")
    return sorted(dirs, key=lambda p: p.name)[-1]


def tool(name: str) -> Path:
    # Windows 下 apksigner 只有 .bat，aapt2/zipalign 是 .exe。
    suffixes = (".exe", ".bat") if os.name == "nt" else ("",)
    for suffix in suffixes:
        path = BUILD_TOOLS / (name + suffix)
        if path.is_file():
            return path
    raise SystemExit(f"找不到 {BUILD_TOOLS / name}(.exe|.bat)")


def run(args: list[str], **kwargs) -> None:
    printable = " ".join(str(a) for a in args)
    print(f"$ {printable}")
    subprocess.run([str(a) for a in args], check=True, **kwargs)


def main() -> None:
    global BUILD_TOOLS
    if not (SDK / "build-tools").is_dir():
        raise SystemExit(f"SDK 缺少 build-tools: {SDK}")
    BUILD_TOOLS = newest_dir(SDK / "build-tools")
    platform = newest_dir(SDK / "platforms")
    android_jar = platform / "android.jar"
    java = JAVA_HOME / "bin" / ("java.exe" if os.name == "nt" else "java")
    javac = JAVA_HOME / "bin" / ("javac.exe" if os.name == "nt" else "javac")
    keytool = JAVA_HOME / "bin" / ("keytool.exe" if os.name == "nt" else "keytool")
    for required in (android_jar, java, javac, keytool):
        if not required.is_file():
            raise SystemExit(f"缺少 {required}")

    if BUILD.exists():
        shutil.rmtree(BUILD)
    (BUILD / "classes").mkdir(parents=True)
    (BUILD / "dex").mkdir()
    (BUILD / "gen").mkdir()

    res_zip = BUILD / "res.zip"
    run([tool("aapt2"), "compile", "--dir", APP / "res", "-o", res_zip])

    base_apk = BUILD / "base.apk"
    run([
        tool("aapt2"), "link", "-o", base_apk,
        "-I", android_jar,
        "--manifest", APP / "AndroidManifest.xml",
        "--min-sdk-version", "24",
        "--target-sdk-version", "34",
        "--java", BUILD / "gen",
        res_zip,
    ])

    # 手写源码 + aapt2 生成的 R.java（MainActivity 引用了 R.string.*）。
    sources = sorted((APP / "java").rglob("*.java")) + sorted((BUILD / "gen").rglob("*.java"))
    if not sources:
        raise SystemExit("没有 Java 源文件")
    # 直接传源文件：Windows 下 javac 的 @argfile 会把反斜杠当转义符，路径会被吃掉。
    run([
        javac, "-encoding", "UTF-8", "-source", "11", "-target", "11",
        "-classpath", android_jar, "-d", BUILD / "classes", *sources,
    ])

    class_files = sorted((BUILD / "classes").rglob("*.class"))
    if not class_files:
        raise SystemExit("javac 未产出 class")
    run([
        java, "-cp", BUILD_TOOLS / "lib" / "d8.jar", "com.android.tools.r8.D8",
        "--lib", android_jar, "--min-api", "24", "--output", BUILD / "dex",
        *class_files,
    ])

    dex = BUILD / "dex" / "classes.dex"
    if not dex.is_file():
        raise SystemExit("d8 未产出 classes.dex")
    with zipfile.ZipFile(base_apk, "a") as apk:
        apk.write(dex, "classes.dex")

    aligned = BUILD / "aligned.apk"
    run([tool("zipalign"), "-f", "-p", "4", base_apk, aligned])

    keystore = BUILD / "debug.keystore"
    if not keystore.is_file():
        run([
            keytool, "-genkeypair", "-keystore", keystore,
            "-storepass", "android", "-keypass", "android",
            "-alias", "androiddebugkey", "-keyalg", "RSA", "-keysize", "2048",
            "-validity", "10000", "-dname", "CN=Android Debug,O=Android,C=US",
        ])

    out_apk = BUILD / APK_NAME
    run([
        tool("apksigner"), "sign",
        "--ks", keystore, "--ks-pass", "pass:android", "--key-pass", "pass:android",
        "--out", out_apk, aligned,
    ])
    run([tool("apksigner"), "verify", "--print-certs", out_apk])

    print(f"\nOK  {out_apk}  ({out_apk.stat().st_size / 1024 / 1024:.2f} MB)")


if __name__ == "__main__":
    sys.exit(main())
