"""APK 体积账本与三条静态断言（只读，不写任何文件）。

为什么存在（三条都是实锤过的坑）：
1. **换 wheel ≠ 换包**。Chaquopy 的 `installDebugPythonRequirements` 会 UP-TO-DATE，
   wheel 内容变了但文件名/版本没变 ⇒ imy 里还是旧 `.so`，APK 尺寸纹丝不动。
2. **增量打包会留白**。AGP 重写单个成员后不压实空闲区 ⇒ 「文件字节数」比「成员字节之和」大几十 MB，
   装到手机上占的就是文件字节数 ⇒ 体积刀看着没生效。
3. **换前端 ≠ 换包内前端**（0.4.4 F1）。`build-frontend-mobile.sh` 只产出 dist；
   若忘记重打 `assets/frontend.zip`（或它没被重新读入），包内仍是旧前端，构建照样 SUCCESS。

判据（任一不满足即 exit 1）：
- 相邻成员之间的空洞 ≤ 1 MiB（留白＝必须 `:app:clean` 重打）。
- `chromadb_rust_bindings…so` 在 imy 内的**原始大小** < 30 MiB（dev profile 是 230 MiB）。
- APK 内 `assets/frontend.zip` 的 sha256 == 仓库里 `src/main/assets/frontend.zip` 的 sha256
  （顶层 STORED ⇒ 字节应逐字相同；不等即「源换了、包没换」）。

用法：backend/.venv/Scripts/python.exe mobile/spike/apk_size_ledger.py [apk 路径]
"""
from __future__ import annotations

import hashlib
import io
import struct
import sys
import zipfile
from pathlib import Path

DEFAULT_APK = Path("mobile/spike/android-spike/app/build/outputs/apk/debug/app-debug.apk")
FRONTEND_ASSET = Path("mobile/spike/android-spike/app/src/main/assets/frontend.zip")
GAP_LIMIT = 1 * 1024 * 1024
RUST_SO_LIMIT = 30 * 1024 * 1024
RUST_SO_NAME = "chromadb_rust_bindings.cpython-313-aarch64-linux-android.so"


def fail(msg: str, errs: list[str]) -> None:
    print(f"❌ {msg}")
    errs.append(msg)


def main() -> int:
    # 闸脚本不能在自己的失败路径上崩：Windows 控制台默认 GBK，中文/❌ 会抛 UnicodeEncodeError
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    apk = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_APK
    if not apk.is_file():
        print(f"❌ 找不到 APK：{apk}")
        return 1
    raw = apk.read_bytes()
    n = len(raw)
    z = zipfile.ZipFile(apk)
    members = z.infolist()
    sum_c = sum(m.compress_size for m in members)
    print(f"APK      = {apk}")
    print(f"字节数   = {n:,}  ({n / 1000**2:.2f} MB 十进制 / {n / 2**20:.2f} MiB)")
    print(f"sha256   = {hashlib.sha256(raw).hexdigest()}")
    print(f"成员数   = {len(members)}  Σ(压缩字节) = {sum_c:,}  结构开销 = {n - sum_c:,}")

    print("\n分项账本（前 10，APK 顶层全 STORED）")
    for m in sorted(members, key=lambda a: -a.compress_size)[:10]:
        print(f"  {m.compress_size:>12,}  {m.filename}")

    errs: list[str] = []

    # 断言 1：成员之间不得有大空洞（增量打包留白）
    prev_end = 0
    worst = (0, 0)
    for m in sorted(members, key=lambda a: a.header_offset):
        gap = m.header_offset - prev_end
        if gap > worst[1]:
            worst = (prev_end, gap)
        name_len, extra_len = struct.unpack_from("<HH", raw, m.header_offset + 26)
        prev_end = m.header_offset + 30 + name_len + extra_len + m.compress_size
    if worst[1] > GAP_LIMIT:
        fail(f"打包留白 {worst[1]:,} B（偏移 {worst[0]:,}）> 1 MiB ⇒ 必须 :app:clean 后重打", errs)

    # 断言 2：Rust 核心必须是瘦 profile
    imy_name = "assets/chaquopy/requirements-common.imy"
    imy = next((m for m in members if m.filename == imy_name), None)
    if imy is None:
        fail(f"缺成员 {imy_name}（Chaquopy 依赖包没进 APK？）", errs)
    else:
        inner = zipfile.ZipFile(io.BytesIO(z.read(imy_name)))
        so = next((a for a in inner.infolist() if a.filename == RUST_SO_NAME), None)
        if so is None:
            fail(f"imy 里没有 {RUST_SO_NAME} ⇒ RAG 必坏", errs)
        elif so.file_size >= RUST_SO_LIMIT:
            fail(f"{RUST_SO_NAME} 原始 {so.file_size:,} B ≥ {RUST_SO_LIMIT:,} B ⇒ 仍是 dev profile", errs)
        else:
            print(f"\nRust 核心：原始 {so.file_size:,} B / imy 内压缩 {so.compress_size:,} B（瘦 profile 已入包）")
        print(f"imy 成员数（dist-info 计数依赖此项）= {len(inner.infolist())}")

    # 断言 3：包内前端 zip == 仓库源 zip（「换了没进包」的唯一硬判据）
    fe_name = "assets/frontend.zip"
    fe = next((m for m in members if m.filename == fe_name), None)
    if fe is None:
        fail(f"缺成员 {fe_name}（前端没进包？）", errs)
    elif not FRONTEND_ASSET.is_file():
        fail(f"缺源文件 {FRONTEND_ASSET}（先跑 build-frontend-mobile.sh）", errs)
    else:
        apk_sha = hashlib.sha256(z.read(fe_name)).hexdigest()
        src_sha = hashlib.sha256(FRONTEND_ASSET.read_bytes()).hexdigest()
        if apk_sha != src_sha:
            fail(
                f"包内 frontend.zip 与源不同（包 {apk_sha[:16]}… vs 源 {src_sha[:16]}…）"
                " ⇒ 前端没进包，必须 :app:clean 后重打",
                errs,
            )
        else:
            print(f"\n前端包：{fe.file_size:,} B / sha256 {apk_sha[:16]}…（与源逐字相同）")

    print("\n" + (
        "❌ 账本判定失败：" + "；".join(errs)
        if errs
        else "✅ 账本判定通过（无留白、Rust 核心已瘦身、前端包与源一致）"
    ))
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
