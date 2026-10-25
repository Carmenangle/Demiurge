"""首尾帧提示词真机探针（首尾帧线）。同时产出「高潮点成品 ＝ 回归基线」与「首尾帧两帧成品」。

存在理由（用户口径 2026-09-21）：首尾帧链与高潮点链**共享同一条编译链**（`generate()`），
改首尾帧时极易连带改坏高潮点。本探针一次跑两路，把「高潮点成品」当**零回归基线**留档，
以后每改一次首尾帧，重跑本探针比对 `_climax_baseline.json` 是否逐字未变。

两路（同一 scene、同一模型、同一预设）：
  ① 高潮基线：`ipp.generate_result(profile, scene, gen)`            —— 温度 0.4（与 i33/i34 探针同口径，基线可比）
  ② 首尾帧：  `ipp.generate_frame_prompts(profile, scene, frames, gen)` —— 温度 0.3（与生产 `/ai/prompt/profile/frames` 同口径）

`frames` 由 `story_frames.extract_story_frames(<楼层全文>)` 取（与 `agent_agency._wb_frames_and_video` 同源），
所以本探针能一次回答：
  - 双锚点到底挑中了哪两段（首/尾是否同段 ⇒ 静止退化）；
  - 「时点提取」LLM 有没有解析成功（`frame_extract_ok`）／提了英文还是中文；
  - 每帧成品长什么样、strategy、validation_errors；
  - **成品里出现的主体/构图是不是「高潮锚点的」**（问题 A 的证据）。

跑法：
    backend/.venv/Scripts/python.exe scripts/probe_frame_prompts.py
    PROBE_PROFILES=anima_tags,natural_language   # 默认 anima_tags（生产 UI 唯一默认档）
    PROBE_CASES="L3·NSFW多人"                     # 用例名前缀过滤
    PROBE_OUT=D:\\pytest-tmp\\i36                  # 输出目录（默认同）

⚠ 产出一律写仓外（仓内 `_*` 草稿会被 release_preflight 密钥扫描误报）。
⚠ 探针直连 LLM（读 backend/data/user_state.json），**不需要起后端**。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

# 绕开本机环境代理（沙箱代理会截走外部请求）。
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_k, None)

from app.services import agent_agency as ag  # noqa: E402
from app.services import image_prompt_extract as ipe  # noqa: E402
from app.services import image_prompt_profiles as ipp  # noqa: E402
from app.services import llm as _llm  # noqa: E402
from app.services import scene_illustration as si  # noqa: E402
from app.services import story_frames as sf  # noqa: E402

OUT_DIR = Path(os.environ.get("PROBE_OUT", r"D:\pytest-tmp\i36"))
OUT_DIR.mkdir(parents=True, exist_ok=True)

OUT_TXT = OUT_DIR / "_frames_probe.txt"
OUT_JSON = OUT_DIR / "_frames_probe.json"
OUT_CLIMAX = OUT_DIR / "_climax_baseline.json"
OUT_RAW = OUT_DIR / "_frames_probe_raw.json"

TEMP_CLIMAX = 0.4
TEMP_FRAMES = 0.3

DL = Path(os.path.expanduser("~")) / "Downloads"
PROFILES = [p.strip() for p in os.environ.get("PROBE_PROFILES", "anima_tags").split(",") if p.strip()]
CASES_FILTER = tuple(
    item.strip() for item in os.environ.get("PROBE_CASES", "").split(",") if item.strip()
)

# 用例与 probe_prompt_delivery 同表（同一批原文/锚点/主体），便于两线对照。
CASES = (
    ("L1·NSFW单人", "nsfw_test.txt", ["清夏", "泰宇老师"],
     "浑身无力的清夏靠在公告栏上。", "nsfw", ["清夏"]),
    ("L2·正常单人", "simple_test.txt", ["罗瑟琳", "卢卡", "塞蕾娜"],
     "塞蕾娜正微笑着。", "sfw", ["塞蕾娜"]),
    ("L3·NSFW多人", "nsfw_test_more Pepole.txt", ["明澜", "清夏", "泰宇老师"],
     "两侧同时献上小穴和后庭，争着要他插进来，屁股轻轻晃着。", "nsfw", ["清夏", "明澜"]),
    ("L2·单人锚点[63]", "simple_test.txt", ["罗瑟琳", "卢卡", "塞蕾娜"],
     "罗瑟琳没有停下脚步。她怕一停下，整个人就会垮掉。", "sfw", ["罗瑟琳"]),
    ("L3·单人锚点[386]", "nsfw_test_more Pepole.txt", ["明澜", "清夏", "泰宇老师"],
     "明澜转过身背对我，缓缓将丰满的臀部朝我挺了过来。", "nsfw", ["明澜"]),
    ("L3·单人锚点[379]", "nsfw_test_more Pepole.txt", ["明澜", "清夏", "泰宇老师"],
     "“接下来当然要轮到明澜了啊。”", "nsfw", ["明澜"]),
    ("L3·单人锚点[343]", "nsfw_test_more Pepole.txt", ["明澜", "清夏", "泰宇老师"],
     "高高翘着臀部，全身挣扎的清夏突然剧烈颤抖起来。", "nsfw", ["清夏"]),
    ("L3·代词双人[360]", "nsfw_test_more Pepole.txt", ["明澜", "清夏", "泰宇老师"],
     "但她双腿抖得厉害，根本站不住，直接软倒在明澜怀里。", "nsfw", ["明澜", "清夏"]),
)

RAW: dict[str, list[dict]] = {}
CALLS: list[dict] = []


def _dump_raw() -> None:
    OUT_RAW.write_text(json.dumps(RAW, ensure_ascii=False, indent=1), encoding="utf-8")


def load_settings() -> dict:
    p = ROOT / "backend" / "data" / "user_state.json"
    return json.loads(p.read_text(encoding="utf-8")).get("settings") or {}


def load_model_conf(st: dict) -> tuple[str, str, str]:
    mid = st.get("activeChatModelId")
    for m in st.get("chatModels") or []:
        if m.get("id") == mid:
            return (str(m.get("baseUrl") or ""), str(m.get("apiKey") or ""),
                    str(m.get("modelName") or m.get("model") or ""))
    raise SystemExit("找不到 activeChatModelId 对应的 chatModels 条目")


SETTINGS = load_settings()
BASE, KEY, MODEL = load_model_conf(SETTINGS)
PRESET_DIR = str(SETTINGS.get("presetDir") or "")
PRESET_NAME = str(SETTINGS.get("activePresetName") or "")


# 单次 LLM 调用的墙钟兜底（秒）。2026-09-21 实测：L3·NSFW多人 那一格提取调用挂死 7 分钟
# 不返回（服务端持续发 keep-alive ⇒ 流式 30s 读超时永不触发）⇒ 整批卡死。
# 超时即记 `<<TIMEOUT>>` 并继续，保证「一格挂死不吃掉整批」。
CALL_TIMEOUT = float(os.environ.get("PROBE_CALL_TIMEOUT", "240"))


def _call_with_timeout(fn, timeout: float) -> str:
    """线程级墙钟兜底：超时抛 TimeoutError（挂死线程留作 daemon，不阻塞后续用例）。"""
    import threading

    box: dict = {}

    def _run() -> None:
        try:
            box["out"] = fn()
        except Exception as exc:  # noqa: BLE001
            box["exc"] = exc

    th = threading.Thread(target=_run, daemon=True)
    th.start()
    th.join(timeout)
    if th.is_alive():
        raise TimeoutError(f"LLM 调用超过 {timeout}s 未返回（服务端挂死）")
    if "exc" in box:
        raise box["exc"]
    return box.get("out") or ""


def make_gen(scene_spec: dict, tag: str, temperature: float):
    """复刻生产 `_profile_llm_fallback._generate`（system_with_preset + 流式 chat）。"""

    def _gen(system: str, user: str) -> str:
        guarded = ipp.system_with_preset(
            system, scene_spec,
            preset_dir=PRESET_DIR, preset_name=PRESET_NAME, user_name="",
            head_only=False,
        )
        kind = "frame_extract" if system == ipp._FRAME_EXTRACT_SYSTEM else "compile"
        t0 = time.monotonic()
        rec: dict = {"tag": tag, "kind": kind, "system_chars": len(guarded),
                     "user_chars": len(user), "preset_applied": guarded != system}
        entry = {"kind": kind, "user": user, "system_chars": len(guarded)}
        try:
            out = _call_with_timeout(
                lambda: _llm.chat_messages_stream(
                    BASE, KEY, MODEL,
                    [{"role": "system", "content": guarded}, {"role": "user", "content": user}],
                    on_delta=lambda _s: None, temperature=temperature, retries=2, proxy="",
                    provider_profile="openai_compatible",
                    timeout_override=CALL_TIMEOUT,
                ),
                CALL_TIMEOUT + 30.0,
            )
            rec.update(ok=True, out_chars=len(out or ""), secs=round(time.monotonic() - t0, 1))
            entry["out"] = out or ""
            CALLS.append(rec)
            return out
        except Exception as exc:  # noqa: BLE001
            rec.update(ok=False, error_type=type(exc).__name__,
                       error_message=str(exc)[:400], secs=round(time.monotonic() - t0, 1))
            entry["out"] = f"<<EXC {type(exc).__name__}: {exc}>>"
            CALLS.append(rec)
            return ""
        finally:
            RAW.setdefault(tag, []).append(entry)
            _dump_raw()
    return _gen


def build_scene(text: str, anchor: str, known: list[str], rating: str, profile: str,
                frame_subjects: list[str] | None = None, *, viewpoint: str = "",
                narrator: str = "") -> dict:
    """复刻 `_wb_comfy_scene_spec` 的缺块分支（actors 走生产同款解析）。"""
    excerpt = si.illustration_scene_excerpt(text, anchor, subjects=known)
    actors = ag._resolve_illustration_request_actors(
        list(known), planned=[], user_text="", narrative=excerpt, present="",
        encounter=[], priority_text=anchor,
    )
    protected = si.protected_illustration_scene_excerpt(text, excerpt)
    scene = {
        "narrative": ipe.restore_jailbreak(excerpt),
        "protected_narrative": protected,
        "draft_prompt": ipe.build_fallback_content_tags(excerpt),
        "appearance": "",
        "wardrobe": "",
        "locale": "",
        "actors": list(actors),
        "known": list(known),
        "rating": rating,
        "aspect_ratio": si.infer_aspect_ratio(excerpt, actors),
        "profile": profile,
    }
    if frame_subjects:
        scene["frame_subjects"] = list(frame_subjects)
    if viewpoint or narrator:
        scene["viewpoint"] = viewpoint
        scene["narrator"] = narrator
    return scene


def _frame_payload(scene: dict, frames: sf.StoryFrames) -> dict:
    """复刻 generate_frame_prompts 的提取输入（事实字段子集），用于打印/诊断。"""
    fact_keys = ("appearance", "wardrobe", "location", "visual_facts", "subjects",
                 "secondary_characters", "actors", "rating")
    extract_scene = {k: scene[k] for k in fact_keys if k in scene}
    return ipp._scene_for_model({
        **extract_scene,
        "frames": {f: f"@({d})@" for f, d in
                   (("first", frames.opening), ("last", frames.closing)) if d},
    })


def main() -> None:
    def say(s: str = "") -> None:
        with OUT_TXT.open("a", encoding="utf-8") as fh:
            fh.write(s + "\n")

    OUT_TXT.write_text("", encoding="utf-8")
    say(f"model={MODEL}  preset={PRESET_NAME!r}")
    say(f"profiles={PROFILES}  cases={list(CASES_FILTER) or '（全量）'}  out={OUT_DIR}")
    say(f"temps: climax={TEMP_CLIMAX} frames={TEMP_FRAMES}")
    say("=" * 100)
    report: list[dict] = []
    climax_baseline: list[dict] = []

    for label, fname, known, anchor, rating, frame_subjects in CASES:
        if CASES_FILTER and not any(label.startswith(p) for p in CASES_FILTER):
            continue
        path = DL / fname
        if not path.exists():
            say(f"### {label}  文件缺失 {path}")
            continue
        text = path.read_text(encoding="utf-8")
        frames = sf.extract_story_frames(text)
        say(f"### {label}  原文 {len(text)} 字  锚点「{anchor}」  角色表 {known}  本帧主体 {frame_subjects}")
        say(f"  ① 双锚点  首={len(frames.opening)} 字  尾={len(frames.closing)} 字  "
            f"依据={frames.evidence}  首==尾? {frames.opening == frames.closing}")
        say(f"     首帧描述: {frames.opening[:160]!r}")
        say(f"     尾帧描述: {frames.closing[:160]!r}")
        vp = si.frame_viewpoint_detail(anchor, text, override="", known=list(known))

        for profile in PROFILES:
            scene = build_scene(text, anchor, known, rating, profile, frame_subjects,
                                viewpoint=vp["viewpoint"], narrator="甲")
            say(f"  -- profile={profile} --  在场 {len(scene['actors'])} 人 {scene['actors']}"
                f"  高潮片段 {len(scene['narrative'])} 字  视角 {vp['viewpoint']}")

            # ② 高潮基线（回归基线；温度 0.4）
            t0 = time.monotonic()
            cdiag: dict = {}
            cprompt = ipp.generate(profile, scene, make_gen(scene, f"{label}|{profile}|climax", TEMP_CLIMAX), cdiag)
            csecs = round(time.monotonic() - t0, 1)
            say(f"     [高潮基线] strategy={cdiag.get('strategy')}  {csecs}s  out={len(cprompt)} 字")
            climax_baseline.append({
                "label": label, "profile": profile,
                "strategy": str(cdiag.get("strategy") or ""),
                "out_chars": len(cprompt), "prompt": cprompt,
            })

            # ③ 首尾帧（温度 0.3，与生产端点同口径）
            fdiag: dict = {}
            payload = _frame_payload(scene, frames)
            say(f"     [首尾帧] 提取入参 frames 段: "
                f"{json.dumps(payload.get('frames') or {}, ensure_ascii=False)[:200]}")
            t1 = time.monotonic()
            results = ipp.generate_frame_prompts(
                profile, scene,
                {"first": frames.opening, "last": frames.closing},
                make_gen(scene, f"{label}|{profile}|frames", TEMP_FRAMES),
                fdiag,
            )
            fsecs = round(time.monotonic() - t1, 1)
            say(f"     [首尾帧] frame_extract_ok={fdiag.get('frame_extract_ok')}"
                f"  extract_missing={[k for k in fdiag if k.endswith('_extract_missing')]}"
                f"  {fsecs}s")
            # 画面主体是**确定性**量（模型侧逐格方差大，见 image_prompt_profiles 第 35 刀），
            # A/B 归因只认它：首帧/尾帧各自收敛到几个人。
            say(f"     画面主体  first={fdiag.get('first_subjects')}  last={fdiag.get('last_subjects')}")
            frame_recs = []
            for frame in ("first", "last"):
                item = results.get(frame)
                if not item:
                    say(f"       [{frame}] （无产出）")
                    continue
                errs = list(item.get("validation_errors") or [])
                say(f"       [{frame}] strategy={item.get('strategy')}  out={len(str(item.get('prompt') or ''))} 字")
                say(f"            {str(item.get('prompt') or '')[:400]}")
                if errs:
                    say(f"            errors: {json.dumps(errs, ensure_ascii=False)[:300]}")
                frame_recs.append({"frame": frame, "strategy": str(item.get("strategy") or ""),
                                   "out_chars": len(str(item.get("prompt") or "")),
                                   "validation_errors": errs,
                                   "prompt": str(item.get("prompt") or "")})
            report.append({
                "label": label, "profile": profile,
                "opening": frames.opening, "closing": frames.closing,
                "frames_equal": frames.opening == frames.closing,
                "evidence": frames.evidence,
                "frame_extract_ok": fdiag.get("frame_extract_ok"),
                "extract_missing": bool([k for k in fdiag if k.endswith("_extract_missing")]),
                "resolved_subjects": {"first": fdiag.get("first_subjects"),
                                      "last": fdiag.get("last_subjects")},
                "frames": frame_recs,
            })
        say("-" * 100)

    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    OUT_CLIMAX.write_text(json.dumps(climax_baseline, ensure_ascii=False, indent=1), encoding="utf-8")
    say(f"落盘: {OUT_TXT.name} / {OUT_JSON.name} / {OUT_CLIMAX.name} / {OUT_RAW.name}")
    say(f"LLM 调用 {len(CALLS)} 次，失败 {len([c for c in CALLS if not c.get('ok')])} 次")


if __name__ == "__main__":
    main()
