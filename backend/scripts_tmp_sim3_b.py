# sim B (deleted after use): approve + real worker + wait + verify
import json, time
from app.services import plan_tasks

tid = open("data\\.sim4_task_id", encoding="utf-8").read().strip()
print("approve:", {k: plan_tasks.approve_task(tid)[k] for k in ("lease_id", "ttl_seconds")}, flush=True)
plan_tasks.start_worker()
deadline = time.time() + 1500
last = ""
while time.time() < deadline:
    t = plan_tasks.get_task(tid)
    line = t["status"] + " | " + " ".join(f"{s['step_id']}:{s['status']}" for s in t["steps"])
    if line != last:
        print(time.strftime("%H:%M:%S"), line, flush=True)
        last = line
    if t["status"] in plan_tasks.TASK_TERMINAL or t["status"] == "blocked":
        break
    time.sleep(3)
t = plan_tasks.get_task(tid)
print("FINAL:", t["status"])
for s in t["steps"]:
    print(f"  {s['step_id']} {s['operation']} -> {s['status']} err={s['last_error'][:60]}")
s5 = next((s for s in t["steps"] if s["operation"].startswith("media")), None)
if s5 and s5["outputs"]:
    out = s5["outputs"]
    print("collected:", out.get("collected"), "| rag:", [r.get("rag_indexed") for r in out.get("results", [])])
