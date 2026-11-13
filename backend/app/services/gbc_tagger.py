"""GBC 场景图打标（外部引擎，2026-11-06）。

分工（用户定案）：**WD14 出标签，GBC 按标签 + 英文场景描述分析实体/关系图**。
GBC 不进安装包——它在自己的 venv 里跑 torch + SAM-3 + EmbeddingGemma，torch/SAM-3
没法像 ONNX 那样「丢一个模型目录」就完事；设置里只登记 `gbc.exe` 与两份权重目录，
VLM 复用「视觉大模型」（本地 Ollama / OpenAI 兼容服务）。缺件抛 GbcUnavailable。

一次打标 = 一次 `gbc run`：临时目录里生成 config.py + job.jsonl，读回 sink 的
graphs.jsonl。临时目录落在 `DATA_DIR/tmp/gbc/`（卸载档位里的 cache），只保留最近若干轮。
"""
from __future__ import annotations

import json
import os
import pprint
import re
import shutil
import subprocess
import time
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from app.config import DATA_DIR

_WORK_ROOT = DATA_DIR / "tmp" / "gbc"
#: 密钥走子进程环境变量，不落 config.py（GBC 的 api 端点只认 `api_key_env`）
API_KEY_ENV = "DEMIURGE_GBC_VLM_KEY"
_KEEP_WORK_DIRS = 20
MAX_TAGS = 32
#: WD14 置信度门槛。GBC 只把过线的标签落成实体，种子描述也必须按同一门槛生成，
#: 否则描述会点名一个 GBC 根本不会产出的物体。build_config 与本文件的描述生成共用。
TAG_THRESHOLD = 0.35

#: 姿态/朝向白名单（2026-11-07）：GBC 摘要由视觉模型写，姿势词会错（俯卧被写成
#: sitting）；WD14 的 on_stomach/lying 是独立分类器结果，只把这组标签喂给布局模型。
#: 词表属主在标签模块（漏进种子描述会变成实体），moss_layout 复用同一份。
POSE_TAGS = frozenset({
    "standing", "sitting", "seiza", "wariza", "indian_style", "straddling",
    "squatting", "crouching", "kneeling", "all_fours", "on_all_fours",
    "lying", "on_back", "on_stomach", "on_side", "prone", "supine",
    "spread_legs", "crossed_legs", "legs_up", "knees_up", "knees_to_chest",
    "fetal_position", "leaning_forward", "leaning_back", "arched_back",
    "looking_back", "from_behind", "from_side", "from_above", "from_below",
})

# ─────────────────── WD14 标签 → GBC 种子描述（2026-11-07） ───────────────────
# GBC 的制导方式是「标签里的名词在 captions.detail 里逐字出现 ⇒ 落成实体」。
# 让 VLM 自由写整图描述会把一体式泳衣写成比基尼、凭空添出剑/手套/标题文字
# （2026-11-07 实锤：生成图照画了原图没有的剑与 "Sword of the Sea" 标题）。
# 所以种子描述只由标签拼成：标签即物体清单，描述即清单。
#
# 下面的过滤与 GBC `nodes/root.py::tag_element` 同口径（外部库不能 import）：
# 非物体标签、部位词、-ing 动作词都不会被它落成实体；这里一并挡在描述之外。

_COUNT_TAG = re.compile(r"^(\d+)\+?(girl|boy|other)s?$")
#: GBC 自己的非物体清单（场景/构图/元信息）
_NON_OBJECT_TAGS = frozenset({
    "solo", "night", "day", "outdoors", "indoors", "scenery", "realistic",
    "comic", "monochrome", "greyscale", "border", "cover", "text",
    "english text", "watermark", "signature", "artist name", "character name",
    "multiple views", "close-up", "full body", "upper body", "cowboy shot",
    "portrait", "no humans", "animal focus", "male focus", "female focus",
    "sky", "cloud", "blurry", "depth of field", "pun",
})
#: GBC 的部位词头（复数由 _part_head 处理）
_PART_HEADS = frozenset({
    "hair", "eyes", "ears", "bangs", "mouth", "skin", "ponytail", "twintails",
    "braid", "sidelocks", "ahoge", "background", "lips", "teeth", "fangs",
    "eyebrows", "eyelashes", "pupils", "nails", "hairband", "shadows", "shadow",
})
#: 头部是这些人称词 = 人物属性，不是第二个人（dark-skinned female 会变成两个 girl）
_PERSON_HEADS = frozenset({
    "girl", "girls", "boy", "boys", "woman", "women", "man", "men", "female",
    "male", "person", "people", "child", "children", "character", "characters",
    "adult", "teen", "teenager", "lady", "guy", "human", "humans",
})
#: 身体部位（WD14 高频；GBC 的 PART_HEADS 只覆盖头部与发饰）
_BODY_HEADS = frozenset({
    "breasts", "chest", "cleavage", "nipples", "navel", "thigh", "thighs",
    "ass", "hips", "leg", "legs", "feet", "foot", "hand", "hands", "finger",
    "fingers", "arm", "arms", "shoulder", "shoulders", "neck", "stomach",
    "belly", "waist", "collarbone", "armpit", "armpits", "skin", "mole",
    "freckles", "scar", "scars", "tattoo", "veins", "tail", "wings",
})
#: 表情/状态短语（头词命中的常见款另由 _BODY_HEADS/_PART_HEADS 兜住）
_EXPRESSION_TAGS = frozenset({
    "smile", "grin", "blush", "expressionless", "angry", "sad", "happy",
    "surprised", "tears", "sweatdrop", "sweat", "frown", "smug", "smirk",
    "serious", "pout", "tongue out", "wink", "one eye closed", "eye contact",
})
#: 元信息/质量/评级词（WD14 会混进标签列表）
_META_TAGS = frozenset({
    "official art", "official alternate costume", "official costume",
    "alternate costume", "cosplay", "highres", "absurdres", "lowres",
    "masterpiece", "best quality", "high quality", "commentary",
    "english commentary", "translated", "bad id", "bad hands", "bad anatomy",
    "watermark", "signature", "safe", "nsfw", "sensitive", "questionable",
    "explicit",
})
_META_PREFIXES = ("year ", "score ", "bad ", "worst ")
#: 视线类（不是物体，头词也不以 -ing 结尾）
_GAZE_TAGS = frozenset({
    "looking at viewer", "looking ahead", "looking to the side", "looking down",
    "looking up", "facing viewer",
})

#: 云端 VLM 的输出预算。GBC 默认实体请求只给 512 tokens，DeepSeek 这类推理模型
#: 一次就烧掉 457 个思考 tokens，正文 JSON 被截断后整个实体被静默丢弃（2026-11-07 实测）。
_CLOUD_MAX_TOKENS = 4096
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}

#: MOSS-Image layout 的 label 只有三档；人物类名字归 character，其余归 object。
_PERSON_NAMES = {
    "girl", "boy", "woman", "man", "person", "people", "child", "character",
    "girls", "boys", "women", "men", "persons", "children", "characters",
}


class GbcUnavailable(RuntimeError):
    """GBC 运行环境或权重不全（路由 503 并附装配指引）。"""


class GbcFailed(RuntimeError):
    """GBC 跑不起来 / 超时 / 没有产出可用图。"""


@dataclass(frozen=True)
class GbcRuntime:
    exe: str
    sam3_dir: str
    embed_dir: str
    device: str = "cuda"
    max_top_level: int = 10
    timeout_s: int = 1800


def _weights_ready(folder: str) -> bool:
    text = (folder or "").strip()
    if not text:
        return False
    path = Path(text).expanduser()
    return path.is_dir() and any(path.glob("*.safetensors"))


def missing_parts(rt: GbcRuntime) -> list[str]:
    """缺什么就点名什么（503 的提示直接照这个拼，不猜）。"""
    missing: list[str] = []
    exe = (rt.exe or "").strip()
    if not exe or not Path(exe).expanduser().is_file():
        missing.append("gbc.exe")
    if not _weights_ready(rt.sam3_dir):
        missing.append("SAM-3 权重目录")
    if not _weights_ready(rt.embed_dir):
        missing.append("EmbeddingGemma 权重目录")
    return missing


def _device(rt: GbcRuntime) -> str:
    """GBC 的 torch 运行时把设备名原样交给 `torch.cuda.set_device()`，而它只收带序号的
    `cuda:N`：裸 `cuda` 会让 `torch-cuda-all` 工作线程启动即抛 ValueError 死掉，
    任务留在 ready 队列里没人消费 ⇒ 进程挂到超时、GPU 占用 0 %。设置里只有 cuda/cpu 两档，
    这里补默认序号。"""
    raw = (rt.device or "").strip().lower()
    if not raw.startswith("cuda"):
        return "cpu"
    return raw if ":" in raw else "cuda:0"


def _is_cloud_endpoint(base_url: str) -> bool:
    host = (urlsplit(base_url or "").hostname or "").strip().lower()
    return bool(host) and host not in _LOOPBACK_HOSTS


def build_config(*, work_dir: Path, job_path: Path, sink_path: Path, rt: GbcRuntime,
                 vlm_base_url: str, vlm_model: str, has_api_key: bool) -> str:
    """生成 GBC 的 Python 配置文本（pprint 输出即合法 Python 字面量）。"""
    endpoint: dict[str, object] = {
        "base_url": (vlm_base_url or "").strip().rstrip("/"),
        "max_inflight": 1,
    }
    if has_api_key:
        endpoint["api_key_env"] = API_KEY_ENV
    vlm: dict[str, object] = {
        "entry": "api", "protocol": "openai", "flavour": "openai",
        "model": (vlm_model or "").strip(), "endpoints": [endpoint],
        "timeout_s": 600,
    }
    if _is_cloud_endpoint(vlm_base_url):
        # 云端不保证支持 response_format json_schema strict（DeepSeek 直接 400），
        # 改由提示词索要 JSON；同时抬高输出上限，避免思考 tokens 吃掉正文。
        vlm["structured"] = "prompt"
        vlm["sampling"] = {"max_tokens": _CLOUD_MAX_TOKENS}
    values: dict[str, object] = {
        "engines": {
            "vlm": vlm,
            "detect": {
                "entry": "torch", "model": str(Path(rt.sam3_dir).expanduser()),
                "dtype": "bfloat16", "devices": [_device(rt)], "max_batch": 2,
            },
            "embed": {
                "entry": "torch", "model": str(Path(rt.embed_dir).expanduser()),
                "devices": ["cpu"], "max_batch": 16,
            },
            "cpu": {"entry": "python", "workers": 8},
        },
        "machine": {"report": str(work_dir / "report")},
        "source": {"kind": "jsonl", "path": str(job_path)},
        "sink": {"kind": "graphs", "path": str(sink_path)},
        "controls": {
            "root": "tags",
            "tag_threshold": TAG_THRESHOLD,
            "max_top_level": max(1, int(rt.max_top_level)),
            "prompts": "json",
            # 单轮就够覆盖顶层物体；放宽轮次在 CPU 上会跑十几分钟（2026-11-05 实测）。
            "max_rounds": 1,
            "max_breadth_rounds": 0,
            "box_score": 0.3,
        },
    }
    return "\n".join(
        f"{key} = {pprint.pformat(value, width=110, sort_dicts=False)}"
        for key, value in values.items()
    ) + "\n"


def build_job(*, key: str, image_path: Path, caption: str,
              tags: dict[str, float]) -> dict[str, object]:
    return {
        "key": key,
        "image": str(image_path),
        "captions": {"detail": caption.strip()},
        "tags": {"general": {str(k): round(float(v), 4) for k, v in tags.items()}},
    }


def _tag_text(tag: object) -> str:
    """标签 → 自然词形（`white_one-piece_swimsuit` → `white one-piece swimsuit`）。"""
    text = re.sub(r"\s*\(.*?\)", "", str(tag).replace("_", " ")).strip().lower()
    return re.sub(r"\s+", " ", text)


def _part_head(head: str) -> bool:
    forms = {head, head + "s"}
    if head.endswith("s"):
        forms.add(head[:-1])
    return bool(forms & _PART_HEADS)


def _is_object_phrase(text: str) -> bool:
    """这句话代表「一个能被 GBC 落成实体的物体」吗（与 tag_element 同口径）。"""
    if not text or text in _NON_OBJECT_TAGS or text in _META_TAGS:
        return False
    if text.startswith(_META_PREFIXES):
        return False
    if text in _GAZE_TAGS or text in _EXPRESSION_TAGS:
        return False
    if re.sub(r"\s+", "_", text) in POSE_TAGS:
        return False
    head = text.split()[-1]
    if head.endswith("ing"):
        return False
    return head not in _PERSON_HEADS and not _part_head(head) and head not in _BODY_HEADS


def caption_from_tags(tags: object) -> str:
    """WD14 标签（按置信度排序）→ GBC 种子描述：只点名标签里真有的物体。

    GBC 按「名词在描述里逐字出现」把标签落成实体，所以描述必须是标签的名词清单，
    而不是另一轮视觉模型的自由发挥。返回空串 = 没有可用的物体标签。
    """
    phrases: list[str] = []
    for tag in tags if isinstance(tags, (list, tuple)) else []:
        raw = str(tag).strip()
        if not raw:
            continue
        count = _COUNT_TAG.match(raw.lower())
        if count:
            noun = "person" if count.group(2) == "other" else count.group(2)
            phrase = noun + "s" if int(count.group(1)) > 1 else noun
        else:
            text = _tag_text(raw)
            if not _is_object_phrase(text):
                continue
            phrase = text
        if phrase not in phrases:
            phrases.append(phrase)
    # 英文修饰语在前 ⇒ 短词是长词的后缀短语时只留最具体的那个
    # （swimsuit / one-piece swimsuit ⊂ white one-piece swimsuit）。
    kept = [text for text in phrases
            if not any(other != text and other.endswith(" " + text) for other in phrases)]
    if not kept:
        return ""
    return "Objects in the image: " + ", ".join(kept) + "."


def _prune_work_dirs() -> None:
    if not _WORK_ROOT.is_dir():
        return
    dirs = sorted((p for p in _WORK_ROOT.iterdir() if p.is_dir()),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    for stale in dirs[_KEEP_WORK_DIRS:]:
        shutil.rmtree(stale, ignore_errors=True)


def _kill_tree(pid: int) -> None:
    """taskkill /T 杀整棵进程树（gbc.exe 会派生 torch worker；项目只在 Windows 发行）。"""
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                   capture_output=True, check=False)


def _sanitized_no_proxy(raw: str) -> str:
    """清洗 NO_PROXY 再交给 GBC 子进程。

    httpx 的 `get_environment_proxies` 只把**裸** IPv6 当地址：`[::1]` 会退化成
    `all://*[::1]` 这种非法图案，任何 `httpx.Client(trust_env=True)` 直接抛
    `InvalidURL: Invalid port: ':1]'`（GBC 的 api 引擎在建客户端时就死在这）。
    这里把 `[...]` 解包成裸地址，畸形项丢弃，并保证本机直连。
    """
    out: list[str] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        if token.startswith("[") and token.endswith("]"):
            token = token[1:-1].strip()
            if not token:
                continue
        if "[" in token or "]" in token:
            continue
        if token not in out:
            out.append(token)
    for must in ("127.0.0.1", "localhost"):
        if must not in out:
            out.append(must)
    return ",".join(out)


def _subprocess_env(api_key: str, proxy: str = "") -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    # 本机 Ollama 必须直连：父进程若带系统代理，httpx 会把 127.0.0.1 也送进代理。
    merged = _sanitized_no_proxy(env.get("NO_PROXY") or env.get("no_proxy") or "")
    env["NO_PROXY"] = merged
    env["no_proxy"] = merged
    # 云端视觉模型必须与产品同源：地址由前端按模型级 proxyMode 解析好后透传。
    # 空串＝直连，要显式清掉继承来的系统代理——否则「直连」会被父进程环境静默推翻。
    target = (proxy or "").strip()
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        if target:
            env[name] = target
        else:
            env.pop(name, None)
    if api_key:
        env[API_KEY_ENV] = api_key
    else:
        env.pop(API_KEY_ENV, None)
    return env


def _vlm_faults(work_dir: Path) -> list[str]:
    """读本轮 GBC report，收视觉模型调用失败的错误串（提取不到时用车道计数兜底）。"""
    report = work_dir / "report"
    messages: list[str] = []
    events = report / "events.jsonl"
    if events.is_file():
        for line in events.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict) or item.get("kind") != "fault.taken":
                continue
            message = str(item.get("error_message") or item.get("error_kind") or "").strip()
            # 只认 chat 节点/HTTP 失败：detect/cpu 车道的故障不背这个锅。
            if (item.get("error_node") == "chat" or "from POST" in message) \
                    and message and message not in messages:
                messages.append(message)
    if messages:
        return messages[:3]
    summary = report / "summary.json"
    if not summary.is_file():
        return []
    try:
        data = json.loads(summary.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    lanes = data.get("lanes") or {}
    vlm = lanes.get("vlm") or {}
    submitted = int(vlm.get("submitted") or 0)
    batches = int(vlm.get("batches") or 0)
    taken = int((data.get("events") or {}).get("fault.taken") or 0)
    if submitted and not batches and taken:
        return [f"视觉模型调用未产出结果（提交 {submitted} 次、失败 {taken} 次）"]
    return []


def _empty_graph_error(graph: dict, work_dir: Path, *, cloud: bool) -> str | None:
    """0 个 entity 且视觉模型有失败 ⇒ 返回给用户的错误；否则 None（保持原行为）。

    GBC 会把 VLM 失败当「没有描述」吞掉，最后仍写出一张只有 image 根顶点的图并
    报成功——用户花了云端调用的钱却拿不到任何实体。这种情况必须当面报错、不存空图。
    """
    vertices = [v for v in (graph.get("vertices") or []) if isinstance(v, dict)]
    if any(v.get("label") == "entity" for v in vertices):
        return None
    faults = _vlm_faults(work_dir)
    if not faults:
        return None
    advice = (
        "这张图可能被云端模型拒绝（NSFW/敏感内容常见），请到「设置 → 模型 → 视觉大模型」"
        "切到本地视觉模型（如 qwen3vl:8b）后重跑；本次未保存空图。"
        if cloud else
        "请检查视觉模型是否可用后重跑；本次未保存空图。"
    )
    return f"GBC 没有解析出任何实体：视觉模型调用失败（{'；'.join(faults)}）。{advice}"


def _ollama_root(base_url: str) -> str:
    """OpenAI 兼容 base（`…/v1`）→ Ollama 原生 API 根。"""
    root = (base_url or "").strip().rstrip("/")
    for suffix in ("/v1", "/api"):
        if root.endswith(suffix):
            return root[: -len(suffix)].rstrip("/")
    return root


def _unload_local_vlm(base_url: str, model: str, *, opener=None) -> bool:
    """GBC 启动前把本机 Ollama 里驻留在显存的 VLM 卸下（失败一律放过）。

    GBC 的 placement 在**启动时**按当前空闲显存一次性校验，检测器固定要 5 GiB；
    而本机 Ollama 一次布局/描述请求就会把 VLM 钉住约 5 GiB，叠加后必然
    `placement does not fit`。Ollama 会在 GBC 真正调 VLM 时按需重载（显存不够
    就部分落 CPU），所以先卸下是回到「冷启动」这一可跑形态。只处理回环地址上
    认得出的 Ollama：卸载不是 GBC 的前置条件，不许因它挡住运行。
    """
    if not (model or "").strip():
        return False
    if (urlsplit(base_url or "").hostname or "").lower() not in _LOOPBACK_HOSTS:
        return False
    root = _ollama_root(base_url)
    # 本机请求必须绕开系统代理，否则会被父进程的 http_proxy 截走。
    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(root + "/api/ps", timeout=3) as resp:
            running = json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - 不是 Ollama / 没在跑：跳过
        return False
    models = running.get("models") if isinstance(running, dict) else None
    resident = False
    for entry in models if isinstance(models, list) else []:
        if not isinstance(entry, dict) or model not in (entry.get("name"), entry.get("model")):
            continue
        try:
            size_vram = int(entry.get("size_vram") or 0)
        except (TypeError, ValueError):
            size_vram = 0
        if size_vram > 0:
            resident = True
            break
    if not resident:
        return False
    request = urllib.request.Request(
        root + "/api/generate",
        data=json.dumps({"model": model, "keep_alive": 0}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with opener.open(request, timeout=10):
            return True
    except Exception:  # noqa: BLE001
        return False


#: 失败根因常常不在输出的最后几行：GBC 的 spawn 用 traceback 收尾，显存/权重这类
#: 真原因在更早的 stderr 里。按特征行挑出来，UI 才能给出能动手的那一句。
_FAILURE_HINTS = (
    "placement does not fit", "GiB free", "CUDA out of memory", "OutOfMemoryError",
    "engine host", "No such file", "not found",
)
_VRAM_HINTS = ("placement does not fit", "GiB free", "CUDA out of memory", "OutOfMemoryError")


def _failure_summary(output: str, returncode: int) -> str:
    lines = [line.strip() for line in (output or "").splitlines() if line.strip()]
    lines = [line for line in lines if not line.startswith(("raise ", "File \""))]
    picked: list[str] = []
    for line in lines:
        if any(hint in line for hint in _FAILURE_HINTS) and line not in picked:
            picked.append(line)
    summary = " | ".join(picked[:4]) or " | ".join(lines[-6:]) or f"exit={returncode}"
    if any(hint in summary for hint in _VRAM_HINTS):
        summary += ("——显存不足：先关掉占显存的程序（ComfyUI 里加载的模型、本地大模型服务）"
                    "后重试，或把检测设备改成 cpu")
    return summary


def _read_graph(sink_path: Path) -> dict:
    if not sink_path.is_file():
        raise GbcFailed("GBC 没有产出场景图（sink 为空）——请查看 data/tmp/gbc/ 下本轮日志")
    records: list[dict] = []
    for line in sink_path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            item = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            records.append(item)
    graphs = [r for r in records if isinstance(r.get("vertices"), list)]
    if not graphs:
        raise GbcFailed("GBC 产出里没有图（任务可能失败，见 data/tmp/gbc/ 下本轮日志）")
    return graphs[-1]


def run_one(*, image_path: Path, caption: str, tags: dict[str, float], rt: GbcRuntime,
            vlm_base_url: str, vlm_model: str, vlm_api_key: str = "",
            vlm_proxy: str = "", key: str = "") -> dict:
    """跑一次 GBC，返回 graph dict（`vertices/img_size/detail_caption`）。"""
    missing = missing_parts(rt)
    if missing:
        raise GbcUnavailable("GBC 运行环境不全：缺少 " + "、".join(missing))
    if not caption.strip():
        raise GbcFailed("缺少英文场景描述（GBC 需要它把标签落成物体）")
    if not (vlm_base_url or "").strip() or not (vlm_model or "").strip():
        raise GbcUnavailable("GBC 需要一个视觉大模型来读实体描述：请先在「设置 → 模型」配置 VLM")

    _unload_local_vlm(vlm_base_url, vlm_model)
    _prune_work_dirs()
    work_dir = _WORK_ROOT / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    work_dir.mkdir(parents=True, exist_ok=False)
    job_path = work_dir / "job.jsonl"
    sink_path = work_dir / "graphs.jsonl"
    config_path = work_dir / "config.py"
    job_path.write_text(
        json.dumps(build_job(key=key, image_path=image_path, caption=caption, tags=tags),
                   ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    config_path.write_text(
        build_config(work_dir=work_dir, job_path=job_path, sink_path=sink_path, rt=rt,
                     vlm_base_url=vlm_base_url, vlm_model=vlm_model,
                     has_api_key=bool(vlm_api_key)),
        encoding="utf-8",
    )

    started = time.monotonic()
    proc = subprocess.Popen(
        [str(Path(rt.exe).expanduser()), "run", str(config_path)],
        cwd=str(work_dir), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        env=_subprocess_env(vlm_api_key, vlm_proxy),
    )
    try:
        output, _ = proc.communicate(timeout=max(60, int(rt.timeout_s)))
    except subprocess.TimeoutExpired:
        _kill_tree(proc.pid)
        try:
            output, _ = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            output = ""
        # 超时最需要现场：把子进程日志尾巴带出来（挂起时唯一线索就在这里）
        tail = " | ".join((output or "").strip().splitlines()[-4:])
        raise GbcFailed(
            f"GBC 超时（{int(rt.timeout_s)}s 未完成）"
            + (f"：{tail}" if tail else "")
            + "——可把检测设备改成 cpu 重试，或减少「最多物体数」") from None
    if proc.returncode != 0:
        raise GbcFailed(f"GBC 运行失败：{_failure_summary(output or '', proc.returncode)}")
    graph = _read_graph(sink_path)
    error = _empty_graph_error(graph, work_dir, cloud=_is_cloud_endpoint(vlm_base_url))
    if error:
        raise GbcFailed(error)
    graph["_elapsed_s"] = round(time.monotonic() - started, 1)
    return graph


# ─────────────────────────── 场景图 → MOSS-Image layout JSON ───────────────────────────

def _desc(vertex: dict) -> str:
    descs = vertex.get("descs") or []
    if descs and isinstance(descs[0], dict):
        return str(descs[0].get("text") or "").strip()
    return ""


def _moss_label(name: str) -> str:
    return "character" if (name or "").strip().lower() in _PERSON_NAMES else "object"


def to_moss(graph: dict, *, draft: bool = False, include_name: bool = False) -> dict:
    """GBC graph → MOSS-Image 布局 JSON（与 Penna `mossLayoutJson` 同形）。

    交付形：bbox=[top-h/2, left-w/2, bottom-h/2, right-w/2]（相对原图中心，y 在前）；
    草稿形：bbox=[left, top, right, bottom]（左上原点），带 width/height 与每块 name。
    ⚠ 交付形没有画布尺寸 ⇒ MOSS 侧必须按原图 img_size 出图，否则 bbox 整体偏移。
    `include_name` 只为内部按实体名拼文字（moss_layout）服务，交付 JSON 不带 name。
    """
    size = graph.get("img_size") or [0, 0]
    width, height = int(size[0] or 0), int(size[1] or 0)
    half_w, half_h = width // 2, height // 2
    items: list[dict] = []
    for vertex in graph.get("vertices") or []:
        if not isinstance(vertex, dict) or vertex.get("label") != "entity":
            continue
        box = vertex.get("bbox") or {}
        if box.get("right") is None or box.get("bottom") is None:
            continue
        left, top = float(box["left"]) * width, float(box["top"]) * height
        right, bottom = float(box["right"]) * width, float(box["bottom"]) * height
        if right <= left or bottom <= top:
            continue
        item: dict[str, object] = {
            "label": _moss_label(str(vertex.get("vertex_id") or "")),
            "caption": _desc(vertex),
        }
        if draft or include_name:
            item["name"] = vertex.get("vertex_id") or ""
        if draft:
            item["bbox"] = [round(left), round(top), round(right), round(bottom)]
        else:
            item["bbox"] = [round(top - half_h), round(left - half_w),
                            round(bottom - half_h), round(right - half_w)]
        items.append(item)
    out: dict[str, object] = {
        "summary": {"canvas": "single", "caption": graph.get("detail_caption") or ""},
        "items": items,
        "bbox_coord_space": "abs" if draft else "abs_centralized",
        "bbox_format": "xyxy" if draft else "yxyx",
    }
    if draft:
        out = {"width": width, "height": height, **out}
    return out


def outline(graph: dict) -> dict:
    """详情面板用的轻量摘要：物体名 + 描述、关系句、耗时。"""
    entities: list[dict[str, str]] = []
    relations: list[str] = []
    for vertex in graph.get("vertices") or []:
        if not isinstance(vertex, dict):
            continue
        if vertex.get("label") == "entity":
            entities.append({"name": str(vertex.get("vertex_id") or ""),
                             "caption": _desc(vertex)})
        elif vertex.get("label") == "relation":
            text = _desc(vertex)
            if text:
                relations.append(text)
    return {
        "entities": entities,
        "relations": relations,
        "elapsed_s": graph.get("_elapsed_s") or 0,
        "moss_items": len(to_moss(graph)["items"]),
    }
