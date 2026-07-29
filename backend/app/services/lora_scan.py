"""扫 loras 目录 + 从模型文件里提触发词。纯本地、零新依赖。

触发词来源按可靠性排序（前者缺失才退后者）：
1. safetensors 文件头的 `__metadata__`（kohya sd-scripts 训练产物带 ss_* 字段）
2. 同名 sidecar（.civitai.info / .json）里的 trainedWords —— 各类下载器常留
3. 用户手填（不在本模块，见 lora_index/routers）

safetensors 文件头格式：前 8 字节小端 uint64 = JSON 头长度，紧跟该长度的 JSON。
只读头部，不加载张量，故无需 safetensors/torch 依赖。
"""
from __future__ import annotations

import json
import struct
from pathlib import Path

# ComfyUI 的 LoraLoader 认这些扩展名
LORA_SUFFIXES = {".safetensors", ".pt", ".ckpt"}

# 头部 JSON 的合理上限，防御损坏/恶意文件报出天文数字长度后一次性读爆内存
_MAX_HEADER = 32 * 1024 * 1024

def scan_lora_dir(loras_dir: str | Path) -> list[str]:
    """递归列出 loras 目录下的模型文件，返回相对路径。

    路径用 `/` 分隔（不用 os.sep），因为要和 ComfyUI 的 LoraLoader.lora_name 逐字对齐 ——
    Windows 上 ComfyUI 也是给出 `子目录/xxx.safetensors` 这种正斜杠写法。
    目录不存在时返回空列表而非抛错：用户可能还没设好路径，同步要能给出空结果而非 500。
    """
    root = Path(loras_dir)
    if not root.is_dir():
        return []
    names: list[str] = []
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in LORA_SUFFIXES:
            names.append(path.relative_to(root).as_posix())
    return sorted(names)


def read_safetensors_meta(path: str | Path) -> dict[str, str]:
    """读 safetensors 文件头里的 `__metadata__`，失败一律返回 {}。

    只读头部：先取 8 字节小端 uint64 得 JSON 长度，再读该长度的 JSON。张量本体不碰，
    所以几 GB 的文件也是毫秒级、且不吃内存。
    非 safetensors（.pt/.ckpt 是 pickle）直接返回 {} —— 不解 pickle，避免任意代码执行风险。
    """
    p = Path(path)
    if p.suffix.lower() != ".safetensors":
        return {}
    try:
        with p.open("rb") as fh:
            head = fh.read(8)
            if len(head) < 8:
                return {}
            size = struct.unpack("<Q", head)[0]
            if size <= 0 or size > _MAX_HEADER:
                return {}
            payload = fh.read(size)
        obj = json.loads(payload)
    except (OSError, struct.error, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    if not isinstance(obj, dict):
        return {}
    meta = obj.get("__metadata__")
    if not isinstance(meta, dict):
        return {}
    # 值可能是 int/嵌套结构，统一转字符串，方便下游一致处理
    return {str(k): v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
            for k, v in meta.items()}


def _tag_counts(meta: dict[str, str]) -> dict[str, int]:
    """把 ss_tag_frequency 拍平成 {tag: 总次数}。

    kohya 存的结构是 {数据集目录名: {tag: 次数}}，多目录训练就有多个键，需合并计数。
    """
    raw = meta.get("ss_tag_frequency", "")
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(obj, dict):
        return {}
    counts: dict[str, int] = {}
    for per_dir in obj.values():
        if not isinstance(per_dir, dict):
            continue
        for tag, n in per_dir.items():
            if isinstance(n, int | float):
                counts[str(tag).strip()] = counts.get(str(tag).strip(), 0) + int(n)
    return {k: v for k, v in counts.items() if k}


# 出现率达最高频这个比例的 tag，才算触发词：真正的触发词几乎每张训练图都打，
# 而画风/服装等普通 tag 只覆盖一部分。取 0.9 是留一点标注遗漏的余量。
_TRIGGER_RATIO = 0.9
# 最多取几个，防止小数据集里一堆 tag 次数并列全被当成触发词
_MAX_TRIGGERS = 4

# 通用 booru 标签停用词。这些在动漫数据集里几乎每张图都打，频率和真触发词并列，
# 光靠 _TRIGGER_RATIO 分不开（实测 iLLC0lorL1nes 里 1girl/solo/c0lorl1nes 都是 45 次）。
# 把它们当触发词注进提示词会强行改画面（比如给风景图塞 1girl）。
# 只收录跨数据集通用的人数/视角/构图/质量词，不收人体部位或服装 —— 那些在特定
# LoRA 里可能真是触发词。
_STOPWORDS = frozenset(
    {
        # 人数
        "1girl", "2girls", "3girls", "multiple girls",
        "1boy", "2boys", "multiple boys",
        "solo", "solo focus",
        # 视角 / 朝向
        "looking at viewer", "looking away", "looking back", "looking to the side",
        "facing viewer", "from side", "from behind", "from above", "from below",
        # 构图
        "simple background", "white background", "transparent background",
        "upper body", "lower body", "full body", "cowboy shot", "portrait", "close-up",
        # 质量词
        "masterpiece", "best quality", "high quality", "highres", "absurdres",
        "very aesthetic", "newest",
    }
)


def _is_stopword(tag: str) -> bool:
    """是否通用标签。booru 标签下划线和空格两种写法混用，统一后再比。"""
    return tag.strip().lower().replace("_", " ") in _STOPWORDS


def extract_triggers(meta: dict[str, str]) -> list[str]:
    """从 safetensors 元数据里猜触发词。猜不到返回 []（交由 sidecar 或用户手填兜底）。

    只用 ss_tag_frequency 的高频 tag。不拿 ss_output_name 当触发词 —— 那只是训练输出
    文件名（常见 `last`、`epoch-000010` 这类），当触发词注进提示词纯属污染。

    先剔停用词再算最高频：否则通用标签占了 top，会把阈值抬到真触发词之上。
    """
    counts = {t: n for t, n in _tag_counts(meta).items() if not _is_stopword(t)}
    if not counts:
        return []
    top = max(counts.values())
    if top <= 0:
        return []
    hits = [t for t, n in counts.items() if n >= top * _TRIGGER_RATIO]
    # 按次数降序，同次数按字母序，保证同一文件每次同步结果稳定
    hits.sort(key=lambda t: (-counts[t], t))
    return hits[:_MAX_TRIGGERS]


# sidecar 候选后缀，按优先级：civitai 专用信息文件在前，
# 再是 ComfyUI-Lora-Manager 的 .metadata.json，最后通用 .json
_SIDECAR_SUFFIXES = (".civitai.info", ".metadata.json", ".json")


def _sidecar_words(obj: dict) -> list[str]:
    """从 sidecar 结构里取 trainedWords。

    两种布局：civitai.info 直接顶层放 trainedWords；ComfyUI-Lora-Manager 的
    .metadata.json 把整个 civitai 响应嵌在 "civitai" 键下。
    """
    for holder in (obj, obj.get("civitai")):
        if not isinstance(holder, dict):
            continue
        words = holder.get("trainedWords")
        if isinstance(words, list):
            out = [str(w).strip() for w in words if str(w).strip()]
            if out:
                return out
    return []


def read_sidecar(path: str | Path) -> list[str]:
    """读同名 sidecar 里的 trainedWords（civitai 系下载器 / Lora-Manager 常留）。没有则 []。

    形如 `xxx.safetensors` → 找 `xxx.civitai.info` / `xxx.metadata.json` / `xxx.json`。
    """
    p = Path(path)
    for suffix in _SIDECAR_SUFFIXES:
        side = p.with_suffix("").with_name(p.stem + suffix)
        if not side.is_file():
            continue
        try:
            obj = json.loads(side.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(obj, dict):
            continue
        words = _sidecar_words(obj)
        if words:
            return words
    return []


def detect_triggers(path: str | Path) -> tuple[list[str], str]:
    """按来源优先级取触发词，返回 (触发词, 来源)。来源为 metadata / sidecar / ""。

    同步时用这个入口；用户手填的条目由 lora_index 保护、不会走到这里。
    """
    meta = read_safetensors_meta(path)
    triggers = extract_triggers(meta)
    if triggers:
        return triggers, "metadata"
    triggers = read_sidecar(path)
    if triggers:
        return triggers, "sidecar"
    return [], ""