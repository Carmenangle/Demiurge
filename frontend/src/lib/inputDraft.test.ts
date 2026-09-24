// 输入框草稿存档（2026-09-21）纯逻辑回归测试。
// 需求来源：用户在输入框写了一半（含插入的图片/文档）就切仓库或刷新，内容不该丢。
// 这里覆盖「键推导 / 读回填 / 写盘 / 配额降级 / 删键」五件事；组件接线由 RichInput.draft.test.tsx 覆盖。

import { describe, expect, it } from "vitest";
import {
  INPUT_DRAFT_PREFIX, clearInputDraft, inputDraftKey, isInputDraftEmpty,
  readInputDraft, writeInputDraft, type InputDraft,
} from "./inputDraft";

/** 内存 Storage 替身（node 环境没有 localStorage）。 */
function memoryStorage(initial: Record<string, string> = {}) {
  const map = new Map(Object.entries(initial));
  return {
    getItem: (key: string) => (map.has(key) ? map.get(key)! : null),
    setItem: (key: string, value: string) => { map.set(key, value); },
    removeItem: (key: string) => { map.delete(key); },
    dump: () => Object.fromEntries(map),
  };
}

/** 模拟 localStorage 配额：超过 limit 字符的写入抛 QuotaExceededError。 */
function quotaStorage(limit: number) {
  const inner = memoryStorage();
  return {
    getItem: inner.getItem,
    removeItem: inner.removeItem,
    setItem: (key: string, value: string) => {
      if (value.length > limit) throw new DOMException("quota", "QuotaExceededError");
      inner.setItem(key, value);
    },
    dump: inner.dump,
  };
}

const draft = (over: Partial<InputDraft> = {}): InputDraft => ({
  text: "", images: [], inspirationAttachments: [], maskedImage: null,
  media: [], attachments: [], updatedAt: 0, ...over,
});

describe("inputDraftKey —— 首页是临时草稿区，不落盘", () => {
  it("仓库/作品 id ⇒ laf_draft_<id>", () => {
    expect(inputDraftKey("repo-1")).toBe(`${INPUT_DRAFT_PREFIX}repo-1`);
  });

  it.each([["home"], [""], ["   "], [undefined], [null]])("不落盘的线程键 %s ⇒ null", (threadId) => {
    expect(inputDraftKey(threadId as string | null | undefined)).toBeNull();
  });
});

describe("readInputDraft —— 坏数据不能拦住输入框挂载", () => {
  it("缺键 ⇒ null", () => {
    expect(readInputDraft("repo-1", memoryStorage())).toBeNull();
  });

  it("home ⇒ null（即使 storage 里塞了东西也不读）", () => {
    const storage = memoryStorage({ [`${INPUT_DRAFT_PREFIX}home`]: JSON.stringify(draft({ text: "x" })) });
    expect(readInputDraft("home", storage)).toBeNull();
  });

  it("JSON 损坏 ⇒ null（不抛）", () => {
    expect(readInputDraft("repo-1", memoryStorage({ [`${INPUT_DRAFT_PREFIX}repo-1`]: "{不是 json" }))).toBeNull();
  });

  it("全空草稿 ⇒ null（等价于没草稿）", () => {
    const storage = memoryStorage({ [`${INPUT_DRAFT_PREFIX}repo-1`]: JSON.stringify(draft({ text: "   " })) });
    expect(readInputDraft("repo-1", storage)).toBeNull();
  });

  it("正常回填：文字 + 图片 + 蒙版 + 附件原样取回", () => {
    const saved = draft({
      text: "写了一半",
      images: ["data:image/png;base64,AAA"],
      maskedImage: { image: "i", mask: "m", preview: "p" },
      media: [{ fileId: "f".repeat(32), name: "a.mp4", mime: "video/mp4", size: 10 }],
      attachments: [{ fileId: "g".repeat(32), name: "a.md", mime: "text/markdown", size: 20 }],
      updatedAt: 123,
    });
    const storage = memoryStorage({ [`${INPUT_DRAFT_PREFIX}repo-1`]: JSON.stringify(saved) });
    expect(readInputDraft("repo-1", storage)).toEqual(saved);
  });

  it("上传中占位（pending-*）不恢复：刷新后上传已断，恢复只会是张不可用的空卡", () => {
    const saved = draft({
      media: [
        { fileId: `pending-abc`, name: "uploading.mp4", mime: "video/mp4", size: 1 },
        { fileId: "h".repeat(32), name: "done.mp4", mime: "video/mp4", size: 2 },
      ],
      attachments: [{ fileId: "pending-xyz", name: "up.md", mime: "text/markdown", size: 3 }],
    });
    const storage = memoryStorage({ [`${INPUT_DRAFT_PREFIX}repo-1`]: JSON.stringify(saved) });
    const restored = readInputDraft("repo-1", storage)!;
    expect(restored.media.map((m) => m.name)).toEqual(["done.mp4"]);
    expect(restored.attachments).toEqual([]);
  });

  it("字段类型不对 ⇒ 逐项丢弃，不整条作废", () => {
    const storage = memoryStorage({
      [`${INPUT_DRAFT_PREFIX}repo-1`]: JSON.stringify({
        text: 42, images: ["ok", 7], maskedImage: { image: "i" }, media: "nope", updatedAt: "yesterday",
      }),
    });
    const restored = readInputDraft("repo-1", storage)!;
    expect(restored.text).toBe("");
    expect(restored.images).toEqual(["ok"]);
    expect(restored.maskedImage).toBeNull();  // 缺 mask/preview ⇒ 不认
    expect(restored.media).toEqual([]);
    expect(restored.updatedAt).toBe(0);
  });
});

describe("writeInputDraft —— 正常写 / 空则删键 / 首页跳过", () => {
  it("正常写 ⇒ saved，且能原样读回", () => {
    const storage = memoryStorage();
    const result = writeInputDraft("repo-1", draft({ text: "草稿", images: ["u"], updatedAt: 5 }), storage);
    expect(result).toBe("saved");
    expect(readInputDraft("repo-1", storage)).toEqual(draft({ text: "草稿", images: ["u"], updatedAt: 5 }));
  });

  it("草稿清空 ⇒ cleared 且删键（不留一条空草稿）", () => {
    const storage = memoryStorage();
    writeInputDraft("repo-1", draft({ text: "有内容" }), storage);
    expect(writeInputDraft("repo-1", draft({ text: "   " }), storage)).toBe("cleared");
    expect(storage.dump()).toEqual({});
  });

  it("首页 ⇒ skipped 且不写任何键", () => {
    const storage = memoryStorage();
    expect(writeInputDraft("home", draft({ text: "首页草稿" }), storage)).toBe("skipped");
    expect(storage.dump()).toEqual({});
  });
});

describe("writeInputDraft —— 配额降级：不静默丢，也不为一张大图赔掉文字", () => {
  it("整包写不下 ⇒ degraded，保留文字/附件，丢掉图片与蒙版并打 heavyDropped", () => {
    const storage = quotaStorage(800);  // 只够装轻字段（降级副本），装不下 2000 字的 dataURI
    const result = writeInputDraft("repo-1", draft({
      text: "这段文字必须活下来",
      images: ["data:image/png;base64," + "A".repeat(2000)],
      maskedImage: { image: "i".repeat(500), mask: "m", preview: "p" },
      attachments: [{ fileId: "k".repeat(32), name: "keep.md", mime: "text/markdown", size: 7 }],
      inspirationAttachments: [{
        id: "c1", title: "灵感卡", content: "卡内容",
        imageUrl: "data:image/png;base64," + "B".repeat(2000),
      }],
      updatedAt: 9,
    }), storage);

    expect(result).toBe("degraded");
    const restored = readInputDraft("repo-1", storage)!;
    expect(restored.text).toBe("这段文字必须活下来");
    expect(restored.attachments.map((a) => a.name)).toEqual(["keep.md"]);
    expect(restored.heavyDropped).toBe(true);
    // 重字段被摘掉（图片/蒙版/灵感卡配图），但灵感卡文字还在
    expect(restored.images).toEqual([]);
    expect(restored.maskedImage).toBeNull();
    expect(restored.inspirationAttachments).toEqual([
      { id: "c1", title: "灵感卡", content: "卡内容", imageUrl: "", sourceUrl: "", imageUrls: [] },
    ]);
  });

  it("降级副本也写不下 ⇒ skipped（放弃，不把异常抛给输入框）", () => {
    const storage = quotaStorage(0);
    expect(writeInputDraft("repo-1", draft({ text: "x" }), storage)).toBe("skipped");
  });
});

describe("clearInputDraft", () => {
  it("删键；首页无键可删也不抛", () => {
    const storage = memoryStorage();
    writeInputDraft("repo-1", draft({ text: "x" }), storage);
    clearInputDraft("repo-1", storage);
    expect(storage.dump()).toEqual({});
    expect(() => clearInputDraft("home", storage)).not.toThrow();
  });
});

describe("isInputDraftEmpty —— 图片/灵感卡/蒙版/附件任一项非空都不算空", () => {
  it("各字段单独命中", () => {
    expect(isInputDraftEmpty(null)).toBe(true);
    expect(isInputDraftEmpty(draft())).toBe(true);
    expect(isInputDraftEmpty(draft({ text: "a" }))).toBe(false);
    expect(isInputDraftEmpty(draft({ images: ["u"] }))).toBe(false);
    expect(isInputDraftEmpty(draft({ maskedImage: { image: "i", mask: "m", preview: "p" } }))).toBe(false);
    expect(isInputDraftEmpty(draft({ inspirationAttachments: [{ id: "1", title: "t", content: "", imageUrl: "" }] }))).toBe(false);
    expect(isInputDraftEmpty(draft({ media: [{ fileId: "f", name: "n", mime: "m", size: 1 }] }))).toBe(false);
    expect(isInputDraftEmpty(draft({ attachments: [{ fileId: "f", name: "n", mime: "m", size: 1 }] }))).toBe(false);
  });
});
