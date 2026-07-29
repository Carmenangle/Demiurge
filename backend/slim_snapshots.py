"""一次性清洗历史快照，瘦身两类大图（与前端 slimSnapshot 同规则）：
1. portsPlan.images：applied/ignored 清空副本；pending 保留。
2. 用户消息 parts 里的 data:URI 大图：解码落盘到 _offloaded/，替换成 local-view 地址
   （原图全质量留盘，呈现不变，可悬浮看大图/再添加到对话）。
绝不碰 message.image（成品图）与 workflow.capturedGraph（/s 出图依赖）。
清洗前对每个被改文件原子备份为 .bak。
"""
import base64
import io
import json
import re
from pathlib import Path
from uuid import uuid4

SNAP_DIR = Path(__file__).resolve().parent / "data" / "chat_snapshots"
OFFLOAD_DIR = SNAP_DIR / "_offloaded"
LOCAL_VIEW_BASE = "http://127.0.0.1:8010/api/comfyui/local-view"


def _offload_data_uri(src: str) -> str:
    """把 data:URI 解码写盘，返回 local-view 地址；失败原样返回。"""
    if not (isinstance(src, str) and src.startswith("data:")):
        return src
    try:
        header, b64 = src.split(",", 1)
        data = base64.b64decode(re.sub(r"\s+", "", b64))
        ext = "png"
        if "image/" in header:
            ext = header.split("image/")[1].split(";")[0] or "png"
        OFFLOAD_DIR.mkdir(parents=True, exist_ok=True)
        dest = OFFLOAD_DIR / f"{uuid4().hex}.{re.sub(r'[^a-z0-9]', '', ext.lower()) or 'png'}"
        dest.write_bytes(data)
        from urllib.parse import quote
        return f"{LOCAL_VIEW_BASE}?path={quote(str(dest))}"
    except Exception:
        return src


def slim_messages(msgs: list) -> tuple[list, int, int]:
    """返回 (新消息列表, portsPlan清空张数, parts落盘张数)。"""
    cleared = 0
    offloaded = 0
    out = []
    for m in msgs:
        if not isinstance(m, dict):
            out.append(m)
            continue
        # 1) 用户 parts 里的 data:URI → 落盘转 local-view
        parts = m.get("parts")
        if isinstance(parts, list) and any(
            isinstance(p, dict) and p.get("type") == "image"
            and isinstance(p.get("url"), str) and p["url"].startswith("data:")
            for p in parts
        ):
            new_parts = []
            for p in parts:
                if (isinstance(p, dict) and p.get("type") == "image"
                        and isinstance(p.get("url"), str) and p["url"].startswith("data:")):
                    p = {**p, "url": _offload_data_uri(p["url"])}
                    offloaded += 1
                new_parts.append(p)
            m = {**m, "parts": new_parts}
        # 2) portsPlan.images
        pp = m.get("portsPlan")
        if pp and pp.get("images"):
            if pp.get("status") in ("applied", "ignored"):
                cleared += len(pp["images"])
                m = {**m, "portsPlan": {**pp, "images": []}}
            else:
                new_imgs = []
                for s in pp["images"]:
                    if isinstance(s, str) and s.startswith("data:"):
                        new_imgs.append(_offload_data_uri(s))
                        offloaded += 1
                    else:
                        new_imgs.append(s)
                m = {**m, "portsPlan": {**pp, "images": new_imgs}}
        out.append(m)
    return out, cleared, offloaded


def main():
    if not SNAP_DIR.is_dir():
        print("无快照目录：", SNAP_DIR)
        return
    for f in sorted(SNAP_DIR.glob("*.json")):
        before = f.stat().st_size
        try:
            data = json.load(io.open(f, encoding="utf-8"))
        except Exception as e:
            print(f"跳过（解析失败）{f.name}: {e}")
            continue
        if not isinstance(data, list):
            continue
        new, cleared, offloaded = slim_messages(data)
        if cleared == 0 and offloaded == 0:
            continue
        f.with_suffix(".json.bak").write_bytes(f.read_bytes())  # 备份
        tmp = f.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(new, ensure_ascii=False), encoding="utf-8")
        tmp.replace(f)  # 原子写回
        after = f.stat().st_size
        print(f"{f.name}: 清空 {cleared} 张 / 落盘 {offloaded} 张, "
              f"{before // 1024}KB -> {after // 1024}KB (备份 .bak)")
    print("完成。确认无误后可删除 *.json.bak。")


if __name__ == "__main__":
    main()

