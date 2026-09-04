# 一次性修复脚本：2026-08-23 画布工具卡旧 bug 导致两条 generation 记录
# （13:22 / 15:25）的 prompt/model/LoRA 存成了模板名/空（13:22 那条已被上一版脚本误删）。
# 从 canvas.json 的 wfCaptured 提取正确元数据，按原 created_at/image_url 重新入库
# （doc_id = gen-sha1(repo|img) 确定性 → 同 id 覆盖，不会重复）。
import json
import re
import sys

import chromadb

sys.path.insert(0, r"D:\tool\Demiurge\backend")

from app.services import rag_store, generation_store
from app.services.rag_backend import EmbedConfig

REPO = "8fed7f23-d4fe-43ca-bf36-25cdc43f9c6c"
CANVAS = r"D:\tool\Demiurge\userdata\pictures\Anima\原创\canvas.json"
VIEW = "http://127.0.0.1:8010/api/comfyui/local-view?path=D%3A%5Ctool%5CDemiurge%5Cuserdata%5Cpictures%5CAnima%5C%E5%8E%9F%E5%88%9B%5C"

cfg = EmbedConfig("http://localhost:11434/v1", "ollama", "qwen3-embedding:latest")

state = json.load(open(r"D:\tool\Demiurge\backend\data\user_state.json", encoding="utf-8"))
chat_models = state["settings"].get("chatModels", [])
active_id = state["settings"].get("activeChatModelId", "")
chat = next((m for m in chat_models if m.get("id") == active_id), chat_models[0] if chat_models else {})

cap = json.load(open(CANVAS, encoding="utf-8"))["nodes"]["wftool-19294199"]["wfCaptured"]


def model_name(g):
    for n in g.values():
        if not re.search(r"checkpoint|unet", n.get("class_type", ""), re.I):
            continue
        for k in ("ckpt_name", "unet_name"):
            v = (n.get("inputs") or {}).get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return ""


def lora_names(g):
    out, seen = [], set()
    for n in g.values():
        if not re.search(r"lora", n.get("class_type", ""), re.I):
            continue
        v = (n.get("inputs") or {}).get("lora_name")
        if isinstance(v, str) and v.strip() and v not in seen:
            seen.add(v)
            out.append(v.strip())
    return out


def positive_prompt(g):
    def linked(v):
        return str(v[0]) if isinstance(v, list) and v else None
    roots = [linked(n.get("inputs", {}).get("positive")) for n in g.values()
             if re.search(r"sampler", n.get("class_type", ""), re.I)]
    for rid in [r for r in roots if r]:
        node = g.get(rid)
        if node and re.search(r"text.*encode", node.get("class_type", ""), re.I):
            t = (node.get("inputs") or {}).get("text")
            if isinstance(t, str) and t.strip():
                return t.strip()
    for n in g.values():
        if re.search(r"text.*encode", n.get("class_type", ""), re.I):
            t = (n.get("inputs") or {}).get("text")
            if isinstance(t, str) and t.strip():
                return t.strip()
    return ""


model = model_name(cap)
loras = lora_names(cap)
prompt = positive_prompt(cap)
print("extracted:", model, loras, prompt[:60])
assert model and prompt, "提取失败，中止"

tags = generation_store._extract_tags(
    prompt, chat.get("baseUrl", ""), chat.get("apiKey", ""), chat.get("modelName", ""),
)
print("tags:", tags)

records = [
    # 13:22（上一版脚本已删，重新补回）
    (1787462549340, VIEW + "workflow_099ba871d1fa2518544ad11d.png"),
    # 15:25（仍在库中，同 id 覆盖）
    (1787469907875, VIEW + "workflow_983574662b2f65f387d36036.png"),
]
for created, img in records:
    rag_store.index_generation(
        REPO, cfg, prompt, tags, img,
        created_at=created,
        template_name="Krea2-高清文生图优化流",
        model_name=model,
        lora_names=",".join(loras),
    )
    print("repaired:", created)

# 验证
client = chromadb.PersistentClient(path=r"D:\tool\Demiurge\backend\data\chroma")
col = client.get_collection(f"repo_{REPO}")
r2 = col.get(include=["metadatas"], limit=10000)
for m in r2["metadatas"]:
    if m.get("kind") == "generation" and m.get("template_name") == "Krea2-高清文生图优化流" \
       and (m.get("created_at") or 0) >= 1787460000000:
        print("now:", m.get("created_at"), "| prompt=", (m.get("prompt") or "")[:50],
              "| model=", m.get("model_name"), "| lora=", m.get("lora_names"),
              "| tags=", (m.get("tags") or "")[:80])
