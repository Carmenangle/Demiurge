"""上下文利用判断机制（context_gate）回归测试。

背景（2026-09-06 驱动测试实锤）：历史全量无脑平铺，20 多条生图历史淹没
「小说转合集卡」指令，模型把合集卡任务编排出生图步骤。本测试锁定分档逻辑：
同域历史保留全文、跨域历史压缩成一行摘要、当前指令永远最高锚点、
意图域未识别时退回原全量拼接（不误伤依赖上文的短指令）。
"""
from app.services import context_gate as gate


def _h(role: str, content: str) -> dict:
    return {"role": role, "content": content}


# ── 意图域判定 ──


def test_domain_of_card_delivery():
    src = "根据这本小说制作合集卡，把全局机制、系统判定机制、局部机制、地理、势力、体系、重大事件、角色等内容都要整理好"
    assert gate.domain_of(src) == "card"


def test_domain_of_image_generation():
    assert gate.domain_of("按这个模板出图，14 套穿搭，Krea2 模板 + QRQ LoRA") == "image"


def test_domain_of_doc():
    assert gate.domain_of("把这些内容整理成一份文档归档") == "doc"


def test_domain_of_short_continuation_is_blank():
    # 短指令依赖上文，不启用强分档（退回原全量拼接）
    assert gate.domain_of("继续") == ""
    assert gate.domain_of("") == ""


# ── 分档 ──


def test_classify_card_intent_keeps_card_history_summarizes_image_history():
    history = [
        _h("user", "按这个模板出图，14 套穿搭，Krea2 模板 + QRQ LoRA，红色丝绒长裙"),
        _h("assistant", "已生成 14 张图，预览如下，参数见各图"),
        _h("user", "根据这本小说制作合集卡，把全局机制、系统判定机制、局部机制、地理、势力、体系、重大事件、角色等内容都要整理好"),
    ]
    items = gate.classify_history_items(history, "根据这本小说制作合集卡")
    tiers = [t for _, t in items]
    # 最后一条用户消息 = 当前指令锚点 → keep
    assert tiers[-1] == gate.KEEP
    # 生图历史跨域 → summary（不淹没当前指令）
    assert tiers[0] == gate.SUMMARY
    assert tiers[1] == gate.SUMMARY


def test_classify_image_intent_keeps_image_history():
    history = [
        _h("user", "把《玫瑰与繁花》制作成合集卡，整理全局机制"),
        _h("assistant", "已完成合集卡"),
        _h("user", "按这个模板出图，14 套穿搭"),
    ]
    items = gate.classify_history_items(history, "按这个模板出图，14 套穿搭")
    tiers = [t for _, t in items]
    assert tiers[0] == gate.SUMMARY  # 卡任务历史跨域压缩
    assert tiers[1] == gate.SUMMARY
    assert tiers[2] == gate.KEEP  # 当前生图指令锚点保留全文


def test_classify_blank_domain_only_clips_oversized():
    history = [_h("user", "好的"), _h("assistant", "x" * 2000)]
    items = gate.classify_history_items(history, "继续")
    tiers = [t for _, t in items]
    assert tiers[0] == gate.KEEP  # 短消息保留
    assert tiers[1] == gate.CLIP  # 超长消息截断


# ── 拼装输出 ──


def test_history_text_gated_compresses_image_history_and_anchors_current():
    """真实装配形状（实锤修复后）：历史不含本轮消息，本轮指令经 anchor_text 显式传入。"""
    ctx = {"history": [
        _h("user", "按这个模板出图，14 套穿搭，Krea2 模板 + QRQ LoRA，红色丝绒长裙，站姿全身，黄昏光线，氛围感背景，电影级打光，全身镜头"),
        _h("assistant", "已生成 14 张图，预览如下，参数见各图"),
    ]}
    intent = "根据这本小说制作合集卡，把全局机制、系统判定机制、局部机制、地理、势力、体系、重大事件、角色等内容都要整理好"
    out = gate.history_text_gated(ctx, intent, anchor_text=intent)
    assert "【最近对话" in out
    assert "当前指令" in out and "最高优先级" in out
    # P1 聚合（2026-09-06 交接）：跨域历史按主题聚合成一段，不再逐条单行摘要
    assert "[跨域聚合·生图]" in out
    assert "此前为『生图』类任务 ×2" in out
    # 摘要只保留头部主题线索，尾部细节被压缩，不再淹没当前指令
    assert "电影级打光" not in out
    assert "全身镜头" not in out
    # 当前指令锚点：第一层是本轮指令全文（显式传入，非历史最后一条）
    assert "[当前指令·最高优先级] 用户：根据这本小说制作合集卡" in out
    assert out.index("[当前指令·最高优先级]") < out.index("[跨域聚合·生图]")


def test_history_text_gated_aggregates_cross_domain_history_by_topic():
    """P1 核心（2026-09-06 交接）：多条跨域历史 → 一段主题聚合摘要，不逐条展开。"""
    ctx = {"history": [
        _h("user", "按这个模板出图，14 套穿搭，Krea2 模板 + QRQ LoRA，红色丝绒长裙，站姿全身，黄昏光线，氛围感背景，电影级打光，全身镜头"),
        _h("assistant", "已生成 14 张图，预览如下，参数见各图"),
        _h("user", "再出一组冬季穿搭，同模板，雪景背景，逆光剪影，低机位"),
    ]}
    intent = "根据这本小说制作合集卡，把全局机制、体系、角色都整理好"
    out = gate.history_text_gated(ctx, intent, anchor_text=intent)
    assert "[跨域聚合·生图]" in out
    assert "此前为『生图』类任务 ×3" in out
    # 一段聚合（不逐条单行）
    assert out.count("细节从略，与当前任务无关") == 1
    assert "涉及主题：" in out
    # 头部主题线索保留、尾部细节词消失
    assert "按这个模板出图" in out
    assert "电影级打光" not in out
    assert "逆光剪影" not in out
    # 分层：当前指令第一层 → 跨域聚合其后
    assert out.index("[当前指令·最高优先级]") < out.index("[跨域聚合·生图]")


def test_history_text_gated_layered_sections_keep_order():
    """分层顺序：当前指令 → 同域 keep 逐条 → 跨域聚合 → 截断。"""
    ctx = {"history": [
        _h("user", "x" * 500),  # clip：无域特征且超长
        _h("user", "按这个模板出图，14 套穿搭"),  # 跨域 summary
        _h("user", "把《玫瑰与繁花》制作成合集卡，整理全局机制"),  # 同域 keep（上一轮指令）
        _h("assistant", "合集卡已生成，含机制整理"),  # 同域 keep
    ]}
    intent = "根据这本小说制作合集卡，把体系、角色都整理好"
    out = gate.history_text_gated(ctx, intent, anchor_text=intent)
    i_anchor = out.index("[当前指令·最高优先级]")
    i_keep = out.index("[相关·保留]")
    i_agg = out.index("[跨域聚合·生图]")
    i_clip = out.index("[截断]")
    assert i_anchor < i_keep < i_agg < i_clip
    assert "此前为『生图』类任务 ×1" in out
    assert "把《玫瑰与繁花》制作成合集卡" in out  # 上一轮同域指令 keep 全文（普通历史层）


def test_history_text_gated_empty_history():
    assert gate.history_text_gated({"history": []}, "制作合集卡") == ""


def test_history_text_gated_falls_back_when_domain_unknown():
    # 短指令「继续」不识别意图域 → 退回原全量拼接（不丢上下文）
    ctx = {"history": [_h("user", "上一轮我们确定了女主角的设定"), _h("assistant", "收到")]}
    out = gate.history_text_gated(ctx, "继续")
    assert "上一轮我们确定了女主角的设定" in out
    assert "[已压缩" not in out


# ── P3①：语义相关度增强分档（2026-09-06 交接路线；embedding 可选，失败退回词表）──


def test_语义高相似把clip档升级keep():
    """无词表命中（clip）但语义与当前指令高度相似 → 升级 keep（只升不降）。"""
    history = [
        _h("user", "上次讨论的项目背景设定要点整理"),  # 无任何词表命中 → clip
        _h("user", "今天天气不错出去走走"),  # 无命中且不相似 → 保持 clip
        _h("user", "根据这本小说制作合集卡，把体系、角色都整理好"),  # 锚点
    ]

    def embed_fn(texts):
        table = {
            "根据这本小说制作合集卡": [1.0, 0.0],
            "上次讨论的项目背景设定要点整理": [0.99, 0.1],
            "今天天气不错出去走走": [0.0, 1.0],
        }
        return [table.get(t, [0.0, 1.0]) for t in texts]

    items = gate.classify_history_items(history, "根据这本小说制作合集卡", embed_fn=embed_fn)
    tiers = [t for _, t in items]
    assert tiers[0] == gate.KEEP  # 语义高相似升级
    assert tiers[1] == gate.CLIP  # 不相似不升级


def test_语义高相似把跨域summary也升级keep():
    history = [
        _h("user", "按这个模板出图，14 套穿搭"),  # 生图词表 → summary
        _h("user", "根据这本小说制作合集卡，把体系、角色都整理好"),  # 锚点
    ]

    def embed_fn(texts):
        # 生图历史与卡指令语义高度相似（如为合集卡做封面图）；数量与请求一致
        table = {"根据这本小说制作合集卡": [1.0, 0.0],
                 "按这个模板出图，14 套穿搭": [1.0, 0.05]}
        return [table.get(t, [0.0, 1.0]) for t in texts]

    items = gate.classify_history_items(history, "根据这本小说制作合集卡", embed_fn=embed_fn)
    assert [t for _, t in items] == [gate.KEEP, gate.KEEP]


def test_embed_fn异常静默退回词表分档():
    history = [
        _h("user", "按这个模板出图，14 套穿搭"),
        _h("user", "根据这本小说制作合集卡，把体系、角色都整理好"),
    ]

    def broken_embed_fn(texts):
        raise ConnectionError("ollama down")

    out = gate.history_text_gated(history and {"history": history}, "根据这本小说制作合集卡", embed_fn=broken_embed_fn)
    assert "[跨域聚合·生图]" in out  # 词表行为不变
    assert "此前为『生图』类任务 ×1" in out


def test_无embed_fn行为与词表完全一致():
    history = [
        _h("user", "按这个模板出图，14 套穿搭，Krea2 模板"),
        _h("user", "根据这本小说制作合集卡，把体系、角色都整理好"),
    ]
    assert (gate.history_text_gated({"history": history}, "根据这本小说制作合集卡")
            == gate.history_text_gated({"history": history}, "根据这本小说制作合集卡", embed_fn=None))


def test_cosine_零向量与维度不等返回0():
    assert gate._cosine([0.0, 0.0], [1.0, 1.0]) == 0.0
    assert gate._cosine([1.0], [1.0, 0.0]) == 0.0
    assert abs(gate._cosine([1.0, 0.0], [1.0, 0.0]) - 1.0) < 1e-9


def test_上一轮生图指令不冒充当前指令_实锤回归():
    """用户四步实锤（2026-09-06）：装配回路历史不含本轮消息——上一轮生图、本轮合集卡时，
    上一轮生图指令曾被误标「当前指令·最高优先级」拿全场最高权重（方向反向放大）。"""
    ctx = {"history": [
        _h("user", "按这个模板出图，14 套穿搭，Krea2 模板 + QRQ LoRA"),
        _h("assistant", "已生成 14 张图，预览如下"),
        _h("user", "再出一组冬季穿搭"),
        _h("assistant", "已生成 8 张"),
    ]}
    intent = "根据这本小说制作合集卡，把机制、角色都整理好"
    out = gate.history_text_gated(ctx, intent, anchor_text=intent)
    # 第一层有且仅有一个「当前指令」= 本轮合集卡全文
    assert out.count("当前指令·最高优先级") == 1
    assert "[当前指令·最高优先级] 用户：根据这本小说制作合集卡" in out
    # 上一轮生图指令（含历史最后一条 user 与 assistant 回复）进跨域聚合压缩，
    # 不进第一层、不标当前指令
    assert "此前为『生图』类任务 ×3" in out
    assert out.index("[当前指令·最高优先级]") < out.index("[跨域聚合·生图]")
    assert "[相关·保留] 用户：再出一组冬季穿搭" not in out
    # 反向对照：不传 anchor_text（旧调用方兼容路径）时，真实装配形状（末条=assistant）
    # 下 fallback 根本产生不了锚点——印证显式锚点是唯一正解
    legacy = gate.history_text_gated(ctx, "再出一组冬季穿搭")
    assert "当前指令·最高优先级] 用户" not in legacy
    # fallback 分支本身仍保留：历史以 user 结尾的调用方，末条 user 仍当锚点
    legacy2 = gate.history_text_gated({"history": [
        _h("assistant", "好的"),
        _h("user", "再出一组冬季穿搭"),
    ]}, "再出一组冬季穿搭")
    assert "[当前指令·最高优先级] 用户：再出一组冬季穿搭" in legacy2


def test_embed部分成功返回数不足时整体跳过语义升级():
    """审计残留观察 #2：embed 部分成功（返回数 < 请求数）会让中间条目错位——
    整体跳过语义升级，退回词表分档（绝不错位升级）。"""
    history = [
        _h("user", "按这个模板出图，14 套穿搭"),  # 词表 → summary（不得被错位升级）
        _h("user", "根据这本小说制作合集卡，把体系、角色都整理好"),  # 锚点（fallback 路径）
    ]

    def partial_embed(texts):  # 请求 3 条只回 2 条
        return [[1.0, 0.0], [1.0, 0.0]]

    items = gate.classify_history_items(
        history, "根据这本小说制作合集卡", embed_fn=partial_embed)
    assert [t for _, t in items] == [gate.SUMMARY, gate.KEEP]  # 词表结果原样，无错位
