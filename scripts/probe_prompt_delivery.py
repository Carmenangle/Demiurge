"""真机测量「提示词传递」：三文段理想锚点 → 独立画像链（带防拦截预设）→ 成稿 or 词表兜底。

回答三问：
 ① 理想输入下，LLM 写的提示词能不能**传下去**（strategy=llm_*／kept_content／repaired），
    还是掉进词表兜底（strategy=fallback）？
 ② 掉兜底时卡在哪一层——「上游返回空」「模型拒答」「格式/事实校验不过」还是「调用抛异常」？
 ③ 兜底产出的「预设提示词」长什么样，与理想成稿差多少。

⚠ 与生产链路的等价性（v2 修正）：
   本探针复刻 `_wb_comfy_scene_spec` 的**缺块**分支（plan={}）＝ 85% 真机流量的那条路。
   **actors 必须由 `_resolve_illustration_request_actors` 从高潮片段推出**，
   不能拿 A/B 的角色表硬写——v1 就是这么错的（硬写 2 人 ⇒ 误以为生产也触发多人契约）。
   取材用 `illustration_scene_excerpt(..., subjects=known)`（词表档口径）。
   `protected_narrative` 用生产同款函数取。

⚠ 产出一律写仓外 %TEMP%（仓内 `_*` 草稿会被 release_preflight 密钥扫描误报，2026-09-19 实锤）。

⚠ 用例第 6 项＝**本帧主体名集合**（`scene_spec["frame_subjects"]`，生产侧由判据档 `subjects` 填充）。
   `build_scene` 无条件写入该键 ⇒ 同一份探针、同一份入参可以**前后各跑一次**做零方差 A/B：
   旧代码不认识这个键，自动就是「多人契约只看在场 actors」的旧行为（2026-09-19 优化项③）。
   前后臂用 `PROBE_OUT=<dir>/before|after` 分开落盘再比对。

跑法：
    backend/.venv/Scripts/python.exe scripts/probe_prompt_delivery.py
    PROBE_PROFILES=anima_tags,natural_language,niji_sections ...    # 生产 UI 可达的三档
    PROBE_PROFILES=krea2 ...                                        # 仅作对照（已下线模板）

⚠ 默认档＝`anima_tags`（生产 UI 唯一默认档）。**`krea2` 已于 2026-08-31 下线**
   （`frontend/src/lib/imagePromptProfiles.ts` 的 `PROMPT_PROFILE_OPTIONS` 只列三档，
   `normalizePromptProfile` 把旧预设里的 `"krea2"` 一律降级为 `anima_tags`）；
   后端 `PROFILE_IDS` / `_KREA_SYSTEM` 仍在库里但**无 UI 入口**。
   只有显式传 `PROBE_PROFILES=krea2` 才会跑到它 —— 别把它当成生产可达路径。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

# 绕开本机环境代理（沙箱代理会截走外部请求）；生产侧 ctx 走默认无代理路径。
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_k, None)

from app.services import agent_agency as ag  # noqa: E402
from app.services import image_prompt_extract as ipe  # noqa: E402
from app.services import image_prompt_profiles as ipp  # noqa: E402
from app.services import llm as _llm  # noqa: E402
from app.services import scene_illustration as si  # noqa: E402

OUT_DIR = Path(os.environ.get(
    "PROBE_OUT",
    str(Path(os.environ.get("TEMP") or os.environ.get("TMP") or ".") / "demiurge_pytest"),
))
OUT_DIR.mkdir(parents=True, exist_ok=True)

# 画面人称（与前端「多元数据插入 → 画面人称」三档同口径）：
#   ""＝自动（生产默认，走 `scene_illustration.frame_viewpoint` 的自动判定）
#   "first_person" / "third_person" ＝ 模拟用户**手选**（手选优先于自动）
VIEWPOINT_OVERRIDE = os.environ.get("PROBE_VIEWPOINT", "").strip()
# 叙事者占位：**中性占位**，不读也不打印 `user_state` 里的真实用户名（同 `_tmp/item25` 口径）。
NARRATOR = os.environ.get("PROBE_NARRATOR", "甲").strip()
# 用例过滤（2026-09-21 第 29 刀）：逗号分隔的**用例名前缀**；空＝全量。
# 单档定向复跑（改人称 / 只验某一格）时不必再另写脚本，例如
#   PROBE_CASES="L3·NSFW多人" PROBE_VIEWPOINT=first_person
CASES_FILTER = tuple(
    item.strip() for item in os.environ.get("PROBE_CASES", "").split(",") if item.strip()
)
OUT_TXT = OUT_DIR / "_prompt_delivery.txt"
OUT_JSON = OUT_DIR / "_prompt_delivery.json"

DL = Path(os.path.expanduser("~")) / "Downloads"
PROFILES = [p.strip() for p in os.environ.get("PROBE_PROFILES", "anima_tags").split(",") if p.strip()]
ALLOW_REPAIR = os.environ.get("PROBE_ALLOW_REPAIR", "1") == "1"
# 模型**原始回复**（含被本地校验判死那些）。只存字符数无法归因「是模型写坏还是归一化漏行」
# （2026-09-19 实锤：anima「必须正好两行」三条是级联派生，真凶在 `_normalize` 的 JSON 分支）。
RAW: dict[str, list[str]] = {}
RAW_OUT = OUT_DIR / "_prompt_delivery_raw.json"

CASES = (
    # label, 文件, 已绑定角色（角色表）, 锚点, rating, 本帧主体名集合
    #
    # 第 6 项＝**本帧主体名集合**，模拟判据档 `subjects`：它就是「画面里有几个人」的
    # 正向证据（用户口径：一律按画面主体数判，不按角色表人数、也不按在场人数）。
    # 空表 ⇒ 视为「无正向证据」＝旧行为（契约退回在场 actors），这是零回归前提。
    # ⚠ 这里是**实验者按原文判读**的替身，不调判据 LLM：单人帧给它 1 个名字，含
    # 代词回指的双人帧（如「她……软倒在世熙怀里」的「她」＝另一个绑定角色）照样给 2 个。
    ("L1·NSFW单人", "nsfw_test.txt", ["宥夏", "泰宇老师"],
     "浑身无力的宥夏靠在公告栏上。", "nsfw", ["宥夏"]),
    ("L2·正常单人", "simple_test.txt", ["罗瑟琳", "卢卡", "塞蕾娜"],
     "塞蕾娜正微笑着。", "sfw", ["塞蕾娜"]),
    ("L3·NSFW多人", "nsfw_test_more Pepole.txt", ["世熙", "宥夏", "泰宇老师"],
     "两侧同时献上小穴和后庭，争着要他插进来，屁股轻轻晃着。", "nsfw", ["宥夏", "世熙"]),
    # 优化项③ 的 A/B 用例：锚点句只点名 1 人、窗口却解析出 ≥2 人
    # ⇒ 契约当前由「窗口提及」驱动而被误触发。
    ("L2·单人锚点[63]", "simple_test.txt", ["罗瑟琳", "卢卡", "塞蕾娜"],
     "罗瑟琳没有停下脚步。她怕一停下，整个人就会垮掉。", "sfw", ["罗瑟琳"]),
    ("L3·单人锚点[386]", "nsfw_test_more Pepole.txt", ["世熙", "宥夏", "泰宇老师"],
     "世熙转过身背对我，缓缓将丰满的臀部朝我挺了过来。", "nsfw", ["世熙"]),
    ("L3·单人锚点[379]", "nsfw_test_more Pepole.txt", ["世熙", "宥夏", "泰宇老师"],
     "“接下来当然要轮到世熙了啊。”", "nsfw", ["世熙"]),
    ("L3·单人锚点[343]", "nsfw_test_more Pepole.txt", ["世熙", "宥夏", "泰宇老师"],
     "高高翘着臀部，全身挣扎的宥夏突然剧烈颤抖起来。", "nsfw", ["宥夏"]),
    # 回归保护：锚点句不点名、但代词回指第二个绑定角色的真双人帧 ⇒ 必须仍然触发契约
    ("L3·代词双人[360]", "nsfw_test_more Pepole.txt", ["世熙", "宥夏", "泰宇老师"],
     "但她双腿抖得厉害，根本站不住，直接软倒在世熙怀里。", "nsfw", ["世熙", "宥夏"]),
)


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

CALLS: list[dict] = []


def make_gen(scene_spec: dict, tag: str):
    """复刻 _profile_llm_fallback._generate（system_with_preset + 流式 chat）。"""

    def _gen(system: str, user: str) -> str:
        guarded = ipp.system_with_preset(
            system, scene_spec,
            preset_dir=PRESET_DIR, preset_name=PRESET_NAME, user_name="",
            head_only=False,
        )
        t0 = time.monotonic()
        rec: dict = {"tag": tag, "system_chars": len(guarded),
                     "user_chars": len(user), "preset_applied": guarded != system}
        try:
            out = _llm.chat_messages_stream(
                BASE, KEY, MODEL,
                [{"role": "system", "content": guarded}, {"role": "user", "content": user}],
                on_delta=lambda _s: None, temperature=0.4, retries=2, proxy="",
                provider_profile="openai_compatible",
            )
            rec.update(ok=True, out_chars=len(out or ""), secs=round(time.monotonic() - t0, 1))
            RAW.setdefault(tag, []).append(out or "")
            RAW_OUT.write_text(json.dumps(RAW, ensure_ascii=False, indent=1), encoding="utf-8")
            CALLS.append(rec)
            return out
        except Exception as exc:  # noqa: BLE001
            rec.update(ok=False, error_type=type(exc).__name__,
                       error_message=str(exc)[:400], secs=round(time.monotonic() - t0, 1))
            RAW.setdefault(tag, []).append(f"<<EXC {type(exc).__name__}: {exc}>>")
            RAW_OUT.write_text(json.dumps(RAW, ensure_ascii=False, indent=1), encoding="utf-8")
            CALLS.append(rec)
            raise
    return _gen


def build_scene(text: str, anchor: str, known: list[str], rating: str, profile: str,
                frame_subjects: list[str] | None = None, *, viewpoint: str = "",
                narrator: str = "") -> dict:
    """复刻 _wb_comfy_scene_spec 的缺块分支（actors 走生产同款解析）。

    `frame_subjects`＝生产侧 `scene_spec["frame_subjects"]`（判据档判定的本帧主体名集合）。
    **无条件写入**：改动前的旧代码不认识这个键 ⇒ 自动就是「契约只看 actors」的旧行为，
    于是同一份探针、同一份入参可以前后各跑一次做零方差 A/B（差别只在被测代码）。

    `viewpoint` / `narrator`（2026-09-21 第 29 刀）：生产侧 `_wb_comfy_scene_spec` 会写这两个键，
    而**旧探针不写** ⇒ 视角条款（`image_prompt_profiles._viewpoint_clause`）全程不注入、
    32 格**从未测到人称路径**（真机 i28 复跑即此状态）。现在按生产同口径写入；
    传空串 ⇒ 键仍写入但取值空 ⇒ 条款返回空串 ⇒ 逐字节等于旧行为（零回归前提）。
    """
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


def main() -> None:
    lines: list[str] = []

    def say(s: str = "") -> None:
        lines.append(s)
        with OUT_TXT.open("a", encoding="utf-8") as fh:
            fh.write(s + "\n")
            fh.flush()

    OUT_TXT.write_text("", encoding="utf-8")
    say(f"model={MODEL}  preset={PRESET_NAME!r}  dir={PRESET_DIR!r}")
    say(f"profiles={PROFILES}  allow_repair={ALLOW_REPAIR}")
    say(f"viewpoint={VIEWPOINT_OVERRIDE or '（自动，生产默认）'}  narrator={NARRATOR!r}"
        f"  cases={list(CASES_FILTER) or '（全量）'}  out={OUT_DIR}")
    say("=" * 96)
    report: list[dict] = []

    for label, fname, known, anchor, rating, frame_subjects in CASES:
        if CASES_FILTER and not any(label.startswith(prefix) for prefix in CASES_FILTER):
            continue
        path = DL / fname
        if not path.exists():
            say(f"### {label} 文件缺失 {path}")
            continue
        text = path.read_text(encoding="utf-8")
        off = si.illustration_anchor_offset(text, anchor)
        pct = -1 if off is None else round(off / max(1, len(text)) * 100)
        say(f"### {label}  锚点 {pct}%  「{anchor}」  角色表 {known}  本帧主体 {frame_subjects}")
        # 画面人称（生产同口径）：手选（PROBE_VIEWPOINT）优先于自动；空串＝自动。
        vp = si.frame_viewpoint_detail(
            anchor, text, override=VIEWPOINT_OVERRIDE, known=list(known),
        )
        say(f"     视角 {vp['viewpoint']}（依据 {vp['source']}／自动判定 {vp['auto']}"
            f"／手选冲突 {vp['conflict']}）")
        for profile in PROFILES:
            scene = build_scene(
                text, anchor, known, rating, profile, frame_subjects,
                viewpoint=vp["viewpoint"], narrator=NARRATOR,
            )
            n_actors = len(scene["actors"])
            say(f"  -- profile={profile} --")
            say(f"     高潮片段 {len(scene['narrative'])} 字 / 保护文 {len(scene['protected_narrative'])} 字"
                f" / 解析出在场 {n_actors} 人 {scene['actors']}"
                f" / 本帧主体 {scene.get('frame_subjects') or '（无正向证据）'}")
            say(f"     片段首 60: {scene['narrative'][:60]!r}")
            diag: dict = {}
            t0 = time.monotonic()
            prompt = ipp.generate(
                profile, scene, make_gen(scene, f"{label}|{profile}"), diag,
                allow_repair=ALLOW_REPAIR,
            )
            secs = round(time.monotonic() - t0, 1)
            strategy = str(diag.get("strategy") or "?")
            # 契约是否真的红过（实测信号，比按 n_actors 预测可靠）：三条错误串任一条出现即判红
            errs = [*(diag.get("first_errors") or []), *(diag.get("repair_errors") or [])]
            contract_red = [
                e for e in errs
                if "第一角色" in e or "第二角色" in e or "2girls" in e or "two girls" in e
            ]
            say(f"     strategy={strategy}  {secs}s  out={len(prompt)} 字"
                f"  契约红={len(contract_red)} 条 {contract_red}")
            say(f"     diag: first_raw={diag.get('first_raw_chars')} first_empty={diag.get('first_empty')}"
                f" first_refusal={diag.get('first_refusal')}"
                f" repair_raw={diag.get('repair_raw_chars')} repair_empty={diag.get('repair_empty')}"
                f" repair_refusal={diag.get('repair_refusal')}")
            for k in ("first_errors", "repair_errors", "warnings"):
                v = diag.get(k)
                if v:
                    say(f"     {k}: {json.dumps(v, ensure_ascii=False)[:300]}")
            ledger = diag.get("field_ledger") or {}
            missing = [f for f, it in ledger.items()
                       if isinstance(it, dict) and it.get("required") and not it.get("covered")]
            say(f"     缺字段: {missing}")
            for ri, raw in enumerate(RAW.get(f"{label}|{profile}") or [], 1):
                if raw.startswith("<<EXC"):
                    say(f"     raw#{ri}: {raw[:80]}")
                    continue
                norm = ipp._normalize(profile, raw, scene)
                say(f"     raw#{ri}: {len(raw)} 字 / 原始 {len(raw.splitlines())} 行"
                    f" ⇒ 归一化后 {len(norm.splitlines())} 行"
                    f" {'' if profile != 'anima_tags' or len(norm.splitlines()) == 2 else '← 非两行：三条格式错误必然连带红'}")
            say("     ---- 最终提示词 ----")
            for ln in str(prompt).splitlines():
                say(f"     | {ln}")
            fb = ipp.deterministic_fallback(profile, scene)
            say(f"     ---- 词表兜底（对照，{len(fb)} 字）----")
            for ln in str(fb).splitlines()[:6]:
                say(f"     # {ln}")
            say()
            report.append({
                "case": label, "profile": profile, "anchor_pct": pct, "anchor": anchor,
                "known": list(known), "actors": list(scene["actors"]),
                "frame_subjects": list(frame_subjects),
                "viewpoint": vp["viewpoint"], "viewpoint_source": vp["source"],
                "viewpoint_auto": vp["auto"], "viewpoint_conflict": vp["conflict"],
                "multi_contract": n_actors >= 2,
                "contract_red": contract_red,
                "excerpt_chars": len(scene["narrative"]),
                "excerpt_head": scene["narrative"][:200],
                "strategy": strategy, "secs": secs, "diag": diag,
                "prompt": prompt, "fallback": fb, "missing": missing,
            })
            OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        say("-" * 96)

    say("== LLM 调用汇总 ==")
    for c in CALLS:
        say(f"  {c['tag']:<26} ok={c.get('ok')} {c.get('secs')}s sys={c.get('system_chars')} "
            f"out={c.get('out_chars')} preset={c.get('preset_applied')} "
            f"err={c.get('error_type','')} {c.get('error_message','')[:100]}")
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n".join(lines[-50:]))


if __name__ == "__main__":
    main()
