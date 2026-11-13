import { describe, expect, it } from "vitest";
import {
  arrangeStoryGrid, autoLayout, computeAxisSnap, genMediaUrl, generationNodeType, groupKeyOf, isArchiveProductNode,
  migrateMediaLayoutKeys, nodeAbsolutePosition, nodeDetail, placeNewNodes, planStoryArrange, projectNodes,
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

  describe("arrangeStoryGrid", () => {
    it("按行优先排列，每行高度取该行最高卡（不是全局最高）", () => {
      const sizes = [
        { w: 240, h: 240 }, { w: 240, h: 240 },
        { w: 180, h: 320 },
      ];

      const ps = arrangeStoryGrid(sizes, [], { cols: 2, originX: 24, originY: 24 });

      expect(ps[0]).toEqual({ x: 24, y: 24 });
      expect(ps[1]).toEqual({ x: 24 + 240 + 24, y: 24 });
      // 第一行最高 240 → 第二行从 24+240+24 开始；若误用全局最高 320 会变成 368。
      expect(ps[2]).toEqual({ x: 24, y: 24 + 240 + 24 });
    });

    it("整块下移避开非剧情节点，剧情节点自身不参与避让", () => {
      const occupied = [{ x: 24, y: 24, w: 240, h: 240 }];
      const ps = arrangeStoryGrid([{ w: 240, h: 240 }], occupied, { gapY: 24 });

      expect(ps[0].y).toBe(24 + 240 + 24);
    });

    it("组内连续：障碍夹在组中间也不给组内开洞（整块避让，不逐行各自避让）", () => {
      const sizes = [{ w: 240, h: 240 }, { w: 240, h: 240 }, { w: 240, h: 240 }];
      // 障碍正卡在第 2 行的目标位置
      const occupied = [{ x: 24, y: 24 + 240 + 24, w: 240, h: 240 }];

      const ps = arrangeStoryGrid(sizes, occupied, { cols: 1, originX: 24, originY: 24 });

      // 整块被推到障碍下方；行距恒为「行高 + gapY」，中间不出现额外空洞
      expect(ps[0].y).toBe(24 + 240 + 24 + 240 + 24);
      expect(ps[1].y - ps[0].y).toBe(240 + 24);
      expect(ps[2].y - ps[1].y).toBe(240 + 24);
    });

    it("多卡片之间不重叠", () => {
      const sizes = [
        { w: 240, h: 240 }, { w: 180, h: 320 }, { w: 240, h: 240 },
        { w: 180, h: 320 }, { w: 240, h: 240 },
      ];
      const ps = arrangeStoryGrid(sizes, [], { cols: 3 });
      const rects = ps.map((p, i) => ({ ...p, ...sizes[i] }));
      for (let i = 0; i < rects.length; i++) {
        for (let j = i + 1; j < rects.length; j++) {
          const a = rects[i], b = rects[j];
          const ov = a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
          expect(ov).toBe(false);
        }
      }
    });

    it("空列表返回空", () => {
      expect(arrangeStoryGrid([])).toEqual([]);
    });
  });

  describe("媒体类型判定（后端 camelCase `mediaType` 是唯一真源）", () => {
    // 后端 `list_generations` 返回 {mediaType, image_url, thumbUrl}，**没有** media_type / video_url
    // ⇒ 视频/音频记录的地址就在 image_url 里（RAG 只有一个地址字段）。
    const videoGen = { ...makeGen("gen-v1", "视频提示词"), mediaType: "video" as const };
    const audioGen = { ...makeGen("a1", ""), mediaType: "audio" as const };

    it("mediaType=video / audio 判成对应节点，不再退化成图组", () => {
      expect(generationNodeType(videoGen)).toBe("video");
      expect(generationNodeType(audioGen)).toBe("audio");
      expect(generationNodeType(makeGen("i1", "图"))).toBe("image-group");
    });

    it("历史 media_type 与 URL 字段仍兼容（优先级：显式类型 > URL）", () => {
      expect(generationNodeType({ ...makeGen("v2", ""), media_type: "video" })).toBe("video");
      expect(generationNodeType({ ...makeGen("v3", ""), video_url: "/x.mp4" })).toBe("video");
      expect(generationNodeType({ ...makeGen("a2", ""), audio_url: "/x.wav" })).toBe("audio");
    });

    it("genMediaUrl：播放地址取 image_url 兜底（后端唯一地址字段）", () => {
      expect(genMediaUrl(videoGen)).toBe("/img/gen-v1.png");
      expect(genMediaUrl({ ...videoGen, video_url: "/v1.mp4" })).toBe("/v1.mp4");
      expect(genMediaUrl(audioGen)).toBe("/img/a1.png");
      expect(genMediaUrl(undefined)).toBe("");
    });

    it("projectNodes 对真实视频记录产出 video-gen-<id> 节点", () => {
      const { nodes } = projectNodes([videoGen]);
      expect(nodes[0].type).toBe("video");
      expect(nodes[0].id).toBe("video-gen-v1");
    });

    it("migrateMediaLayoutKeys：旧 img-gen 键与删除黑名单跟着类型改名（位置不丢、删掉的不复活）", () => {
      const rect = { x: 1, y: 2, w: 3, h: 4 };
      const r = migrateMediaLayoutKeys([videoGen], { "img-gen-gen-v1": rect }, ["img-gen-gen-v1", "story-x"]);

      expect(r.changed).toBe(true);
      expect(r.nodes["video-gen-gen-v1"]).toEqual(rect);
      expect(r.nodes["img-gen-gen-v1"]).toBeUndefined();
      expect(r.deletedIds).toEqual(["video-gen-gen-v1", "story-x"]);

      // 幂等：再跑一次不动
      const again = migrateMediaLayoutKeys([videoGen], r.nodes, r.deletedIds);
      expect(again.changed).toBe(false);
      expect(again.nodes).toEqual(r.nodes);
    });

    it("migrateMediaLayoutKeys：图片记录与已迁移记录不参与", () => {
      const r = migrateMediaLayoutKeys([makeGen("i1", "图")], { "img-gen-i1": { x: 0, y: 0, w: 1, h: 1 } }, []);
      expect(r.changed).toBe(false);
    });
  });

  describe("isArchiveProductNode（整理时的废案组判定）", () => {
    it("生成产物节点算废案", () => {
      expect(isArchiveProductNode("img-gen-da0d1bc25f30f7f1aeda435a4d3151bb3cb77f3c")).toBe(true);
      expect(isArchiveProductNode("video-gen-abc123")).toBe(true);
      expect(isArchiveProductNode("audio-gen-abc123")).toBe(true);
    });

    it("剧情/输入/工具/素材节点不算废案", () => {
      expect(isArchiveProductNode("story-3ff8f4ad-ab52-4958")).toBe(false);
      expect(isArchiveProductNode("input-1a2b3c4d")).toBe(false);
      expect(isArchiveProductNode("wftool-tpl-1")).toBe(false);
      expect(isArchiveProductNode("insp-1a2b3c4d")).toBe(false);
      expect(isArchiveProductNode("ref-1a2b3c4d")).toBe(false);
    });

    it("废案组排在剧情网格右侧且与剧情矩形不重叠", () => {
      // 剧情 4 楼 → 3 列 2 行；废案组从这里算出的右边界 + 96 起排
      const storySizes = [
        { w: 240, h: 240 }, { w: 240, h: 240 }, { w: 240, h: 240 },
        { w: 180, h: 320 },
      ];
      const storyPs = arrangeStoryGrid(storySizes, [], { cols: 3, originX: 24, originY: 24 });
      const right = Math.max(...storyPs.map((p, i) => p.x + storySizes[i].w));
      const storyRects = storyPs.map((p, i) => ({ ...p, ...storySizes[i] }));

      const archiveSizes = [{ w: 240, h: 300 }, { w: 240, h: 300 }];
      const archivePs = arrangeStoryGrid(archiveSizes, storyRects, { cols: 4, originX: right + 96, originY: 24 });

      expect(archivePs[0].x).toBe(right + 96);
      const overlaps = (a: { x: number; y: number; w: number; h: number }, r: { x: number; y: number; w: number; h: number }) =>
        a.x < r.x + r.w && a.x + a.w > r.x && a.y < r.y + r.h && a.y + a.h > r.y;
      for (const ap of archivePs) {
        const a = { ...ap, ...archiveSizes[archivePs.indexOf(ap)] };
        for (const r of storyRects) expect(overlaps(a, r)).toBe(false);
      }
    });
  });

  describe("整理分组：剧情主网格 + 废案组（真实尺寸序列）", () => {
    // 真实画布形态：14 楼（12 张 240×240 + 2 张 180×320，按 4 列排），
    // 两张灵感卡卡在剧情带中间（x=264/504，y=568~968）。
    const storySizes = [
      ...[0, 1, 2].flatMap(() => [{ w: 240, h: 240 }, { w: 240, h: 240 }, { w: 240, h: 240 }, { w: 180, h: 320 }]),
      { w: 240, h: 240 }, { w: 240, h: 240 },
    ];
    const cards = [
      { x: 264, y: 568, w: 180, h: 320 },
      { x: 504, y: 648, w: 180, h: 320 },
    ];

    it("剧情整块连续：4 行行距恒为行高 + gapY，不被灵感卡逐行打洞", () => {
      const ps = arrangeStoryGrid(storySizes, cards, { cols: 4, originX: 24, originY: 24 });

      expect(ps).toHaveLength(14);
      // 块高 = 3×320 + 240 + 3×24 = 1272；卡底 968 + gap 24 = 992，步进 24 落到 1008
      expect(ps[0]).toEqual({ x: 24, y: 1008 });
      for (const i of [4, 8, 12]) expect(ps[i].y - ps[0].y).toBe(i / 4 * 344);
      // 4 列 x 轴固定
      expect(ps.map((p) => p.x).slice(0, 4)).toEqual([24, 288, 552, 816]);
    });

    it("废案组排在剧情右边界 + 96，整组不重叠", () => {
      const storyPs = arrangeStoryGrid(storySizes, cards, { cols: 4, originX: 24, originY: 24 });
      const storyRects = storyPs.map((p, i) => ({ ...p, ...storySizes[i] }));
      const right = Math.max(...storyPs.map((p, i) => p.x + storySizes[i].w)); // 996
      const archiveSizes = Array.from({ length: 9 }, () => ({ w: 240, h: 300 }));

      const ps = arrangeStoryGrid(archiveSizes, [...cards, ...storyRects], {
        cols: 4, originX: right + 96, originY: 24,
      });

      expect(right).toBe(996);
      expect(ps[0]).toEqual({ x: 1092, y: 24 });
      expect(ps.map((p) => p.y)).toEqual([24, 24, 24, 24, 348, 348, 348, 348, 672]);
      const rects = ps.map((p) => ({ ...p, w: 240, h: 300 }));
      for (const a of rects) {
        for (const r of storyRects) {
          expect(a.x < r.x + r.w && a.x + a.w > r.x && a.y < r.y + r.h && a.y + a.h > r.y).toBe(false);
        }
      }
    });

    it("planStoryArrange：两组一次算完，废案从主网格右边界 + 96 起；废案为空时只回剧情组", () => {
      const story = storySizes.map((s, i) => ({ id: `s${i}`, ...s }));
      const archive = Array.from({ length: 9 }, (_, i) => ({ id: `a${i}`, w: 240, h: 300 }));
      const opts = { cols: 4, originX: 24, originY: 24 };

      const plan = planStoryArrange(story, archive, cards, opts);

      expect(plan.story.map((p) => p.id)).toEqual(story.map((s) => s.id));
      expect(plan.story[0]).toEqual({ id: "s0", x: 24, y: 1008, w: 240, h: 240 });
      expect(plan.archive[0]).toEqual({ id: "a0", x: 1092, y: 24, w: 240, h: 300 });
      for (const a of plan.archive) {
        for (const s of plan.story) {
          expect(a.x < s.x + s.w && a.x + a.w > s.x && a.y < s.y + s.h && a.y + a.h > s.y).toBe(false);
        }
      }

      const only = planStoryArrange(story, [], cards, opts);
      expect(only.archive).toEqual([]);
      expect(only.story).toHaveLength(14);
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
