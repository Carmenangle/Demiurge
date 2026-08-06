import json

from langchain_core.messages import AIMessage

from app.services import edit_agent, project_files


def test_校验工具读取真实作品文件并记录结果(monkeypatch, tmp_path):
    events = []
    monkeypatch.setattr(
        edit_agent.run_trace, "emit",
        lambda _ctx, event, **data: events.append((event, data)),
    )
    project_files.write_text(tmp_path, "角色卡/院长/card.json", json.dumps({
        "spec": "chara_card_v2", "name": "塞西莉亚",
    }, ensure_ascii=False))
    tools = {item.name: item for item in edit_agent._tools({}, tmp_path)}

    result = json.loads(tools["validate_project_file"].invoke({
        "path": "角色卡/院长/card.json", "artifact_type": "character_card",
    }))

    assert result["valid"] is True
    assert result["name"] == "塞西莉亚"
    assert any(event == "edit.validation_succeeded" for event, _ in events)


def test_外部迁移工具写入Demiurge角色卡且默认不覆盖(monkeypatch, tmp_path):
    events = []
    monkeypatch.setattr(
        edit_agent.run_trace, "emit",
        lambda _ctx, event, **data: events.append((event, data)),
    )
    project_files.write_text(tmp_path, "待迁移/角色.json", json.dumps({
        "spec": "chara_card_v2", "data": {"name": "塞西莉亚", "description": "院长"},
    }, ensure_ascii=False))
    tools = {item.name: item for item in edit_agent._tools({}, tmp_path)}

    first = json.loads(tools["convert_st_project_file"].invoke({
        "source_path": "待迁移/角色.json", "artifact_type": "character_card",
        "target_dir": "转换结果",
    }))
    second = json.loads(tools["convert_st_project_file"].invoke({
        "source_path": "待迁移/角色.json", "artifact_type": "character_card",
        "target_dir": "转换结果",
    }))

    assert first["converted"] is True
    assert project_files.file_exists(tmp_path, "转换结果/角色卡/塞西莉亚/card.json")
    assert second == {
        "converted": False, "reason": "target_exists",
        "paths": ["转换结果/角色卡/塞西莉亚/card.json"],
    }
    assert any(event == "edit.import_converted" for event, _ in events)


def test_运行时选择专家并采用内置覆盖(monkeypatch, tmp_path):
    captured = {}
    events = []

    class FakeAgent:
        def invoke(self, payload, config):
            captured["payload"] = payload
            captured["config"] = config
            return {"messages": [AIMessage(content="角色卡已检查")]}

    def fake_build_model(*args, **kwargs):
        captured["model_kwargs"] = kwargs
        return object()

    def fake_create_agent(*, model, tools, system_prompt):
        captured["system_prompt"] = system_prompt
        captured["tool_names"] = [item.name for item in tools]
        return FakeAgent()

    monkeypatch.setattr(edit_agent.project_files, "project_root", lambda _repo_id: tmp_path)
    monkeypatch.setattr(edit_agent.llm, "build_model", fake_build_model)
    monkeypatch.setattr("langchain.agents.create_agent", fake_create_agent)
    monkeypatch.setattr(
        edit_agent.run_trace, "emit",
        lambda _ctx, event, **data: events.append((event, data)),
    )
    ctx = {
        "repo_id": "作品一", "chat_base": "http://localhost/v1", "chat_key": "key",
        "chat_model": "model", "history": [],
        "builtin": {"edit_character_card": {
            "systemPrompt": "作品专用角色卡规则", "temperature": 0.45,
            "topP": 0.75, "maxTokens": 3072,
        }},
    }

    result = edit_agent.run(ctx, "制作角色卡", [], [])

    assert result["result_text"] == "角色卡已检查"
    assert result["trace"][-1] == "📝 角色卡制作执行完成"
    assert captured["model_kwargs"]["temperature"] == 0.45
    assert captured["model_kwargs"]["top_p"] == 0.75
    assert captured["model_kwargs"]["max_tokens"] == 3072
    assert "当前可操作根是已选中的小仓库" in captured["system_prompt"]
    assert "作品专用角色卡规则" in captured["system_prompt"]
    assert "validate_project_file" in captured["tool_names"]
    assert "convert_st_project_file" in captured["tool_names"]
    assert "save_attachment_png" in captured["tool_names"]
    assert "publish_character_card" in captured["tool_names"]
    assert "publish_preset" in captured["tool_names"]
    assert "read_recent_agent_trace" in captured["tool_names"]
    assert any(
        event == "edit.specialist_selected" and data["specialist"] == "edit_character_card"
        for event, data in events
    )
