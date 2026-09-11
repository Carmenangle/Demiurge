import { describe, expect, it } from "vitest";
import {
  autoLayout, computeAxisSnap, groupKeyOf, nodeAbsolutePosition, nodeDetail, placeNewNodes, projectNodes,
} from "./canvasRuntime";

const makeGen = (id: string, prompt: string, ts = 0) =>
  ({ id, prompt, image_url: `/img/${id}.png`, created_at: ts });

describe("computeAxisSnap", () => {
  const SNAP = 10, RELEASE = 25;
  it("中心对中心吸附", () => {
    const r = computeAxisSnap(96, [100], null, SNAP, RELEASE);
    expect(r.pos).toBe(100);
    expect(r.delta).toBe(4);
  });
  it("距离超阈值不吸附", () => {
    const r = computeAxisSnap(60, [200], null, SNAP, RELEASE);
    expect(r.pos).toBeNull();
    expect(r.delta).toBe(0);
  });
  it("滞回：已吸附后轻微偏移保持吸附", () => {
    // prevPos=100，拖动中心 120（|120-100|=20 < 25 保持）
    const r = computeAxisSnap(120, [999], 100, SNAP, RELEASE);
    expect(r.pos).toBe(100);
    expect(r.delta).toBe(-20);
  });
  it("滞回释放：偏离超过 releasePx 后允许重新吸附新目标", () => {
    // prevPos=100，拖动中心 160（|160-100|=60 > 25 释放）→ 新目标 165（|165-160|=5 < 10）
    const r = computeAxisSnap(160, [165], 100, SNAP, RELEASE);
    expect(r.pos).toBe(165);
    expect(r.delta).toBe(5);
  });
  it("多目标时吸附最近到最近者", () => {
    const r = computeAxisSnap(96, [100, 300], null, SNAP, RELEASE);
    expect(r.pos).toBe(100);
  });
});

describe("nodeAbsolutePosition", () => {
  const n = (id: string, x: number, y: number, parentId?: string) => ({ id, position: { x, y }, parentId });
  it("无父节点：绝对位置 = 局部位置", () => {
    const nodes = [n("a", 10, 20)];
    const byId = new Map(nodes.map((x) => [x.id, x]));
    expect(nodeAbsolutePosition(nodes[0], byId)).toEqual({ x: 10, y: 20 });
  });
  it("一层父：累加父组局部坐标", () => {
    const parent = n("p", 100, 200);
    const child = n("c", 5, 8, "p");
    const byId = new Map([[parent.id, parent], [child.id, child]]);
    expect(nodeAbsolutePosition(child, byId)).toEqual({ x: 105, y: 208 });
  });
  it("嵌套父链逐级累加", () => {
    const gp = n("gp", 1000, 500);
    const parent = n("p", 100, 200, "gp");
    const child = n("c", 5, 8, "p");
    const byId = new Map([[gp.id, gp], [parent.id, parent], [child.id, child]]);
    expect(nodeAbsolutePosition(child, byId)).toEqual({ x: 1105, y: 708 });
  });
  it("父链成环时中断不死循环", () => {
    const a = n("a", 10, 10, "b");
    const b = n("b", 20, 20, "a");
    const byId = new Map([[a.id, a], [b.id, b]]);
    expect(nodeAbsolutePosition(a, byId)).toEqual({ x: 30, y: 30 }); // a + b 一次即停
  });
  it("父节点缺失时按当前局部坐标返回", () => {
    const child = n("c", 5, 8, "missing");
    const byId = new Map([[child.id, child]]);
    expect(nodeAbsolutePosition(child, byId)).toEqual({ x: 5, y: 8 });
  });
});

describe("canvasRuntime", () => {
  describe("groupKeyOf", () => {
    it("相同 prompt 归同组", () => {
      expect(groupKeyOf("a cat")).toBe("p:a cat");
      expect(groupKeyOf("a cat")).toBe(groupKeyOf("a cat"));
    });
    it("不同 prompt 不同组", () => {
      expect(groupKeyOf("cat")).not.toBe(groupKeyOf("dog"));
    });
    it("空 prompt 各自独立", () => {
      const k1 = groupKeyOf("");
      const k2 = groupKeyOf("");
      expect(k1).toMatch(/^id:/);
      expect(k1).not.toBe(k2); // 随机后缀
    });
  });

  describe("projectNodes", () => {
    it("空列表返回空", () => {
      const { nodes, byGroup } = projectNodes([]);
      expect(nodes).toHaveLength(0);
      expect(byGroup.size).toBe(0);
    });

    it("每条生成独立成节点（不再按 prompt 聚合）", () => {
      const gens = [makeGen("1", "cat", 100), makeGen("2", "cat", 200), makeGen("3", "cat", 300)];
      const { nodes } = projectNodes(gens);
      expect(nodes).toHaveLength(3); // 每条生成一节点，不再聚合同 prompt
      expect(nodes[0].generationIds).toEqual(["3"]); // 倒序：新在前
      expect(nodes[1].generationIds).toEqual(["2"]);
      expect(nodes[2].generationIds).toEqual(["1"]);
    });

    it("不同 prompt 各成一节点", () => {
      const gens = [makeGen("1", "cat"), makeGen("2", "dog")];
      const { nodes } = projectNodes(gens);
      expect(nodes).toHaveLength(2);
    });

    it("含空 prompt 时各自独立", () => {
      const gens = [makeGen("1", ""), makeGen("2", "cat"), makeGen("3", "")];
      const { nodes } = projectNodes(gens);
      expect(nodes).toHaveLength(3); // 空 prompt 不合并
    });

    it("byGroup 与 nodes.id 对应", () => {
      const gens = [makeGen("1", "cat")];
      const { nodes, byGroup } = projectNodes(gens);
      const node = nodes[0];
      expect(byGroup.get(node.id)).toHaveLength(1);
      expect(byGroup.get(node.id)?.[0].id).toBe("1");
    });
  });

  describe("autoLayout", () => {
    it("空节点列表不过", () => {
      expect(autoLayout([])).toHaveLength(0);
    });

    it("无坐标节点排成网格", () => {
      const nodes = [
        { id: "n0", type: "image-group" as const, x: 0, y: 0, w: 240, h: 300, generationIds: ["1"] },
        { id: "n1", type: "image-group" as const, x: 0, y: 0, w: 240, h: 300, generationIds: ["2"] },
        { id: "n2", type: "image-group" as const, x: 0, y: 0, w: 240, h: 300, generationIds: ["3"] },
      ];
      const laid = autoLayout(nodes, { cols: 2, cellW: 240, cellH: 300, gapX: 24, gapY: 24, originX: 10, originY: 10 });
      expect(laid[0].x).toBe(10);
      expect(laid[0].y).toBe(10);
      expect(laid[1].x).toBe(274); // 240 + 24
      expect(laid[1].y).toBe(10);
      expect(laid[2].x).toBe(10);
      expect(laid[2].y).toBe(334); // 300 + 24
    });

    it("已有坐标的节点保持原位", () => {
      const nodes = [
        { id: "n0", type: "image-group" as const, x: 100, y: 200, w: 240, h: 300, generationIds: ["1"] },
        { id: "n1", type: "image-group" as const, x: 0, y: 0, w: 240, h: 300, generationIds: ["2"] },
      ];
      const laid = autoLayout(nodes);
      expect(laid.find((n) => n.id === "n0")).toMatchObject({ x: 100, y: 200 });
      // n1 追加在 placed 后面，从 cursor=1 开始
      expect(laid.find((n) => n.id === "n1")!.x).toBeGreaterThan(0);
    });
  });

  describe("placeNewNodes", () => {
    const S = { w: 240, h: 300 };

    it("无锚点时第一个节点落在原点", () => {
      const [p] = placeNewNodes([S], null, []);
      expect(p).toEqual({ x: 24, y: 24 });
    });

    it("非原位：从锚点右侧第 1 列展开，行与锚点垂直居中", () => {
      const anchor = { x: 100, y: 200, w: 240, h: 300 };
      const [p] = placeNewNodes([S], anchor, []);
      expect(p.x).toBe(100 + 240 + 24);
      // 行 0 与锚点垂直居中：同高 → y 与锚点一致
      expect(p.y).toBe(200);
    });

    it("原位替换：第一个节点落在锚点、垂直居中（高度不同时居中）", () => {
      const anchor = { x: 100, y: 200, w: 240, h: 427 };
      const [p0, p1] = placeNewNodes([S, S], anchor, [], { replace: true });
      // 427 高锚点居中 300 高节点 → 上移 (427-300)/2 = 63.5
      expect(p0).toEqual({ x: 100, y: 200 + (427 - 300) / 2 });
      // 第二个从锚点右侧第 1 列开始
      expect(p1.x).toBe(100 + 240 + 24);
    });

    it("排满 cols 后换行向下", () => {
      const anchor = { x: 0, y: 0, w: 240, h: 300 };
      const ps = placeNewNodes([S, S, S, S], anchor, [], { cols: 2, gapX: 24, gapY: 24 });
      // 行 0：col 0、col 1（在锚点右侧）
      expect(ps[0].x).toBe(240 + 24);
      expect(ps[0].y).toBe(0);
      expect(ps[1].x).toBe(240 + 24 + (240 + 24));
      expect(ps[1].y).toBe(0);
      // 行 1
      expect(ps[2].x).toBe(240 + 24);
      expect(ps[2].y).toBe(300 + 24);
    });

    it("落点与 occupied 重叠时向右/向下避让，保证不重叠", () => {
      const anchor = { x: 0, y: 0, w: 240, h: 300 };
      // 锚点右侧第 1 格 (264,0) 已被占用 → 应跳到下一格
      const occupied = [{ x: 264, y: 0, w: 240, h: 300 }];
      const [p] = placeNewNodes([S], anchor, occupied, { cols: 3 });
      const overlaps = (a: { x: number; y: number }, r: { x: number; y: number; w: number; h: number }) =>
        a.x < r.x + r.w && a.x + 240 > r.x && a.y < r.y + r.h && a.y + 300 > r.y;
      expect(overlaps(p, occupied[0])).toBe(false);
      expect(p.x).toBe(264 + (240 + 24)); // 向右挪一格
    });

    it("本批节点之间也互不重叠", () => {
      const ps = placeNewNodes([S, S, S], null, [], { cols: 3 });
      const rects = ps.map((p, i) => ({ ...p, ...S }));
      for (let i = 0; i < rects.length; i++) {
        for (let j = i + 1; j < rects.length; j++) {
          const a = rects[i], b = rects[j];
          const ov = a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
          expect(ov).toBe(false);
        }
      }
    });
  });

  describe("nodeDetail", () => {
    it("image-group 每条生成独立，gen 列表只含自己", () => {
      const gens = [makeGen("1", "a cat", 100), makeGen("2", "a cat", 200)];
      const { nodes, byGroup } = projectNodes(gens);
      expect(nodes).toHaveLength(2);
      const detail = nodeDetail(nodes[0], byGroup);
      expect(detail).not.toBeNull();
      expect(detail!.gens).toHaveLength(1); // 每条生成独立节点
      expect(detail!.title).toBe("图组 · 1 张");
      expect(detail!.prompt).toBe("a cat");
      expect(detail!.imageUrls).toHaveLength(1);
    });

    it("找不到 byGroup 时返回 null", () => {
      const detail = nodeDetail({ id: "ghost", type: "image-group", x: 0, y: 0, w: 240, h: 300, generationIds: [] }, new Map());
      expect(detail).toBeNull();
    });
  });
});
