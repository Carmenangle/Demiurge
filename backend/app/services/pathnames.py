"""文件名/路径片段清洗，单点。

此前散在 image_store/image_utils/chat_snapshot/model_downloader 四处（两处字节相同，
两处 fallback/strip 略异）。收口成一个可调 helper，各调用方按需传 fallback/strip 保持原行为。
纯函数，接口即测试面。
"""
from __future__ import annotations

import re


def safe_seg(s: str, fallback: str = "x", *, strip: bool = True) -> str:
    """把任意字符串清成安全的路径片段：非 [A-Za-z0-9._-] 换成 _。

    - strip=True（默认）：两端去 . 和 _（原 image_store/image_utils/model_downloader 行为）。
    - strip=False：不去两端（原 chat_snapshot 行为，thread_id 用）。
    - 结果为空时回退 fallback。
    """
    out = re.sub(r"[^A-Za-z0-9._\-]", "_", s or "")
    if strip:
        out = out.strip("._")
    return out or fallback


def safe_dir(s: str, fallback: str = "x") -> str:
    """把仓库名清成安全的文件夹名，保留中文/字母数字，只挡 Windows 非法字符。

    与 safe_seg 不同：safe_seg 会把中文也换成 _（适合 id/文件名），
    safe_dir 用于「文件夹名=仓库名」，需保留中文可读性。
    - 挡掉 Windows 路径非法字符 \\ / : * ? " < > | 和控制字符 → _
    - 去两端空白与点（Windows 不允许文件夹名以点/空格结尾）
    - 结果为空回退 fallback
    """
    out = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", s or "")
    out = out.strip().strip(".")
    return out or fallback
