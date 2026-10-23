"""T1.2 特征测试：`plan_compiler_node`（419 行）函数级拆解的行为锚 + 源码形态锁。

拆解方法论（真源 `docs/memory/agent-graph-seam-plan-2026-09-16.md` §4.1）：
- **等价矩阵**：把改造前的内联判定原样抄成 `_legacy_*` 参照实现，断言新抽出的纯判定
  接缝在输入网格上与其**逐点相等** ⇒「零行为变化」可证伪，不靠读代码相信。
- **源码形态锁**：行为网**抓不到分层/形态回退**（把接缝内联回去照样全绿）⇒ 另加断言
  「主函数确实由接缝组装」+「已降到目标行数以内」+「每个接缝自主函数**可达**（无死接缝 / 无内联回退）」。

⚠ `_legacy_*` 是本文件对改造前写法的**逐字抄录**（2026-09-16，自
`agent_graph.plan_compiler_node` 内联段），不得顺手"优化"——它是等价性的唯一参照。

⚠ 本函数拆解前做过「**就地覆写变量**」清查（T1.1 教训），结论：
- `text` 在「固化询问答复轮」被就地覆写为 `intent_text`（原 L711），此后仍被多处读取
  ⇒ 该覆写**必须留在主函数**（下游读的必须是覆写后的值）；接缝 `_pc_judge_text` 只吃
  `intent_text`、只吐判定文本，不碰 `text`。
- `trace` 有三处 `trace = trace + [...]` 重新绑定：①答复轮那次留在主函数（下游继续读）；
  ②设计讨论那次在接缝 `_pc_design_discussion_reply` 内，但该接缝**立即 return 结果**
  （新 trace 已嵌在返回的节点结果里）；③`_pc_compile_and_finalize` 内部同理
  ⇒ 两处均等价，无需把 trace 单独回传。
- `_pc_collect_attachments` / `_pc_prev_batch_and_flow_hint` 以**就地 mutate** 传入的
  `attachments` 列表传递结果 ⇒ 异常路径下「已收集到的部分」仍对下游可见，与改造前的
  局部变量绑定语义一致（若改成「返回新列表」，异常时下游会读到空列表 ⇒ 静默语义漂移）。
"""

import inspect
import itertools
import re

from app.services import agent_graph as ag
from app.services import agent_graph_plan as agp  # plan 域新属主（2026-10-23 方案 B 再拆）
from app.services import plan_compiler as pm


# ---------------------------------------------------------------------------
# legacy* 参照实现：改造前内联判定（逐字抄自 plan_compiler_node）
# ---------------------------------------------------------------------------
def _legacy_approval_word(text):
    """逐字抄自 plan_compiler_node L661-665（正则 + group 归一）。"""
    m = re.match(r"^\s*(批准|同意|执行|确认|approve|ok|好|取消|cancel|撤销)\s*$",
                 text, flags=re.IGNORECASE)
    if not m:
        return None
    return m.group(1).lower()


def _legacy_configured_models(ctx):
    """逐字抄自 plan_compiler_node L653-657。"""
    return {
        key for key, flag in (("chat", True), ("image", ctx.get("gen_base")),
                              ("video", ctx.get("vid_base")), ("embed", ctx.get("embed_base")))
        if flag
    }


def _legacy_judge_text(intent_text, history):
    """逐字抄自 plan_compiler_node L712-717（含 history 前插循环与 strip）。"""
    judge = intent_text
    if not pm.is_doc_delegation_intent(judge):
        for h in (history or []):
            if isinstance(h, dict) and h.get("role") == "user":
                judge = str(h.get("content") or "") + "\n" + judge
        judge = judge.strip()
    return judge


# ---------------------------------------------------------------------------
# 等价矩阵
# ---------------------------------------------------------------------------
def test_approval_word等价矩阵():
    """审批词正则 + group(1).lower() 的等价性（含大小写/空白/近似负例）。"""
    candidates = (
        "批准", "同意", "执行", "确认", "取消", "撤销", "好", "ok", "OK", "Ok",
        "approve", "Approve", "APPROVE", "cancel", "Cancel", "CANCEL",
        "  批准  ", "\t取消\n", "大概批准", "批准一下", "好的", "no", "", "   ",
        "执行! ", "？", "批准。", "同 意",
    )
    checked = 0
    for text in candidates:
        assert ag._pc_approval_word(text) == _legacy_approval_word(text), text
        checked += 1
    assert checked == len(candidates)
    # 网格必须真正踩到命中与不命中两侧，否则等价矩阵是空转
    hits = {t for t in candidates if _legacy_approval_word(t) is not None}
    assert hits, "网格未覆盖「命中」侧"
    assert len(hits) < len(candidates), "网格未覆盖「不命中」侧"


def test_configured_models等价矩阵():
    """三模态开关集合的等价性（chat 恒在；image/video/embed 为真值判定）。"""
    grid = itertools.product((None, "", "http://x"), (None, "", "http://v"),
                             (None, "", "http://e"))
    checked = 0
    for gen, vid, emb in grid:
        ctx = {}
        if gen is not None:
            ctx["gen_base"] = gen
        if vid is not None:
            ctx["vid_base"] = vid
        if emb is not None:
            ctx["embed_base"] = emb
        assert ag._pc_configured_models(ctx) == _legacy_configured_models(ctx), ctx
        checked += 1
    assert checked == 3 * 3 * 3
    # 「全空」必须只剩 chat；「全真」必须四个都在
    assert _legacy_configured_models({}) == {"chat"}
    assert _legacy_configured_models(
        {"gen_base": "g", "vid_base": "v", "embed_base": "e"}) == {
        "chat", "image", "video", "embed"}


def test_judge_text等价矩阵():
    """判定文本拼接的等价性（history 前插顺序 + doc_delegation 短路 + strip）。"""
    intent_texts = ("", "  ", "整理成设定总集", "根据提示词文档分批生成图片",
                    "跟我讨论各服装的生图提示词", "这是问句吗？")
    histories = (
        None,
        [],
        [{"role": "user", "content": "第一句"}],
        [{"role": "user", "content": "第一句"}, {"role": "user", "content": "第二句"}],
        [{"role": "assistant", "content": "助手话"}, {"role": "user", "content": "用户话"}],
        [{"role": "user", "content": None}],
        [{"role": "user"}],
        ["不是 dict"],
        [{"role": "user", "content": "  "}],
    )
    checked = 0
    doc_branch = set()
    for intent, hist in itertools.product(intent_texts, histories):
        got = ag._pc_judge_text(intent, hist)
        assert got == _legacy_judge_text(intent, hist), (intent, hist)
        doc_branch.add(pm.is_doc_delegation_intent(intent))
        checked += 1
    assert checked == len(intent_texts) * len(histories)
    # doc_delegation 与 非 doc_delegation 两条路径都必须被踩到（否则前插循环没被验证）
    assert doc_branch == {False, True}, doc_branch


def test_judge_text前插顺序被钉住():
    """显式钉住「history 是逐条前插」这一反直觉顺序（后出现的用户消息更靠前）。

    这条不是等价矩阵能替代的——它把「语义」本身写死，防止将来把前插误"修"成追加。
    """
    got = ag._pc_judge_text("本轮", [
        {"role": "user", "content": "A"},
        {"role": "user", "content": "B"},
    ])
    assert got == "B\nA\n本轮"


# ---------------------------------------------------------------------------
# 源码形态锁
# ---------------------------------------------------------------------------
_SEAMS = (
    # 前置：配置集合 / 审批词 / 判定文本（纯判定）
    "_pc_configured_models",
    "_pc_approval_word",
    "_pc_approval_word_reply",
    "_pc_judge_text",
    # 分支：固化询问 / full 自由循环 / 委派登记 / 设计讨论
    "_pc_ask_recipe_offer",
    "_pc_run_full_loop",
    "_pc_register_delegation",
    "_pc_design_discussion_reply",
    # 尾部：编译上下文预读（附件收集 + 上一批/流程提示） / 编译+落盘+投递
    "_pc_collect_attachments",
    "_pc_prev_batch_and_flow_hint",
    "_pc_compile_and_finalize",
)

_TARGET_LINES = 160


def _calls(src: str, name: str) -> bool:
    """src 里是否出现对 name 的调用（名称后必须紧跟 `(`，防前缀名误命中）。"""
    return re.search(rf"(?<![\w]){re.escape(name)}\(", src) is not None


def test_拆解接缝齐备且同属主():
    for name in _SEAMS:
        fn = getattr(ag, name, None)
        assert callable(fn), f"接缝缺失：{name}"
        assert inspect.getsourcefile(fn) == agp.__file__, f"{name} 不在 agent_graph_plan 属主内"


def test_plan_compiler主函数已降到目标行数():
    src = inspect.getsource(ag.plan_compiler_node)
    body_lines = src.count("\n") + 1
    assert body_lines <= _TARGET_LINES, (
        f"plan_compiler_node 仍为 {body_lines} 行（目标 ≤{_TARGET_LINES}）——拆解疑似回退"
    )


def test_主函数由接缝组装_全部接缝自plan_compiler可达():
    """调用传递闭包：从 `plan_compiler_node` 出发，每个接缝都必须可达。

    一条断言同时锁住三件事（比「逐个列名 + 叶子豁免」更严，且不误伤叶子接缝）：
    ① **组装**——主函数必须真的调用接缝，否则一个都不可达；
    ② **无内联回退**——某段被抄回主函数后，对应接缝不再被任何人调用 ⇒ 不可达；
    ③ **无死接缝**——接缝抽出来却没人用 ⇒ 不可达。
    ⚠ `_pc_approval_word` 这类**叶子接缝**由其它接缝调用（非主函数直调），本锁天然覆盖，
    无需另设豁免名单（豁免名单会让「主函数未直调」这类误判重新出现）。
    """
    seen = set()
    frontier = ["plan_compiler_node"]
    while frontier:
        cur = frontier.pop()
        if cur in seen:
            continue
        seen.add(cur)
        fn = getattr(ag, cur, None)
        if fn is None:
            continue
        csrc = inspect.getsource(fn)
        for n in _SEAMS:
            if n not in seen and _calls(csrc, n):
                frontier.append(n)
    unreachable = [n for n in _SEAMS if n not in seen]
    assert not unreachable, f"接缝从主函数不可达（疑似内联回退 / 死接缝）：{unreachable}"
