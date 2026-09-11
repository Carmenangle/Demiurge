import pytest

from app.services import edit_artifacts, edit_session, project_files


@pytest.mark.parametrize("message", [
    "为什么角色描述没有更新", "查看当前角色卡", "检查脚本有没有问题", "说明文件归属",
])
def test_解释检查类请求默认只读(message):
    assert edit_session.mutation_requested(message) is False


@pytest.mark.parametrize("message", [
    "制作角色卡", "编写 Python 脚本", "请修复这个错误", "检查并修复角色卡", "迁移 ST 预设",
    "更新角色卡描述", "检查并更新世界书",
])
def test_明确执行类请求允许修改(message):
    assert edit_session.mutation_requested(message) is True


def test_执行层要求先列目录且修改已有文件前先读(tmp_path):
    project_files.write_text(tmp_path, "card.json", '{"name":"塞西莉亚"}')
    session = edit_session.EditSession(tmp_path, allow_mutation=True)

    with pytest.raises(project_files.ProjectFileError, match="list_project_files"):
        session.authorize_write("new.json")
    session.record_list()
    with pytest.raises(project_files.ProjectFileError, match="read_project_file"):
        session.authorize_write("card.json")
    session.record_read("card.json")
    session.authorize_write("card.json")


def test_只读会话执行层拒绝写入(tmp_path):
    session = edit_session.EditSession(tmp_path, allow_mutation=False)
    session.record_list()
    with pytest.raises(project_files.ProjectFileError, match="只读请求"):
        session.authorize_write("new.json")


def test_候选内容在写入前按文件类型校验(tmp_path):
    session = edit_session.EditSession(tmp_path, allow_mutation=True)
    with pytest.raises(edit_artifacts.ArtifactValidationError, match="扁平格式"):
        session.validate_candidate(
            "角色卡/塞西莉亚/card.json",
            '{"spec":"chara_card_v2","data":{"name":"塞西莉亚"}}',
        )
