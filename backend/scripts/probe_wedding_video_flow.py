"""《堕落魔戒》婚礼堂（8 镜 / 66s）完整视频前置流程：场景 → 角色状态图 → 编辑后分镜图。

数据真源：`_tmp/review_still_luoluo.json`（11-11 定稿 8 镜，`message=bff28b0a…`，
`viewpoint=first_person`，在场「我 / 腓特烈白色婚纱 / 蛮啾」）。

三步严格按用户 2026-11-12 交代的顺序执行，产物统一落在
`userdata/pictures/堕落魔戒/SAVE02/video_flow/wedding/`：

1. `scenes/`    —— 16:9 空场景环境图（2 张：旧仓库礼堂 / 卧室），场景卡取自同源
   LLM 补全批次 `_tmp/scene_refs/luoluo_save02_llm/manifest.json`。
2. `characters/`—— 9:16 角色当前状态图（腓特烈白色婚纱状态），图生图锁身份。
   蛮啾没有定妆底图 ⇒ 显式写进 `missing_character_refs`，**不静默跳过**。
   🔴 预设的定妆底图是黑色礼服，与本轮白色婚纱状态冲突；图生图模板 cfg=1 /
   start_at_step=3 属「以底图为准」，提示词改不动服装 ⇒ 状态底图改用同批次
   11-12 已编译的 `_tmp/luoluo_916/shot_01.png`（白婚纱全身像），原定妆照
   仍写入 `portrait_base_image` 留证。
3. `shots/`     —— 8 张 9:16 分镜图，**每镜独立文生图**（`Krea2-高清文生图优化流`，
   576×1024 → 864×1536）。这是 2026-11-12 用户拍板的最终口径：**抛弃「场景底图 +
   角色底图合成参考板」那套方案**，回归正常分镜，不再追求跨镜一致。
   🔴 走不通的两条路（留档备查，不要再绕回去）：
   · `Krea2-平铺图生图` 只有**一个** `base_image` 槽，且 `cfg=1 / start_at_step=3`
     （denoise≈0.57）会继承输入几何 ⇒ 分栏参考板被原样画成分屏拼贴
     （留证 `shots_composite_DEPRECATED/shot_00_SPLITSCREEN_REJECTED.png`）；
   · `Krea2-AIO-参考编辑/图生图` 虽是**真双图**（`reference_image_1/2` 各接一个
     VAEEncode → 子图 `source_latent`/`source_latent_b`），但它是 in-context 画布，
     输出是「上角色 / 中花屏 / 下场景」三段堆叠；补 `target_latent`、给明确融合
     指令都压不住（留证 `_tmp/aio_dual/`）。
   ⇒ 跨镜一致性这条路在本地模型上不成立；确定性方案见 `_tmp/gpt_dual/`
   （GPT-image `images/edits` 多图字段，45 s 出单一画面，但需云端 key）。

两条内容边界（写进 manifest，不静默改口径）：
· 镜 6 / 7 原稿含露骨性描写 ⇒ 出图改用**中性化分镜提示词**（保留机位 / 婚纱 /
  项圈 / 床 / 窗帘光 / 蛮啾，去掉露骨身体与体液描写），标 `content_neutralized`。
· 角色状态图提示词只复用 11-11 镜 1 定稿的 tag 行（丢掉会串人的 manjiu 段），
  零 LLM、零云端，状态卡与状态哈希照常落盘保留证据链。

运行：`python backend/scripts/probe_wedding_video_flow.py --stage all [--force] [--dry-run]`
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

from app.services import (  # noqa: E402
    character_reference,
    character_state_card,
    comfyui_client,
    repo_meta,
    scene_reference,
    shot_prompt_composer,
    template_store,
    workflow_submission,
)

REPO_ID = "c5c60de2-18f1-4b88-b15a-5d4f3f5e652e"
MESSAGE_ID = "bff28b0a-f9b7-4168-b2ff-4c0274eeecb1"
REVIEW = ROOT / "_tmp" / "review_still_luoluo.json"
SCENE_MANIFEST = ROOT / "_tmp" / "scene_refs" / "luoluo_save02_llm" / "manifest.json"
CHAT = ROOT / "userdata" / "pictures" / "堕落魔戒" / "SAVE02" / "chat.json"
OUT = ROOT / "userdata" / "pictures" / "堕落魔戒" / "SAVE02" / "video_flow" / "wedding"

TEMPLATE_TEXT2IMG = "a546d311e3dd45cd9c50ab0aae82a1c2"      # Krea2 高清文生图（16:9）
TEMPLATE_IMG2IMG = "e8bbc2c970734c90a75bf8de1409dbd6"      # Krea2 平铺图生图（9:16）
# 分镜＝每镜独立文生图（用户 2026-11-12 定案：不再走参考底图合成）。
TEMPLATE_SHOT = TEMPLATE_TEXT2IMG
IMAGE_MODE_SHOT = "text2img_per_shot"
SHOT_W, SHOT_H = 576, 1024
STYLE_LORA = "Krea2_平涂线稿风.safetensors"
BOARD_W, BOARD_H = 864, 1536

# 🔴 2026-11-12 真机验收修正：预设里的腓特烈定妆底图是**黑色礼服**，
# 而 `Krea2-平铺图生图` 的一阶段是 steps=7/start_at_step=3/cfg=1（跳过高噪声步、
# 文本引导极弱），本质「以底图为准的轻量重绘」⇒ 用黑礼服底图跑白婚纱提示词
# 只换来一层白头纱，黑裙不变。这里改用**同批次 11-12 已编译的白婚纱全身像**
# 作状态底图：身份/服装都由底图锁定，提示词只负责「当前状态」，顺应模板而非硬顶。
# 原定妆照仍写进 manifest（portrait_base_image），口径不静默改。
STATE_BASE_IMAGE = ROOT / "_tmp" / "luoluo_916" / "shot_01.png"
STATE_BASE_REASON = (
    "预设定妆底图为黑色礼服，与当前白色婚纱状态冲突；图生图模板 cfg=1 且 "
    "start_at_step=3，提示词无法改写服装 ⇒ 改用同批次已编译的 9:16 白婚纱全身像作状态底图"
)
STATE_BASE_NAME = "腓特烈"

# 11-12 00:10 那批 9:16 分镜图的场景连续性锚点，逐镜复用，避免硬切时换场景。
WAREHOUSE_ANCHORS = {
    1: "Scene continuity: the side door frame she just entered through is clearly visible behind her, "
       "with the red carpet leading from it into the warehouse; the same wooden beams, white gauze, "
       "and two iron buckets of white flowers remain in view at the edges.",
    2: "Scene continuity: at the far end of the red carpet behind her, the side door she entered through "
       "is clearly visible, its frame draped with white gauze; the raised wooden platform with two iron "
       "buckets of white flowers sits to one side.",
    3: "Scene continuity: behind her, the red carpet runs back toward the side door; keep the side door "
       "frame and white gauze visible at the edge of the background, with the same wooden beams and the "
       "wooden platform with two iron buckets of white flowers.",
    4: "Scene continuity: the side door and the red carpet remain visible in the background; the same "
       "wooden beams, white gauze, and two iron buckets of white flowers stay in view.",
}
WARDROBE_ANCHOR = (
    "Wardrobe continuity: the same white wedding gown in every warehouse shot — fitted lace bodice, "
    "corseted waist, full hoop skirt with layered white tulle, long bridal veil, white high heels."
)
# 🔴 2026-11-12 真机第二次修正：原「上区场景 / 下区角色」的分栏参考板被模板
# 原样继承（denoise≈0.57 / cfg=1 ⇒ 输出几何跟输入走），shot_00 出图成了上下两幅
# 拼接的分屏画。现在输入必须是**一张连贯画面**，不再有分栏；提示词也不再说 board。
BOARD_NOTE = (
    "The input is ONE undivided reference image, not a board or a collage. Use it only as a "
    "reference for identity, wardrobe, lighting and location; produce one single continuous "
    "cinematic shot matching the scene description, no split screen, no panels, no horizontal divider."
)
# 状态卡的「当前状态」必须进提示词，否则状态图会退化成定妆图。
# 只取正文中可拍且中性的事实（服装 / 手持物 / 姿态 / 表情 / 光线），不取露骨身体描写。
STATE_ANCHOR = (
    "Current state: white wedding gown with a small collar hidden under the long bridal veil, "
    "a bouquet held in the left hand while the right hand lifts the skirt hem, dark-yellow eyes "
    "with a restrained gentle smile, head held up; she has just stepped in through the side door "
    "of the warehouse, soft daylight and dust in the air, white gauze fluttering in the sea breeze."
)
# 🔴 2026-11-12 用户定案「R-18 不过滤」：负面词里**不得**出现 NSFW/nude/exposed/explicit
# 这类压制词 —— 那等于在提示词层做内容审查，把原稿的露骨描写压回去。
# 负面词只留「换人 / 变形 / 串人 / 质量 / 文字 / 裁切」，全部是质量约束。
NEGATIVE_CHARACTER = (
    "different person, different face, different hairstyle, changed body type, "
    "extra characters, multiple people, other people, background figures, bystanders, "
    "manjiu, holding books, holding documents, notebook, black dress, dark dress, black gown, "
    "black clothing, low quality, blurry, text, watermark, "
    "cropped head, cropped feet, out of frame"
)
STATE_FRAMING = (
    "Full body vertical portrait: complete head-to-toe including shoes, small margin above the head "
    "and below the feet, front view facing the camera, head up, eyes open and looking at the camera, "
    "face and features clearly visible; no back view, no cropped legs, no closed eyes."
)
# 服装/单人锁定：底图已是白婚纱，这里压住「黑色礼服回潮」与原稿 manjiu 段落的串人。
STATE_LOCK = (
    "pure white wedding gown, white lace bodice, white tulle hoop skirt, long white bridal veil, "
    "white high heels, solo, 1girl, no other characters, no bystanders, plain uncluttered background"
)
NEGATIVE_SHOT = (
    "different person, different face, "
    "split screen, diptych, two stacked images, upper and lower panels, horizontal divider, "
    "divided layout, collage, comic panels, grid, borders, text, watermark, extra characters, "
    "black dress, dark dress, black gown, dark brown hair, black hair, dark hair, "
    "low quality, blurry, cropped head, cropped feet, out of frame"
)
# 8 宫格复核（2026-11-12）：shot_02 原稿 `dark-yellow long hair` 被模型画成深棕发、
# shot_07 中性化时漏写发色画成黑发 ⇒ 补身份锚点，只重出这两镜（其余 prompt 不变 ⇒ skip）。
HAIR_ANCHOR_TAGS = (
    "golden blonde long hair, bright golden hair, same hair color as the reference"
)
HAIR_FIX_SHOTS = (2,)

# 原稿 6/7 镜含露骨性描写；这里只保留可拍的机位、服装、道具、光线与在场角色。
NEUTRAL_PROMPTS = {
    6: (
        "masterpiece, best quality, score_7, score_9, very aesthetic, ultra detailed, fair skin, "
        "high contrast, anime coloring, 1girl, solo, tall, dark-yellow long hair, white wedding gown, "
        "wrinkled wedding dress, bridal veil slipped off one shoulder, pink collar with a small leash "
        "resting at the neck, lying on her side, pillow, bed, white silk stockings, one directional "
        "light from the curtain gap, calm expression, first person view, pov, bedroom,\n"
        "The tall adult woman lies on her side on the bed in a wrinkled white wedding gown, the long "
        "veil slipped off one shoulder. A pink collar with a leash rests around her neck. Her face is "
        "half-buried in the pillow, shoulders relaxed, one hand resting on the sheets. A narrow strip "
        "of daylight from the curtain gap falls across the bedsheet."
    ),
    7: (
        "masterpiece, best quality, score_7, score_9, very aesthetic, ultra detailed, high contrast, "
        "anime coloring, manjiu, small round bird, holding a broom under its wing, wedged in a doorway, "
        # 2026-11-12 8 宫格复核：中性化时漏写发色，shot_07 出成黑发 ⇒ 补回身份锚点。
        "1girl, dark-yellow long hair, golden blonde long hair, white wedding gown, wrinkled dress, "
        "lying on her side, bed, pink collar, dim bedroom, "
        "one directional light from the curtain gap, first person view, pov,\n"
        "A small round manjiu is wedged halfway through the door gap, a broom clamped under its wing, "
        "frozen at the threshold. Behind it, in the dim room, the tall adult woman lies on her side on "
        "the bed in a wrinkled white wedding gown, a pink collar at her neck. Her face is lifted from "
        "the pillow, calm, and a narrow strip of daylight from the curtain crosses the sheets."
    ),
}


def log(message: str) -> None:
    print(message, flush=True)


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def scene_for_shot(shot: dict) -> str:
    """按本镜画面事实选场景：床/枕头/卧室 ⇒ scene-1，其余 ⇒ scene-0。"""
    text = f"{shot.get('locale') or ''} {shot.get('image_prompt') or ''}".lower()
    bedroom_cues = ("bed", "pillow", "bedsheet", "bedroom", "房间内", "卧室")
    return "scene-1" if any(cue in text for cue in bedroom_cues) else "scene-0"


def build_reference_board(scene_path: Path, character_path: Path, dest: Path,
                          *, with_character: bool = True) -> str:
    """⛔ **DEPRECATED（2026-11-12 用户定案抛弃）** —— 不再被 `stage_shots` 调用。

    保留仅为留档与回归对照。它曾把「场景图 + 角色状态图」合成一张 9:16 基底，
    以绕开单 `base_image` 槽与 `center` 裁切；但真机证明这条路压不住跨镜一致：
    模板 `denoise≈0.57 / cfg=1` 会继承输入几何，同一张底图喂 5 次就得到 5 个不同的
    「仓库」，且分栏板会被原样画成分屏拼贴。

    现在的口径是**每镜独立文生图**（`TEMPLATE_SHOT`），不追跨镜一致。
    若将来要恢复，两条支路的行为仍被测试锁着：

    · `with_character=False`（环境镜）：16:9 场景图完整等比放竖幅中央，
      上下用同图强模糊放大版补边并羽化 ⇒ 竖幅空场景，左右不裁。
    · `with_character=True`（人物镜）：9:16 角色状态图作全幅基底，
      场景图只作低强度模糊氛围层混色 ⇒ 身份/白婚纱由基底锁，场景色光仍进链。
    """
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

    scene = Image.open(scene_path).convert("RGB")
    dest.parent.mkdir(parents=True, exist_ok=True)

    if not with_character:
        bg = ImageEnhance.Brightness(
            scene.resize((BOARD_W, BOARD_H), Image.Resampling.LANCZOS).filter(
                ImageFilter.GaussianBlur(36)
            )
        ).enhance(0.62)
        scale = min(BOARD_W / scene.width, (BOARD_H * 0.55) / scene.height)
        sw, sh = round(scene.width * scale), round(scene.height * scale)
        fit = scene.resize((sw, sh), Image.Resampling.LANCZOS)
        mask = Image.new("L", (sw, sh), 0)
        draw = ImageDraw.Draw(mask)
        fade = max(8, round(sh * 0.2))
        for y in range(sh):
            alpha = min(1.0, min(y, sh - 1 - y) / fade)
            draw.line((0, y, sw, y), fill=int(255 * alpha))
        bg.paste(fit, ((BOARD_W - sw) // 2, (BOARD_H - sh) // 2), mask)
        out = bg
    else:
        character = Image.open(character_path).convert("RGB")
        char = character.resize((BOARD_W, BOARD_H), Image.Resampling.LANCZOS)
        tint = scene.resize((BOARD_W, BOARD_H), Image.Resampling.LANCZOS).filter(
            ImageFilter.GaussianBlur(40)
        )
        out = Image.blend(char, tint, 0.14)

    out.save(dest, format="PNG")
    return str(dest)


def shot_has_character(shot: dict) -> bool:
    """环境镜（无人物）用纯场景基底，人物镜用角色状态图基底。

    判据取**实际出图提示词**的第一行 tag（镜 6/7 已被中性化替换）。
    """
    prompt, _ = shot_prompt(shot)
    tag_line = prompt.split("\n", 1)[0].lower()
    return "1girl" in tag_line or "1boy" in tag_line


def shot_prompt(shot: dict, *, neutralize: bool = False,
                appearance: list[str] | None = None) -> tuple[str, bool]:
    """返回 (提示词, 是否中性化)。非中性镜沿用 11-11 已编译定稿。

    🔴 **默认不中性化**（用户 2026-11-12 定案：R-18 不过滤）。镜 6/7 直接用原稿露骨
    提示词，和其余镜走同一条路 —— 不再有「我自己加的内容审查」。
    `neutralize=True` 才换成 `NEUTRAL_PROMPTS`（能力保留，供投稿/送审等需要收边的场合）。
    """
    index = int(shot["shot_index"])
    neutral = bool(neutralize) and index in NEUTRAL_PROMPTS
    prompt = str(NEUTRAL_PROMPTS[index] if neutral else shot.get("image_prompt") or "").strip()
    if not neutral and index in WAREHOUSE_ANCHORS:
        anchor = WAREHOUSE_ANCHORS[index]
        if anchor not in prompt:
            prompt += "\n" + anchor
        if WARDROBE_ANCHOR not in prompt:
            prompt += "\n" + WARDROBE_ANCHOR
    if index in HAIR_FIX_SHOTS and not appearance:
        # 兜底：拿不到 WD14 自动外貌锚点时，才用手写的发色锚点。
        # 加在 tag 行（权重最高的第一行），不是追加到尾部自然语言中。
        lines = prompt.split("\n", 1)
        lines[0] = lines[0].rstrip().rstrip(",") + ", " + HAIR_ANCHOR_TAGS
        prompt = "\n".join(lines)
    if appearance:
        # 🔴 2026-11-12：分镜里明明有 framing / camera_motion，之前一个都没进 image_prompt
        # ⇒ 模型自己构图（shot_07 画成臀腿特写）。这里把景别与机位编译进 tag 行。
        prompt = shot_prompt_composer.compose_shot_prompt(
            prompt,
            framing=str(shot.get("framing") or ""),
            camera_motion=str(shot.get("camera_motion") or ""),
            appearance=list(appearance),
            single_subject=shot_prompt_composer.is_single_human(shot.get('characters')),
        )
    # 纯文生图没有输入图 ⇒ 不再追加 BOARD_NOTE（那是图生图时代的「输入是一张未分割
    # 参考图」说明）。留着只会让模型以为存在参考图。
    return prompt, neutral


def state_prompt(review: dict) -> str:
    """角色状态图提示词。

    只取 11-11 镜 1 定稿的**第一行 tag 行**：第二段自然语言里有
    `several manjiu freeze mid-task`，真机已实证它会在状态图两侧串出两个拿抄本的小人。
    服装/单人锁定与当前状态锚点前置，避免长提示词尾部衰减。
    """
    source = str((review["shots"][1]).get("image_prompt") or "")
    tag_line = source.split("\n", 1)[0].strip()
    return "\n".join([
        tag_line,
        STATE_LOCK,
        STATE_ANCHOR,
        STATE_FRAMING,
    ])


def stage_scenes(*, url: str, force: bool, dry_run: bool) -> dict:
    """第一步：16:9 空场景环境图（2 张）。"""
    manifest = json.loads(SCENE_MANIFEST.read_text(encoding="utf-8"))
    cards = {
        key: item["card"]
        for key, item in (manifest.get("scenes") or {}).items()
        if isinstance(item, dict) and isinstance(item.get("card"), dict)
    }
    if not cards:
        raise SystemExit("LLM 场景卡 manifest 里没有 card")
    out_dir = OUT / "scenes"
    log(f"[1/3] 场景图 {len(cards)} 张 -> {out_dir}")
    if dry_run:
        return {"status": "dry-run", "scene_keys": sorted(cards)}
    result = scene_reference.generate_scene_references(
        cards, template_id=TEMPLATE_TEXT2IMG, url=url, out_dir=out_dir,
        force=force, width=1024, height=576,
    )
    for key, item in result["scenes"].items():
        log(f"  {key}: {item['status']} {item.get('image_path') or item.get('error') or ''}")
    _write_json(out_dir / "step_manifest.json", {
        "step": 1, "name": "scenes", "aspect_ratio": "16:9",
        "size": "1536x864", "template_id": TEMPLATE_TEXT2IMG,
        "card_source": str(SCENE_MANIFEST.relative_to(ROOT)),
        "scenes": result["scenes"],
    })
    return result


def stage_characters(*, url: str, force: bool, dry_run: bool) -> dict:
    """第二步：9:16 角色当前状态图（腓特烈白色婚纱）。"""
    turns = json.loads(CHAT.read_text(encoding="utf-8"))
    msg = next((m for m in turns if str(m.get("id") or "").startswith(MESSAGE_ID)), None)
    if msg is None:
        raise SystemExit(f"chat.json 里找不到消息 {MESSAGE_ID}")
    text = str(msg.get("text") or "")
    review = json.loads(REVIEW.read_text(encoding="utf-8"))
    meta = review.get("meta") or {}
    status = meta.get("status") or {}
    present = str(status.get("在场") or "")
    locale = str(status.get("地点") or "")
    base = repo_meta.output_dir_from_state()
    preset = ((repo_meta.load_user_state().get("settings") or {}).get("mediaInsert") or {}).get(REPO_ID) or {}
    loras = preset.get("characterLoras") or {}

    out_dir = OUT / "characters"
    out_dir.mkdir(parents=True, exist_ok=True)
    cards: list[dict] = []
    missing: list[dict] = []

    # 只在场、且预设里配了定妆底图的角色能出状态图；其余显式记缺失。
    portrait_base: dict[str, str] = {}
    named = [STATE_BASE_NAME, "蛮啾", "我"]
    for name in named:
        entry = loras.get(name) or {}
        portrait = str(entry.get("baseImage") or "").strip()
        portrait_base[name] = portrait
        base_image = portrait
        if name == STATE_BASE_NAME and STATE_BASE_IMAGE.is_file():
            base_image = str(STATE_BASE_IMAGE)
            log(f"  [{name}] 状态底图改用白婚纱全身像：{STATE_BASE_IMAGE.relative_to(ROOT)}")
        if not base_image or not Path(base_image).is_file():
            missing.append({"name": name, "reason": "无定妆底图 / 第一人称不入画", "base_image": portrait})
            continue
        card = character_state_card.extract_state_card(
            base=base, repo_id=REPO_ID, name=name, text=text, present=present,
            scene_key="wedding", base_image=base_image, locale=locale,
            others=list(loras.keys()),
        )
        card["state_hash"] = character_state_card.state_hash(card)
        cards.append(card)
        log(f"  [{name}] hash={card['state_hash']} 服装={card.get('wardrobe') or '（沿用底图）'}")
        log(f"    missing={card.get('missing')} inferred={card.get('inferred')}")

    # 状态图提示词：tag 行沿用 11-11 镜 1 定稿，丢掉会串人的 manjiu 段落；零 LLM / 零露骨。
    prompt = state_prompt(review)
    prompts = {str(c["name"]): prompt for c in cards}
    if dry_run:
        return {"status": "dry-run", "cards": cards, "missing": missing,
                "prompt_chars": len(prompt)}

    result = character_reference.generate_character_references(
        cards, prompts=prompts, template_id=TEMPLATE_IMG2IMG, url=url,
        out_dir=out_dir, force=force, negative=NEGATIVE_CHARACTER,
        style_loras=[{"name": STYLE_LORA, "weight": 0.8}],
    )
    for name, item in result["characters"].items():
        log(f"  {name}: {item['status']} {item.get('image_path') or item.get('error') or ''}")
    _write_json(out_dir / "cards.json", cards)
    _write_json(out_dir / "step_manifest.json", {
        "step": 2, "name": "characters", "aspect_ratio": "9:16",
        "size": "864x1536", "template_id": TEMPLATE_IMG2IMG,
        "message_id": MESSAGE_ID, "present": present,
        "prompt_source": "review_still_luoluo.json#shots[1].image_prompt 第一行 tag（丢掉 manjiu 段）+ 服装/单人锁定 + 当前状态锚点 + 全身正面取景条款",
        "prompt": prompt,
        "prompt_lock": STATE_LOCK,
        "prompt_framing": STATE_FRAMING,
        "prompt_state_anchor": STATE_ANCHOR,
        "negative": NEGATIVE_CHARACTER,
        "portrait_base_image": portrait_base.get(STATE_BASE_NAME, ""),
        "state_base_image": str(STATE_BASE_IMAGE),
        "base_image_override_reason": STATE_BASE_REASON if STATE_BASE_IMAGE.is_file() else "",
        "missing_character_refs": missing,
        "characters": result["characters"],
    })
    return result


def _submit_shot(*, template_id: str, prompt: str, url: str, dest: Path,
                 timeout_s: int, width: int = 0, height: int = 0,
                 negative: str = "", image_ref: str = "") -> dict:
    """单镜出图：提交 / 轮询 / 落盘。

    `image_ref` 为空 ⇒ 纯文生图（2026-11-12 用户定案：回到正常分镜，不再合成参考底图）；
    非空 ⇒ 图生图（保留能力，供 `--stage characters` 之外的场景复用）。
    """
    template = template_store.get_template(template_id)
    if template is None:
        return {"status": "failed", "error": f"模板不存在：{template_id}"}
    exposed = template.get("exposed") or []
    has_base = any(
        isinstance(e, dict) and str(e.get("binding") or "") == "base_image"
        for e in exposed
    )
    if image_ref and not has_base:
        return {"status": "failed", "error": "模板没有 base_image 槽"}
    values = scene_reference.build_template_values(
        template, prompt=prompt, negative=negative,
        width=width, height=height, style_loras=[{"name": STYLE_LORA, "weight": 0.8}],
        base_image=image_ref,
    )
    submitted = workflow_submission.submit_template(
        template_id, values, prompt=prompt, url=url,
        loras=[{"name": STYLE_LORA, "weight": 0.8}], lora_mode="single",
    )
    prompt_id = str(submitted.get("prompt_id") or "")
    if not prompt_id:
        return {"status": "failed", "error": "提交未返回 prompt_id"}
    deadline = time.monotonic() + max(30, timeout_s)
    while time.monotonic() < deadline:
        result = comfyui_client.fetch_result(url, prompt_id)
        state = str(result.get("status") or "")
        if state == "completed":
            images = result.get("images") or []
            if not images:
                return {"status": "failed", "prompt_id": prompt_id, "error": "完成但无图片"}
            image = images[0]
            dest.parent.mkdir(parents=True, exist_ok=True)
            comfyui_client.fetch_view_to_file(
                url, str(image.get("filename") or ""), str(dest),
                type=str(image.get("type") or "output"),
                subfolder=str(image.get("subfolder") or ""),
            )
            return {"status": "generated", "prompt_id": prompt_id, "image_path": str(dest)}
        if state in ("failed", "not_found"):
            return {"status": "failed", "prompt_id": prompt_id,
                    "error": str(result.get("error") or "ComfyUI 执行失败")[:300]}
        time.sleep(3)
    return {"status": "failed", "prompt_id": prompt_id, "error": f"超时 {timeout_s}s"}


def stage_shots(*, url: str, force: bool, dry_run: bool, limit: int, timeout_s: int,
                only: set[int] | None = None, neutralize: bool = False) -> dict:
    """第三步：8 张 9:16 分镜图，**每镜独立文生图**（正常分镜做法）。

    2026-11-12 用户定案：抛弃「场景底图 + 角色底图合成参考板」那套方案 ——
    本地 Krea2 图像模型局限太大（单槽 / cfg=1 / denoise≈0.57），跨镜一致性压不住，
    且 Krea2-AIO 双源是 in-context 画布（输出三段堆叠）。改为**按分镜逐镜出图**，
    不再追求画面前后一致；连续性只靠提示词里的场景/服装锚点做软约束。
    """
    review = json.loads(REVIEW.read_text(encoding="utf-8"))
    shots = review["shots"]
    out_dir = OUT / "shots"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 外貌锚点：用 WD14 对角色状态图反推（发色/瞳色/发型/体型），一次算好供全部镜复用。
    character_file = (OUT / "characters"
                      / character_reference.character_reference_filename(STATE_BASE_NAME))
    appearance = shot_prompt_composer.appearance_anchor(character_file)
    if appearance:
        log("  外貌锚点（WD14 自动）：" + ", ".join(appearance))
    else:
        log("  ⚠ 没拿到外貌锚点（WD14 未装/图缺失），退回手写发色锚点")

    records: dict[str, dict] = {}
    manifest_path = out_dir / "step_manifest.json"
    try:
        old = json.loads(manifest_path.read_text(encoding="utf-8"))
        if isinstance(old, dict) and isinstance(old.get("shots"), dict):
            records = old["shots"]
    except (OSError, ValueError):
        records = {}

    for shot in shots[: limit or len(shots)]:
        index = int(shot["shot_index"])
        key = f"shot_{index:02d}"
        dest = out_dir / f"{key}.png"
        if only and index not in only:
            continue
        prompt, neutral = shot_prompt(shot, neutralize=neutralize, appearance=appearance)
        scene_key = scene_for_shot(shot)
        prior = records.get(key, {})
        if (dest.is_file() and not force and prior.get("prompt") == prompt
                and prior.get("image_mode") == IMAGE_MODE_SHOT):
            log(f"  {key}: skipped（提示词未变）")
            continue
        log(f"  {key}: {scene_key} neutralized={neutral} chars={len(prompt)}")
        if dry_run:
            continue
        try:
            outcome = _submit_shot(
                template_id=TEMPLATE_SHOT, prompt=prompt, url=url, dest=dest,
                timeout_s=timeout_s, width=SHOT_W, height=SHOT_H,
            )
        except Exception as exc:  # noqa: BLE001 单镜失败不拖垮整批
            outcome = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"[:300]}
        records[key] = {
            "shot_index": index, "scene_key": scene_key,
            "image_mode": IMAGE_MODE_SHOT,
            "template_id": TEMPLATE_SHOT,
            "prompt": prompt, "content_neutralized": neutral,
            "neutralize_reason": "原稿含露骨性描写，改用中性化分镜提示词" if neutral else "",
            "image_path": outcome.get("image_path") or "",
            "status": outcome.get("status") or "failed",
            "error": outcome.get("error") or "",
            "prompt_id": outcome.get("prompt_id") or "",
        }
        log(f"    -> {records[key]['status']} {records[key]['image_path'] or records[key]['error']}")
        _write_json(manifest_path, {
            "step": 3, "name": "shots", "aspect_ratio": "9:16", "size": "864x1536",
            "image_mode": IMAGE_MODE_SHOT, "template_id": TEMPLATE_SHOT,
            "note": "每镜独立文生图；不追跨镜一致，连续性只靠提示词锚点",
            "shots": records,
        })
    return {"shots": records}


def merge_flow_steps(prev: dict, current: dict) -> dict:
    """三步可分开跑 ⇒ 总览累积合并，不让最后一步覆盖前两步。

    兼容两种历史格式：旧版把单步结果放在顶层 `scenes/characters/shots`，
    新版统一收进 `steps`。"""
    steps: dict = {}
    if isinstance(prev, dict):
        for key in ("scenes", "characters", "shots"):
            if isinstance(prev.get(key), dict):
                steps[key] = prev[key]
        if isinstance(prev.get("steps"), dict):
            steps.update(prev["steps"])
    merged = dict(current)
    for key in ("scenes", "characters", "shots"):
        if key in merged:
            steps[key] = merged.pop(key)
    merged["steps"] = steps
    merged["steps_completed"] = [
        key for key in ("scenes", "characters", "shots") if key in steps
    ]
    return merged


def main() -> int:
    parser = argparse.ArgumentParser(description="堕落魔戒·婚礼堂完整视频前置流程（三步）")
    parser.add_argument("--stage", default="all",
                        choices=["scenes", "characters", "shots", "all"])
    parser.add_argument("--url", default="")
    parser.add_argument("--force", action="store_true", help="忽略缓存强制重出")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划与提示词，不提交")
    parser.add_argument("--limit", type=int, default=0, help="第三步只做前 N 镜")
    parser.add_argument("--only", default="", help="只做指定镜，逗号分隔（如 1,4）；配合 --force 重出")
    parser.add_argument("--neutralize", action="store_true", help="镜 6/7 改用中性化提示词（默认关闭：R-18 不过滤）")
    parser.add_argument("--timeout", type=int, default=600, help="单镜等待秒数")
    args = parser.parse_args()

    settings = repo_meta.load_user_state().get("settings") or {}
    url = args.url or str(settings.get("comfyuiUrl") or "") or "http://127.0.0.1:8188"
    OUT.mkdir(parents=True, exist_ok=True)
    log(f"ComfyUI={url} stage={args.stage} force={args.force} dry-run={args.dry_run}")
    log(f"真源：{REVIEW.relative_to(ROOT)} | 输出：{OUT.relative_to(ROOT)}")

    summary: dict = {"url": url, "stage": args.stage, "dry_run": args.dry_run,
                     "review": str(REVIEW.relative_to(ROOT)),
                     "output": str(OUT.relative_to(ROOT))}
    # 三步可分开跑 ⇒ 总览必须累积，不能让最后一步覆盖掉前两步的记录。
    manifest_path = OUT / "flow_manifest.json"
    try:
        prev = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        prev = {}
    if args.stage in ("scenes", "all"):
        summary["scenes"] = stage_scenes(url=url, force=args.force, dry_run=args.dry_run)
    if args.stage in ("characters", "all"):
        summary["characters"] = stage_characters(url=url, force=args.force, dry_run=args.dry_run)
    if args.stage in ("shots", "all"):
        only = {int(part) for part in args.only.split(",") if part.strip().isdigit()}
        summary["shots"] = stage_shots(
            url=url, force=args.force, dry_run=args.dry_run,
            limit=args.limit, timeout_s=args.timeout, only=only or None,
            neutralize=args.neutralize,
        )
    if not args.dry_run:
        summary = merge_flow_steps(prev, summary)
        _write_json(manifest_path, summary)
        log(f"\n总览：{manifest_path}（已完成步骤：{', '.join(summary['steps_completed'])}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
