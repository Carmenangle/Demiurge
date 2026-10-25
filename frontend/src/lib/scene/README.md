# scene 壁纸实时渲染

Wallpaper Engine 的 `scene.pkg` 在页面内按图层实时绘制，替代「把多层拼好的预览图当单张图」。
数据流与属主（详见 `docs/tech-manual/F-frontend/11-frontend-foundation.md` 的同名小节）：

```
GET /api/user-state/wallpapers/scene-pkg?source=…        ← 后端只交字节，不解析包
GET /api/user-state/wallpapers/scene-properties?source=… ← 后端只交属性表 + WE 生效值
  → sceneWorker.ts         Worker 里解码（PKGV/LZ4/TEX → PNG，shims 顶掉 node:*）
  → sceneBackdrop.ts       装配、fit 映射、ready/fallback 状态机、预设热重建
  → scenePlayer.ts         同源 blob: iframe + 垫片（清单 fetch、鼠标视差、暂停=定格）
  → scenePresets.ts        预设快照缓存 + 面板控件的纯换算（颜色刻度/条件显示/差集）
  → sceneBindings.ts       「这份清单实际读了哪些属性」的共享登记处
  → vendor/                上游 MIT 播放器（改动逐条记在 NOTICE.md，来源与 SHA-256 同）
```

## puppet 角色拼接与动画

WE puppet 的 atlas 是「拆散部件图」：身体由 MDLV 网格索引 atlas 区域（不是整张 quad），
头发/服饰是挂在骨骼（MDAT socket）上的独立对象。`we-scene-manifest.js` 在对象 model JSON
含 `puppet` 时改为发射 `mesh` 图层（顶点/法线/UV/索引 + 蒙皮索引/权重 base64、`meshScale`），
并把 `manifest.puppets[id]` 填成骨骼载荷（父子链、本地矩阵、inverseBind、MDLA 关键帧轨道）。
`we-scene-player-html.js` 用 `drawElements` 在 FBO 与主画面两个 pass 画网格，并在 CPU 侧做
**线性混合蒙皮**（顶点 × `world × inverseBind`，权重 4 骨骼）后上传到 `DYNAMIC_DRAW` 缓冲。

动画按 WE 的 `animationlayers` 语义展开：第一条非 additive 层是基础姿势，additive 循环层
（眨眼/呼吸/重心）叠加其上；**非循环的单发 clip 是点击触发的，不自动播**。additive 且非循环、
或 `visible` 为假的层跳过播放，但仍收集其绑定键（用户随时可能把开关打开）。
挂在骨骼上的附件（头发）按「最近带 `attachment` 的祖先」求**刚性子树增量**：绑定 socket
与实际 socket 给出 `x' = R(Δθ)(x − tB) + tA`，播放器让整个子树绕绑定 socket 的屏幕点旋转、
再按 `S(tA − tB)` 平移——缺了它，身体在呼吸而头发钉在原地。

动画跑在**播放器自己的 `animTime` 时钟**上（与 `u_time` 那条墙钟分开），所以暂停即定格姿势。
e2e 的像素对照就靠这条：`wallpaperPaused:true` 一挂，待机动画与着色器相位同时冻住。
不冻的话这张作品常驻的呼吸/云层漂移会让**同一状态连拍两张差到 55%**，任何「换预设前后画面变了」
的判据都被淹掉（实测）。`__weDebugAnim` / `__weDebugTime` 是留给控制台手工对照的调试挂钩，
自动化用例目前没用它们。

边界：蒙皮权重固定 4 骨骼；附件跟随只走「最近祖先」的刚性子树，多级 puppet 嵌套未实现；
文字（时钟/日期）与 sprite 粒子已由第五刀接入（NOTICE 改动⑦⑧），日期脚本三族写法与字号/行距
由后续一刀补齐（改动⑪⑫，见「文字与日期」）；仍未覆盖的只剩任意 `update()` 脚本、
`rope`/`ropetrail` 粒子与音频感应（见「明确不做」）。

## WE 预设部署

用户实锤的需求：右侧栏选壁纸时摆出的选项，要是这张作品在 Wallpaper Engine 里的**真选项**，
且改了立刻反映到背景上。

- **后端 `scene_properties(source)`** 读 `project.json` 的 `general.properties`，只留
  `slider/bool/combo/color` 四类，剔掉广告（`_AD_KEY_RE`/`_AD_TEXT_RE`：键名过长、含
  `url|link|donate|patreon…`，或 `text` 里有 `<img>`/`<a>`/`http`）与 `schemecolor`（那是
  WE 浏览器自己的 UI 主题色，不进场景），再把用户在 WE 里的覆盖值（
  `<WE>/config.json` 的 `wproperties[<scene.pkg>][Monitor0]`）合进 `value`，并标 `we_overridden`。
  **后端不判「哪条会生效」**——那由渲染清单决定，否则每加一个渲染特性都要改后端。
- **清单的 `userBindings` 才是诚实闸门**：只有真的落到某个渲染层属性上的键才登记
  （`noteBinding`，动画层/附件跟随/origin 绑定都算）。面板按它收窄控件：摆一个改了没反应的
  假控件，比少摆一个真控件更坏。第四刀实锤这张作品的 52 条非广告属性里只有 6 条被消费；
  第五刀接入文字/粒子/层效果后是 **41 条消费 / 面板 37 行**（口径见文末「待决」）。
- **改了不重解包**：预设变化只触发 `dsh-set-manifest` 热重建清单（大包解码实测 16 秒），
  纹理工件复用。持久化只存**相对 WE 生效值的差集**（`scenePresetDeviation`），改回原样即删键
  ——全量快照会把用户日后在 WE 侧的调整永久钉死。
- **首帧刻意先用工程烘焙值**（不等 `scene-properties` 那次 HTTP），随后热重建换成 WE 生效值。
  于是存在一个「面板已是 WE 值、画布还是烘焙画面」的窗口期，`ready` 状态里的
  `presetApplied` 把它变成可观测事实，宿主写进 `.app-backdrop-scene-host` 的
  `data-scene-preset="pending|applied"`；e2e 必须先等 `applied` 再截图。

## 暂停 = 定格

`dsh-set-pause` 在垫片里是**真停帧**（上游只冻结动画时钟、仍每帧全屏重绘）。三条规则缺一不可：

1. **settle 判据用真实 draw call + 贴图上传数**。上游 `renderFrame` 在清单到位前直接 return
   （连 clear 都不发），未解码完的层被逐层跳过，所以开局即暂停时只补一帧会定格在空白画面上
   （真机截图整屏全白）。判据：连续两帧绘制次数相同**且不为 0**，且这一帧窗口内**没有新的
   `texImage2D` 上传**才允许停帧；等不到稳定就按 `WARM_FRAME_CAP=120` 兜底先定格。
2. **贴图在定格之后才解码完 ⇒ 立刻补一帧**。`loadTexture` 先塞 1×1 占位、`img.onload` 里才
   真正上传，而 `onload` 不走 rAF——换清单后的头几帧可能「图层数已稳定、暗色天空还没传上来」，
   于是定格在半张画面上（真机实锤：部署夜晚预设后先定在白天那一半）。
3. **暂停期间钉死交给播放器的时间戳**（`frozenNow`）。上游用 `now` 反推 `elapsed` 喂 `u_time`，
   而云层/水面/霓虹这类着色器动画不走 `animTime`，`dsh-set-pause` 管不住它们：不钉住的话，
   暂停中每补一帧（换预设、换填充方式、合成器补画）都会把动画相位往前跳一格。钉住后补帧幂等。

补帧通道：宿主在 resize / focus / 恢复可见时调 `ScenePlayerHandle.recover()` →
`dsh-recover-renderer`。垫片拦截该消息，只在 GL 上下文**真的**丢失时才放行给上游重建，
否则自己补一帧（`stopImmediatePropagation`）——否则「为补帧发的消息」会被上游读成「渲染器坏了」。

## 视频纹理（MP4-backed `.tex`）

WE 允许把**视频**当图层贴图：`.tex` 的 TEXB 头带 v4 视频标记（`isVideoMp4`，紧邻的
`freeImageFormat` 必须为 -1），载荷是内嵌 H.264 MP4。工坊 `3723257973`（Blue Archive 生盐诺亚「落花吟」）的唯一美术层就是这种
`materials/art.tex`——原始 TEX 67,326,945 B，MP4 从**字节 91** 起、长 67,326,854 B，与实际
`art.mp4` 逐字节相同（容器长度字段不可信，`sliceMp4Payload` 在前 4KB 里找 `ftyp`）。

链路四段，各归各的属主：

1. `vendor/we-scene-manifest.js`：`parseTexInternal` 认视频（v4 标记或首 8 字节 `ftyp`）后，
   `extractSceneResourceVia` 返回**裸 MP4**，不再走静态帧解码（`decodeTex` 对视频显式抛错）。
2. `decodeScenePackage.ts`：`isMp4Bytes` 反查资源表，`markSceneVideoLayers` 给引用该资源的层标
   `videoTex: true`。**manifest 不猜语义，标注只在产品侧做**。
3. `sceneBackdrop.ts`：blob MIME **按字节嗅探**（`mimeOf(path, bytes)` → `video/mp4`）——
   清单里的后缀仍叫 `.tex`，按后缀猜会得到 `image/png`、`<img>` 失败、整层被 `!loaded` 静默跳过。
4. `vendor/we-scene-player-html.js`：`loadTexture(url, repeat, video)` 创建
   `<video muted loop playsInline>`（append 到 body、不可见）；**逐帧重采由渲染循环做**——
   `renderFrame` 每帧调 `pumpVideoTextures(now)`，凡 `currentTime` 变了就 `texImage2D(el)` 重传。
   `requestVideoFrameCallback` 只是「早一帧拿到」的加速通道，**不能当唯一通道**（见下）。
   缓存键带 `|video` 后缀，`pruneTextures` 据此识别并拆掉 video（`pause` → 清 `src` → `load()` → 移除节点），
   `dsh-set-pause` 也遍历缓存真正 `pause()`。

🔴 **rVFC 不能当纹理上传的唯一通道**（2026-10-25 真机 WebView2 实锤「重载后壁纸不动」）：rVFC 只在
解码帧**真被合成器呈现**时回调，而这里的 `<video>` 是 1px / `opacity:0` 的纯纹理源，WebView2 不呈现它
⇒ 连续 12 秒 rVFC **0 次回调、视频源 `texImage2D` 0 次**，同期 `currentTime` 1:1 推进、`readyState=4`、
`totalVideoFrames` 每秒 +30（`droppedVideoFrames` 同步涨）⇒ 纹理钉在最后一次 `loadeddata` 的帧上，
而文字层照常走 = 用户看到的「艺术字还在、壁纸不动了」。同一个 `pumpVideoTextures` 还顺带按
**≤1 次/秒**重试此前被 `catch {}` 吞掉的 `play()` 拒绝；`videoTextures` 为空时直接 return，暂停零成本。

🔴 两条硬约束：**NPOT 先 `CLAMP_TO_EDGE`**（3840×2160；WebGL1 的 NPOT + REPEAT 会成为
incomplete 纹理、取样恒 `(0,0,0,1)`，画面直接画黑）；**暂停要真停解码**，不是只冻时钟。
边界：只认 H.264/MP4、按 CLAMP 采样（不重复平铺）、MP4 无 alpha；每帧上传有解码+拷贝成本，
这是「视频当纹理」的固有代价。真机探针 `_tmp/probe-3723-video.mjs`：截图与 WE 参考同内容、
`currentTime` 推进 / 暂停钉住 / 恢复推进、`glError=0`；重载与「刷新壁纸/加载中滚动」两条网
见 `e2e-manual/wallpaper-scene-reload.spec.ts`（判据是**源是 `<video>` 的上传次数**跟得上渲染帧数，
不是 draw call——draw call 照常而纹理不换帧正是这次缺陷的形态）。

## 文字与日期

WE 的文字对象（`font` + `pointsize`）在清单里发射成 `kind:"text"` 图层。字形不进 GPU 网格：
播放器用 2D canvas **2× 超采样**栅成位图（白色字，颜色交给 `u_tint`，同一句话日夜两套颜色复用
一张位图），上传后仍走 `progBasic`，视差、旋转、混合与图像层共用一条路径。位图按
「字体家族 + 字号 + 整句」缓存（上限 48，逐旧淘汰）；字体是异步的（`FontFace` + blob），
未就位先用系统字栈出字，`ready`/`fallback` 后整张缓存作废重建，不留旧字形。

- **时钟**：`{time:'clock', hour24, seconds, delimiter}`，来自作者脚本里
  `getHours/getMinutes` 的固定惯用法，档位取 `scriptproperties`（默认真、假、`:`）。
- **日期**：不执行 WE 脚本，`textSpecOf` 对脚本做静态求值，产出
  `{time:'date', format, months?, days?, dayTable?, yearSplit?}`。`format` 是带
  `{month}|{weekday}|{dayTable}|{day}|{year}` 占位符的模板（可含真实换行），月名/星期/补零日号/
  年份拆分都用作者工程里的原文。已覆盖三族真实写法：横排月名 + 分隔符、竖排空行 + 逐字年份、
  只输出星期名（NOTICE 改动⑪）；认不出的脚本返回 null ⇒ 该层不发射，不猜格式。
- **字号 = `pointsize × 对象自身与中间父组 scale`**（镜像取绝对值），最外层「组件」根组的 scale
  只经 transform 折叠挪位置，不乘进字形（NOTICE 改动⑫）。只吃自身 scale 会把问候语压成 7.37
  的小糊点；把根组 0.28 也乘进来则整组文字小到看不见。
- **问候语**：`{time:'greeting', labels, thresholds}`，静态抠出作者的小时阈值表（≥2 张全字符串
  标签表 + `switch` 映射 + 递增阈值），播放器按当前小时取词（10:46 → "Morning"、12:34 → "Noon"）。
- **帧率**：`{time:'fps', label, refreshMs, text}`，`active=false` 整层缺席；播放器按 rAF 实测帧间隔
  求平均，按刷新窗口出 `label + 整数`，暂停时冻住。浏览器真实帧率不会等于 WE 自己的统计值。
- **行距吃字体度量**：`fontBoundingBoxAscent + Descent`（量不到才回退 1.25em）。参考图里
  Quicksand 日期列 ≈1.17em、spincycle 竖排 SUN ≈1.22em；固定 1.25 会把 SUN 撑高并压到时钟。
- **暂停＝定格**：日期与时钟都吃 `wallClock`，`dsh-set-pause` 后不再推进，恢复即跟回真实时间。
- 字体来源与图像纹理同一条通道：包内字体解成 `scene-resource/` blob；WE 安装目录的内置字体
  （如 `fonts/spincycle_3d_ot.otf`）走 `we-asset/` 命名空间 + 后端只读路由。取不到就退系统字栈，
  不带倒整幅画面。**生产页 CSP 的 `font-src` 必须含 `blob:`**（属主 `backend/app/main.py`
  `_DOCUMENT_CSP`）：三条 FontFace 全走 blob，缺了会在生产态静默退系统字（3D `SUN` 变实心、
  行距变短压住时钟）；dev vite 不发 CSP，e2e 看不见这层，回归在 `backend/tests/test_csp_header.py`。
- 真机样张 `3723257973`（NOTICE 改动⑪/⑫/⑬）：日期列 `2 5 / O C T / 2 0 2 6`、竖排 `S U N`、
  右下时钟、左上问候语与帧率，字号 25.2 / 82.155 / 56.71 / 23.025 / 22.752；`e2e-manual/wallpaper-scene-text-layout.spec.ts`
  同时钉住清单模板、坐标、逐行光栅与字体加载。

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

层内视差与镜头视差都由作品自己的 `general.cameraparallax` 门控：清单写
`cameraParallax: general.cameraparallax !== false`，播放器 `cameraParallaxEnabled()` 为假时
`layerOrigin()` 原样返回 `layer.x/y`、相机视差项也跳过。用户实锤的就是这条——壁纸在 WE 里
关了镜头位移，应用内却跟着鼠标动。

## 明确不做

- **完整脚本引擎**：`scripted: true` 的场景渲染静态结构与 `animationlayers`，但脚本驱动的
  行为（点击切姿势、`scriptproperties` 的动态求值）不跑。
- **`rope`/`ropetrail` 粒子、音频感应**：绳/尾迹类粒子不模拟（`hrbigbmousetrail`/`shu` 一族的键
  因此不进面板）；音频感应（`_01`/`tiao`/`newproperty18`/`newproperty45`）需要系统音频环回——
  `getDisplayMedia({audio:true})` 每次要用户授权且 WebView2 壳内可用性未验，接不接待用户定案。
  文字（时钟/日期/问候语/帧率四族固定形状）与 sprite 粒子**已接入**（NOTICE 改动⑦⑧⑪；真机
  `3346715292` 的时钟/日期/鸟群/雾已出，`3723257973` 的竖排日期/星期、问候语与帧率已按原布局出）；
  **作者其余任意 `update()` 脚本仍不执行、对应图层不出字**；日/夜两侧个别 solidlayer 覆盖与
  `hrbigeffects` 一族的键仍未接。

## 待决

- **属性覆盖面的诚实口径**：面板只显示 `userBindings`，所以「渲染器还没实现的键」对用户是
  不可见的。当前实测：`3346715292` 的 78 条 `project.json` 属性里渲染器真消费 **41** 条、
  后端剔广告/`schemecolor` 后交 **52** 条、面板实际 **37 行**（combo 5 / bool 11 / slider 21）；
  精确条数以 `scene-properties` + 清单 `userBindings` 实测为准。要继续做的是把飘动
  （`waterwaves`/`foliagesway`）、相机几何、音频感应、鼠标尾迹接进渲染器，而不是放宽面板。
- **绘制搬进 Worker + OffscreenCanvas**：前提不成立——播放器是自包含 WebGL1 页面
  （单 IIFE、`<canvas id="canvas">`、零 `OffscreenCanvas`），把绘制搬走等于把整个播放器搬进
  Worker（自备 `document`/`Image`/`rAF` 垫片）或重写渲染，不是「解码已在 Worker、只差绘制」。
  现状压开销只有「不可见即暂停」与上面的「暂停=定格」。
- **首屏进度与流式解码**：`decodeSceneInWorker` 目前一次性回结果，没有阶段回调；大包（中位 13.3 MiB、
  最大 132 MiB）期间界面只有 loading，没有百分比。
- **列表封面换实时缩略图**：每份缩略图要一个 WebGL 上下文，列表几十条会撞浏览器上限（超出后旧上下文
  被丢弃、画面变黑）⇒ 必须先定预算（同时存在的实时上下文数、离屏可见才建、滚出即释放），
  否则就会退回到今天的 preview.gif。这就是它现在停在静态封面的原因。
