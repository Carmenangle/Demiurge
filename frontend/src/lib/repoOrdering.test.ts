import { describe, expect, it } from "vitest";
import type { Repo } from "../stores/repos";
import {
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
