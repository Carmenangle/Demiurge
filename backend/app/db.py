import logging
import sqlite3
import threading
import time
from collections.abc import Iterator
from pathlib import Path

from app.config import DB_PATH
from app.file_lock import exclusive_path_lock

_LOG = logging.getLogger("uvicorn.error")

#: `app.db` 的结构档位（写进库头的 `PRAGMA user_version`）。改表/加列就 +1，并让 `init_db`
#: 里那条台阶能把它升上来。
_DB_VERSION = 2


def _backup_db() -> Path | None:
    """用 sqlite 在线备份把 `app.db` 复制成 `app.db.bak-<时间戳>`；失败返回 None。

    走 backup API 而不是复制文件：WAL 模式下单拷 `.db` 会漏掉还没落主文件的页，副本本身就
    是个打不开的库——而这份副本存在的唯一理由是「建新之前先保住旧数据」，它必须是能打开的。
    """
    target = DB_PATH.with_name(f"{DB_PATH.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    if target.exists():
        return target
    try:
        source = sqlite3.connect(DB_PATH)
        try:
            destination = sqlite3.connect(target)
            try:
                source.backup(destination)
            finally:
                destination.close()
        finally:
            source.close()
        return target
    except sqlite3.Error as exc:
        _LOG.warning("app.db 备份失败（%s → %s）：%s", DB_PATH, target.name, exc)
        return None


def _schema_gate() -> bool:
    """库档位闸：True＝可以照常建表/补列；False＝库比本代码新且备份失败，什么都不许动。

    只有两条分支是今天真能走到的（2026-11-02 P1 定档）：
    - `<= 当前档` ⇒ 就地迁移。历史上所有装过的库都是档位 0，而 `init_db` 的 DDL 全是
      `CREATE ... IF NOT EXISTS` + 补列，本来就前向兼容，所以不需要备份。
    - `> 当前档`（降级回来，或两个版本并跑同一目录）⇒ 备份 + 建新。**备份失败绝不建新**：
      宁可旧代码不去动新库的结构，也不拿一份可能打不开的副本换掉真数据。
    「差距超出窗口就备份+建新」的那个窗口下界今天恒不可达（只有一档台阶），所以这里**不写
    跑不到的分支**；等 `app.db` 出现第二档需要删列/改型的迁移时，再按
    `docs/ROADMAP-INSTALLER-AND-UPDATE.md` P1 的规则补下界。
    """
    if not DB_PATH.is_file():
        return True  # 新库没有档位可读，直接建
    connection = sqlite3.connect(DB_PATH)
    try:
        version = int(connection.execute("pragma user_version").fetchone()[0])
    finally:
        connection.close()
    if version <= _DB_VERSION:
        if version < _DB_VERSION:
            _LOG.info("app.db 档位 %d → %d：按加法迁移（建表/补列）后就地打档。",
                      version, _DB_VERSION)
        return True
    backup = _backup_db()
    if backup is None:
        _LOG.error("app.db 档位 %d 高于本代码 %d，且备份失败 ⇒ 不动库结构。",
                   version, _DB_VERSION)
        return False
    try:
        DB_PATH.unlink()
        for suffix in ("-wal", "-shm"):  # 主文件删了留着 WAL 会拼出一个半截旧库
            DB_PATH.with_name(DB_PATH.name + suffix).unlink(missing_ok=True)
    except OSError as exc:
        _LOG.error("app.db 已备份到 %s 但删不掉原库（%s）⇒ 不动库结构。", backup, exc)
        return False
    _LOG.warning(
        "app.db 档位 %d 高于本代码 %d ⇒ 已备份到 %s 并按新档重建；"
        "资产主表是 Chroma 与磁盘的镜像，用 scripts/backfill_asset_db.py --apply 重建。",
        version, _DB_VERSION, backup)
    return True


def _stamp_schema_version(connection: sqlite3.Connection) -> None:
    """把当前档写进库头。PRAGMA 的写形态不接受绑定参数 ⇒ 只能拼字面量，
    而值是模块级 int 常量，没有外部输入面。"""
    if int(connection.execute("pragma user_version").fetchone()[0]) != _DB_VERSION:
        connection.execute(f"pragma user_version={_DB_VERSION}")


# WAL 是**写在库文件头里的持久设置**，只需切一次。
# 2026-10-18 真机复现：并发首连（批量视觉描述 ThreadPoolExecutor）各自跑
# `pragma journal_mode=WAL` 抢建 -wal/-shm ⇒ 输的连接后续 UPDATE 全报
# "attempt to write a readonly database"（4 并发里头 3 个写失败，表现是
# 「任务显示 done，但资产库里看不到描述」）。故用进程内锁把「首次切换」串行化。
_WAL_LOCK = threading.Lock()
_wal_ready = False


def _ensure_wal(connection: sqlite3.Connection) -> None:
    """首次连接时切 WAL（串行），之后靠库文件头即可，不再重复 pragma。"""
    global _wal_ready
    if _wal_ready:
        return
    with _WAL_LOCK:
        if _wal_ready:
            return
        try:
            connection.execute("pragma journal_mode=WAL")
        except sqlite3.Error:  # 切不成不致命：后续写入按默认 journal 走，列表/写入仍可用
            pass
        _wal_ready = True


# 结构初始化闸：键是**当前 DB_PATH 字符串**（测试/脚本会换路径），不能缓存成全局布尔。
# 线程锁保证同进程只跑一次；跨进程由 `<db>.init.lock` 串行（2026-11-07 G 项）——
# 否则两个实例同看老库时都会执行 `alter table ... add column`，后到的撞 duplicate column。
_SCHEMA_LOCK = threading.Lock()
_schema_ready: set[str] = set()


def _init_lock_path() -> Path:
    return DB_PATH.with_name(f"{DB_PATH.name}.init.lock")


def _raw_connection() -> sqlite3.Connection:
    """只建连接、不做结构初始化；供 `init_db` 内部使用，避免
    `init_db → get_connection → init_db` 递归。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("pragma busy_timeout=5000")
    _ensure_wal(connection)
    return connection


def _ensure_schema() -> None:
    """首次取连接时幂等补建结构：不跑 lifespan 的端点测试/脚本不会拿到空库。"""
    if str(DB_PATH) in _schema_ready:
        return
    init_db(lazy=True)


def get_connection() -> sqlite3.Connection:
    _ensure_schema()
    return _raw_connection()


def write_with_retry(operation, attempts: int = 3) -> object:
    """写库重试：Windows 上「最后一个连接关闭即删除 -wal/-shm」，并发下新连接可能
    撞上正被删除的 WAL ⇒ UPDATE 报 "attempt to write a readonly database"。

    这不是数据问题（重开连接即恢复），2026-10-18 批量视觉描述并发写真机复现
    （4 张里 2 张主表同步失败 ⇒ 任务显示 done、资产库里却看不到描述）。
    operation(conn) 内请自行开连接/关连接；失败按 20ms→60ms 退避重试。
    """
    last: Exception | None = None
    for i in range(max(1, attempts)):
        try:
            return operation(get_connection())
        except sqlite3.OperationalError as exc:
            last = exc
            if "readonly" not in str(exc).lower():
                raise
            time.sleep(0.02 * (i + 1))
    raise last  # type: ignore[misc]


def connection_scope() -> Iterator[sqlite3.Connection]:
    connection = get_connection()
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def init_db(*, lazy: bool = False) -> None:
    """建表/补列/打档位。幂等：同一 `DB_PATH` 只跑一次（懒初始化失败不落闸，下次重试）。

    `lazy=True` 由 `get_connection()` 的首次补建触发；启动路径由 lifespan 显式调用，
    异常必须向上抛让启动失败。跨进程用 `<db>.init.lock` 串行：两个实例指向同一
    `DATA_DIR` 时，后到的会等前一个建完再进来，不会并发补列。
    """
    key = str(DB_PATH)
    if key in _schema_ready:
        return
    with _SCHEMA_LOCK:
        if key in _schema_ready:
            return
        with exclusive_path_lock(_init_lock_path(), timeout=60.0):
            if not _schema_gate():
                _schema_ready.add(key)
                return
            _create_schema()
            _schema_ready.add(key)
    _LOG.info(
        "app.db 结构初始化完成（%s）：%s",
        "首次连接补建" if lazy else "启动装配",
        DB_PATH,
    )


def _create_schema() -> None:
    """建表/补列/打档位；结构闸与幂等判定由 `init_db` 负责。"""
    connection = _raw_connection()
    try:
        # P1 资产主表：FTS5 **外部内容表**的列名必须与内容表（assets）列名一致。
        # 早期 schema 误用列名 `tags`（内容表实际叫 tags_json）⇒ FTS 需读列值的路径
        # （count/rebuild）报 "no such column: T.tags"。检测到旧形态先删表删触发器，
        # 随后按新 schema 重建并 rebuild 索引（幂等；已正确的库不动）。
        stale_fts = False
        row = connection.execute(
            "select sql from sqlite_master where name = 'assets_fts'"
        ).fetchone()
        if row is not None and "tags_json" not in (row[0] or ""):
            connection.execute("drop table assets_fts")
            for trig in ("assets_ai", "assets_ad", "assets_au"):
                connection.execute(f"drop trigger if exists {trig}")
            stale_fts = True
        connection.executescript(
            """
            create table if not exists ai_providers (
                id text primary key,
                name text not null,
                provider_type text not null,
                base_url text not null,
                api_key text not null default '',
                default_model text not null default '',
                supports_model_discovery integer not null default 1,
                enabled integer not null default 1,
                created_at text not null,
                updated_at text not null
            );

            create table if not exists ai_provider_models (
                id text primary key,
                provider_id text not null,
                model_name text not null,
                source text not null,
                supports_image integer,
                created_at text not null,
                unique(provider_id, model_name),
                foreign key(provider_id) references ai_providers(id) on delete cascade
            );

            create table if not exists workflow_build_tasks (
                id text primary key,
                session_id text not null,
                mode text not null,
                need text not null,
                payload text not null,
                status text not null,
                result text not null default '',
                error text not null default '',
                created_at integer not null,
                updated_at integer not null,
                worker_id text not null default '',
                lease_expires_at integer not null default 0
            );
            create index if not exists idx_workflow_build_tasks_queue
                on workflow_build_tasks(status, created_at);

            create table if not exists chat_agent_queue (
                id text primary key,
                thread_id text not null,
                need text not null,
                payload text not null,
                status text not null,
                error text not null default '',
                created_at integer not null,
                updated_at integer not null,
                worker_id text not null default '',
                lease_expires_at integer not null default 0
            );
            create index if not exists idx_chat_agent_queue_queue
                on chat_agent_queue(status, created_at);

            -- LoRA 触发词：主存放这里而非向量库，因为编排注入要按 lora_name 精确查
            -- （向量检索会近似匹配到错文件）。向量库只作检索镜像，见 services/lora_store.py。
            create table if not exists lora_triggers (
                lora_name text primary key,        -- 相对 loras 目录，与 LoraLoader.lora_name 一致
                triggers text not null default '', -- 逗号分隔
                note text not null default '',
                suggested_weight real not null default 0.8,
                suggested_prompt text not null default '',
                source text not null default '',   -- metadata | sidecar | manual
                missing integer not null default 0,-- 1=文件已不在磁盘（不删，保住手填内容）
                updated_at integer not null
            );
            """
        )
        columns = {
            row["name"] for row in connection.execute("pragma table_info(workflow_build_tasks)")
        }
        if "worker_id" not in columns:
            connection.execute(
                "alter table workflow_build_tasks add column worker_id text not null default ''"
            )
        if "lease_expires_at" not in columns:
            connection.execute(
                "alter table workflow_build_tasks add column lease_expires_at integer not null default 0"
            )
        lora_columns = {
            row["name"] for row in connection.execute("pragma table_info(lora_triggers)")
        }
        if "suggested_weight" not in lora_columns:
            connection.execute(
                "alter table lora_triggers add column suggested_weight real not null default 0.8"
            )
        if "suggested_prompt" not in lora_columns:
            connection.execute(
                "alter table lora_triggers add column suggested_prompt text not null default ''"
            )

        connection.executescript(
            """
            create table if not exists plan_tasks (
                id text primary key,
                repo_id text not null default '',
                output_dir text not null default '',
                intent text not null default '',
                plan_json text not null,
                content_hash text not null,
                status text not null,
                lease_id text not null default '',
                error text not null default '',
                result_json text not null default '',
                created_at integer not null,
                updated_at integer not null,
                worker_id text not null default '',
                lease_expires_at integer not null default 0
            );
            create index if not exists idx_plan_tasks_queue
                on plan_tasks(status, created_at);

            create table if not exists plan_task_steps (
                task_id text not null,
                seq integer not null,
                step_id text not null,
                operation text not null,
                params_json text not null default '{}',
                inputs_from_json text not null default '[]',
                outputs_json text not null default '{}',
                status text not null default 'pending',
                attempts integer not null default 0,
                last_error text not null default '',
                updated_at integer not null,
                primary key (task_id, seq)
            );
            create index if not exists idx_plan_task_steps_seq
                on plan_task_steps(task_id, seq);

            -- ===== P1-1 资产主表（元数据真相）＋ P1-2 回收站 ＋ P1-3 FTS5（2026-10-18）=====
            -- schema 按 docs/PLAN-ASSET-LIBRARY-UX §P1-1；另加 derived_from（读侧契约
            -- 需要派生链，否则详情面板「派生来源」断）。deleted_at 非空＝回收站。
            -- 双写纪律：rag_store 写 Chroma 后同步这里（失败只 warning，回填脚本可补）；
            -- 读路径（列表/标签统计/结构化搜索/回收站）以本表为准，语义检索仍走 Chroma。
            create table if not exists assets (
                id text primary key,
                repo_id text not null default '',
                kind text not null default 'generation',
                media_type text not null default 'image',
                source text not null default '',
                image_url text not null default '',
                thumb_url text not null default '',
                prompt text not null default '',
                description text not null default '',
                tags_json text not null default '[]',
                template_name text not null default '',
                model_name text not null default '',
                lora_names text not null default '',
                created_at integer not null default 0,
                deleted_at integer,
                source_url text not null default '',
                source_meta text not null default '',
                derived_from text not null default '[]'
            );
            create index if not exists idx_assets_repo_created
                on assets(repo_id, created_at);
            create index if not exists idx_assets_deleted
                on assets(deleted_at);
            create table if not exists asset_tags (
                asset_id text not null,
                tag text not null,
                repo_id text not null default '',
                primary key (asset_id, tag)
            );
            create index if not exists idx_asset_tags_tag
                on asset_tags(tag, repo_id);
            -- GBC 场景图（2026-11-06）：整图 + 两份 MOSS layout JSON。
            -- 不入 Chroma 元数据：正文十几 KB，塞进向量库会让列表/检索读放大。
            -- moss_layout_json（2026-11-07）：官方完整布局（style/cast/逐项，模型写文字、GBC 给框）。
            create table if not exists asset_gbc (
                asset_id text primary key,
                repo_id text not null default '',
                graph_json text not null default '{}',
                moss_json text not null default '{}',
                moss_draft_json text not null default '{}',
                moss_layout_json text not null default '{}',
                engine_json text not null default '{}',
                created_at integer not null default 0
            );
            -- FTS5 外部内容表（正文留在 assets，触发器同步索引；unicode61 对英文标签够用，
            -- 中文子串检索由 SQL 侧 LIKE 兜底）。⚠ 列名必须与 assets 列名逐一对应
            -- （外部内容表契约），故用 tags_json 而非 tags。
            create virtual table if not exists assets_fts using fts5(
                prompt, description, tags_json,
                content='assets', content_rowid='rowid', tokenize='unicode61'
            );
            create trigger if not exists assets_ai after insert on assets begin
                insert into assets_fts(rowid, prompt, description, tags_json)
                values (new.rowid, new.prompt, new.description, new.tags_json);
            end;
            create trigger if not exists assets_ad after delete on assets begin
                insert into assets_fts(assets_fts, rowid, prompt, description, tags_json)
                values ('delete', old.rowid, old.prompt, old.description, old.tags_json);
            end;
            create trigger if not exists assets_au after update on assets begin
                insert into assets_fts(assets_fts, rowid, prompt, description, tags_json)
                values ('delete', old.rowid, old.prompt, old.description, old.tags_json);
                insert into assets_fts(rowid, prompt, description, tags_json)
                values (new.rowid, new.prompt, new.description, new.tags_json);
            end;
            """
        )
        if stale_fts:
            # 旧 FTS 表已重建 ⇒ 从 assets 重建索引（否则检索空）
            connection.execute("insert into assets_fts(assets_fts) values('rebuild')")
        gbc_columns = {
            row["name"] for row in connection.execute("pragma table_info(asset_gbc)")
        }
        if "moss_layout_json" not in gbc_columns:
            connection.execute(
                "alter table asset_gbc add column moss_layout_json text not null default '{}'"
            )
        _stamp_schema_version(connection)
        connection.commit()
    finally:
        # Windows 上漏关连接会占住 -wal/-shm，临时目录清理报 WinError 32。
        connection.close()
