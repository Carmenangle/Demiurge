import sqlite3
from collections.abc import Iterator

from app.config import DB_PATH


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("pragma busy_timeout=5000")
    connection.execute("pragma journal_mode=WAL")
    return connection


def connection_scope() -> Iterator[sqlite3.Connection]:
    connection = get_connection()
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def init_db() -> None:
    with get_connection() as connection:
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
