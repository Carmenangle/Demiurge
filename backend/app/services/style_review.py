"""S2 活人感通审（LLM）：确定性 lint 抓不到的整体感（讨好腔通篇/节拍/口语化/细节支撑）
由通审模型兜底，走正文发出后的维护通道（与纪要/Curator 同队列，不新开并行通道）。

采样制：cfg.review_every 控制频率（0=关闭，默认每 5 轮一次），不占正文额度；
失败静默降级只记 trace，永不阻断正文或维护流程。诊断并入 Narrative CI 诊断流
（code=prose_style.CODE_STYLE_LIVING_REVIEW），处置复用既有 RESOLUTIONS 生命周期。
"""
from __future__ import annotations

from typing import Any, Callable

from app.services import narrative_ci, prose_style, structured_output
from app.services.structured_contracts import BaseModel, Field


class StyleReview(BaseModel):
    """通审结构化判定：只按清单核对，不自由发挥；诊断腔同样受「无套路句式」约束。"""
    alive_score: int = Field(ge=0, le=100, description="活人感 0-100")
    opening_specificity: str = ""   # 开头是否具体（vs 套路景语开场）
    rhythm: str = ""                # 节奏观感（长短交错 vs 均匀节拍）
    colloquial: str = ""            # 对白/叙述口语化程度
    detail_support: str = ""        # 关键描写是否有具体细节支撑（vs 空洞概括）
    summary: str = ""               # 中文综述：最大的一两个问题 + 证据短句


_REVIEW_SYSTEM = (
    "你是剧情正文通审。通读整段正文，按四个维度核对并给出活人感评分：\n"
    "1) 开头是否具体（直接进人物/动作/场景细节，而非「夜色深沉/阳光透过」式套路景语）；\n"
    "2) 节奏（句长长短交错 vs 节拍器般均匀）；\n"
    "3) 口语化（对白像真人说话 vs 书面腔）；\n"
    "4) 细节支撑（关键描写有具体细节，而非空洞概括堆形容词）。\n"
    "要求：你的输出本身不得使用套路句式、破折号连击、自问自答；只写具体观察与证据。"
)

_REVIEW_USER = "【本轮正文】\n{text}\n\n请输出通审 JSON。"

_MIN_REVIEW_LEN = 120  # 过短正文不值得一次通审调用


def should_review(cfg: dict | None, *, turn: int, text_len: int) -> bool:
    """采样闸门：总开关开 + review_every>0 + 到轮 + 正文足够长。"""
    config = cfg or {}
    if not config.get("enabled", True):
        return False
    every = int(config.get("review_every", 5) or 0)
    if every <= 0 or turn <= 0 or turn % every != 0:
        return False
    return text_len >= _MIN_REVIEW_LEN


def maybe_review(*, cfg: dict | None, text: str, turn: int,
                 output_dir: str, repo_id: str,
                 chat_base: str, chat_key: str, chat_model: str,
                 chat_fn: Callable | None, structured_chat_fn: Callable | None = None,
                 proxy_kwargs: dict[str, Any] | None = None,
                 trace: Callable | None = None) -> bool:
    """到采样轮且通过闸门时调一次通审并把综合诊断写入 Narrative CI。返回是否真的调了。"""
    body = prose_style.restore_jailbreak(text or "")
    if not should_review(cfg, turn=turn, text_len=len(body)):
        if trace is not None:
            trace("style_review", status="skipped", turn=turn)
        return False
    call_args = (chat_base, chat_key, chat_model, _REVIEW_SYSTEM,
                 _REVIEW_USER.format(text=body))
    call_kwargs: dict[str, Any] = {"temperature": 0.3, **(proxy_kwargs or {})}
    try:
        result = structured_output.invoke(
            StyleReview,
            native=(lambda: structured_chat_fn(*call_args, schema=StyleReview, **call_kwargs))
            if callable(structured_chat_fn) else None,
            legacy=lambda: chat_fn(*call_args, **call_kwargs),
            trace=trace,
        )
    except Exception as exc:  # noqa: BLE001 - 通审失败静默降级
        if trace is not None:
            trace("style_review", status="error", error=str(exc))
        return True
    review = result.value
    evidence = f"开头:{review.opening_specificity or '—'}；节奏:{review.rhythm or '—'}；" \
               f"口语化:{review.colloquial or '—'}；细节:{review.detail_support or '—'}"
    diagnostic = narrative_ci._diagnostic(
        turn, prose_style.CODE_STYLE_LIVING_REVIEW,
        f"活人感通审：{review.alive_score}/100。{(review.summary or '').strip()}",
        evidence, "style_review", "info",
    )
    saved = narrative_ci.save(output_dir, repo_id, [diagnostic])
    if trace is not None:
        trace("style_review", status="ok", alive_score=review.alive_score, saved=saved)
    return True
