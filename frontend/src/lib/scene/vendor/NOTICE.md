# Vendored: Wallpaper Engine scene decoder + WebGL live player

Demiurge 的 scene 壁纸实时渲染用的两个文件是从第三方 MIT 插件里**拷进仓库**的，
不是我们自己写的。这里记录来源、改动清单与再同步方法，`vendor/LICENSE` 是随附的许可文本。

## 来源

| 项 | 值 |
| --- | --- |
| 上游包 | `dsh-plugin-wallpaper-engine` v0.6.8 |
| 仓库 | https://github.com/elysia395/dsh-wallpaper-engine |
| 许可 | `package.json` 声明 `"license": "MIT"`（上游**没有**附带 LICENSE 文件，见下） |
| 取用时间 | 2026-10-23，取自本机已安装的 `D:\tool\DSHEAC AIO\dsh-home\profiles\web-desktop\node_modules\dsh-plugin-wallpaper-engine\lib\` |
| 拷贝日期 | 2026-10-23 |

上游文件 SHA-256（原文，未改动前）：

- `lib/scene-manifest.js` — `50211bacbf460c4ca1e9fac64bf8c14c64ce73475fb68ceb882e42163c6c9a8b`
- `lib/scene-player.js` — `df8d4eb0f6c6f489a16a46683591a8968f01fef7140b96c6bbff2edf8a057a36`

本仓库内拷贝的 SHA-256（改动后）：

- `we-scene-manifest.js` — `322332c92c5483436d587f89ea95e24ba902d8b08c9081ec8e4160014df73a56`
- `we-scene-player-html.js` — `a66b65a426c6759d175bdbd51957b8d3bdf6572f2b7f5fbe00d17b815f16017b`

## 改动清单（全部改动，逐字）

### `we-scene-manifest.js`（上游 `lib/scene-manifest.js`）

1. 文件头 docstring 追加 6 行 vendored 说明注释（无代码影响）。
2. 4 行 import 的说明符从 `node:*` 改指向 `../shims/*`；`jpeg-js` 那行不变：

```diff
-import { Buffer } from 'node:buffer';
-import { lstatSync, readdirSync, readFileSync, realpathSync, statSync } from 'node:fs';
+import { Buffer } from '../shims/nodeBuffer';
+import { lstatSync, readdirSync, readFileSync, realpathSync, statSync } from '../shims/nodeFs';
 import { decode as decodeJpeg } from 'jpeg-js';
-import { join as joinPath, resolve as resolvePath, sep } from 'node:path';
-import { deflateSync, inflateSync } from 'node:zlib';
+import { join as joinPath, resolve as resolvePath, sep } from '../shims/nodePath';
+import { deflateSync, inflateSync } from '../shims/nodeZlib';
```

除此之外**没有任何**逻辑改动：PKGV 解析、LZ4 block chain、TEX/DXT 解码、
`buildSceneManifest`、`extractSceneResource`、分配上限护栏全部原样。

### `we-scene-player-html.js`（上游 `lib/scene-player.js`）

只有文件头注释追加 7 行；`WE_SCENE_PLAYER_HTML` 模板字符串**逐字未改**。
Demiurge 不编辑播放器页面本体，而是在装配播放器文档时于其 `<script>` 之前**注入一段垫片**
（把播放器对 `/wallpaper-engine/scene-manifest/...` 的 fetch 指向 blob URL），
注入代码在本目录之外的 `../scenePlayer.ts`，因此再次同步上游时这两个文件可以直接整份替换。

播放器文档用 `blob:` URL 装载而**不是 `srcdoc`**：`about:srcdoc` 文档里
`window.location.origin` 读到的是字符串 `'null'`，而上游播放器两头都拿它做校验
（入站 `ev.origin !== window.location.origin` 丢弃、出站 `postMessage(msg, window.location.origin)`），
结果 fit / pause 全部失效且 `dsh-scene-unavailable` 也回不来。`blob:` 文档继承创建者源，
两头都是真实站点源（2026-10-23 真机实测：`iframeOrigin='null'` + rAF 51 帧/600ms 但画面不重绘）。

## 为什么必须有垫片

便携包的运行时里**没有 Node**（`runtime-portable-release` 只带 Python Runtime），
所以解码必须在 Web Worker 里跑；`node:buffer` / `node:zlib` / `node:path` / `node:fs`
由 `../shims/` 提供：

- `nodeBuffer.ts` — `buffer` ponyfill。上游依赖 `Buffer` 的 `slice`/`concat`/`writeUInt32BE`/
  `toString('base64')`，所以跨边界的字节一律包成 Buffer，不能只给 `Uint8Array`。
- `nodeZlib.ts` — `fflate`。PNG IDAT 是 zlib 封装，故用 `unzlibSync`/`zlibSync` 而非裸 deflate；
  Node 的 `maxOutputLength`（zip-bomb 上限）在 fflate 的 `{out}` 上会**静默截断**（实测：给 10 字节的
  `out` 解 1000 字节数据返回长度就是 10），所以改用流式 `Unzlib` 逐块累加、越界即抛。
- `nodePath.ts` — `join`/`resolve`/`sep` 的 POSIX 语义最小实现。
- `nodeFs.ts` — 一律抛错。上游只有「散装目录场景」分支（`*FromDir`）碰 fs，
  Demiurge 只走 `scene.pkg` 内存分支（`pkgSceneAccess`），真碰到 fs 说明入口传错，不该降级。

`lib/pkg-extract.js`、`lib/scene-scripts.js`、`lib/index.js`、`lib/client.js` **没有拷进来**：
前者被 `scene-manifest.js` 自包含取代，后三者是 Node 宿主 glue；上游的
`@shaderfrog/glsl-parser` 依赖只被 glue 用到，实时渲染链不需要。

## 许可文本的诚实说明

上游 npm 包与仓库都没有 LICENSE 文件，只有 `package.json` 的 `"license": "MIT"` 与
`scene-player.js` 自带的 `@license MIT` 头。因此 `LICENSE` 里的 SPDX MIT 正文按标准模板
逐字复现，版权行写作「the dsh-wallpaper-engine authors」——即按仓库归属标注，
**没有**编造具体自然人姓名。若日后上游补出正式 LICENSE，应以其为准替换本文件并更新此段。

## 已知能力边界（不是我们改坏的，是上游播放器本身的范围）

- 只实现 `genericimage2` 等常用 shader 与 particle / 2D 图层；**没有** puppet 骨骼绑定
  （部件跟随动画）与完整脚本引擎：清单里 `scripted: true` 的场景会渲染但脚本驱动的行为不跑。
  本机 122 个 scene 作品里 30 个是 scripted，0 个含 `"puppet"`。
- 鼠标视差需要鼠标事件进入播放器文档；壁纸层是 `pointer-events: none`，所以视差不跟手。
- 同源 iframe 仍占用渲染主线程；真正的工作线程隔离要改 OffscreenCanvas，属后续项。
