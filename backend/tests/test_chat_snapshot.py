import threading
import time

from app.services import chat_snapshot
from app.services.chat_snapshot import claim_media_slot_submission, load, save


def test_assistant_message_preserves_shape_and_key_order():
    message = chat_snapshot.assistant_message(
        "mid", "text", image="image.png", interrupted=True,
    )

    assert message == {
        "id": "mid", "role": "assistant", "text": "text",
        "image": "image.png", "interrupted": True,
    }
    assert list(message) == ["id", "role", "text", "image", "interrupted"]


def test_private_alias_points_to_public_message():
    # generation_store 已改用公共 assistant_message；别名仍在，保内部旧调用不破
    assert chat_snapshot._assistant_message is chat_snapshot.assistant_message


def test_用户消息在助手生成前落盘且重复确保不覆盖前端富内容(monkeypatch, tmp_path):
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")

    assert chat_snapshot.ensure_user_message(
        "thread", "user-1", "继续剧情", ["reference.png"],
    )
    assert chat_snapshot.load("thread") == [{
        "id": "user-1", "role": "user", "text": "继续剧情",
        "parts": [
            {"type": "text", "text": "继续剧情"},
            {"type": "image", "url": "reference.png"},
        ],
    }]

    rich = [{
        "id": "user-1", "role": "user", "text": "继续剧情",
        "parts": [{"type": "masked-image", "url": "preview.png"}],
    }]
    chat_snapshot.save("thread", rich)
    assert chat_snapshot.ensure_user_message("thread", "user-1", "继续剧情")
    assert chat_snapshot.load("thread") == rich

def test_用户消息带附件时快照持久化file_part(monkeypatch, tmp_path):
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")

    attachments = [{"file_id": "a" * 32, "name": "形象提示词-阮秋白.md", "mime": "text/markdown", "size": 45800}]
    assert chat_snapshot.ensure_user_message("thread", "user-file", "帮我读取", attachments=attachments)
    saved = chat_snapshot.load("thread")[0]
    assert saved["parts"] == [
        {"type": "text", "text": "帮我读取"},
        {"type": "file", "fileId": "a" * 32, "name": "形象提示词-阮秋白.md", "mime": "text/markdown", "size": 45800},
    ]


def test_可从Trace把遗失用户消息恢复到对应助手消息之前(monkeypatch, tmp_path):
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
    chat_snapshot.save("thread", [
        {"id": "before", "role": "assistant", "text": "前一轮"},
        {"id": "answer", "role": "assistant", "text": "本轮回答"},
    ])

    assert chat_snapshot.ensure_user_message(
        "thread", "recovered-user", "从Trace恢复的输入", before_id="answer",
    )
    assert [item["id"] for item in chat_snapshot.load("thread")] == [
        "before", "recovered-user", "answer",
    ]


def test_prompt_history只转换快照中仍存在的对话文本():
    snapshot = [
        {"id": "u1", "role": "user", "text": "保留的用户消息"},
        {"id": "card", "role": "assistant", "text": "", "workflow": {"templateName": "x"}},
        {"id": "a1", "role": "assistant", "text": "", "parts": [
            {"type": "text", "text": "保留的助手消息"},
            {"type": "image", "url": "x.png"},
            {"type": "media-slot", "slotId": "slot-1", "status": "ready"},
        ]},
        {"id": "a2", "role": "assistant", "text": "", "image": "top-level.png"},
        {"id": "u2", "role": "user", "text": "仅保留文字", "images": ["upload.png"]},
    ]

    assert chat_snapshot.to_prompt_history(snapshot) == [
        {"role": "user", "content": "保留的用户消息"},
        {"role": "assistant", "content": "保留的助手消息"},
        {"role": "user", "content": "仅保留文字"},
    ]


def test_prompt_history与前端标签过滤对齐():
    snapshot = [
        # 状态/Toast 不进上下文
        {"id": "toast", "role": "assistant", "text": "已提交到 ComfyUI 生成（prompt_id: x，20 个节点）…", "system": True},
        # 顶层媒体气泡（工作流产物带提示词）不进上下文
        {"id": "gen", "role": "assistant", "text": "1girl, portrait", "image": "/local-view?path=a.png"},
        # 非剧情路由（生图/视频专家）不进上下文
        {"id": "vid", "role": "assistant", "text": "girl dancing", "route": "video"},
        {"id": "gen2", "role": "assistant", "text": "提示词", "route": "generate"},
        # 剧情专家产出进上下文
        {"id": "story", "role": "assistant", "text": "剧情正文", "route": "roleplay"},
        {"id": "ans", "role": "assistant", "text": "对话正文", "route": "answer"},
        {"id": "u1", "role": "user", "text": "用户消息"},
    ]

    assert chat_snapshot.to_prompt_history(snapshot) == [
        {"role": "assistant", "content": "剧情正文"},
        {"role": "assistant", "content": "对话正文"},
        {"role": "user", "content": "用户消息"},
    ]


def test_backfill_story_tags回填剧情与状态标签():
    snapshot = [
        {"id": "s1", "role": "assistant", "text": "夜风穿过巷口，她停下脚步。"},
        {"id": "toast", "role": "assistant", "text": "已提交到 ComfyUI 生成（prompt_id: abc，20 个节点），正在运转工作流…"},
        {"id": "gen", "role": "assistant", "text": "1girl, portrait", "image": "/local-view?path=a.png"},
        {"id": "prompt", "role": "assistant", "text": "QRQ, masterpiece, very aesthetic, best quality, score_9, nsfw, 1girl, solo, ultra detailed, absurdres, 8k, high resolution"},
        {"id": "tagged", "role": "assistant", "text": "已有标签", "route": "generate"},
        {"id": "u1", "role": "user", "text": "继续"},
        {"id": "empty", "role": "assistant", "text": ""},
        {"id": "card", "role": "assistant", "text": "模板卡", "workflow": {"templateName": "A"}},
    ]

    out, stats = chat_snapshot.backfill_story_tags(snapshot)
    by_id = {item["id"]: item for item in out}
    assert by_id["s1"]["route"] == "roleplay"
    assert by_id["toast"]["system"] is True and "route" not in by_id["toast"]
    assert by_id["prompt"]["route"] == "generate"
    assert "route" not in by_id["gen"] and "system" not in by_id["gen"]
    assert by_id["tagged"]["route"] == "generate"
    assert "route" not in by_id["u1"]
    assert "route" not in by_id["empty"]
    assert "route" not in by_id["card"]
    assert stats == {"changed": 3, "story": 1, "status": 1, "generate": 1}


def test_backfill_story_tags幂等已标签消息不动():
    snapshot = [{"id": "x", "role": "assistant", "text": "正文", "route": "roleplay"}]
    out, stats = chat_snapshot.backfill_story_tags(snapshot)
    assert out == snapshot
    assert stats["changed"] == 0


def test_较旧的异步快照不得覆盖删除后的新快照(monkeypatch, tmp_path):
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")

    assert chat_snapshot.save_if_newer("thread", [{"id": "kept"}], revision=20)
    assert not chat_snapshot.save_if_newer(
        "thread", [{"id": "kept"}, {"id": "deleted"}], revision=19,
    )
    assert chat_snapshot.load("thread") == [{"id": "kept"}]


def test_生成图片按slot原位替换而不追加消息(monkeypatch, tmp_path):
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
    chat_snapshot.save("thread", [{
        "id": "bot", "role": "assistant", "text": "前文后文",
        "parts": [
            {"type": "text", "text": "前文"},
            {"type": "media-slot", "slotId": "slot-1", "status": "pending"},
            {"type": "text", "text": "后文"},
        ],
    }])

    assert chat_snapshot.resolve_media_slot(
        "thread", "bot", "slot-1", "local://image", media_type="image",
    )
    items = chat_snapshot.load("thread")
    assert len(items) == 1
    assert items[0]["parts"] == [
        {"type": "text", "text": "前文"},
        {"type": "image", "url": "local://image", "slotId": "slot-1", "status": "ready"},
        {"type": "text", "text": "后文"},
    ]


def test_后台正文先落盘并创建稳定媒体槽(monkeypatch, tmp_path):
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")

    chat_snapshot.append_text("thread", "bot", "前文后文")
    assert chat_snapshot.ensure_media_slot("thread", "bot", "slot-1", offset=2)
    chat_snapshot.append_text("thread", "bot", "前文后文")

    assert chat_snapshot.load("thread") == [{
        "id": "bot", "role": "assistant", "text": "前文后文",
        "parts": [
            {"type": "text", "text": "前文"},
            {"type": "media-slot", "slotId": "slot-1", "status": "pending"},
            {"type": "text", "text": "后文"},
        ],
    }]


def test_Comfy提交后把prompt_id写回持久化媒体槽(monkeypatch, tmp_path):
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
    chat_snapshot.save("thread", [{
        "id": "bot", "role": "assistant", "text": "正文",
        "parts": [{"type": "media-slot", "slotId": "slot-1", "status": "pending"}],
    }])

    assert chat_snapshot.bind_media_slot_prompt("thread", "bot", "slot-1", "prompt-1")
    assert chat_snapshot.load("thread")[0]["parts"] == [{
        "type": "media-slot", "slotId": "slot-1", "status": "pending",
        "promptId": "prompt-1",
    }]


def test_自动插画提交前只能原子认领一次pending槽(monkeypatch, tmp_path):
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
    chat_snapshot.save("thread", [{
        "id": "bot", "role": "assistant", "text": "正文",
        "parts": [{"type": "media-slot", "slotId": "slot-1", "status": "pending"}],
    }])

    assert chat_snapshot.claim_media_slot_submission("thread", "bot", "slot-1")
    assert not chat_snapshot.claim_media_slot_submission("thread", "bot", "slot-1")
    assert chat_snapshot.bind_media_slot_prompt("thread", "bot", "slot-1", "prompt-1")
    assert not chat_snapshot.claim_media_slot_submission("thread", "bot", "slot-1")


def test_前端旧快照不能清掉服务端插画认领或完成结果(monkeypatch, tmp_path):
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
    chat_snapshot._REVISIONS.clear()
    pending = [{
        "id": "bot", "role": "assistant", "text": "正文",
        "parts": [{"type": "media-slot", "slotId": "slot-1", "status": "pending"}],
    }]
    chat_snapshot.save("thread", pending)
    assert chat_snapshot.claim_media_slot_submission("thread", "bot", "slot-1")

    assert chat_snapshot.save_if_newer("thread", pending, revision=1)
    assert chat_snapshot.load("thread")[0]["parts"][0]["submissionClaim"] is True
    assert not chat_snapshot.claim_media_slot_submission("thread", "bot", "slot-1")

    assert chat_snapshot.bind_media_slot_prompt("thread", "bot", "slot-1", "prompt-1")
    assert chat_snapshot.resolve_media_slot(
        "thread", "bot", "slot-1", "ready.png", regeneration={"kind": "template"},
    )
    assert chat_snapshot.save_if_newer("thread", pending, revision=2)
    assert chat_snapshot.load("thread")[0]["parts"][0]["url"] == "ready.png"

    deleted = [{"id": "bot", "role": "assistant", "text": "正文", "parts": []}]
    assert chat_snapshot.save_if_newer("thread", deleted, revision=3)
    # 空 parts 不再等于「删掉这张插画」：前后端都没有单槽删除入口（dropMediaSlot 只清
    # 未提交的 pending 槽，且清空后写的是 parts:undefined），2026-10-29 某作品/SAVE01
    # 三批插画就是这么被旧快照静默抹掉的。媒体按服务端布局回位（这条槽本来就在 index 0），
    # 正文按客户端走。
    assert chat_snapshot.load("thread")[0]["parts"] == [
        {"type": "image", "url": "ready.png", "slotId": "slot-1", "status": "ready",
         "regeneration": {"kind": "template"}},
        {"type": "text", "text": "正文"},
    ]


def _server_with_illustration(chat_snapshot, monkeypatch, tmp_path, repo_meta):
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
    chat_snapshot._REVISIONS.clear()
    chat_snapshot.save("thread", [{
        "id": "bot", "role": "assistant", "text": "前文后文",
        "parts": [
            {"type": "text", "text": "前文"},
            {"type": "image", "url": "local://pic/1.png", "slotId": "slot-1", "status": "ready"},
            {"type": "text", "text": "后文"},
        ],
    }])


def test_前端纯文本快照不能抹掉服务端已完成的插画(monkeypatch, tmp_path):
    """真机事故复现：客户端内存态只有文本段 → 全量快照保存把插画删了。

    2026-10-29 用户实锤「某作品/SAVE01 是有生成几批插画的」，但 chat.json 里
    39 条消息的 parts 只剩文本；10-26 离线包同作品还有 3 个 ready 图片 part + 5 个槽。
    """
    from app.services import repo_meta
    _server_with_illustration(chat_snapshot, monkeypatch, tmp_path, repo_meta)

    stale = [{"id": "bot", "role": "assistant", "text": "前文后文",
              "parts": [{"type": "text", "text": "前文"}, {"type": "text", "text": "后文"}]}]
    assert chat_snapshot.save_if_newer("thread", stale, revision=1)
    assert chat_snapshot.load("thread")[0]["parts"] == [
        {"type": "text", "text": "前文"},
        {"type": "image", "url": "local://pic/1.png", "slotId": "slot-1", "status": "ready"},
        {"type": "text", "text": "后文"},
    ]


def test_多批插画的段间位置随正文一起还原(monkeypatch, tmp_path):
    """某作品/SAVE01 的真实形状：一条正文里几张图分散在不同段落之后。"""
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
    chat_snapshot._REVISIONS.clear()
    server_parts = []
    for index, title in enumerate(["第一段", "第二段", "第三段", "第四段"]):
        server_parts.append({"type": "text", "text": title})
        if index in (0, 2):
            server_parts.append({
                "type": "image", "url": f"local://pic/{index}.png",
                "slotId": f"slot-{index}", "status": "ready",
            })
    chat_snapshot.save("thread", [{"id": "bot", "role": "assistant",
                                   "text": "第一段第二段第三段第四段", "parts": server_parts}])

    stale = [{"id": "bot", "role": "assistant", "text": "第一段第二段第三段第四段",
              "parts": [{"type": "text", "text": "第一段"}, {"type": "text", "text": "第二段"},
                        {"type": "text", "text": "第三段"}, {"type": "text", "text": "第四段"}]}]
    assert chat_snapshot.save_if_newer("thread", stale, revision=1)
    assert chat_snapshot.load("thread")[0]["parts"] == server_parts


def test_前端消息没带parts时服务端插画原位保留(monkeypatch, tmp_path):
    """同一条链的第二种形状：客户端消息压根没有 parts 键（纯 text 消息）。"""
    from app.services import repo_meta
    _server_with_illustration(chat_snapshot, monkeypatch, tmp_path, repo_meta)

    assert chat_snapshot.save_if_newer(
        "thread", [{"id": "bot", "role": "assistant", "text": "前文后文"}], revision=1)
    assert chat_snapshot.load("thread")[0]["parts"] == [
        {"type": "text", "text": "前文"},
        {"type": "image", "url": "local://pic/1.png", "slotId": "slot-1", "status": "ready"},
        {"type": "text", "text": "后文"},
    ]


def test_插画回到客户端改写过的正文尾部而不是消失(monkeypatch, tmp_path):
    """文本段数对不齐（用户编辑过正文）时按尾部挂载：宁可位置退让，不静默丢图。"""
    from app.services import repo_meta
    _server_with_illustration(chat_snapshot, monkeypatch, tmp_path, repo_meta)

    edited = [{"id": "bot", "role": "assistant", "text": "合成一段的新正文",
               "parts": [{"type": "text", "text": "合成一段的新正文"}]}]
    assert chat_snapshot.save_if_newer("thread", edited, revision=1)
    parts = chat_snapshot.load("thread")[0]["parts"]
    assert parts[0] == {"type": "text", "text": "合成一段的新正文"}
    assert {"type": "image", "url": "local://pic/1.png", "slotId": "slot-1",
            "status": "ready"} in parts


def test_未提交pending槽仍可被前端清理已提交槽不丢(monkeypatch, tmp_path):
    """pruneUnsubmittedMediaSlots 的合法清理面不能被扩大。"""
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
    chat_snapshot._REVISIONS.clear()
    server = [{"id": "bot", "role": "assistant", "text": "正文",
               "parts": [
                   {"type": "text", "text": "正文"},
                   {"type": "media-slot", "slotId": "slot-1", "status": "pending"},
                   {"type": "media-slot", "slotId": "slot-2", "status": "pending",
                    "promptId": "prompt-2"},
               ]}]
    chat_snapshot.save("thread", server)

    client = [{"id": "bot", "role": "assistant", "text": "正文",
               "parts": [{"type": "text", "text": "正文"}]}]
    assert chat_snapshot.save_if_newer("thread", client, revision=1)
    parts = chat_snapshot.load("thread")[0]["parts"]
    assert {"type": "media-slot", "slotId": "slot-2", "status": "pending",
            "promptId": "prompt-2"} in parts
    assert not any(part.get("slotId") == "slot-1" for part in parts)


def test_快照读空后的历史重建列表不能整条覆盖会话(monkeypatch, tmp_path):
    """真因层防护：重建列表 id 全是新造的，与既有会话零交集 ⇒ 一律拒绝落盘。

    2026-10-29 某作品/SAVE01：包里的 27 个消息 id 与现文件 39 个 id 零交集，
    现文件每条只剩 {id, role, text, parts:[文本段]}——正是 useSessionLoad 退回
    langgraph 历史 + crypto.randomUUID 重建后的形状，插画与 trace 就是这么整条没的。
    """
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
    chat_snapshot._REVISIONS.clear()
    original = [
        {"id": "m-1", "role": "user", "text": "第一轮",
         "parts": [{"type": "text", "text": "第一轮"}]},
        {"id": "m-2", "role": "assistant", "text": "第二轮正文",
         "parts": [
             {"type": "text", "text": "第二轮正文"},
             {"type": "image", "url": "local://pic/1.png", "slotId": "slot-1", "status": "ready"},
         ]},
    ]
    chat_snapshot.save("thread", original)

    rebuilt = [
        {"id": "n-1", "role": "user", "text": "第一轮",
         "parts": [{"type": "text", "text": "第一轮"}]},
        {"id": "n-2", "role": "assistant", "text": "第二轮正文",
         "parts": [{"type": "text", "text": "第二轮正文"}]},
    ]
    assert chat_snapshot.save_if_newer("thread", rebuilt, revision=1) is False
    assert chat_snapshot.load("thread") == original


def test_删掉部分消息与清空对话仍照常保存(monkeypatch, tmp_path):
    """拒绝面只收「零交集」：真实删除仍有交集，清空传空列表，都不该被拦。"""
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
    chat_snapshot._REVISIONS.clear()
    chat_snapshot.save("thread", [
        {"id": "m-1", "role": "user", "text": "第一轮"},
        {"id": "m-2", "role": "assistant", "text": "第二轮"},
        {"id": "m-3", "role": "user", "text": "第三轮"},
    ])

    assert chat_snapshot.save_if_newer(
        "thread",
        [{"id": "m-1", "role": "user", "text": "第一轮"},
         {"id": "m-3", "role": "user", "text": "第三轮"}],
        revision=1)
    assert [item["id"] for item in chat_snapshot.load("thread")] == ["m-1", "m-3"]

    assert chat_snapshot.save_if_newer("thread", [], revision=2)
    assert chat_snapshot.load("thread") == []

    assert chat_snapshot.save_if_newer(
        "thread", [{"id": "m-9", "role": "user", "text": "重新开始"}], revision=3)
    assert [item["id"] for item in chat_snapshot.load("thread")] == ["m-9"]


def test_快照损坏时拒绝保存而不是覆盖真源(monkeypatch, tmp_path):
    """损坏文件读不动 ≠ 没有历史：拿空 current 参与合并再回写等于二次毁数据。"""
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
    chat_snapshot._REVISIONS.clear()
    chat_snapshot.save("thread", [{"id": "m-1", "role": "user", "text": "第一轮"}])
    target = tmp_path / "thread.json"
    target.write_text("{ broken", encoding="utf-8")

    assert chat_snapshot.save_if_newer(
        "thread", [{"id": "m-1", "role": "user", "text": "改写"}], revision=1) is False
    assert target.read_text(encoding="utf-8") == "{ broken"


def test_整条消息删除后插画不复活(monkeypatch, tmp_path):
    """用户删除的是消息本身：客户端不再提这条 id，服务端不得把它塞回来。"""
    from app.services import repo_meta
    _server_with_illustration(chat_snapshot, monkeypatch, tmp_path, repo_meta)
    chat_snapshot.save("thread", chat_snapshot.load("thread") + [{
        "id": "bot-2", "role": "assistant", "text": "第二段",
        "parts": [
            {"type": "text", "text": "第二段"},
            {"type": "image", "url": "local://pic/2.png", "slotId": "slot-9", "status": "ready"},
        ],
    }])

    assert chat_snapshot.save_if_newer(
        "thread", [{"id": "bot", "role": "assistant", "text": "前文后文"}], revision=1)
    items = chat_snapshot.load("thread")
    assert [item["id"] for item in items] == ["bot"]
    assert not any(part.get("slotId") == "slot-9"
                   for item in items for part in (item.get("parts") or []))


def test_重新生图按相同slot替换已有图片而不追加消息(monkeypatch, tmp_path):
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
    chat_snapshot.save("thread", [{
        "id": "bot", "role": "assistant", "text": "前文后文",
        "parts": [
            {"type": "text", "text": "前文"},
            {"type": "image", "url": "local://old", "slotId": "slot-1", "status": "ready"},
            {"type": "text", "text": "后文"},
        ],
    }])

    regeneration = {"kind": "template", "templateId": "tpl", "values": {"39.text": "p"}}
    assert chat_snapshot.resolve_media_slot(
        "thread", "bot", "slot-1", "local://new", media_type="image",
        regeneration=regeneration,
    )
    items = chat_snapshot.load("thread")
    assert len(items) == 1
    assert items[0]["parts"][1] == {
        "type": "image", "url": "local://new", "slotId": "slot-1", "status": "ready",
        "regeneration": regeneration,
    }


def test_自动插画失败删除slot并保留正文(monkeypatch, tmp_path):
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
    chat_snapshot.save("thread", [{
        "id": "bot", "role": "assistant", "text": "前文后文",
        "parts": [
            {"type": "text", "text": "前文"},
            {"type": "media-slot", "slotId": "slot-1", "status": "pending"},
            {"type": "text", "text": "后文"},
        ],
    }])

    assert chat_snapshot.remove_media_slot("thread", "bot", "slot-1")
    assert chat_snapshot.load("thread") == [{
        "id": "bot", "role": "assistant", "text": "前文后文",
        "parts": [{"type": "text", "text": "前文后文"}],
    }]
    assert not chat_snapshot.remove_media_slot("thread", "bot", "slot-1")


def test_append_helpers_preserve_existing_behavior(monkeypatch, tmp_path):
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")

    chat_snapshot.append_image("thread", "image", "image.png", None)
    chat_snapshot.append_text("thread", "blank", "   ")
    chat_snapshot.append_text("thread", "text", "hello")

    assert chat_snapshot.load("thread") == [
        {"id": "image", "role": "assistant", "text": "", "image": "image.png"},
        {"id": "text", "role": "assistant", "text": "hello"},
    ]


def test_save_and_upsert_share_thread_lock(monkeypatch, tmp_path):
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    original_save = chat_snapshot._save_unlocked
    save_started = threading.Event()
    release_save = threading.Event()

    def slow_save(thread_id, messages):
        if messages == [{"id": "frontend"}]:
            save_started.set()
            assert release_save.wait(timeout=2)
        original_save(thread_id, messages)

    monkeypatch.setattr(chat_snapshot, "_save_unlocked", slow_save)
    full_write = threading.Thread(
        target=chat_snapshot.save,
        args=("thread", [{"id": "frontend"}]),
    )
    incremental_write = threading.Thread(
        target=chat_snapshot.upsert,
        args=("thread", {"id": "backend"}),
    )

    full_write.start()
    assert save_started.wait(timeout=2)
    incremental_write.start()
    time.sleep(0.05)
    assert incremental_write.is_alive()

    release_save.set()
    full_write.join(timeout=2)
    incremental_write.join(timeout=2)

    assert chat_snapshot.load("thread") == [
        {"id": "frontend"},
        {"id": "backend"},
    ]


def test_未配仓库文件夹回退旧位置(monkeypatch, tmp_path):
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
    assert chat_snapshot._path("r1") == tmp_path / "r1.json"


def test_配了仓库文件夹落作品子文件夹(monkeypatch, tmp_path):
    from app.services import repo_meta
    out = tmp_path / "repos"
    out.mkdir()
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path / "legacy")
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: str(out))
    monkeypatch.setattr(repo_meta, "folder_name", lambda rid: "爱丽丝的故事")
    chat_snapshot.save("r1", [{"id": "a"}])
    assert (out / "爱丽丝的故事" / "chat.json").is_file()
    assert chat_snapshot.load("r1") == [{"id": "a"}]


def test_存量会话读时惰性迁移到仓库文件夹(monkeypatch, tmp_path):
    from app.services import repo_meta
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    out = tmp_path / "repos"
    out.mkdir()
    # 旧位置先有存量会话
    (legacy / "r1.json").write_text('[{"id":"old"}]', encoding="utf-8")
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", legacy)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: str(out))
    monkeypatch.setattr(repo_meta, "folder_name", lambda rid: "作品A")
    # 首次解析即搬迁：新位置出现、旧位置消失
    resolved = chat_snapshot._path("r1")
    assert resolved == out / "作品A" / "chat.json"
    assert resolved.is_file()
    assert not (legacy / "r1.json").exists()
    assert chat_snapshot.load("r1") == [{"id": "old"}]


def test_视频槽原位回填与槽丢失时补插到既有消息(monkeypatch, tmp_path):
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
    chat_snapshot.save("thread-v", [{
        "id": "bot", "role": "assistant", "text": "正文",
        "parts": [{"type": "text", "text": "正文"},
                  {"type": "media-slot", "slotId": "slot-1", "status": "pending"}],
    }])
    assert chat_snapshot.resolve_media_slot(
        "thread-v", "bot", "slot-1", "local://movie.mp4", media_type="video",
    )
    part = chat_snapshot.load("thread-v")[0]["parts"][1]
    assert part["type"] == "video" and part["status"] == "ready"
    assert part["url"] == "local://movie.mp4"

    # 2026-09-01 用户实锤：槽意外丢失时也要把已生成媒体补插进既有消息，绝不孤儿化
    chat_snapshot.save("thread-v", [{"id": "bot", "role": "assistant", "text": "正文", "parts": []}])
    assert chat_snapshot.resolve_media_slot(
        "thread-v", "bot", "slot-1", "local://orphan.mp4", media_type="video",
    ) is True
    parts = chat_snapshot.load("thread-v")[0]["parts"]
    assert len(parts) == 2 and parts[0]["type"] == "text"
    assert parts[1]["type"] == "video" and parts[1]["slotId"] == "slot-1"
    assert len(chat_snapshot.load("thread-v")) == 1


def test_resolve_media_slot_音频槽保留角色名分条元数据(monkeypatch, tmp_path):
    """A1.6：音频槽回填后保留 kind/speaker/seq/total，刷新恢复仍能按角色分条展示。"""
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
    chat_snapshot.save("thread-a", [{
        "id": "bot", "role": "assistant", "text": "正文",
        "parts": [{
            "type": "media-slot", "slotId": "audio-1", "status": "pending",
            "kind": "audio", "speaker": "阿尼玛", "seq": 2, "total": 3,
        }],
    }])

    assert chat_snapshot.resolve_media_slot(
        "thread-a", "bot", "audio-1", "local://a.wav", media_type="audio",
    )
    part = chat_snapshot.load("thread-a")[0]["parts"][0]
    assert part["type"] == "audio" and part["status"] == "ready"
    assert part["url"] == "local://a.wav"
    assert part["speaker"] == "阿尼玛"
    assert part["seq"] == 2 and part["total"] == 3 and part["kind"] == "audio"

    # 非音频槽（图片）不注入音频元数据
    chat_snapshot.save("thread-a", [{
        "id": "bot", "role": "assistant", "text": "正文",
        "parts": [{"type": "media-slot", "slotId": "slot-1", "status": "pending"}],
    }])
    assert chat_snapshot.resolve_media_slot(
        "thread-a", "bot", "slot-1", "local://img.png", media_type="image",
    )
    part = chat_snapshot.load("thread-a")[0]["parts"][0]
    assert part["type"] == "image" and part["status"] == "ready"
    assert "speaker" not in part and "kind" not in part


def test_merge_fields_对未知message_id静默返回不追加幽灵消息(monkeypatch, tmp_path):
    """N1 修复：未知 message_id 的 merge_fields 不应追加幽灵消息。"""
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
    chat_snapshot.save("thread", [
        {"id": "existing", "role": "assistant", "text": "已知消息"},
    ])

    # merge_fields 对未知 id 静默返回，不追加
    chat_snapshot.merge_fields("thread", "unknown-id", inspiration={"selected": ["url1"]})
    assert len(chat_snapshot.load("thread")) == 1
    assert chat_snapshot.load("thread")[0]["id"] == "existing"

    # merge_fields 对已知 id 正常更新
    chat_snapshot.merge_fields("thread", "existing", inspiration={"selected": ["url2"]})
    assert len(chat_snapshot.load("thread")) == 1
    assert chat_snapshot.load("thread")[0]["inspiration"] == {"selected": ["url2"]}


def test_select_inspiration_正常选中并更新(monkeypatch, tmp_path):
    """N3 测试：正常选中灵感卡图片并持久化到快照。"""
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
    chat_snapshot.save("thread", [
        {"id": "msg-1", "role": "assistant", "text": "搜索结果",
         "inspiration": {"query": "猫", "results": [{"url": "http://img.com/cat.png"}]}},
    ])

    result = chat_snapshot.select_inspiration("thread", "msg-1",
                                               ["http://img.com/cat.png", "http://img.com/dog.png"])
    assert result == {"ok": True, "selected": ["http://img.com/cat.png", "http://img.com/dog.png"]}
    loaded = chat_snapshot.load("thread")
    assert loaded[0]["inspiration"]["selected"] == ["http://img.com/cat.png", "http://img.com/dog.png"]


def test_select_inspiration_未知message_id不追加幽灵消息(monkeypatch, tmp_path):
    """N3 测试：未知 message_id 应静默返回，不追加幽灵消息。"""
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
    chat_snapshot.save("thread", [
        {"id": "existing", "role": "assistant", "text": "已知消息"},
    ])

    result = chat_snapshot.select_inspiration("thread", "unknown-id",
                                               ["http://img.com/test.png"])
    assert result == {"ok": True, "selected": ["http://img.com/test.png"]}
    # 不应追加幽灵消息
    assert len(chat_snapshot.load("thread")) == 1
    assert chat_snapshot.load("thread")[0]["id"] == "existing"


def test_select_inspiration_过滤非http协议(monkeypatch, tmp_path):
    """N3 测试：仅接受 http(s) URL，过滤 file:// 等非法协议。"""
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
    chat_snapshot.save("thread", [
        {"id": "msg-1", "role": "assistant", "text": "搜索结果",
         "inspiration": {"query": "test", "results": []}},
    ])

    result = chat_snapshot.select_inspiration("thread", "msg-1",
                                               ["http://a.com/1.png", "file:///etc/passwd", "   "])
    assert result == {"ok": True, "selected": ["http://a.com/1.png"]}
    loaded = chat_snapshot.load("thread")
    assert loaded[0]["inspiration"]["selected"] == ["http://a.com/1.png"]


def test_音频槽追加保留已有图片槽并写入分条元数据(monkeypatch, tmp_path):
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
    # 同轮先出图：消息已有图片槽
    chat_snapshot.save("thread", [{
        "id": "bot", "role": "assistant", "text": "正文",
        "parts": [
            {"type": "image", "url": "local://img", "slotId": "img-1", "status": "ready"},
        ],
    }])

    assert chat_snapshot.append_media_slot(
        "thread", "bot", "audio-0", speaker="苏映晚", seq=1, total=2,
    )
    parts = chat_snapshot.load("thread")[0]["parts"]
    # 图片槽保留，音频槽追加在末尾且带分条元数据
    assert parts == [
        {"type": "image", "url": "local://img", "slotId": "img-1", "status": "ready"},
        {"type": "media-slot", "slotId": "audio-0", "status": "pending",
         "kind": "audio", "speaker": "苏映晚", "seq": 1, "total": 2},
    ]


def test_音频槽纯文本消息补正文且幂等(monkeypatch, tmp_path):
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
    chat_snapshot.save("thread", [{
        "id": "bot", "role": "assistant", "text": "她低声道：「我认输。」",
    }])

    assert chat_snapshot.append_media_slot("thread", "bot", "audio-0")
    # 幂等：重复追加不产生第二个槽
    assert chat_snapshot.append_media_slot("thread", "bot", "audio-0")
    parts = chat_snapshot.load("thread")[0]["parts"]
    assert parts == [
        {"type": "text", "text": "她低声道：「我认输。」"},
        {"type": "media-slot", "slotId": "audio-0", "status": "pending", "kind": "audio"},
    ]



def test_认领缺槽时登记而非拒绝(tmp_path):
    """2026-08-30 实锤：前端防抖快照未写入 pending 槽 → 认领 False → 静默丢任务。"""
    thread = "t-claim"
    msgs = [{"id": "m1", "role": "assistant", "text": "剧情正文。"}]
    save(thread, msgs)

    assert claim_media_slot_submission(thread_id=thread, message_id="m1", slot_id="s1") is True
    # 已认领拒绝重复
    assert claim_media_slot_submission(thread_id=thread, message_id="m1", slot_id="s1") is False
    items = load(thread)
    parts = items[0]["parts"]
    slot = [p for p in parts if p.get("slotId") == "s1"][0]
    assert slot.get("submissionClaim") is True
    assert parts[0]["type"] == "text" and parts[0]["text"] == "剧情正文。"  # 正文转 part 保留


def test_bind槽位缺失时登记补齐(monkeypatch, tmp_path):
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
    save("thread", [{"id": "m1", "role": "assistant", "text": "正文"}])
    assert chat_snapshot.bind_media_slot_prompt("thread", "m1", "slot-1", "prompt-9")
    message = load("thread")[0]
    assert message["parts"][0] == {"type": "text", "text": "正文"}
    slot = message["parts"][-1]
    assert slot["slotId"] == "slot-1" and slot["promptId"] == "prompt-9"
    assert "submissionClaim" not in slot


def test_bind已存在槽位时原地落promptId(monkeypatch, tmp_path):
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
    save("thread", [{"id": "m1", "role": "assistant", "text": "",
                     "parts": [{"type": "media-slot", "slotId": "s1", "status": "pending",
                                "submissionClaim": True}]}])
    assert chat_snapshot.bind_media_slot_prompt("thread", "m1", "s1", "p1")
    message = load("thread")[0]
    assert len(message["parts"]) == 1
    assert message["parts"][0]["promptId"] == "p1"
    assert "submissionClaim" not in message["parts"][0]


def test_旧快照trace占位不覆盖服务端已落盘正文(monkeypatch, tmp_path):
    """2026-09-01 用户实锤：切工作流/画布页后前端旧状态保存，把已生成正文覆盖成
    「🧭 主管分派 → 剧情扮演」占位。save_if_newer 必须保留服务端完整正文。"""
    from app.services import repo_meta
    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
    full_body = "完整剧情正文" * 40
    save("thread-trace-guard", [{"id": "m1", "role": "assistant", "text": full_body}])

    accepted = chat_snapshot.save_if_newer(
        "thread-trace-guard",
        [{"id": "m1", "role": "assistant", "text": "🧭 主管分派 → 剧情扮演"}],
        revision=1,
    )

    assert accepted is True
    message = load("thread-trace-guard")[0]
    assert message["text"] == full_body  # 服务端正文不被 trace 占位覆盖
