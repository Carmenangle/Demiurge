import json
import base64

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.services import edit_agent, project_files


def _simulate(monkeypatch, tmp_path, message, scenario, *, history=None, images=None):
    captured = {"events": []}

    class FakeAgent:
        def invoke(self, payload, config):
            captured["messages"] = payload["messages"]
            captured["config"] = config
            tools = {item.name: item for item in captured["tools"]}
            reply = scenario(tools, payload["messages"])
            return {"messages": [*payload["messages"], AIMessage(content=reply)]}

    def fake_create_agent(*, model, tools, system_prompt):
        captured["tools"] = tools
        captured["system_prompt"] = system_prompt
        return FakeAgent()

    monkeypatch.setattr(edit_agent.project_files, "project_root", lambda _repo_id: tmp_path)
    monkeypatch.setattr(edit_agent.llm, "build_model", lambda *args, **kwargs: object())
    monkeypatch.setattr("langchain.agents.create_agent", fake_create_agent)
    monkeypatch.setattr(
        edit_agent.run_trace,
        "emit",
        lambda _ctx, event, **data: captured["events"].append((event, data)),
    )
    ctx = {
        "repo_id": "模拟作品",
        "chat_base": "http://localhost/v1",
        "chat_key": "test-key",
        "chat_model": "test-model",
        "history": history or [],
    }
    result = edit_agent.run(ctx, message, images or [], [])
    return result, captured


def _json(value):
    return json.dumps(value, ensure_ascii=False, indent=2)


def test_模拟角色卡专家创建侧车并逐项校验(monkeypatch, tmp_path):
    def scenario(tools, _messages):
        assert json.loads(tools["list_project_files"].invoke({})) == []
        card = {
            "name": "塞西莉亚",
            "description": "孤儿院院长",
            "personality": "温和而谨慎",
            "first_mes": "欢迎回来。",
            "spec": "chara_card_v2",
            "extensions": {"保留字段": True},
        }
        book = {"entries": [{
            "comment": "孤儿院",
            "keys": ["孤儿院"],
            "content": "塞西莉亚经营的孤儿院。",
            "constant": False,
            "enabled": True,
        }]}
        tools["write_project_file"].invoke({
            "path": "角色卡/塞西莉亚/card.json", "content": _json(card),
        })
        tools["write_project_file"].invoke({
            "path": "角色卡/塞西莉亚/worldbook.json", "content": _json(book),
        })
        assert "塞西莉亚" in tools["read_project_file"].invoke({
            "path": "角色卡/塞西莉亚/card.json",
        })
        card_result = json.loads(tools["validate_project_file"].invoke({
            "path": "角色卡/塞西莉亚/card.json", "artifact_type": "character_card",
        }))
        book_result = json.loads(tools["validate_project_file"].invoke({
            "path": "角色卡/塞西莉亚/worldbook.json", "artifact_type": "worldbook",
        }))
        assert card_result["valid"] and book_result["valid"]
        return "角色卡和世界书侧车已创建并校验"

    result, captured = _simulate(
        monkeypatch, tmp_path, "制作塞西莉亚角色卡和配套世界书", scenario,
    )

    assert result["result_text"] == "角色卡和世界书侧车已创建并校验"
    assert project_files.file_exists(tmp_path, "角色卡/塞西莉亚/card.json")
    assert captured["events"][0][1]["specialist"] == "edit_character_card"


def test_模拟预设正则专家创建两类Demiurge产物(monkeypatch, tmp_path):
    def scenario(tools, _messages):
        assert json.loads(tools["list_project_files"].invoke({})) == []
        preset = {
            "prompts": [{
                "identifier": "main", "role": "system", "content": "推进剧情",
            }],
            "prompt_order": [{
                "order": [{"identifier": "main", "enabled": True}],
            }],
            "thinking_chains": [{
                "name": "冲突链", "content": "判断目标和阻力", "position": "tail",
                "when": {"scene": "conflict"},
            }],
        }
        regex = [{
            "id": "clean-think", "scriptName": "隐藏思考",
            "findRegex": "/<think>[\\s\\S]*?<\\/think>/g", "replaceString": "",
            "trimStrings": [], "placement": [2], "disabled": False,
            "markdownOnly": True, "promptOnly": False, "runOnEdit": True,
            "substituteRegex": 0, "minDepth": None, "maxDepth": None,
        }]
        for path, content, kind in (
            ("预设/冲突.json", preset, "preset"),
            ("预设/冲突.regex.json", regex, "regex"),
        ):
            tools["write_project_file"].invoke({"path": path, "content": _json(content)})
            assert json.loads(tools["validate_project_file"].invoke({
                "path": path, "artifact_type": kind,
            }))["valid"]
        return "预设与正则已创建并校验"

    _, captured = _simulate(
        monkeypatch, tmp_path, "制作一个冲突场景预设和正则", scenario,
    )

    assert captured["events"][0][1]["specialist"] == "edit_preset_regex"


def test_模拟脚本专家创建可校验Python(monkeypatch, tmp_path):
    def scenario(tools, _messages):
        assert json.loads(tools["list_project_files"].invoke({})) == []
        source = (
            "from pathlib import Path\n\n"
            "def main() -> None:\n"
            "    print(Path(__file__).resolve().parent)\n\n"
            "if __name__ == '__main__':\n"
            "    main()\n"
        )
        tools["write_project_file"].invoke({"path": "tools/show_root.py", "content": source})
        assert tools["read_project_file"].invoke({"path": "tools/show_root.py"}) == source
        check = json.loads(tools["validate_project_file"].invoke({
            "path": "tools/show_root.py", "artifact_type": "python",
        }))
        assert check["valid"]
        return "脚本已创建并通过语法校验"

    _, captured = _simulate(monkeypatch, tmp_path, "编写一个 Python 作品脚本", scenario)

    assert captured["events"][0][1]["specialist"] == "edit_script"


def test_模拟排错只读和明确修复两条路径(monkeypatch, tmp_path):
    path = "角色卡/塞西莉亚/card.json"
    project_files.write_text(tmp_path, path, _json({
        "name": "塞西莉亚", "description": "旧描述", "spec": "chara_card_v2",
    }))

    def inspect(tools, _messages):
        assert path in json.loads(tools["list_project_files"].invoke({}))
        assert "旧描述" in tools["read_project_file"].invoke({"path": path})
        return "定位到角色描述仍为旧值；本轮未修改"

    before = project_files.read_text(tmp_path, path)
    _, inspected = _simulate(monkeypatch, tmp_path, "为什么角色描述没有更新", inspect)
    assert project_files.read_text(tmp_path, path) == before
    assert not any(event == "edit.file_written" for event, _ in inspected["events"])

    def repair(tools, _messages):
        assert path in json.loads(tools["list_project_files"].invoke({}))
        current = tools["read_project_file"].invoke({"path": path})
        assert "旧描述" in current
        result = tools["replace_in_project_file"].invoke({
            "path": path, "old_text": "旧描述", "new_text": "孤儿院院长",
        })
        assert result.startswith("SUCCESS")
        assert "孤儿院院长" in tools["read_project_file"].invoke({"path": path})
        assert json.loads(tools["validate_project_file"].invoke({
            "path": path, "artifact_type": "character_card",
        }))["valid"]
        return "已最小替换并校验"

    _, repaired = _simulate(monkeypatch, tmp_path, "修复角色卡描述没有更新的问题", repair)
    assert any(event == "edit.file_replaced" for event, _ in repaired["events"])
    assert repaired["events"][0][1]["specialist"] == "edit_debug"


@pytest.mark.parametrize(
    ("message", "source_path", "source", "kind", "expected_path"),
    [
        (
            "把 ST 角色卡转换成 Demiurge 格式", "导入/角色.json",
            {"name": "旅人", "description": "远方来客"}, "character_card",
            "结果/角色卡/旅人/card.json",
        ),
        (
            "把 ST 世界书转换为 Demiurge", "导入/世界书.json",
            {"entries": {"0": {"key": "王都", "content": "帝国首都"}}}, "worldbook",
            "结果/worldbook.json",
        ),
        (
            "迁移 ST 预设到 Demiurge", "导入/预设.json",
            {
                "prompts": [{"identifier": "main", "role": "system"}],
                "prompt_order": [{"order": [{"identifier": "main", "enabled": True}]}],
            }, "preset", "结果/预设.json",
        ),
        (
            "迁移 ST 正则到 Demiurge", "导入/正则.json",
            [{"findRegex": "/foo/g", "replaceString": "bar"}], "regex",
            "结果/regex.json",
        ),
    ],
)
def test_模拟外部迁移专家四类转换(
    monkeypatch, tmp_path, message, source_path, source, kind, expected_path,
):
    project_files.write_text(tmp_path, source_path, _json(source))

    def scenario(tools, _messages):
        assert source_path in json.loads(tools["list_project_files"].invoke({}))
        result = json.loads(tools["convert_st_project_file"].invoke({
            "source_path": source_path, "artifact_type": kind, "target_dir": "结果",
        }))
        assert result["converted"]
        assert expected_path in result["paths"]
        return "迁移完成"

    _, captured = _simulate(monkeypatch, tmp_path, message, scenario)
    assert project_files.file_exists(tmp_path, expected_path)
    assert captured["events"][0][1]["specialist"] == "edit_import_adapter"


def test_模拟通用专家识别作品根文件属主且不误写(monkeypatch, tmp_path):
    project_files.write_text(tmp_path, "_repo.json", _json({"id": "模拟作品"}))
    project_files.write_text(tmp_path, "chat.json", _json({"messages": []}))

    def scenario(tools, _messages):
        files = json.loads(tools["list_project_files"].invoke({}))
        assert files == ["_repo.json", "chat.json"]
        assert "messages" in tools["read_project_file"].invoke({"path": "chat.json"})
        return "已确认作品根负责仓库标记与会话快照"

    _, captured = _simulate(monkeypatch, tmp_path, "说明当前作品文件归属", scenario)
    assert captured["events"][0][1]["specialist"] == "edit_general"
    assert not any(event in {"edit.file_written", "edit.file_replaced"}
                   for event, _ in captured["events"])


def test_模拟历史角色顺序和图片附件完整传入执行Agent(monkeypatch, tmp_path):
    history = [
        {"role": "user", "content": "先检查现有角色卡"},
        {"role": "assistant", "content": "请提供参考图"},
    ]
    image = "data:image/png;base64,AAAA"

    def scenario(_tools, messages):
        assert isinstance(messages[0], HumanMessage)
        assert isinstance(messages[1], AIMessage)
        assert isinstance(messages[2], HumanMessage)
        assert messages[2].content == [
            {"type": "text", "text": "根据附件制作角色卡"},
            {"type": "image_url", "image_url": {"url": image}},
        ]
        return "附件与历史已接收"

    result, _ = _simulate(
        monkeypatch, tmp_path, "根据附件制作角色卡", scenario,
        history=history, images=[image],
    )
    assert result["result_text"] == "附件与历史已接收"


def test_模拟附件保存为角色头像(monkeypatch, tmp_path):
    png = project_files.PNG_SIGNATURE + b"avatar"
    image = "data:image/png;base64," + base64.b64encode(png).decode("ascii")

    def scenario(tools, _messages):
        tools["list_project_files"].invoke({})
        saved = json.loads(tools["save_attachment_png"].invoke({
            "path": "角色卡/塞西莉亚/avatar.png", "attachment_index": 0,
        }))
        assert saved["saved"]
        return "头像已保存"

    _simulate(
        monkeypatch, tmp_path, "保存附件作为角色头像", scenario, images=[image],
    )
    assert project_files.read_png(tmp_path, "角色卡/塞西莉亚/avatar.png") == png


def test_模拟角色卡从作品快照受控发布到源库(monkeypatch, tmp_path):
    library = tmp_path / "角色卡源库"
    path = "角色卡/塞西莉亚/card.json"
    project_files.write_text(tmp_path, path, _json({
        "name": "塞西莉亚", "description": "孤儿院院长",
        "spec": "chara_card_v2", "character_book": None,
        "regex_scripts": [], "extensions": {},
    }))
    monkeypatch.setattr(
        edit_agent.edit_publication.repo_meta, "setting_dir_from_state",
        lambda key: str(library) if key == "characterDir" else "",
    )

    def scenario(tools, _messages):
        assert path in json.loads(tools["list_project_files"].invoke({}))
        tools["read_project_file"].invoke({"path": path})
        result = json.loads(tools["publish_character_card"].invoke({
            "name": "塞西莉亚",
        }))
        assert result["published"]
        return "角色卡已发布"

    _simulate(monkeypatch, tmp_path, "保存并发布塞西莉亚角色卡", scenario)
    assert (library / "塞西莉亚" / "card.json").is_file()


def test_模拟工具失败均进入Trace且不越过小仓库(monkeypatch, tmp_path):
    project_files.write_text(tmp_path, "已有.json", "{}")

    def scenario(tools, _messages):
        assert tools["read_project_file"].invoke({"path": "../外部.json"}).startswith("ERROR:")
        invalid = json.loads(tools["validate_project_file"].invoke({
            "path": "已有.json", "artifact_type": "worldbook",
        }))
        assert invalid["valid"] is False
        return "已报告边界和格式错误"

    _, captured = _simulate(monkeypatch, tmp_path, "检查这些文件", scenario)
    event_names = [event for event, _ in captured["events"]]
    assert "edit.file_failed" in event_names
    assert "edit.validation_failed" in event_names
