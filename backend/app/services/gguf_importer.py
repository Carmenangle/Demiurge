"""GGUF 模型导入器：扫描 → 解析元数据 → 硬件适配 → 导入 Ollama → 注册 provider。

设计目标：兼容所有 GGUF 量化模型（LLM / 视觉 VLM / Embedding），帮助低配置用户
在本地 Ollama 上也能跑得动模型。

流程：
1. scan_gguf_dir(dir)    递归扫描任意目录，识别主模型与 mmproj 视觉投影
2. parse_gguf(path)      读 GGUF 头部 KV（架构/参数/量化/视觉能力），纯 Python 无依赖
3. fit_hardware(meta)    用 model_lease.device_probe() 探测显存 → 给降档建议
4. import_to_ollama()   生成 Modelfile（FROM + ADAPTER + TEMPLATE）→ ollama create
5. register_provider()  导入成功后写入 ai_providers（Ollama 本地服务）

安全：只读用户显式指定的目录；导入只写 Ollama 模型库；路径做严格校验。
"""
from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── 数据类 ───────────────────────────────────────────────────────────────────

@dataclass
class GgufMeta:
    """GGUF 文件解析出的元数据。"""
    path: str
    filename: str
    size_bytes: int = 0
    size_label: str = ""
    architecture: str = ""
    kind: str = "model"          # model | mmproj
    parameters_b: float = 0.0    # 参数量（B 单位）
    context_length: int = 0
    embedding_length: int = 0
    file_type: int = 0
    quant: str = ""              # 量化档：Q4_K_M / Q6_K / f16 ...
    is_vision: bool = False      # 是否视觉模型（含 clip 或 mmproj）
    has_vision_encoder: bool = False
    name: str = ""
    is_embedding: bool = False
    notes: list[str] = field(default_factory=list)


@dataclass
class ImportResult:
    ok: bool
    model_name: str = ""
    message: str = ""
    meta: GgufMeta | None = None
    elapsed_sec: float = 0.0


# ── GGUF 头部解析（纯 Python，无 llama.cpp 依赖） ────────────────────────────

_GGUF_MAGIC = b"GGUF"

# GGUF KV value 类型
_GGUF_TYPE = {
    0: "uint8", 1: "int8", 2: "uint16", 3: "int16", 4: "uint32", 5: "int32",
    6: "float32", 7: "bool", 8: "string", 9: "array", 10: "uint64", 11: "int64",
    12: "float64",
}

# 量化等级名称（用于从 file_type 推导人类可读档位）
_FILE_TYPE_QUANTS = {
    0: "f32", 1: "f16", 2: "q4_0", 3: "q4_1", 6: "q5_0", 7: "q5_1",
    8: "q8_0", 9: "q8_1", 10: "q2_k", 11: "q3_k", 12: "q4_k", 13: "q5_k",
    14: "q6_k", 15: "q8_k", 16: "iq2_xxs", 17: "iq2_xs", 18: "iq3_xxs",
    19: "iq1_s", 20: "iq4_nl", 21: "iq3_s", 22: "iq2_s", 23: "iq4_xs",
    24: "i8", 25: "i16", 26: "i32", 27: "i64", 28: "f6", 29: "iq1_m",
    30: "bf16", 32: "f32", 33: "f16", 34: "bf16", 35: "q4_0_4_4",
    36: "q4_0_4_8", 37: "q4_0_8_8", 38: "tq1_0", 39: "tq2_0", 40: "iq4_nl_4_4",
    41: "iq4_nl_4_8", 42: "iq4_nl_8_8", 43: "q4_0_8_4", 44: "q4_0_4_4",
    45: "q4_0_4_8", 46: "q4_0_8_8", 47: "q3_k_xs", 48: "iq1_s_xs",
    49: "iq2_s_xs", 50: "iq2_m", 51: "iq3_s_xs", 52: "q4_k_xs", 53: "q5_k_xs",
    54: "q6_k_xs", 55: "q8_k_xs", 56: "q8_0_8_4", 57: "q8_0_8_8",
    58: "q8_0_4_4", 59: "q8_0_4_8", 60: "q8_0_4_16", 61: "q8_0_4_32",
    62: "q8_0_16_16", 63: "q8_0_32_32", 64: "q5_k_xs", 65: "q5_k_s",
    66: "q4_k_s", 67: "q3_k_s", 68: "q4_0_xs", 69: "q4_1_xs", 70: "q6_k_xs",
    71: "q6_k_s", 72: "q8_0_xs", 73: "q8_0_s", 74: "iq4_xs_xs",
    75: "iq4_xs_s", 76: "iq3_xxs_xs", 77: "iq3_xxs_s", 78: "iq2_xxs_xs",
    79: "iq2_xxs_s", 80: "iq1_s_xs", 81: "iq1_s_s", 82: "iq4_nl_xs",
    83: "iq4_nl_s", 84: "iq4_nl_4_4_xs", 85: "iq4_nl_4_4_s",
}

# 量化档位的近似显存（每 B 参数，MiB）——用于低配置适配估算
_QUANT_MIB_PER_B = {
    "f32": 4.0, "f16": 2.0, "bf16": 2.0, "f6": 1.2,
    "q8_0": 1.1, "q8_k": 1.1, "q6_k": 0.85, "q5_k": 0.75,
    "q5_0": 0.75, "q5_1": 0.8, "q4_k": 0.65, "q4_0": 0.62,
    "q4_1": 0.66, "q3_k": 0.55, "q2_k": 0.45, "iq4_xs": 0.62,
    "iq3_s": 0.52, "iq2_s": 0.42, "iq1_s": 0.35,
}

# 视觉架构关键字
_VISION_ARCHS = {
    "qwen2vl", "qwen3vl", "qwen2.5vl", "llava", "llava16", "llava_next",
    "minicpmv", "minicpm-v", "bunny", "moondream", "clip", "gemma3",
    "gemma4", "smolvlm", "phi3v", "internvl", "internvl2", "internvl3",
    "glm4v", "glm4.1v", "mllama", "llama3.2-vision", "florence2",
    "pali", "paligemma", "qwen2_vl", "qwen3_vl", "deepseek-vl", "deepseekvl2",
    "pixtral", "granite-vision", "vi-llava", "olmovid", "fuyu", "idefics2",
    "idefics3", "chameleon", "moondream2", "moondream3", "vgg", "resnet",
}

# 嵌入模型架构关键字
_EMBED_ARCHS = {
    "bert", "nomic-bert", "gte", "bge", "qwen3-embedding", "qwen2-embedding",
    "mxbai", "snowflake", "llama-embedding", "granite-embedding",
    "minilm", "e5", "gte-qwen2", "jina", "bge-m3", "m3",
}


def _read_str(f) -> str:
    length = struct.unpack("<Q", f.read(8))[0]
    raw = f.read(length)
    return raw.decode("utf-8", errors="replace")


def _read_scalar(f, vtype: int) -> Any:
    """读一个标量 KV 值。返回 Python 值；array 只读前几个元素用于探测。"""
    if vtype == 0:
        return struct.unpack("<B", f.read(1))[0]
    if vtype == 1:
        return struct.unpack("<b", f.read(1))[0]
    if vtype == 2:
        return struct.unpack("<H", f.read(2))[0]
    if vtype == 3:
        return struct.unpack("<h", f.read(2))[0]
    if vtype == 4:
        return struct.unpack("<I", f.read(4))[0]
    if vtype == 5:
        return struct.unpack("<i", f.read(4))[0]
    if vtype == 6:
        return struct.unpack("<f", f.read(4))[0]
    if vtype == 10:
        return struct.unpack("<Q", f.read(8))[0]
    if vtype == 11:
        return struct.unpack("<q", f.read(8))[0]
    if vtype == 12:
        return struct.unpack("<d", f.read(8))[0]
    if vtype == 7:
        return bool(struct.unpack("<B", f.read(1))[0])
    if vtype == 8:
        return _read_str(f)
    if vtype == 9:
        atype = struct.unpack("<I", f.read(4))[0]
        alen = struct.unpack("<Q", f.read(8))[0]
        # array：只读前 3 个元素判断结构（避免大数组拖慢）
        vals = []
        for _ in range(min(alen, 3)):
            vals.append(_read_scalar(f, atype))
        if alen > 3:
            # 跳过剩余元素
            _skip_array(f, atype, alen - 3)
        return vals
    return None


def _skip_array(f, atype: int, count: int) -> None:
    """跳过 array 剩余元素（只读标量类型）。"""
    sizes = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}
    if atype == 8:
        for _ in range(count):
            length = struct.unpack("<Q", f.read(8))[0]
            f.seek(length, os.SEEK_CUR)
    elif atype in sizes:
        f.seek(sizes[atype] * count, os.SEEK_CUR)


def parse_gguf(path: str | Path) -> GgufMeta | None:
    """解析 GGUF 文件头部 KV，返回元数据；非 GGUF 或损坏返回 None。"""
    p = Path(path)
    if not p.is_file():
        return None
    meta = GgufMeta(
        path=str(p),
        filename=p.name,
        size_bytes=p.stat().st_size,
    )
    try:
        with open(p, "rb") as f:
            magic = f.read(4)
            if magic != _GGUF_MAGIC:
                return None
            ver, n_tensors, n_kv = struct.unpack("<IQQ", f.read(20))
            meta.notes.append(f"GGUF v{ver}, {n_tensors} tensors, {n_kv} KV")
            kv: dict[str, Any] = {}
            for _ in range(n_kv):
                klen = struct.unpack("<Q", f.read(8))[0]
                key = f.read(klen).decode("utf-8", errors="replace")
                vtype = struct.unpack("<I", f.read(4))[0]
                kv[key] = _read_scalar(f, vtype)
    except (struct.error, OSError, UnicodeDecodeError):
        return None

    meta.architecture = str(kv.get("general.architecture", ""))
    meta.kind = str(kv.get("general.type", "model"))
    meta.size_label = str(kv.get("general.size_label", ""))
    meta.name = str(kv.get("general.name", ""))
    meta.file_type = int(kv.get("general.file_type", 0) or 0)
    meta.quant = _FILE_TYPE_QUANTS.get(meta.file_type, f"type{meta.file_type}")

    # 从文件名提取量化（更可靠，文件名常带 Q4_K_M 等）
    fn_quant = _quant_from_filename(p.name)
    if fn_quant:
        meta.quant = fn_quant

    # 参数量：从 size_label（"8.2B"）或架构 block_count × embedding 估算
    mb = re.search(r"([\d.]+)\s*[Bb]", meta.size_label)
    if mb:
        meta.parameters_b = float(mb.group(1))
    elif meta.architecture:
        arch = meta.architecture
        block_count = int(kv.get(f"{arch}.block_count", 0) or 0)
        emb = int(kv.get(f"{arch}.embedding_length", 0) or 0)
        if block_count and emb:
            # 粗略：~12 * block * emb^2 / 8B 参数（llama 系约 12 系数）
            meta.parameters_b = round(12 * block_count * emb * emb / 1e9, 2)

    meta.context_length = int(kv.get(
        f"{meta.architecture}.context_length", 0
    ) or kv.get("llama.context_length", 0) or 0)
    meta.embedding_length = int(kv.get(
        f"{meta.architecture}.embedding_length", 0
    ) or 0)

    # 视觉能力判断
    arch_l = meta.architecture.lower()
    meta.has_vision_encoder = bool(kv.get("clip.has_vision_encoder", False))
    meta.is_vision = (
        meta.kind == "mmproj"
        or meta.has_vision_encoder
        or arch_l in _VISION_ARCHS
        or any(k.startswith("clip.vision.") for k in kv)
    )
    meta.is_embedding = (
        arch_l in _EMBED_ARCHS
        or "embedding" in arch_l
        or bool(kv.get("tokenizer.ggml.model", "") == "bert")
        or "pooling" in str(kv.get(f"{meta.architecture}.pooling_type", ""))
    )
    return meta


def _quant_from_filename(name: str) -> str:
    """从文件名提取量化档（如 Q4_K_M / Q6_K / f16）。"""
    m = re.search(r"[._-]([Qq][0-9]_[A-Za-z0-9_]+|F16|F32|BF16|IQ[0-9]_[A-Za-z0-9_]+|Q8_0)", name, re.IGNORECASE)
    if m:
        return m.group(1).lower()
    return ""


# ── 目录扫描 ─────────────────────────────────────────────────────────────────

def scan_gguf_dir(directory: str | Path) -> dict[str, Any]:
    """递归扫描目录找 GGUF 文件。返回 {files:[meta], mmproj:[meta], models:[meta]}。

    规则：
    - 任何 .gguf 文件都解析
    - general.type == mmproj（或文件名含 mmproj）→ 视觉投影
    - 其余 → 主模型
    """
    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        return {"error": f"目录不存在：{directory}", "files": [], "models": [], "mmproj": []}

    files: list[GgufMeta] = []
    mmproj: list[GgufMeta] = []
    models: list[GgufMeta] = []

    for p in sorted(root.rglob("*.gguf")):
        if p.stat().st_size < 64:  # 跳过 0 字节占位文件
            continue
        meta = parse_gguf(p)
        if meta is None:
            continue
        files.append(meta)
        if meta.kind == "mmproj" or "mmproj" in p.name.lower():
            mmproj.append(meta)
        else:
            models.append(meta)

    return {
        "directory": str(root),
        "files": [_meta_dict(m) for m in files],
        "models": [_meta_dict(m) for m in models],
        "mmproj": [_meta_dict(m) for m in mmproj],
        "count": len(files),
    }


def _meta_dict(m: GgufMeta) -> dict[str, Any]:
    return {
        "path": m.path,
        "filename": m.filename,
        "size_bytes": m.size_bytes,
        "size_gb": round(m.size_bytes / (1024 ** 3), 2),
        "size_label": m.size_label,
        "architecture": m.architecture,
        "kind": m.kind,
        "parameters_b": m.parameters_b,
        "context_length": m.context_length,
        "quant": m.quant,
        "is_vision": m.is_vision,
        "has_vision_encoder": m.has_vision_encoder,
        "is_embedding": m.is_embedding,
        "name": m.name,
        "notes": m.notes,
    }


def find_mmproj_for(gguf_dir: str | Path, model_path: str) -> str:
    """为指定主模型找同目录下匹配的 mmproj。优先文件名架构匹配。"""
    root = Path(gguf_dir).expanduser().resolve()
    model_path = Path(model_path)
    candidates = [p for p in root.rglob("*.gguf") if "mmproj" in p.name.lower()]
    if not candidates:
        return ""
    # 优先同目录
    same_dir = [p for p in candidates if p.parent == model_path.parent]
    pool = same_dir or candidates
    return str(pool[0])


# ── 硬件适配（低配置降档建议） ───────────────────────────────────────────────

def _probe_device() -> dict[str, Any]:
    """探测 GPU/内存。model_lease 优先，失败降级为纯估算。"""
    try:
        from app.services import model_lease
        dev = model_lease.device_probe()
        return {
            "available_mib": dev.available_mib,
            "total_mib": dev.total_mib,
            "name": dev.name,
            "probe_source": dev.probe_source,
            "probe_error": dev.probe_error,
        }
    except Exception as exc:  # noqa: BLE001
        return {"available_mib": 0, "total_mib": 0, "name": "", "probe_source": "unavailable", "probe_error": str(exc)}


def fit_hardware(meta: GgufMeta, device: dict[str, Any] | None = None) -> dict[str, Any]:
    """根据硬件显存给出运行建议（能否跑/推荐量化/CPU 模式提示）。

    规则（启发式）：
    - 可用显存 >= 模型估算 → 推荐直接跑
    - 显存不足但有 CPU 内存 → 提示 CPU 模式（OLLAMA_NUM_CPU / -ngl 0）
    - 显存完全不足 → 建议换更小量化/更小模型
    """
    device = device or _probe_device()
    available_mib = int(device.get("available_mib", 0) or 0)

    # 估算模型显存：参数量 × 每 B 量化系数 + 上下文开销
    quant_coef = _QUANT_MIB_PER_B.get(meta.quant.split("_")[0].lower(), 0.7)
    if meta.quant.startswith("f16"):
        quant_coef = 2.0
    params = meta.parameters_b or _estimate_params_from_size(meta.size_bytes)
    model_mib = int(params * quant_coef * 1024)
    ctx_mib = min(2048, max(256, int(meta.context_length / 128))) if meta.context_length else 256
    total_needed = model_mib + ctx_mib

    suggestions: list[str] = []
    level = "ok"

    if available_mib <= 0:
        level = "cpu_only"
        suggestions.append("未检测到可用 GPU 显存，将使用 CPU 推理（速度较慢，但可运行）")
        if total_needed > 32 * 1024:
            suggestions.append(f"模型约需 {total_needed // 1024} GB 内存，请确保系统内存充足")
    elif total_needed <= available_mib:
        suggestions.append(f"显存充足（可用 {available_mib // 1024} GB ≥ 需要 {total_needed // 1024} GB），可直接 GPU 运行")
    else:
        # 显存不足
        ratio = total_needed / available_mib if available_mib else 99
        if ratio <= 2.0:
            level = "partial_offload"
            suggestions.append(f"显存不足（需要约 {total_needed // 1024} GB，可用 {available_mib // 1024} GB），建议：")
            suggestions.append("  ① 启用 GPU 分层加载（ollama 自动 offload 到 CPU）")
            suggestions.append(f"  ② 或改用更低量化档（当前 {meta.quant}，可尝试 q4_K_M / q3_K_M）")
        else:
            level = "low"
            suggestions.append(f"显存严重不足（需要约 {total_needed // 1024} GB，可用 {available_mib // 1024} GB），建议：")
            suggestions.append("  ① 换用更小的量化档（q2_K / IQ2_XS）或更小参数模型")
            suggestions.append("  ② 仅 CPU 运行（OLLAMA 会自动使用系统内存，但速度慢）")

    return {
        "level": level,  # ok | partial_offload | low | cpu_only
        "device": device,
        "model_mib": model_mib,
        "context_mib": ctx_mib,
        "total_needed_mib": total_needed,
        "suggestions": suggestions,
    }


def _estimate_params_from_size(size_bytes: int) -> float:
    """从文件大小粗估参数量（B）：假设 Q4 量化 ~0.65B/GB。"""
    gb = size_bytes / (1024 ** 3)
    return round(gb * 1.5, 2)  # Q4 下 1GB ≈ 1.5B 参数


# ── Ollama 导入 ──────────────────────────────────────────────────────────────

_OLLAMA_PRESETS = {
    "qwen3vl": {
        "template": "{{- if .System }}<|im_start|>system\n{{ .System }}<|im_end|>\n{{- end }}\n{{- range .Messages }}<|im_start|>{{ .Role }}\n{{ .Content }}<|im_end|>\n{{- end }}<|im_start|>assistant\n",
        "stop": ["<|im_end|>", "<|im_start|>"],
    },
}


def _modelfile_content(gguf_path: str, mmproj_path: str = "", template: str = "",
                       stop: list[str] | None = None) -> str:
    """生成 Modelfile 内容。FROM 绝对路径 + ADAPTER（视觉）+ TEMPLATE。"""
    lines = [f"FROM {gguf_path}"]
    if mmproj_path:
        lines.append(f"ADAPTER {mmproj_path}")
    if template:
        lines.append(f'TEMPLATE """{template}"""')
    if stop:
        for s in stop:
            lines.append(f'PARAMETER stop "{s}"')
    return "\n".join(lines)


def _suggest_model_name(meta: GgufMeta) -> str:
    """生成 Ollama 模型名：架构-参数量-量化，如 qwen3vl:8b-q6_k。"""
    arch = meta.architecture or "model"
    params = f"{meta.parameters_b:g}b" if meta.parameters_b else ""
    quant = meta.quant
    base = f"{arch}:{params}"
    if quant:
        base = f"{arch}:{params}-{quant}" if params else f"{arch}:{quant}"
    # 清洗：ollama 名称只允许小写字母数字 ._-:
    base = re.sub(r"[^a-z0-9._:\-]", "", base.lower())
    return base or "gguf-model"


def _run_ollama(args: list[str], timeout: int = 600) -> tuple[int, str]:
    """执行 ollama 命令。返回 (returncode, stdout+stderr)。"""
    exe = shutil.which("ollama")
    if not exe:
        # Windows 常见安装路径兜底
        candidates = []
        if local_app_data := os.environ.get("LOCALAPPDATA"):
            candidates.append(Path(local_app_data) / "Programs" / "Ollama" / "ollama.exe")
        if program_files := os.environ.get("PROGRAMFILES"):
            candidates.append(Path(program_files) / "Ollama" / "ollama.exe")
        for cand in candidates:
            if cand.is_file():
                exe = str(cand)
                break
    if not exe:
        return 1, "未找到 ollama 可执行文件，请先安装 Ollama（https://ollama.com）"
    try:
        proc = subprocess.run(
            [exe, *args],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except FileNotFoundError:
        return 1, "ollama 可执行文件不存在"
    except subprocess.TimeoutExpired:
        return 1, f"ollama 命令超时（>{timeout}s）"


def import_to_ollama(
    gguf_path: str,
    *,
    model_name: str = "",
    mmproj_path: str = "",
    quantize: str = "",
    ollama_host: str = "http://127.0.0.1:11434",
    timeout: int = 900,
) -> ImportResult:
    """把 GGUF 导入 Ollama。

    参数：
    - gguf_path    主模型 GGUF 绝对路径
    - model_name   Ollama 模型名（默认自动生成）
    - mmproj_path  视觉投影文件（可选，自动配对）
    - quantize     ollama create -q 参数（如 q4_K_M，可选）
    - ollama_host  Ollama 服务地址
    """
    started = time.time()
    path = Path(gguf_path).expanduser().resolve()
    if not path.is_file():
        return ImportResult(ok=False, message=f"GGUF 文件不存在：{gguf_path}")

    meta = parse_gguf(path)
    if meta is None:
        return ImportResult(ok=False, message=f"无法解析 GGUF 文件：{path.name}")

    if not model_name:
        model_name = _suggest_model_name(meta)

    # mmproj 自动配对
    if not mmproj_path and meta.is_vision and meta.kind != "mmproj":
        found = find_mmproj_for(path.parent, str(path))
        if found:
            mmproj_path = found

    # 构造 Modelfile
    preset = _OLLAMA_PRESETS.get(meta.architecture.lower(), {})
    modelfile = _modelfile_content(
        str(path), mmproj_path,
        template=preset.get("template", ""),
        stop=preset.get("stop"),
    )

    try:
        with tempfile.TemporaryDirectory(prefix="gguf_import_") as tmp:
            mf_path = Path(tmp) / "Modelfile"
            mf_path.write_text(modelfile, encoding="utf-8")
            cmd = ["create", model_name, "-f", str(mf_path)]
            if quantize:
                cmd += ["-q", quantize]
            env = dict(os.environ)
            env["OLLAMA_HOST"] = ollama_host
            code, out = _run_ollama(cmd, timeout=timeout)
            if code != 0:
                return ImportResult(
                    ok=False, model_name=model_name, meta=meta,
                    message=f"ollama create 失败（exit {code}）：{out[-800:]}",
                    elapsed_sec=round(time.time() - started, 1),
                )
    except Exception as exc:  # noqa: BLE001
        return ImportResult(
            ok=False, model_name=model_name, meta=meta,
            message=f"导入异常：{exc}", elapsed_sec=round(time.time() - started, 1),
        )

    return ImportResult(
        ok=True, model_name=model_name, meta=meta,
        message=f"导入成功：{model_name}（{meta.architecture}，{meta.quant}，{meta.size_label or ''}）"
                f"{'，支持视觉' if meta.is_vision else ''}",
        elapsed_sec=round(time.time() - started, 1),
    )


# ── Provider 注册（写入 ai_providers 表） ────────────────────────────────────

def register_provider(
    model_name: str,
    *,
    base_url: str = "http://127.0.0.1:11434/v1",
    api_key: str = "ollama",
    provider_name: str = "",
) -> dict[str, Any]:
    """把导入的模型注册进 Demiurge 的 Ollama provider（对话/视觉模型）。

    返回 {ok, message, provider_id?, model?}。
    已存在同名 provider 则只补模型；不存在则新建。
    """
    try:
        from app.services import ai_provider_service
        from app.schemas.ai_provider import AIProviderCreate
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"provider 服务不可用：{exc}"}

    provider_name = provider_name or "Ollama 本地"
    try:
        providers = ai_provider_service.list_providers()
        provider = next((p for p in providers if p.base_url.rstrip("/") == base_url.rstrip("/")), None)

        if provider is None:
            created = ai_provider_service.create_provider(AIProviderCreate(
                name=provider_name,
                provider_type="openai_compatible",
                base_url=base_url,
                api_key=api_key,
                default_model=model_name,
                enabled=True,
                models=[model_name],
            ))
            return {"ok": True, "message": f"已创建 provider「{provider_name}」并注册 {model_name}",
                    "provider_id": created.id}
        else:
            models = ai_provider_service.get_provider_models(provider.id)
            if model_name in models:
                return {"ok": True, "message": f"{model_name} 已在 provider「{provider.name}」中",
                        "provider_id": provider.id}
            ai_provider_service.add_manual_model(provider.id, model_name)
            return {"ok": True, "message": f"已把 {model_name} 加入 provider「{provider.name}」",
                    "provider_id": provider.id}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"注册 provider 失败：{exc}"}


def import_gguf_flow(
    gguf_path: str,
    *,
    model_name: str = "",
    mmproj_path: str = "",
    quantize: str = "",
    register: bool = True,
) -> dict[str, Any]:
    """一键流程：解析 → 导入 Ollama → 注册 provider。返回完整结果。"""
    result = import_to_ollama(
        gguf_path, model_name=model_name, mmproj_path=mmproj_path, quantize=quantize,
    )
    payload: dict[str, Any] = {
        "ok": result.ok,
        "model_name": result.model_name,
        "message": result.message,
        "meta": _meta_dict(result.meta) if result.meta else None,
        "elapsed_sec": result.elapsed_sec,
        "fit": fit_hardware(result.meta) if result.meta else None,
    }
    if result.ok and register:
        reg = register_provider(result.model_name)
        payload["register"] = reg
    return payload


# ── 工具函数 ─────────────────────────────────────────────────────────────────

def ollama_list_models(host: str = "http://127.0.0.1:11434") -> list[str]:
    """列出 Ollama 当前已安装模型（用于去重/展示）。"""
    try:
        import json
        import urllib.request
        req = urllib.request.Request(host.rstrip("/") + "/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return [str(m.get("name", "")) for m in data.get("models", []) if m.get("name")]
    except Exception:  # noqa: BLE001
        return []


def is_ollama_running(host: str = "http://127.0.0.1:11434") -> bool:
    return len(ollama_list_models(host)) > 0 or _ping(host)


def _ping(host: str) -> bool:
    try:
        import urllib.request
        with urllib.request.urlopen(host.rstrip("/") + "/api/tags", timeout=3) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001
        return False
