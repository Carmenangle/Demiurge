# full-round sim A (deleted after use): all 12 outfits + QRQ lora
import json, re
from app.services import plan_compiler, plan_tasks, llm, capability_handlers as ch

st = json.load(open('data/user_state.json', encoding='utf-8'))
s = st['settings']
cfg = next(m for m in s['chatModels'] if m['modelName'] == 'gpt-5.6-terra')
out_base = s.get('outputDir') or ''

text = ("读取 D:\\video\\寻味电台\\角色设定与生成\\角色一览图\\角色时尚穿搭\\形象提示词-唐柚.md，"
        "这是唐柚的设计文档。LoRA 用 QRQ 风格的那个，用 Krea2-高清文生图优化流模板，"
        "为文档里的每个套装各生成一张图（以文档实际清点为准，全部套装），提交到 http://127.0.0.1:8188，"
        "完成后把图片采集登记进资产库")

pat = re.compile(r"[A-Za-z]:\\[^\s<>|？?」』]*\.(?:md|txt|json|csv|log|ya?ml|xml|html)")
attachments = [{"name": p.rsplit("\\", 1)[-1],
                "text": plan_compiler.read_user_file(p, max_chars=60000)["text"]}
               for p in set(pat.findall(text))]
catalog = ch.lora_list()
lines = "\n".join(f"- {i['file']}" + (f"（触发词:{'/'.join(i['triggers'])}，建议权重:{i['suggested_weight']}）"
                                       if i["triggers"] else "") for i in catalog["loras"])
attachments.append({"name": "本机 LoRA 目录",
                    "text": f"共 {catalog['count']} 个：\n{lines}\n用户指定了 LoRA 时必须在 submit 参数写 lora_name（真实文件名）。"})
print("附件:", [(a["name"], len(a["text"])) for a in attachments], flush=True)

outcome = plan_compiler.compile_plan(
    intent=text, attachments=attachments, repo_id="save01-e2e", output_dir=out_base,
    configured_models={"chat", "image"},
    chat_base=cfg['baseUrl'], chat_key=cfg['apiKey'], chat_model=cfg['modelName'],
    chat_fn=llm.chat, temperature=0.2)
if outcome.plan is None:
    print("COMPILE FAILED:"); [print(" -", e) for e in outcome.errors]; raise SystemExit(1)
plan = outcome.plan
for i, st_ in enumerate(plan.steps, 1):
    print(f"  {i}. {st_.operation} params={json.dumps(st_.params, ensure_ascii=False)[:110]}")
print("budgets:", plan.budgets.model_dump())
lora_ok = any("lora_name" in json.dumps(st_.params) for st_ in plan.steps if "submit" in st_.operation)
print("LORA 已入参:", lora_ok)

filled = plan_compiler.fill_prompt_sections(plan, attachments)
print("段落回填:", filled, "个变体")
assert filled > 0, "回填失败：variants 仍无提示词"

submitted = plan_tasks.submit_task(plan, output_dir=out_base, repo_id="save01-e2e",
                                   configured_models={"chat", "image"})
print("submitted:", submitted)
with open("data\\.sim4_task_id", "w") as f:
    f.write(submitted["task_id"])
