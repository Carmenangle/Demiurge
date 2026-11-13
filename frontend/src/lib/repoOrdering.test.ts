import { describe, expect, it } from "vitest";
import type { Repo } from "../stores/repos";
import {
  coverNeedsRepair,
  isVideoCover,
  latestGeneratedCover,
  orderReposByLatestGeneration,
  recordGeneratedRepoCover,
  replaceRepoCover,
} from "./repoOrdering";

const repo = (id: string, createdAt: number, coverAt?: number, parentId?: string): Repo => ({
  id,
  name: id,
  createdAt,
  ...(coverAt === undefined ? {} : { coverAt }),
  ...(parentId === undefined ? {} : { parentId }),
});

describe("视频封面识别", () => {
  it("识别直链、ComfyUI view 与 local-view 查询串里的视频", () => {
    expect(isVideoCover("http://x/v.mp4")).toBe(true);
    expect(isVideoCover("http://x/view?filename=out.webm&subfolder=")).toBe(true);
    expect(isVideoCover("/local-view?path=D%3A%5Cwork%5Cv.mov")).toBe(true);
    expect(isVideoCover("https://x/clip.MKV?token=1")).toBe(true);
  });

  it("图片、动图与无扩展名地址不误判", () => {
    expect(isVideoCover("http://x/a.png")).toBe(false);
    expect(isVideoCover("http://x/a.gif?filename=a.gif")).toBe(false);
    expect(isVideoCover("/local-view?path=D%3A%5Cwork%5Ca.webp")).toBe(false);
    expect(isVideoCover("http://x/media?id=abc")).toBe(false);
    expect(isVideoCover(undefined)).toBe(false);
  });
});

describe("历史封面回填", () => {
  const item = (patch: {
    source?: string;
    mediaType?: "image" | "video" | "audio";
    image_url?: string;
    created_at?: number;
  }) => ({ image_url: "/media/a.png", created_at: 100, ...patch });

  it("类型跟着最新一条走：最新视频用视频封面，最新图片用图片封面", () => {
    expect(latestGeneratedCover([
      item({ mediaType: "image", created_at: 100 }),
      item({ mediaType: "video", image_url: "/media/v.mp4", created_at: 200 }),
    ])).toEqual({ url: "/media/v.mp4", at: 200 });
    expect(latestGeneratedCover([
      item({ mediaType: "video", image_url: "/media/v.mp4", created_at: 100 }),
      item({ mediaType: "image", image_url: "/media/new.png", created_at: 200 }),
    ])).toEqual({ url: "/media/new.png", at: 200 });
  });

  it("跳过联网/本地导入与音频资产", () => {
    expect(latestGeneratedCover([
      item({ source: "web", created_at: 300 }),
      item({ source: "import", created_at: 400 }),
      item({ mediaType: "audio", created_at: 500 }),
      item({ mediaType: "image", created_at: 100 }),
    ])).toEqual({ url: "/media/a.png", at: 100 });
    expect(latestGeneratedCover([])).toBeNull();
  });

  it("只在新媒体晚于 coverAt 且地址不同时回填", () => {
    expect(coverNeedsRepair("/old.png", 100, { url: "/v.mp4", at: 200 })).toBe(true);
    expect(coverNeedsRepair("/v.mp4", 100, { url: "/v.mp4", at: 200 })).toBe(false);
    expect(coverNeedsRepair("/old.png", 200, { url: "/v.mp4", at: 200 })).toBe(false);
    expect(coverNeedsRepair(undefined, undefined, null)).toBe(false);
  });
});

describe("repository generation ordering", () => {
  it("orders small repositories by latest generated image, not creation time", () => {
    const olderRepoWithNewImage = repo("new-image", 100, 900, "parent");
    const newerRepoWithOldImage = repo("old-image", 800, 200, "parent");

    expect(orderReposByLatestGeneration(
      [newerRepoWithOldImage, olderRepoWithNewImage],
      [newerRepoWithOldImage, olderRepoWithNewImage],
    ).map((item) => item.id)).toEqual(["new-image", "old-image"]);
  });

  it("orders top-level repositories by the newest image in their descendants", () => {
    const first = repo("first", 900);
    const second = repo("second", 800);
    const items = [
      first,
      second,
      repo("first-child", 100, 300, "first"),
      repo("second-child", 200, 700, "second"),
    ];

    expect(orderReposByLatestGeneration([first, second], items).map((item) => item.id))
      .toEqual(["second", "first"]);
  });

  it("puts repositories without generated images last and keeps them newest-first", () => {
    const generated = repo("generated", 100, 200);
    const newerEmpty = repo("newer-empty", 900);
    const olderEmpty = repo("older-empty", 500);

    expect(orderReposByLatestGeneration(
      [olderEmpty, generated, newerEmpty],
      [olderEmpty, generated, newerEmpty],
    ).map((item) => item.id)).toEqual(["generated", "newer-empty", "older-empty"]);
  });

  it("does not mutate the source array", () => {
    const items = [repo("old", 100, 100), repo("new", 200, 200)];
    orderReposByLatestGeneration(items, items);
    expect(items.map((item) => item.id)).toEqual(["old", "new"]);
  });

  it("does not change generation order time when an old image is chosen as cover", () => {
    const items = [repo("target", 100, 500)];
    const updated = replaceRepoCover(items, "target", "/old-image.png");

    expect(updated[0]).toMatchObject({ cover: "/old-image.png", coverAt: 500 });
  });

  it("records the timestamp only when a new generated image is saved", () => {
    const items = [repo("target", 100, 500)];
    const updated = recordGeneratedRepoCover(items, "target", "/new-image.png", 900);

    expect(updated[0]).toMatchObject({ cover: "/new-image.png", coverAt: 900 });
  });
});
