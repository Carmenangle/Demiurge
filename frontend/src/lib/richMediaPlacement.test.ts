import { describe, expect, it } from "vitest";
import { orphanMediaIndexes, splitMediaChunks } from "./richMediaPlacement";

const S = (n: number) => `\uE000LAF_MEDIA_${n}\uE001`;

describe("splitMediaChunks", () => {
  it("无媒体槽时不切（调用方走纯正文渲染）", () => {
    expect(splitMediaChunks("正文", 0)).toEqual([]);
    expect(orphanMediaIndexes([], 0)).toEqual([]);
  });

  it("哨兵齐全时按「正文 / 槽下标」交替切出顺序，无槽退到尾部", () => {
    const chunks = splitMediaChunks(`前段${S(0)}中段${S(1)}尾段`, 2);
    expect(chunks).toEqual(["前段", 0, "中段", 1, "尾段"]);
    expect(orphanMediaIndexes(chunks, 2)).toEqual([]);
  });

  it("显示层正则吃掉中间哨兵：该槽退到正文尾部，其余位置不变", () => {
    const chunks = splitMediaChunks(`前段${S(0)}中段尾段`, 2);
    expect(chunks).toEqual(["前段", 0, "中段尾段"]);
    expect(orphanMediaIndexes(chunks, 2)).toEqual([1]);
  });

  it("全部哨兵被吃掉：所有槽按原顺序退到尾部，一张不丢", () => {
    const chunks = splitMediaChunks("整段正文没有哨兵", 3);
    expect(chunks).toEqual(["整段正文没有哨兵"]);
    expect(orphanMediaIndexes(chunks, 3)).toEqual([0, 1, 2]);
  });

  it("越界槽下标不算已定位，真槽仍进孤儿兜底", () => {
    const chunks = splitMediaChunks(`正文${S(9)}`, 2);
    expect(chunks).toEqual(["正文", 9, ""]);
    expect(orphanMediaIndexes(chunks, 2)).toEqual([0, 1]);
  });
});
