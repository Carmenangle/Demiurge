import pytest


@pytest.fixture(autouse=True)
def _isolate_process_local_model_leases():
    """进程级调度状态不能从一个测试泄漏到下一个测试。"""
    from app.services import model_lease

    model_lease._reset_for_tests()
    yield
    model_lease._reset_for_tests()


@pytest.fixture(autouse=True)
def _isolate_capability_leases(tmp_path, monkeypatch):
    """能力租约测试走临时文件，绝不碰真实 backend/data/capability_leases.json。

    grant/revoke 的 _save_persisted 与 _reset_for_tests 的 unlink 都作用于模块级
    LEASE_FILE；若指向真实运行态文件，全量测试会累积写入并在 unlink 时被安全删除
    钩子判成「批量删除」抛 SystemExit(1) 整批挂（2026-09-03 实锤 28 例 setup 级失败）。
    重定向到 tmp_path + 每测试前后清内存租约，同时满足 CI 封闭化合同（测试禁止
    依赖/污染 backend/data 私有数据）。
    """
    from app.services import capability_sandbox

    monkeypatch.setattr(capability_sandbox, "LEASE_FILE",
                        tmp_path / "capability_leases.json")
    capability_sandbox._reset_for_tests()
    yield
    capability_sandbox._reset_for_tests()


@pytest.fixture(autouse=True)
def _isolate_task_progress_store(tmp_path, monkeypatch):
    """任务进度快照一律落 tmp，绝不碰真实 backend/data/task_progress/*.json（2026-09-10）。

    所有命名空间文件（`fabric_checkpoints` / `plan_recipes` / `recipe_offers` /
    `downloads` / `plan_tasks`…）都经 `task_progress_store._path` 落同一个模块级
    `STORE_DIR`。此前只靠各用例自己 `monkeypatch.setattr(task_progress_store,
    "STORE_DIR", tmp_path)`，于是**新写的生产写入路径一旦没被显式重定向就会漏**：
    09-10 全量测试实锤——真实 `fabric_checkpoints.json` 被覆盖成 `{}`（若用户正停在
    fabric 审批断点上，跑一次测试就把断点抹了），且 `recipe_offers.json` 落进一条
    output_dir 指向已删除 pytest 临时目录的脏询问。

    这里统一收口，新存储不必再自带隔离样板；用例要断言别的目录仍可自行覆盖本绑定。
    """
    from app.services import task_progress_store

    monkeypatch.setattr(task_progress_store, "STORE_DIR", tmp_path / "task_progress")


@pytest.fixture(autouse=True, scope="session")
def _isolate_run_trace(tmp_path_factory):
    """Agent trace 全程落 tmp，绝不碰真实 backend/data/logs/agent-trace.jsonl（2026-09-15 P2）。

    **必须是会话级，不能只做函数级** —— 这是踩过才定下的：函数级 monkeypatch 会在每个用例
    teardown 时把 `TRACE_FILE` 还原成真实路径，而 agent_graph 的**后台维护线程**（table_maintenance /
    belief.extract / style_review，见 `agent_graph._run_maintenance` 一链）是在用例结束后才 emit 的
    ⇒ 那几行会落回真实文件。实测：函数级隔离下跑 4 个 emit 密集文件仍向真实文件写入 546 字节
    （新增行时间戳正好压在该批收尾秒）。会话级把真实路径锁一整个 session，线程再晚也落在 tmp。

    背景（为什么值得单独一个 fixture）：此前触发真实 `emit` 的用例会写真实 trace，其轮转 `_rotate`
    在超 `MAX_BYTES` 时 `unlink`/`replace` 备份，反复触发会撞沙箱 safe-delete 守卫抛 `SystemExit(1)`
    ——**不是 `Exception`**，`emit` 的 `except Exception` 兜不住，于是整批假失败（堆栈签名固定为
    `emit → _rotate(TRACE_FILE)`，留档 `backend/_tmp/pytest_lease.txt`、`junit-p0p1-full.xml`）。
    重定向到 tmp 后文件远小于 `MAX_BYTES` ⇒ `_rotate` 早返回，**根本不产生 unlink**。

    全仓**没有** `from app.services.run_trace import TRACE_FILE` 式冻结绑定，调用方一律
    `run_trace.emit(...)` / `run_trace.read_recent(...)` 走模块属性 ⇒ 改模块属性即覆盖全部调用方。
    用例要断言别的目录/轮转行为，可在自己函数体里再 `monkeypatch.setattr(run_trace, "TRACE_FILE", ...)`
    （后设者胜，`test_run_trace.py` / `test_run_trace_read.py` / `test_agent_dispatch.py` 即此）。
    """
    from app.services import run_trace

    trace_dir = tmp_path_factory.mktemp("agent-trace")
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(run_trace, "TRACE_DIR", trace_dir)
    monkeypatch.setattr(run_trace, "TRACE_FILE", trace_dir / "agent-trace.jsonl")
    yield trace_dir
    monkeypatch.undo()


# ---------------------------------------------------------------------------
# 2026-09-15 P3：其余模块级存储常量补进 conftest（与上一批同口径）。
#
# 这批常量都指向**真实 backend/data 运行态**，此前只靠各用例自己
# `monkeypatch.setattr`，于是任何「新写的生产写入路径」一旦没被显式重定向就会漏
# （09-10 task_progress_store、09-15 run_trace 两次事故同款成因）。
#
# 收口前实测（全量 pytest 前后对比 backend/data 全量文件的 size+mtime，
# 脚本 `_tmp/data_snapshot.py`，快照 `_tmp/snap_A|B.json`）：**7 个真实文件被写** ——
# `chat_revisions.json`、`chroma/chroma.sqlite3`、`comfy_extra_paths.yaml`、
# `flow_docs/{repo-1,repo-123,r1,work}.json`。补完本组 fixture 后应回落为 0。
#
# 经 grep 确认：attachment_store / chat_snapshot / build_session_store / procedure_skills /
# prompt_approval_store / scenario_lab 六者全仓**零**「`from <模块> import <常量>`」式
# 跨模块冻结绑定（调用方一律走模块属性）⇒ patch 模块属性即覆盖全部调用方。
# 唯 CHROMA_DIR 例外：被 4 个模块 `from app.config import CHROMA_DIR` 冻结。
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_attachment_store(tmp_path, monkeypatch):
    """对话附件一律落 tmp，绝不碰真实 backend/data/attachments/。"""
    from app.services import attachment_store

    monkeypatch.setattr(attachment_store, "ATTACH_ROOT", tmp_path / "attachments")


@pytest.fixture(autouse=True)
def _isolate_chat_snapshot(tmp_path, monkeypatch):
    """会话快照 + 修订表落 tmp，绝不碰真实 backend/data/chat_snapshots|chat_revisions.json。

    `_REVISIONS_PATH` 是**收口前实测泄漏点**（`_save_revisions` 直接 mkdir+write，无条件写）。
    注意：仓库文件夹可解析时快照走仓库目录 —— 那是作品数据不是运行态，本 fixture 只管
    `SNAP_DIR` 这个「旧位置/未配置仓库文件夹时的回退」。
    """
    from app.services import chat_snapshot

    monkeypatch.setattr(chat_snapshot, "SNAP_DIR", tmp_path / "chat_snapshots")
    monkeypatch.setattr(chat_snapshot, "_REVISIONS_PATH", tmp_path / "chat_revisions.json")


@pytest.fixture(autouse=True)
def _isolate_library_root(tmp_path, monkeypatch):
    """`user_state.json` 单一属主的读源落 tmp ⇒ 测试永远解析不出真实库根（**收口前实测泄漏点**）。

    **向量（2026-09-17 实测）**：`chat_snapshot._path()` 第一件事就是问属主
    `repo_meta.output_dir_from_state()`。真实 `backend/data/user_state.json` 里
    `settings.outputDir` 一旦配了（本机 = `D:/tool/Demiurge/userdata/pictures`），
    `_path("<任意 thread_id>")` 就解析成 `<库根>/<thread_id>/chat.json` ——
    上面 `_isolate_chat_snapshot` 只盖住 `SNAP_DIR` 那条**回退**分支，挡不住这个：
    output_dir 非空时压根不走 SNAP_DIR。实测（`_path('probe-leak-check')`）确为
    `D:\\tool\\Demiurge\\userdata\\pictures\\probe-leak-check\\chat.json`。
    后果：任何碰 `chat_snapshot` 却没自行 patch 的用例，都会在用户真实图库里留下
    以 thread_id 命名的夹具目录（库根现存 `thread/`、`t1/`、`x/`、`work/`、
    `t-claim/`、`zzz-insp-diag/`、`SAVE01 [09f79e6f]/` 等即此残留）。

    **钉 `repo_meta.DATA_DIR` 而不是钉 `output_dir_from_state` 函数**：前者是这批
    测试既有的接线缝（`test_artifacts_sync_worldbook._wire` 等 30+ 处 patch 的就是它，
    后设者胜 ⇒ 那些用例语义不变）；钉函数会把「用例自建 tmp 状态桩」的读法整个旁路掉
    （首轮实测 15 例红，全是这条）。DATA_DIR 指向空 tmp ⇒ 未配置 ⇒ 快照回退
    SNAP_DIR（已被 `_isolate_chat_snapshot` 指向 tmp）。
    要断言「配置了库根」的用例照旧自行写 tmp `user_state.json` 并 patch 本绑定。
    """
    from app.services import repo_meta

    monkeypatch.setattr(repo_meta, "DATA_DIR", tmp_path / "data")


@pytest.fixture(autouse=True)
def _isolate_build_session_store(tmp_path, monkeypatch):
    """AI 搭工作流的会话态落 tmp，绝不碰真实 backend/data/build_sessions/。"""
    from app.services import build_session_store

    monkeypatch.setattr(build_session_store, "SESS_DIR", tmp_path / "build_sessions")


@pytest.fixture(autouse=True)
def _isolate_procedure_skills(tmp_path, monkeypatch):
    """流程技能库落 tmp，绝不碰真实 backend/data/procedure_skills.json。"""
    from app.services import procedure_skills

    monkeypatch.setattr(procedure_skills, "STORE", tmp_path / "procedure_skills.json")


@pytest.fixture(autouse=True)
def _isolate_prompt_approval_store(tmp_path, monkeypatch):
    """提示词审批表落 tmp，绝不碰真实 backend/data/prompt_approvals.json。"""
    from app.services import prompt_approval_store

    monkeypatch.setattr(prompt_approval_store, "APPROVALS_FILE", tmp_path / "prompt_approvals.json")


@pytest.fixture(autouse=True)
def _isolate_scenario_lab(tmp_path, monkeypatch):
    """情景实验的快照/实验区落 tmp，绝不碰真实 backend/data/scenario_{snapshots,experiments}/。"""
    from app.services import scenario_lab

    monkeypatch.setattr(scenario_lab, "SNAPSHOT_ROOT", tmp_path / "scenario_snapshots")
    monkeypatch.setattr(scenario_lab, "EXPERIMENT_ROOT", tmp_path / "scenario_experiments")


@pytest.fixture(autouse=True)
def _isolate_flow_context(tmp_path, monkeypatch):
    """流程文档句柄落 tmp，绝不碰真实 backend/data/flow_docs/（**收口前实测泄漏点**）。

    `flow_context._root()` 在**调用时**才 `from app.config import DATA_DIR`；这里直接把该
    解析函数指向 tmp —— 比改 `config.DATA_DIR` 安全（后者会连带影响 8 个「调用时读者」，
    其中读设置的都是 `repo_meta` 属主；2026-09-15 P8 前 `routers/artifacts.py` 的
    `sync-worldbook` 也直读真实 `user_state.json`，已收口）。

    ⚠ 顺带一条跨 fixture 的事故留档：**别让 `config.DATA_DIR` 指向真实盘的情况出现在
    「会让端点按真实设置落盘」的用例里** —— P8 实测，`sync-worldbook` 的目标目录来自
    `user_state.json` 的 `worldbookDir`，没盖住就会把用户真实资产库
    `userdata/worlds/<卡名>.json` 覆写掉（事故与恢复见 `tests/test_artifacts_sync_worldbook.py` 模块 docstring）。
    """
    from app.services import flow_context

    monkeypatch.setattr(flow_context, "_root", lambda: tmp_path / "flow_docs")


@pytest.fixture(autouse=True)
def _isolate_chroma_store(tmp_path, monkeypatch):
    """向量库持久化目录落 tmp，绝不碰真实 backend/data/chroma/（**收口前实测泄漏点**）。

    为什么两层一起上：全仓有 **5 个 chroma 客户端站点、三种解析方式** ——
    ① `scenario_lab` / `visual_asset_index` / `output_path_migration` 用**冻结常量**
       `CHROMA_DIR`（`from app.config import CHROMA_DIR`，改 config 属性覆盖不到）；
    ② `rag_backend.store()` 走 langchain 的 `Chroma(persist_directory=CHROMA_DIR)`
       （不是 `chromadb.PersistentClient`，边界桩盖不住，只能改它的模块常量）；
    ③ `repo_meta._rewrite_paths` 是**调用时**才 `DATA_DIR / "chroma"`，既无常量可改、
       也非 langchain 路径。
    ⇒ 常量层改 ① ②，`chromadb.PersistentClient` 边界桩兜 ③（顺带把 ① 再兜一遍）。
    """
    import chromadb

    from app import config
    from app.services import output_path_migration, rag_backend, scenario_lab, visual_asset_index

    chroma = tmp_path / "chroma"
    monkeypatch.setattr(config, "CHROMA_DIR", chroma)
    for mod in (output_path_migration, rag_backend, scenario_lab, visual_asset_index):
        monkeypatch.setattr(mod, "CHROMA_DIR", chroma)

    real_client = chromadb.PersistentClient

    def _sandboxed_client(*args, **kwargs):
        if args:
            args = (str(chroma), *args[1:])
        else:
            kwargs["path"] = str(chroma)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(chromadb, "PersistentClient", _sandboxed_client)


@pytest.fixture(autouse=True)
def _isolate_comfy_launcher(tmp_path, monkeypatch):
    """ComfyUI 启动器**写出的**运行态文件落 tmp（**实测泄漏**：真实 data/comfy_extra_paths.yaml）。

    ⚠ 不能整块改 `comfy_launcher.DATA_DIR` 了事：`DATA_DIR` 同时喂**读侧**
    —— `_config_path()`（`data/comfy_config.json`，真实 ComfyUI 安装路径）被
    `load_config()` 读给 `audio_merge.find_ffmpeg()` 用。若连它一起指向 tmp，
    `find_ffmpeg()` 读不到配置 ⇒ `test_audio_merge` 的 3 条「真实 ffmpeg」用例
    从**执行**静默退化为**跳过**（2026-09-15 实测踩到：全量 skipped 0→3）。
    故这里只让 DATA_DIR 影响**写侧**路径（`write_ext_yaml` / `_stdout_log_path`），
    再把 `_config_path` 钉回真实文件，保读取语义不变。
    （用例要重定向配置读写时，照 `test_comfy_launcher.py` 自行 patch `_config_path`。）
    """
    from app.services import comfy_launcher

    real_config_path = comfy_launcher._config_path()
    monkeypatch.setattr(comfy_launcher, "DATA_DIR", tmp_path)
    monkeypatch.setattr(comfy_launcher, "_config_path", lambda: real_config_path)


@pytest.fixture(autouse=True)
def _block_os_folder_reveal(monkeypatch):
    """测试期间绝不真的弹出资源管理器（2026-09-10 事故）。

    `reveal_in_folder` 在本机 win32 下会 `subprocess.Popen(["explorer", ...])`——跑一次
    全量测试就真弹一个窗口（指向 pytest 临时目录）。此前只有一个用例自己 monkeypatch，
    而且**打错了目标**：`routers/artifacts.py` 是 `from ... import reveal_in_folder` 的
    独立绑定，改服务模块属性碰不到路由里的引用，于是那个用例既「假绿」（只断言 body
    非空）又真弹窗。这里把两处绑定一起换成降级桩（False = 非 Windows/失败），用例要断言
    别的行为请自行覆盖对应绑定。
    """
    from app.services import collection_artifacts
    from app.routers import artifacts as artifacts_router

    def _never_reveal(path, select=True):
        return False

    monkeypatch.setattr(collection_artifacts, "reveal_in_folder", _never_reveal)
    monkeypatch.setattr(artifacts_router, "reveal_in_folder", _never_reveal)


# ===== P1 资产主表（2026-10-18）：双写会落 app.db —— 全量测试一律指向 tmp =====
# rag_store.index_generation / delete_doc / set_doc_tags 现在会双写 SQLite 主表；
# 若不隔离，跑一次全量测试就往用户真实 app.db 里灌几百条假资产（repo="r1" 之类）。
# 做法＝模板库 init 一次 + 每个测试复制一份 ⇒ 隔离且便宜（读路径「空库回退 Chroma」
# 的断言也依赖每测试干净库，共享库会串味）。

@pytest.fixture(scope="session")
def _app_db_template(tmp_path_factory):
    from app import db as app_db

    path = tmp_path_factory.mktemp("appdb-template") / "app.db"
    original = app_db.DB_PATH
    app_db.DB_PATH = path
    try:
        app_db.init_db()
    finally:
        app_db.DB_PATH = original
    return path


@pytest.fixture(autouse=True)
def _isolate_app_db(_app_db_template, monkeypatch, tmp_path_factory):
    """每测试一份全新的主表副本（模板克隆）；需要自定义时自行再 monkeypatch DB_PATH。

    ⚠ 副本必须放**独立目录**（tmp_path_factory.mktemp），不能塞进用例的 tmp_path——
    多个用例断言「tmp_path 里只有自己写的文件」，多一个 app.db 就假红
    （2026-10-18 首轮全量实锤 5 例）。
    """
    import shutil

    from app import db as app_db

    target = tmp_path_factory.mktemp("appdb-run") / "app.db"
    shutil.copyfile(_app_db_template, target)
    monkeypatch.setattr(app_db, "DB_PATH", target)
