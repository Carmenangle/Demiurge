"""纯注入逻辑测试：脱离 live ComfyUI 验证套值 + 提示词注入 + 缺失必填。"""
from app.services import workflow_injector


def test_套用暴露字段的用户值():
    api = {"5": {"inputs": {"steps": 1}}}
    exposed = [{"node_id": "5", "field": "steps"}]
    missing = workflow_injector.inject_template_values(api, exposed, {"5.steps": 20})
    assert api["5"]["inputs"]["steps"] == 20
    assert missing == []


def test_图像输入口为空记入缺失():
    api = {}
    exposed = [{"node_id": "7", "field": "image", "control": "image", "label": "底图"}]
    missing = workflow_injector.inject_template_values(api, exposed, {})
    assert missing == ["底图"]


def test_提示词注入首个常见文本字段():
    api = {"9": {"inputs": {"text": "old"}}}
    workflow_injector.inject_template_values(api, [], {}, "hello", "9")
    assert api["9"]["inputs"]["text"] == "hello"


def test_提示词无目标节点不注入():
    api = {"9": {"inputs": {"text": "old"}}}
    workflow_injector.inject_template_values(api, [], {}, "hello", "999")
    assert api["9"]["inputs"]["text"] == "old"


def test_未暴露的键不覆盖():
    api = {"5": {"inputs": {"steps": 1}}}
    missing = workflow_injector.inject_template_values(api, [], {"5.steps": 20})
    assert api["5"]["inputs"]["steps"] == 1  # 不在 exposed 内 → 不动
    assert missing == []


def test_嵌套values展平后按binding注入():
    # 2026-09-05 实锤：模型把批参数写成 {"name","prompt","values":{"latent_width":…}}
    # 嵌套形态，嵌套键从未进注入 → 宽高静默落回模板默认（720×1080 请求出横版图）
    api = {"20": {"inputs": {"width": 1280, "height": 720}}}
    exposed = [{"node_id": "20", "field": "width", "binding": "latent_width"},
               {"node_id": "20", "field": "height", "binding": "latent_height"}]
    variant = {"name": "套一", "values": {"latent_width": 720, "latent_height": 1080}}
    workflow_injector.inject_template_values(api, exposed, variant, "p", "20")
    assert api["20"]["inputs"]["width"] == 720
    assert api["20"]["inputs"]["height"] == 1080


def test_嵌套values与顶层键冲突时顶层优先():
    api = {"20": {"inputs": {"width": 1280}}}
    exposed = [{"node_id": "20", "field": "width", "binding": "latent_width"}]
    variant = {"latent_width": 640, "values": {"latent_width": 999}}
    workflow_injector.inject_template_values(api, exposed, variant)
    assert api["20"]["inputs"]["width"] == 640


def test_追加多个model_only_lora并重接下游():
    api = {
        "10": {"class_type": "UNETLoader", "inputs": {}},
        "20": {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": ["10", 0], "lora_name": "old", "strength_model": 0.8,
        }},
        "30": {"class_type": "KSampler", "inputs": {"model": ["20", 0]}},
    }
    workflow_injector.inject_lora_stack(api, "20", [
        {"name": "style.safetensors", "weight": 0.7},
        {"name": "a.safetensors", "weight": 0.9},
        {"name": "b.safetensors", "weight": 1.1},
    ])
    assert api["20"]["inputs"]["lora_name"] == "style.safetensors"
    assert api["31"]["inputs"]["model"] == ["20", 0]
    assert api["32"]["inputs"]["model"] == ["31", 0]
    assert api["30"]["inputs"]["model"] == ["32", 0]


def test_追加完整lora_loader时model和clip共同串联():
    api = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {}},
        "2": {"class_type": "LoraLoader", "inputs": {
            "model": ["1", 0], "clip": ["1", 1], "lora_name": "old",
            "strength_model": 0.8, "strength_clip": 0.8,
        }},
        "3": {"class_type": "KSampler", "inputs": {"model": ["2", 0]}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 1]}},
    }
    workflow_injector.inject_lora_stack(api, "2", [
        {"name": "style.safetensors", "weight": 0.7},
        {"name": "role.safetensors", "weight": 1.0},
    ])
    assert api["5"]["inputs"]["model"] == ["2", 0]
    assert api["5"]["inputs"]["clip"] == ["2", 1]
    assert api["3"]["inputs"]["model"] == ["5", 0]
    assert api["4"]["inputs"]["clip"] == ["5", 1]


def test_无lora模式把工作流内全部lora权重归零():
    api = {
        "1": {"class_type": "LoraLoader", "inputs": {
            "strength_model": 0.8, "strength_clip": 0.6,
        }},
        "2": {"class_type": "LoraLoaderModelOnly", "inputs": {"strength_model": 1.0}},
    }
    workflow_injector.disable_all_loras(api)
    assert api["1"]["inputs"]["strength_model"] == 0
    assert api["1"]["inputs"]["strength_clip"] == 0
    assert api["2"]["inputs"]["strength_model"] == 0


def test_number字段的字符串数字自动收窄为数值():
    # Autopilot 批量注入的宽高可能是字符串（'720'/'1080'），必须转数值再进
    # ComfyUI prompt，否则节点类型校验失败（2026-09-04 阮秋白批次实况）。
    api = {"20": {"inputs": {"width": 1280, "height": 720}}}
    exposed = [
        {"node_id": "20", "field": "width", "control": "number", "semantic": "width", "binding": "latent_width"},
        {"node_id": "20", "field": "height", "control": "number", "semantic": "height", "binding": "latent_height"},
    ]
    missing = workflow_injector.inject_template_values(api, exposed, {
        "width": "720", "height": "1080",
    })
    assert missing == []
    assert api["20"]["inputs"]["width"] == 720
    assert type(api["20"]["inputs"]["width"]) is int
    assert api["20"]["inputs"]["height"] == 1080


def test_number字段小数与解析失败值():
    api = {"6": {"inputs": {"cfg": 0.0}}}
    exposed = [{"node_id": "6", "field": "cfg", "control": "number"}]
    workflow_injector.inject_template_values(api, exposed, {"6.cfg": "0.5"})
    assert api["6"]["inputs"]["cfg"] == 0.5
    # 解析失败的非数字字符串保留原值（不吞异常、不改语义）
    api2 = {"6": {"inputs": {"cfg": 0.0}}}
    workflow_injector.inject_template_values(api2, exposed, {"6.cfg": "step="})
    assert api2["6"]["inputs"]["cfg"] == "step="


def test_非number字段不做数值收窄():
    # 文本/prompt 字段即使值长得像数字也保持字符串原样
    api = {"9": {"inputs": {"text": "old"}}}
    exposed = [{"node_id": "9", "field": "text"}]
    workflow_injector.inject_template_values(api, exposed, {"9.text": "123"})
    assert api["9"]["inputs"]["text"] == "123"
    assert type(api["9"]["inputs"]["text"]) is str


def test_文本字段按大写String键注入():
    # Anima AIO 全能流的提示词节点是原生 String 节点（输入键就是 "String"），
    # 且模板没有 prompt_node_id、只有 exposed 上 binding=="prompt" 这一条线索 ——
    # 兜底分支必须真把正文写进去（2026-09-26 真机：改造前这里静默落空）。
    api = {"1610": {"inputs": {"String": "模板里烘焙的旧正文"}}}
    exposed = [{"node_id": "1610", "field": "String", "control": "text", "binding": "prompt"}]
    missing = workflow_injector.inject_template_values(api, exposed, {}, "新正文", "")
    assert missing == []
    assert api["1610"]["inputs"]["String"] == "新正文"


def test_提示词不落到非文本字段():
    # 键名大小写不敏感后仍要认 _TEXT_FIELDS 的语义优先级：只有一个 STRING 能装正文时，
    # 绝不能落进 filename_prefix 这类恰好也是字符串的字段（旧实现按「首个字符串字段」
    # 兜底，键序一变就写错地方）。
    api = {"9": {"inputs": {"filename_prefix": "Demiurge_x", "STRING": "old"}}}
    workflow_injector.inject_template_values(api, [], {}, "hello", "9")
    assert api["9"]["inputs"]["STRING"] == "hello"
    assert api["9"]["inputs"]["filename_prefix"] == "Demiurge_x"


def test_视频宽高覆盖连线值():
    """注入必须盖掉「来自 ResolutionSelector 的连线」：`inputs.width` 在 API 图里是
    `[源节点, 槽]` 列表，写入标量即断线（2026-10-31 用户定案：图片的宽高要和视频的宽高一致，
    而成图尺寸只由注入决定 ⇒ 这两键不覆盖连线就等于没生效）。"""
    api = {"329": {"inputs": {"prompt": "", "width": [59, 0], "height": [59, 1]}}}
    exposed = [
        {"node_id": "329", "field": "width", "control": "number", "binding": "video_width"},
        {"node_id": "329", "field": "height", "control": "number", "binding": "video_height"},
    ]
    missing = workflow_injector.inject_template_values(
        api, exposed, {"329.width": 864, "329.height": 1536},
    )

    assert missing == []
    assert api["329"]["inputs"]["width"] == 864
    assert api["329"]["inputs"]["height"] == 1536


def test_首帧尺寸缺失时保留模板连线():
    """量不到尺寸（videoSize 未传）⇒ 不写这两键，视频沿用模板自带的分辨率选择器，
    而不是把连线打断成 0/None。"""
    api = {"329": {"inputs": {"width": [59, 0], "height": [59, 1]}}}
    exposed = [
        {"node_id": "329", "field": "width", "control": "number", "binding": "video_width"},
        {"node_id": "329", "field": "height", "control": "number", "binding": "video_height"},
    ]
    workflow_injector.inject_template_values(api, exposed, {})

    assert api["329"]["inputs"]["width"] == [59, 0]
    assert api["329"]["inputs"]["height"] == [59, 1]
