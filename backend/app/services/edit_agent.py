"""编辑模式 Agent：角色卡、作品脚本与排错，文件工具限定在当前作品目录。"""
from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

from app.services import (
    edit_artifacts,
    edit_import_adapter,
    edit_publication,
    edit_session,
    llm,
    project_files,
    run_trace,
)
from app.services.pathnames import safe_dir
from app.services.edit_agent_profiles import select_specialist, system_prompt_for


def _tool_error(ctx: Any, operation: str, path: str, exc: Exception) -> str:
    run_trace.emit(ctx, "edit.file_failed", operation=operation, path=path, error=str(exc))
    return f"ERROR: {exc}"


def _tools(
    ctx: Any, root, session: edit_session.EditSession | None = None,
    images: list[str] | None = None,
):
    policy = session or edit_session.EditSession(root, allow_mutation=True, enforce_workflow=False)
    attachments = images or []

    @tool
    def list_project_files(path: str = "", recursive: bool = True) -> str:
        """列出当前作品目录中的文件。path 必须是相对路径；空字符串表示作品根目录。"""
        try:
            items = project_files.list_files(root, path, recursive=recursive)
            policy.record_list()
            run_trace.emit(ctx, "edit.file_listed", path=path, recursive=recursive, count=len(items))
            return json.dumps(items, ensure_ascii=False)
        except project_files.ProjectFileError as exc:
            return _tool_error(ctx, "list", path, exc)

    @tool
    def read_project_file(path: str) -> str:
        """读取当前作品内一个 UTF-8 文本文件。path 必须是相对路径。"""
        try:
            content = project_files.read_text(root, path)
            policy.record_read(path)
            run_trace.emit(ctx, "edit.file_read", path=path, chars=len(content))
            return content
        except project_files.ProjectFileError as exc:
            return _tool_error(ctx, "read", path, exc)

    @tool
    def write_project_file(path: str, content: str) -> str:
        """创建或完整覆写当前作品内一个 UTF-8 文本文件。仅在用户明确要求修改文件时调用。"""
        try:
            policy.authorize_write(path)
            validation = policy.validate_candidate(path, content)
            size = project_files.write_text(root, path, content)
            policy.record_change(path)
            run_trace.emit(ctx, "edit.file_written", path=path, bytes=size)
            if validation is not None:
                run_trace.emit(
                    ctx, "edit.validation_succeeded", path=path,
                    artifact_type=validation.get("type"), result=validation, automatic=True,
                )
            return f"SUCCESS: 已写入 {path}（{size} 字节）"
        except (project_files.ProjectFileError, edit_artifacts.ArtifactValidationError) as exc:
            return _tool_error(ctx, "write", path, exc)

    @tool
    def replace_in_project_file(
        path: str, old_text: str, new_text: str, replace_all: bool = False,
    ) -> str:
        """精确替换当前作品文件中的文本。默认只允许唯一命中；多处替换须显式 replace_all=true。"""
        try:
            policy.authorize_write(path)
            original = project_files.read_text(root, path)
            count = original.count(old_text)
            if count == 0:
                raise project_files.ProjectFileError("文件中未找到待替换文本")
            if count > 1 and not replace_all:
                raise project_files.ProjectFileError(
                    f"待替换文本出现 {count} 次；请提供更精确的文本或允许全部替换",
                )
            updated = original.replace(old_text, new_text, -1 if replace_all else 1)
            validation = policy.validate_candidate(path, updated)
            count = project_files.replace_text(
                root, path, old_text, new_text, replace_all=replace_all,
            )
            policy.record_change(path)
            run_trace.emit(ctx, "edit.file_replaced", path=path, count=count)
            if validation is not None:
                run_trace.emit(
                    ctx, "edit.validation_succeeded", path=path,
                    artifact_type=validation.get("type"), result=validation, automatic=True,
                )
            return f"SUCCESS: 已在 {path} 替换 {count} 处"
        except (project_files.ProjectFileError, edit_artifacts.ArtifactValidationError) as exc:
            return _tool_error(ctx, "replace", path, exc)

    @tool
    def validate_project_file(path: str, artifact_type: str = "auto") -> str:
        """校验作品文件格式。支持角色卡、世界书、预设、正则、Python 和普通 JSON。"""
        try:
            content = project_files.read_text(root, path)
            policy.record_read(path)
            result = edit_artifacts.validate(path, content, artifact_type)
            policy.validated_paths.add(path)
            run_trace.emit(
                ctx, "edit.validation_succeeded", path=path,
                artifact_type=result.get("type"), result=result,
            )
            return json.dumps({"valid": True, **result}, ensure_ascii=False)
        except (project_files.ProjectFileError, edit_artifacts.ArtifactValidationError) as exc:
            run_trace.emit(
                ctx, "edit.validation_failed", path=path,
                artifact_type=artifact_type, error=str(exc),
            )
            return json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False)

    @tool
    def convert_st_project_file(
        source_path: str, artifact_type: str = "auto", target_dir: str = "",
        overwrite: bool = False,
    ) -> str:
        """把当前作品内的外部 JSON 角色卡、世界书、预设或正则转为 Demiurge 格式。"""
        try:
            source = project_files.read_text(root, source_path)
            policy.record_read(source_path)
            converted = edit_import_adapter.convert(
                source_path, source, artifact_type, target_dir,
            )
            collisions = [
                path for path in converted.files if project_files.file_exists(root, path)
            ]
            if collisions and not overwrite:
                return json.dumps({
                    "converted": False, "reason": "target_exists", "paths": collisions,
                }, ensure_ascii=False)
            for path in converted.files:
                policy.authorize_write(path)
            for path, content in converted.files.items():
                policy.validate_candidate(path, content)
                project_files.write_text(root, path, content)
                policy.record_change(path)
            paths = list(converted.files)
            run_trace.emit(
                ctx, "edit.import_converted", source_path=source_path,
                artifact_type=converted.artifact_type, paths=paths, overwrite=overwrite,
            )
            return json.dumps({
                "converted": True, "artifact_type": converted.artifact_type, "paths": paths,
            }, ensure_ascii=False)
        except (
            project_files.ProjectFileError,
            edit_artifacts.ArtifactValidationError,
            edit_import_adapter.ImportConversionError,
        ) as exc:
            run_trace.emit(
                ctx, "edit.import_failed", source_path=source_path,
                artifact_type=artifact_type, error=str(exc),
            )
            return json.dumps({"converted": False, "error": str(exc)}, ensure_ascii=False)

    @tool
    def save_attachment_png(path: str, attachment_index: int = 0, overwrite: bool = False) -> str:
        """把本轮第 N 个 PNG 附件保存到当前作品；可用于角色头像或 expressions/<表情>.png。"""
        try:
            if not 0 <= attachment_index < len(attachments):
                raise project_files.ProjectFileError("附件序号不存在")
            exists = project_files.file_exists(root, path)
            if exists and not overwrite:
                return json.dumps({
                    "saved": False, "reason": "target_exists", "path": path,
                }, ensure_ascii=False)
            policy.authorize_write(path, existing_confirmed=overwrite)
            data = edit_publication.attachment_png(attachments[attachment_index])
            size = project_files.write_png(root, path, data)
            policy.record_change(path)
            run_trace.emit(ctx, "edit.attachment_saved", path=path, bytes=size)
            return json.dumps({"saved": True, "path": path, "bytes": size}, ensure_ascii=False)
        except (project_files.ProjectFileError, edit_publication.EditPublicationError) as exc:
            return _tool_error(ctx, "save_attachment", path, exc)

    @tool
    def publish_character_card(name: str, overwrite: bool = False) -> str:
        """把当前作品角色卡快照发布到后端设置的角色卡源库；同名默认不覆盖。"""
        source_path = f"角色卡/{safe_dir(name)}/card.json"
        try:
            policy.authorize_publish(source_path)
            result = edit_publication.publish_character(root, name, overwrite=overwrite)
            run_trace.emit(
                ctx, "edit.character_published", name=name, overwrite=overwrite, result=result,
            )
            return json.dumps({"published": True, **result}, ensure_ascii=False)
        except (project_files.ProjectFileError, edit_publication.EditPublicationError) as exc:
            return _tool_error(ctx, "publish_character", source_path, exc)

    @tool
    def publish_preset(path: str, name: str, overwrite: bool = False) -> str:
        """把当前作品内已校验预设发布到后端设置的预设源库；同名默认不覆盖。"""
        try:
            policy.authorize_publish(path)
            result = edit_publication.publish_preset(root, path, name, overwrite=overwrite)
            run_trace.emit(
                ctx, "edit.preset_published", path=path, name=name,
                overwrite=overwrite, result=result,
            )
            return json.dumps({"published": True, **result}, ensure_ascii=False)
        except (project_files.ProjectFileError, edit_publication.EditPublicationError) as exc:
            return _tool_error(ctx, "publish_preset", path, exc)

    @tool
    def read_recent_agent_trace(turn_id: str = "", limit: int = 50) -> str:
        """读取当前作品最近 Agent Trace；可按 turn_id 精确过滤，最多 200 条。"""
        records = run_trace.read_recent(
            str(ctx.get("repo_id") or ctx.get("thread_id") or ""),
            turn_id=turn_id, limit=limit,
        )
        run_trace.emit(ctx, "edit.trace_read", turn_id=turn_id, count=len(records))
        return json.dumps(records, ensure_ascii=False)

    return [
        list_project_files, read_project_file, write_project_file,
        replace_in_project_file, validate_project_file, convert_st_project_file,
        save_attachment_png, publish_character_card, publish_preset, read_recent_agent_trace,
    ]


def run(ctx: Any, message: str, images: list[str], trace: list[str]) -> dict:
    """运行一次受限编辑 Agent；工具行为由 project_files 做硬边界校验。"""
    specialist = select_specialist(message)
    try:
        run_trace.emit(
            ctx, "edit.specialist_selected", specialist=specialist.id,
            specialist_name=specialist.name,
        )
        builtin = ctx.get("builtin") or {}
        settings = builtin.get(specialist.id) if isinstance(builtin, dict) else {}
        settings = settings if isinstance(settings, dict) else {}
        temperature = settings.get("temperature", specialist.temperature)
        top_p = settings.get("topP")
        max_tokens = settings.get("maxTokens")
        override = settings.get("systemPrompt")
        system_prompt = system_prompt_for(
            specialist, override if isinstance(override, str) else "",
        )
        root = project_files.project_root(ctx.get("repo_id") or ctx.get("thread_id") or "")
        session = edit_session.EditSession(
            root=root, allow_mutation=edit_session.mutation_requested(message),
        )
        model = llm.build_model(
            ctx["chat_base"], ctx["chat_key"], ctx["chat_model"],
            temperature=float(temperature), proxy=ctx.get("chat_proxy", ""),
            top_p=top_p, max_tokens=max_tokens, sdk_retries=0,
        )
        from langchain.agents import create_agent
        agent = create_agent(
            model=model, tools=_tools(ctx, root, session, images), system_prompt=system_prompt,
        )
        messages = []
        for item in ctx.get("history") or []:
            content = str(item.get("content") or "")
            if not content:
                continue
            messages.append(AIMessage(content=content) if item.get("role") == "assistant"
                            else HumanMessage(content=content))
        current: str | list[dict] = message
        if images:
            current = [{"type": "text", "text": message}]
            current.extend({"type": "image_url", "image_url": {"url": url}} for url in images)
        messages.append(HumanMessage(content=current))
        run_trace.emit(ctx, "agent.started", agent=specialist.id, project_root=str(root))
        result = agent.invoke({"messages": messages}, {"recursion_limit": 24})
        for path, validation in session.verify_changes():
            run_trace.emit(
                ctx, "edit.validation_succeeded", path=path,
                artifact_type=validation.get("type"), result=validation, automatic=True,
            )
        replies = result.get("messages") or []
        reply = llm.flatten_content(replies[-1].content).strip() if replies else ""
        run_trace.emit(ctx, "agent.completed", agent=specialist.id, content=reply)
        return {
            "result_text": reply or f"{specialist.name}未返回内容",
            "trace": trace + [f"📝 {specialist.name}执行完成"],
        }
    except Exception as exc:  # noqa: BLE001
        run_trace.emit(ctx, "agent.error", agent=specialist.id, error=str(exc))
        return {"result_text": f"编辑失败：{exc}", "trace": trace}
