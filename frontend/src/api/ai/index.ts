// api/ai 门面（2026-10-24 按资源域拆分，原 1631 行单文件 → api/ai/ 目录 13 个域模块）。
//
// 合同：
// - 存量消费方一律 `from "../api/ai"`（本文件 re-export 全量符号，导入方零改动；vi.mock 本
//   specifier 的测试同样不受影响）；
// - 新代码建议按域直导（如 `from "../api/ai/rag"`），减小耦合面；
// - 域清单与符号全集由 `ai.facade.test.ts` 源码形态锁钉死——新增域必须同步登记该锁，
//   域模块禁止 import views/components（api 层不得反向依赖视图层）。
export * from "./agent";
export * from "./agentInvocation";
export * from "./attachments";
export * from "./build";
export * from "./chatQueue";
export * from "./chatSession";
export * from "./illustrationTelemetry";
export * from "./inspiration";
export * from "./prompts";
export * from "./rag";
export * from "./support";
export * from "./webMaterials";
export * from "./wire";
export * from "./workflowPorts";
