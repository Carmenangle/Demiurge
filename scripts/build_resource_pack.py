"""构建可单独上传的 Demiurge 发布资源包（多包）。

每个包都是 `presets/<包名>/`，带 `manifest.json`（逐文件 sha256）与 `README.md`，
源文件按**点名清单**选取、按**白名单字段**清洗；`presets/` 其余内容一律被 .gitignore
与 release_preflight 挡在发布面外。
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

STRIP_FIELDS = {
    "api_key",
    "apiKey",
    "reverse_proxy",
    "proxy_password",
    "proxy_preset",
    "custom_url",
    "custom_include_headers",
    "custom_include_body",
    "custom_exclude_body",
    "api_url_scale",
    "authorization",
    "access_token",
}
SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"hf_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)

PRESET_PACKAGE = "Demiurge-presets-regex"
TEMPLATE_PACKAGE = "Demiurge-comfyui-templates"
# 模板记录只保留这些键：source_path / created_at / updated_at 不在名单里，
# 因此本机绝对路径不可能靠「忘了删」溜进发布面（白名单，不是黑名单）。
TEMPLATE_RECORD_KEYS = (
    "id",
    "name",
    "description",
    "exposed",
    "node_order",
    "input_node_ids",
    "output_node_ids",
    "primary_output_node_id",
    "preserve_loras",
    "workflow_data",
)
# 只服务画布预览的 widget 键，与 workflow_convert._UI_ONLY_WIDGET_KEYS 同口径：
# 提交链本来就会丢掉它们，洗掉不改变任何生成行为，但能带走第三方 CDN 的一次性播放地址。
UI_ONLY_WIDGET_KEYS = frozenset({"videopreview"})
# 模型权重下载地址要随包保留（首启靠它拉模型），其余外链属上一手分享者的产物。
ALLOWED_URL_HOSTS = frozenset({"huggingface.co"})
_URL_RE = re.compile(r"https?://([^/\"\\\s]+)")
# 视频生成节点类型：只有恰好一个候选时 template_store 才会自动绑 width/height，
# 所以「图里有且仅有一个视频节点」是宽高一致能成立的前提，必须在打包时钉住。
VIDEO_SIZE_NODE_TYPES = ("minimaxh3referencetovideo", "minimaxh3imagetovideo")
# 逐个点名，不扫目录：IndexTTS 那条记录的本机真实专名写在图内（非 source_path），
# 洗不掉也留不得，点名是唯一能把它挡在包外的做法。
TEMPLATE_PACKAGE_ITEMS: tuple[dict[str, Any], ...] = (
    {
        "record_id": "643baff143664bf7bb1afe44d6c37470",
        "stem": "MiniMax_H3_图生视频_首帧",
        "name": "MiniMax H3 图生视频（首帧）",
        # 这四条只存在于记录手标的 exposed 里：_infer_binding 只对已 exposed 的字段生效，
        # 所以「只装图本体」会丢它们 —— 包必须装整条记录，见 build_template_pack 的断言。
        # #58：last_frame_image 是尾帧 LoadImage 的注入口，缺它前端 lastFrameImage 静默丢弃。
        "manual_bindings": ("base_image", "video_prompt", "video_duration", "last_frame_image"),
        "first_frame_field": "first_frame",
        "last_frame_field": "last_frame",
        # 换型后的类型逐条写进清单：下一条模板可以是别的节点类型，断言不能替它假定答案。
        "expected_node_type": "MiniMaxH3ImageToVideo",
    },
)


def sanitize(value: Any) -> Any:
    """递归剥离连接、鉴权字段，保留预设和正则语义。"""
    if isinstance(value, dict):
        return {key: sanitize(item) for key, item in value.items() if key not in STRIP_FIELDS}
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    return value


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assert_clean(path: Path) -> None:
    content = path.read_text(encoding="utf-8")
    if any(pattern.search(content) for pattern in SECRET_PATTERNS):
        raise ValueError(f"资源包仍含疑似密钥: {path}")


def _assert_allowed_hosts(path: Path) -> None:
    """外链只许是模型仓库；其余是上一手分享者的一次性产物地址。"""
    hosts = {match.split("?", 1)[0].lower() for match in _URL_RE.findall(path.read_text(encoding="utf-8"))}
    unexpected = sorted(hosts - ALLOWED_URL_HOSTS)
    if unexpected:
        raise ValueError(f"资源包含非白名单外链: {path} -> {unexpected}")


def _assert_no_private_names(path: Path, names: tuple[str, ...]) -> None:
    if not names:
        return
    content = path.read_text(encoding="utf-8").lower()
    hits = [name for name in names if name.lower() in content]
    if hits:
        raise ValueError(f"资源包仍含本机真实专名: {path} -> {hits}")


def build(root: Path, output: Path) -> list[Path]:
    preset_source = root / "presets" / "GrayWill-0.46-demiurge.json"
    global_regex_source = root / "backend" / "data" / "regex_scripts.json"
    if not preset_source.is_file():
        raise FileNotFoundError(preset_source)
    if not global_regex_source.is_file():
        raise FileNotFoundError(global_regex_source)

    preset = sanitize(_read_json(preset_source))
    extensions = preset.get("extensions") if isinstance(preset, dict) else None
    embedded = extensions.get("regex_scripts") if isinstance(extensions, dict) else None
    global_regex = sanitize(_read_json(global_regex_source))
    if not isinstance(embedded, list) or not all(isinstance(item, dict) for item in embedded):
        raise ValueError("预设缺少 extensions.regex_scripts")
    if not isinstance(global_regex, list) or not all(
        isinstance(item, dict) for item in global_regex
    ):
        raise ValueError("全局正则文件格式无效")

    preset_target = output / "preset" / "GrayWill-0.46-demiurge.json"
    embedded_target = output / "regex" / "GrayWill-0.46-embedded-regex.json"
    global_target = output / "regex" / "Demiurge-global-regex.json"
    _write_json(preset_target, preset)
    _write_json(embedded_target, embedded)
    _write_json(global_target, global_regex)

    readme = output / "README.md"
    readme.parent.mkdir(parents=True, exist_ok=True)
    readme.write_text(
        """# Demiurge 预设与正则资源包

## 内容

- `preset/GrayWill-0.46-demiurge.json`：Demiurge 当前适配预设，保留 33 条内嵌 ST 正则。
- `regex/GrayWill-0.46-embedded-regex.json`：上述 33 条正则的独立导出，供只导入正则时使用。
- `regex/Demiurge-global-regex.json`：Demiurge 当前使用的 24 条全局正则。

## 使用

在 Demiurge 中导入预设后，再按需要导入全局正则。预设内嵌正则导出主要用于 SillyTavern 或手工恢复；不要把内嵌正则和同名全局正则重复启用，否则文本可能被处理两次。

本包不包含角色卡专属正则、会话、世界书、图片、API 配置或原始 `GrayWill-0.46-ex` 参考文件。所有连接与鉴权字段已递归移除。

公开分发前仍需确认 GrayWill 预设及其正则的上游授权；清洗密钥不等于取得再分发许可。
""",
        encoding="utf-8",
    )

    files = [preset_target, embedded_target, global_target, readme]
    for path in files:
        _assert_clean(path)
    manifest = output / "manifest.json"
    _write_json(
        manifest,
        {
            "package": "Demiurge-presets-regex",
            "preset": "GrayWill-0.46-demiurge",
            "embedded_regex_count": len(embedded),
            "global_regex_count": len(global_regex),
            "files": [
                {
                    "path": path.relative_to(output).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": _sha256(path),
                }
                for path in files
            ],
        },
    )
    _assert_clean(manifest)
    return [*files, manifest]


def _privacy_denylist(root: Path) -> tuple[str, ...]:
    """本机真实专名清单是 Git 忽略的运行态文件；干净检出上不存在 ⇒ 返回空、不判红。"""
    path = root / "backend" / "data" / "privacy_denylist.txt"
    if not path.is_file():
        return ()
    return tuple(line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _scrub_graph_ui_state(graph: dict) -> None:
    """丢掉只服务画布预览的 widget 键；图结构（nodes/links/slots）一律不动。"""
    for node in graph.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        widgets = node.get("widgets_values")
        if isinstance(widgets, dict):
            for key in list(widgets):
                if key in UI_ONLY_WIDGET_KEYS:
                    widgets.pop(key)


def sanitize_template(record: dict) -> dict:
    """把运行态模板记录洗成可分发的一份：白名单取键 + 剥预览态。"""
    graph = record.get("workflow_data")
    if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list):
        raise ValueError("模板记录缺少 workflow_data 图快照")
    cleaned: dict[str, Any] = {key: copy.deepcopy(record[key]) for key in TEMPLATE_RECORD_KEYS if key in record}
    _scrub_graph_ui_state(cleaned["workflow_data"])
    return cleaned


def _assert_graph_intact(before: dict, after: dict) -> None:
    """清洗只许删预览态：节点/链路集合与首帧接线必须逐条还在。"""

    def node_ids(graph: dict) -> list[str]:
        return sorted(str(node.get("id")) for node in graph.get("nodes") or [])

    if node_ids(before) != node_ids(after):
        raise ValueError("清洗改变了节点集合")
    if len(before.get("links") or []) != len(after.get("links") or []):
        raise ValueError("清洗改变了链路集合")
    link_ids = {str(link[0]) for link in after.get("links") or [] if isinstance(link, list) and link}
    for node in after.get("nodes") or []:
        for index, slot in enumerate(node.get("inputs") or []):
            if not isinstance(slot, dict):
                continue
            ref = slot.get("link")
            if ref is None:
                continue
            if str(ref) not in link_ids:
                raise ValueError(f"悬空引用：节点 {node.get('id')} 输入 {index} 指向不存在的链路 {ref}")
            link = next(item for item in after["links"] if str(item[0]) == str(ref))
            if node["id"] != link[3] or index != link[4]:
                raise ValueError(f"链路端点不符：节点 {node.get('id')} 输入 {index} vs 链路 {link}")


def _assert_template_publishable(record: dict, item: dict) -> None:
    """钉住「换型 + 绑定 + 首尾帧接线」这几件真正会静默丢失的事。"""
    if str(record.get("name") or "") != item["name"]:
        raise ValueError(f"模板名与点名清单不符: {record.get('name')!r} != {item['name']!r}")
    for key in ("source_path", "created_at", "updated_at"):
        if key in record:
            raise ValueError(f"清洗后仍带本机字段 {key}")

    bindings = {
        str(field.get("binding") or "")
        for field in record.get("exposed") or []
        if isinstance(field, dict) and field.get("binding")
    }
    missing = [name for name in item["manual_bindings"] if name not in bindings]
    if missing:
        raise ValueError(f"缺少手标绑定（只装图本体会丢的就是这些）: {missing}")

    graph = record["workflow_data"]
    video_nodes = [
        node
        for node in graph.get("nodes") or []
        if str(node.get("type") or "").lower() in VIDEO_SIZE_NODE_TYPES
    ]
    if len(video_nodes) != 1:
        raise ValueError(
            f"视频生成节点须恰好 1 个才能自动绑宽高，实际 {len(video_nodes)} 个"
        )
    node = video_nodes[0]
    frame = next(
        (slot for slot in node.get("inputs") or [] if str(slot.get("name")) == item["first_frame_field"]),
        None,
    )
    if not frame or frame.get("link") is None:
        raise ValueError(f"节点 {node.get('id')} 的 {item['first_frame_field']} 没有接线（首帧链丢失）")
    # #58：SetNode「尾帧图像」写了变量但没人读是静默的 ⇒ 必须钉住 GetNode → last_frame。
    last_frame = next(
        (slot for slot in node.get("inputs") or []
         if str(slot.get("name")) == item["last_frame_field"]),
        None,
    )
    if not last_frame or last_frame.get("link") is None:
        raise ValueError(
            f"节点 {node.get('id')} 的 {item['last_frame_field']} 没有接线（尾帧链丢失）"
        )
    if str(node.get("type")) != item["expected_node_type"]:
        raise ValueError(
            f"首帧链节点类型回退成 {node.get('type')}（应为 {item['expected_node_type']}）"
        )


def build_template_pack(root: Path, output: Path) -> list[Path]:
    templates_dir = root / "backend" / "data" / "templates"
    files: list[Path] = []
    entries: list[dict[str, Any]] = []
    for item in TEMPLATE_PACKAGE_ITEMS:
        source = templates_dir / f"{item['record_id']}.json"
        if not source.is_file():
            raise FileNotFoundError(f"缺少模板记录: {source}")
        record = _read_json(source)
        if not isinstance(record, dict):
            raise ValueError(f"模板记录格式无效: {source}")
        before = record.get("workflow_data") or {}
        cleaned = sanitize_template(record)
        _assert_graph_intact(before, cleaned["workflow_data"])
        _assert_template_publishable(cleaned, item)

        template_target = output / "templates" / f"{item['stem']}.json"
        graph_target = output / "workflows" / f"{item['stem']}.json"
        _write_json(template_target, cleaned)
        _write_json(graph_target, cleaned["workflow_data"])
        files += [template_target, graph_target]
        entries.append(
            {
                "id": cleaned.get("id"),
                "name": cleaned.get("name"),
                "template_file": template_target.relative_to(output).as_posix(),
                "workflow_file": graph_target.relative_to(output).as_posix(),
                "nodes": len(cleaned["workflow_data"].get("nodes") or []),
                "links": len(cleaned["workflow_data"].get("links") or []),
                "bindings": sorted(
                    str(field.get("binding"))
                    for field in cleaned.get("exposed") or []
                    if isinstance(field, dict) and field.get("binding")
                ),
            }
        )
    if not entries:
        raise ValueError("点名清单为空：模板包不得产出空壳")

    readme = output / "README.md"
    readme.parent.mkdir(parents=True, exist_ok=True)
    readme.write_text(
        f"""# Demiurge ComfyUI 工作流模板包

## 内容

- `templates/<模板名>.json`：**已配置好的模板记录**（图快照 + 暴露参数绑定 + 输入/输出节点 + `preserve_loras`）。
- `workflows/<模板名>.json`：同一张图的裸 LiteGraph 工作流，供「工作流模板 → 选择文件导入 / 扫描默认目录」在画布里查看与改参。

当前收录：{", ".join(str(entry["name"]) for entry in entries)}。

## 使用（两条路，选一条）

1. **装成可直接 `/w` 调用的模板（推荐，绑定原样恢复）**
   把 `templates/<模板名>.json` 复制到 Demiurge 数据目录的 `templates/` 下即可，
   文件名就是模板 id（`<32 位 hex>.json`）。列表每次请求都读盘，放好即生效，不必重启。
   数据目录：桌面版在应用目录下 `backend/data/`，源码运行在 `backend/data/`，
   设了 `LAF_DATA_DIR` 时以该变量为准。同名文件会被覆盖，装前先备份你改过的那份。
2. **只拿工作流图**
   把 `workflows/<模板名>.json` 放进「设置 → 路径」里配置的工作流目录，
   在「工作流模板 → 扫描默认目录」导入，然后**手动重新勾选暴露参数**——
   这条路会丢失下面的绑定，除非你愿意重标一遍。

## 为什么必须走第 1 条

`video_width` / `video_height` 由后端按节点类型自动补，只要图里有且仅有一个视频生成节点就能恢复；
但 `base_image` / `video_prompt` / `video_duration` / `last_frame_image`
**只存在于模板记录手标的 exposed 里**，自动推断只作用于已勾选的字段。
⇒ 只搬工作流 JSON = 首帧参考口、视频提示词、时长、尾帧图四个入口静默消失，
画布上看不出任何异常。这就是本包同时放 `templates/` 的原因。

## 清洗与保留

剥掉：本机绝对路径（`source_path` 等，按白名单取键，不可能靠「忘了删」漏出）、
画布预览态 `videopreview`（提交链本来也丢，附带清掉上一手分享者的一次性 CDN 播放地址）。
保留：`huggingface.co` 的 4 条模型权重地址（首启下载要用）、全部节点/链路/槽位接线、
模板自带 LoRA 的 `preserve_loras`。构建时对每包做真实专名与外链白名单校验，命中即构建失败。

## 授权

工作流图与节点参数来自第三方分享，公开分发前需确认上游授权；清洗密钥不等于取得再分发许可。
""",
        encoding="utf-8",
    )

    pack_files = [readme, *files]
    for path in pack_files:
        _assert_clean(path)
        _assert_allowed_hosts(path)
        _assert_no_private_names(path, _privacy_denylist(root))
    manifest = output / "manifest.json"
    _write_json(
        manifest,
        {
            "package": TEMPLATE_PACKAGE,
            "kind": "comfyui_workflow_template",
            "template_count": len(entries),
            "templates": entries,
            "files": [
                {
                    "path": path.relative_to(output).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": _sha256(path),
                }
                for path in pack_files
            ],
        },
    )
    _assert_clean(manifest)
    return [*pack_files, manifest]


# 包名 → 构建函数的唯一登记表；顺序与内容必须和 release_preflight.DISTRIBUTABLE_PACKAGES
# 及 .gitignore 的反向豁免一致（tests/test_release_preflight.py 逐条锁住）。
PACKAGE_BUILDERS = {
    PRESET_PACKAGE: build,
    TEMPLATE_PACKAGE: build_template_pack,
}


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    presets_dir = root / "presets"
    # 不传参数 = 全量刷新；传包名 = 只刷那一个。不加这个开关时，只想重出模板包的
    # 一次运行会把预设包一起按当前本机预设重写，无关改动就顺着 diff 溜进提交。
    wanted = list(argv or []) or list(PACKAGE_BUILDERS)
    unknown = sorted(set(wanted) - set(PACKAGE_BUILDERS))
    if unknown:
        print(f"未知资源包: {', '.join(unknown)}；可选: {', '.join(PACKAGE_BUILDERS)}", file=sys.stderr)
        return 2
    for name in wanted:
        files = PACKAGE_BUILDERS[name](root, presets_dir / name)
        total = sum(path.stat().st_size for path in files)
        print(f"资源包已生成: presets/{name}（文件 {len(files)}，{total / 1024:.1f} KiB）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
