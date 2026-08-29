# round 3 driver (deleted after use): approve + real worker + wait + graph verify
import json, time
from app.services import plan_tasks

tid = open("data\\.sim3_task_id", encoding="utf-8").read().strip()
print("approve:", {k: plan_tasks.approve_task(tid)[k] for k in ("lease_id", "ttl_seconds")}, flush=True)
plan_tasks.start_worker()
deadline = time.time() + 560
last = ""
while time.time() < deadline:
    t = plan_tasks.get_task(tid)
    line = t["status"] + " | " + " ".join(f"{s['step_id']}:{s['status']}" for s in t["steps"])
    if line != last:
        print(time.strftime("%H:%M:%S"), line, flush=True)
        last = line
    if t["status"] in plan_tasks.TASK_TERMINAL or t["status"] == "blocked":
        break
    time.sleep(2)
t = plan_tasks.get_task(tid)
print("FINAL:", t["status"])
for s in t["steps"]:
    print(f"  {s['step_id']} {s['operation']} -> {s['status']} err={s['last_error'][:60]}")

# graph 级验收：正向词互异 + LoraLoader 挂 QRQ + RAG 入库
from urllib.request import urlopen
collect = next((s for s in t["steps"] if s["operation"] == "media.collect_comfy_outputs"), None)
if collect and collect["status"] == "done":
    print("rag_indexed:", [r.get("rag_indexed") for r in collect["outputs"]["results"]])
    prompts = []
    for r in collect["outputs"]["results"]:
        with urlopen(f"http://127.0.0.1:8188/history/{r['prompt_id']}", timeout=10) as fh:
            entry = json.loads(fh.read()).get(r["prompt_id"]) or {}
        graph = (entry.get("prompt") or [None, None, {}])[2]
        texts = [v.get("inputs", {}).get("text", "") for v in graph.values()
                 if isinstance(v, dict) and v.get("class_type") == "CLIPTextEncode"]
        prompts.append(max(texts, key=len))
        lora = [v.get("inputs", {}).get("lora_name") for v in graph.values()
                if isinstance(v, dict) and v.get("class_type") == "LoraLoader"]
        print(r["label"], "| LoRA 节点:", lora, "| 正向词:", len(prompts[-1]), "字")
    print("两套互异:", prompts[0] != prompts[1])
