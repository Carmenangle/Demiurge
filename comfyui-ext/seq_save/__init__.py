# -*- coding: utf-8 -*-
"""ComfyUI 自定义节点：保存图片（裸四位序号续排）。

需求（2026-09-25 用户）：目标文件夹里已有 0168.png…0171.png 这类裸数字文件名，
新图继续 0172、0173…（4 位补零）；已删掉的号不回收——永远「现有最大序号 + 1」。
标准 SaveImage 的文件名合同写死「前缀_#####_」，做不到裸数字续排 ⇒ 本节点补位。

部署：随 comfyui-ext 外挂目录加载（extra_model_paths 的 custom_nodes），
重启 ComfyUI（后端重启即可）后，画布节点搜索「保存图片(序号续排)」。
"""
import json
import os
import re
import shutil

import numpy as np
from PIL import Image, PngImagePlugin
import torch

import folder_paths


class SeqSaveImage:
    """保存到指定文件夹，文件名 = [前缀]四位序号.png，序号 = 现有最大序号 + 1。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "output_dir": ("STRING", {"default": "", "multiline": False}),
                "prefix": ("STRING", {"default": ""}),
                "digits": ("INT", {"default": 4, "min": 2, "max": 8}),
            },
            "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"},
        }

    RETURN_TYPES = ()
    FUNCTION = "save"
    OUTPUT_NODE = True
    CATEGORY = "image/save"

    def save(self, images, output_dir, prefix="", digits=4, prompt=None, extra_pnginfo=None):
        out_dir = (output_dir or "").strip() or os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "output")
        os.makedirs(out_dir, exist_ok=True)

        # 扫现有最大序号：文件名 = [前缀]数字.png（忽略扩展名大小写）。
        pat = re.compile(r"^(?:%s)?(\d+)\.png$" % re.escape(prefix.strip()), re.IGNORECASE)
        max_seq = 0
        for name in os.listdir(out_dir):
            m = pat.match(name)
            if m:
                max_seq = max(max_seq, int(m.group(1)))

        # ⚠ pnginfo 必须是 PngImagePlugin.PngInfo 对象（裸 dict 会在 PIL._save 炸
        #   'dict' object has no attribute 'chunks'——真机 18:37 实锤）。
        metadata = None
        if prompt is not None:
            metadata = PngImagePlugin.PngInfo()
            metadata.add_text("prompt", json.dumps(prompt))
        if extra_pnginfo is not None:
            metadata = metadata or PngImagePlugin.PngInfo()
            for key, value in extra_pnginfo.items():
                metadata.add_text(key, json.dumps(value))

        results = []
        out_root = os.path.abspath(folder_paths.get_output_directory())
        preview_dir = os.path.join(out_root, "_seq_preview")
        for i, image in enumerate(images):
            seq = max_seq + 1 + i          # 已删的号不回收：永远现有最大 + 1
            name = f"{prefix.strip()}{str(seq).zfill(digits)}.png"
            path = os.path.join(out_dir, name)
            arr = 255.0 * image.cpu().numpy()
            img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
            img.save(path, pnginfo=metadata, compress_level=4)
            # 预览回传：/view 只服务 output/input/temp ⇒ 目标文件夹在 output 之外时，
            # 复制一份进 output/_seq_preview/（同序号覆盖）供前端展示。
            try:
                abs_path = os.path.abspath(path)
                if os.path.commonpath([abs_path, out_root]) == out_root:
                    rel = os.path.relpath(abs_path, out_root)
                    sub, ui_name = os.path.dirname(rel), os.path.basename(rel)
                else:
                    os.makedirs(preview_dir, exist_ok=True)
                    shutil.copyfile(path, os.path.join(preview_dir, name))
                    sub, ui_name = "_seq_preview", name
                results.append({"filename": ui_name, "subfolder": sub, "type": "output"})
            except Exception:  # noqa: BLE001
                results.append({"filename": name, "subfolder": "", "type": "output"})
        return {"ui": {"images": results}}

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("NaN")   # 每次都重跑（保存节点标准做法）


NODE_CLASS_MAPPINGS = {"SeqSaveImage": SeqSaveImage}
NODE_DISPLAY_NAME_MAPPINGS = {"SeqSaveImage": "保存图片(序号续排)"}
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
