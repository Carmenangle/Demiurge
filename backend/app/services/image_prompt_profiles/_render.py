"""调度与生成管线：按 profile 把各模型域与共享底座装配成最终提示词——_normalize/_errors
分发、system/preset/quality/negative、确定性兜底分发、字段覆盖、失败重 patch、
generate/generate_result/分帧。2026-10-24 审计中期项拆域；函数体逐字节未改。"""
from __future__ import annotations
import json
import re
from collections.abc import Callable, Mapping
from app.services import image_prompt_extract
from app.services.image_prompt_profile_templates import (
    ANIMA_NEGATIVE_TAGS,
    PROFILE_IDS,
    _KREA_SYSTEM,
    _SYSTEMS,
)
from app.services.image_prompt_profiles._base import (
    _ANIMA_ACTION_RE,
    _CJK_RE,
    _REFUSAL_RE,
    _UNPAINTABLE_RE,
    _action_fact_conflicts,
    _anonymize_prompt_names,
    _character_labels,
    _character_names,
    _contract_subject_genders,
    _contract_subject_names,
    _diag_list,
    _drop_orphan_subject_counts,
    _drop_unsourced_device_mentions,
    _ensure_first_person_height_angle,
    _ensure_first_person_view_tag,
    _expected_present,
    _first_person_limb_entry_phrasing,
    _first_person_prose_meta_guard,
    _first_person_strip_camera_direction_tags,
    _is_action_fact,
    _is_dangling_fact,
    _json_object,
    _json_residue_hits,
    _looks_like_prose_sentence,
    _looks_like_scene_description,
    _looks_like_tag_run,
    _multi_actor_contract_errors,
    _resolve_subject_counts,
    _scene_action_tags,
    _scene_for_facts,
    _scene_for_model,
    _split_comma_phrase,
    _split_length_warnings,
    _strip_bystander_camera_tags,
    _strip_refusal_suffix,
    _strip_think,
    _strip_wrapping,
    _unsourced_contact_errors,
    _unsourced_contact_hits,
    _unsourced_device_hits,
    _unsourced_visual_device_errors,
    _unwrap_stray_parentheses,
    _viewpoint_clause,
    _visual_fact_errors,
    prompt_field_ledger,
)
from app.services.image_prompt_profiles.krea import (
    _complete_inline_krea_facts,
    _first_person_krea_guards,
    _inline_krea_errors,
    _krea_contract_errors,
    _krea_prompt,
    _krea_scene_fallback,
)
from app.services.image_prompt_profiles.anima import (
    _anima_clean_tag_head,
    _anima_content_description,
    _anima_contract_errors,
    _anima_keep_content,
    _anima_naturalize_prose,
    _anima_scene_fact_errors,
    _anima_scene_fallback,
    _anima_two_line_rescue,
    _drop_generic_anima_tags,
    _extra_field_prose,
    _labelled_fields_object,
    anima_quality_tags,
)
from app.services.image_prompt_profiles.niji import (
    _first_person_niji_guards,
    _niji_scene_fallback,
    _niji_sections_join,
)
from app.services.image_prompt_profiles.natural import (
    _natural_scene_fallback,
)


def negative_prompt(profile: str, _scene: Mapping[str, object] | None = None) -> str:
    return ANIMA_NEGATIVE_TAGS if profile == "anima_tags" else ""


def profile_defaults(profile: str, rating: str = "nsfw") -> dict[str, str]:
    if profile not in PROFILE_IDS:
        raise ValueError(f"未知提示词模式：{profile}")
    return {
        "quality_prompt": anima_quality_tags(rating) if profile == "anima_tags" else "",
        "negative_prompt": negative_prompt(profile),
    }


def system_with_preset(
    system: str,
    scene: Mapping[str, object],
    *,
    preset_dir: str = "",
    preset_name: str = "",
    user_name: str = "",
    task_label: str = "内部生图提示词任务",
    head_only: bool = True,
) -> str:
    """把当前偏置/防拦截预设用于独立提示词调用，不携带剧情历史。

    head_only=False：携带完整预设（含 <Order> 剧情机制段）——token 更贵，但防拦截
    强度与剧情主生成同级（2026-09-01 用户要求：生图提示词要像剧情一样稳定）。

    ⚠ 拼接边界（2026-09-19 优化项②取证）：预设段**整段前置**于任务框定与 Profile system，
    而预设正文自带「正文简体中文」类语言要求 ⇒ 对「输出语言」这个话题，上游指令在篇幅与
    位置上双压模板。**语言约束的压制放在各 Profile 的 system 文本里**（覆盖式声明，
    见 _NATURAL_SYSTEM / _NIJI_SYSTEM），本函数**不**在这里再插一层——同一条约束两处写
    就是两处漂移，且模板段离生成位置更近（近因）。此处只负责拼接与顺序保持。
    """
    if not (preset_dir.strip() and preset_name.strip()):
        return system
    try:
        from app.services import preset_store

        preset = preset_store.read_preset(preset_dir, preset_name)
        if not preset:
            return system
        model_scene = _scene_for_model(scene)
        actors = model_scene.get("actors")
        names = [str(name).strip() for name in actors] if isinstance(actors, list) else []
        source = json.dumps(model_scene, ensure_ascii=False, separators=(",", ":"))
        markers = {
            "char_name": "、".join(name for name in names if name),
            "char_description": str(model_scene.get("appearance") or ""),
            "char_personality": "",
            "scenario": str(model_scene.get("narrative") or ""),
            "dialogue_examples": "",
            "worldbook": str(model_scene.get("appearance") or ""),
            "worldbook_after": "",
            "persona": "",
            "user_name": user_name.strip(),
            "last_user_message": source,
            "last_char_message": "",
        }
        # head_only=False 时携带完整预设（含 <Order> 剧情机制段），防拦截强度与
        # 剧情主生成同级——2026-09-01 用户定案：生图提示词的稳定性优先于 ~8k token。
        guard = preset_store.assemble_system(preset, markers, head_only=head_only).strip()
        if not guard:
            return system
        return (
            guard
            + f"\n\n【{task_label}】以下任务独立于剧情正文，只输出目标协议要求的提示词，"
              "不得续写剧情、解释、道歉或附加拒答说明。\n"
            + system
        )
    except Exception:  # noqa: BLE001 预设读取失败时仍允许按 Profile 自身协议生成
        return system


def _normalize(
    profile: str, raw: str, scene: Mapping[str, object] | None = None,
) -> str:
    if profile == "krea2":
        # 第 64 刀：krea2 与 anima **同构**（首行 tags ＋ 散文）⇒ 首行也走第一人称收口
        # （剔旁观机位词 ＋ 补 POV 声明）；散文行逐字节不动。
        return _anonymize_prompt_names(
            _first_person_krea_guards(_krea_prompt(raw, scene), scene or {}), scene or {})
    if profile == "anima_tags":
        # 第 33 刀（2026-09-21）：`{}` 形式的 JSON 之外，再认一种**合法序列化变体** ——
        # 模型用 `visual_hook: … / primary_focus: … / supporting_elements: … / content: …`
        # 把四个规格字段写全（只是外层没有花括号）。不认它的话，那种 raw 会掉进下面的
        # 「非 JSON」分支、被整段吞进 tags 行且**一条格式红都不报** ⇒ `patched` 直接把
        # 半 JSON 残留交付出去。准入很窄（四字段全在且值非空），见 `_labelled_fields_object`。
        value = _json_object(raw) or _labelled_fields_object(raw)
        explicit_tags = ""
        explicit_prose = ""
        if value:
            source = str(value.get("content") or "")
        else:
            lines = [line.strip() for line in _strip_wrapping(raw).splitlines() if line.strip()]
            if len(lines) >= 2:
                body = " ".join(lines[1:])
                if re.search(
                    r"\b(?:masterpiece|best quality|score_[1-9]|anime coloring)\b",
                    lines[0], re.I,
                ):
                    # 已编译结果再次经过 normalize_inline 时，只剥掉质量前缀，保留同一行内容 tags。
                    explicit_tags = re.sub(
                        r"^[\s\S]*?\banime coloring\b\s*,?", "", lines[0],
                        count=1, flags=re.I,
                    ).strip(" ,")
                    if not explicit_tags:
                        legacy_tags, dot, legacy_prose = body.partition(".")
                        explicit_tags = legacy_tags.strip(" ,")
                        if dot and legacy_prose.strip():
                            body = legacy_prose.strip()
                else:
                    # 主模型偶尔把环境 tags 放首行、人物和动作写成第二行分号描述；
                    # 确定性复制具体描述到 tags 行，不能把 Agent 已提取的画面事实丢掉。
                    explicit_tags = ", ".join((lines[0].rstrip(" ,"), body))
                    explicit_tags = re.sub(
                        r"\b(?:(?:primary|second) adult character|the visual center)\s*:\s*",
                        "", explicit_tags, flags=re.I,
                    )
                    explicit_tags = re.sub(r"\s*[.;]\s*", ", ", explicit_tags)
                    explicit_tags = explicit_tags.encode("ascii", "ignore").decode("ascii")
                explicit_prose = re.sub(
                    r"\bprimary adult character\s*:\s*",
                    "The primary adult character is ", body, flags=re.I,
                )
                explicit_prose = re.sub(
                    r"\bsecond adult character\s*:\s*",
                    "The second adult character is ", explicit_prose, flags=re.I,
                )
                explicit_prose = re.sub(r"\s*;\s*", ". ", explicit_prose)
                explicit_prose = explicit_prose.encode("ascii", "ignore").decode("ascii").strip()
                if explicit_prose and explicit_prose[-1] not in ".!?”":
                    explicit_prose += "."
                source = ""
            else:
                source = " ".join(lines)
        content_lines = [
            line.strip() for line in _strip_wrapping(source).splitlines() if line.strip()
        ]
        content = " ".join(content_lines).strip()
        # 第一行是质量+内容 tags；第二行才是解释文段。
        if explicit_tags or explicit_prose:
            tag_head = explicit_tags
            prose = explicit_prose
        elif len(content_lines) >= 2:
            # 2026-09-19 修（真源：`docs/illustration-prompt-delivery-2026-09-19.md` §2.6）：
            # JSON 分支原先把 content 的换行**先压平**再 `partition(".")`。模型若用换行（而非句点）
            # 分隔 tags 与 prose（真机 L1/L3 实锤），整段会被塞进 tag_head、prose 落空 ⇒ 输出退化
            # 1 行 ⇒「必须正好两行」连带「第一行须含质量词与≥6 tags」「第二行须含英文画面描述」
            # 三连判死（模型两次都写了完整内容，第一稿是被本地归一化判死的）。
            # 改为与上方非 JSON 质量前缀分支同口径：首行 tags、其余并入 prose。
            # 单行 content 仍走下方首个句点切分（设计口径，行为逐字不变）。
            tag_head = content_lines[0]
            prose = " ".join(content_lines[1:])
        else:
            tag_head, dot, prose_tail = content.partition(".")
            prose = prose_tail.strip() if dot else ""
        if value and not prose.strip():
            # 2026-09-19 真机实锤（`%TEMP%\demiurge_pytest\after_normfix\_prompt_delivery_raw.json`）：
            # 模型把 tags 写进 `content`、把画面句写进**非规格字段** `content_note`
            # ⇒ 上面三条切分路径都拿不到 prose（`content` 内既无换行也无句点）⇒ 输出退化 1 行。
            # ⚠ 这条只在 prose 本已为空时触发（那个状态必然死稿）⇒ 单向放宽、零回归。
            prose = _extra_field_prose(value)
        if value and not prose.strip() and content.strip():
            # 第 33 刀（2026-09-21 真机 `D:\pytest-tmp\i33` 实锤，命中 2 格）：模型把 `content`
            # 写成**没有内部句点的形态**，上面三条切分路径全拿不到 prose ⇒ 输出退化成 **1 行**
            # ⇒「必须正好两行」三连判死 ⇒ 掉通用词表模板，把模型 1715／1826 字的真实画面全数丢掉。
            # 两种形态分别处理（都只在 `_errors` 判死前进来，**单向放宽**）：
            #   ① `tags ; 英文画面句`——真机 `L3·单人锚点[386]` 实锤：`;` 之前是逗号 tag 串、
            #      之后是完整英文句。按 `;` 切开，两行各得其所（tag 行 ＋ 散文行）。
            #   ② 纯逗号短语——真机 `L1·NSFW单人` 实锤：整段就是画面短语。此时它**本身就是画面
            #      描述**（非 JSON 分支对同形态也是「同一段文字既进 tags 又进 prose」，见
            #      `explicit_tags = ", ".join((lines[0].strip(" ,"), body))`）⇒ 直接当 prose。
            # 两条都**不新增任何模板引导句**（红线 28 ②），只在末尾补一个句点满足「英文自然语言
            # 画面描述」的形状判据。
            tag_part, sep, prose_part = content.partition(";")
            if sep and _looks_like_tag_run(tag_part) and _looks_like_prose_sentence(prose_part):
                tag_head = tag_part.strip(" ,")
                prose = prose_part.strip()
            elif _looks_like_scene_description(content):
                # 第 72 刀：旧行为把 `tag_head` 与 `prose` 设成**同一个串** ⇒ 交付两遍。
                # 先试机械切分（tag 形状的短段留首行、成句段给次行）；切不动才退回旧法。
                split_tags, split_prose = _split_comma_phrase(content)
                if split_prose:
                    tag_head, prose = split_tags, split_prose
                else:
                    prose = content.strip()
            if prose and prose[-1] not in ".!?":
                prose += "."
        tag_head = _anima_clean_tag_head(tag_head)
        tag_head = _drop_generic_anima_tags(tag_head)
        prose = _unwrap_stray_parentheses(_anima_naturalize_prose(prose))
        # 第 68 刀：第一人称下剔掉散文行的元语言镜头句
        # （`the camera is the narrator's eyes` 这类；零命中即逐字节返回）。
        prose = _first_person_prose_meta_guard(prose, scene or {})
        rating = str((scene or {}).get("rating") or "sfw")
        # 第 31 刀①②：**先拼质量行、再整行归一**——去重必须横跨「程序前置的质量行」与
        # 「模型自带的内容 tags」两个来源（真机首行这两块整段重复），人数词归一同样要看全行。
        head = _resolve_subject_counts(f"{anima_quality_tags(rating)}, {tag_head}")
        # 第 40 刀：族内归一之后再查**跨族**——girls 族已占满画面主体名额时，多出来的 boys 族
        # 计数词是孤儿（真机：第一人称下模型把**不入画的叙事者**写成了 `1boy`）。
        # 画面主体按 37 刀口径（已剔叙事者）⇒ 第三人称下叙事者占一个名额，`1boy` 自然保留。
        # 第 46 刀：有逐主体性别时口径**对称**（某族真值为 0 ⇒ 整族剔；纯男帧的 `1girl` 也剔）。
        head = _drop_orphan_subject_counts(
            head,
            len(_contract_subject_names(scene or {})),
            _contract_subject_genders(scene or {}),
        )
        # 第 60 刀（2026-09-28）：第一人称下剔掉**旁观/取景矛盾机位词**。
        # `_VIEWPOINT_FORBIDDEN_CAMERA` 原先是一张**空转的禁表**——只被抄进
        # `_viewpoint_clause` 的中文指令，**从未被任何代码消费** ⇒ 模型不听话时
        # `from behind` / `three-quarter back view` / `looking down at face` 直接提交
        # 给 ComfyUI（真机 slot `illo-req-…-53` 四次重生成里命中两次）。
        # ⚠ 零回归锁：非第一人称 / 无收束依据 / 行内无命中 ⇒ 都逐字节返回原串。
        # 第 73 刀（2026-09-29）：先削掉首行里**指向镜头的方位短语**
        # （上游「禁用机位」条款曾亲手给出 `her back arching toward the camera`
        #   范例，探针 [38] 首行因此出现 `back facing the camera`。）
        head = _first_person_strip_camera_direction_tags(head, scene or {})
        head = _strip_bystander_camera_tags(head, scene or {})
        # 第 65 刀（2026-09-28）：给「叙事者部位事实」补**从画面下缘进入**的呈现限定
        # （**追加**原串、不改写 ⇒ 账本 `expected` 判据不受影响、43／47 刀 12 处锁全绿）。
        # ⚠ 必须夹在「剔禁词」与「补 POV」之间（与 `_first_person_head_guards` 同序）。
        head = _first_person_limb_entry_phrasing(head, scene or {})
        # 2026-10-23（用户定案）：画面事实写明对方比叙事者高 ⇒ 机械补**轻微仰拍**，
        # 并剔掉 `from above`/`high angle` 这类会把身高差画反的高机位词。
        head = _ensure_first_person_height_angle(head, scene or {})
        # 第 61 刀（2026-09-28 · 用户定案「机械补」）：第一人称下**确保首行有 POV 正向声明**。
        # 该词形在本仓原先**没有任何注入点**（只出现在注释里）⇒ 全靠 profile LLM 自觉；
        # 真机四次重生成里它只在第 1 次自觉写了 ⇒ 后三次按默认第三人称构图。
        # ⚠ 幂等：已有 `first person view`/`pov`/`male viewer pov` ⇒ 逐字节返回原串。
        head = _ensure_first_person_view_tag(head, scene or {})
        # 第 67 刀（用户真机审查）：JSON 残片一律不得交付（见 `_json_residue_hits`）
        # ⇒ 返回空串，走既有的「空稿」兜底通道。
        if _json_residue_hits(f"{head},\n{prose}"):
            return ""
        return _anonymize_prompt_names(
            f"{head},\n{prose}", scene or {},
        )
    if profile == "natural_language":
        return _anonymize_prompt_names(
            _unwrap_stray_parentheses(_strip_wrapping(raw)), scene or {})
    if profile == "niji_sections":
        value = _json_object(raw)
        # 第 64 刀：只给 `subject` 段做第一人称收口（风格段／附加段／参数段一律不动）。
        sections = _first_person_niji_guards(
            [str(value.get(key) or "").strip()
             for key in ("subject", "style", "additions", "suffix")],
            scene or {},
        )
        return _anonymize_prompt_names(
            _unwrap_stray_parentheses(_niji_sections_join(sections)),
            scene or {},
        )
    raise ValueError(f"未知提示词模式：{profile}")


def normalize_inline(profile: str, raw: str, scene: Mapping[str, object] | None = None) -> str:
    """归一已有提示词或本地编译结果；不调用第二个模型。"""
    if profile not in PROFILE_IDS:
        profile = "anima_tags"  # 2026-08-31 默认协议切 Anima（krea2 模版不再默认）
    raw = _strip_refusal_suffix(raw)
    if profile == "niji_sections":
        text = _strip_wrapping(raw)
        prompt = _normalize(profile, text, scene) if text.lstrip().startswith("{") else text
    else:
        prompt = _normalize(profile, raw, scene)
    if profile == "krea2":
        if not _inline_krea_errors(prompt, scene or {}):
            return prompt
        return _complete_inline_krea_facts(prompt, scene or {})
    errors = _errors(profile, prompt, scene or {})
    if profile == "anima_tags" and errors and all(
        error.startswith("角色条目中的可视事实未落实：") for error in errors
    ):
        # 同义表述不应让整份 Agent 画面退化；字段账本会把精确稳定外貌补进两行。
        return prompt
    return "" if errors else prompt


def _errors(profile: str, prompt: str, scene: Mapping[str, object]) -> list[str]:
    errors: list[str] = []
    if profile != "krea2" and _REFUSAL_RE.search(prompt):
        errors.append("模型返回拒答")
    if profile == "krea2":
        errors.extend(_krea_contract_errors(prompt, scene, check_length=True))
    elif profile == "anima_tags":
        lines = prompt.splitlines()
        tags = lines[0] if len(lines) == 2 else ""
        prose = lines[1] if len(lines) == 2 else ""
        if len(lines) != 2:
            errors.append("必须正好两行")
        if not tags.isascii() or len([tag for tag in tags.split(",") if tag.strip()]) < 6:
            errors.append("第一行必须包含质量词与至少六个英文内容tags")
        if not re.search(r"^[A-Za-z][^.?!]{24,}[.?!](?:\s|$)", prose):
            errors.append("第二行必须包含英文自然语言画面描述")
        if _scene_action_tags(scene) and not _ANIMA_ACTION_RE.search(prompt):
            errors.append("剧情存在明确动作，提示词必须保留具体动作或姿态")
    elif profile == "natural_language":
        if len(prompt) < 20:
            errors.append("自然语言描述过短")
        if _CJK_RE.search(prompt):
            errors.append("自然语言最终提示词必须为纯英文")
        if prompt.startswith("```") or not re.search(r"[。.!?]", prompt):
            errors.append("必须是完整自然语言段落")
    elif profile == "niji_sections":
        lines = prompt.splitlines()
        if len(lines) != 4 or any(not line.strip() for line in lines):
            errors.append("必须包含主体、风格、附加提示词、后缀指令四段")
        elif not lines[3].startswith("--"):
            errors.append("第四段必须只包含后缀指令")
        if _CJK_RE.search(prompt):
            errors.append("Niji 最终提示词必须为纯英文")
    if profile != "krea2":
        errors.extend(_multi_actor_contract_errors(profile, prompt, scene))
    if profile != "krea2":
        errors.extend(_visual_fact_errors(prompt, scene))
    # 第 22 刀 ★1：与 `_visual_fact_errors` 同层、方向相反（管「加」不管「漏」）。
    # 放在 `if profile != "krea2"` 之外＝四档都过同一份闸（krea2 的 `_krea_contract_errors`
    # 只并了「漏」那一半，这里补齐「加」那一半）。
    errors.extend(_unsourced_visual_device_errors(prompt, scene))
    # 第 70 刀：与道具闸**同层同向** —— 身体部位与接触形态的编造同样不得放行。
    errors.extend(_unsourced_contact_errors(prompt, scene))
    return errors


def _body_errors(profile: str, prompt: str) -> list[str]:
    """成稿「本体」违例：只判**结构（行/段）与语言**，不含任何依赖 SceneSpec 的事实/契约缺口。

    用途是优化项⑤ 的补丁门槛（「稿子能不能用」而不是「稿子答不答得全」）。实现上把 scene
    换成空表：`_multi_actor_contract_errors` / `_visual_fact_errors` / `_scene_action_tags`
    的入口都先判来源为空，空 scene 下必然产出空表 ⇒ `_errors` 只剩与 scene 无关的格式判定。
    这样判定口径仍是**单一真源**，不必把正则抄第二份（抄一份就是一份漂移风险）。
    """
    errors, _warnings = _split_length_warnings(_errors(profile, prompt, {}))
    return errors


def _system(profile: str, scene: Mapping[str, object]) -> str:
    if profile == "krea2":
        if scene.get("character_lora"):
            return _KREA_SYSTEM + (
                "当前画面已绑定角色 LoRA，但 LoRA 只是视觉辅助，不能替代角色条目。仍须逐项把条目中的"
                "发色、发型、发饰、五官、体型和当前服装写成具体英文；不得输出角色姓名、模型名称、权重、"
                "触发词或任何‘由模型保持身份’的空泛句。"
            ) + _viewpoint_clause(scene)
        return _KREA_SYSTEM + _viewpoint_clause(scene)
    try:
        return _SYSTEMS[profile] + _viewpoint_clause(scene)
    except KeyError as exc:
        raise ValueError(f"未知提示词模式：{profile}") from exc


def deterministic_fallback(profile: str, scene: Mapping[str, object]) -> str:
    """仅用主生成已给出的场景事实组装提示词，不再调用文本模型。"""
    if profile == "krea2":
        prompt = _krea_scene_fallback(scene)
    elif profile == "anima_tags":
        prompt = _anima_scene_fallback(scene)
    elif profile == "natural_language":
        prompt = _natural_scene_fallback(scene)
    elif profile == "niji_sections":
        prompt = _niji_scene_fallback(scene)
    else:
        raise ValueError(f"未知提示词模式：{profile}")
    return _anonymize_prompt_names(prompt, scene)


def complete_field_coverage(
    profile: str, prompt: str, scene: Mapping[str, object],
) -> tuple[str, dict[str, dict[str, object]]]:
    """只补缺字段，不替换已经合格的高潮与艺术决策。"""
    ledger = prompt_field_ledger(prompt, scene)
    missing = [field for field, item in ledger.items() if item["required"] and not item["covered"]]
    factual_missing = [field for field in missing if field in {
        "appearance", "wardrobe", "action", "location", "visual_facts",
    }]
    if not factual_missing:
        return prompt, ledger
    factual_details: list[str] = []
    for field in (
        "appearance", "wardrobe", "action", "visual_facts", "location",
    ):
        if field in factual_missing:
            expected = ledger[field]["expected"]
            if isinstance(expected, list):
                factual_details.extend(str(value) for value in expected if str(value).strip())
    details: list[str] = list(factual_details)
    # 第 29 刀（2026-09-21 用户定案）：**同部位已写过动作的动作事实不追加**
    # （成因与词表见 `_action_fact_conflicts`）。只掐「动作」事实，形态默认值照旧补。
    action_expected = ledger.get("action", {}).get("expected")
    conflicted = {
        str(fact) for fact in (action_expected if isinstance(action_expected, list) else [])
        if str(fact).strip() and _action_fact_conflicts(prompt, str(fact))
    }
    if conflicted:
        factual_details = [fact for fact in factual_details if fact not in conflicted]
        details = [detail for detail in details if detail not in conflicted]
        ledger["__action_conflict__"] = {"skipped": sorted(conflicted)}
    defaults = {
        "camera": "a scene-appropriate medium shot with coherent perspective and readable depth of field",
        "composition": "a clear primary visual focus, foreground guidance, controlled negative space, and layered background depth",
        "lighting": "one coherent directional light source with consistent highlights and cast shadows",
        "material": "consistent hair, fabric and surface rendering with believable light response",
        "quality": "consistent anatomy, stable contours, clean edges, and restrained detail",
    }
    details.extend(defaults[field] for field in missing if field in defaults)
    details = list(dict.fromkeys(detail for detail in details if detail and _expected_present(prompt, detail) is False))
    if not details:
        # 第 33 刀（2026-09-21 用户口径）：niji **内容段末尾统一 `", "`**。这条不变式要在**交付口**
        # 成立——`_normalize` 补过一次，但下面补全分支会先 `rstrip` 再拼；无缺口时也得保证，
        # 否则「有时带、有时不带」比不补更难判读。
        if profile == "niji_sections":
            return _niji_sections_join(prompt.splitlines()), ledger
        return prompt, ledger
    ledger["__field_repair__"] = {
        "count": len(details),
        "fields": sorted({field for field in missing if field in defaults}),
    }
    # 2026-09-20 用户实锤（L3·natural 真机）：原实现把**内部账本标签**原样拼进交付文本——
    # `… focusing all texture on the stretched, eager flesh. Required visible facts: looking at;
    # withdrawing the fingers; consistent anatomy, …` ⇒ 交付稿里出现一句「机制自述」。
    # 用户口径：交付文本只写画面。⇒ 去掉标签；补全是否发生改由 `ledger["__field_repair__"]` 传递。
    # ⚠ 第 27 刀试过「散文通道排除动作片段」⇒ **7 例红灯**：krea2 / natural_language **没有 tags 行**，
    #   散文段是动作事实的**唯一**通道（`test_所有模板把连续高潮编译为复合动作…` 靠它）。
    #   ⇒ 事实一条不删，只**换语法外壳**；anima 的 `depicted with` 名词槽另修（见下）。
    # ⚠ 第 28 刀①（2026-09-20 真机实锤）：旧实现 `"; ".join(片段) + "."` 直接拼在模型稿后
    #   ⇒ `… stands still in the room. withdrawing the fingers; a clear primary visual focus, …`
    #   —— 句号后接**小写片段**。krea2 改为**只改标点**（句号→逗号），理由见 krea2 分支。
    # ⚠ 第二层实锤（2026-09-20 第 21 刀）：**只删标签不够**——`looking at; consistent anatomy,
    # stable contours, clean edges, and restrained detail.` 这类**悬空片段**还挂在文末，
    # 且 anima 文段被拼成病句 `The two adult characters are depicted with looking at.`。
    # 根因：账本里的**事实片段**（`looking at` / `withdrawing the fingers`）被当成句子用。
    # 定案：**事实片段不进任何散文通道**（它们本就来自叙事，模型没写就是没写）；
    # 只有**形态默认值**可以补，且必须补成语法合格的一句英文。
    # 事实**该补**（契约要求 `bedchamber` / `legs raised over the partner's shoulders` 这类
    # 事实必须出现在成稿里），但**悬空动词片段**不能补——`looking at` 单独挂着不成话。
    details = [detail for detail in details if not _is_dangling_fact(detail)]
    natural_sentence = (
        "The image also keeps " + "; ".join(details) + "." if details else ""
    )
    # anima 首行与 niji 分段是**逗号 tag 序列**：长句默认值进去会被下游当 tag。真机 L1/L2/L3 实锤
    # `..., dramatic tension., withdrawing what was sheathed, a scene-appropriate medium shot
    #  with coherent perspective and readable depth of field,`（L2·niji 同病：质量长句进了 subject 段）
    # ⇒ 逗号序列只收 tag 形状；长句默认值只配进自然语言正文。
    tag_defaults = {
        defaults["camera"]: "medium shot, coherent perspective, readable depth of field",
        defaults["composition"]: (
            "clear primary visual focus, foreground guidance, controlled negative space, "
            "layered background depth"
        ),
        defaults["lighting"]: "one directional light source, consistent highlights, cast shadows",
        defaults["material"]: (
            "consistent hair fabric and surface rendering, believable light response"
        ),
        defaults["quality"]: "consistent anatomy, stable contours, clean edges, restrained detail",
    }
    tag_details = [tag_defaults.get(detail, detail) for detail in details]
    # 解释文段只复述**真事实**，不复述通用模板长句（真机实锤：L3·anima 文段被接上
    # `… a clear primary visual focus, foreground guidance, … one coherent directional light source …`）。
    fact_set = set(factual_details)
    prose_facts = [detail for detail in details if detail in fact_set]
    if profile == "krea2":
        # 第 28 刀①（2026-09-20 真机实锤）：旧实现 `prompt.rstrip(" .") + ". " + "; ".join(details)
        # + "."` ⇒ `… stands still in the room. withdrawing the fingers; a clear primary visual
        # focus, …` —— 模型句已收尾，后面却接一个**小写动词片段**，读起来就是断句。
        # ⚠ 两条被否掉的修法（都试过/推演过，留档防回归）：
        #   ① 「片段首字母大写 / 套 `The visible action is …`」⇒ **破合同锁**：字段验收要求事实
        #      **逐字**出现（`a glowing spiral object …` / 单字地点 `bedchamber`），大写即失配，
        #      实测 3 例红灯（`test_所有模板逐条验收…` / `test_krea2词数偏短…` / `test_krea2补丁降级…`）；
        #   ② 「加引导句（`Also in the frame:` 之类）」⇒ 与用户口径相反：用户要的是**砍掉模板尾巴**
        #      （`In this instant, the concrete action is …` / `The image also keeps …` 已因同理被削）。
        # ⇒ 定案＝**只改标点、不改字**：句号改逗号，让账本片段成为模型句的**分词状语 / 同位语续列**。
        repaired = prompt.rstrip(" .") + ", " + "; ".join(details) + "."
    elif profile == "natural_language":
        if not natural_sentence:
            # 事实片段补齐对散文无意义 ⇒ 不算补全（trace 也不再报 field_repair）
            ledger.pop("__field_repair__", None)
            return prompt, ledger
        repaired = prompt.rstrip() + " " + natural_sentence
    elif profile == "anima_tags":
        lines = prompt.splitlines()
        if len(lines) != 2:
            return prompt, ledger
        # 可见事实属于 tags 行；解释文段是**模型自己的**自然语言，只允许追加真事实的复述句
        # （既有锁：`test_anima同义外貌不丢弃Agent动作成稿而由字段账本精确补齐` 要求事实也进第二行）。
        # 悬空 tag（`looking at`）接进文段会产出病句 ⇒ 剔除悬空片段（判据同散文通道）。
        prose_facts = [fact for fact in prose_facts if not _is_dangling_fact(fact)]
        # 2026-09-20 第 27 刀：文段只复述**非动作**真事实（动作片段归 tags 行）。全部是动作片段时
        # **不追加**任何尾巴——否则模型自己写好的文段会被接上 `The three adult characters are
        # depicted with withdrawing the fingers.` 这种病句（用户实锤）。
        prose_states = [fact for fact in prose_facts if not _is_action_fact(fact)]
        tail = (
            f" {_anima_content_description(scene, prose_states, include_actions=False)}"
            if prose_states else ""
        )
        repaired = (
            lines[0].rstrip(" ,") + ", " + ", ".join(tag_details) + ",\n"
            + lines[1].rstrip() + tail
        )
    elif profile == "niji_sections":
        lines = prompt.splitlines()
        if len(lines) != 4:
            return prompt, ledger
        lines[0] = lines[0].rstrip(" ,.;") + ", " + ", ".join(tag_details[:4])
        if len(tag_details) > 4:
            lines[2] = lines[2].rstrip(" ,.;") + ", " + ", ".join(tag_details[4:])
        # 上面两处 `rstrip` 会吃掉内容段末尾的 `", "` ⇒ 交付前统一按第 33 刀口径重拼一次
        # （`--` 参数段保持原样，见 `_niji_sections_join`）。
        repaired = _niji_sections_join(lines)
    else:
        return prompt, ledger
    repaired = _anonymize_prompt_names(repaired, scene)
    return repaired, prompt_field_ledger(repaired, scene)


def _patch_failed_prompt(
    profile: str, prompt: str, scene: Mapping[str, object],
    report: dict[str, object] | None = None,
) -> str:
    """补丁降级（优化项⑤）：宁可给「模型稿 + 本地补丁」，不给「词表通用模板」。

    旧行为：稿子只要还有一条错误就**整稿作废** ⇒ `deterministic_fallback` 词表模板。
    真机实测代价（2026-09-19 §2.14）：2453 字 raw、带着真实高潮事实的成稿被换成 777 字模板，
    连带词表误映射出 `removing the visible object` 事故串；补丁接手后该格 777 → 1993 字。

    这里把「作废」收窄成「缺口补丁」：稿子**本体**（结构与语言）可用时保留模型稿，
    缺的客观字段（外貌/着装/动作/地点/可视事实）交给 `complete_field_coverage` 补一句。
    与 A1（`_anima_keep_content`，2026-09-04）同一取向，只是门槛更贴事实——A1 要求
    content 自身**全项通过**，本函数只要求本体可用；A1 在前，故 A1 的行为逐字不变。

    **单向放宽**：只在调用方**本来就要掉兜底**的分支上接手 ⇒ 对已合格稿零影响。
    本体不合格（空稿/拒答/行数或语言不合规）时返回 ""，调用方照旧词表兜底。

    事实与契约的残红**不阻断**保留：词表模板本身也未必满足它们，而模型稿带着真实画面。

    ⚠ 唯一例外＝**凭空装置**（第 33 刀 · 第 14 项）：那不是「缺一项事实」，是**编造**。这类稿要么
    摘掉装置提及后交付，要么作废退词表——绝不原样放行（红线 22「不可放走编造镜面」）。
    """
    if not str(prompt or "").strip():
        return ""
    if _body_errors(profile, prompt):
        return ""
    patched, _ledger = complete_field_coverage(profile, prompt, scene)
    if not str(patched or "").strip() or _body_errors(profile, patched):
        return ""
    # 第 70 刀：编造的「隔着布料的器官接触」**不做机械摘除**（改句子会伤语法/语义，
    # 与道具闸「摘掉装置」不同）⇒ 直接作废，交回调用方走既有兜底通道。
    if _unsourced_contact_hits(patched, scene):
        return ""
    device_hits = _unsourced_device_hits(patched, scene)
    if device_hits:
        pruned = _drop_unsourced_device_mentions(profile, patched, scene)
        if (not pruned.strip() or pruned == patched
                or _body_errors(profile, pruned)
                or _unsourced_visual_device_errors(pruned, scene)):
            if report is not None:
                report["patched_device_dropped"] = [label for label, _ in device_hits]
            return ""
        if report is not None:
            report["patched_device_stripped"] = [label for label, _ in device_hits]
        return pruned
    return patched


def generate(
    profile: str,
    scene: Mapping[str, object],
    generate_text: Callable[[str, str], str],
    diagnostics: dict[str, object] | None = None,
    *,
    allow_repair: bool = True,
) -> str:
    """调用文本模型并验证目标协议；语义格式错误时携带原因重写一次。

    allow_repair=False（插画链同轮兜底用）：第一次不通过直接本地确定性兜底，
    不再花第二次 LLM 调用——正文已生成完，用户不该为提示词重写干等。"""
    if profile not in PROFILE_IDS:
        raise ValueError(f"未知提示词模式：{profile}")
    report = diagnostics if diagnostics is not None else {}
    report.clear()
    report.update({"strategy": "direct", "first_errors": [], "repair_errors": []})
    fact_scene = _scene_for_facts(scene)
    model_scene = _scene_for_model(scene)
    system = _system(profile, fact_scene)
    source = json.dumps(model_scene, ensure_ascii=False, separators=(",", ":"))
    first_raw = generate_text(system, source)
    raw = _strip_refusal_suffix(_strip_think(first_raw))
    # 插桩（2026-09-18）：把「上游返回空」「模型明确拒答」「内容被剥离成空」三种失败
    # 分开记账——此前 trace 只有 strategy=fallback，分不清 NSFW 拦截发生在哪一层。
    report["first_raw_chars"] = len(first_raw or "")
    report["first_empty"] = not raw.strip()
    report["first_refusal"] = bool(
        _REFUSAL_RE.search(image_prompt_extract.restore_jailbreak(first_raw or ""))
    )
    prompt = _normalize(profile, raw, fact_scene)
    errors, length_warnings = _split_length_warnings(_errors(profile, prompt, fact_scene))
    if length_warnings:
        report["warnings"] = length_warnings
    # 语言违规的**观测事实**（2026-09-19 优化项②）：本档「中文」违例是已知的采样事件，
    # 只记错误串无法区分「偶发一句中文残留」与「整篇中文叙事」——两者的修法完全不同。
    # 这里把根因形态压成一个比率，trace 里与 first_errors 并排即可判读（先观测后改法）。
    if errors and _CJK_RE.search(prompt):
        report["first_cjk_chars"] = len(_CJK_RE.findall(prompt))
        report["first_cjk_ratio"] = round(
            len(_CJK_RE.findall(prompt)) / max(1, len(prompt)), 3)
    # 画面不可绘制内容的**观测事实**（2026-09-20 用户口径：自然语言档必须「定格」，不得写
    # 气味 / 声音 / 台词 / 心理）。与 CJK 观测同源——**只记事实、不判死**：这类措辞偏了也
    # 比掉词表兜底好，把它升级成错误会让每一格都更容易掉兜底。留待真机 trace 判读效果。
    unpaintable = sorted({match.group(0).lower() for match in _UNPAINTABLE_RE.finditer(prompt)})
    if unpaintable:
        report["first_unpaintable_terms"] = unpaintable
    if not raw.strip() and _REFUSAL_RE.search(image_prompt_extract.restore_jailbreak(first_raw)):
        errors.insert(0, "模型返回拒答")
    if profile == "anima_tags":
        errors.extend(_anima_contract_errors(raw))
        errors.extend(_anima_scene_fact_errors(prompt, fact_scene))
    if not errors:
        completed, ledger = complete_field_coverage(profile, prompt, fact_scene)
        report["field_ledger"] = ledger
        return completed
    report["first_errors"] = list(dict.fromkeys(errors))
    if not allow_repair:
        # A1：即使不允许第二次调用，content 合格时也不该被中间字段措辞拖进词表。
        kept = _anima_keep_content(raw, fact_scene) if profile == "anima_tags" else ""
        if kept:
            report["strategy"] = "kept_content"
            completed, ledger = complete_field_coverage(profile, kept, fact_scene)
            report["field_ledger"] = ledger
            return completed
        # ⑤ 补丁降级：稿子本体可用就留住模型稿、只补缺口，不再整稿作废退词表。
        patched = _patch_failed_prompt(profile, prompt, fact_scene, report)
        if patched:
            report["strategy"] = "patched"
            completed, ledger = complete_field_coverage(profile, patched, fact_scene)
            report["field_ledger"] = ledger
            return completed
        # 第 58 刀：两行格式判死 ⇒ 本地归一（内容合格、只剩格式时不整稿作废）。
        rescued = _anima_two_line_rescue(raw, fact_scene)
        if rescued:
            report["strategy"] = "normalized_two_line"
            completed, ledger = complete_field_coverage(profile, rescued, fact_scene)
            report["field_ledger"] = ledger
            return completed
        report["strategy"] = "fallback"
        fallback = deterministic_fallback(profile, fact_scene)
        completed, ledger = complete_field_coverage(profile, fallback, fact_scene)
        report["field_ledger"] = ledger
        return completed
    repair = (
        f"{source}\n\n上次输出未通过：{'；'.join(errors)}。请严格按系统协议重写。"
        f"\n上次输出：{raw}"
    )
    second_raw = generate_text(system, repair)
    repaired_raw = _strip_refusal_suffix(_strip_think(second_raw))
    report["repair_raw_chars"] = len(second_raw or "")
    report["repair_empty"] = not repaired_raw.strip()
    report["repair_refusal"] = bool(
        _REFUSAL_RE.search(image_prompt_extract.restore_jailbreak(second_raw or ""))
    )
    prompt = _normalize(profile, repaired_raw, fact_scene)
    errors, _extra_warnings = _split_length_warnings(_errors(profile, prompt, fact_scene))
    prior_warnings = report.get("warnings")
    if _extra_warnings and isinstance(prior_warnings, list):
        report["warnings"] = list(dict.fromkeys([*prior_warnings, *_extra_warnings]))
    if (not repaired_raw.strip()
            and _REFUSAL_RE.search(image_prompt_extract.restore_jailbreak(second_raw))):
        errors.insert(0, "模型返回拒答")
    if profile == "anima_tags":
        errors.extend(_anima_contract_errors(repaired_raw))
        errors.extend(_anima_scene_fact_errors(prompt, fact_scene))
    if errors:
        report["repair_errors"] = list(dict.fromkeys(errors))
        # 第 58 刀：repair 后仍只剩两行格式问题 ⇒ 本地归一，不整稿作废退词表。
        rescued = _anima_two_line_rescue(repaired_raw, fact_scene)
        if rescued:
            report["strategy"] = "normalized_two_line"
            completed, ledger = complete_field_coverage(profile, rescued, fact_scene)
            report["field_ledger"] = ledger
            return completed
        if profile == "anima_tags" and set(errors) <= {
            "必须正好两行",
            "第一行必须包含质量词与至少六个英文内容tags",
            "第二行必须包含英文自然语言画面描述",
        }:
            # 第 59 刀：归一救不回且**只剩两行格式错** ⇒ 定向重试一次——把两行模板
            # 写死进纠错指令（比「请严格按系统协议重写」具体得多），输出再走本地归一；
            # 首次输出也纳入归一候选。兜底稿是无角色身份的降级，能救一张是一张。
            # ⚠ 只对格式错重试：焦点/事实/JSON 结构违例是模板修不了的，不烧这次调用。
            retry_prompt = (
                f"{source}\n\n上次输出未通过：{'；'.join(errors)}。"
                "重写必须**严格两行**：第一行是英文逗号分隔的内容 tags（至少 6 个）；"
                "第二行是一句英文画面描述（字母开头、至少 25 个字符、以句号/问号/感叹号收尾）。"
                f"\n上次输出：{repaired_raw or raw}"
            )
            third_raw = _strip_refusal_suffix(_strip_think(generate_text(system, retry_prompt)))
            report["retry_raw_chars"] = len(third_raw or "")
            for candidate in (third_raw, raw):
                third = _anima_two_line_rescue(candidate, fact_scene)
                if third:
                    report["strategy"] = "normalized_two_line+retry"
                    completed, ledger = complete_field_coverage(profile, third, fact_scene)
                    report["field_ledger"] = ledger
                    return completed
        report["strategy"] = "fallback"
        # Krea2 的长度只用于引导纠错；英文、段落、分级和禁词仍是硬合同。
        if profile == "krea2" and not _inline_krea_errors(prompt, fact_scene):
            completed, ledger = complete_field_coverage(profile, prompt, fact_scene)
            report["field_ledger"] = ledger
            return completed
        if profile == "anima_tags":
            # A1：重写后仍只余中间契约字段违例 → 保留合格 content（真实高潮动作），
            # 不再整稿作废退词表通用动作（2026-09-04 removing the visible object 事故）。
            kept = _anima_keep_content(repaired_raw, fact_scene)
            if kept:
                report["strategy"] = "kept_content"
                completed, ledger = complete_field_coverage(profile, kept, fact_scene)
                report["field_ledger"] = ledger
                return completed
        # ⑤ 补丁降级：重写后仍红，但稿子本体可用 ⇒ 留住模型稿、只补缺口，
        # 不再整稿作废退词表模板（A1 之上再放宽一层：不再要求 content 全项通过）。
        patched = _patch_failed_prompt(profile, prompt, fact_scene, report)
        if patched:
            report["strategy"] = "patched"
            completed, ledger = complete_field_coverage(profile, patched, fact_scene)
            report["field_ledger"] = ledger
            return completed
        fallback = deterministic_fallback(profile, fact_scene)
        completed, ledger = complete_field_coverage(profile, fallback, fact_scene)
        report["field_ledger"] = ledger
        return completed
    report["strategy"] = "repaired"
    completed, ledger = complete_field_coverage(profile, prompt, fact_scene)
    report["field_ledger"] = ledger
    return completed


def generate_result(
    profile: str,
    scene: Mapping[str, object],
    generate_text: Callable[[str, str], str],
) -> dict[str, object]:
    diagnostics: dict[str, object] = {}
    prompt = generate(profile, scene, generate_text, diagnostics)
    first_errors = diagnostics.get("first_errors")
    repair_errors = diagnostics.get("repair_errors")
    first_items = first_errors if isinstance(first_errors, list) else []
    repair_items = repair_errors if isinstance(repair_errors, list) else []
    result: dict[str, object] = {
        "prompt": prompt,
        "negative_prompt": negative_prompt(profile, scene),
        "strategy": str(diagnostics.get("strategy") or "direct"),
        "validation_errors": list(dict.fromkeys([
            *[str(item) for item in first_items],
            *[str(item) for item in repair_items],
        ])),
        "field_ledger": diagnostics.get("field_ledger") or {},
    }
    return result


_FRAME_EXTRACT_SYSTEM = (
    "你是首尾帧画面事实提取器。输入包含两段画面描述（first=楼层开头画面、last=楼层结尾画面）"
    "与角色/场景结构化事实。分别针对每个时点提取英文结构化画面事实，只输出 JSON 对象："
    '{"first": {"action": "...", "visual_facts": ["...", "..."], "subjects": ["..."]}, '
    '"last": {"action": "...", "visual_facts": ["...", "..."], "subjects": ["..."]}}。'
    "action：该时点人物的身体动作与姿态，一条具体可见的英文短语，不含心理与叙事总结。"
    "visual_facts：该时点可见的视觉事实列表（3-6 条英文短语：肢体位置、衣物状态、道具、"
    "环境互动），必须与该时点的画面描述一致，不得引入画面描述之外的事件。"
    "subjects：该时点**画面里实际可见的人物**列表，按出现顺序，逐条抄输入中给出的指代"
    "（`The primary adult character` 等）或该时点描述里明确出现的人名；"
    "只被台词提及、并未出现在这个画面里的人**不要写**；判断不了就留空数组。"
)


def _parse_frame_extract(raw: str) -> dict[str, dict[str, object]] | None:
    """解析提取输出；剥 think 与代码围栏，字段非法视为整体失败。

    提取模型带 CoT 时 JSON 常混在 think 之后，不剥离则解析必败 → 两次重试全废 →
    帧编译失去提取事实（2026-08-29 新仓库 trace：尾帧降级为中文叙事原文）。
    """
    import json as _json

    text = _strip_think(raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`").lstrip("json").strip()
    try:
        value = _json.loads(text)
    except ValueError:
        return None
    if not isinstance(value, dict):
        return None
    parsed: dict[str, dict[str, object]] = {}
    for frame in ("first", "last"):
        item = value.get(frame)
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "").strip()
        facts = item.get("visual_facts")
        visual_facts = [
            str(entry).strip()
            for entry in (facts if isinstance(facts, list) else [])
            if str(entry).strip()
        ]
        raw_subjects = item.get("subjects")
        subjects = [
            str(entry).strip()
            for entry in (raw_subjects if isinstance(raw_subjects, list) else [])
            if str(entry).strip()
        ]
        if action or visual_facts:
            parsed[frame] = {
                "action": action,
                "visual_facts": visual_facts,
                "subjects": subjects,
            }
    return parsed or None


# 第 34 刀：帧场景**不得继承高潮锚点的画面要素**（用户规格 2026-09-21「首尾帧无需高潮点
# 判断流程，只提取剧情前面部分 / 尾部部分」）。`scene` 是随请求恒定下发的高潮锚点
# `scene_spec`（`agent_agency._wb_frames_and_video`），其中
# narrative / draft_prompt / subjects / frame_subjects / composition / camera /
# action / visual_facts / action_sequence 全按**高潮锚点**算出来 —— 旧实现
# `frame_scene = dict(scene)` 只覆盖 action/visual_facts/protected_narrative，
# 于是首/尾帧被画成高潮画面。真机实锤（探针 i36）：L3 首帧描述只有两人对白，
# 产物却同时列出 primary/second/third adult character（三人）。
#
# ⚠ `frame_subjects` 在**剔除之列**（高潮那份不能用），但第 35 刀把它**按本帧重建**填回来
# ——见 `_build_frame_scene`。剔了不填会让画面主体退回 `actors`（在场全集），
# 契约便按在场人数成稿（真机 i38 实锤：单人的尾帧被硬套多人契约、好成稿被判死）。
_FRAME_SCENE_EXCLUDE = frozenset({
    "narrative", "protected_narrative", "draft_prompt",
    "subjects", "frame_subjects", "secondary_characters",
    "composition", "camera", "action", "visual_facts", "action_sequence", "motion",
})


def _resolve_frame_subjects(
    scene: Mapping[str, object], tokens: object, label_map: Mapping[str, str] | None = None,
) -> list[str]:
    """把时点提取器报出的「本帧可见人物」映射回本名（第 35 刀）。

    为什么要映射：提取器看到的是**匿名后的指代**（`the primary adult character`，见
    `_anonymize_scene_characters`），而本地多人契约（`_actor_visual_details` 的 facts
    归属）按**本名**建键 ⇒ 不映射回去，逐人描述会退化成 `is present in frame` 占位符。

    ⚠ **宁缺勿猜**：一条都对不上（提取器吐中文散文式主体）⇒ 返回空表 ⇒ 调用方**不写**
    `frame_subjects` ⇒ 退回 `actors`，逐字节等于第 34 刀行为（零回归）。
    """
    if not isinstance(tokens, list):
        return []
    base = [str(name).strip() for name in _character_names(scene) if str(name).strip()]
    mapping = dict(label_map) if label_map else dict(
        zip(_character_labels(len(base)), base, strict=False)
    )
    lowered = {str(label).strip().lower(): str(name).strip() for label, name in mapping.items()}
    known_values = scene.get("known")
    candidates = [*base]
    if isinstance(known_values, list):
        candidates.extend(str(name).strip() for name in known_values if str(name).strip())
    candidates = list(dict.fromkeys(candidates))
    resolved: list[str] = []
    for token in tokens:
        text = str(token).strip()
        if not text:
            continue
        name = lowered.get(text.lower()) or next(
            (item for item in candidates if item == text or item.lower() == text.lower()), ""
        )
        if name and name not in resolved:
            resolved.append(name)
    return resolved


def _build_frame_scene(
    scene: Mapping[str, object], desc: str, facts: Mapping[str, object] | None,
    label_map: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """按帧时点重建帧场景。

    事实底座沿用（外貌 / 服装 / 地点 / 在场 / 评级 / 画幅 / 视角 / LoRA），
    画面要素一律取自**本帧自己的描述**：`narrative`/`protected_narrative` 换成帧描述、
    `draft_prompt` 由帧描述重建（与高潮场景从自己那段正文重建同口径）、
    `action`/`visual_facts` 由时点提取器回填（缺失则留空，交 `generate()` 从帧描述推）。

    第 35 刀：`frame_subjects`（**画面主体**）同样只认本帧——取提取器报出的本帧可见人物
    （`_resolve_frame_subjects` 映射回本名）。提取器没报 / 对不上 ⇒ **不写**该键 ⇒
    `_contract_subject_names` 退回 `actors`（零回归）。⚠ 只剔高潮锚点那一份，绝不接管
    `actors`：`actors` 是事实底座（在场全集），第 32 刀「缩输出、不缩归因」的红线。
    """
    frame_scene: dict[str, object] = {
        key: value for key, value in scene.items() if key not in _FRAME_SCENE_EXCLUDE
    }
    frame_scene["narrative"] = desc
    frame_scene["protected_narrative"] = f"@({desc})@"
    frame_scene["draft_prompt"] = image_prompt_extract.build_fallback_content_tags(desc)
    if facts:
        if facts.get("action"):
            frame_scene["action"] = facts["action"]
        if facts.get("visual_facts"):
            frame_scene["visual_facts"] = facts["visual_facts"]
        subjects = _resolve_frame_subjects(scene, facts.get("subjects"), label_map)
        if subjects:
            frame_scene["frame_subjects"] = subjects
    return frame_scene


def generate_frame_prompts(
    profile: str,
    scene: Mapping[str, object],
    frame_descs: Mapping[str, str],
    generate_text: Callable[[str, str], str],
    diagnostics: dict[str, object] | None = None,
) -> dict[str, dict[str, object]]:
    """首尾帧提示词：与高潮点同构——先提取帧时点结构化事实，再走同一编译器。

    高潮点的 action/visual_facts 是主生成同轮搭车提取的结构化产物；首尾帧没有同轮
    载体，这里补一步「时点提取」：一次 LLM 从楼层前/尾部分分别提取该时点的
    action/visual_facts/subjects（英文结构化，字段名与高潮链 scene 对齐），再逐帧
    generate() 编译（同一校验、带因重写、deterministic_fallback 兜底）。
    帧描述是剧情可见正文，模型侧以 @(...)@ 防拦截标记保护（对齐 protected_narrative）。

    ⚠ 传入的 `scene` 是**高潮锚点**的 scene_spec；每帧实际编译用的是 `_build_frame_scene()`
    重建出的帧场景（事实底座沿用、画面要素只取本帧自己的描述、画面主体取本帧提取结果）
    ——见 `_FRAME_SCENE_EXCLUDE` 与 `_resolve_frame_subjects`。
    """
    if profile not in PROFILE_IDS:
        raise ValueError(f"未知提示词模式：{profile}")
    report = diagnostics if diagnostics is not None else {}
    report.clear()
    wanted = {
        frame: str(desc or "").strip()
        for frame, desc in frame_descs.items()
        if str(desc or "").strip()
    }
    if not wanted:
        return {}
    # 提取输入：事实字段子集（不含整段 narrative）+ 标记保护的帧描述。
    fact_keys = (
        "appearance", "wardrobe", "location", "visual_facts", "subjects",
        "secondary_characters", "actors", "rating",
    )
    extract_scene = {
        key: scene[key] for key in fact_keys if key in scene
    }
    extract_model_input = {
        **extract_scene,
        "frames": {
            frame: f"@({desc})@" for frame, desc in wanted.items()
        },
    }
    # 第 35 刀：提取器读到的是**匿名后的指代**（`the primary adult character`）⇒ 先按
    # `_anonymize_scene_characters` 的同一份名单与同一份序数词表记下「指代 ↔ 本名」，
    # 提取回来后用它映射回本名（否则多人契约的 facts 归属按键对不上）。
    extract_names = _character_names(extract_model_input)
    label_map = dict(
        zip(_character_labels(len(extract_names)), extract_names, strict=False)
    )
    extract_source = _scene_for_model(extract_model_input)
    extract_payload = json.dumps(
        extract_source, ensure_ascii=False, separators=(",", ":"),
    )
    extracted: dict[str, dict[str, object]] | None = None
    raw = generate_text(_FRAME_EXTRACT_SYSTEM, extract_payload)
    extracted = _parse_frame_extract(raw)
    if extracted is None:
        retry = (
            f"{extract_payload}\n\n上次输出无法解析为约定 JSON，请只输出约定 JSON 对象。\n"
            f"上次输出：{raw}"
        )
        extracted = _parse_frame_extract(generate_text(_FRAME_EXTRACT_SYSTEM, retry))
    report["frame_extract_ok"] = extracted is not None
    # 观测（2026-09-23 甲）：此前 trace 只记 frame_extract_ok 一个布尔 ⇒ 真机出现
    # 「首尾帧成品几乎逐字相同」时，无法判断是**提取器把两帧抹平**还是**编译器稀释**。
    # 这里把提取器的输入/原始输出/解析结果原样带出（纯记录，不参与任何判定）。
    report["frame_extract_input"] = extract_payload
    report["frame_extract_raw"] = raw
    report["frame_extract_parsed"] = extracted
    results: dict[str, dict[str, object]] = {}
    for frame, desc in wanted.items():
        facts = (extracted or {}).get(frame)
        if not facts:
            report[f"{frame}_extract_missing"] = True
        # 帧场景按本帧时点重建（不继承高潮锚点的画面要素），帧描述以 protected_narrative
        # 进链：模型侧带防拦截标记，本地校验用还原正文。
        frame_scene = _build_frame_scene(scene, desc, facts, label_map)
        # 诊断（第 35 刀）：本帧的**画面主体**（多人契约的判据）。模型侧逐格方差大
        # （真机实测：同输入同代码，高潮基线也会 direct↔fallback 翻转），唯这一项是
        # 确定性的 ⇒ 改帧逻辑时只认它做 A/B 归因。
        report[f"{frame}_subjects"] = list(_contract_subject_names(frame_scene))
        frame_diag: dict[str, object] = {}
        # ⚠ 2026-10-20 回退：曾在此传 allow_repair=False 省一轮 LLM 往返（该链最多 6 次调用，
        # 240s 前端超时易打满），但 `03-backend-illustration-pipeline.md:343-345` 记着
        # **2026-09-01 用户确认**：单次不重写会让提示词明显简略。用户口径「只要结果好就没问题」
        # ⇒ 质量优先，帧编译同样保留这次带因重写。
        prompt = generate(profile, frame_scene, generate_text, frame_diag)
        # 观测（2026-09-23 甲）：本帧实际喂进编译器的字段账本 + 场景键 + 成品。
        # 关键怀疑点：两帧的 camera/composition 不是继承来的（scene_spec 本就没有这两个键），
        # 而是编译器从各自 ≤500 字的帧描述里推的 ⇒ 需要账本与成品来判「谁抹平的」。
        report[f"{frame}_field_ledger"] = frame_diag.get("field_ledger") or {}
        report[f"{frame}_scene_keys"] = sorted(frame_scene.keys())
        report[f"{frame}_prompt"] = prompt
        report[f"{frame}_prompt_chars"] = len(prompt)
        results[frame] = {
            "prompt": prompt,
            "negative_prompt": negative_prompt(profile, frame_scene),
            "strategy": str(frame_diag.get("strategy") or "direct"),
            "validation_errors": list(dict.fromkeys([
                *[str(item) for item in _diag_list(frame_diag, "first_errors")],
                *[str(item) for item in _diag_list(frame_diag, "repair_errors")],
            ])),
            "field_ledger": frame_diag.get("field_ledger") or {},
        }
    return results
