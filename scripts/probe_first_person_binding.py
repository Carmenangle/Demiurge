"""第 42 刀真机探针：第一人称「叙事者不入画」＋ anima 多人衣着从属绑定。

背景（2026-09-22 用户实锤）
-------------------------
用户手选**第一人称**，高潮点生图却仍把主人公画进画面 ⇒ 本该穿在女主身上的衣服跑到主人公
身上、女主变裸。归因见 `docs/memory/2026-09-22.md`：

  * `narrator` 只从用户设置（人设名）取，`backend/data/user_state.json` 里 `userName` 为空
    ⇒ 第 37 刀四条数据层闸（`filter_anchor_subjects` / `_strip_narrator_from_model_view` /
    `_contract_subject_names` / 性别注入闸）**集体空转**；
  * 真机 `illustration.anchor_intent.subjects=["洛渊","沈映澜"]` ⇒ 叙事者占了主体名额 ⇒
    多人契约按两人成稿 ⇒ 成稿含 `adult man`，女主服装被绑到他身上。

本探针验证两件事（均**直连 LLM**，读 `backend/data/user_state.json`，不需要起后端）：

  A. **判据层（零 LLM）**：`filter_anchor_subjects` 在第一人称下把 ∉ 角色卡的名字剔出画面
     主体（第 42 刀·B）⇒ `_contract_subject_names` 只看到 1 个主体。
  B. **提示层（真机一次调用）**：第一人称、**无 narrator** 时，成稿是否仍写男性主体
     （验第 42 刀·C），以及双女主帧的衣着是否**分句绑到各自主体**（验第 42 刀·D）。

用法（必须从仓库根或 backend 目录调用）：
    backend/.venv/Scripts/python.exe scripts/probe_first_person_binding.py

⚠ 隐私红线：本文件与输出一律用中性占位名（甲/乙/丙），不得出现真名。
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

# 绕开本机环境代理（沙箱代理会截走外部请求）。
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_k, None)

from app.services import image_prompt_profiles as ipp
from app.services import llm as _llm
from app.services import scene_illustration as si

OUT_DIR = Path(os.environ.get("PROBE_OUT", r"D:\pytest-tmp\i42"))
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_TXT = OUT_DIR / "_first_person_probe.txt"
OUT_JSON = OUT_DIR / "_first_person_probe.json"

PROFILE = os.environ.get("PROBE_PROFILE", "anima_tags")
TEMP = 0.3
CALL_TIMEOUT = float(os.environ.get("PROBE_CALL_TIMEOUT", "240"))

# 中性占位名（隐私红线：绝不用真名）。
PROTAGONIST = "甲"          # 叙事者本名，**不是**已配置角色卡
CARD_A = "乙"              # 已配置角色卡（女主一）
CARD_B = "丙"              # 已配置角色卡（女主二）
KNOWN = [CARD_A, CARD_B]   # 角色卡名单（画面主体的身份真源）


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

LINES: list[str] = []
RECORDS: list[dict] = []


def say(line: str = "") -> None:
    LINES.append(line)
    OUT_TXT.write_text("\n".join(LINES) + "\n", encoding="utf-8")


def _call_with_timeout(fn, timeout: float) -> str:
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


def make_gen(scene: dict, tag: str):
    """复刻生产 `_profile_llm_fallback._generate`（system_with_preset + 流式 chat）。"""

    def _gen(system: str, user: str) -> str:
        guarded = ipp.system_with_preset(
            system, scene,
            preset_dir=PRESET_DIR, preset_name=PRESET_NAME, user_name="",
            head_only=False,
        )
        t0 = time.monotonic()
        out = _call_with_timeout(
            lambda: _llm.chat_messages_stream(
                BASE, KEY, MODEL,
                [{"role": "system", "content": guarded}, {"role": "user", "content": user}],
                on_delta=lambda _s: None, temperature=TEMP, retries=2, proxy="",
                provider_profile="openai_compatible",
                timeout_override=CALL_TIMEOUT,
            ),
            CALL_TIMEOUT + 30.0,
        )
        say(f"    · LLM 调用 [{tag}] system={len(guarded)}字 user={len(user)}字 "
            f"out={len(out)}字 {round(time.monotonic() - t0, 1)}s")
        return out

    return _gen


# ── 场景构造（形状对齐 `_anima_场景` / 真机 scene_spec）────────────────────────

def scene_first_person() -> dict:
    """第一人称、**无 narrator**（真机默认）、单主体。

    `frame_subjects` 取第 42 刀·B 之后的值（叙事者已被剔出画面主体）。
    """
    return {
        "narrative": f"我抓住{CARD_A}的手腕，把她按在墙上，低头吻住她的锁骨。",
        "protected_narrative": (
            f"@(我抓住{CARD_A}的手腕，把她按在墙上，低头吻住她的锁骨。)@"
        ),
        "draft_prompt": "",
        "appearance": (
            f"{PROTAGONIST}：高大成年男性，黑色长袍。\n"
            f"{CARD_A}：银白长发的成年女性，身材丰满，白色蕾丝内衣。"
        ),
        "wardrobe": f"{CARD_A}：白色蕾丝内衣",
        "locale": "卧房",
        "actors": [CARD_A],
        "subjects": [{"name": CARD_A, "description": "成年女性，银白长发"}],
        "frame_subjects": [CARD_A],
        "viewpoint": "first_person",
        "rating": "nsfw",
        "profile": PROFILE,
    }


def scene_two_girls() -> dict:
    """第三人称双女主：两人衣着**明显互异**，验文段是否分句绑定。"""
    return {
        "narrative": (
            f"{CARD_A}穿着被裁剪镂空的白色婚纱坐在床边，双手托着自己的胸部朝向镜头；"
            f"{CARD_B}穿着黑色兔女郎装背对镜头，弯腰趴在床上，双手掰开臀部，回头看向镜头。"
        ),
        "protected_narrative": (
            f"@({CARD_A}穿着被裁剪镂空的白色婚纱坐在床边，双手托着自己的胸部朝向镜头；"
            f"{CARD_B}穿着黑色兔女郎装背对镜头，弯腰趴在床上，双手掰开臀部，回头看向镜头。)@"
        ),
        "draft_prompt": "",
        "appearance": (
            f"{CARD_A}：金色长发的成年女性，肌肤白皙。\n"
            f"{CARD_B}：银白短发的成年女性，体态纤细。"
        ),
        "wardrobe": f"{CARD_A}：镂空白色婚纱\n{CARD_B}：黑色兔女郎装",
        "locale": "酒店套房",
        "actors": [CARD_A, CARD_B],
        "subjects": [
            {"name": CARD_A, "description": "金色长发的成年女性"},
            {"name": CARD_B, "description": "银白短发的成年女性"},
        ],
        "frame_subjects": [CARD_A, CARD_B],
        "viewpoint": "third_person",
        "rating": "nsfw",
        "profile": PROFILE,
    }


# ── 判据层（零 LLM）────────────────────────────────────────────────────────────

def report_judgement_layer() -> None:
    say("=" * 78)
    say("A · 判据层（零 LLM）：第一人称下非角色卡主体被剔出画面（第 42 刀·B）")
    say("=" * 78)
    # 锚点句**不点名任何角色** ⇒ 第③步「锚点句点名收敛」不介入，只照出第②步 B 的效果。
    anchor = "我把她按在墙上，低头吻住她的锁骨。"
    raw_intent = [PROTAGONIST, CARD_A]          # 真机形态：判据把叙事者算成主体
    for override in ("first_person", "third_person"):
        got = si.filter_anchor_subjects(
            raw_intent, anchor, KNOWN, override=override,
        )
        say(f"  filter_anchor_subjects({raw_intent}, override={override!r}) ⇒ {got}")

    sp = scene_first_person()
    tp = scene_two_girls()
    before = {
        **sp, "subjects": [], "frame_subjects": [PROTAGONIST, CARD_A],
    }
    say(f"  契约主体名单 · 修前形态(frame_subjects={[PROTAGONIST, CARD_A]}) "
        f"⇒ {ipp._contract_subject_names(before)}")
    say(f"  契约主体名单 · 修后形态(frame_subjects={[CARD_A]}) "
        f"⇒ {ipp._contract_subject_names(sp)}")
    say(f"  契约主体名单 · 第三人称双女主 ⇒ {ipp._contract_subject_names(tp)}")
    say()


# ── 提示层（真机 LLM）──────────────────────────────────────────────────────────

# ⚠ 用词边界匹配：`man` 不得误命中 `woman`。
_MASCULINE_RE = re.compile(
    r"\b(?:1boy|2boys|adult man|man|men|male|his|he)\b", re.IGNORECASE,
)


def _masculine_hits(text: str) -> list[str]:
    return sorted({m.group(0).lower() for m in _MASCULINE_RE.finditer(text)})


def run_cell(name: str, scene: dict) -> None:
    say("=" * 78)
    say(f"{name}")
    say("=" * 78)
    say(f"  scene: viewpoint={scene.get('viewpoint')!r} narrator={scene.get('narrator')!r} "
        f"frame_subjects={scene.get('frame_subjects')}")
    say(f"  契约主体名单 ⇒ {ipp._contract_subject_names(scene)}")
    diagnostics: dict = {}
    t0 = time.monotonic()
    try:
        prompt = ipp.generate(PROFILE, scene, make_gen(scene, name), diagnostics)
    except Exception as exc:  # noqa: BLE001
        say(f"  !! 调用失败：{type(exc).__name__}: {exc}")
        RECORDS.append({"cell": name, "error": f"{type(exc).__name__}: {exc}"})
        say()
        return
    lines = prompt.splitlines()
    say(f"  strategy={diagnostics.get('strategy')!r} "
        f"errors={diagnostics.get('first_errors')} repair={diagnostics.get('repair_errors')} "
        f"{round(time.monotonic() - t0, 1)}s")
    say("  --- 成稿 ---")
    for line in lines:
        say(f"  | {line}")
    hits = _masculine_hits(prompt)
    say(f"  男性主体标记命中 ⇒ {hits or '（无）'}")
    RECORDS.append({
        "cell": name, "scene_viewpoint": scene.get("viewpoint"),
        "contract_subjects": ipp._contract_subject_names(scene),
        "strategy": diagnostics.get("strategy"),
        "first_errors": diagnostics.get("first_errors"),
        "prompt": prompt, "masculine_hits": hits,
        "secs": round(time.monotonic() - t0, 1),
    })
    say()


def main() -> None:
    say(f"model={MODEL}  preset={PRESET_NAME!r}  profile={PROFILE}")
    say(f"known(角色卡)={KNOWN}  叙事者本名={PROTAGONIST!r}（刻意不在 known 里）")
    say()
    report_judgement_layer()
    if os.environ.get("PROBE_SKIP_LLM") == "1":
        say("[skip] PROBE_SKIP_LLM=1 ⇒ 只跑判据层（零 LLM），不调模型。")
        OUT_JSON.write_text(json.dumps(RECORDS, ensure_ascii=False, indent=1), encoding="utf-8")
        return
    run_cell("B1 · 第一人称·单主体（验 C：叙事者不入画）", scene_first_person())
    run_cell("B2 · 第三人称·双女主（验 D：衣着分句绑定）", scene_two_girls())
    OUT_JSON.write_text(json.dumps(RECORDS, ensure_ascii=False, indent=1), encoding="utf-8")
    say(f"[done] {OUT_TXT}  {OUT_JSON}")


if __name__ == "__main__":
    main()
