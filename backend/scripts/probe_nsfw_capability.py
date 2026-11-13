"""验证「本地模型能生成 NSFW」——不是生成内容，是验链路不做内容审查。

判定链（全程本地，零云端）：
  1. 用《示例作品甲》镜 6 的**原稿露骨提示词**提交 ComfyUI（`Krea2-高清文生图优化流`）；
  2. 负面词取 `probe_wedding_video_flow.NEGATIVE_SHOT`（已确认不含 NSFW/nude/explicit）；
  3. 出图后用 **WD14 tagger** 反推，`include_rating=True` 取 rating；
  4. rating 命中 `explicit` / `questionable` ⇒ PASS（模型确实把露骨内容画出来了）。

同时报告「提示词有没有被截断/改写」，排除"提示词被吞掉但图碰巧露骨"的假阳性。

用法：python backend/scripts/probe_nsfw_capability.py [--shots 6,7] [--threshold 0.5]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

from app.services import (  # noqa: E402
    comfyui_client,
    scene_reference,
    template_store,
    wd14_tagger,
    workflow_submission,
)

_spec = importlib.util.spec_from_file_location(
    "wedding_flow", BACKEND / "scripts" / "probe_wedding_video_flow.py")
flow = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = flow
_spec.loader.exec_module(flow)

URL = "http://127.0.0.1:8188"
OUT = ROOT / "_tmp" / "nsfw_capability"
RATINGS = ("general", "sensitive", "questionable", "explicit")


def generate(shot: dict, timeout_s: int = 600) -> dict:
    index = int(shot["shot_index"])
    dest = OUT / f"shot_{index:02d}_raw.png"
    from PIL import Image

    if dest.is_file() and dest.stat().st_size > 100_000:
        # 复用已出图，只补打标（避免为了换个判定方式重跑一遍模型）
        print(f"[shot_{index:02d}] reuse {dest.name}", flush=True)
        prompt = str(shot.get("image_prompt") or "").strip()
        return {"shot_index": index, "status": "generated", "image": str(dest),
                "size": list(Image.open(dest).size), "prompt_chars": len(prompt),
                "prompt_id": "", "reused": True, "prompt": prompt}
    prompt = str(shot.get("image_prompt") or "").strip()
    template = template_store.get_template(flow.TEMPLATE_SHOT)
    values = scene_reference.build_template_values(
        template, prompt=prompt, negative=flow.NEGATIVE_SHOT,
        width=flow.SHOT_W, height=flow.SHOT_H,
        style_loras=[{"name": flow.STYLE_LORA, "weight": 0.8}],
    )
    submitted = workflow_submission.submit_template(
        flow.TEMPLATE_SHOT, values, prompt=prompt, url=URL,
        loras=[{"name": flow.STYLE_LORA, "weight": 0.8}], lora_mode="single",
    )
    pid = str(submitted.get("prompt_id") or "")
    print(f"[shot_{index:02d}] submit prompt_id={pid} prompt_chars={len(prompt)}", flush=True)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        res = comfyui_client.fetch_result(URL, pid)
        state = str(res.get("status") or "")
        if state == "completed":
            images = res.get("images") or []
            if not images:
                return {"shot_index": index, "status": "failed", "error": "no image"}
            img = images[0]
            OUT.mkdir(parents=True, exist_ok=True)
            comfyui_client.fetch_view_to_file(
                URL, str(img.get("filename") or ""), str(dest),
                type=str(img.get("type") or "output"),
                subfolder=str(img.get("subfolder") or ""),
            )
            from PIL import Image

            return {"shot_index": index, "status": "generated", "image": str(dest),
                    "size": list(Image.open(dest).size), "prompt_chars": len(prompt),
                    "prompt_id": pid, "prompt": prompt}
        if state in ("failed", "not_found"):
            return {"shot_index": index, "status": "failed",
                    "error": str(res.get("error"))[:300]}
        time.sleep(3)
    return {"shot_index": index, "status": "failed", "error": f"timeout {timeout_s}s"}


def rate(image_path: Path) -> dict:
    # 注意：include_rating 在 predict() 上，reverse_tags() 只是「只要标签名」的包装。
    tags = wd14_tagger.predict(image_path, include_rating=True)
    ratings = {name: round(score, 4) for name, score in tags if name.lower() in RATINGS}
    top = sorted(((name, round(score, 3)) for name, score in tags
                  if name.lower() not in RATINGS), key=lambda x: -x[1])[:12]
    return {"ratings": ratings, "top_tags": top}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default="6,7")
    ap.add_argument("--threshold", type=float, default=0.5)
    args = ap.parse_args()
    picks = {int(p) for p in args.shots.split(",") if p.strip().isdigit()}

    if not wd14_tagger.model_available():
        print("WD14 模型不可用，无法给客观判据")
        return 2

    review = json.loads(flow.REVIEW.read_text(encoding="utf-8"))
    shots = [s for s in review["shots"] if int(s["shot_index"]) in picks]

    template = template_store.get_template(flow.TEMPLATE_SHOT) or {}
    bindings = [str(e.get("binding") or "") for e in (template.get("exposed") or [])]
    print("=== 链路自检 ===")
    print("模板:", template.get("name"), "| exposed:", bindings)
    print("模板有 negative_prompt 槽（负面词是否真的生效）:", "negative_prompt" in bindings)
    print("负面词含压制词 NSFW/nude/explicit/sexual fluids:",
          any(w in flow.NEGATIVE_SHOT.lower() for w in
              ("nsfw", "nude", "explicit", "sexual fluid")))
    print("默认中性化:", flow.shot_prompt(shots[0])[1])
    print()

    report = {"threshold": args.threshold, "shots": []}
    for shot in shots:
        rec = generate(shot)
        if rec.get("status") != "generated":
            report["shots"].append(rec)
            print("  生成失败:", rec.get("error"))
            continue
        wd = rate(Path(rec["image"]))
        ratings = wd["ratings"]
        hit = {k: v for k, v in ratings.items()
               if k.lower() in ("explicit", "questionable") and v >= args.threshold}
        rec.update(wd)
        rec["nsfw_hit"] = bool(hit)
        rec["verdict"] = "PASS（本地模型能生成 NSFW）" if hit else "FAIL（未检出露骨 rating）"
        report["shots"].append(rec)
        print(f"  rating={ratings}")
        print(f"  top_tags={wd['top_tags'][:8]}")
        print(f"  -> {rec['verdict']}")
        print()

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    passed = [s for s in report["shots"] if s.get("nsfw_hit")]
    print(f"=== 结论：{len(passed)}/{len(report['shots'])} 镜 PASS ===")
    print("报告:", OUT / "report.json")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
