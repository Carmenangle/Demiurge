"""提示词模版生成链接线探针（2026-10-25 P2，零 LLM 桩版）。

不起后端、不连 LLM：用桩 generate_text 复述「模型一次就写对」的路径，验证
custom 模版从 store → 编排层解析 → generate()/build_video_request 的**整条接线**：
  ① 建自定义 anima 模版（克隆内置 + 追加段 + 自有质量/负面词）；
  ② _resolve_prompt_template 解析出（内置 kind, 记录）；
  ③ generate() 吃 system_override/quality_override，成品仍过同一合同；
  ④ 建自定义视频模版 → _video_skeleton_for → build_video_request 三形态；
  ⑤ 删模版后解析降级（按 prompt_profile_kind），不抛错。

跑法：backend/.venv/Scripts/python.exe scripts/probe_prompt_template_chain.py
产出：stdout（不落盘；需要留档时自行重定向）。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

for _k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_k, None)

from app.services import agency_scene_comfy as asc  # noqa: E402
from app.services import image_prompt_profiles as ipp  # noqa: E402
from app.services import prompt_template_store as store  # noqa: E402
from app.services import video_prompt as vp  # noqa: E402

# 模版目录落系统临时区，绝不碰真实 backend/data
_TMP = Path(tempfile.mkdtemp(prefix="probe-pt-"))
store.PROMPT_TEMPLATES_DIR = _TMP / "prompt_templates"

STUB_ANIMA_JSON = json.dumps({
    "visual_hook": "a diagonal rail of warm light leads to the decisive glance",
    "primary_focus": "the reflected crimson eye",
    "supporting_elements": ["silver crest", "pale silhouette"],
    "content": (
        "1girl, long black hair, amber eyes, white shirt, black pencil skirt, "
        "legs together, medium shot, dramatic side light, notice board background\n"
        "She leans back against the notice board with her legs trembling."
    ),
})

SPEC = {
    "narrative": "角色A抬眼笑说「开饭」，三人围坐面馆。",
    "appearance": "角色A(米色针织开衫+陶杯)、角色B(粉卫衣+棒棒糖)、角色C(和风长衫+茶盏)",
    "wardrobe": "全员日常私服",
    "locale": "温暖小面馆内景，暖黄吊灯，木质吧台",
    "actors": ["角色A", "角色B", "角色C"],
    "rating": "sfw",
    "motion": 2,
    "negative_prompt": "禁止五官漂移",
}


def main() -> int:
    print(f"[probe] store dir: {store.PROMPT_TEMPLATES_DIR}")

    # ① 自定义 anima 模版（克隆内置 + 追加段 + 自有质量/负面词）
    tpl = store.save_template({
        "kind": "anima_tags", "name": "探针·我的Anima", "base_id": "builtin:anima_tags",
        "system_text": store.builtin_view("anima_tags")["system_text"],
        "extra_instructions": "【探针追加】红色元素必须在首行出现。",
        "quality_tags": "best quality, probe_quality",
        "negative_tags": "探针负面词",
    })
    print(f"[1] custom template id={tpl['id']} base_hash={tpl['base_hash']}")

    # ② 编排层解析
    ctx = {"prompt_profile": f"custom:{tpl['id']}", "prompt_profile_kind": "anima_tags"}
    kind, record, missing = asc._resolve_prompt_template(ctx)
    overrides = asc._template_overrides(kind, record)
    print(f"[2] resolve → kind={kind} missing={missing} "
          f"system_override={bool(overrides['system_override'])} "
          f"quality={overrides['quality_override']!r} negative={overrides['negative_override']!r}")
    assert kind == "anima_tags" and not missing and record is not None

    # ③ generate() 走 override（桩模型一次写对）
    scene = {"rating": "sfw", "actors": ["角色A"], "narrative": "她抬眼笑。"}
    diagnostics: dict[str, object] = {}

    def _stub(system: str, user: str) -> str:
        assert "【探针追加】" in system, "system_override 没送到模型侧"
        return STUB_ANIMA_JSON

    prompt = ipp.generate(
        kind, scene, _stub, diagnostics,
        system_override=overrides["system_override"],
        quality_override=overrides["quality_override"],
    )
    head = prompt.splitlines()[0] if prompt else ""
    print(f"[3] generate strategy={diagnostics.get('strategy')} chars={len(prompt)}")
    print(f"    head: {head[:110]}…")
    assert "probe_quality" in head, "自定义质量词没进首行"
    assert ipp._errors(kind, prompt, scene) == [], "custom 产出没过合同"
    neg = ipp.negative_prompt(kind, scene, negative_override=overrides["negative_override"])
    print(f"    negative: {neg}")

    # ④ 视频：自定义 climax 模版 → 骨架 → 三形态 dry-run
    vtpl = store.save_template({
        "kind": "video_climax", "name": "探针·视频高潮",
        "section_titles": {"meta": "元信息·探针"},
        "camera_band_overrides": {"2": "探镜：镜头缓慢横移"},
        "beat_labels": ["起", "承", "合"], "default_duration": 6,
        "negative_extra": "探针负面追加", "meta_note": "探针备注",
    })
    vctx = {"video_prompt_template_id": f"custom:{vtpl['id']}"}
    sk = asc._video_skeleton_for(vctx, "climax")
    assert sk is not None
    for mode in ("climax", "firstlast", "transition"):
        # 每形态各建一个对应 kind 的模版（_video_skeleton_for 按 mode 匹配）。
        # 转场形态的 [过渡] 拍是单拍 ⇒ 只看 0 档；正片看 2 档。
        band_key = "0" if mode == "transition" else "2"
        mtpl = store.save_template({
            "kind": f"video_{mode}", "name": f"探针·{mode}",
            "section_titles": {"meta": "元信息·探针"},
            "camera_band_overrides": {band_key: "探镜：镜头缓慢横移"},
            "default_duration": 6, "negative_extra": "探针负面追加", "meta_note": "探针备注",
        })
        sk = asc._video_skeleton_for(
            {"video_prompt_template_id": f"custom:{mtpl['id']}"}, mode)
        req = vp.build_video_request(mode=mode, spec=SPEC, video_config={}, skeleton=sk)
        text = req["submit"]["prompt"]
        print(f"[4] {mode}: sections={req['prompt_sections']}")
        print(f"    first block: {text.split(chr(10) + chr(10))[0][:90]}")
        assert "元信息·探针" in text and "探镜：镜头缓慢横移" in text
        assert "探针负面追加" in text and "6 seconds" in text

    # ⑤ 删除后降级（不抛错）
    store.delete_template(tpl["id"])
    kind2, record2, missing2 = asc._resolve_prompt_template(ctx)
    print(f"[5] after delete → kind={kind2} record={record2} missing={missing2}")
    assert kind2 == "anima_tags" and record2 is None and missing2 is True

    print("[probe] OK —— 自定义模版整条接线（生图 + 视频三形态 + 降级）全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
