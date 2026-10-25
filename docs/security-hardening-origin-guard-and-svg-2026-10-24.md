# 改进文档：本地 API 网络面加固（Origin/Host 闸 + local-view SVG 响应头）

- 日期：2026-10-24
- 来源：全库代码审计（架构/安全/工程过程），对应审计建议清单第 1、2 项
- 状态：**改进项 1 已实施（2026-10-24）**——实施期对 §1.1/§1.3 做了机制修正（FastAPI 0.140 严格
  Content-Type 默认实测，H1 链条收敛，见 §1.1 修正注）；**改进项 2 已实施（2026-10-24 同日）**
  ——按 §2.2/§2.3 方案 B 落地（`LocalMedia.headers` 响应头属主 + `_media_type` svg 修正，零功能
  回退）。审计建议第 3 项（url_guard 出站 TOCTOU，§1.6 #6 登记的另立刀）同日已实施（见
  `ARCHITECTURE.md` 条 55）；本审计指出的 XSS→密钥链残余两环（localStorage 密钥 + CSP，
  对应审计清单改进项 3）亦同日实施（见 `ARCHITECTURE.md` 条 56——CSP 属主经实探修正为
  后端，非 tauri.conf）。
  本文档同时保留设计定稿与实施后真源两个角色；动代码时按 §1.5 / §2.5 的同步清单走。
- 涉及模块：`backend/app/main.py`、新增 `backend/app/services/request_guard.py`、`backend/app/services/local_media.py`；前端与桌面壳**零改动**。

---

## 一、改进项 1：回环门禁中间件补 Origin/Host 校验

### 1.1 问题与证据

现有防线（`backend/app/main.py:82-95`）只看 TCP 对端地址：`request.client.host` 是回环就放行。
这挡不住**浏览器中介**的攻击——本机浏览器发起的跨站请求，对端地址同样是 127.0.0.1：

- **H1（drive-by CSRF）**：用户开着 Demiurge（默认端口 8010 有公开文档）时访问恶意网页，
  跨站请求可触达本机 API。**机制经实施期真实探针修正（2026-10-24，初稿判断过重）**：
  本机 venv 内 FastAPI 0.140.13 默认开启**严格 Content-Type**（`strict_content_type` 默认值，
  `fastapi/routing.py:399-446`）——无 Content-Type / form 编码 / text/plain 的请求体对
  「JSON Pydantic 模型端点」一律保持 raw bytes → 校验 422 **不解析**（实测
  `POST /api/comfyui/stop` form 体回 422 `Input should be a valid dictionary...`，显式
  `application/json` 才 200）。因此初稿所写「form 编码体被解析进扁平模型 ⇒
  `install-tracked` 表单链 clone+pip **等效 RCE」在该版本上不成立**；JSON 模型端点
  （含 install-tracked）实际被上游默认挡住。**跨站仍可达**的 POST（AST 全量枚举确认）：
  ① multipart 端点 `/api/comfyui/upload`、`/api/user-state/wallpapers/upload`
  （`<form enctype=multipart>` 是 CORS 简单请求，浏览器不预检、请求照发、**必带 Origin**）；
  ② 无 body 的 POST（如 rag 的 `list_taggers`）。Origin 闸把这两类整类拦死，且把防线从
  「FastAPI 版本行为」自有化为项目合同——上游若翻转 strict 默认，本闸仍在。
- **H2（DNS rebinding 读密钥，主修复动因）**：攻击者域名先解析到自己的服务器、再 rebind
  到 127.0.0.1。受害页面 fetch `http://evil.com:8010/api/user-state` 与页面同源 → GET 不带
  Origin 头、CORS 不拦、严格 Content-Type 与 GET 无关 → `GET /api/user-state`
  （`backend/app/routers/user_state.py:54`）返回含各模型 apiKey 的 settings → 响应可被
  攻击者页面直接读取。rebind 后 Host 头是攻击者域名 ⇒ Host 闸拦截。
- **H2（DNS rebinding 读密钥）**：攻击者域名先解析到自己的服务器、再 rebind 到 127.0.0.1。
  受害页面 fetch `http://evil.com:8010/api/user-state` 与页面同源 → GET 不带 Origin 头、
  `GET /api/user-state`（`backend/app/routers/user_state.py:54`）返回含各模型 apiKey 的
  settings → 响应可被攻击者页面直接读取。

根因相同：**回环门禁只认证「连接从哪来」，不认证「请求头声称自己是谁」**。
`main.py:76-78` 的注释已自认「CORS 只挡浏览器跨源，挡不住非浏览器客户端」——本改进把
「浏览器中介」这半句也补上，是对既有纵深防御思路的**补全**而非改向。

### 1.2 改进方法

**规则**（在 `_loopback_only` 中间件里、client-IP 判定之后追加两道闸）：

| 请求头 | 规则 | 拦截目标 |
|---|---|---|
| `Origin`（若存在） | 解析出的 host 必须是回环（`127.0.0.1` / `localhost` / `::1`，**端口不限**）；字面量 `null` 一律拒绝 | H1（跨站表单 POST 必带 Origin）+ H2 的跨端口变体 |
| `Host` | host 部分必须是回环或 `testserver`（见 §1.6 取舍③），**端口不限** | H2 的同端口 rebinding（rebind 后 GET 不带 Origin，但 Host 仍是攻击者域名） |

语义边界：

1. **`Origin` 缺失 = 放行**。非浏览器客户端（Tauri 壳 `http_probe.rs` 的裸 TCP 探活、后端
   httpx 自调用、curl）不发 Origin；同源 GET 导航/`<img>`/`<video>` 媒体加载也不发。
   这些是本设计刻意保留的信任面（单机无鉴权是既定设计，本闸只管浏览器中介）。
2. **`LAF_ALLOW_REMOTE=1` 时两道闸整体跳过**，与 client-IP 门禁同步开关。远端模式本就是
   「自担风险、无鉴权全放开」的显式逃生门（`main.py:78`），不做半吊子鉴权。
3. 拒绝响应沿用现有形状：`403` + `JSONResponse({"detail": ...})`，文案可操作
   （如「请求 Origin/Host 非本机，已拒绝（跨站请求？）」）。
4. 中间件顺序无需动：`@app.middleware("http")` 在内层、CORSMiddleware 在外层
   （后 add 者先执行）。CORS 预检 OPTIONS 由外层直接应答，不会进本闸；跨站简单请求
   进本闸被 403，CORS 不会给 403 补放行头，浏览器按网络错误处理——正合预期。

**代码归属**（遵循「接口即测试面」+ 单一属主）：

- 纯判定函数进**新模块 `app/services/request_guard.py`**（入站请求头校验属主；
  不并入 `url_guard.py`——那是「出站 fetch 目标校验」，关注点不同）：

```python
"""入站请求头安全闸：Origin/Host 必须声称来自本机（防浏览器 drive-by CSRF 与 DNS rebinding）。"""
from __future__ import annotations

from urllib.parse import urlparse

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
# Starlette TestClient 默认 Host；浏览器永不发送、非浏览器本机进程本就受信（见改进文档 §1.6 ③）
_TOOL_HOSTS = {"testserver"}


def origin_allowed(origin: str) -> bool:
    """Origin 头值是否放行：host 为回环（端口不限）；字面量 "null" 拒绝。"""
    value = (origin or "").strip()
    if value == "null":
        return False  # 沙箱 iframe（含攻击者构造的）发 null，宁可错杀
    try:
        host = (urlparse(value).hostname or "").lower()
    except ValueError:
        return False
    if host in _LOOPBACK_HOSTS:
        return True
    try:
        import ipaddress
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def host_allowed(host_header: str) -> bool:
    """Host 头是否放行：host 部分为回环（端口不限）或测试工具默认值。"""
    value = (host_header or "").strip()
    if not value:
        return False
    host = value.split(":", 1)[0].strip("[]").lower()
    return host in _LOOPBACK_HOSTS | _TOOL_HOSTS
```

- `main.py` 的 `_loopback_only` 里接线（约 10 行）：`_ALLOW_REMOTE` 为真直接跳过；
  否则取 `request.headers.get("origin")` / `request.headers.get("host")` 逐个过闸、不过即 403。
  不新增跨层 import（`main → services` 方向合法，import-linter 17 条契约不受影响）。

### 1.3 回归测试与验收（PreCompletion 口径）

现状：`_loopback_only` 中间件**零测试覆盖**（tests/ 无 `仅允许本机`/`LAF_ALLOW_REMOTE` 命中），
本次一并补齐。新增 `backend/tests/test_request_guard.py`：

- 纯函数网（`request_guard`）：回环三主机 × 任意端口放行；`127.0.0.5`（回环段非 127.0.0.1）
  放行；`evil.com` / `192.168.1.5` / `null` / 空串 / 非法串拒绝；`Host` 含端口、IPv6 方括号
  `[::1]:8010` 解析正确；`testserver` 放行。
- 中间件集成网（TestClient，走真实 app）：无 Origin 无特殊 Host（TestClient 默认）放行——
  **保住全部既有路由测试**；`Origin: http://127.0.0.1:5173`、`http://localhost:8010`、
  `http://127.0.0.1:15173`（e2e 端口）放行；`Origin: http://evil.com` 与 `Origin: null` 403；
  `Host: evil.com:8010` 403；`LAF_ALLOW_REMOTE=1` + `Origin: http://192.168.1.5:8010` 放行
  （env 需在 import 前××设置：沿用 `docs/memory/2026-08-12-visual-ci-implementation.md`
  记载的既有做法，或以纯函数 + 薄接线分测规避 reload 问题）。
- **真实失败路径回归**（实施后收敛为两类）：① 跨站 multipart 上传
  （`POST /api/user-state/wallpapers/upload`，files+data + `Origin: http://evil.com`）断言 403
  且文件不落盘——这是 FastAPI 严格 Content-Type 挡不住、真实可达的 drive-by 类；
  ② form 编码 + `Origin: http://evil.com` 打 `POST /api/comfyui/stop`（monkeypatch spy），
  断言 **403 而非 422**——即闸在 handler 前拦截，防线不依赖上游把 body 挡成 422 的版本行为；
  同请求换 `Origin: http://127.0.0.1:5173` + `application/json` 断言 200 且 spy 被调用
  （合法流量穿透）。
- 受控红检（变异预测）：删 Origin 闸 → 跨站 form 用例红；删 `null` 拒绝 → null 用例红；
  把「端口不限」写成固定端口表 → 15173 用例红。
- 运行态验证：`npm run dev`（5173）全功能走查（设置保存、聊天发送、SSE、ComfyUI 提交）；
  `npx playwright test --config playwright.e2e-manual.config.ts`（含 wallpaper-scene 系列，
  验证 blob: 同源播放器与 `<video>` local-view 加载不踩闸）；桌面壳双击启动（http_probe
  探活/复用判定正常）。
- 门禁：`ruff check app tests`；定向 pytest（新文件 + `tests/test_comfyui.py` 等受影响面）；
  `lint-imports`；`mypy`——**`main.py` 不在 `mypy_files.txt`**（清单只收零错误文件，已知口径），
  按条目 35/37 的先例对改动文件单跑 mypy 核对，不为本次扩清单。

### 1.4 与既有合同/记忆的冲突分析

| # | 既有约定（出处） | 冲突？ | 依据 / 化解 |
|---|---|---|---|
| 1 | 「单机 loopback 场景风险低」——早前安全走查结论（`docs/memory/topic-release-audit-test.md:171-173`：`local-view` 按设计可读任意媒体扩展名 + loopback 门禁 ⇒ 风险低） | **结论需更新，非合同冲突** | 该结论的前提「loopback 门禁挡住外部」被 H1/H2 推翻（浏览器中介不改变对端 IP）。按 AGENTS「文档与实际代码冲突时，先查代码取证，再修正文档并注明」处理：实施时在该 topic 追加 2026-10-24 更新条目，注明威胁模型演进与新增两道闸，**不删旧条原文**。 |
| 2 | 「浏览器直连必须继续可用，壳不是运行前提」（`ARCHITECTURE.md:523`，WPF 壳合同，Tauri 壳同语义） | 不冲突 | 浏览器直接导航 `http://127.0.0.1:8010/` 是 GET、不带 Origin → 放行；打开后的页面同源 POST 带 `Origin: http://127.0.0.1:8010` → 回环 → 放行。 |
| 3 | CORS 白名单仅 5173 两源（`main.py:98-104`）+ Playwright baseURL `127.0.0.1:15173`（`frontend/playwright.config.ts:13`） | 不冲突（**设计约束**） | Origin 闸只判「主机回环、端口不限」，与 CORS 端口白名单解耦。若写成固定端口表会同时挡掉 e2e（15173）与任意隔离端口——§1.3 受控红检已锁。 |
| 4 | TestClient 生态（tests/ 大量直连 app；默认 `Host: testserver`、无 Origin） | 不冲突（**设计约束**） | Origin 缺失放行 + Host 白名单收 `testserver`（理由见 §1.6 ③）。既有全部路由测试保持绿。 |
| 5 | `LAF_ALLOW_REMOTE=1` 自担风险逃生门（`main.py:76-79`） | 不冲突 | 三道闸（client-IP/Origin/Host）共用同一开关；远端模式语义保持「全放开」，不引入半吊子鉴权。 |
| 6 | 单机无鉴权是设计决定（`main.py:76-77` 注释；审计确认「无登录不是缺陷」） | 不冲突 | 本闸**不给 API 加身份认证**，只拒绝「声称来自非本机的浏览器请求」；本机进程仍零门槛（单用户桌面语义不变）。 |
| 7 | scene 壁纸播放器 iframe 不设 sandbox、与宿主同源（`frontend/src/lib/scene/scenePlayer.ts:229` 有注释说明动机；对应红线见手册 F 册壁纸章） | 不冲突 | blob: 文档 origin 即 `http://127.0.0.1:8010` → 播放器发请求带回环 Origin 或不带 Origin → 放行。§1.3 已把 wallpaper-scene e2e 列入必跑项。 |
| 8 | 架构合同「路由薄、服务深 / services 禁止依赖 routers」 | 不冲突 | 新逻辑落 `services/request_guard.py` 纯函数，`main.py` 只接线；无 routers→services 反向依赖。 |
| 9 | 双端 wire 合同「新事件同步更新协议/前端解码/reducer/双端测试」 | 不适用 | 无新事件、无 wire 变更，前端零改动。 |
| 10 | 隐私红线（真名/用户名不进源码与测试，根 `MEMORY.md` §5） | 不冲突 | 测试用例 host 一律用 `evil.com` / `192.168.1.5` 等中性值。 |

### 1.5 实施时的文档同步清单（AGENTS 双绑定规则）

1. `ARCHITECTURE.md` 深化日志追加条目（跨层决策先改合同真源）：内容 = H1/H2 根因、三道闸
   语义、`request_guard` 属主、红线（Origin 闸端口不限 / `null` 拒绝 / 远端模式整体跳过 /
   TestClient Host 白名单不可删）。
2. 手册：`docs/tech-manual/G-contracts-release/10-backend-auxiliary.md` 增「入站请求头安全闸」
   小节（与既有 `is_local_view_url` 红线并列）。
3. 记忆：`docs/memory/topic-release-audit-test.md` 追加威胁模型更新条目（见 §1.4 #1）。
4. 根 `MEMORY.md` 只按需加表格行（该文件已是索引，正文进 topic）。

### 1.6 已知取舍与明确不做的事

1. **不引入 token/会话鉴权**——与「单机无鉴权」设计决定冲突，且单用户桌面场景收益低。
2. **不做 Host 精确端口校验**——端口可由 `DEMIURGE_PORT`/`LAF_RUNTIME_PORT` 协商
   （`desktop/tauri/src/runtime_host.rs:44-56`），校验端口等于制造第二份端口真源（违反单一属主）。
3. **Host 白名单收 `testserver` 的理由**：Starlette TestClient 默认值，浏览器永不发送该值；
   能伪造 Host 的本机进程本来就在信任面内（它可以直接调 API）。这是「保住整个测试生态」
   与「零安全损失」的交点。
4. **`Origin: null` 拒绝**：sandboxed iframe（攻击者可低成本构造）发 null，放行等于给 CSRF
   留后门；本项目自有 blob: iframe（scene 播放器）的请求**要么无 Origin（GET）要么回环 Origin
   （POST）**，不受影响（§1.3 e2e 兜底验证）。
5. **残留窗口（接受）**：Origin/Host 校验防的是浏览器中介；本机恶意进程仍可直接调 API——
   这是单机无鉴权设计的固有边界，文档如实登记，不假装解决。
6. url_guard 的出站 TOCTOU（validate 与 fetch 二次解析）**不在本项范围**（审计建议第 3 项，
   另立刀）。

---

## 二、改进项 2：local-view 的 SVG 响应头加固

### 2.1 问题与证据

- `local-view` 按设计读任意本地路径、以扩展名白名单挡 LFI（`backend/app/services/url_guard.py:137-151`、
  `backend/app/routers/comfyui.py:338-356`）——白名单**含 `.svg`**（`url_guard.py:141`）。
- `local_media._media_type` 对 svg 产出 `image/svg`（`backend/app/services/local_media.py:95`，
  非标准类型），`FileResponse` 按此回发；浏览器对非标准 image/* 会内容嗅探，SVG 内嵌
  `<script>` 可被执行。
- 执行上下文是**应用同源**（`http://127.0.0.1:8010`）：用户在 webview 内直接打开/被诱导导航到
  一个本地 `.svg` 的 local-view URL ⇒ 脚本同源执行 ⇒ 可无鉴权调用全部本地 API（结合 H1 修掉后，
  这仍是残余的同源执行面）。
- **入库链真实存在**：`image_proxy.fetch_remote_image` 只要求 content-type `image/*`
  （`backend/app/services/image_proxy.py:66-68`），`image/svg+xml` 可通过 ⇒ 远程 svg 能经
  灵感卡/保存链路落盘，再经 local-view 服务——「本地文件必是用户自己的」不成立。

### 2.2 方案对比与推荐

| 方案 | 做法 | 优点 | 缺点 |
|---|---|---|---|
| A：白名单去 svg | `_MEDIA_EXTS` 与 `local_media._media_type` 同步删 svg | 最彻底 | **功能回退**：对话背景图允许用户填任意图片完整路径（`url_guard.py:138-139` 注释），svg 背景将 403；需动两处同步合同 |
| **B：响应头（推荐）** | 白名单不动；local-view 响应统一加 `X-Content-Type-Options: nosniff`，svg 额外加 `Content-Security-Policy: sandbox`；顺带把 `_media_type` 的 svg 修正为 `image/svg+xml` | 零功能回退：`<img>` 引用与静态浏览不受影响（img 上下本就不执行脚本；直接导航时 CSP sandbox 禁脚本/表单，静态图形照常渲染）；只动单一属主一处 | 无法阻止 svg 以外未来的内容型风险（可接受，白名单已收窄） |

推荐 **B**。nosniff 全量加（对 jpg/png 无副作用，杜绝所有嗅探面）；CSP sandbox 仅 svg
（对媒体流无意义且省头）。

### 2.3 改进方法

单一属主 = `local_media.LocalMedia.headers`（200 `FileResponse` 与 206 `StreamingResponse`
**两分支都从这一个属性取头**，`routers/comfyui.py:349-356`，改一处全通）：

```python
@property
def headers(self) -> dict[str, str]:
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(self.content_length),
        "Cache-Control": "no-cache",   # 原有注释与语义保持不动
        # 内容型安全：nosniff 杜绝嗅探；svg 直接导航时以 CSP sandbox 禁脚本/表单
        # （<img>/<video> 引用不受影响——媒体元素上下本就不执行脚本）。
        "X-Content-Type-Options": "nosniff",
    }
    if self.path.suffix.lower() == ".svg":
        headers["Content-Security-Policy"] = "sandbox"
    if self.partial:
        headers["Content-Range"] = f"bytes {self.start}-{self.end}/{self.file_size}"
    return headers
```

`_media_type` 同步修正：显式映射 `"svg": "image/svg+xml"`（当前走兜底 `image/svg` 是笔误级
非标准值；放入 `videos`/`audios` 同风格的显式小表）。**不动** `url_guard._MEDIA_EXTS`
（方案 B 不碰白名单，F 册同步合同零涉及）。

范围外（登记不修）：`GET /comfyui/view`（`routers/comfyui.py:287`）是 ComfyUI 自身响应的
透传，响应头属 ComfyUI；`/api/user-state/wallpapers/thumb` 输出恒为 `image/jpeg`
（`routers/user_state.py:232-237`）无 svg 面；`/api/user-state/upload-bg` 白名单本就不含 svg。

### 2.4 回归测试与验收

扩展 `backend/tests/test_local_media.py`（既有文件）+ 路由级用例：

- svg：`open_local_media` 产出的 headers 含 `CSP: sandbox` + `nosniff`，`media_type ==
  "image/svg+xml"`；路由级（TestClient 打 `/api/comfyui/local-view?path=<tmp>.svg`）断言响应头
  逐项在位（**真实失败路径**：恶意 svg 内嵌 `<script>` 文件 + 直接 GET，断言脚本无法执行的
  可观测代理 = sandbox 头存在）。
- png/mp4：无 CSP 头（不过度加）、有 `nosniff`；Range 206 分支头仍在（partial + svg 组合
  也不漏）。
- 既有行为锁全部保持：`Cache-Control: no-cache` 语义（`local_media.py:42-49` 的 ETag 协商
  设计）与 403/404/416 路径不变。
- 受控红检：删 `nosniff` → 全量用例红；删 svg 分支 → svg 用例红；`media_type` 回退
  `image/svg` → 类型断言红。
- 运行态：设置一张 svg 为对话背景 + 壁纸库浏览（`<img>`/`<video>` 加载正常）；
  `npm test`（前端零改动，跑定向即可）+ `vite build` 不必（无前端变更）。
- 门禁：同 §1.3（`local_media.py` 是否在 `mypy_files.txt` 同样核对，不在则单跑）。

### 2.5 与既有合同/记忆的冲突分析

| # | 既有约定（出处） | 冲突？ | 依据 / 化解 |
|---|---|---|---|
| 1 | 「`/local-view` 用扩展名白名单挡 LFI，**不得按目录 jail**（设计上要读任意本地路径）」（手册 `C-comfyui-media/04` §红线 10） | 不冲突 | 方案 B 只加响应头，不碰路径语义与白名单成员；jail 禁令针对「按目录收窄读取」，本改进未引入任何目录判定。 |
| 2 | 「`local_media` 的 MIME 表与 `url_guard._MEDIA_EXTS` **同步覆盖**」（手册 `F-frontend/11:148`，缺 `ogv` 曾致「能上传但 local-view 403」） | 不冲突 | 白名单零改动 ⇒ 同步合同零触及；`_media_type` 属 local_media 自有属主，修笔误（svg+xml）不涉 `upload-bg` 白名单（本就不含 svg）。若改选方案 A 则**必须**两侧同步 + 评估 svg 背景回退——这正是推荐 B 的原因。 |
| 3 | 「对话背景图允许用户填任意图片完整路径」（`url_guard.py:138-139` 设计注释） | 不冲突（**B 的选型依据**） | svg 背景继续可用；静态图形渲染不受 CSP sandbox 影响（sandbox 禁脚本/表单/插件，不禁绘制）。 |
| 4 | `local_media` 缓存头设计（`local_media.py:42-49`：`no-cache` 不可改 `no-store`，整库头像/背景/资产走本端点） | 不冲突 | 新头是**追加**键；`Cache-Control` 原值与 ETag 协商语义逐字保留（§2.4 行为锁兜底）。 |
| 5 | 灵感卡「远程 URL 预览经后端代理中转」（CONTEXT.md 灵感卡词条、M1.2） | 不冲突 | `image_proxy` 不动：svg 灵感卡预览照常经代理拉取（代理响应不落 webview 直航路径）；本项只封 local-view 的同源执行面。可选加强「代理拒 svg」会造成灵感卡 svg 预览回退，**明确不做**，登记为有意取舍。 |
| 6 | 架构合同「配置、协议、清洗规则和数据真源必须只有一个属主」 | 不冲突且**更符合** | 响应头收进 `LocalMedia.headers` 单点（原 200/206 两分支已同源取头，维持）；不在 router 层散加。 |
| 7 | 红线「`is_local_view_url` 必须主机 loopback + 路径含 `/comfyui/local-view`」（手册 `G/10:55`） | 不冲突 | 该函数语义不动；本项与它正交（它管出站代理豁免判定，本项管入站响应头）。 |

### 2.6 实施时的文档同步清单

1. `ARCHITECTURE.md`：深化日志条目（可与改进项 1 合并为「网络面加固」一条刀，或独立成条）。
2. 手册 `C-comfyui-media/04` §local-view 接口表与红线 10 处**追加一行**响应头合同
   （「nosniff 全量 + svg 加 CSP sandbox，属主 `LocalMedia.headers`」）。
3. 记忆：`docs/memory/topic-release-audit-test.md` 与 #1 同批登记（两条同源审计产出）。

---

## 三、两案共用的完成口径

- 顺序建议：**先做项 1**（H1/H2 是可利用链），项 2 随后或同刀；两案互不依赖、可独立验收。
- 判绿标准（AGENTS PreCompletion）：新增回归测试全绿；受影响既有套件（`test_local_media.py`、
  comfyui 路由相关、e2e-manual wallpaper 系列）全绿；`ruff` 0；`lint-imports` 17 kept 0 broken；
  mypy 对改动文件单跑核对（两文件均预计不在 `mypy_files.txt`，遵循条目 35 先例不扩清单）；
  `git diff --check` 干净。
- 报告须列：实际跑的测试与结果、运行态验证（5173 走查 + e2e + 壳启动）、剩余问题
  （§1.6 #5 的本机进程信任边界、url_guard 出站 TOCTOU 转后续刀）。
