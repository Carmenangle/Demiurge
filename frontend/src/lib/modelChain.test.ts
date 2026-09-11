import { describe, expect, it } from "vitest";
import {
  loadersInChain, modelWidgetOf, samplerIds, walkModelChain,
} from "./modelChain";

// 造节点：inputs 用 source_node_id 表示上游（与 schemaFromRawWorkflow 产出的形状一致）
const node = (
  id: string, type: string,
  opts: { up?: string; upName?: string; widgets?: Record<string, unknown> } = {},
) => ({
  id, type,
  inputs: opts.up ? [{ name: opts.upName || "model", source_node_id: opts.up }] : [],
  widgets: Object.entries(opts.widgets || {}).map(([name, value]) => ({ name, value })),
});

describe("walkModelChain", () => {
  it("走通用户 Krea2 流的两跳链 KSampler → LoraLoaderModelOnly → UNETLoader", () => {
    const nodes = [
      node("15", "KSamplerAdvanced", { up: "19" }),
      node("19", "LoraLoaderModelOnly", { up: "14", widgets: { lora_name: "a.safetensors" } }),
      node("14", "UNETLoader", { widgets: { unet_name: "krea2.safetensors" } }),
    ];
    const chain = walkModelChain(nodes, "15");
    expect(chain.map((c) => [c.hops, c.type, c.widget])).toEqual([
      [1, "LoraLoaderModelOnly", "lora_name"],
      [2, "UNETLoader", "unet_name"],
    ]);
  });

  it("穿过直通节点找到真正的加载器（反推动漫 4 跳链）", () => {
    const nodes = [
      node("499", "KSampler", { up: "519" }),
      node("519", "CFGNorm", { up: "518" }),
      node("518", "ModelSamplingNewbie", { up: "505" }),
      node("505", "PathchSageAttentionKJ", { up: "490" }),
      node("490", "UNETLoader", { widgets: { unet_name: "u.safetensors" } }),
    ];
    const chain = walkModelChain(nodes, "499");
    expect(chain).toHaveLength(4);
    // 直通节点没有模型 widget，只有末端加载器有
    expect(loadersInChain(chain).map((c) => c.type)).toEqual(["UNETLoader"]);
    expect(loadersInChain(chain)[0].value).toBe("u.safetensors");
  });

  it("一跳就是加载器时链长为 1", () => {
    const nodes = [
      node("380", "KSampler", { up: "395" }),
      node("395", "CheckpointLoaderSimple", { widgets: { ckpt_name: "c.safetensors" } }),
    ];
    expect(walkModelChain(nodes, "380")).toHaveLength(1);
  });

  it("链上有多个可改点时全部列出（LoRA 与底模都能改）", () => {
    const nodes = [
      node("1", "KSampler", { up: "2" }),
      node("2", "LoraLoader", { up: "3", widgets: { lora_name: "l.safetensors" } }),
      node("3", "UNETLoader", { widgets: { unet_name: "u.safetensors" } }),
    ];
    expect(loadersInChain(walkModelChain(nodes, "1"))).toHaveLength(2);
  });

  it("成环不死循环", () => {
    const nodes = [
      node("1", "KSampler", { up: "2" }),
      node("2", "LoraLoader", { up: "3", widgets: { lora_name: "x" } }),
      node("3", "LoraLoader", { up: "2", widgets: { lora_name: "y" } }),
    ];
    const chain = walkModelChain(nodes, "1");
    expect(chain.length).toBeLessThanOrEqual(2);
  });

  it("超过 maxHops 就停", () => {
    const nodes = [node("0", "KSampler", { up: "1" })];
    for (let i = 1; i <= 20; i++) {
      nodes.push(node(String(i), "Passthrough", { up: String(i + 1) }));
    }
    expect(walkModelChain(nodes, "0", 5)).toHaveLength(5);
  });

  it("model 口未连线时返回空链", () => {
    expect(walkModelChain([node("1", "KSampler")], "1")).toEqual([]);
  });

  it("找不到采样器返回空链", () => {
    expect(walkModelChain([node("1", "KSampler")], "999")).toEqual([]);
  });

  it("认 unet 别名作为 model 输入", () => {
    const nodes = [
      node("1", "KSampler", { up: "2", upName: "unet" }),
      node("2", "UNETLoader", { widgets: { unet_name: "u.safetensors" } }),
    ];
    expect(walkModelChain(nodes, "1")).toHaveLength(1);
  });
});

describe("modelWidgetOf", () => {
  it("已知类型直接认，值为空也认", () => {
    expect(modelWidgetOf(node("1", "UNETLoader", { widgets: { unet_name: "" } })))
      .toBe("unet_name");
  });

  it("未知自定义加载器按 widget 名兜底", () => {
    expect(modelWidgetOf(node("1", "MyCustomLoader", {
      widgets: { diffusion_name: "m.safetensors" },
    }))).toBe("diffusion_name");
  });

  it("直通节点没有模型 widget", () => {
    expect(modelWidgetOf(node("1", "CFGNorm", { widgets: { strength: 1 } }))).toBe("");
  });

  it("值不像模型文件的不误认", () => {
    expect(modelWidgetOf(node("1", "Foo", { widgets: { model_name: "hello world" } })))
      .toBe("");
  });
});

describe("samplerIds", () => {
  it("找出全部采样器，大小写不敏感", () => {
    const nodes = [
      node("1", "KSampler"), node("2", "KSamplerAdvanced"),
      node("3", "CLIPTextEncode"), node("4", "SamplerCustom"),
    ];
    expect(samplerIds(nodes)).toEqual(["1", "2", "4"]);
  });
});
