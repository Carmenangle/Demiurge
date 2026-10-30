// @vitest-environment jsdom
// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { existsSync, readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { MODEL_SETTING_KEYS } from "./mobileModelSettings";

// 本仓未装 @types/node（应用代码不碰 Node API），但本用例要按 cwd 找后端源码 ⇒ 就地声明最小签名。
declare const process: { cwd(): string };

/**
 * 「哪些设置字段归手机」的跨语言契约（2026-10-30 联机模型所有权）。
 *
 * 前端把模型字段从回写电脑的 settings 里摘掉、另存手机本机端点；后端用同一张清单
 * ①校验本机端点收哪些字段 ②在电脑侧对缺省字段沿用盘上旧值。两边分叉会静默丢配置
 * （手机存不进去 / 电脑模型被抹），且没有类型系统能拦住跨语言清单 ⇒ 直接读后端源码对表。
 */
function readBackend(rel: string): string {
  const candidates = [`${process.cwd()}/../${rel}`, `${process.cwd()}/${rel}`];
  const hit = candidates.find((p) => existsSync(p));
  if (!hit) throw new Error(`找不到后端源码 ${rel}（试过：${candidates.join(" / ")}）`);
  return readFileSync(hit, "utf-8");
}

function tupleStrings(source: string, name: string): string[] {
  const block = source.match(new RegExp(`${name}\\s*=\\s*\\(([^)]*)\\)`));
  expect(block, `backend 源码里找不到 ${name}`).not.toBeNull();
  return [...block![1].matchAll(/"([^"]+)"/g)].map((m) => m[1]);
}

const secret = readBackend("backend/app/services/secret_fields.py");
const localModels = readBackend("backend/app/services/mobile_local_models.py");

function backendModelKeys(): string[] {
  const list = tupleStrings(secret, "MODEL_LIST_KEYS");
  const objs = tupleStrings(secret, "MODEL_OBJ_KEYS");
  const block = localModels.match(/MODEL_KEYS[^=]*=\s*\(([\s\S]*?)\n\)/);
  expect(block, "backend 源码里找不到 MODEL_KEYS").not.toBeNull();
  const ids = [...block![1].matchAll(/"([^"]+)"/g)].map((m) => m[1]);
  return [...list, ...ids, ...objs];
}

describe("联机模型字段清单：前端投影 = 后端 MODEL_KEYS", () => {
  it("两边字段完全一致", () => {
    expect([...MODEL_SETTING_KEYS].sort()).toEqual(backendModelKeys().sort());
  });

  it("后端确实用这张清单做本机端点校验与电脑侧缺席保留", () => {
    expect(localModels).toContain("MODEL_KEYS");
    expect(localModels).toContain("def preserve_stored_models");
    expect(readBackend("backend/app/routers/user_state.py"))
      .toContain("preserve_stored_models");
  });
});
