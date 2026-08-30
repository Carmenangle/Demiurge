# two-image review test (deleted after use)
import time
from app.services import plan_compiler, plan_tasks
from app.services.structured_contracts import GenerationPlan, PlanBudgets, PlanStep

DOC = r"D:\video\寻味电台\角色设定与生成\角色一览图\角色时尚穿搭\形象提示词-唐柚.md"
doc = plan_compiler.read_user_file(DOC, max_chars=60000)["text"]
sec1 = plan_compiler.extract_section(doc, "【春·套一】")
sec2 = plan_compiler.extract_section(doc, "【睡衣·套一】")
assert len(sec1) > 500 and len(sec2) > 500, "抽取失败"
print("套一:", len(sec1), "字 | 睡衣:", len(sec2), "字 | 互异:", sec1 != sec2)

import time as _time
import urllib.request
plan = GenerationPlan(
    intent=f"两张对比验收图：春·套一 与 睡衣·套一（QRQ LoRA + Krea2 模板）[{int(_time.time())}]",
    repo_id="save01-e2e",
    budgets=PlanBudgets(max_steps=2, max_gpu_tasks=2, max_llm_calls=0),
    steps=[PlanStep(id="s1", operation="workflow.submit_batch", params={
        "template_id": "Krea2-高清文生图优化流",
        "url": "http://127.0.0.1:8188",
        "lora_name": "QRQ 风格",
        "variants": [
            {"name": "春·套一", "prompt": sec1},
            {"name": "睡衣·套一", "prompt": sec2},
        ],
    })],
    approval_required=["workflow.submit_batch"],
)
import json
st = json.load(open('data/user_state.json', encoding='utf-8'))
out_base = st['settings'].get('outputDir') or ''
sub = plan_tasks.submit_task(plan, output_dir=out_base, repo_id="save01-e2e",
                             configured_models={"chat", "image"})
tid = sub["task_id"]
print("submitted:", tid, flush=True)
# 走 HTTP API 批准（租约在后端进程内存里，必须由后端进程自己发）
req = urllib.request.Request(
    f"http://127.0.0.1:8010/api/plans/{tid}/approve",
    data=json.dumps({"approved_by": "user"}).encode("utf-8"),
    headers={"Content-Type": "application/json"}, method="POST")
with urllib.request.urlopen(req, timeout=15) as r:
    print("approve:", json.loads(r.read()), flush=True)
# 不在驱动进程开 worker：执行交给后端进程自己的 plan_task worker（租约在后端内存可见）
deadline = time.time() + 420
while time.time() < deadline:
    t = plan_tasks.get_task(tid)
    if t["status"] in plan_tasks.TASK_TERMINAL or t["status"] == "blocked":
        break
    time.sleep(3)
t = plan_tasks.get_task(tid)
print("FINAL:", t["status"])
s1 = t["steps"][0]
if s1["last_error"]:
    print("err:", s1["last_error"][:150])
s_col = next((s for s in t["steps"] if s["operation"].startswith("media")), None)
if s_col and s_col["outputs"].get("results"):
    for r in s_col["outputs"]["results"]:
        print("=", r["label"], "|", r.get("file"), "| RAG:", r.get("rag_indexed"))
elif s_col:
    print("collect:", s_col["status"], s_col["last_error"][:120])
