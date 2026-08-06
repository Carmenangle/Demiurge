"""编辑 Agent 单轮执行策略：把文件工作流合同从提示词下沉到工具层。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.services import edit_artifacts, project_files

_MUTATION_WORDS = (
    "创建", "制作", "编写", "写入", "新增", "添加", "修改", "修复", "替换",
    "改成", "更新", "保存", "转换", "迁移", "导入", "整理成", "生成文件",
)
_READ_ONLY_WORDS = (
    "为什么", "原因", "查看", "检查", "分析", "说明", "解释", "评审", "是否", "有没有",
)
_EXPLICIT_OVERRIDE = (
    "并修复", "请修复", "帮我修复", "直接修复", "检查并修改", "检查并修复", "检查并更新",
)


def mutation_requested(message: str) -> bool:
    text = (message or "").strip().casefold()
    if any(word in text for word in _EXPLICIT_OVERRIDE):
        return True
    if any(word in text for word in _READ_ONLY_WORDS):
        return False
    return any(word in text for word in _MUTATION_WORDS)


def requires_validation(path: str) -> bool:
    lower = path.casefold()
    return lower.endswith((".json", ".py", ".js", ".mjs", ".cjs"))


@dataclass
class EditSession:
    root: Path
    allow_mutation: bool
    enforce_workflow: bool = True
    listed: bool = False
    read_paths: set[str] = field(default_factory=set)
    changed_paths: set[str] = field(default_factory=set)
    validated_paths: set[str] = field(default_factory=set)

    def record_list(self) -> None:
        self.listed = True

    def record_read(self, path: str) -> None:
        self.read_paths.add(path)

    def authorize_write(self, path: str, *, existing_confirmed: bool = False) -> None:
        if not self.allow_mutation:
            raise project_files.ProjectFileError("本轮是只读请求，禁止创建、修改或迁移文件")
        if self.enforce_workflow and not self.listed:
            raise project_files.ProjectFileError("写入前必须先调用 list_project_files 确认当前作品")
        if (
            self.enforce_workflow
            and project_files.file_exists(self.root, path)
            and path not in self.read_paths
            and not existing_confirmed
        ):
            raise project_files.ProjectFileError("修改现有文件前必须先调用 read_project_file")

    def authorize_publish(self, source_path: str) -> None:
        if not self.allow_mutation:
            raise project_files.ProjectFileError("本轮是只读请求，禁止发布到源库")
        if self.enforce_workflow and not self.listed:
            raise project_files.ProjectFileError("发布前必须先调用 list_project_files 确认当前作品")
        if self.enforce_workflow and source_path not in self.read_paths:
            raise project_files.ProjectFileError("发布前必须先调用 read_project_file 检查源文件")

    def validate_candidate(self, path: str, content: str) -> dict | None:
        if not requires_validation(path):
            return None
        result = edit_artifacts.validate(path, content, "auto")
        self.validated_paths.add(path)
        return result

    def record_change(self, path: str) -> None:
        self.changed_paths.add(path)

    def verify_changes(self) -> list[tuple[str, dict]]:
        results: list[tuple[str, dict]] = []
        for path in sorted(self.changed_paths):
            if not requires_validation(path):
                continue
            content = project_files.read_text(self.root, path)
            result = edit_artifacts.validate(path, content, "auto")
            self.validated_paths.add(path)
            results.append((path, result))
        return results
