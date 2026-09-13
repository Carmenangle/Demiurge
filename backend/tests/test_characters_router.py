from __future__ import annotations

import json

from app.routers import characters
from app.services import character_store


def test_角色卡详情保存接口写回三个正文字段(tmp_path):
    base = str(tmp_path / "cards")
    folder = character_store.card_dir(base, "露娜")
    folder.mkdir(parents=True)
    (folder / character_store.CARD_FILE).write_text(
        json.dumps({"name": "露娜", "description": "旧值"}, ensure_ascii=False), encoding="utf-8",
    )
    request = characters.CardUpdateRequest(
        base=base, name="露娜", description="银发蓝眼",
        first_mes="欢迎回来", creator_notes="测试卡",
    )

    result = characters.update_character(request)

    assert result["description"] == "银发蓝眼"
    assert result["first_mes"] == "欢迎回来"
    assert result["creator_notes"] == "测试卡"
