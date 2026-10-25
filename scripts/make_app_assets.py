"""从一张方形主图重生成全部「封面」产物（桌面壳图标 / 内嵌后端 exe 图标 /
移动端启动图标 / 前端 favicon / GitHub 分享卡）。

主图源：`assets/app-icon-source.png`（1024 方形，仓内真源；外部 ComfyUI 产物
每次换图先归一到它，再跑本脚本）。

两种取景：
- 图标族用 `ICON_*` 方形取景（略偏上，保住脸部与纱帽），因为 16–48 px 只剩主体可辨；
- 分享卡用 2:1 横条取景（`CARD_*`），GitHub social preview 固定 1280×640。

用法：`python scripts/make_app_assets.py`
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets" / "app-icon-source.png"
OUT_DIR = ROOT / "desktop" / "tauri" / "icons"

ICO_SIZES = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
# 方形取景：边长占主图比例 + 取景中心（0..1，y 偏上让脸与帽子占满小图标）
ICON_SIDE_RATIO, ICON_CX, ICON_CY = 0.72, 0.50, 0.44
# 横条取景：高度占比 + 中心
CARD_H_RATIO, CARD_CX, CARD_CY = 0.50, 0.50, 0.42
CARD_SIZE = (1280, 640)

# 换图必须同步的产物：目标路径 → (取景框, 输出尺寸)
TARGETS: dict[str, tuple[str, tuple[int, int]]] = {
    "desktop/tauri/icons/icon.ico": ("icon", (256, 256)),
    "desktop/Demiurge.Desktop/Assets/app.ico": ("icon", (256, 256)),
    "mobile/android/app/src/main/res/mipmap-xxxhdpi/ic_launcher.png": ("icon", (256, 256)),
    "frontend/public/demiurge-icon.png": ("icon", (256, 256)),
    # GitHub social preview 上传上限 1 MB，1280×640 的 PNG 会超（实测 1.39 MB）⇒ 出 JPG。
    "assets/github-social-preview.jpg": ("card", CARD_SIZE),
}


def _crop(img: Image.Image, kind: str) -> Image.Image:
    w, h = img.size
    if kind == "card":
        cw, ch = w, int(h * CARD_H_RATIO)
        cx, cy = int(w * CARD_CX), int(h * CARD_CY)
    else:
        side = int(min(w, h) * ICON_SIDE_RATIO)
        cx, cy = int(w * ICON_CX), int(h * ICON_CY)
        cw = ch = side
    left, top = max(0, cx - cw // 2), max(0, cy - ch // 2)
    right, bottom = min(w, left + cw), min(h, top + ch)
    return img.crop((left, top, right, bottom))


def main() -> None:
    src = Image.open(SOURCE).convert("RGB")
    if src.width != src.height:
        raise SystemExit(f"主图必须是方形，当前 {src.size}")
    cache: dict[str, Image.Image] = {}
    for rel, (kind, size) in TARGETS.items():
        out = ROOT / Path(rel)
        out.parent.mkdir(parents=True, exist_ok=True)
        region = cache.setdefault(kind, _crop(src, kind))
        img = region.resize(size, Image.LANCZOS)
        if out.suffix == ".ico":
            img.save(out, format="ICO", sizes=ICO_SIZES)
        elif out.suffix in (".jpg", ".jpeg"):
            img.save(out, quality=90, optimize=True, progressive=True)
        else:
            img.save(out, optimize=True)
        print(f"{rel}  {img.size[0]}x{img.size[1]}  {out.stat().st_size} B")


if __name__ == "__main__":
    main()
