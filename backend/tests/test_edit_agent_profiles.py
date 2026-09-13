from app.services.edit_agent_profiles import select_specialist, system_prompt_for


def test_编辑专家按任务语义分派():
    assert select_specialist("制作一张角色卡和配套世界书").id == "edit_character_card"
    assert select_specialist("修改预设里的正则 placement").id == "edit_preset_regex"
    assert select_specialist("写一个 Python 批处理脚本").id == "edit_script"
    assert select_specialist("这个角色卡格式错误，请修复").id == "edit_debug"
    assert select_specialist("整理当前作品说明").id == "edit_general"


def test_ST迁移请求交外部内容迁移专家():
    assert select_specialist("把 ST 角色卡转换成 Demiurge 格式").id == "edit_import_adapter"
    assert select_specialist("迁移 SillyTavern 预设和正则").id == "edit_import_adapter"
    assert select_specialist("把 ST 世界书转换为 Demiurge").id == "edit_import_adapter"
    prompt = system_prompt_for(select_specialist("转换 ST 角色卡"))
    assert "ST JSON 只作为输入格式" in prompt
    assert "card.json + worldbook.json + regex.json" in prompt
    assert "PNG 角色卡" in prompt


def test_专家提示词始终包含项目公共合同():
    specialist = select_specialist("制作角色卡")
    prompt = system_prompt_for(specialist, "自定义角色卡规则")

    assert "当前可操作根是已选中的小仓库" in prompt
    assert "自定义角色卡规则" in prompt
    assert "角色卡源库与作品快照隔离" in prompt


def test_预设专家以Demiurge运行格式为主而非ST格式():
    prompt = system_prompt_for(select_specialist("制作偏置预设和正则"))

    assert "Demiurge 偏置预设格式" in prompt
    assert "presetDir/<安全名>.json" in prompt
    assert "真实 scene/affinity/turn" in prompt
    assert "全局 → 当前激活预设 → 当前角色卡" in prompt
    assert "SillyTavern" not in prompt


def test_角色卡专家以Demiurge落盘与快照合同为主():
    prompt = system_prompt_for(select_specialist("制作角色卡和世界书"))

    assert "Demiurge 角色卡落盘格式" in prompt
    assert "characterDir 是可复用源库" in prompt
    assert "<当前作品>/角色卡/<安全卡名>/" in prompt
    assert "NormalizedCard" in prompt
    assert "外部卡格式只作导入或导出边界" in prompt
    assert "publish_character_card" in prompt
    assert "save_attachment_png" in prompt


def test_脚本专家理解Demiurge作品数据合同():
    prompt = system_prompt_for(select_specialist("编写作品数据处理脚本"))

    assert "Demiurge 作品数据合同" in prompt
    assert "chat.json 是可见会话快照真源" in prompt
    assert "messageId + slotId" in prompt
    assert "不得直接改 chronicle.db" in prompt
    assert '"javascript"' in prompt


def test_排错专家理解Demiurge真实运行接缝():
    prompt = system_prompt_for(select_specialist("排错后台插画失败"))

    assert "agent-trace.jsonl" in prompt
    assert "实时请求与 chat_agent_queue" in prompt
    assert "messageId + slotId" in prompt
    assert "chat.json" in prompt
    assert "read_recent_agent_trace" in prompt


def test_通用编辑专家先识别Demiurge文件属主():
    prompt = system_prompt_for(select_specialist("整理当前作品资料"))

    assert "Demiurge 文件属主" in prompt
    assert "状态、表格、纪要和 RAG 不因名称相近就直接改文件" in prompt
    assert "当前文件工具只覆盖已选小仓库" in prompt
