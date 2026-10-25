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

本仓库内拷贝的 SHA-256（当前改动后，含 Demiurge 视差 / puppet 蒙皮动画 / 用户预设 /
文字与时钟 / 日期脚本模板 / 问候语与帧率脚本 / 字号自身+中间父组 scale / 2D 粒子 / 云层流动 / 视频纹理 补丁）：

- `we-scene-manifest.js` — `b76f3f7ea2389b6adccfa00e886e08c8085f91fc6d934ee49e6a9e2c388a653e`
- `we-scene-player-html.js` — `0293083295d419672cca0caa07d7addd0b7b25344214b1da1e5c122f304de977`

## 改动清单（全部改动，逐字）

### `we-scene-manifest.js`（上游 `lib/scene-manifest.js`）

1. 文件头 docstring 的 vendored 说明改为指向本 NOTICE（无代码影响）。
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

3. **Demiurge 改动①（用户批准的例外）：保留层视差深度。** 在 `buildSceneManifestVia`
   的标准图层 push 里（锚点：含 `isGround` / `sway` / `swaySpeed: 1.5` 的那个对象）追加：

```diff
             isGround,
             sway: 0,
             swaySpeed: 1.5,
+            depth: parseVec2(obj.parallaxDepth, [0, 0]),
         });
```

   同处新增 `parseVec2` 辅助函数（紧跟 `parseVec3`）。注意真实 WE 数据里
   `parallaxDepth` 是**两分量字符串**（如 `"1.000 1.000"`，x/y 分轴），
   不是数字；`parseVec3` 要求 ≥3 分量因此不能复用。上游整条丢弃此字段，
   导致多部件场景各层的相对位移恒为 0，前景与背景叠死。

4. **Demiurge 改动③：puppet 网格与骨骼附件。** 上游只把对象当整张 quad 画，
   而 puppet 角色的 atlas 是「拆散的部件图」+ MDLV 网格索引 ⇒ 整张 quad 会把部件画散
   （真机 `3346715292` 实锤）。新增内容：

   - `parseMdlRig(buf)`（export，供单测）：解析 MDLS0003 骨骼（父索引 + 列主序 4×4
     本地矩阵）与 MDAT0001 附件（`u16 boneIndex` + UTF-8 NUL 名 + 64 字节 socket 矩阵），
     骨骼 world 逐级 `parentWorld × local`，附件 socket = `boneWorld × attachmentMatrix`。
   - `readMdlCString` 从逐字节 `fromCharCode` 改为模块级 UTF-8 `textDecoder`：
     真实包里的材质路径与附件名是中文（`头`），按 Latin-1 读会查不到附件。
   - `parseVec3` 先解包 `{ value: "x y z" }`：脚本/用户绑定的 origin 保留最后解析值
     在该字段里，当成「缺失」会让整组后代（含 puppet 组）错位。
   - `isObjectVisible(obj)`（`buildSceneManifestVia` 内）：own + 祖先链的
     `visible === false` / `visible.value === false` 才算隐藏；`{user:{...},value}`
     按 `value` 判（不执行脚本条件）。用于替代原来的单层判断。
   - `puppetRigOf(obj)`: 对象 `image` 指向的 model JSON 里若有 `puppet` 文件，
     解析一次并缓存（name → attachment）。
   - `resolveObjectTransform`：折叠到父级时若子对象有 `attachment`，把 local origin
     用父对象 puppet rig 的 socket 矩阵变换（origin 折叠只看直接父级；子树随骨骼摆动是
     下面的改动⑤ `followOf`）。
   - 图层发射：model JSON 含 `puppet` 时不再 push 整张 quad，而是解析 MDLV，按
     mesh 各自的 material pass 取纹理，push `mesh:{posB64,normB64,uvB64,indicesB64,
     iCount,idx32}` + `meshScale:[scaleX,scaleY]`；`x/y` 用对象世界原点（网格顶点是
     puppet 本地单位，不再按 quad 宽高摆放）。

5. **Demiurge 改动⑤：puppet 蒙皮数据、动画层语义、附件跟随与视差开关。**（2026-10-24，
   真机 `3346715292` 修「人物不动、头发脱节、鼠标一动背景跟着走」）

   - `parseMdlVertices` 按 mesh flag 取 `MDL_FLAG_SKIN_BLEND(0x00800000)` /
     `MDL_FLAG_SKIN_WEIGHT(0x01000000)`：命中则额外读每顶点的 4 骨骼索引（u8×4）与 4 权重
     （f32×4），图层里发射 `skinIndicesB64` / `skinWeightsB64`。
   - `buildPuppetPayload(rig, id)` → `manifest.puppets[puppetId]`：`boneCount`、每骨骼
     本地 4×4（9 分量压缩存 `local`）、`parents`、`inverseBind`，以及 MDLA 剪辑表
     （`clips[名称] = {loop, fps, tracks:[{bone, keysB64}]}`）。
   - `animationOf(obj, payload)` 解 `scene.animationlayers`：**第一条非 additive 层是基础姿势，
     additive 循环层叠加其上；非循环单发 clip 是点击触发的，不自动播**（`additive && !loop`
     同样跳过）。每条形如 `{id, additive, offset, rate, blend}`，`offset` 取
     `visible.scriptproperties.percentage`。
   - `followOf(obj)` 求附件（头发/挂饰）的**刚性子树增量**：向上找最近的带 `attachment` 的祖先
     （深度上限 32），把绑定 socket 与实际 socket 的差写成 `{puppetId, animation, bone,
     local, bindSocket, bindAngle, screenOrigin, screenMap}`，交给播放器做
     `x' = R(Δθ)(x − tB) + tA`。缺它则身体呼吸、头发钉在原地。
   - 清单顶层 `cameraParallax: general.cameraparallax !== false`（作品自己关掉镜头位移时，
     应用内必须跟着关掉——这是用户实锤的第一条）。
   - 绑定键的收集贯穿以上三处：`collectBindings` / `noteBinding` 保证「隐藏的动画层」也登记
     其用户属性（用户随时可能把开关打开）。

6. **Demiurge 改动⑥：WE 用户预设。** `buildSceneManifestVia(access, token, userProperties)`
   多收一个「用户在 Wallpaper Engine 里的生效值」表；对象属性节点上的 `{user:{name}}` 绑定
   经 `bindingValueOf` 取用户值、缺失时回退烘焙值。凡真的落到某个渲染层属性上的键都进
   `usedBindings`，清单非空时写成 `manifest.userBindings`（排序去重）。
   **这个字段是前端选项面板的诚实闸门**：工程里留着但场景不消费的键（各种开关、广告、UI 色）
   不进清单，也就不进面板。

7. **Demiurge 改动⑦：WE 文字对象（时钟 / 日期 / 自定义文字）发射为文字图层。**（2026-10-24，
   真机 `3346715292` 的「时间组件显示 / 日期组件显示」）上游只画 `image`，`font` + `pointsize`
   一类对象整条被 `!obj.image ⇒ continue` 丢掉。新增：

   - `textSpecOf(node)`：`obj.text` 折成 `{text}`（字符串或标量绑定值）或时间规格。WE 的时钟
     不是格式串而是**一段固定惯用法的 JS**：`getHours`+`getMinutes` ⇒ `{time:'clock', hour24,
     seconds, delimiter}`（`hour24` 默认真、`seconds` 默认假，均取自 `scriptproperties`）；
     `getFullYear`+`getMonth`+`getDate` ⇒ 日期规格。日期规格在 2026-10-25 改为**模板化提取**
     （`{time:'date', format, months?, days?, dayTable?, yearSplit?}`，见第 12 项）：
     月名 / 星期名 / 补零日号 / 年份拆分都用作者工程里的原文，我们不自带翻译。
     **认不出的脚本返回 null ⇒ 不出文字层**（宁可少画，不猜内容）。
   - `canvasOriginValue(node)` + `CANVAS_ORIGIN_X/Y`：WE 位置脚本的惯用法
     `value.x = scriptProperties.x * engine.canvasSize.x`。baked origin 只是编辑器快照，
     换分辨率或改用户百分比后**必须按画布尺寸重算**，否则文字钉在旧位置。
   - `textLayerOf(obj)`：出 `kind:'text'` 图层（字号 = `pointsize × 对象自身与中间父组的 scale`，
     最外层组件根组只挪位置、不缩放字形，见第 12 项；颜色 `parseVec3(obj.color)`，`depth` 与图像层
     同走 `depthOf`，字体包内优先、否则走内置资产通道）。
     **发射点排在 `!obj.image` 分支与图像名黑名单之前**，所以 `day7` / `Clock` 这类名字
     不会被 `black|len|util` 误杀。
   - `WE_BUILT_IN_PREFIX = 'we-asset/'` + `manifest.builtInAssetKeys[相对路径] = [生效键…]`：
     WE 安装目录自带的字体 / 粒子贴图不在包里，改走这条资源命名空间，由后端只读路由
     `/api/user-state/wallpapers/we-asset` 供字节；登记「被哪些用户属性门控」是为了取不到字节时
     面板能如实收窄。
   - 解码侧同批补两种真实贴图格式：`RG88`（R 作灰度颜色、G 作透明度——容器里这类格式没有 alpha）
     与 `TEXS0001..3` 帧表（官方雾贴图是一张 8×8 序列页，帧表原样带出去给主线程）。
   - 绑定纪律与图层一致：隐藏的文字对象只交出「此刻为什么看不见」那个键，字号 / 透明度这些
     细节键等到真出图层时才 `commitStagedBindings()`。

8. **Demiurge 改动⑧：WE 粒子系统发射为 2D 精灵。**（2026-10-24，同作品的雾 / 光束 / 鸟群）
   发射循环排在 `is3D` 分支**之外**——纯 2D 壁纸也要有雾与光束，藏在 3D 遍历里它们永远不出现。

   - **只模拟渲染器为 `sprite` 的系统**；`rope` / `ropetrail`（鼠标尾迹一族）要逐帧连线 +
     `eventfollow` 子系统，本实现不跑 ⇒ 判渲染器必须排在判可见性**之前**，否则「尾迹当前是关的」
     会把 `shu` 当成生效键交出去。
   - 混合模式读材质：`additive`（雾 / 光束）与 `translucent`（鸟 / 星星）两条通道。
   - 参数按 WE 的 initializer / operator 名字取：`lifetimerandom` `sizerandom` `velocityrandom`
     `colorrandom` `alpharandom` `rotationrandom`，算子 `alphafade` `movement`（重力）
     `oscillatealpha`（闪烁）`controlpointattract`（吸引点，鸟群收尾靠它）。
   - `instanceoverride` 是作者在同一份预设上的逐实例微调（数量 / 速率 / 尺寸 / 透明度 / 染色），
     值本身也可能是用户绑定，所以逐条走 `bindingValueOf`。`count` 只在作者真写了时生效：
     `<1` 当比例、`≥1` 当绝对上限且**绝不放大**预设自带的数量上限（官方 Stars 写的就是 5）。
   - 序列帧：WE 官方把尺寸编进文件名（`birds_128x120x16` ⇒ 一格 128×120、16 帧），列数由播放器
     按纹理实际宽度换算；内置资产没这个约定，帧表来自 `.tex` 的 TEXS 段。
   - 发射器 `boxrandom` 的 `distancemax` 是 vec3（各轴半宽）、`sphererandom` 是标量半径，两条
     分开解析。

9. **Demiurge 改动⑨：`effects/waterflow`（本作品＝背景云层流动）。**（2026-10-24）
   `waterflowOf(obj, resolveTexRef)` 在对象的 effects 里找 `effects/waterflow/effect.json`，
   取 `passes[0]`：槽位顺序即 WE 的 `g_Texture0/1/2`（0=对象自身的图，渲染期已知；1=流场遮罩；
   2=相位图），参数取 `constantshadervalues.speed/strength/phasescale`，命中时图层多一个
   `flow:{maskUrl,phaseUrl,speed,strength,phasescale,enabled}`。三条 fail-closed 判据：遮罩缺失 /
   是 `_rt_` 渲染目标 / **包内 `readFile` 取不到字节** ⇒ 整条不发也不提交任何键；`visible` 是字面量
   `false`（作者硬关，本包 day4/night4 即如此）⇒ 同样不发；只有 `visible` 带用户绑定时才把描述符留给
   播放器做开关。速度 / 强度 / phasescale **排在闸门之后**解析——它们也带绑定，提前折叠会把没人消费的
   键漏进面板。相位图**取不到不判死**（本包那一槽是 WE 内置 `util/white`，磁盘上没有文件），
   `phaseUrl` 留空串，播放器用 1×1 白兜底＝全场同相，与 WE 实机一致。
   发射点 `const layerFlow = waterflowOf(obj, resolveLayerTex)` **不再加 `alpha >= 0.999` 闸门**：
   播放器的专用程序与标准程序共用同一顶点着色器和同一套 `u_tint/u_bright/u_power/u_alpha`，
   透明度与 UV 裁剪都在 flow 路径里得到同样处理（原先那道闸是因为复用的 `progFlow` 没有 alpha
   uniform，半透明层走进去会丢透明度——那属于播放器侧改动⑨ 换掉 `progFlow` 后不再存在的前提）。

10. **Demiurge 改动⑩：视频纹理（MP4-backed `.tex`）交回裸 MP4。**（2026-10-25，
    真机 `3723257973` = Blue Archive 生盐诺亚「落花吟」）该作品的人像主图是一张
    **视频纹理**：`TEXV0005` 头里的 `isVideoMp4` 为真，载荷是 3840×2160 / 19.98s 的
    H.264 MP4（67MB）。上游 `extractSceneResourceVia` 对这类 `.tex` 会落进 `decodeTex` 的
    拒绝分支（`tex: video mp4 textures cannot be decoded to a static frame`），
    再被外层 `catch { return file.bytes; }` **静默当成纹理解码失败原样回吐整个 TEX 容器**；
    产品侧把它标成 `image/png` 建 blob ⇒ `<img>` 加载失败 ⇒ 播放器跳过该层，
    整幅人物与樱花消失、只剩文字层（用户截图实锤）。
    新增 `sliceMp4Payload(raw, mip0)` 与 `extractSceneResourceVia` 开头的分支：
    `parsed.isVideoMp4` 为真时在 mip0 / 原始字节的前 4KiB 里找 `ftyp` box，
    **从 box 长度字段（`ftyp` 前 4 字节）切到 EOF** 交回裸 MP4，规则与
    `extractSceneVideoVia` 一致。真机实测：本包 MP4 起点在字节 91（前面是 TEX 头 +
    mipmap 前言，且容器声明的长度不可信），切出的 67,326,854 字节与参考视频逐字节相同。
    找不到 `ftyp` 时保持旧行为（回吐原始 TEX），不新增崩溃路径。
    解码仍是 `decodeTex` 的拒绝路径：静态帧接口不伪造视频首帧。

11. **Demiurge 改动⑪：日期脚本改为静态求值 + 模板化提取。**（2026-10-25，真机
    `3723257973` = Blue Archive 生盐诺亚「落花吟」）原实现用 `scriptTable` 按
    `scriptProperties.<prop> == <值>` 锚点找一张顶层常量表，只覆盖「横排、单表」的简单写法；
    本包作者把月名/日号/年份拆成全角度的竖排表，且表在 `if` 块里、`newLine` 由
    `alignVertical`/`useDelimiter` 派生 ⇒ 旧解析器整层拿不到内容、日期退回横排。新增一组
    **不执行脚本**的静态求值器：

    - `scanIfBlocks(src)` + `braceEnd` + `condHolds(node, cond)` + `activeAt`：找出脚本里所有
      `if (scriptProperties.<键> ==/!= <字面量>)` 块（支持 `&&`/`||`），按用户生效值判定当前
      走哪些分支。`braceEnd` 必须先减深度再与 0 比较：先比较后减会让外层块的右花括号永远
      匹配不上、整段吞到文件尾（本刀踩过，Date/Day 两层同时消失）。
    - `varExprs` / `varValueOf` / `splitTopLevel` / `scriptArray` / `literalOf` /
      `unescapeJsString`：在**当前生效的分支集合**里解析 `var x = ...` 的字符串拼接表达式
      （`'O' + newLine + 'C' + ...`、`yearold.split('')`、`delimiterValue = ['\n\n']` 等），
      求出月名表、日号表、星期表、组间分隔符与年份拆分符。
    - `dateFormatOf(src, node, blocks)`：从 `return` 语句反推作者的拼串顺序，归一成
      `{dayTable}|{month}|{year}|{weekday}|{day}` 占位符模板（例如本包
      `{dayTable}\n\n{month}\n\n{year}`、另一族的 `{weekday}`、`{day}/{month}/{year}`）。
    - `textSpecOf` 只在这套求值完整成立时发出 `{time:'date', format, months?, days?,
      dayTable?, yearSplit?}`；任一表/分支认不出 ⇒ 返回 null，不猜格式。
    - 三族真实写法都有单测（`decodeScenePackage.test.ts` 的 Family A/B/C）：横排 + 空格月名、
      竖排 + 空行分隔 + 逐字年份、只输出星期名；外加「脚本返回未知表 ⇒ 该层不发射」的
      fail-closed 用例。

12. **Demiurge 改动⑫：文字字号吃自身与中间父组 scale，不吃最外层组件根组。**（2026-10-25，
    两轮修正）第一轮把父链折叠后的 `sizeMul` 全乘进 `fontSize`，日期被放大、星期缩成小 "S"；
    第二轮改成只吃对象自身 scale，结果问候语只剩 7.37、竖排 SUN 比日期大 3.8 倍。最终按 WE
    参考图逐字测量定规则：**对象自身 + 中间父组的 scale 连乘，最外层「组件」根组的 scale 只
    折叠进 origin**（根组是整块组件的摆放缩放，字形不吃）。真机 `3723257973` 五处锚点
    （应用截图与 WE 参考图按坐标距离归一后逐项对字高）：问候语 32×0.23025×3.125=**23.025**
    与帧率 24×0.948=**22.752** 同高；竖排 SUN 96.653×0.85=**82.155** 与日期 140×0.18=**25.2**
    的比≈3.26（参考图 3.2）；时钟 202.26×0.28038=**56.71** 与日期比 2.25（参考图 2.24）。
    `resolveObjectTransform` 额外回传 `innerScale`（自身到中间父组的乘积），
    `sizeMul = (|innerScale.x| + |innerScale.y|) / 2`（镜像取绝对值），
    `fontSize = pointsize × sizeMul`；origin 仍折叠整条父链。

13. **Demiurge 改动⑬：问候语与帧率脚本改为静态提取。**（2026-10-25，真机 `3723257973`
    左上角 "Morning" 与 "fps：60"）这两层此前落进 `textSpecOf` 的「认不出 ⇒ 不出层」分支，
    实拍缺两块文字。仍**不执行脚本**，只认两种固定形状：

    - `greetingSpecOf(src)` ⇒ `{time:'greeting', labels, thresholds}`。要求四环齐全：
      `new Date().getHours()` 的变量声明；至少两个「全字符串」对象字面量标签表；
      `switch(<tag>)` 且每个 `case 'x': <var> = <表>[<整数下标>]` 指向**同一张表**；
      `<tag> = hour < N ? 'tag' : …` 的阈值链（阈值严格递增且 ≤24，末支必须是字面量）。
      任一环缺失即返回 null，整层不发射。标签表取英文变体（中文变体在真机上被
      `visible=false` 关掉，只按可见性纪律交出 `newproperty54`）。
    - `fpsSpecOf(src, node)` ⇒ `{time:'fps', label, refreshMs, text}`。要求同时出现
      `Math.round`、`1000 / (`、`scriptProperties.refreshInterval`、`scriptProperties.label`；
      脚本若读 `scriptProperties.active`，其生效值（用户覆盖优先）为假时返回 null——
      WE 那边 `update` 返回 undefined、文本不刷新，我们也不该继续画。`refreshMs` 取
      `[0, 60000]` 内的生效值，否则 500；`text` 是工程里烘焙的初值（`"fps：60"`），
      在第一个刷新窗口结束前显示它。
    - 顺序：`textSpecOf` 先试 FPS、再试问候语，最后才走时钟/日期惯用法——FPS 脚本里也有
      `new Date()`，不先拦住会被误判成时钟。

14. 其余逻辑（PKGV 解析、LZ4、TEX/DXT、`extractSceneResource` 等）原样。

**已知限制（本刀范围）**：蒙皮权重固定 4 骨骼；附件跟随只走「最近祖先」的刚性子树，多级
puppet 嵌套未实现；脚本引擎仍不执行（`scripted:true` 场景渲染静态结构 + `animationlayers`，
但点击切姿势、`scriptproperties` 的动态求值不跑）。文字只认四族固定形状：§7 的时钟惯用法、
§11 的日期模板族、本项的问候语阈值表与帧率计数；**其他形状的任意 `update()` 脚本**仍整层跳过
（宁可少画，不猜内容）；
`rope` / `ropetrail` 粒子不模拟（⇒ 鼠标尾迹一族的键不进面板）；音频感应属性不接（面板同样不摆）；
`effects/waterwaves` 与 `effects/foliagesway` 未实现 ⇒ 角色的「飘动效果」`bigbdriftingeffect`
仍是未生效键，不进面板。

除上述 14 项外**没有任何**逻辑改动。

### `we-scene-player-html.js`（上游 `lib/scene-player.js`）

1. 文件头注释改写为「本地改动见本 NOTICE」（无代码影响）。
2. Demiurge 不编辑播放器页面的 `<script>` 结构，而是在装配播放器文档时于其 `<script>` 之前
   **注入一段垫片**（代码在本目录之外的 `../scenePlayer.ts`，垫片插入点本身不在本文件里）。
   垫片现在挂住这些点，逐条都有真机依据：
   - `window.fetch`：把 `/wallpaper-engine/scene-manifest/...` 重写到 blob URL。
   - `window.requestAnimationFrame` / `cancelAnimationFrame`：暂停时把回调挂进队列并返回 0，
     恢复时先补跑挂起帧；**settle 判据 = 连续两帧 draw call 相同且不为 0，且该帧窗口内无新贴图上传**，
     另有 `WARM_FRAME_CAP=120` 兜底（上游 `renderFrame` 在清单到位前连 clear 都不发、未加载层被
     逐层跳过 ⇒ 只补一帧会定格在空白画面上）。
   - `WebGLRenderingContext.prototype.texImage2D`：计数 + 「定格后才到的贴图上传 ⇒ 立刻补一帧」。
     上游 `loadTexture` 先塞 1×1 占位、`img.onload` 里才真正上传，而 `onload` **不走 rAF**。
   - 交给播放器的时间戳：暂停期间钉死成 `frozenNow`（上游用 `now` 反推 `elapsed` 喂 `u_time`，
     云层/水面/霓虹等着色器动画不走 `animTime`，不钉住则每次补帧都把相位往前跳一格）。
   - `dsh-pointer`：用 `new MouseEvent('mousemove', …)` 还原宿主转发的归一化坐标。
   - `dsh-recover-renderer`：垫片拦截并自己补一帧，**只在 `gl.isContextLost()` 为真时**放行给
     上游重建——否则宿主「为补帧发的消息」会被上游读成「渲染器坏了」。
3. **Demiurge 改动②（用户批准的例外）：层内视差 + 视差开关。** 在 `WE_SCENE_PLAYER_HTML`
   模板字符串内部、`function drawCloudsBgLayer(...)` 之前插入 `PARALLAX_RATIO` 与 `layerOrigin()`，
   并把四处图层 quad 的模型矩阵从 `layer.x/layer.y` 改为 `layerOrigin(...)`：

   - Pass 1 反射 FBO 的天空/上层循环（`progBasic`，紧跟 `if (!texRec.loaded) continue;`）
   - flowimage 分支（`progFlow`）
   - flag 分支（`progFlag`）
   - Pass 2 标准图层分支（`progBasic`，紧跟 `u_power` 设置）

   反射层（`progReflection`）最初不套视差：反射层不带 `parallaxDepth`，其 rect 同时用于
   采样坐标换算，故保持 `layer.x/layer.y`。改动⑤把它也统一收进 `layerFrame()`——**视差仍因
   `depth` 缺省 `[0,0]` 而恒等**，多出来的只是附件跟随增量（镜中的头发要跟着头动），
   采样坐标换算仍用原 rect。
   `sceneW/sceneH` 是 `renderFrame` 的局部量（不是全局），所以 `layerOrigin` 显式接收，
   不能照抄成闭包读取。`depth` 缺省 `[0,0]` ⇒ 无该字段的场景行为逐字不变。
   另新增 `cameraParallaxEnabled()`（`sceneData.cameraParallax !== false`）：为假时
   `layerOrigin()` 直接返回 `layer.x/y`，相机分支里的鼠标视差项（`targetYaw/targetPitch`）
   也置 0——作品在 WE 里关了 `general.cameraparallax` 就不该跟着鼠标晃。
4. **Demiurge 改动④：puppet 网格绘制。** 在 `mat4Transform2D` 之后插入
   `drawBasicMeshLayer(layer, aPos, aUv, sceneW, sceneH, elapsed, proj)`：
   用 `getGpuMesh(layer.mesh)` 建 pos/norm/uv/index 缓冲，模型矩阵为
   `mat4Transform2D(origin.x, origin.y, meshScale[0], meshScale[1], angle)`
   （网格顶点是 puppet 本地单位，**不能**再乘 `layer.w/h`），
   `gl.drawElements(gl.TRIANGLES, iCount, idxType, 0)`，然后恢复 `quadBuf` 与
   `aPos/aUv` 的 16 字节 stride 指针。两个绘制入口各加一条 `layer.mesh` 分支：
   Pass 1 反射 FBO 的天空/上层循环，以及 Pass 2 主画面（在标准 quad 分支之前）。
5. **Demiurge 改动⑤：puppet 蒙皮与动画层。**（2026-10-24）

   - `getGpuMesh` 识别 `mesh.skinIndicesB64/skinWeightsB64`：命中时位置缓冲改为
     `DYNAMIC_DRAW` 并另建 `skinnedPos`。
   - 新增 `mat4MultiplyOut` / `puppetRuntime` / `animationSignature` / `buildPoseLocal` /
     `puppetWorld` / `puppetPose` / `skinGpuMesh`：按清单 `puppets[puppetId]` 的骨骼链与 MDLA
     轨道合成姿势（基础层 + additive 叠加，`offset/rate/blend` 逐条吃清单给的），
     `skin = world × inverseBind`，CPU 侧 4 骨骼加权后 `bufferSubData` 回位置缓冲。
     姿势按 `puppetId|动画签名` 缓存，签名不变不重算。
   - 新增 `followDelta(follow)` 与 `layerFrame(layer, …)`：附件（头发）子树绕**绑定 socket
     的屏幕点**旋转 `Δθ` 再平移 `S(tA − tB)`，网格与 quad 两类绘制都改走 `layerFrame`。
   - 动画时钟独立：`let animTime = 0` + 每帧 `animTime += dt`，姿势只吃它（`u_time` 仍吃
     `elapsed`）⇒ `dsh-set-pause` 能真正冻住姿势。
   - 调试挂钩 `window.__weDebugAnim` / `window.__weDebugTime`（供控制台手工冻住动画做像素对照；
     自动化用例改用产品语义的 `wallpaperPaused` 冻帧）。不冻帧没法比像素：这张作品常驻呼吸/云层
     漂移，实测**同一状态连拍两张差 55%**。

6. **Demiurge 改动⑦（播放器侧）：文字光栅化与实时时钟。**（2026-10-24）WE 的字形是 3D 文字
   网格，本实现等价地用 2D canvas + `FontFace` 生成位图、上传后走**同一个 `progBasic`** ⇒
   视差、旋转、混合与图像图层共用一条路径。新增 `TEXT_SUPERSAMPLE=2`（2× 超采样）、
   `textBitmapCache`（上限 `TEXT_CACHE_LIMIT=48`，LRU 逐旧）、`fontFaceOf`（字体异步：未就位期间
   先用系统字栈出字，`ready`/`fallback` 后整张位图缓存作废重建，不留旧字形）、`textLineOf` /
   `textBitmapOf`（时钟按 `hour24/seconds/delimiter` 拼，日期按清单的 `format` 模板替换作者表，
   见第 11 项）、`drawTextLayer(layer, aPos, aUv, sceneW, sceneH, wallMs, proj)`。
   **字形按白色光栅化、颜色交给 `u_tint`**：同一句话在白天/夜晚两套颜色下复用同一张位图。
   时钟只吃 `wallClock`（每帧 `Date.now()`）而不吃 `elapsed` ⇒ 暂停＝定格时画面里的时间也停住，
   恢复后立刻跟回真实时间。
7. **Demiurge 改动⑧（播放器侧）：2D 粒子模拟。**（2026-10-24）`particles2dState`（**WeakMap**
   按系统对象存状态：改一次属性清单就整体重建，用 Map 会把每份旧清单的键永久留住，
   实测滑块每拖一格泄漏一组粒子）、`getParticles2d` / `spawnParticle2d` / `updateParticles2d`
   （发射速率、生命、逐轴尺寸与扩散、重力、淡入淡出、闪烁、吸引点、按年龄推进的序列帧），
   绘制复用 `progParticle` 的正交 quad 通道，与 3D 通道 `updateParticles3d` 状态分开。
   每帧写 `window.__weParticles = {systems, live, quads, steps}`——真机 e2e 靠它把
   「没发射」「贴图没到」「模拟没推进」三种失败分开（只看像素分不出）。
8. **Demiurge 改动⑨（播放器侧）：`effects/waterflow` 走专用流场扰动程序。**（2026-10-24，
   本作品的「背景云层流动」）三处插入：`fsWaterflow` 片元着色器（紧挨 `fsFlow` 之后、
   `createShader` 之前）、`const progWaterflow = createProgram(vsBasic, fsWaterflow)`（紧跟
   `progFlow`）、`whiteTex()`（`loadTexture` 之后，一张 1×1 全白纹理，对应 WE 内置但**不落盘**的
   `util/white`）。Pass 2 标准图层分支**之前**插入 `layer.flow` 分支：`u_tex` 绑本层自己的贴图
   （**CLAMP 上传 + 着色器里 `fract()` 环绕**：WebGL1 的 NPOT 贴图设成 REPEAT 会让整张纹理
   incomplete、取样恒返回 (0,0,0,1)，本包天空 4200×2500 正是 NPOT ⇒ 天空被画成一块不透明黑，
   真机截图实锤），`u_mask` 绑清单给的流场遮罩，`u_phase` 绑相位图，
   取不到时落到 `whiteTex()`＝全场同相；颜色通道 `u_tint/u_bright/u_power/u_alpha` 与标准分支逐字
   对齐（否则拨一下开关天空就跟着变明暗），坐标走 `layerFrame` + `u_uvRect`，所以视差与裁剪不缺。
   **量纲（这一条是本项存在的理由）**：WE 的 `strength` 是**像素**，`u_amp` 是 UV 位移，CPU 侧必须
   除以内容贴图宽（`flowAmp = strength / flowContent.width`）。第一版复用上游 `progFlow` 时把
   `strength` 原值当 UV 喂进去，而 `fsFlow` 里还有 `* 0.1 * cycle` 的乒乓交叉淡入——`strength=5`
   变成约半屏位移。`progFlow` 服务的是 WE `flowimage`（三张内容图各自漂移 + 交叉淡入），与单张贴图
   的 waterflow **不是同一个效果**，不能复用；上游另有 `lib/we-renderer/effects/waterflow.js` 的
   CPU 实现，但本仓库没有那个文件，其口径未经核对，不足为依据。
   两个缺陷（半屏位移 + NPOT 黑天）都由 e2e 那条「白天必须比夜晚亮 20」抓到：贴图自身平均亮度
   蓝天 159.6 / 星空背景 67.4，画成黑块时两侧实测只差 2.3。
   仍保留的**已知偏离**：相位差用 `phase * phasescale` 直接进 `sin()`，`feather` 的 smoothstep 忽略；
   Pass 1 反射 FBO 一律不套效果（与 flowimage / flag 一致）。遮罩或本层贴图没到位则整层落回标准
   绘制——少一次扰动，但天还在。
9. **Demiurge 改动⑥（播放器侧）：热换清单不重建 iframe。** `swapSceneManifest(next)` +
   `collectLiveTextureUrls` / `pruneTextures` / `pruneGpuMeshes`：预设改一个键时宿主只重解码清单
   （包体与解码结果还在 Worker 缓存里），播放器换掉清单并回收不再被引用的贴图与网格；
   换清单期间 `manifestOverridden` 挡住上游自己去 `fetch` 旧 URL。
10. **Demiurge 改动⑩（播放器侧）：视频纹理走 `<video>` 通道。**（2026-10-25，
    真机 `3723257973`）`loadTexture(url, repeat, video)` 增加第三参：清单里 `videoTex: true`
    的图层不再创建 `<img>`，而是建一个 `muted / loop / playsInline` 的 `<video>`
    （1×1 隐形挂在 `document.body`，`preload=auto`），`loadeddata` / `seeked` 与
    `requestVideoFrameCallback` 每帧把当前帧 `texImage2D` 成纹理（无 rVFC 的浏览器退到
    `timeupdate`）。三条必须点：
    - **NPOT 先设 CLAMP**：3840×2160 不是 2 的幂，`loadTexture` 的默认环绕停在 REPEAT，
      NPOT + REPEAT 是 incomplete 纹理 ⇒ 采样恒 (0,0,0,1) ⇒ 整层画成不透明黑；
      视频分支在第一次上传时就设 `CLAMP_TO_EDGE`（与改动⑨的天图同一教训）。
    - **暂停要真的停播**：`dsh-set-pause` 现在遍历纹理缓存对每个视频 `pause()` / `play()`；
      只停 rAF 会让 4K 解码在「已暂停」的壁纸后台继续吃 CPU/GPU。
    - **换预设要回收**：`pruneTextures` 的 key 解析同时剥掉 `|video` 后缀（否则每条视频
      都会被判成「不在 live 集合」而每换一次预设就重建、或反过来漏回收），
      并按「pause → 移除 src → load() → 摘出 DOM」释放解码器。
    `layer.flow` 分支的内容图同样透传 video 标记；标准图层与 Pass 1 FBO 两条绘制路径都走同一记录。
    色彩空间未做显式转换（按 `<video>` 默认实现，真机与 WE 观感一致；如需 sRGB 校准另开一刀）。
11. **Demiurge 改动⑪（播放器侧）：日期模板替换 + 行距吃字体度量。**（2026-10-25，
    真机 `3723257973`，与清单第 11/12 项配对）
    - `textLineOf` 的 `time === 'date'` 分支不再拼死格式，而是按清单
      `{format, months, days, dayTable, yearSplit}` 做占位符替换：
      `{month}|{weekday}|{dayTable}|{day}|{year}`；`yearSplit` 把年份逐字拆开，`dayTable`
      是作者的补零/竖排日号表。任何占位符缺表就退回裸值，不留 `{...}` 在画面上。
      时间仍吃 `wallClock`（暂停＝定格时日期也停住），**不执行** WE 脚本。
    - `textBitmapOf` 的行距从固定 `1.25em` 改为**字体自身 ascent+descent**
      （`ctx.measureText('Mg').fontBoundingBoxAscent + fontBoundingBoxDescent`，量不到才回退
      1.25em；字体异步就位后 `clearTextBitmaps()` 会按真字体重新量）。WE 参考图里
      Quicksand 日期列行距 ≈1.17em、spincycle 竖排 `SUN` ≈1.22em；固定 1.25 会把三行 SUN
      多撑约 10~20%，把底下的时钟压掉。多行仍逐行居中、2× 超采样、白色光栅 + `u_tint` 染色。

12. **Demiurge 改动⑫（播放器侧）：问候语与实时帧率。**（2026-10-25，与清单第 13 项配对）
    - `textLineOf` 末尾新增 `time === 'greeting'` 分支：取 `wallClock` 的小时，按清单的
      `thresholds` 从 0 开始逐段推进，返回对应 `labels[i]`。吃 `wallClock` 而不是
      `Date.now()` ⇒ 暂停定格时问候语也停住，恢复后跟回真实时间。
    - 新增 `fpsState`（`Map`，按图层对象存 `{last, start, sum, count, text}`）与
      `fpsTextOf(layer, nowMs)`：用**实际渲染帧的 rAF 时间戳差**求瞬时帧率，在
      `refreshMs` 窗口内取平均后 `Math.round` 拼上 `label`；窗口结束前一直显示清单里的烘焙初值。
      暂停时不累计；`dsh-set-pause` 恢复时 `fpsState.clear()`——否则冻结那段时间的
      空档会被算成一次极低帧率，恢复后第一格数字明显偏低。
    - `drawTextLayer` 多收一个 `nowMs`：`time === 'fps'` 走 `fpsTextOf`，其余仍走
      `textLineOf`。`swapSceneManifest` 末尾清空 `fpsState`（旧图层对象不再被引用，留着只是泄漏）。
    - 数值语义的**已知偏离**：WE 统计的是它自己的渲染循环，我们用浏览器实际 rAF 帧率，
      所以数字会贴近显示器刷新率（常见 60）而不是 WE 截图里的 20~30；后台标签页降频时
      数字也会跟着掉。这是「诚实的实测帧率」，不是伪造 WE 的读数。

除上述 12 项外模板字符串本体未改（`<script>` 数量与垫片插入点保持不变）。

播放器文档用 `blob:` URL 装载而**不是 `srcdoc`**：`about:srcdoc` 文档里
`window.location.origin` 读到的是字符串 `'null'`，而上游播放器两头都拿它做校验
（入站 `ev.origin !== window.location.origin` 丢弃、出站 `postMessage(msg, window.location.origin)`），
结果 fit / pause 全部失效且 `dsh-scene-unavailable` 也回不来。`blob:` 文档继承创建者源，
两头都是真实站点源（2026-10-23 真机实测：`iframeOrigin='null'` + rAF 51 帧/600ms 但画面不重绘）。

## 再同步上游

两个文件都是**纯 CRLF**；编辑时先判 EOL、按 CRLF 写回，否则 `git diff` 会整文件变红，
垫片/HTML 锁测试也会失效。行号会漂移，重新施加补丁时按上面的**代码锚点**定位，不要按行号：

- manifest：搜 `swaySpeed: 1.5,`，同时确认所在 push 对象含 `isGround`；`parseVec2` 搜 `parseVec3`。
  puppet 补丁搜 `parseMdlRig`（导出函数）与 `puppetRigOf`（manifest 闭包内），
  图层发射搜 `typeof modelJson.puppet === 'string'`，可见性搜 `isObjectVisible`，
  蒙皮/动画搜 `MDL_FLAG_SKIN_BLEND`、`buildPuppetPayload`、`animationOf`、`followOf`，
  视差开关搜 `cameraParallax:`，用户预设搜 `noteBinding` / `manifest.userBindings`。
  文字补丁搜 `textSpecOf` / `textLayerOf` / `canvasOriginValue` / `scanIfBlocks` / `condHolds` /
  `dateFormatOf`（日期模板求值），字号规则搜 `resolveObjectTransform` 回传的 `innerScale`
  （**不要**把最外层根组 scale 折进字形），月名/日号表搜 `scriptArray` 与 `varValueOf`，
  问候语搜 `greetingSpecOf`，帧率搜 `fpsSpecOf`，以及主循环里
  `typeof obj.text !== 'undefined' && typeof obj.image !== 'string'` 那个分支（**必须排在**
  `!obj.image ⇒ continue` 与图像名黑名单之前）；内置资产通道搜 `WE_BUILT_IN_PREFIX` /
  `builtInAssetKeys`；贴图格式搜 `TexFormat.RG88` 与 `TEXS000([1-3])`。
  粒子补丁搜 `if (typeof obj.particle !== 'string')`（循环排在 `is3D` 分支之外）与
  `rendererName !== 'sprite'`；水flow 效果搜 `waterflowOf`（紧跟 `hasReflectionEffect`）、
  描述符里的 `phaseUrl` / `phasescale`，与 push 里的 `flow: layerFlow || undefined`
  （**这道发射没有 alpha 闸门**）。
- player：搜 `function drawCloudsBgLayer(` 插辅助函数；四处绘制原本搜
  `mat4Transform2D(layer.x, layer.y, layer.w, layer.h, layer.angle || 0)`，现已被
  `layerFrame(layer, sceneW, sceneH)` 取代，按紧随其后的 program 名（`progBasic` ×2 /
  `progFlow` / `progWaterflow`（新增分支）/ `progFlag`）区分，**不要**给 `progReflection` 的采样坐标换算套视差。
  puppet 补丁搜 `function drawBasicMeshLayer(`、`function skinGpuMesh(`、`function followDelta(`
  与两处 `if (layer.mesh)`（FBO 循环与主循环各一，主循环那处必须在 `progBasic` 标准分支之前）。
  文字补丁搜 `function drawTextLayer(`、`function textBitmapOf(`、`function textLineOf(`、
  `fontBoundingBoxAscent`（行距锚点）、`let wallClock`；问候语搜 `layer.time === 'greeting'`，
  帧率搜 `function fpsTextOf(` 与 `const fpsState`（暂停恢复处的 `fpsState.clear()` 不能删：那是
  「冻结空档不被算进平均」的唯一防线）；
  粒子补丁搜 `function updateParticles2d(`、`const particles2dState`、`window.__weParticles`；
  waterflow 分支搜 `if (layer.flow && layer.flow.enabled !== false)`（在主循环 `// Standard Layer`
  之前）、`const progWaterflow`、`const fsWaterflow`、`function whiteTex(`，分支里的
  `flowAmp = (layer.flow.strength || 1) / Math.max(1, flowContent.width)` **不能删**（那是像素→UV 的
  唯一换算点），内容贴图那一句必须是 `loadTexture(layer.texUrl)`（**不带** repeat）且着色器里带
  `fract(`（NPOT 贴图用 REPEAT 会整张失效）；热换清单搜 `function swapSceneManifest(` 与 `manifestOverridden`。

整份替换上游文件后必须重新施加以上补丁，并更新本文件里的 SHA-256。

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

## 已知能力边界（不是我们改坏的，是这套实现的范围）

- **puppet 已支持蒙皮与 `animationlayers`**：MDLV 网格 + MDAT 骨骼附件 + 4 骨骼线性混合蒙皮 +
  MDLA 关键帧剪辑，基础姿势 + additive 循环层（眨眼/呼吸/重心）自动播，附件子树（头发）跟随骨骼
  刚性旋转；**非循环单发 clip 视为点击触发，不自动播**。
- **文字与 WE 粒子都已接入**（2026-10-24）：时钟/日期走§改动⑦、sprite 粒子走§改动⑧。
  真机样张 `3346715292`：角色身体/裙摆/头发拼合且随呼吸摆动、头发跟随头部，时钟与日期文字按
  真实墙钟走，雾/光束/鸟群三套 sprite 系统真发射。
- **日期脚本的三族写法已覆盖**（2026-10-25）：横排月名 + 分隔符、竖排空行 + 逐字年份、只输出
  星期名（见§改动⑪/⑫）。真机样张 `3723257973`：日期列 `2 5 / O C T / 2 0 2 6`、大号竖排
  `S U N`、右下时钟，字号层级 82.155 / 56.71 / 25.2 与 WE 参考一致；e2e
  `wallpaper-scene-text-layout.spec.ts` 同时钉住清单格式、坐标与逐行光栅。
- **问候语与帧率脚本已接入**（2026-10-25，见§改动⑬/⑫）：问候语按工程里的小时阈值表静态求值
  （12:34 显示 "Noon"、10:46 显示 "Morning"），帧率由播放器实测 rAF 帧间隔。真机样张
  `3723257973` 左上角两块文字现在会画出来，字号 23.025 / 22.752 与 WE 参考同高。
  **仍不支持**其他任意 `update()` 脚本形状（认不出就整层跳过）。
  FPS 数字是浏览器真实帧率，不会等于 WE 自己的统计值。
- **视频纹理已接入**（2026-10-25，`3723257973`）：`.tex` 里内嵌的 MP4 由清单侧切成裸流，
  播放器按 `<video>` 逐帧上传；暂停随 `setPaused` 真的停解码。**未做**：H.264 之外的编码由
  浏览器决定（WE 用的就是 H.264，实测 Edge 可解）；视频纹理的透明通道不受支持（H.264 无 alpha）。
- **WE 属性覆盖面由清单说了算**：`manifest.userBindings` 只登记真正落到渲染层的键，面板据此收窄。
  `3346715292` 实测 78 条属性里 **41 条生效**（含本作的 `newproperty9` 云层流动 /
  `newproperty10` 云层速度 / `shine` 光束 / `hrbigbfog` 雾）。未生效的 37 条里大头是作者当
  分隔线与推广位用的 `text` 键（本就没有渲染语义），真正待补的功能族是：
  **飘动效果**（`bigbdriftingeffect`，需 `waterwaves` + `foliagesway` 两条新着色器通道）、
  **相机几何**（`newproperty30`/`x3`/`y1`/`newproperty1`，本作是 2D 正交场景，镜头参数怎么落要先定）、
  **音频感应**（`_01`/`tiao`/`newproperty18`/`newproperty45`，需要音频输入，属产品决策）、
  **鼠标尾迹**（`shu`/`hrbigbmousetrail`/`newproperty16`/`newproperty17`，rope 渲染器不模拟）、
  提示框与 WE 方案色。这些不进面板是**诚实**，不是遗漏的借口。
- 鼠标视差由宿主转发（见 `../README.md`），并受作品自己的 `general.cameraparallax` 门控；
  壁纸层仍是 `pointer-events: none`。
- 「暂停 = 定格」由垫片的 settle 判据（draw call + 贴图上传）、`frozenNow` 时间戳钉死与
  `dsh-recover-renderer` 补帧通道共同实现；垫片之外上游播放器逐字不改。
- 同源 iframe 仍占用渲染主线程；真正的工作线程隔离要改 OffscreenCanvas，属后续项。
