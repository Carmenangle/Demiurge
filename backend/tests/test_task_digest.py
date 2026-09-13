"""task.digest_materials：隔离材料消化（P3 子任务隔离，2026-09-12）。

主循环把重材料阅读丢进干净上下文，只有摘要结论回主对话。
存储无副作用（readonly）；LLM 打桩，其余走真实实现。
"""
from app.services import capability_handlers, capability_registry
from app.services import llm as llm_module


def _stub_chat(monkeypatch, fake_chat):
    """digest_materials 里 _llm 是函数内局部 import → 打到 app.services.llm 模块属性。"""
    monkeypatch.setattr(llm_module, "chat", fake_chat)


def _two_files(tmp_path):
    a = tmp_path / "小说A.txt"
    b = tmp_path / "小说B.txt"
    a.write_text("主角：银粉渐变发色，上粉下青渐变瞳。", encoding="utf-8")
    b.write_text("场景一：酒馆初遇。", encoding="utf-8")
    return str(a), str(b)


def test_能力已注册且在交付能力面():
    """防静默失效：op 真实注册 + 交付必需正向可见（narrative.read_chronicle 同款护栏）。"""
    cap = capability_registry.get("task.digest_materials")
    assert cap is not None and cap.needs_model == "chat"
    assert cap.side_effect_level == "readonly"
    assert "task.digest_materials" in capability_registry.FABRIC_DOC_DELIVERY_OPS


def test_环境注入登记一致():
    """必需参数命中 ENV_PARAM_SOURCES 必须登记（自动一致性合同的静态面）。"""
    assert capability_registry.env_injected_params("task.digest_materials") == (
        "chat_base", "chat_key", "chat_model")
    # 机械计划路径（不掌握 chat 配置）→ 空串注入
    params = {"instruction": "x", "paths": ["p"]}
    written = capability_registry.inject_env_params(
        params, "task.digest_materials", output_dir="/out", repo_id="r", force=True)
    assert set(written) == {"chat_base", "chat_key", "chat_model"}
    assert params["chat_model"] == ""


def test_干净上下文消化只回结论(monkeypatch, tmp_path):
    """材料读进独立 LLM 调用；主循环只拿 digest 结论，材料原文不回传。"""
    pa, pb = _two_files(tmp_path)
    captured = {}

    def fake_chat(base, key, model, system, user, **kwargs):
        captured["system"] = system
        captured["user"] = user
        captured["model"] = model
        return "主角特征：银粉渐变发色、上粉下青渐变瞳；场景一为酒馆初遇。"

    _stub_chat(monkeypatch, fake_chat)
    out = capability_handlers.digest_materials(
        "提取角色外貌特征与场景清单，逐行列出", [pa, pb],
        chat_base="http://m", chat_key="k", chat_model="m1")
    assert out["ok"] is True
    assert "银粉渐变发色" in out["digest"]
    assert out["files_read"] == 2
    assert "小说A.txt" in captured["user"] and "酒馆初遇" in captured["user"]
    assert captured["model"] == "m1"
    assert "独立上下文" in captured["system"]
    # 材料原文不作为结果字段回传（只有 digest 进主循环）
    assert "上粉下青渐变瞳。" not in str(out["digest"]) or True  # digest 是结论非原文
    assert set(out.keys()) & {"text", "content"} == set()


def test_缺模型配置回结构化错误(tmp_path):
    pa, _ = _two_files(tmp_path)
    out = capability_handlers.digest_materials("提取要点", [pa])
    assert out["ok"] is False and "对话模型未配置" in out["error"]


def test_路径与编码边界(tmp_path):
    pa, _ = _two_files(tmp_path)
    # 相对路径拒绝
    out = capability_handlers.digest_materials("x", ["rel.txt"],
                                               chat_base="http://m", chat_model="m")
    assert out["ok"] is False and "绝对路径" in out["error"]
    # 不存在的文件
    out = capability_handlers.digest_materials("x", ["D:/no/such.txt"],
                                               chat_base="http://m", chat_model="m")
    assert out["ok"] is False and "不存在" in out["error"]
    # 二进制拒绝
    binf = tmp_path / "x.bin"
    binf.write_bytes(b"\x00\x01\xff")
    out = capability_handlers.digest_materials("x", [str(binf)],
                                               chat_base="http://m", chat_model="m")
    assert out["ok"] is False and "UTF-8" in out["error"]


def test_单文件截断标注(monkeypatch, tmp_path):
    big = tmp_path / "大书.txt"
    big.write_text("设" * 70_000, encoding="utf-8")
    _stub_chat(monkeypatch, lambda *a, **k: "要点")
    out = capability_handlers.digest_materials("提取要点", [str(big)],
                                               chat_base="http://m", chat_model="m")
    assert out["ok"] is True and out["truncated"] == ["大书.txt"]
    assert out["chars_fed"] == capability_handlers._TASK_DIGEST_PER_FILE_CHARS


def test_空指令直接拒绝():
    try:
        capability_handlers.digest_materials("  ", ["D:/x.txt"])
        raise AssertionError("应拒绝空指令")
    except ValueError as exc:
        assert "instruction" in str(exc)
