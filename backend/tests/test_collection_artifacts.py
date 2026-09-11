"""合集卡产物收集 / 访问端点测试：收集、目录 jail、预览、下载、打开位置。"""
from __future__ import annotations

import json
import os as _os
import time as _time
from pathlib import Path

import pytest

from app.services import collection_artifacts as artifacts
from app.services import chat_stream_protocol as protocol
from app.services import repo_meta
from app.routers import artifacts as artifacts_router


@pytest.fixture()
def sample_works(tmp_path: Path) -> Path:
    """构造一个作品根：<根>/玫瑰与繁花/card.json + worldbook.json；_prep 与根级文件垫底。"""
    root = tmp_path / "works"
    work = root / "玫瑰与繁花"
    work.mkdir(parents=True)
    (work / "card.json").write_text(
        json.dumps({"name": "玫瑰与繁花", "character_book": {"entries": []}}, ensure_ascii=False),
        encoding="utf-8",
    )
    (work / "worldbook.json").write_text(
        json.dumps({"entries": [{"key": "机制"}]}, ensure_ascii=False), encoding="utf-8",
    )
    prep = root / "_prep"
    prep.mkdir()
    (prep / "card.json").write_text("{}", encoding="utf-8")  # 不该被收集
    (root / "notes.txt").write_text("杂项", encoding="utf-8")
    return root


def _register_work_repo(monkeypatch) -> str:
    """把 sample_works 的「玫瑰与繁花」当成已登记仓库，返回可用的 repo_id。

    B3/A（2026-09-10）起读端点**必须带 repo_id**：缺省 = 不给任何域
    （列表回空 / 单文件 403，见 `test_artifact_domains.py` 的缺省策略用例）。
    本文件的路由类用例考的是路由机制（scope 校验 / 响应形状 / 可替换绑定），
    一律经此拿 id，不要再传 `repo_id=""` 去撞新策略。
    """
    monkeypatch.setattr(repo_meta, "_load_state",
                        lambda: {"repos": [{"id": "r-mrh", "name": "玫瑰与繁花"}]})
    return "r-mrh"


class TestCollect:
    def test_收集主卡与世界书并带元数据(self, sample_works: Path):
        items = artifacts.collect_artifacts(str(sample_works))
        names = [i["name"] for i in items]
        assert any("角色主卡" in n and "玫瑰与繁花" in n for n in names)
        assert any("世界书" in n and "玫瑰与繁花" in n for n in names)
        card = next(i for i in items if i["name"].startswith("角色主卡"))
        assert card["kind"] == "card"
        assert card["path"].endswith("card.json")
        assert card["size"] > 0
        wb = next(i for i in items if i["name"].startswith("世界书"))
        assert wb["kind"] == "worldbook"

    def test_排除_prep与子目录之外的杂物(self, sample_works: Path):
        items = artifacts.collect_artifacts(str(sample_works))
        # 注意：pytest 临时目录名可能含测试函数名（"_prep" 子串），只按路径段断言
        paths = [Path(i["path"]) for i in items]
        assert all("_prep" not in p.parts for p in paths)
        assert all(p.name != "notes.txt" for p in paths)

    def test_空或不存在目录返回空列表(self, tmp_path: Path):
        assert artifacts.collect_artifacts(str(tmp_path / "nope")) == []
        assert artifacts.collect_artifacts("") == []

    def test_最新目录聚合_不混入历史作品的交付物(self, sample_works: Path):
        """2026-09-08：产物卡只展示「最近一次被写入」的目录——历史作品更旧，
        修改其 mtime 后混入同一根，也不得出现在本次产物里。"""
        other = sample_works / "神权大陆"
        other.mkdir()
        (other / "card.json").write_text("{}", encoding="utf-8")
        # 神权大陆本是最新创建的——人为调旧，让「最新目录」= 玫瑰与繁花
        old = _time.time() - 7200
        _os.utime(other / "card.json", (old, old))
        items = artifacts.collect_artifacts(str(sample_works))
        paths = [Path(i["path"]) for i in items]
        assert all(Path("神权大陆") not in p.parts for p in paths)
        names = [p.name for p in paths]
        assert "card.json" in names and "worldbook.json" in names

    def test_最新目录无世界书时补充根级世界书(self, sample_works: Path):
        # 玫瑰与繁花只有主卡：worldbook.json 移到根级，并把 card 时间戳设为最新
        work = sample_works / "玫瑰与繁花"
        (work / "worldbook.json").rename(sample_works / "worldbook.json")
        now = _time.time()
        _os.utime(work / "card.json", (now, now))
        items = artifacts.collect_artifacts(str(sample_works))
        names = [Path(i["path"]).name for i in items]
        assert "card.json" in names
        assert "worldbook.json" in names  # 根级副本被补充

    def test_只收最新目录且组内按修改时间倒序(self, tmp_path: Path):
        root = tmp_path / "w"
        old = root / "旧作品"
        old.mkdir(parents=True)
        (old / "card.json").write_text("{}", encoding="utf-8")
        # 让旧作品目录时间戳明显更旧
        old_time = _time.time() - 3600
        _os.utime(old / "card.json", (old_time, old_time))
        fresh = root / "新作品"
        fresh.mkdir(parents=True)
        # 同目录两个交付物：card 早 10 秒、worldbook 最新 → 组内倒序 worldbook 在前
        (fresh / "card.json").write_text("{}", encoding="utf-8")
        (fresh / "worldbook.json").write_text('{"entries":[]}', encoding="utf-8")
        now = _time.time()
        _os.utime(fresh / "card.json", (now - 10, now - 10))
        _os.utime(fresh / "worldbook.json", (now, now))
        items = artifacts.collect_artifacts(str(root))
        # 只收最新目录（新作品）的两个交付物，旧作品不混入
        assert len(items) == 2
        assert all("旧作品" not in i["path"] for i in items)
        mt = [i["mtime"] for i in items]
        assert mt == sorted(mt, reverse=True)

    def test_since过滤只收本轮写入的产物(self, tmp_path: Path):
        """2026-09-09 用户定案（通用，不针对某个作品）：产物卡必须对应**本轮任务**
        实际写入的产物——since = 本轮开始时间，早于它的（上一轮/历史作品）一律不收。

        实锤：御仙 ST 卡任务没有产出时，产物卡把上一轮「玫瑰与繁花」当成本轮产物。
        """
        root = tmp_path / "w"
        old = root / "上一轮作品"
        old.mkdir(parents=True)
        (old / "card.json").write_text("{}", encoding="utf-8")
        (old / "worldbook.json").write_text('{"entries":[]}', encoding="utf-8")
        old_time = _time.time() - 3600
        _os.utime(old / "card.json", (old_time, old_time))
        _os.utime(old / "worldbook.json", (old_time, old_time))
        # 本轮开始时间（上一轮之后、本轮写入之前）
        run_started = _time.time() - 5
        # 本轮未写任何产物 → 空（不再退回上一轮产物）
        assert artifacts.collect_artifacts(str(root), since=run_started) == []
        # 本轮写入新作品 → 只收新作品
        new = root / "本轮作品"
        new.mkdir()
        (new / "card.json").write_text("{}", encoding="utf-8")
        (new / "worldbook.json").write_text('{"entries":[]}', encoding="utf-8")
        items = artifacts.collect_artifacts(str(root), since=run_started)
        assert items and all("本轮作品" in i["path"] for i in items)
        assert all("上一轮作品" not in i["path"] for i in items)
        # since=0 退回旧行为（最新组），供历史消息补卡等无 run 时间场景
        assert artifacts.collect_artifacts(str(root)) != []

    def test_文档收集超过上限时取最新而非字母序前若干(self, tmp_path: Path):
        """2026-09-10 A3：docs/ 是只增不减的累积目录，超过 `_MAX_DOCS` 时必须保最新。

        原实现按文件名排序取前 8：第 9 份文档只要名字排在字母序后面，就永远进不了
        产物卡——用户会看到「刚写完的文档没出现在产物列表里」的静默丢失。收集语义是
        「最近的交付物」，因此先按 mtime 倒序再截断。
        """
        root = tmp_path / "w"
        docs = root / "docs"
        docs.mkdir(parents=True)
        now = _time.time()
        for index in range(9):
            old = docs / f"{index:02d}_旧稿.md"
            old.write_text(f"# 旧稿 {index}", encoding="utf-8")
            stamp = now - 1000 + index
            _os.utime(old, (stamp, stamp))
        newest = docs / "设定总集.md"  # 字母序排在所有「0X_旧稿」之后
        newest.write_text("# 设定总集", encoding="utf-8")
        _os.utime(newest, (now, now))

        items = artifacts.collect_artifacts(str(root))
        names = [Path(i["path"]).name for i in items]
        assert len(items) == 8                     # 上限生效
        assert names[0] == "设定总集.md"            # 最新的一份在最前
        assert "08_旧稿.md" in names               # 次新的保留
        assert "01_旧稿.md" not in names           # 超出上限的最旧者被挤出
        assert [i["mtime"] for i in items] == sorted(
            [i["mtime"] for i in items], reverse=True)


class TestResolve:
    def test_正常文件通过(self, sample_works: Path):
        path = str(sample_works / "玫瑰与繁花" / "card.json")
        assert artifacts.resolve_artifact(str(sample_works), path).is_file()

    def test_目录穿越被拒(self, sample_works: Path):
        with pytest.raises(artifacts.ArtifactAccessError) as ei:
            artifacts.resolve_artifact(str(sample_works), str(sample_works.parent / "outside.json"))
        assert ei.value.status == 403

    def test_非白名单文件被拒(self, sample_works: Path):
        with pytest.raises(artifacts.ArtifactAccessError) as ei:
            artifacts.resolve_artifact(str(sample_works), str(sample_works / "notes.txt"))
        assert ei.value.status == 403

    def test_文件不存在返回404(self, sample_works: Path):
        with pytest.raises(artifacts.ArtifactAccessError) as ei:
            artifacts.resolve_artifact(str(sample_works), str(sample_works / "卡" / "card.json"))
        assert ei.value.status == 404

    def test_目录默认拒绝_allow_dir放行定位(self, sample_works: Path):
        work = sample_works / "玫瑰与繁花"
        with pytest.raises(artifacts.ArtifactAccessError):
            artifacts.resolve_artifact(str(sample_works), str(work))
        assert artifacts.resolve_artifact(str(sample_works), str(work), allow_dir=True).is_dir()


class TestProtocol:
    def test_artifacts事件编码为版本化判定联合(self):
        event = protocol.encode_event({"artifacts": [
            {"kind": "card", "name": "角色主卡 · 玫瑰与繁花",
             "path": r"D:\works\card.json", "size": 85764, "mtime": 1234.5},
        ]})
        assert event["type"] == "artifacts"
        assert event["data"]["items"][0]["kind"] == "card"
        assert event["data"]["items"][0]["size"] == 85764

    def test_artifacts事件字段类型归一(self):
        event = protocol.encode_event({"artifacts": [
            {"kind": "card", "name": 42, "path": None, "size": "1024", "mtime": "1.5"},
        ]})
        item = event["data"]["items"][0]
        assert item["name"] == "42"
        # 空 path（None）归一为空串，前端宽松忽略空路径产物
        assert item["path"] == ""
        assert isinstance(item["size"], int) and item["size"] == 1024
        assert isinstance(item["mtime"], float)


class TestFabricFinalize:
    """2026-09-09 收紧：仅 done 下发产物卡——未完成/需要批准的中间消息
    不再携带产物，否则历史消息会重复展示同一批「当前最新产物」。"""

    @staticmethod
    def _make_works(tmp_path: Path) -> Path:
        work = tmp_path / "玫瑰与繁花"
        work.mkdir(parents=True)
        (work / "card.json").write_text('{"name":"x"}', encoding="utf-8")
        return tmp_path

    def _finalize(self, tmp_path: Path, status: str):
        from types import SimpleNamespace
        from app.services import agent_graph
        outcome = SimpleNamespace(status=status, reply="已写完一批", error="步数上限",
                                  steps=[], messages=[])
        return agent_graph._fabric_finalize(outcome, "补写", ["trace"], {}, str(tmp_path))

    def test_only_done下发产物卡_未完成不下发(self, tmp_path: Path):
        """2026-09-09：中间消息（step_limit/error）不得再带产物——产物卡只属于
        完成态；未完成消息前端也不渲染产物卡组件（autoLoad 随之收紧）。"""
        self._make_works(tmp_path)
        done = self._finalize(tmp_path, "done")
        assert done["artifacts"], "done 必须带产物卡"
        for status in ("step_limit", "error"):
            partial = self._finalize(tmp_path, status)
            assert "artifacts" not in partial, f"{status} 中断不得带产物卡"
            assert partial["result_text"].startswith("智能编造执行未完成")

    def test_无落盘产物时artifacts为空列表不报错(self, tmp_path: Path):
        out = self._finalize(tmp_path, "done")
        assert out["artifacts"] == []

    def test_AgentState必须声明artifacts键防LangGraph静默丢弃(self):
        """2026-09-08 实锤：TypedDict state 未声明 artifacts → LangGraph 把它从节点
        输出里静默丢弃（upd 无该键 → SSE 事件永不发出 → 快照无产物卡）。
        本测试守护声明存在，防止以后重构删掉。"""
        from app.services import agent_graph
        assert "artifacts" in agent_graph.AgentState.__annotations__, (
            "AgentState 缺少 artifacts 声明，LangGraph 会静默丢弃该键（产物卡事件失效）")


class TestRouter:
    @staticmethod
    def _as_repo(monkeypatch) -> str:
        """见模块级 `_register_work_repo`（两个类共用同一份登记口径）。"""
        return _register_work_repo(monkeypatch)

    def test_preview_返回文本与元信息(self, sample_works: Path, tmp_path: Path, monkeypatch):
        # works 根未配置(空)时放行 scope(与现有端一致)
        monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
        repo_id = self._as_repo(monkeypatch)
        path = str(sample_works / "玫瑰与繁花" / "card.json")
        result = artifacts_router.artifact_preview(
            output_dir=str(sample_works), path=path, repo_id=repo_id,
        )
        assert result["ok"] is True
        assert result["name"] == "card.json"
        assert result["kind"] == "card"
        assert "玫瑰与繁花" in result["text"]
        assert result["truncated"] is False

    def test_download_返回文件响应(self, sample_works: Path, monkeypatch):
        monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
        repo_id = self._as_repo(monkeypatch)
        from fastapi.responses import FileResponse
        resp = artifacts_router.artifact_download(
            output_dir=str(sample_works), path=str(sample_works / "玫瑰与繁花" / "worldbook.json"),
            repo_id=repo_id,
        )
        assert isinstance(resp, FileResponse)
        original = (sample_works / "玫瑰与繁花" / "worldbook.json").read_bytes()
        assert original.startswith(b'{"entries"')

    def test_open_folder_打开失败时降级ok_false(self, sample_works: Path, monkeypatch):
        """explorer 不可用（非 Windows / 失败）时降级 ok=false，前端据此提示降级。

        2026-09-10 事故：本用例原叫「非Windows环境降级ok_false」，却 patch 了
        `collection_artifacts.reveal_in_folder`——路由用的是**自己模块里的 import 绑定**
        （`routers/artifacts.py: from ... import reveal_in_folder`），根本打不到，于是真的
        `Popen(explorer /select,)` 弹了资源管理器窗口；断言还只写 `body is not None`，
        所以一直假绿。现在替换路由绑定并断言真实降级结果（会话级兜底见 conftest）。
        """
        monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
        repo_id = self._as_repo(monkeypatch)
        card = sample_works / "玫瑰与繁花" / "card.json"
        monkeypatch.setattr(artifacts_router, "reveal_in_folder",
                            lambda path, select=True: False)
        resp = artifacts_router.artifact_open_folder(artifacts_router.OpenFolderRequest(
            output_dir=str(sample_works), path=str(card), repo_id=repo_id,
        ))
        data = json.loads(resp.body)
        assert data["ok"] is False
        assert Path(data["path"]).name == "card.json"

    def test_open_folder_成功路径回ok_true且透传select(self, sample_works: Path, monkeypatch):
        """反向：替换成 True 时路由如实回 ok=true，并透传 select——把「路由走的是可替换
        绑定」这一契约固定下来（换回直连服务模块就会在此暴露）。"""
        monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
        repo_id = self._as_repo(monkeypatch)
        card = sample_works / "玫瑰与繁花" / "card.json"
        seen: dict[str, object] = {}

        def _fake(path, select=True):
            seen["path"], seen["select"] = path, select
            return True

        monkeypatch.setattr(artifacts_router, "reveal_in_folder", _fake)
        resp = artifacts_router.artifact_open_folder(artifacts_router.OpenFolderRequest(
            output_dir=str(sample_works), path=str(card), select=False, repo_id=repo_id,
        ))
        assert json.loads(resp.body)["ok"] is True
        assert seen["select"] is False
        assert Path(seen["path"]).name == "card.json"

    def test_list_返回当前作品产物供历史消息补卡(self, sample_works: Path, monkeypatch):
        monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
        repo_id = self._as_repo(monkeypatch)
        result = artifacts_router.artifact_list(output_dir=str(sample_works), repo_id=repo_id)
        assert result["ok"] is True
        names = [Path(i["path"]).name for i in result["items"]]
        assert "card.json" in names and "worldbook.json" in names

    def test_list_缺省repo_id回空不串味(self, sample_works: Path, monkeypatch):
        """2026-09-10 用户定案：不给 repo_id 时列表回空 items（而非退回全库 → 刷出别人产物）。"""
        monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
        assert artifacts_router.artifact_list(
            output_dir=str(sample_works), repo_id="") == {"ok": True, "items": []}

    def test_list_works根已配置且不匹配时400(self, sample_works: Path, monkeypatch):
        # works 根配置为别的目录 → 传作品根被拒（scope 校验生效）
        monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: str(sample_works.parent))
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as ei:
            artifacts_router.artifact_list(output_dir=str(sample_works), repo_id="")
        assert ei.value.status_code == 400

    def test_works_root已配置时正反斜杠等价通过(self, sample_works: Path, monkeypatch):
        """2026-09-08：works_root_violation 字符串直比把 D:/x 与 D:\\x 判成不等→400。
        归一并大小写后：配置了仓库文件夹时，等价路径必须通过。"""
        monkeypatch.setattr(repo_meta, "output_dir_from_state",
                            lambda: str(sample_works).replace("/", _os.sep))
        repo_id = self._as_repo(monkeypatch)
        resp = artifacts_router.artifact_preview(
            output_dir=str(sample_works).replace(_os.sep, "/"),  # 故意用反斜杠/正斜杠混合形式
            path=str(sample_works / "玫瑰与繁花" / "card.json"),
            repo_id=repo_id,
        )
        assert resp["ok"] is True
        # 配置了根但传入别的目录 → 400
        monkeypatch.setattr(repo_meta, "output_dir_from_state",
                            lambda: str(sample_works.parent))
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as ei:
            artifacts_router.artifact_preview(
                output_dir=str(sample_works),
                path=str(sample_works / "玫瑰与繁花" / "card.json"),
                repo_id=repo_id,
            )
        assert ei.value.status_code == 400

    def test_测试环境屏蔽系统弹窗(self, sample_works: Path):
        """conftest 的 `_block_os_folder_reveal` 必须生效。

        2026-09-10 事故：全量测试每跑一次就真弹一个资源管理器窗口（指向 pytest 临时
        目录），因为唯一「模拟非 Windows」的用例 patch 错了目标。本用例把「测试环境一律
        屏蔽系统弹窗」钉死——两处绑定都得是降级桩，谁删掉 conftest 那条兜底就会在这里红。
        """
        assert artifacts_router.reveal_in_folder(sample_works) is False
        assert artifacts.reveal_in_folder(sample_works) is False

    def test_open_folder_目录越界返回400(self, sample_works: Path, monkeypatch):
        monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
        repo_id = _register_work_repo(monkeypatch)
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as ei:
            artifacts_router.artifact_open_folder(artifacts_router.OpenFolderRequest(
                output_dir=str(sample_works),
                path=str(sample_works.parent / "evil.json"),
                repo_id=repo_id,
            ))
        assert ei.value.status_code == 403


class TestAssetEndpoint:
    """C4（2026-09-10）：`/asset` 按语义只是给 `<img src>` 用的插图端点。

    旧行为：任何**白名单内**产物都内联返回（card.json 也能拿到 application/json 内联
    响应），端点用途与白名单面不等价。现在收口到 `DOC_ASSET_SUFFIXES`（与产物白名单、
    doc.attach_material 同一属主）。
    """

    _PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8

    @staticmethod
    def _seed_doc(sample_works: Path) -> tuple[Path, Path]:
        """B3/A 后 docs/ 落**作品域**（<根>/玫瑰与繁花/docs/），不再落作品库根。"""
        assets = sample_works / "玫瑰与繁花" / "docs" / "assets"
        assets.mkdir(parents=True, exist_ok=True)
        png = assets / "pic.png"
        png.write_bytes(TestAssetEndpoint._PNG)
        doc = sample_works / "玫瑰与繁花" / "docs" / "设定总集.md"
        doc.write_text("# 设定总集\n", encoding="utf-8")
        return png, doc

    def test_插图素材按图片类型内联返回(self, sample_works: Path, monkeypatch):
        monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
        repo_id = _register_work_repo(monkeypatch)
        png, _ = self._seed_doc(sample_works)
        from fastapi.responses import FileResponse
        resp = artifacts_router.artifact_asset(output_dir=str(sample_works), path=str(png),
                                               repo_id=repo_id)
        assert isinstance(resp, FileResponse)
        # 内联（不带 attachment），否则浏览器不会把 <img src> 当图片渲染
        assert resp.media_type == "image/png"
        assert "attachment" not in str(resp.headers.get("content-disposition", "")).lower()

    def test_文档与主卡不走asset而是400(self, sample_works: Path, monkeypatch):
        monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
        repo_id = _register_work_repo(monkeypatch)
        _, doc = self._seed_doc(sample_works)
        from fastapi import HTTPException
        for target in (doc, sample_works / "玫瑰与繁花" / "card.json"):
            with pytest.raises(HTTPException) as ei:
                artifacts_router.artifact_asset(output_dir=str(sample_works), path=str(target),
                                                repo_id=repo_id)
            assert ei.value.status_code == 400
            assert "/asset 只服务文档插图素材" in ei.value.detail

    def test_非白名单文件仍先被目录jail与白名单拦(self, sample_works: Path, monkeypatch):
        """加固不放松既有防线：越界/非白名单文件仍是 403/404，而不是 400 后缀错。"""
        monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
        repo_id = _register_work_repo(monkeypatch)
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as ei:
            artifacts_router.artifact_asset(
                output_dir=str(sample_works), path=str(sample_works.parent / "外面.png"),
                repo_id=repo_id)
        assert ei.value.status_code == 403
        stray = sample_works / "玫瑰与繁花" / "杂项.png"
        stray.write_bytes(self._PNG)
        with pytest.raises(HTTPException) as ei2:
            artifacts_router.artifact_asset(output_dir=str(sample_works), path=str(stray),
                                            repo_id=repo_id)
        assert ei2.value.status_code == 403

