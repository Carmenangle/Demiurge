import { createServer } from "node:http";

const repos = [
  { id: "library", name: "测试仓库", createdAt: 1 },
  { id: "work", parentId: "library", name: "恢复测试作品", createdAt: 2 },
];
const snapshot = [{
  id: "assistant-scene", role: "assistant", text: "高潮段落之后应当原位显示插画。",
  parts: [
    { type: "text", text: "高潮段落之后应当原位显示插画。" },
    { type: "media-slot", slotId: "slot-1", status: "pending", promptId: "mock-prompt" },
    { type: "text", text: "这是图片之后的收束段落。" },
    // V1.3 视频槽（promptId=mock-video-prompt → mock 返回视频产物）
    { type: "media-slot", slotId: "slot-video", status: "pending", promptId: "mock-video-prompt" },
  ],
}];
const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=", "base64");

function send(response, value, status = 200, contentType = "application/json") {
  response.writeHead(status, {
    "content-type": contentType,
    "access-control-allow-origin": "*",
    "access-control-allow-headers": "content-type",
    "access-control-allow-methods": "GET,POST,PUT,PATCH,DELETE,OPTIONS",
  });
  response.end(contentType === "application/json" ? JSON.stringify(value) : value);
}

createServer((request, response) => {
  if (request.method === "OPTIONS") return send(response, {});
  const url = new URL(request.url, "http://127.0.0.1:18110");
  if (url.pathname.includes("comfyui") || url.pathname.includes("finalize")) {
    console.log(`[mock] ${request.method} ${url.pathname}${url.search}`);
  }
  if (url.pathname === "/health") return send(response, { ok: true });
  if (url.pathname === "/mock.png") return send(response, png, 200, "image/png");
  if (url.pathname === "/mock.mp4") return send(response, Buffer.alloc(0), 200, "video/mp4");
  if (url.pathname === "/api/user-state" && request.method === "GET") return send(response, { repos, settings: null });
  if (url.pathname === "/api/user-state") return send(response, { ok: true });
  if (url.pathname === "/api/ai/chat/snapshot") return send(response, { items: snapshot });
  if (url.pathname === "/api/ai/chat/snapshot/save") return send(response, { ok: true, saved: true });
  if (url.pathname === "/api/ai/image-agent/running") return send(response, { running: false });
  if (url.pathname === "/api/ai/image-agent/running-threads") return send(response, { threads: [] });
  if (url.pathname === "/api/ai/chat-queue") return send(response, { tasks: [] });
  // V1.3：视频槽 mock——prompt_id=mock-video-prompt 时返回视频产物（无图）
  const isVideoPrompt = url.searchParams.get("prompt_id") === "mock-video-prompt";
  if (url.pathname === "/api/comfyui/result") return send(response, isVideoPrompt
    ? { status: "completed", error: "", texts: [], images: [],
        videos: [{ filename: "mock.mp4", subfolder: "", type: "output" }] }
    : { status: "completed", error: "", texts: [], videos: [],
        images: [{ filename: "final.png", subfolder: "", type: "output" }] });
  if (url.pathname === "/api/comfyui/finalize-generation") {
    // POST：prompt_id 在 body，需异步读取后再应答；target 的 slot 以请求为准（图片/视频槽各自原位回填）
    let raw = "";
    request.on("data", (chunk) => { raw += chunk; });
    request.on("end", () => {
      let body = {};
      try { body = JSON.parse(raw) || {}; } catch { /* ignore */ }
      const pid = String(body.prompt_id || "");
      const tgt = body.target && body.target.slot_id
        ? body.target
        : { message_id: "assistant-scene", slot_id: "slot-1" };
      const video = pid === "mock-video-prompt";
      return send(response, video
        ? {
            prompt_id: pid, durable: true, complete: true, messages: [],
            videos: [{ message_id: tgt.message_id, display_url: "http://127.0.0.1:18110/mock.mp4", persisted: true, snapshotted: true, errors: [] }],
            target: { message_id: tgt.message_id, slot_id: tgt.slot_id, media_type: "video", url: "http://127.0.0.1:18110/mock.mp4" },
          }
        : {
            prompt_id: pid, durable: true, complete: true, messages: [],
            images: [{ message_id: tgt.message_id, display_url: "http://127.0.0.1:18110/mock.png", persisted: true, indexed: true, snapshotted: true, errors: [] }],
            target: { message_id: tgt.message_id, slot_id: tgt.slot_id, media_type: "image", url: "http://127.0.0.1:18110/mock.png" },
          });
    });
    return;
  }
  if (url.pathname === "/api/agents") return send(response, []);
  if (url.pathname === "/api/workflows/templates") return send(response, { items: [] });
  if (url.pathname === "/api/generations") return send(response, { items: [] });
  return send(response, {});
}).listen(18110, "127.0.0.1");
