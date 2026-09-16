"""fabric 内容循环域（原 agent_graph C 簇，逐字节搬移）。

逐字节自 agent_graph.py 搬移（方案 B 第二步之 C，见 docs/memory/agent-graph-seam-plan-2026-09-16.md）；
行为等价由既有测试网 + 搬移块 sha1 校验保证。
"""

from __future__ import annotations
import json
import re
from typing import Any, Callable
from app.services import agent_context, plan_compiler, run_trace
from app.services import llm as _llm
from app.services.agent_contracts import RunContext
from app.services.agent_support import _proxy_kw
from app.services.agent_retrieval import _history_embed_fn

def _flow_doc_hint(ctx: Any, user_text: str) -> str:
    """current_flow_doc 续跑提示（固化链 §3 设计 A）：延续语 + 有句柄才注入，纯机械。"""
    try:
        from app.services import flow_context

        repo_id = str(ctx.get("repo_id") or ctx.get("thread_id") or "")
        return flow_context.resume_hint(repo_id, user_text)
    except Exception:  # noqa: BLE001 - 句柄不可用不阻断编译/自由循环
        return ""


def _knowledge_catalog_text() -> str:
    """拼接注入文本（第三类目录附件，与配方清单同机制）。

    - 注入模式取 agent_knowledge.injection_config()：always（全量常驻=老行为）或
      smart（默认）——技能文档（meta.skill 非空且不在 always_docs）只注入一行触发
      描述 + 指令：命中场景先调 knowledge.load_doc(name=文件名主名) 拉全文照执行；
    - 无头普通知识文档保持历史行为：全量注入（KNOWLEDGE_MAX_FILES 上限不变）。
    """
    from app.services import agent_knowledge
    cfg = agent_knowledge.injection_config()  # 缺省 {} → smart
    mode = str(cfg.get("mode") or agent_knowledge.MODE_SMART)
    force_always = {str(n) for n in (cfg.get("always_docs") or []) if str(n).strip()}
    skill_lines: list[str] = []
    legacy: list[dict] = []
    for meta in agent_knowledge.list_docs():
        is_skill = bool(meta.get("skill")) and mode == agent_knowledge.MODE_SMART \
            and meta.get("name") not in force_always
        (skill_lines if is_skill else legacy).append(meta)

    parts: list[str] = []
    if skill_lines:
        lines = []
        for meta in skill_lines:
            trigger = str(meta.get("whenToUse") or "").strip()
            name = str(meta.get("name") or meta.get("file") or "?")
            lines.append(f"- {meta.get('file', name)}（skill={meta['skill']}）"
                         + (f"：{trigger}" if trigger else ""))
        parts.append(
            "【固化技能库】以下流程技能按触发场景按需装载、不常驻注入：\n"
            + "\n".join(lines)
            + "\n接到与某个技能触发场景相符的任务时，必须先调用 "
              "knowledge.load_doc(name=文件名主名，如「固化02-小说转合集卡规范」) "
              "拉取全文并照其结构与质量标准执行；拿不准是否相关也应先加载核对，"
              "禁止凭目录一句话另搞一套。")

    legacy_texts: list[str] = []
    appended = 0
    for meta in legacy:
        if appended >= agent_knowledge.KNOWLEDGE_MAX_FILES:
            break
        try:
            doc = agent_knowledge.read_doc(meta["name"])
        except (FileNotFoundError, ValueError, OSError):
            continue
        text = (doc.get("content") or "").strip()
        if text:
            legacy_texts.append(f"【知识：{meta['name']}】\n{text}")
            appended += 1
    if legacy_texts:
        parts.append("【固化知识库】以下是本项目沉淀的流程规范与映射表，执行相关任务时必须遵守"
                     "其中的结构与质量标准，禁止凭印象另搞一套：\n\n" + "\n\n".join(legacy_texts))
    return "\n\n".join(parts)


def _recipe_catalog_text() -> str:
    """已保留（saved）固化流程预设清单：注入编译/自由循环上下文供复用匹配（草稿不出现）。"""
    try:
        from app.services import plan_tasks as _pt
        saved = [r for r in _pt.list_recipes().values()
                 if str(r.get("status") or "saved") == "saved"]
    except Exception:  # noqa: BLE001 - 清单不可用时不阻断对话
        return ""
    if not saved:
        return ""
    lines = []
    for r in sorted(saved, key=lambda x: str(x.get("created_at") or 0), reverse=True):
        plan = r.get("plan") or {}
        steps = plan.get("steps") or []
        ops = "、".join(str(s.get("operation") or "") for s in steps[:6])
        lines.append(f"- 《{r.get('name')}》 id={r.get('id')}"
                     f"（{len(steps)} 步：{ops}）意图：{r.get('intent') or ''}")
    return ("【固化流程预设】以下是用户已确认固化的可重放流程。用户目标与某条高度一致时，"
            "优先调用 plan.instantiate_recipe 整条重放（省 token，durable/expensive 步骤照常走审批）；"
            "不完全一致才逐步编排：\n" + "\n".join(lines))


def _live_trace(ctx: dict, agent: str):
    """把 run_trace 事件同时实时推给前端对话流（stream_sink.trace）。

    非流式节点（计划编译/自由循环）内部阻塞时，SSE 也要能看到过程：
    思考、重试、工具调用、步骤执行、失败——不再只有「生成中」黑盒。
    """
    def _line(event: str, data: dict) -> str:
        op = str(data.get("operation") or data.get("tool") or "")
        status = str(data.get("status") or "")
        if event == "model.request":
            return "🤔 模型正在思考…"
        if event == "structured.output":
            return "📐 解析模型输出…"
        if event == "plan.compiled":
            return f"✅ 计划编译完成（{data.get('steps', '?')} 步）" if status == "ok"                 else "⚠️ 计划校验未通过，正在重试…"
        if event == "plan.sections_filled":
            return f"🧩 回填 {data.get('count', '?')} 个套装提示词"
        if event == "plan.validated":
            return "✅ 计划校验通过，投递执行队列"
        if event == "plan.attachments":
            return f"📎 已预读文档：{data.get('path', '')}" if status == "ok"                 else f"⚠️ 文档预读失败：{data.get('path', '')}"
        if event == "tool.call":
            return f"🔧 调用工具：{op}"
        if event == "tool.result":
            return f"{'✅' if data.get('ok') else '❌'} 工具{'完成' if data.get('ok') else '失败'}：{op}"
        if event == "plan.step_started":
            return f"▶ 执行步骤：{op}"
        if event == "plan.step_done":
            return f"✅ 步骤完成：{op}"
        if event == "plan.step_failed":
            return f"❌ 步骤失败：{op}（{str(data.get('error') or '')[:60]}）"
        if event == "plan.step_blocked":
            return f"⛔ 步骤受阻：{op}（{str(data.get('reason') or '')}）"
        if event == "plan.terminal":
            return f"🏁 计划结束：{status}"
        return f"🔄 {event}"

    def _detail(event: str, data: dict) -> str | None:
        """trace 行详情（2026-09-06）：思考文本 / 工具参数 / 工具结果，
        前端执行过程面板点开查看。"""
        if event == "model.request":
            t = str(data.get("thinking") or "").strip()
            return t[:400] if t else None
        if event == "tool.call":
            p = data.get("params")
            if p is None:
                return None
            try:
                return json.dumps(p, ensure_ascii=False)[:400]
            except Exception:
                return str(p)[:400]
        if event == "tool.result":
            r = data.get("result")
            if r is None:
                return None
            try:
                return (json.dumps(r, ensure_ascii=False)[:500]
                        if not isinstance(r, str) else r[:500])
            except Exception:
                return str(r)[:500]
        return None

    def emit(event: str, **data):
        run_trace.emit(ctx, event, agent=agent, **data)
        sink = ctx.get("stream_sink")
        if not callable(sink):
            return
        text = _line(event, data or {})
        if text:
            try:
                detail = _detail(event, data or {})
                sink({"trace": text, **({"detail": detail} if detail else {})})
            except Exception:
                pass
    return emit


def _live_chat(ctx: dict):
    """包装 chat_fn：调用前/成功/失败/重试都实时 sink 到对话流。

    模型调用可能挂起数分钟——调用前先显示「思考中」，重试时逐次显示原因，
    否则前端只有一动不动「生成中」。
    """
    base_fn = ctx.get("chat_fn") or _llm.chat

    def _sink(text: str) -> None:
        sink = ctx.get("stream_sink")
        if callable(sink):
            try:
                sink({"trace": text})
            except Exception:
                pass

    def wrapped(base, key, model, system, user, **kw):
        _sink("🤔 模型思考中…")
        try:
            if base_fn is _llm.chat:
                kw["on_retry"] = lambda n, e: _sink(
                    f"🔄 第 {n} 次重试（{str(e)[:60]}）")
            result = base_fn(base, key, model, system, user, **kw)
            _sink("✅ 模型响应完成")
            return result
        except Exception as e:  # noqa: BLE001 - 原样抛出，只补可视化
            _sink(f"❌ 模型调用失败：{str(e)[:80]}")
            raise
    return wrapped


def _history_summarizer(context: RunContext):
    """自动压缩的摘要回调（/compact 对齐）：LLM 把旧段历史压成结构化要点。

    失败/空摘要返回 None → recent_history 退回机械保头保尾裁剪（fail-open）。
    """
    try:
        base = context.chat.base_url
        key = context.chat.api_key
        model = context.chat.model
    except Exception:  # noqa: BLE001 - dict ctx/缺 chat 配置 → 无摘要器（机械路径）
        return None
    if not (base and model):
        return None
    proxy = (context.chat_proxy_url or "").strip()

    def _summarize(old: list[dict]) -> str | None:
        transcript = "\n".join(
            ("用户" if item.get("role") == "user" else "助手") + "：" + str(item.get("content") or "")
            for item in old)
        system = (
            "你是对话压缩器。把以下历史对话压缩成一段结构化摘要，供后续对话作为唯一历史依据。"
            "必须保留：任务目标与演变、已确认的决定与设定（角色/服装/配色/文件路径等专名与数字原样保留）、"
            "用户明确否决的内容、未完成事项。不新增内容、不评论、不提问。只输出摘要正文。")
        try:
            out = _llm.chat(base, key, model, system, transcript, temperature=0.2,
                            **({"proxy": proxy} if proxy else {}))
        except Exception:  # noqa: BLE001 - 压缩失败退机械裁剪
            return None
        return (out or "").strip() or None

    return _summarize


def _fabric_capabilities(intent_text: str, configured: set[str]) -> list[dict]:
    """自由循环能力清单：卡/书/文档交付任务（固化02/03/04）按白名单过滤；
    其余（批量生图等确定性任务走计划编译，不走自由循环）保持全量。

    白名单单一属主 = capability_registry.FABRIC_DOC_DELIVERY_OPS（2026-09-10，
    此前是本文件里的硬编码 frozenset，新增能力要改两处）；
    再按意图按需叠加两个可选通道（都要求用户显式说出口，默认关闭）：
    - FABRIC_TOOLING_OPS：写脚本/跑命令/排错（自建工具通道）；
    - FABRIC_WEB_OPS：联网检索 + 受控下载（链路①+②，内置安全链而非裸脚本联网）。
    """
    from app.services import capability_registry as _cr
    caps = _cr.with_availability(configured)
    if not plan_compiler.is_doc_delegation_intent(intent_text):
        return caps
    allowed = set(_cr.FABRIC_DOC_DELIVERY_OPS)
    if plan_compiler.wants_tooling_intent(intent_text):
        allowed |= _cr.FABRIC_TOOLING_OPS
    if plan_compiler.wants_web_material_intent(intent_text):
        allowed |= _cr.FABRIC_WEB_OPS
    return [c for c in caps if c.get("operation") in allowed]


def _fabric_discipline(ctx: Any) -> str:
    """自由循环 system 级执行纪律（2026-09-06）：大 txt 附件禁止整本 read_text。

    放 system（每步决策都可见）而非仅 history——实锤：history 里的纪律提示模型会无视，
    长文本进 messages 后决策输出被网关截断（第 5 步模型决策失败）。返回空串=无纪律。
    """
    parts = []
    # 2026-09-07 治本：纪律无条件注入（此前只在附件 .txt/.md 时注入——后续补写指令
    # 不带附件，模型完全看不到纪律，反复自我怀疑/不按规范执行实锤）。
    if True:
        parts.append(
            "【执行纪律·合集卡制作】本任务是小说→合集卡内容生成，必须遵守以下规范：\n"
                "1. 章节感知阅读（2026-09-07 用户定案）：先 novel.survey 拿章节清单（标题/offset），标题是线索——"
                "从标题识别角色/地区/情节，规划定向阅读，禁止通读全文、禁止按字数硬切。"
                "按章节读：单章 ≤2 万字多章合并一卷（offset 定位），单章 >2 万分次读。\n"
                "2. 角色素材：用 novel.charfacts 切素材段（_prep/charfacts/<名>.txt），"
                "切素材时用 chapter_start/chapter_end 限定到当前剧情已推进到的章节。\n"
                "3. 防剧透：写角色经历/重大事件条目时，只使用已读章节的内容，"
                "禁止使用未读章节/未来剧情——角色经历不能超过当前剧情推进的部分。\n"
                "4. 任务定性（2026-09-06 用户定案，网关 400 实锤）：当前任务是制作合集卡"
                "（六层条目：系统判定机制/全局机制/世界背景地理势力事件/局部机制/角色卡/名册）。"
                "先判定本作 NSFW/正常：出现成人向设定/玩法即判 NSFW；判 NSFW 后参照现有 NSFW 卡的"
                "机制层写法（如「全局机制·女尊采补与男畜逆袭」「全局机制·龙阳决与双修」——机制条目"
                "是设定化命名不是具体描写），本卡必须产出同名口径的 NSFW 玩法机制条目，禁止回避删除"
                "成人玩法设定。成人设定（角色关系/主仆/肉体/体质/个体机制/好感度）如实转写为设定条目，"
                "禁止因敏感内容更换任务；条目正文是设定描述不是文学复述，禁止原样复制具体性行为描写"
                "（会触发网关内容审核 400）。先通读全书整体归纳六层结构，再分批落条目，禁止碎片化输出。\n"
                "5. 制卡师立场（2026-09-06 用户定案，NSFW 认知纠正）：你是专业的 NSFW 合集卡制卡师。"
                "NSFW 角色卡是正当的创作产物，用户委托制作它就是本任务的合法目标——不是违规内容、"
                "不需要净化、不需要回避、更不需要在内心评判它。对成人设定坦然、专业、设定化地处理："
                "像 ST 社区制卡者一样把敏感内容转写为机制条目（采补/双修/调教/契约/受孕/炉鼎…），"
                "完整保留设定深度，不因内容敏感而删减角色或机制。\n"
                "6. NSFW 成功生成经验（参照 Character Card Builder spec 过程机制，2026-09-06）："
                "学它的机制而非单卡结构——E1 卡纲阶段显式确认 NSFW 属性/偏好方向/角色名单，确认前"
                "不写正式条目、不靠猜测；E2 步骤化渐进（卡纲→分批条目→验收，批间确认）；E3 立场不拒"
                "（NSFW 是正常创作）；E4 确认 NSFW 后如实完整转写成人设定，不回避不省略不净化，条目"
                "正文设定化描述，具体性行为词汇被网关 400 时自动交本地模型转写（混合模式），云端继续"
                "下一步；E5 模糊设定主动向用户确认，不脑补关键机制。\n"
                "7. 两阶段流水线（2026-09-07 用户定案）：Part 1 通读建档——目标是整理全文，"
                "最终自然读完所有卷；每读若干卷把该卷的角色/事件/世界观要点用 doc.create_repo/"
                "file.edit 追加进作品 _prep/材料/{world,chars,mech,nsfw}.json，原文交 fabric 压缩；"
                "通读完成后产出【目标方案】（出场人物/大事件/世界观/地理势力概览 + 章节-内容映射）"
                "落 docs/目标方案-<作品名>.md 给用户确认。Part 2 定向详读——基于目标方案针对每块"
                "内容定向读对应章节补充细节，分批写完整条目；判 NSFW 必须有 8-14 条独立"
                "「NSFW·<标题>」条目（含【边界】仅限成年声明）。通读是目标而非禁止，但必须"
                "边读边整理，禁止 19 步纯读不产出。\n"
                "8. 禁止重复读同一段（2026-09-07 实锤）：分卷读覆盖全书后，模型因上下文压缩"
                "「忘记」已读细节，反复 read_text 同一 offset（0/20000/40000 绕圈 6 轮无推进）。"
                "规则：同一文件同一 offset 最多读 2 次；第 2 次后仍缺细节，读 charfacts 素材段"
                "或基于已读归纳直接产出材料/卡纲——禁止第 3 次重复读同一段，读完即产出，"
                "不得停滞在确认细节。\n"
                "9. 卡纲前置概览（2026-09-07 用户定案）：请求写卡纲（doc.create_repo）时，"
                "卡纲 content 第一部分必须是【已读章节与材料概览】——已读章节范围（如「第 1-6 章」）、"
                "角色名单（主要出场角色）、机制要点、事件线索、地理/势力概览；让用户批准的是"
                "有依据的草案，禁止凭少量阅读匆忙给空结构。\n"
                "10. B 态单卡合集（2026-09-07 用户定案，240KB）：默认产出单卡——全部六层条目写完后，"
                "用 character.upsert_repo 落主卡，card.data.character_book.entries = 全部条目 list，"
                "card.json 含全部内容 ≈ 240KB（A 态主卡仅 2KB 骨架=没内嵌，不合格）；first_mes 非空。\n"
                "11. 密度（2026-09-09 用户定案）：角色条目 ≥1800 字、机制 ≥800、编号 ≥600、NSFW ≥400；"
                "NSFW 玩法 ≥400——旧 400/200/200 太低导致 2KB 骨架卡。\n"
                "12. 素材锚定扩写法（2026-09-07 用户定案，防偏差）：密度不达标时禁止凭记忆"
                "重写一个短版覆盖——那不是扩写是退化（22:08 实锤：戴茂 1945→949 倒退）。"
                "正确做法：(a) 先读该条目对应素材段 _prep/charfacts/<角色或主题名>.txt 的"
                "完整内容（分卷读，直到读完,不能只读前 2 万字符）；(b) 从素材原文提取关键词/"
                "具体细节——真实事件、对话、关系变化、外貌描写、机制运作、好感节点；(c) 在"
                "已有条目基础上逐字段扩充这些原文细节，每条目标 ≥1800/800/600/400 字；(d) "
                "扩写内容必须能追溯到素材原文，禁止凭空脑补、禁止重复堆砌同一细节。\n"
                "13. 扩写目标校准（2026-09-07）：一条不达标条目的扩写，期望一次到位（如角色"
                "从 900→1800+，机制 500→800+），写入后跑 check_density 确认；若仍不达标，"
                "继续读素材补细节，而不是反复重写同一短版。\n"
                "14. 环境信任与上下文压缩认知（2026-09-07 实锤）：(a) repo_id 是作品仓库"
                "UUID（如 8fed7f23-...），由执行环境归一注入，worldbook 快照就写在其目录下的"
                "worldbook.json——这是正确的，不要怀疑/改用它显示名（如「玫瑰与繁花」只是文件夹名）；"
                "(b) 对话历史里出现「UPSERT_ENTRIES 已写…」是上下文压缩标记：系统把旧步骤的"
                "条目参数压缩成摘要以控制上下文长度，**实际落盘的 worldbook.json 始终是完整条目**，"
                "不是摘要——不要误以为没写入而停下来问用户；(c) 数据完整性以 check_density + "
                "worldbook 快照为准，不要因为看到压缩标记或 repo_id 长相而自我怀疑、中断流程；"
                "继续按密度检查补写直至 passed=True。\n"
                "15. 主角归一（{{user}} 映射，2026-09-07 用户定案·通用规则）：主角（玩家扮演"
                "对象）在卡里对应 {{user}}，不是普通角色。(a) 主角自身条目不含【与{{user}}的"
                "关系】【好感分阶】（玩家不对自己有第三人称视角），改用【主角定位】；"
                "(b) 其他角色/机制/世界/NSFW 条目正文里提及主角一律替换为 {{user}}"
                "（如「戴茂哥哥」→「{{user}}哥哥」），确保玩家代入不穿帮；(c) 先识别谁是"
                "主角（通读主视角/被后宫围绕的核心），禁止把配角误当 {{user}}；"
                "(d) 此规则对任何作品的主角生效，不限于本作。\n"
                "15b. 主角条目保留与初始好感（2026-09-08 用户定案）：(e) 主角自身条目必须保留"
                "（comment 仍为「角色卡·主角名」），用【主角定位】替代【与{{user}}的关系】——"
                "主角是 {{user}} 的化身卡，不能被删除或覆盖，其他条目提及主角仍替换为 {{user}}；"
                "(f) 好感度系统机制条目（2026-09-08 用户定案）：初始好感不是写在每个角色条目里硬编码，"
                "而是独立做一条「系统判定机制·好感度初始值与动态变化」条目，由它统一规定：初始好感按剧情因果推导"
                "（不是默认 0）——亲缘/旧识/恩人出场即中高好感 55-90，{{user}} 操作损害过其利益或敌对/仇视出身"
                "则初次见面即负好感 -30 到 -10，陌生中立 20-55；同时规定好感度随剧情事件动态增减（被救+/被夺利-）。"
                "各角色条目里不必再写初始好感值，统一由这条机制条目管辖。\n"
                "16. 定向补写·不重读重写达标条目（2026-09-08 用户定案，防绕圈）：check_density 返回的"
                "below 清单是唯一补写范围——只补 below 里列出的不达标条目（读它对应的 _prep/charfacts 素材），"
                "已达标条目（在 below 之外的）禁止重读、禁止重写、禁止反复 read worldbook.json 全文核对差异"
                "（13:23 实锤：48 步反复 read 同一 worldbook.json off=0 原地绕圈）。补写流程："
                "check_density 拿 below 清单、逐条读对应素材补写、再 check_density 确认，达标条目不进循环。")
    return "\n\n".join(parts)


def _recent_inspiration_blocks(ctx: Any, limit: int = 2, chars: int = 700) -> str:
    """最近灵感卡 → 讨论上下文参考块（2026-09-11 实锤修复）。

    to_prompt_history 刻意跳过灵感卡（不污染剧情上下文），副作用是讨论型请求
    （「参考这个角色设计服装」）的 AI 看不到卡里的发色/瞳色/设定，反问用户要特征
    ——用户观感即「灵感图丢了/特征没理解」。此处只进自由循环历史（智能编造域），
    剧情通道不受影响。只搬标题+正文节选+已勾选图 URL。
    """
    from app.services import chat_snapshot
    thread_id = str(ctx.get("thread_id") or "")
    if not thread_id:
        return ""
    cards: list[dict] = []
    try:
        for item in chat_snapshot.load(thread_id):
            if isinstance(item, dict) and isinstance(item.get("inspiration"), dict):
                cards.append(item["inspiration"])
    except Exception:
        return ""
    if not cards:
        return ""
    blocks: list[str] = []
    for insp in cards[-limit:]:
        title = str(insp.get("title") or "未命名")
        content = str(insp.get("content") or "")[:chars]
        selected = [u for u in (insp.get("selected") or []) if isinstance(u, str)]
        block = f"【灵感参考 · {title}】\n{content}"
        if selected:
            block += "\n已选参考图：" + "；".join(selected[:3])
        blocks.append(block)
    return ("【最近灵感卡（讨论时直接引用其特征，无需再向用户索要）】\n"
            + "\n\n".join(blocks))


def _task_session_continuation(ctx: Any, intent_text: str) -> bool:
    """上下文合同 P1：任务会话接续判定——先查登记（显式状态），词表只兜底。

    登记里该会话有活跃 design_discussion 任务 + 本轮带设计话题词 + 本轮不是
    其他明确意图（委派/文档交付）→ 仍走设计讨论循环。登记缺失/TTL 过期/
    任务类型不符一律 False（fail-open 退回词表粘性判定）。
    """
    from app.services import plan_compiler, task_session_store
    source = (intent_text or "").strip()
    if not source:
        return False
    if (plan_compiler.is_delegation_intent(source)
            or plan_compiler.is_doc_delegation_intent(source)):
        return False
    if not plan_compiler.mentions_design_topic(source):
        return False
    session = task_session_store.active(thread_id=str(ctx.get("thread_id") or ""))
    return bool(session and session.get("task_type") == "design_discussion")


def _recent_user_texts(ctx: Any, limit: int = 3) -> list:
    """快照里最近 N 条用户消息原文（讨论粘性判定用，不含本轮）。"""
    try:
        from app.services import chat_snapshot
        items = chat_snapshot.load(str(ctx.get("thread_id") or ""))
    except Exception:  # noqa: BLE001 - 快照缺失不阻断路由
        return []
    out = []
    for _m in items:
        if isinstance(_m, dict) and _m.get("role") == "user":
            _t = str(_m.get("text") or "")
            if _t:
                out.append(_t)
    return out[-limit:]


def _recent_attachment_lines(ctx: Any, limit: int = 5) -> str:
    """本会话此前上传的附件 → 真实路径引用块（讨论接续轮用）。

    附件挂在当轮 ctx、快照消息 parts（type=file）里有存档（2026-09-11 实锤）：
    文档在上一轮上传、接续轮 ctx 无 attachments → AI 回头找用户要
    「你文档中的服装设计内容」。从快照倒序捞 fileId 并解析真实路径。
    """
    try:
        from app.services import attachment_store, chat_snapshot
        items = chat_snapshot.load(str(ctx.get("thread_id") or ""))
    except Exception:  # noqa: BLE001
        return ""
    seen = {str(_a.get("file_id") or "") for _a in (ctx.get("attachments") or [])
            if isinstance(_a, dict)}
    lines: list = []
    for _m in reversed(items):
        if len(lines) >= limit:
            break
        if not (isinstance(_m, dict) and _m.get("role") == "user"):
            continue
        for _p in (_m.get("parts") or []):
            if not (isinstance(_p, dict) and _p.get("type") == "file"):
                continue
            _fid = str(_p.get("fileId") or "")
            _name = str(_p.get("name") or "")
            if not _fid or _fid in seen:
                continue
            seen.add(_fid)
            _path = ""
            try:
                _r = attachment_store.resolve(_fid)
                _path = str(_r) if _r is not None else ""
            except Exception:  # noqa: BLE001
                _path = ""
            lines.append(f"- {_name}（真实路径：{_path or '未落盘'}）")
    if not lines:
        return ""
    return ("【本会话此前上传的附件文件（接续轮仍可直接引用）】\n"
            + "\n".join(lines)
            + "\n直接使用上述真实路径读取/引用，禁止反复 list_dir 查找、"
              "禁止向用户索要本会话已上传过的文件。")


def _fabric_knowledge_history(ctx: Any, user_text: str,
                              task_type: str = "") -> str:
    """自由循环历史装配（full/approval 共用）：分档历史 + 配方目录 + 固化知识目录 + 流程句柄。

    历史走 context_gate 按当前意图域分档注入（2026-09-06 实锤：生图历史全量平铺
    淹没「小说转合集卡」指令，模型把合集卡任务编排出生图步骤）；意图域未识别时
    退回原全量拼接，不误伤依赖上文的短指令。
    上下文合同 P1（2026-09-11）：原 include_inspirations 布尔升级为 task_type
    合同模板——"design_discussion" 注入 ②材料（灵感卡+历史附件真实路径）+
    ③规范（提示词规范全文），其余任务类型暂走基线装配。
    """
    from app.services import context_gate as _cg
    intent_source = str(ctx.get("message") or user_text or "").strip()
    # 实锤修复（2026-09-06 用户四步实锤）：装配回路历史不含本轮消息（前端 visibleHistory
    # 在 setMessages 前计算、后端 append_turn 回合结束才落），历史最后一条 user 是上一轮
    # 指令——必须显式传本轮指令全文作锚点，否则上一轮跨域指令被误标「当前指令」拿最高权重
    #（生图→合集卡场景被反向放大）。P3①：嵌入配置存在时叠加语义分档（失败退回词表）。
    # P2b（2026-09-12）：keep 档按 token 预算收口（同域长讨论不再无限膨胀），
    # 预算走主通道同款「min(设置, 模型窗口−预留)」；摘要器 fail-open 退机械聚合。
    _hist_budget = agent_context.resolve_history_budget(
        ctx.get("context_max_tokens") or 0, ctx.get("preset_sampling"))
    history_text = _cg.history_text_gated(ctx, intent_source, anchor_text=intent_source,
                                          embed_fn=_history_embed_fn(ctx),
                                          keep_token_budget=_hist_budget,
                                          keep_summarizer=_history_summarizer(ctx))
    recipe_catalog = _recipe_catalog_text()
    if recipe_catalog:
        history_text = (history_text + "\n\n" + recipe_catalog).strip()
    knowledge = _knowledge_catalog_text()
    if knowledge:
        history_text = (history_text + "\n\n" + knowledge).strip()
    flow_hint = _flow_doc_hint(ctx, user_text)
    if flow_hint:
        history_text = (history_text + "\n\n" + flow_hint).strip()
    # 上下文合同⑤产物指针（2026-09-12）：活跃任务会话里累积的已产出文件路径注入，
    # 接续轮「改一下/再补一份」直接改这些文件而非重生成。会话缺失/TTL 过期静默跳过。
    try:
        from app.services import task_session_store
        _sess = task_session_store.active(thread_id=str(ctx.get("thread_id") or ""))
        _prod = [str(_p) for _p in ((_sess or {}).get("product_paths") or []) if _p]
        if _prod:
            history_text = (history_text
                            + "\n\n【本任务已产出文件（更早轮次交付物，直接引用路径；"
                              "用户要求修改时改这些文件，勿重复生成）】\n"
                            + "\n".join(f"- {_p}" for _p in _prod[:10])).strip()
    except Exception:  # noqa: BLE001 - 产物指针缺失不影响装配
        pass
    if task_type == "design_discussion":
        insp_block = _recent_inspiration_blocks(ctx)
        if insp_block:
            history_text = (history_text + "\n\n" + insp_block).strip()
        # 设计讨论提示词规范注入（2026-09-11 用户四条质量反馈固化）：参考图锚点
        # 第一句 / 渐变特征恒定 / 三段式 tag+自然语言混排 / 长度下限。目前唯一
        # include_inspirations=True 调用方是设计讨论分支，故挂在此处随讨论生效；
        # 文档缺失/读取失败不阻断讨论。
        try:
            from app.services import agent_knowledge
            _spec = agent_knowledge.read_doc("设计讨论-角色服装提示词规范.md")
            if _spec.get("content"):
                history_text = (history_text
                                + "\n\n【设计讨论提示词规范（下方产出草案必须逐条遵守）】\n"
                                + str(_spec["content"])).strip()
        except Exception:  # noqa: BLE001 - 规范文档缺失不阻断讨论
            pass
        _hist_att = _recent_attachment_lines(ctx)
        if _hist_att:
            history_text = (history_text + "\n\n" + _hist_att).strip()
    attach_lines = []
    for _a in (ctx.get("attachments") or []):
        _name = str(_a.get("name") or "")
        _fid = str(_a.get("file_id") or "")
        _path = ""
        if _fid:
            try:
                from app.services import attachment_store as _attach_store
                _p = _attach_store.resolve(_fid)
                _path = str(_p) if _p is not None else ""
            except Exception:  # noqa: BLE001 - 路径解析失败不阻断
                _path = ""
        if _name or _path:
            attach_lines.append(f"- {_name}（真实路径：{_path or '未落盘'}）")
    if attach_lines:
        history_text = (history_text + "\n\n【用户上传的附件文件】\n"
                        + "\n".join(attach_lines)
                        + "\n直接使用上述真实路径读取/引用，禁止反复 list_dir 查找。").strip()
    # 固化02/03 内容生成纪律（2026-09-06 实锤：模型对小说附件整本 file.read_text
    # 分块翻读（40k 字符/次），长输入决策被网关拖死）。规范 §2.5 要求机械预处理：
    # 小说/txt 大附件先 novel.survey 拿候选名单 → novel.charfacts 切素材段 →
    # 模型只读素材段写条目，禁止整本 read_text。
    _txt_big = any(str(_a.get("name") or "").lower().endswith((".txt", ".md"))
                   for _a in (ctx.get("attachments") or []))
    if _txt_big:
        history_text += (
            "\n\n【执行纪律】用户上传的是长文本/小说类附件（可能数十万字）："
            "先 novel.survey 拿候选名单 → novel.charfacts 切素材段（用 chapter_start/end "
            "限定到当前剧情推进章节）→ 角色条目只读素材段；理解机制/背景/事件可 "
            "file.read_text 分卷读（每次 ≤20000 字符，按章节顺序），禁止一次硬塞超长文本。"
            "写角色经历/事件不得使用未读章节的未来剧情。")
    return history_text


def _fabric_run_args(ctx: Any, *, intent: str, history: str,
                     access_mode: str, lease_id: str, output_dir: str,
                     repo_id: str, configured: set[str],
                     images: list[str], resume: dict | None = None,
                     checkpoint_fn: Callable | None = None,
                     delivery_intent: str | None = None) -> dict:
    """自由循环公共调用参数（full/approval 共用）。

    2026-09-07 重发的资本：card 交付任务启动时自动扫描作品已有产出
    （卡纲/目标方案/素材/世界书快照/主卡），生成【前序产出档案】注入 system——
    模型开局就知道已有产出，在其上继续/修改/优化，不重读全文重建。

    delivery_intent（2026-09-10）：判定用文本 = 本轮原文拼上历史用户消息后的
    `_fabric_judge`。**只影响能力面与前序产出档案**，不改 intent（进度卡/断点仍用
    本轮原文）——解决「先对话积累上下文，再说一句『整理成设定总集』」这类短指令：
    若用本轮原文判定，能力面收窄与前序产出扫描都会落空（实锤场景）。
    """
    _delivery = delivery_intent if delivery_intent is not None else intent
    work_brief = ""
    try:
        if plan_compiler.is_doc_delegation_intent(_delivery):
            from app.services import fabric_work
            # 缓存不是无脑读取（2026-09-07 用户定案）：前序产出档案的注入量受
            # 上下文预算约束——取 context_max_tokens 的 20% 且封顶 4000 字符；
            # 无上限（0）用默认 4000；预算被调小时缓存自动缩量，不让档案挤占
            # 历史与本体注入，避免「缓存反客为主」。
            _ctx_budget = ctx.get("context_max_tokens")
            _brief_budget = 4000
            if isinstance(_ctx_budget, int) and _ctx_budget > 0:
                _brief_budget = max(800, min(4000, _ctx_budget // 5))
            work_brief = fabric_work.build_work_brief(
                output_dir, repo_id, max_chars=_brief_budget)
    except Exception:  # noqa: BLE001 - 档案生成失败不阻断 run
        work_brief = ""
    return dict(
        intent=intent, history=history,
        capabilities=_fabric_capabilities(_delivery, configured),
        access_mode=access_mode, lease_id=lease_id,
        output_dir=output_dir, repo_id=repo_id,
        configured_models=configured,
        chat_base=ctx["chat_base"], chat_key=ctx["chat_key"],
        chat_model=ctx.get("route_model") or ctx["chat_model"],
        chat_fn=_live_chat(ctx),
        structured_chat_fn=ctx.get("structured_chat_fn"),
        images=list(images or []),
        proxy_kwargs=_proxy_kw(ctx),
        # 联网检索代理（web.search_materials）：与聊天代理分开——本应用把「联网代理」
        # 单独配置（RunContext.proxy_url → ctx["proxy"]），走它访问外网搜索源。
        search_proxy=str(ctx.get("proxy") or ""),
        trace=_live_trace(ctx, "fabric"),
        resume=resume,
        checkpoint_fn=checkpoint_fn,
        system_extra=_fabric_discipline(ctx),
        work_brief=work_brief,
    )


def _collect_fabric_artifacts(ctx: Any, output_dir: str, since: float = 0.0) -> list[dict]:
    """收集**本轮实际写入**的交付物（主卡/世界书），供产物卡展示；失败返回空列表不阻断结果。

    2026-09-09：since = 本 run 开始时间，只收 mtime ≥ since 的产物——否则「目录里最新的
    产物」会把上一轮/历史作品的产物当成本轮产物（实锤：御仙 ST 卡任务显示玫瑰与繁花）。
    since=0 时退回旧行为（全目录最新组），供历史消息补卡等无 run 时间的场景使用。
    2026-09-10 B3/A：repo_id 现在是**真参与域解析**（域 = 作品域 ∪ 本作品拥有的卡目录），
    不再只是软提示——否则收产区是作品库根全域，会把别作品刚写的东西当成本轮产物。
    """
    try:
        from app.services import collection_artifacts
        repo_id = str((ctx or {}).get("repo_id") or "")
        return collection_artifacts.collect_artifacts(output_dir, repo_id=repo_id, since=since)
    except Exception:  # noqa: BLE001 - 产物卡只是展示增强
        return []


def _fabric_finalize(outcome: Any, text: str, trace: list[str],
                     ctx: Any, output_dir: str) -> dict:
    """自由循环终局统一处理（full/approval 共用）：done 带固化询问；error 如实返回。

    2026-09-08：产物卡「随时可见」——done 与未完成（step_limit/error/awaiting_approval）
    都收集当前已落盘的交付物（主卡/世界书）随结果下发；未完成时展示的是**已落盘的部分**
    （如只写了 worldbook.json、还没内嵌主卡）。收集失败不影响结果。
    """
    # 2026-09-09 收紧：仅 done 才下发产物卡（中间「需要批准/执行未完成」不再有卡，
    # 否则前端会把同一批「当前最新产物」挂在所有智能编造消息上）。done 同时存一版快照。
    if outcome.status == "done":
        artifacts: list[dict] = _collect_fabric_artifacts(
            ctx, output_dir, since=float(getattr(outcome, "_started_at", 0.0) or 0.0))
        recipe_marker = ""
        # 事前固化询问的「直接跑」分支（2026-09-10）：用户已经在询问里明确表示不要为
        # 这次任务固化流程。若这里照旧产出草稿卡，等于问完照样弹「保留/不保留」，
        # 那次询问就是纯噪声——所以必须抑制。
        if bool((ctx or {}).get("_suppress_recipe")):
            run_trace.emit(ctx, "fabric.solidified", status="suppressed",
                           reason="用户在事前询问里选择了「直接跑」")
        else:
            try:
                from app.services import plan_tasks as _pt
                recipe = _pt.solidify_steps(
                    intent=text, steps=outcome.steps, output_dir=output_dir, name=text[:40])
                recipe_marker = f"\n\n[[recipe:{recipe['id']}|{recipe['name']}]]"
                run_trace.emit(ctx, "fabric.solidified", status="draft",
                               recipe_id=recipe["id"], steps=len(outcome.steps))
            except ValueError as exc:
                run_trace.emit(ctx, "fabric.solidified", status="skipped", reason=str(exc))
            except Exception as exc:  # noqa: BLE001 - 固化失败不影响本轮结果
                run_trace.emit(ctx, "fabric.solidified", status="error", error=str(exc))
        try:
            from app.services import artifact_versions as _av
            # B3/A：快照存储域 = 作品域（仓库/小仓库文件夹），由 repo_id 解析——
            # 此前落作品库根下的全局 `_versions/`，所有作品的版本混放、回档列表互相串味。
            _av.snapshot_current(output_dir, trigger="done",
                                 summary=outcome.reply[:200] or text[:200],
                                 repo_id=str((ctx or {}).get("repo_id") or ""))
        except Exception:  # noqa: BLE001 - 版本快照失败不影响交付
            pass
        # 上下文合同⑤产物指针回写（2026-09-12）：本轮实际写入的交付物路径进任务
        # 会话，接续轮注入「已产出文件」——用户说「改一下」时改这些文件而非重生成。
        # 已有活跃任务（讨论/委派）时保留其 task_type，只累积产物。
        try:
            from app.services import task_session_store
            _paths = [str(_a.get("path") or "") for _a in (artifacts or [])
                      if isinstance(_a, dict) and _a.get("path")]
            if _paths:
                _tid = str((ctx or {}).get("thread_id") or "")
                _prev = task_session_store.active(thread_id=_tid)
                task_session_store.save({
                    "task_type": (_prev or {}).get("task_type") or "fabric_delivery",
                    "intent": text,
                    "thread_id": _tid,
                    "output_dir": output_dir,
                    "product_paths": _paths[:10],
                })
        except Exception:  # noqa: BLE001 - 产物登记失败不影响交付
            pass
        return {"result_text": outcome.reply + recipe_marker,
                "trace": trace + ["🧵 智能编造自由循环完成"],
                "artifacts": artifacts}
    return {"result_text": f"智能编造执行未完成（{outcome.status}）：{outcome.error}",
            "trace": trace + [f"🧵 智能编造循环中断：{outcome.status}"]}


def _recipe_offer_pending(ctx: Any, output_dir: str) -> dict | None:
    """取本会话待确认的事前固化询问（无则 None）。"""
    from app.services import recipe_offer_store
    try:
        return recipe_offer_store.pending(
            output_dir=output_dir, thread_id=str((ctx or {}).get("thread_id") or ""))
    except Exception:  # noqa: BLE001 - 询问不可用时不拦对话
        return None


def _recipe_offer_answer(text: str, ctx: Any, output_dir: str) -> dict | None:
    """消费事前固化询问的答复（2026-09-10）。

    返回 None = 没有待确认询问、或这句话不是在回答询问（调用方继续正常流程）；
    返回 dict = 已消费并**删掉**该询问，调用方按其继续：
    - `answer="solidify"`：照常跑，跑完正常固化（done 时出草稿卡）；
    - `answer="skip"`：照常跑，但抑制本次草稿卡。

    `intent` / `delivery_intent` 回放**询问时存下的原始任务文本**——用户这一句只是
    「要」/「直接跑」，不是任务本身；若拿它当 intent，真正的任务会整个丢失。
    """
    match = _RECIPE_OFFER_RE.match(text or "")
    if match is None:
        return None
    offer = _recipe_offer_pending(ctx, output_dir)
    if offer is None:
        return None
    from app.services import recipe_offer_store
    try:
        recipe_offer_store.delete(str(offer.get("id") or ""))
    except Exception:  # noqa: BLE001 - 删除失败也不阻断本次执行
        pass
    return {
        "answer": "solidify" if match.group(1) in _RECIPE_OFFER_YES else "skip",
        "intent": str(offer.get("intent") or ""),
        "delivery_intent": str(offer.get("delivery_intent") or ""),
    }


def _recipe_hard_replay(hit: dict, score: float, ctx: Any, output_dir: str,
                        trace: list[str]) -> dict | None:
    """命中同类配方时尝试**硬重放**（2026-09-10）。返回 dict=已投递并给出明示；None=不重放。

    只对「内容无关」的配方硬重放：固化把步骤参数原样存下，内容型参数（如
    `doc.create_repo` 的 content）也在里面——硬重放那样的配方只会把当时生成的内容原样
    再写一遍，本轮素材一变就写错了。这类配方改走**骨架复用**（不重放，交给自由循环按
    本次素材重新生成），由既有的配方清单提示词承担，这里只留一条 trace 便于排障。

    「明示 + 可反悔」：投递后如实告知命中了哪条、相似度多少，并指路下方计划卡的「取消」。
    """
    from app.services import recipe_match
    if recipe_match.replay_safety(hit) != "hard":
        run_trace.emit(ctx, "recipe.replay", status="skeleton",
                       recipe_id=hit.get("id"), score=round(score, 3))
        return None
    try:
        from app.services import plan_tasks as _pt
        task = _pt.instantiate_recipe(
            str(hit.get("id") or ""), output_dir=output_dir,
            repo_id=str(ctx.get("repo_id") or ""))
    except Exception as exc:  # noqa: BLE001 - 重放失败就退回自由循环正常编排
        run_trace.emit(ctx, "recipe.replay", status="error", error=str(exc))
        return None
    run_trace.emit(ctx, "recipe.replay", status="hit",
                   recipe_id=hit.get("id"), score=round(score, 3))
    return {
        "result_text": (
            f"检测到与已固化流程《{hit.get('name')}》同类"
            f"（相似度 {round(score * 100)}%），已整条重放，省去重新编排。\n"
            f"计划已投递：{str(task.get('intent') or '')[:120]}\n"
            "审批与配额照常生效，进度见后台活动面板。\n"
            "如果这不是你要的，就在下方计划卡点「取消」，再重发原指令即可。"),
        "trace": trace + [f"🧩 命中固化流程《{hit.get('name')}》，整条重放"]}


def _fabric_approval_word(word: str, ctx: Any, output_dir: str,
                          trace: list[str]) -> dict | None:
    """对话审批词处理 approval 自由循环 checkpoint（2026-09-06）。

    返回 dict=已处理（批准后续跑/取消）；None=没有待审批的自由循环 checkpoint。
    """
    from app.services import capability_sandbox as _sb, fabric_checkpoint, fabric_loop

    pending = fabric_checkpoint.pending(output_dir=output_dir)
    if not pending:
        return None
    cp = pending[0]
    if word in ("取消", "cancel", "撤销"):
        try:
            _sb.revoke(str(cp.get("lease_id") or ""))
        except Exception:  # noqa: BLE001
            pass
        fabric_checkpoint.delete(cp["id"])
        return {"result_text": f"已取消智能编造：{str(cp.get('intent'))[:120]}", "trace": trace}
    # 批准:追加租约授权 → 从断点续跑。
    # running 断点（step_limit/error 后）无 pending_tool，跳过授权直接续跑。
    _pending_tool = str(cp.get("pending_tool") or "")
    if _pending_tool:
        _lease_id = str(cp.get("lease_id") or "")
        try:
            _sb.grant_operation(_lease_id, _pending_tool, path=output_dir)
        except PermissionError as exc:
            # 2026-09-14 修复：断点停在审批点超过租约 TTL（24h）时租约已过期。
            # 用户此刻的批准=新的授权动作 → 先续期救活租约再追加授权，断点与
            # 已完成步骤保留（此前 grant 不查过期"成功"、续跑 authorize 再拦 →
            # 批准死循环；租约不在内存时则删断点丢进度）。
            if "已过期" in str(exc) and _sb.lease_registered(_lease_id):
                try:
                    _sb.renew(_lease_id)
                    _sb.grant_operation(_lease_id, _pending_tool, path=output_dir)
                except Exception as exc2:  # noqa: BLE001 - 续期/追加仍失败则中止
                    fabric_checkpoint.delete(cp["id"])
                    return {"result_text": f"审批失败：{exc2}", "trace": trace}
            else:
                # 租约不存在/已撤销：确实无法接续，删断点但必须明说出路
                fabric_checkpoint.delete(cp["id"])
                return {"result_text": f"审批失败：{exc}（断点已清理，请重新发起任务）",
                        "trace": trace}
        except Exception as exc:  # noqa: BLE001 - 其他异常同样中止
            fabric_checkpoint.delete(cp["id"])
            return {"result_text": f"审批失败：{exc}", "trace": trace}
    configured = {key for key, flag in (("chat", True), ("image", ctx.get("gen_base")),
                                        ("video", ctx.get("vid_base")), ("embed", ctx.get("embed_base")))
                  if flag}
    # 批准后续跑：追加一条「已批准」提示，让模型重新发起被拦操作（否则模型误以为
    # 已执行直接 done——2026-09-06 实锤）。已完成步骤保留不重跑。
    import json as _json
    resume_messages = list(cp.get("messages") or [])
    resume_messages.append({"role": "user", "content": _json.dumps(
        {"approval_granted": f"用户已批准操作「{cp.get('pending_tool') or ''}」。"
                             "请重新发起该操作执行（不要重复其他已完成步骤）。"},
        ensure_ascii=False)})
    # 2026-09-07 治本：续跑也要每步保存断点（重试的资本）——批准后进入新一轮
    # 自由循环，若不带 checkpoint_fn，期间 step_limit/error 时断点停在旧状态，
    # 甚至被误删，用户无法再接续（21:54 实锤：step_limit 后 checkpoint 被清空）。
    cp_id = str(cp.get("id") or "")
    cp_repo = str(cp.get("repo_id") or ctx.get("repo_id") or ctx.get("thread_id") or "")
    cp_intent = str(cp.get("intent") or "")
    cp_history = str(cp.get("history") or "")
    cp_lease = str(cp.get("lease_id") or "")
    # 续跑沿用启动时的判定文本（旧断点无此字段 → 回退 intent），保证能力面/前序产出
    # 档案与首次启动一致（2026-09-10）。
    cp_delivery = str(cp.get("delivery_intent") or cp_intent)

    def _cp_fn(step: int, msgs: list, steps: list, status: str) -> None:
        fabric_checkpoint.save({
            "id": cp_id, "status": status,
            "intent": cp_intent, "history": cp_history,
            "delivery_intent": cp_delivery,
            "messages": msgs, "steps": steps,
            "lease_id": cp_lease, "pending_tool": "",
            "output_dir": output_dir, "repo_id": cp_repo,
            "thread_id": str(ctx.get("thread_id") or ""),
        })

    args = _fabric_run_args(
        ctx, intent=cp_intent, history=cp_history,
        access_mode=_sb.ACCESS_APPROVAL, lease_id=cp_lease,
        output_dir=output_dir,
        repo_id=cp_repo,
        configured=configured, images=[],
        resume={"messages": resume_messages, "steps": cp.get("steps") or []},
        checkpoint_fn=_cp_fn,
        delivery_intent=cp_delivery,
    )
    outcome = fabric_loop.run_loop(**args)
    if outcome.status == "awaiting_approval":
        cp.update({
            "pending_tool": outcome.pending_tool,
            "messages": outcome.messages, "steps": outcome.steps,
            "status": "awaiting_approval",
        })
        fabric_checkpoint.save(cp)
        return {"result_text": f"智能编造需要批准：下一步将「{outcome.pending_tool}」。回复「批准」继续、「取消」中止。",
                "trace": trace + ["🧵 智能编造等待审批"]}
    if outcome.status == "done":
        # 仅完成清理断点；step_limit/error 保留 running 断点供用户再接续
        # （2026-09-07 实锤修复：此前无条件 delete，step_limit 后无法续跑）。
        fabric_checkpoint.delete(cp_id)
    else:
        fabric_checkpoint.save({
            "id": cp_id, "status": "running",
            "intent": cp_intent, "history": cp_history,
            "messages": outcome.messages, "steps": outcome.steps,
            "lease_id": cp_lease, "pending_tool": "",
            "output_dir": output_dir, "repo_id": cp_repo,
            "thread_id": str(ctx.get("thread_id") or ""),
        })
    return _fabric_finalize(outcome, cp_intent, trace, ctx, output_dir)


def _run_fabric_content_loop(ctx, state, *, intent_text, judge_text, text,
                             trace, output_dir, configured,
                             task_type: str = "") -> dict:
    """approval 模式内容生成/讨论型任务：fabric 自由循环断点审批。

    2026-09-11 自 doc 交付分支抽取：文档交付、设计讨论与「计划编译撞意图-步骤
    不一致」的智能降级共用同一循环（step_limit/error 自动续跑 ≤2 次，
    awaiting_approval 落断点）。task_type="design_discussion" 时按合同模板把
    最近灵感卡/历史附件/提示词规范注入讨论历史（上下文合同 P1）。
    """
    from app.services import capability_sandbox, fabric_checkpoint, fabric_loop
    lease = capability_sandbox.grant(
        subject=f"fabric:{ctx.get('thread_id') or ctx.get('message_id') or 'sess'}",
        capabilities=[], ttl_seconds=86400, approved_by="pending",
        mode=capability_sandbox.ACCESS_APPROVAL)
    history_text = _fabric_knowledge_history(ctx, text, task_type=task_type)
    cp_id = f"fabric:{ctx.get('thread_id') or ctx.get('message_id') or 'sess'}"
    repo_id = str(ctx.get("repo_id") or ctx.get("thread_id") or "")
    thread_id = str(ctx.get("thread_id") or "")
    resume: dict | None = None
    # 2026-09-07 重试的资本：启动前检查未完成断点（running/awaiting_approval），
    # 有则自动恢复，不从头重跑（中断/step_limit/error 后不再白跑）。
    # 匹配放宽：同会话（thread_id）有未完成断点即续跑——重发指令时文本略变
    # （加标点/换措辞）也能恢复，不被「intent 不一致」误判从头开始。
    _cp = fabric_checkpoint.resumable(output_dir=output_dir, thread_id=thread_id)
    if _cp is not None:
        resume = {"messages": list(_cp.get("messages") or []),
                  "steps": list(_cp.get("steps") or [])}
    def _cp_fn(step: int, msgs: list, steps: list, status: str) -> None:
        fabric_checkpoint.save({
            "id": cp_id, "status": status,
            "intent": intent_text, "history": history_text,
            "delivery_intent": judge_text,
            "messages": msgs, "steps": steps,
            "lease_id": lease["id"], "pending_tool": "",
            "output_dir": output_dir, "repo_id": repo_id, "thread_id": thread_id,
        })
    outcome: Any = None
    _auto = 0
    while True:
        outcome = fabric_loop.run_loop(**_fabric_run_args(
            ctx, intent=intent_text, history=history_text,
            access_mode=capability_sandbox.ACCESS_APPROVAL,
            lease_id=lease["id"], output_dir=output_dir,
            repo_id=repo_id, configured=configured,
            images=list(state.get("images") or []),
            resume=resume, checkpoint_fn=_cp_fn,
            delivery_intent=judge_text,
        ))
        if outcome.status in ("step_limit", "error") and _auto < 2:
            _cp2 = fabric_checkpoint.resumable(output_dir=output_dir, thread_id=thread_id)
            if _cp2 is None:
                break
            _auto += 1
            resume = {"messages": list(_cp2.get("messages") or []),
                      "steps": list(_cp2.get("steps") or [])}
            run_trace.emit(ctx, "fabric.auto_resume", round=_auto,
                           status=outcome.status, steps=len(outcome.steps))
            continue
        break
    if outcome.status == "awaiting_approval":
        fabric_checkpoint.save({
            "id": cp_id, "status": "awaiting_approval",
            "intent": intent_text, "history": history_text,
            "delivery_intent": judge_text,
            "messages": outcome.messages, "steps": outcome.steps,
            "lease_id": lease["id"], "pending_tool": outcome.pending_tool,
            "output_dir": output_dir, "repo_id": repo_id, "thread_id": thread_id,
        })
        return {"result_text": f"智能编造需要批准：下一步将「{outcome.pending_tool}」。"
                               f"回复「批准」继续、「取消」中止。",
                "trace": trace + ["🧵 智能编造等待审批"]}
    # 2026-09-07 重试资本：仅「完成」清理断点；step_limit/error 保留 running 断点，
    # 用户点「批准继续」可从断点续跑（不再需要重新发完整指令）。
    if outcome.status == "done":
        fabric_checkpoint.delete(cp_id)
    return _fabric_finalize(outcome, intent_text, trace, ctx, output_dir)


_RECIPE_OFFER_YES = ("要", "固化", "固化它", "要固化", "固化流程")


_RECIPE_OFFER_NO = ("直接跑", "不用", "跳过", "不固化", "直接处理")


_RECIPE_OFFER_RE = re.compile(
    r"^\s*(" + "|".join(_RECIPE_OFFER_YES + _RECIPE_OFFER_NO) + r")\s*$",
    flags=re.IGNORECASE)
