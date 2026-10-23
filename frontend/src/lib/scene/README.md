# scene 壁纸实时渲染

Wallpaper Engine 的 `scene.pkg` 在页面内按图层实时绘制，替代「把多层拼好的预览图当单张图」。
数据流与四个属主（详见 `docs/tech-manual/F-frontend/11-frontend-foundation.md` 的同名小节）：

```
GET /api/user-state/wallpapers/scene-pkg?source=…   ← 后端只交字节，不解析包
  → sceneWorker.ts         Worker 里解码（PKGV/LZ4/TEX → PNG，shims 顶掉 node:*）
  → sceneBackdrop.ts       装配、fit 映射、ready/fallback 状态机
  → scenePlayer.ts         同源 blob: iframe + 垫片（清单 fetch、鼠标视差还原）
  → vendor/                上游 MIT 播放器（逐字不改，来源与 SHA-256 见 NOTICE.md）
```

## 来源标识

`attachSceneBackdrop({ source })` 只透传，不拆解前缀；后端 `scene_package(source)` 是唯一解析方。

| 标识 | 含义 | 判据 |
| --- | --- | --- |
| `workshop:<id>` / 纯数字 | 创意工坊作品 | `<工坊根>/<id>/scene.pkg` 存在 |
| `local:<绝对路径>` | 本地文件夹里的 WE 工程 | 目录里**同时**有 `project.json` 与 `scene.pkg` |

壁纸条目存 `sceneSource`；只有旧 `workshopId` 的条目按 `workshop:<id>` 补出，升级后不会失去实时渲染。

## 鼠标视差

背景层**保持 `pointer-events:none`**（放开会让 iframe 吞掉宿主的滚轮、拖拽与点选）。
宿主监听 `mousemove` → rAF 节流 → `postMessage({type:"dsh-pointer", x, y})`（归一化坐标）
→ 垫片用 `new MouseEvent("mousemove", {clientX: x*innerWidth, …})` 还原。`pointer:false` 可关。

## 明确不做

- **puppet 骨骼绑定**与**完整脚本引擎**：上游播放器没有这两项能力。本机 122 个 scene 作品里
  30 个 `scripted:true`、0 个含 puppet ⇒ 不是本层补得上的，别排进待办。

## 待决

- **绘制搬进 Worker + OffscreenCanvas**：前提不成立——播放器是自包含 WebGL1 页面
  （单 IIFE、`<canvas id="canvas">`、零 `OffscreenCanvas`），把绘制搬走等于把整个播放器搬进
  Worker（自备 `document`/`Image`/`rAF` 垫片）或重写渲染，不是「解码已在 Worker、只差绘制」。
  现状压开销只有「不可见即暂停」。
- **首屏进度与流式解码**：`decodeSceneInWorker` 目前一次性回结果，没有阶段回调；大包（中位 13.3 MiB、
  最大 132 MiB）期间界面只有 loading，没有百分比。
- **列表封面换实时缩略图**：每份缩略图要一个 WebGL 上下文，列表几十条会撞浏览器上限（超出后旧上下文
  被丢弃、画面变黑）⇒ 必须先定预算（同时存在的实时上下文数、离屏可见才建、滚出即释放），
  否则就会退回到今天的 preview.gif。这就是它现在停在静态封面的原因。
