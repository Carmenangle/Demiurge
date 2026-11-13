import { describe, expect, it, vi } from "vitest";
// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync } from "node:fs";
import {
  buildChatRoomHistory, chatContextPressure, CHAT_CONTEXT_FULL_COUNT, CHAT_CONTEXT_WARN_CHARS,
  CHAT_TIME_DIVIDER_SECONDS,
  chatRoomMessagesToChat, chatRoomThreadId, loadHandledChatMedia, markHandledChatMedia,
  defaultChatRoomSelection, formatChatTimeDivider, mergeChatRoomMessages,
  mergeSnapshotMessages, resolveChatRoomMembers, sanitizeChatText, shouldShowChatTimeDivider,
  shouldUpdateChatAvatar, shouldStickChatToBottom, sortChatRoomMembers,
} from "./chatRoom";
import type { ChatMessage } from "../types/chat";
import {
  chatMediaTasksForThread, finishChatMediaTask, startChatMediaTask, subscribeChatMediaTasks,
} from "./chatMediaActivity";

const chatRoomView = readFileSync(
  new URL("../components/chat/ChatRoomView.tsx", import.meta.url), "utf8",
);

describe("聊天 / 群聊纯逻辑", () => {
  it("私聊每个角色独立线程，群聊共用一条", () => {
    expect(chatRoomThreadId("repo-1", "chat")).toBe("repo-1__chat");
    // 与后端 chat_room.thread_id_for 逐字一致（后端 roster 返回的 member_threads 用它）。
    expect(chatRoomThreadId("repo-1", "chat", "腓特烈大帝"))
      .toBe("repo-1__chat__腓特烈大帝_mv3ud7");
    expect(chatRoomThreadId("repo-1", "chat", "腓特烈大帝"))
      .not.toBe(chatRoomThreadId("repo-1", "chat", "奥古斯特"));
    expect(chatRoomThreadId("repo-1", "group_chat", "腓特烈大帝")).toBe("repo-1__group");
    expect(chatRoomThreadId("", "chat", "甲")).toMatch(/^home__chat__甲_/);
  });

  it("只有非常规装扮 [AVATAR] 能换聊天头像（所有作品统一）", () => {
    expect(shouldUpdateChatAvatar({ avatar: true }, true)).toBe(true);
    // 开关关闭 ⇒ 连头像替换都不做
    expect(shouldUpdateChatAvatar({ avatar: true }, false)).toBe(false);
    // 普通 [SELFIE]（无 avatar 标记）永远不覆盖
    expect(shouldUpdateChatAvatar({}, true)).toBe(false);
    expect(shouldUpdateChatAvatar({ avatar: false }, true)).toBe(false);
    expect(shouldUpdateChatAvatar(null, true)).toBe(false);
  });

  it("向上翻历史时不被新消息或图片拽回底部", () => {
    // 用户已经上翻：助理新消息/图片到达不贴底
    expect(shouldStickChatToBottom({ lastRole: "assistant", atBottom: false })).toBe(false);
    // 本来就在底部：继续贴底
    expect(shouldStickChatToBottom({ lastRole: "assistant", atBottom: true })).toBe(true);
    // 用户自己刚发消息：无论之前在哪都回到底部
    expect(shouldStickChatToBottom({ lastRole: "user", atBottom: false })).toBe(true);
  });

  it("生成中快照兜底：同 id 保留更长的流式文本，快照新增消息补进来", () => {
    const current: ChatMessage[] = [
      { id: "u1", role: "user", text: "在吗" },
      { id: "a1", role: "assistant", speaker: "甲", text: "这是已经流式显示出来的完整回复" },
      { id: "local", role: "assistant", speaker: "甲", text: "本地刚生成、快照还没有" },
    ];
    const snapshot: ChatMessage[] = [
      { id: "u1", role: "user", text: "在吗" },
      { id: "a1", role: "assistant", speaker: "甲", text: "半成品" },
      { id: "a2", role: "assistant", speaker: "甲", text: "快照新增的第二条" },
    ];

    const merged = mergeSnapshotMessages(current, snapshot);

    expect(merged.map((m) => m.id)).toEqual(["u1", "a1", "a2", "local"]);
    expect(merged.find((m) => m.id === "a1")?.text).toBe("这是已经流式显示出来的完整回复");
  });

  it("成员解析：私聊只取首位，群聊保持勾选顺序并按绑定卡过滤", () => {
    expect(resolveChatRoomMembers("chat", ["甲", "乙"], ["乙", "甲"])).toEqual(["乙"]);
    expect(resolveChatRoomMembers("group_chat", ["甲", "乙"], ["乙", "路人"])).toEqual(["乙"]);
    expect(resolveChatRoomMembers("group_chat", ["甲", "乙"], [])).toEqual(["甲", "乙"]);
  });

  it("合集卡旧开场卡名不在名册时回退名册首位（不锁死在作品名）", () => {
    expect(resolveChatRoomMembers("chat", ["林昭", "苏晚"], ["堕落魔戒"])).toEqual(["林昭"]);
    expect(resolveChatRoomMembers("group_chat", ["林昭", "苏晚"], ["堕落魔戒"]))
      .toEqual(["林昭", "苏晚"]);
  });

  it("默认勾选：私聊取开场卡，群聊全选", () => {
    expect(defaultChatRoomSelection("chat", ["甲", "乙"], "乙")).toEqual(["乙"]);
    expect(defaultChatRoomSelection("group_chat", ["甲", "乙"], "乙")).toEqual(["甲", "乙"]);
  });

  it("历史序列化给 assistant 加 speaker 前缀", () => {
    const messages: ChatMessage[] = [
      { id: "u1", role: "user", text: "你们好" },
      { id: "a1", role: "assistant", text: "你好呀", speaker: "甲" },
      { id: "a2", role: "assistant", text: "嗯。", speaker: "乙" },
      { id: "a3", role: "assistant", text: "无发言人" },
    ];
    expect(buildChatRoomHistory(messages)).toEqual([
      { role: "user", content: "你们好" },
      { role: "assistant", content: "【甲】你好呀" },
      { role: "assistant", content: "【乙】嗯。" },
      { role: "assistant", content: "无发言人" },
    ]);
  });

  it("群聊上下文始终是整段对话，不是只保留上一轮", () => {
    const messages: ChatMessage[] = [
      { id: "u1", role: "user", text: "第一轮" },
      { id: "a1", role: "assistant", text: "甲的第一句", speaker: "甲" },
      { id: "u2", role: "user", text: "第二轮" },
      { id: "a2", role: "assistant", text: "乙的回复", speaker: "乙" },
    ];
    expect(buildChatRoomHistory(messages)).toEqual([
      { role: "user", content: "第一轮" },
      { role: "assistant", content: "【甲】甲的第一句" },
      { role: "user", content: "第二轮" },
      { role: "assistant", content: "【乙】乙的回复" },
    ]);
  });

  it("头像顺序只按最近消息时间倒序，同值保持名册原顺序", () => {
    const names = ["甲", "乙", "丙"];
    // 都没聊过：保持名册原顺序，不因「被选中/被打开」改变。
    expect(sortChatRoomMembers(names)).toEqual(["甲", "乙", "丙"]);
    expect(sortChatRoomMembers(names, { 乙: 300, 甲: 100 })).toEqual(["乙", "甲", "丙"]);
    // 同时间戳保持入参顺序（稳定），避免刷新后头像乱跳。
    expect(sortChatRoomMembers(names, { 甲: 300, 乙: 300 })).toEqual(["甲", "乙", "丙"]);
  });

  it("QQ 风格时间分隔：首条或间隔 ≥10 分钟才显示，连续消息不重复", () => {
    const base = new Date(2026, 10, 11, 14, 0, 0).getTime() / 1000;
    expect(shouldShowChatTimeDivider(base, undefined)).toBe(true);
    expect(shouldShowChatTimeDivider(base + CHAT_TIME_DIVIDER_SECONDS - 1, base)).toBe(false);
    expect(shouldShowChatTimeDivider(base + CHAT_TIME_DIVIDER_SECONDS, base)).toBe(true);
    expect(shouldShowChatTimeDivider(undefined, base)).toBe(false);
  });

  it("时间分隔文案：今天 HH:mm、昨天/更早带日期，跨天即使不足 10 分钟也显示", () => {
    const now = new Date(2026, 10, 11, 14, 0, 0).getTime() / 1000;
    const today = new Date(2026, 10, 11, 9, 5, 0).getTime() / 1000;
    const yesterday = new Date(2026, 10, 10, 23, 59, 0).getTime() / 1000;
    const older = new Date(2026, 9, 30, 8, 7, 0).getTime() / 1000;
    const afterMidnight = new Date(2026, 10, 11, 0, 1, 0).getTime() / 1000;

    expect(formatChatTimeDivider(today, now)).toBe("09:05");
    expect(formatChatTimeDivider(yesterday, now)).toBe("昨天 23:59");
    expect(formatChatTimeDivider(older, now)).toBe("10-30 08:07");
    expect(shouldShowChatTimeDivider(afterMidnight, yesterday)).toBe(true);
    expect(formatChatTimeDivider(0, now)).toBe("");
  });

  it("快照合并保留本地 ts，快照有 ts 时以快照为准", () => {
    const current: ChatMessage[] = [
      { id: "a1", role: "assistant", speaker: "甲", text: "流式文本", ts: 100 },
    ];
    expect(mergeSnapshotMessages(current, [
      { id: "a1", role: "assistant", speaker: "甲", text: "快照文本" },
    ])[0].ts).toBe(100);
    expect(mergeSnapshotMessages(current, [
      { id: "a1", role: "assistant", speaker: "甲", text: "快照文本", ts: 200 },
    ])[0].ts).toBe(200);
  });

  it("聊天流事件透传服务端 ts 与 poke，poke 不进模型历史", () => {
    expect(chatRoomMessagesToChat([
      { id: "m1", speaker: "甲", text: "嗨", ts: 1762500000 },
      { id: "m2", speaker: "甲", text: "戳了戳你", poke: true },
    ])).toEqual([
      { id: "m1", role: "assistant", speaker: "甲", text: "嗨", ts: 1762500000 },
      { id: "m2", role: "assistant", speaker: "甲", text: "戳了戳你", poke: true },
    ]);
    expect(buildChatRoomHistory([
      { id: "u1", role: "user", text: "在吗" },
      { id: "p1", role: "assistant", speaker: "甲", text: "戳了戳你", poke: true },
      { id: "a1", role: "assistant", speaker: "甲", text: "在。" },
    ])).toEqual([
      { role: "user", content: "在吗" },
      { role: "assistant", content: "【甲】在。" },
    ]);
  });

  it("流事件按后端 id upsert，不产生重复气泡", () => {
    const first = chatRoomMessagesToChat([
      { id: "m1", speaker: "甲", text: "嗨" },
    ]);
    expect(first).toEqual([{ id: "m1", role: "assistant", speaker: "甲", text: "嗨" }]);
    const next = mergeChatRoomMessages(first, chatRoomMessagesToChat([
      { id: "m1", speaker: "甲", text: "嗨（更新）" },
      { id: "m2", speaker: "乙", text: "我也在" },
    ]));
    expect(next).toHaveLength(2);
    expect(next[0].text).toBe("嗨（更新）");
    expect(next[1].speaker).toBe("乙");
  });

  it("上下文压力按可入模文本估算，system / poke 不计入", () => {
    const short = chatContextPressure([
      { id: "u1", role: "user", text: "在吗" },
      { id: "a1", role: "assistant", speaker: "甲", text: "在。" },
      { id: "p1", role: "assistant", speaker: "甲", text: "戳了戳你", poke: true },
      { id: "e1", role: "assistant", text: "聊天失败：超时", system: true },
    ]);
    expect(short.count).toBe(2);
    expect(short.chars).toBe(4);
    expect(short.level).toBe("ok");

    const long = chatContextPressure([
      { id: "u1", role: "user", text: "字".repeat(CHAT_CONTEXT_WARN_CHARS) },
    ]);
    expect(long.level).toBe("warn");

    const many: ChatMessage[] = Array.from({ length: CHAT_CONTEXT_FULL_COUNT }, (_, index) => ({
      id: `m${index}`, role: "user" as const, text: "嗯",
    }));
    expect(chatContextPressure(many).level).toBe("full");
  });

  it("上下文压力阈值跟随 contextMaxTokens（预算调大不再过早提醒）", () => {
    const mid = [{ id: "u1", role: "user" as const, text: "字".repeat(13_000) }];
    expect(chatContextPressure(mid, 20_000).level).toBe("warn");
    expect(chatContextPressure(mid, 100_000).level).toBe("ok");

    const nearFull = [{ id: "u1", role: "user" as const, text: "字".repeat(19_000) }];
    expect(chatContextPressure(nearFull, 20_000).level).toBe("full");
    expect(chatContextPressure(nearFull, 100_000).level).toBe("ok");

    const manyLong = Array.from({ length: 200 }, (_, index) => ({
      id: `m${index}`, role: "user" as const, text: "嗯",
    }));
    expect(chatContextPressure(manyLong, 20_000).level).toBe("full");
    expect(chatContextPressure(manyLong, 100_000).level).toBe("ok");
  });

  it("旧记录已被自动摘要时，压力条只算服务端保留的尾部", () => {
    const old = Array.from({ length: 400 }, (_, index) => ({
      id: `old${index}`, role: "user" as const, text: "旧".repeat(500),
    }));
    const recent = Array.from({ length: 5 }, (_, index) => ({
      id: `new${index}`, role: "assistant" as const, text: "新".repeat(100),
    }));

    // 20 万字旧记录：此前会永远报 full；现在只按自动摘要触发线内的尾部算。
    const pressure = chatContextPressure([...old, ...recent], 100_000);
    expect(pressure.level).not.toBe("full");
    expect(pressure.chars).toBeLessThan(400 * 500);
    expect(pressure.count).toBeLessThan(400);

    // 最近一条本身就把尾部撑爆 → 仍要报 full。
    const huge = [{ id: "u1", role: "user" as const, text: "字".repeat(100_000) }];
    expect(chatContextPressure(huge, 20_000).level).toBe("full");
  });

  it("后台追问/主动持有线程锁时，用户发送先取消并等待释放", () => {
    expect(chatRoomView).toContain("const backgroundBusyRef = useRef(false);");
    expect(chatRoomView).toContain("正在结束后台消息…");
    expect(chatRoomView).toContain("await cancelAgent(threadId);");
    expect(chatRoomView).toContain("await waitForAgentRelease(threadId);");
    expect(chatRoomView).toContain("后台消息还在生成，稍等几秒再发。");
  });

  it("私聊消息支持编辑 / 删除 / 重新发送 / 重新生成", () => {
    expect(chatRoomView).toContain("editMessageRemote(threadId, updated)");
    expect(chatRoomView).toContain("saveSnapshot(threadId, next)");
    expect(chatRoomView).toContain("重新发送");
    expect(chatRoomView).toContain("重新生成");
    expect(chatRoomView).toContain("删除这条消息？");
    expect(chatRoomView).toContain("重新发送将删除这条消息及其后的");
    expect(chatRoomView).toContain("重新生成将删除此消息后的");
  });

  it("聊天回合结束后仍会发起自发媒体拍摄，不同角色可并行", () => {
    // 不能再用回合 busy / 全局单闸：两个角色同时发照片时后一个会被静默丢弃。
    expect(chatRoomView).toContain(
      "if (chatMediaTasksForThread(threadId).some(",
    );
    expect(chatRoomView).not.toContain("|| mediaBusyRef.current) return;");
    expect(chatRoomView).not.toContain("if (settings.chatRoomMediaInsert !== true || busy) return;");
  });

  it("后台追问/重开也处理媒体请求，并显示正在输入中", () => {
    expect(chatRoomView).toContain('const [backgroundTyping, setBackgroundTyping] = useState("");');
    expect(chatRoomView).toContain("setBackgroundTyping(`${speaker} 正在输入中…`)");
    // 追问只合并文字会把角色自己发的照片丢掉。
    expect(chatRoomView).toContain("追问/重开里角色自己发的照片也要拍");
    expect(chatRoomView).toContain("generateChatMedia(pendingMedia);");
  });

  it("主动扫描不依赖刷新：进入私聊先扫一次，之后每 2 分钟重扫", () => {
    expect(chatRoomView).toContain("const timer = window.setInterval(scan, 120_000);");
    expect(chatRoomView).toContain('document.addEventListener("visibilitychange", onVisible);');
    expect(chatRoomView).toContain("window.addEventListener(\"focus\", onVisible);");
    expect(chatRoomView).toContain("lastInitiativeScanRef");
    expect(chatRoomView).not.toContain("initiativeScanKeyRef");
  });

  it("空闲时每 12s 同步快照，不刷新也能看到角色新消息", () => {
    expect(chatRoomView).toContain("}, 12_000);");
    expect(chatRoomView).toContain(
      "if (!alive || document.hidden || busyRef.current) return;",
    );
  });

  it("拍摄失败先等显卡空闲重拍，仍失败由角色用「网不好」带过", () => {
    expect(chatRoomView).toContain("generateChatMediaImageWithRetry");
    expect(chatRoomView).toContain("waitForComfyIdle");
    expect(chatRoomView).toContain("第一次没拍成，正在等显卡空闲重拍");
    expect(chatRoomView).toContain('console.warn("[chat-media] image failed", detail);');
    // 角色自己用网不好解释，技术原因只在日志里。
    expect(chatRoomView).toContain("刚网不太好，照片没发出去。等会儿我再给你补一张。");
    expect(chatRoomView).not.toContain("照片没拍成，稍后再试。");
    expect(chatRoomView).not.toContain("拍摄失败：${detail}");
  });

  it("聊天自拍走多元数据插入的 ComfyUI 模板，不落生图 API", () => {
    expect(chatRoomView).toContain("generateChatMediaImageWithRetry");
    expect(chatRoomView).toContain('request.kind === "image" && preset?.templateId');
    expect(chatRoomView).toContain("templates.find((item) => item.id === preset.templateId)");
    expect(chatRoomView).toContain("多元数据插入绑定的工作流模板不存在");
    // 照片要能刷新后仍在：图片气泡并入聊天快照。
    expect(chatRoomView).toContain(
      "saveSnapshot(threadId, [...messagesRef.current, imageMessage])",
    );
    // 头像单独生成 1:1，不拿聊天里的照片顶替；图1成功后先补话，头像后台跑。
    expect(chatRoomView).toContain('aspectRatio: "1:1"');
    expect(chatRoomView).toContain("头像特写，正方形构图");
    // 头像短超时，卡住时中断，避免占着 ComfyUI 队列拖住后续拍摄。
    expect(chatRoomView).toContain("pollTimeoutMs: 5 * 60 * 1000");
    expect(chatRoomView).toContain('reactionInvocation.reactionKind = "image";');
    expect(chatRoomView).toContain("头像没换好。");
    expect(chatRoomView).toContain("chatReaction(reactionInvocation)");
  });

  it("拍摄中状态跨切屏保留：任务按线程登记，结束后移除", () => {
    let notified = 0;
    const off = subscribeChatMediaTasks(() => { notified += 1; });
    startChatMediaTask({ threadId: "t1", speaker: "甲", kind: "image" });
    startChatMediaTask({ threadId: "t2", speaker: "乙", kind: "video" });

    expect(chatMediaTasksForThread("t1").map((task) => task.speaker)).toEqual(["甲"]);
    expect(notified).toBeGreaterThan(0);

    finishChatMediaTask("t1", "甲");
    finishChatMediaTask("t2", "乙");
    expect(chatMediaTasksForThread("t1")).toEqual([]);
    off();
    expect(chatRoomView).toContain("startChatMediaTask({");
    expect(chatRoomView).toContain(
      "finishChatMediaTask(mediaTaskThread, request.speaker, mediaTaskId)",
    );
    expect(chatRoomView).toContain("正在拍摄中…");
    // 第一次失败等显卡空闲时显示 waiting，而不是误报「正在拍摄中」。
    expect(chatRoomView).toContain('mediaTasks[0].phase === "waiting"');
    expect(chatRoomView).toContain("正在等显卡空闲…");
    // 切角色不得把上一位的拍摄状态带进当前私聊（2026-11-11 实锤）。
    expect(chatRoomView).toContain("setMediaTasks([])");
    expect(chatRoomView).toContain("message.speaker === activeSpeaker");
  });

  it("拍摄期间不锁聊天输入，可取消；同轮多条媒体全部发起", () => {
    // 媒体不再占用聊天回合的 busy：输入框可继续发消息，顶部维护仍禁用。
    expect(chatRoomView).not.toContain("mediaHeldBusy");
    expect(chatRoomView).toContain("const mediaBusy = mediaTasks.length > 0;");
    expect(chatRoomView).toContain("onBusyChange?.(busy || mediaBusy)");
    expect(chatRoomView).toContain("cancelChatMediaTask(threadId, mediaTasks[0].speaker)");
    expect(chatRoomView).toContain("取消拍摄");
    // 取消时尽量连 ComfyUI 任务一起中断，不只停前端等待。
    expect(chatRoomView).toContain("interruptComfy(settings.comfyuiUrl, submittedPromptId)");
    // 群聊同一轮多位角色各发一条，不能只生成第一条。
    expect(chatRoomView).toContain(
      "for (const media of pendingMedia) generateChatMedia(media);",
    );
    // 任务带 id：旧任务的收尾不能删掉后来新起的拍摄。
    expect(chatRoomView).toContain("const mediaTaskId = startChatMediaTask({");
    expect(chatRoomView).toContain(
      "finishChatMediaTask(mediaTaskThread, request.speaker, mediaTaskId)",
    );
    // 等 GPU 空闲也要能被「取消拍摄」打断。
    expect(chatRoomView).toContain("shouldStop: () => cancelled");
    // 刷新后从已提交的 ComfyUI 任务续接，不重新提交。
    expect(chatRoomView).toContain("resumeChatMediaImage");
    expect(chatRoomView).toContain("loadChatMediaResume(threadId, speaker)");
    expect(chatRoomView).toContain("saveChatMediaResume({");
    expect(chatRoomView).toContain("resumePromptId");
    // 源消息被删除/重新生成后，迟到的图不再插回聊天。
    expect(chatRoomView).toContain(
      "!messagesRef.current.some((message) => message.id === request.sourceMessageId)",
    );
  });

  it("私聊不接收其他角色的消息与媒体（防止串线程）", () => {
    // 切角色前未收尾的 chat_messages 事件只能记到对方私聊的未读，不能并进当前聊天。
    expect(chatRoomView).toContain("const allIncoming = chatRoomMessagesToChat");
    expect(chatRoomView).toContain("message.speaker === activeSpeaker");
    expect(chatRoomView).toContain("request.speaker !== activeSpeaker");
    expect(chatRoomView).toContain("addUnread(privateId, message.speaker, 1)");
  });

  it("媒体标记与多余空行不进气泡（流式期间也不露）", () => {
    expect(sanitizeChatText("看。\n\n\n\n[SELFIE: 女仆装]")).toBe("看。");
    expect(sanitizeChatText("A\n\n\nB")).toBe("A\n\nB");
    // 流式中还没闭合的半个标记也要藏住
    expect(sanitizeChatText("[AVATAR: 婚纱")).toBe("");
    expect(chatRoomView).toContain("sanitizeChatText(replace ? delta : message.text + delta)");
  });

  it("整行括号动作不进气泡；正常聊天括号与媒体占位保留", () => {
    // 2026-11-11 用户实锤：聊天是隔着屏幕打字，整条动作描写没有代入感。
    expect(sanitizeChatText("（把手机重新举起来，耐心地对着镜头偏了偏头）")).toBe("");
    expect(sanitizeChatText(
      "（把手机重新举起来，耐心地对着镜头偏了偏头）\n嗯，看清楚了。",
    )).toBe("嗯，看清楚了。");
    expect(sanitizeChatText("（笑）想你了")).toBe("（笑）想你了");
    expect(sanitizeChatText("我明天(周六)有空")).toBe("我明天(周六)有空");
    expect(sanitizeChatText("（发来一张自拍）")).toBe("（发来一张自拍）");
    expect(sanitizeChatText("（无回复）")).toBe("（无回复）");
    // 只有动作、没有正文/媒体的流事件不生成空气泡。
    expect(chatRoomMessagesToChat([{
      id: "m1", speaker: "甲", text: "（把手机重新举起来，对着镜头偏了偏头）",
    }])).toEqual([]);
    // 旧角色消息的整行动作不进模型历史；用户原话保留。
    expect(buildChatRoomHistory([
      { id: "a1", role: "assistant", speaker: "甲", text: "（把手机重新举起来，对着镜头偏了偏头）" },
      { id: "a2", role: "assistant", speaker: "甲", text: "在。" },
      { id: "u1", role: "user", text: "（其实我不太舒服）" },
    ])).toEqual([
      { role: "assistant", content: "【甲】在。" },
      { role: "user", content: "（其实我不太舒服）" },
    ]);
  });

  it("自发媒体请求按线程记住已处理，刷新不重复拍照", () => {
    const store = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => { store.set(key, value); },
    });
    markHandledChatMedia("t1", "m1");
    expect(loadHandledChatMedia("t1").has("m1")).toBe(true);
    expect(loadHandledChatMedia("t2").has("m1")).toBe(false);
    vi.unstubAllGlobals();
    // 打开聊天时补拍快照里的 media_request（用户当时不在该私聊页）。
    expect(chatRoomView).toContain("loadHandledChatMedia(threadId)");
    expect(chatRoomView).toContain("sourceMessageId: message.id");
  });

  it("重新发送只按用户看得见的消息数确认", () => {
    expect(chatRoomView).toContain("const visibleAfter = current.slice(index + 1).filter(");
    expect(chatRoomView).toContain("重新发送将删除这条消息及其后的 ${visibleAfter} 条");
    expect(chatRoomView).not.toContain("const discarded = current.length - index;");
  });

  it("已读/送达圆点：角色右侧未读红、已读绿；用户左侧发送中红、送达绿", () => {
    expect(chatRoomView).toContain("chatroom-read-dot");
    expect(chatRoomView).toContain("assistantRead ? \"read\" : \"unread\"");
    expect(chatRoomView).toContain("userFailed ? \"unread\" : userDelivered ? \"read\" : \"unread\"");
    expect(chatRoomView).toContain("已送达");
    expect(chatRoomView).toContain("fetchChatReadState(threadId)");
    // 2026-11-11 用户实锤「明明在这个页面却是红点」：后台到达的消息被
    // document.hidden 跳过；回到页面/窗口聚焦/滚到底都必须补报一次。
    expect(chatRoomView).toContain('document.addEventListener("visibilitychange", onVisible)');
    expect(chatRoomView).toContain('window.addEventListener("focus", onVisible)');
    expect(chatRoomView).toContain("reportRead();");
    expect(chatRoomView).toContain("setReadUpTo((current) => Math.max(current, lastTs))");
  });
});
