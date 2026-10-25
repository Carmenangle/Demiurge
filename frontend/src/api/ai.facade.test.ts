// api/ai 门面与拆分形态锁（2026-10-24 按资源域拆分）。
// 真实失败路径：拆分后域模块漏导出 / 门面漏 re-export / 域模块反向依赖视图层——
// 消费方（41 个文件）在编译期或运行期才炸。这里三层锁：
// ① 源码扫描：域模块 export 符号全集 ≡ 预期清单（漏一个即红，含 type/interface）；
// ② 门面形态：ai.ts 已删除、index.ts 纯 re-export（无函数体）、域清单与 re-export 一一对应；
// ③ 分层：域模块禁止 import views/components（api 层不得反向依赖视图层）。
// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync, statSync } from "node:fs";
import { describe, expect, it } from "vitest";
import * as facade from "./ai";

const AI_DIR = new URL("./ai/", import.meta.url);
const readAi = (rel: string) => readFileSync(new URL(rel, AI_DIR), "utf8");

/** 预期符号全集（按域分组；与各域模块 export 一一对应，新增域/符号必须同步登记）。 */
const EXPECTED: Record<string, string[]> = {
  "wire.ts": [
    "POLL_TIMEOUT_MS", "Embed", "Chat", "ChatCfg", "VlmWire",
    "chatBody", "ragEmbed", "sseEmbed",
  ],
  "attachments.ts": ["FileAttachmentMeta", "uploadAttachment", "attachmentUrl"],
  "prompts.ts": [
    "GenPromptResult", "genPrompt", "describeImage", "translateText", "DescribeResult",
    "describeWorkflow", "polishDescription", "genProfilePrompt", "FramePromptItem",
    "genFramePrompts", "getProfilePromptDefaults", "extractKeywords",
  ],
  "workflowPorts.ts": ["PortOp", "PortsPlan", "workflowPorts"],
  "inspiration.ts": ["Inspiration", "fetchInspiration", "selectInspiration", "proxyImageUrl"],
  "chatSession.ts": [
    "ChatTurn", "MemorySealResult", "sealMemory", "fetchHistory", "clearCache",
    "compactHistory", "saveSnapshot", "fetchSnapshot", "editMessageRemote", "chatAppend",
    "exportSnapshot", "importSnapshot",
  ],
  "agent.ts": [
    "multiAgent", "regenerateImage", "fetchAgentRunning", "cancelAgent", "fetchRunningChatThreads",
  ],
  "agentInvocation.ts": ["AgentInvocation", "agentInvocationBody"],
  "chatQueue.ts": [
    "ChatQueueStatus", "ChatQueueTask", "enqueueChatQueueTask", "listChatQueueTasks",
    "cancelChatQueueTask",
  ],
  "illustrationTelemetry.ts": [
    "reportIllustrationFailure", "claimIllustrationSubmission", "reportIllustrationStage",
    "reportIllustrationSubmission", "reportAudioSubmission", "ensureAudioSlot",
  ],
  "build.ts": [
    "syncNodes", "SyncProgress", "syncProgress", "nodeStats", "NodePackItem", "NodePackDetail",
    "listNodePacks", "getNodePack", "updateNodePackContent", "BuildResult", "BuildTurn",
    "buildWorkflow", "buildModule", "buildDirect", "buildPlan", "saveWorkflow", "Skeleton",
    "listSkeletons", "skeletonGraph", "BuildSessionMeta", "BuildSessionFull", "listBuildSessions",
    "getBuildSession", "saveBuildSession", "deleteBuildSession", "WorkflowBuildTaskStatus",
    "WorkflowBuildTask", "enqueueWorkflowBuildTask", "listWorkflowBuildTasks",
    "cancelWorkflowBuildTask",
  ],
  "rag.ts": [
    "indexGeneration", "indexDocument", "importDocuments", "RagDoc", "listDocs", "Generation",
    "WEB_MATERIALS_REPO_ID", "listGenerations", "wd14Tag", "TaggerInfo", "listTaggers",
    "tagGeneration", "TagSnapshot", "listTagSnapshots", "activateTagSnapshot", "deleteTagSnapshot",
    "generateThumbs", "searchGenerations", "VisualPreferenceReason", "recordVisualPreference",
    "setGenerationDescription", "indexVisualGenerations", "describeMissingAssets",
    "pruneGenerations", "pruneOrphanRepos", "RepoSyncRepo", "RepoSyncReport", "repoSyncReport",
    "repoSyncFix", "searchAssets", "listTrash", "restoreAsset", "purgeAsset", "tagFromText",
    "clearTrash", "TagStat", "tagStats", "setTags", "deleteDoc", "updateDoc",
  ],
  "webMaterials.ts": [
    "WebMaterial", "listWebMaterials", "WebMaterialSearchItem", "searchWebMaterials", "PixivUser",
    "pixivFollowing", "pixivPages", "favoriteWebMaterial", "marketImageProxyUrl",
    "saveWebMaterial", "saveWebSeries", "deleteWebMaterial", "InspirationCardImage",
    "InspirationCardAsset", "saveInspirationCard", "listInspirationCards", "getInspirationCard",
    "updateInspirationCard", "deleteInspirationCard", "ReferenceImage", "listReferenceImages",
  ],
  "support.ts": ["supportStream"],
};

/** 从模块源码提取 export 符号名（type/interface/const/function/async function/let）。 */
function exportedSymbols(src: string): string[] {
  const names: string[] = [];
  const re = /export\s+(?:async\s+)?(?:type|interface|const|function|let)\s+([A-Za-z_$][\w$]*)/g;
  for (const m of src.matchAll(re)) names.push(m[1]);
  return names;
}

describe("api/ai 拆分形态锁", () => {
  it("旧单文件已删除，目录门面在位", () => {
    expect(() => statSync(new URL("./ai.ts", import.meta.url))).toThrow();  // 相对本文件＝src/api/ai.ts
    expect(statSync(AI_DIR).isDirectory()).toBe(true);
  });

  it("域模块 export 符号全集 ≡ 预期清单（漏导出/误删/私化即红）", () => {
    for (const [file, expected] of Object.entries(EXPECTED)) {
      const src = readAi(file);
      expect(exportedSymbols(src).sort(), file).toEqual([...expected].sort());
    }
  });

  it("门面是纯 re-export：逐域 export *，无函数体/常量定义", () => {
    const src = readAi("index.ts");
    const reexports = [...src.matchAll(/export \* from "\.\/([\w]+)";/g)].map((m) => `${m[1]}.ts`);
    expect(reexports.sort()).toEqual(Object.keys(EXPECTED).sort());
    // 除 re-export 行外只允许注释；出现 function/const/type 定义即门面被污染
    const code = src.replace(/\/\/[^\n]*/g, "").trim();
    expect(code).not.toMatch(/\b(function|const|let|interface)\b/);
  });

  it("域模块禁止反向依赖视图层（api → views/components 即红）", () => {
    for (const file of Object.keys(EXPECTED)) {
      const src = readAi(file);
      expect(src, file).not.toMatch(/from\s+"(\.\.\/)+(views|components)\//);
    }
  });

  it("运行时门面暴露的值符号与预期一致（re-export 漏项即红）", () => {
    const values = Object.values(EXPECTED).flat().filter((name) => {
      // 类型符号在运行时不可见；值符号必须出现在门面命名空间
      return name in (facade as unknown as Record<string, unknown>);
    });
    // 反向校验：预期里的每个「非类型」符号都能从门面取到
    const typeNames = new Set([
      "Embed", "Chat", "ChatCfg", "VlmWire", "FileAttachmentMeta", "GenPromptResult",
      "DescribeResult", "FramePromptItem", "PortOp", "PortsPlan", "Inspiration", "ChatTurn",
      "MemorySealResult", "AgentInvocation", "ChatQueueStatus", "ChatQueueTask", "SyncProgress",
      "NodePackItem", "NodePackDetail", "BuildResult", "BuildTurn", "Skeleton",
      "BuildSessionMeta", "BuildSessionFull", "WorkflowBuildTaskStatus", "WorkflowBuildTask",
      "RagDoc", "Generation", "TaggerInfo", "TagSnapshot", "TagStat", "VisualPreferenceReason",
      "RepoSyncRepo", "RepoSyncReport", "WebMaterial", "WebMaterialSearchItem", "PixivUser",
      "InspirationCardImage", "InspirationCardAsset", "ReferenceImage",
    ]);
    const expectedValues = Object.values(EXPECTED).flat().filter((n) => !typeNames.has(n));
    expect(values.sort()).toEqual([...expectedValues].sort());
  });
});
