#!/usr/bin/env bash
# 手机端前端出包单一入口（M7；0.4.4 F1 起含「打 zip + 入包身份」）：
#   重建 dist → 自检（含负例自检）→ 打 assets/frontend.zip（确定性、全 DEFLATE）→ 打印身份。
#   VITE_API_BASE=/api            同源 API（PLAN §9）
#   VITE_COMFY_PROGRESS_PROXY=1   进度 WS 走后端中继（M7，lib/comfyProgress.ts）
#   VITE_MOBILE_PROFILE=1         手机端形态（设置页风险提示 + PC 主机登记，lib/mobileProfile.ts）
# PC 桌面包不受影响：不带这些变量构建即桌面形态。
# 用法（Git Bash）：bash mobile/spike/build-frontend-mobile.sh
# 判据：dist 无 MSYS 盘符泄漏、含 /api 与 /api/comfyui/ws（后者带负例自检）；
#       zip 成员相对 dist 根、全部 DEFLATE、文件数与 dist 一致。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

echo "[1/3] vite build -> mobile/spike/frontend-mobile-dist"
cd "$ROOT/frontend"
# MSYS_NO_PATHCONV=1 必带：否则 Git Bash 把 /api 改写成 Windows 盘符路径进产物
# （PLAN §9 教训，0.3.2 及更早的手机包全带错误基址，只有联机才暴露）。
# outDir 必须用相对路径：绝对 /d/... 会被 node 当成盘符根下的路径。
MSYS_NO_PATHCONV=1 VITE_API_BASE=/api VITE_COMFY_PROGRESS_PROXY=1 VITE_MOBILE_PROFILE=1 \
  npx vite build --outDir ../mobile/spike/frontend-mobile-dist --emptyOutDir

echo "[2/3] dist 自检"
cd "$ROOT"
python - <<'PY'
import pathlib, sys

dist = pathlib.Path("mobile/spike/frontend-mobile-dist")
files = sorted(p for p in dist.rglob("*") if p.is_file())
if not files:
    sys.exit("FAIL: dist 为空")
if not (dist / "index.html").is_file():
    sys.exit("FAIL: dist 根缺 index.html")

# 只看代码/标记文件：dist 里大部分是图片字节，没必要读进来
texts = {p: p.read_bytes() for p in files if p.suffix in (".js", ".html", ".css")}
if not texts:
    sys.exit("FAIL: dist 里没有 .js/.html/.css")

if any(b"develop/Git" in b for b in texts.values()):
    sys.exit("FAIL: 产物残留 develop/Git（MSYS 路径改写泄漏）")

# minifier 会把字符串字面量规范化成模板串（本轮实测 `` `/api` ``），故引号不参与判据
API_BASE = (b'"/api"', b"'/api'", b"`/api`")
if not any(pat in b for b in texts.values() for pat in API_BASE):
    sys.exit("FAIL: 产物缺少同源 /api 基址")

WS = b"/api/comfyui/ws"
def has_ws_literal(blobs_):
    return any(WS in b for b in blobs_.values())

if not has_ws_literal(texts):
    sys.exit("FAIL: 产物缺少进度 WS 代理端点 /api/comfyui/ws（VITE_COMFY_PROGRESS_PROXY 没生效？）")
# 负例自检：检查函数对「不含该字面量」的输入必须判假，否则这套闸本身就是绿的假的。
if has_ws_literal({pathlib.Path("neg.js"): b"no endpoint here\n"}):
    sys.exit("FAIL: 负例自检失败——检查函数对不存在的字面量也判真")

hits = sum(1 for b in texts.values() if WS in b)
print(f"  dist 文件数 = {len(files)}；/api/comfyui/ws 命中文件数 = {hits}")
PY

echo "[3/3] 打 frontend.zip（成员相对 dist 根，确定性时间戳；全成员 DEFLATE）"
python - <<'PY'
import hashlib, pathlib, sys, zipfile

dist = pathlib.Path("mobile/spike/frontend-mobile-dist")
asset = pathlib.Path("mobile/spike/android-spike/app/src/main/assets/frontend.zip")
files = sorted(p for p in dist.rglob("*") if p.is_file())

# 全部成员 DEFLATE（level 9）。**不能**因为「图片已压缩」就 STORED：
# 0.4.3 旧包 385 个成员全 DEFLATE（106,443,180 B 内容 → 103,322,546 B 文件，PNG 也能省 2~4%）；
# 0.4.4 实测把媒体改 STORED 会白涨 1.05 MB，直接破 190 MB 线。
# APK 顶层的 noCompress("zip") 只影响 frontend.zip 这一层，不影响 zip 内部。
asset.parent.mkdir(parents=True, exist_ok=True)
tmp = asset.with_name(asset.name + ".tmp")
content = 0
with zipfile.ZipFile(tmp, "w", compresslevel=9) as zf:
    for p in files:
        data = p.read_bytes()
        content += len(data)
        info = zipfile.ZipInfo(p.relative_to(dist).as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
        info.external_attr = 0o644 << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        zf.writestr(info, data)
tmp.replace(asset)

raw = asset.read_bytes()
with zipfile.ZipFile(asset) as zf:
    names = zf.namelist()
    if len(names) != len(files):
        sys.exit(f"FAIL: zip 成员数 {len(names)} != dist 文件数 {len(files)}")
    stored = [n for n in names if zf.getinfo(n).compress_type != zipfile.ZIP_DEFLATED]
    if stored:
        sys.exit(f"FAIL: {len(stored)} 个成员没压缩（例如 {stored[:3]}）⇒ 体积线会被冲掉")
print(f"  源文件   = {asset}")
print(f"  字节数   = {len(raw):,}")
print(f"  sha256   = {hashlib.sha256(raw).hexdigest()}")
print(f"  文件数   = {len(names)}（内容 {content:,} B → 压缩后 {len(raw):,} B，{len(raw)/content:.3%}）")
PY
