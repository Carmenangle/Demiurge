import { Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { fetchProxyStatus, type ProxyStatus } from "../../api/userState";
import { probeComfyHosts } from "../../api/comfyui";
import { loadMobileRemote } from "../../api/mobileRemote";
import { normalizeSearchProvider, SEARCH_PROVIDER_OPTIONS, type SearchProvider } from "../../lib/searchSource";
import { DEFAULT_MACHINE_ID, isLoopbackComfyUrl, listMachines, resolveComfyRouteIds } from "../../lib/comfyMachines";
import type { ComfyMachine, ComfyRoute, GbcEngine } from "../../stores/settings";
import { DEFAULT_GBC } from "../../stores/settings";
import type { PanelProps } from "./GeneralPanel";
import { MOBILE_PROFILE } from "../../lib/mobileProfile";

// 按模态选机的三个下拉（地址簿面板）。label 是玩家视角的产物名。
const ROUTE_ROWS: { key: keyof ComfyRoute; label: string }[] = [
  { key: "image", label: "图片" },
  { key: "video", label: "视频" },
  { key: "audio", label: "配音" },
];

// 生效来源 → 界面用词（后端 system=Windows 系统代理、env=环境变量、manual=手填兜底）
const PROXY_SOURCE_LABEL: Record<string, string> = {
  system: "系统代理", env: "环境变量代理", manual: "手填地址", none: "无",
};

// 把探测结果拼成一句人话：要么说明「已启用 + 来源 + 地址」，要么说明「为什么没启用」。
function proxySummary(st: ProxyStatus | null, checking: boolean): string {
  if (!st) return checking ? "检测中…" : "尚未检测";
  const source = PROXY_SOURCE_LABEL[st.source] || st.source;
  if (st.listening) {
    const net = st.search_reachable === true
      ? " · 搜索可用"
      : st.search_reachable === false
        ? ` · 搜不到结果（${st.search_detail || "网络或搜索源不可用"}）`
        : "";
    return `已启用：${source} ${st.address}${net}`;
  }
  const hint = st.system_address
    ? `系统代理指向 ${st.system_address}，但本机没有进程在监听`
    : st.manual_address
      ? `手填地址 ${st.manual_address} 未在监听`
      : "本机未检测到系统代理";
  // 没代理不等于搜不到：直连能搜到就不用折腾代理
  const direct = st.search_reachable === true ? "；但直连可以搜到结果（当前无需代理）" : "";
  return `未启用：${hint}，将直连外网${direct}`;
}

export function PathsPanel({ draft, setDraft }: PanelProps) {
  const [proxyProbe, setProxyProbe] = useState<{ checking: boolean; status: ProxyStatus | null }>(
    { checking: false, status: null },
  );
  // P15（2026-09-15）：仓库文件夹为空时自动定位 + 高亮该输入框——引导弹窗「去配置」跳过来后
  // 用户一眼看到该填哪一格（后端已把作品根校验统一为「未配置即拒绝」）。填了就不打扰。
  const [highlightOutputDir, setHighlightOutputDir] = useState(false);
  useEffect(() => {
    if ((draft.outputDir || "").trim()) return;
    setHighlightOutputDir(true);
    document.getElementById("setting-output-dir")?.scrollIntoView({ block: "center" });
    const timer = setTimeout(() => setHighlightOutputDir(false), 2500);
    return () => clearTimeout(timer);
    // 仅在面板挂载时判定一次：之后用户自己输入不该反复抢焦点
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // search=true 时后端会额外真跑一次最小搜索（数秒）；挂载时只做快速探活。
  const probeProxy = async (search = false) => {
    setProxyProbe((p) => ({ ...p, checking: true }));
    try {
      const st = await fetchProxyStatus({ search });
      setProxyProbe({ checking: false, status: st });
      // 回写草稿：proxyEnabled=是否探到可用代理（自动），systemProxyUrl 供 effectiveGlobalProxyUrl 取用
      setDraft((d) => ({
        ...d,
        proxyEnabled: st.listening,
        systemProxyUrl: (st.source === "system" || st.source === "env") ? st.address : "",
        systemProxySource: st.source,
      }));
    } catch {
      setProxyProbe((p) => ({ ...p, checking: false }));
    }
  };
  useEffect(() => { void probeProxy(); /* eslint-disable-line react-hooks/exhaustive-deps */ }, []);
  // 联机同库时地址簿真源在电脑上：本机这份只是镜像，改了既会被下次同步覆盖，
  // 登记端点（/comfyui/hosts）也是 PC 独占、代理过去只会 403 ⇒ 只读。
  // null＝还没读到：先按只读渲染，宁可慢一拍也不让人改一个保存必 403 的表。
  const [mobileRemoteActive, setMobileRemoteActive] = useState<boolean | null>(null);
  useEffect(() => {
    if (!MOBILE_PROFILE) return;  // PC 后端不注册 /mobile/remote，别去撞 404
    let alive = true;
    let timer: number | undefined;
    const load = async (attempt: number): Promise<void> => {
      try {
        const cfg = await loadMobileRemote();
        if (alive) setMobileRemoteActive(cfg.active);
      } catch {
        if (!alive) return;
        // 本机后端自己答的端点，失败多为开机瞬间的抖动 ⇒ 重试两次再回落到可编辑
        if (attempt < 2) {
          timer = window.setTimeout(() => { void load(attempt + 1); }, 1200);
        } else {
          setMobileRemoteActive(false);
        }
      }
    };
    void load(0);
    return () => { alive = false; if (timer !== undefined) window.clearTimeout(timer); };
  }, []);
  const machinesReadOnly = MOBILE_PROFILE && mobileRemoteActive !== false;
  // 额外机台的连通性探测结果（id → 状态）。探测走 /comfyui/status，不需要先进白名单。
  const [machineProbe, setMachineProbe] = useState<
    Record<string, { checking: boolean; online?: boolean; running?: number; pending?: number; detail?: string }>
  >({});
  const probeMachine = async (id: string, url: string) => {
    const target = url.trim();
    if (!target) return;
    setMachineProbe((p) => ({ ...p, [id]: { checking: true } }));
    try {
      const result = await probeComfyHosts([target]);
      const item = result.items.find((entry) => entry.url === target);
      setMachineProbe((p) => ({
        ...p,
        [id]: {
          checking: false,
          online: !!item?.online,
          running: item?.running,
          pending: item?.pending,
          ...(item && !item.online ? { detail: item.error || "连不上" } : {}),
        },
      }));
    } catch (reason) {
      setMachineProbe((p) => ({
        ...p, [id]: { checking: false, online: false, detail: (reason as Error).message },
      }));
    }
  };
  // ---- ComfyUI 地址簿（条目 0 就是上面那个「访问地址」，这里只管额外机台）----
  const extras = draft.comfyExtra || [];
  const machines = listMachines(draft);
  const addMachine = () => {
    const id = crypto.randomUUID();
    setDraft((d) => ({ ...d, comfyExtra: [...(d.comfyExtra || []), { id, name: "", url: "" }] }));
  };
  const updateMachine = (id: string, patch: Partial<ComfyMachine>) =>
    setDraft((d) => ({
      ...d,
      comfyExtra: (d.comfyExtra || []).map((m) => (m.id === id ? { ...m, ...patch } : m)),
    }));
  const removeMachine = (id: string) => {
    setDraft((d) => {
      // 路由指着这台机的话一并清掉：留死引用会让多选框显示成「默认」，与实际解析不符
      const nextRoute: ComfyRoute = { ...(d.comfyRoute || {}) };
      for (const row of ROUTE_ROWS) {
        const kept = resolveComfyRouteIds(d, row.key).filter((item) => item !== id);
        if (kept.length) nextRoute[row.key] = kept;
        else delete nextRoute[row.key];
      }
      return {
        ...d,
        comfyExtra: (d.comfyExtra || []).filter((m) => m.id !== id),
        comfyRoute: nextRoute,
      };
    });
    setMachineProbe((p) => {
      const next = { ...p };
      delete next[id];
      return next;
    });
  };
  const toggleRoute = (key: keyof ComfyRoute, id: string) =>
    setDraft((d) => {
      const current = resolveComfyRouteIds(d, key);
      // 空列表语义＝默认机台；勾选时以默认机台为基底，取消它=只留其它机台。
      const base = current.length ? current : [DEFAULT_MACHINE_ID];
      const next = base.includes(id) ? base.filter((item) => item !== id) : [...base, id];
      const cleaned = next.length ? next : [DEFAULT_MACHINE_ID];
      const nextRoute: ComfyRoute = { ...(d.comfyRoute || {}) };
      if (cleaned.length === 1 && cleaned[0] === DEFAULT_MACHINE_ID) delete nextRoute[key];
      else nextRoute[key] = cleaned;
      return { ...d, comfyRoute: nextRoute };
    });
  const gbc: GbcEngine = draft.gbc ?? DEFAULT_GBC;
  const patchGbc = (patch: Partial<GbcEngine>) =>
    setDraft((d) => ({ ...d, gbc: { ...(d.gbc ?? DEFAULT_GBC), ...patch } }));
  return (
    <div className="settings-section">
      <h4>路径</h4>
      <div className="field">
        <label>工作流默认读取路径</label>
        <input
          value={draft.workflowDir}
          onChange={(e) => setDraft((d) => ({ ...d, workflowDir: e.target.value }))}
          placeholder="D:\\ComfyUI\\workflows"
        />
      </div>
      <div
        className="field"
        id="setting-output-dir"
        style={highlightOutputDir ? {
          outline: "2px solid var(--accent, #6F7F5D)",
          outlineOffset: 4,
          borderRadius: 8,
          transition: "outline-color 0.4s",
        } : undefined}
      >
        <label>仓库文件夹（图片 + 会话记录）</label>
        <input
          value={draft.outputDir}
          onChange={(e) => setDraft((d) => ({ ...d, outputDir: e.target.value }))}
          placeholder="D:\\ComfyUI\\output"
          autoFocus={highlightOutputDir}
        />
        <p className="field-hint">
          每个作品的私有产物都落此目录下的作品文件夹：生成图片、会话记录、好感度状态、往事纪要、参考图。
          与 ComfyUI 共用 output 目录时，只搬带标记的作品文件夹，不碰 ComfyUI 自有输出。改此路径会整体迁移并重写引用。
        </p>
      </div>
      <div className="field">
        <label>角色卡文件夹</label>
        <input
          value={draft.characterDir}
          onChange={(e) => setDraft((d) => ({ ...d, characterDir: e.target.value }))}
          placeholder="D:\\Demiurge\\characters"
        />
        <p className="field-hint">
          导入的角色卡按「小仓库」存进此目录：每张卡一个文件夹，含卡本体、内嵌世界书/正则与对话记录。
        </p>
      </div>
      <div className="field">
        <label>世界书文件夹</label>
        <input
          value={draft.worldbookDir}
          onChange={(e) => setDraft((d) => ({ ...d, worldbookDir: e.target.value }))}
          placeholder="D:\\Demiurge\\worldbooks"
        />
        <p className="field-hint">独立世界书文件存放目录。</p>
      </div>
      <div className="field">
        <label>偏置预设文件夹</label>
        <input
          value={draft.presetDir}
          onChange={(e) => setDraft((d) => ({ ...d, presetDir: e.target.value }))}
          placeholder="D:\\Demiurge\\presets"
        />
        <p className="field-hint">SillyTavern OpenAI 预设存放目录（仅剧情模式用）。</p>
      </div>
      <div className="field">
        <label>ComfyUI 目录（含 main.py）</label>
        <input
          value={draft.comfyuiPath}
          onChange={(e) => setDraft((d) => ({ ...d, comfyuiPath: e.target.value }))}
          placeholder="D:\\tool\\ComfyUI\\ComfyUI_aaaki\\ComfyUI"
        />
      </div>
      <div className="field">
        <label>ComfyUI 访问地址</label>
        <input
          value={draft.comfyuiUrl}
          onChange={(e) => setDraft((d) => ({ ...d, comfyuiUrl: e.target.value }))}
          placeholder="http://127.0.0.1:8188"
        />
        {MOBILE_PROFILE ? (
          <p className="field-hint">
            手机端两种填法：① 联机同库时一般不用改（地址随电脑数据走，生成由电脑上的
            ComfyUI 执行）；② 不联机直连：填电脑的局域网/Tailscale 地址
            （http://电脑IP:8188），或一台可公网访问的云端 ComfyUI（https://…，推荐 https）。
            保存时自动登记进本机后端白名单；未登记的地址提交会被拒绝。
          </p>
        ) : (
          <p className="field-hint">
            非本机地址（局域网 / Tailscale / 云端 https）保存时会自动登记进本机后端
            白名单并落盘，重启后仍生效；白名单上限 8 台，登记失败时保存照常成功但
            提交会被拒绝（原因会提示）。
          </p>
        )}
        {!isLoopbackComfyUrl(draft.comfyuiUrl) && (
          <p className="field-hint" style={{ color: "var(--warning, #b45309)" }}>
            当前默认地址不在本机：<strong>原生画布不可用</strong>（画布只嵌本机
            ComfyUI，非回环地址会被浏览器安全策略挡白）。对话里出图 / 视频 / 配音
            不受影响；要用画布或手动运转工作流，请把默认地址填回
            <code> http://127.0.0.1:8188</code>，其他机台放到下面的地址簿里。
          </p>
        )}
      </div>
      <div className="field">
        <div
          className="settings-actions-head"
          style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}
        >
          <label style={{ margin: 0 }}>ComfyUI 机台（地址簿）</label>
          <button className="btn" onClick={addMachine} disabled={machinesReadOnly}>
            <Plus size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />新增机台
          </button>
        </div>
        <p className="field-hint">
          上面那个「访问地址」就是默认机台。这里加额外机台：
          <strong>名字只是标签，地址才是真值</strong>——历史和重新生成记的是地址，
          改名不会断历史。保存时自动登记进本机后端白名单。
        </p>
        {mobileRemoteActive === true && (
          <p className="field-hint" style={{ color: "var(--warning, #b45309)" }}>
            联机中：地址簿的真源在电脑上，请到电脑端修改（本机这份会被同步覆盖）。
          </p>
        )}
        {extras.length === 0 && (
          <p className="field-hint" style={{ opacity: 0.7 }}>还没有额外机台。</p>
        )}
        {extras.map((m) => {
          const probe = machineProbe[m.id];
          return (
            <div
              key={m.id}
              style={{ border: "1px solid var(--border, #ccc)", borderRadius: 8, padding: 10, marginTop: 8 }}
            >
              <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 6 }}>
                <input
                  style={{ flex: 1 }}
                  value={m.name}
                  onChange={(e) => updateMachine(m.id, { name: e.target.value })}
                  placeholder="机台名（如 云端 A100）"
                  disabled={machinesReadOnly}
                />
                <button
                  className="btn"
                  onClick={() => void probeMachine(m.id, m.url)}
                  disabled={machinesReadOnly || !m.url.trim() || !!probe?.checking}
                >
                  {probe?.checking ? "探测中…" : "探测"}
                </button>
                <button
                  className="btn"
                  onClick={() => removeMachine(m.id)}
                  disabled={machinesReadOnly}
                  title="删除这台机台（保存后同时注销白名单）"
                >
                  <Trash2 size={15} />
                </button>
              </div>
              <input
                style={{ width: "100%" }}
                value={m.url}
                onChange={(e) => updateMachine(m.id, { url: e.target.value })}
                placeholder="http://192.168.1.9:8188 或 https://…"
                disabled={machinesReadOnly}
              />
              {probe && !probe.checking && (
                <p className="field-hint">
                  {probe.online
                    ? `已连通：运行中 ${probe.running ?? 0} / 排队 ${probe.pending ?? 0}（分镜任务会优先分给队列短的机台）`
                    : `连不上：${probe.detail || "地址不通或 ComfyUI 未启动"}`}
                </p>
              )}
            </div>
          );
        })}
        <div style={{ display: "flex", gap: 16, flexWrap: "wrap", marginTop: 10 }}>
          {ROUTE_ROWS.map((row) => {
            const selectedIds = resolveComfyRouteIds(draft, row.key)
              .filter((id) => machines.some((machine) => machine.id === id));
            // 空/全未知 ⇒ 默认机台；勾选可多台，分镜任务按队列负载分发。
            const effective = selectedIds.length ? selectedIds : [DEFAULT_MACHINE_ID];
            return (
              <div key={row.key} aria-label={`${row.label}机台`} style={{ marginTop: 6 }}>
                <span style={{ marginRight: 8 }}>{row.label}</span>
                {machines.map((machine) => {
                  const label = machine.id === DEFAULT_MACHINE_ID
                    ? `默认（${machine.url || "未填写"}）`
                    : `${machine.name}（${machine.url}）`;
                  return (
                    <label key={machine.id} style={{ display: "inline-flex", alignItems: "center", gap: 4, marginRight: 12 }}>
                      <input
                        type="checkbox"
                        checked={effective.includes(machine.id)}
                        onChange={() => toggleRoute(row.key, machine.id)}
                        disabled={machinesReadOnly}
                      />
                      {label}
                    </label>
                  );
                })}
              </div>
            );
          })}
        </div>
        <p className="field-hint">
          对话里按模态选机：图片 / 视频 / 配音可各选**多台**；分镜任务会探测各机队列，
          优先分给运行中/排队短的机台并行出片。全不选＝默认机台。
          跨机协作时帧图会由本工具在机台之间搬运（视频机无需能访问图片机）。
          画布与手动运转工作流永远只走默认机台。
        </p>
      </div>
      <div className="field">
        <label>ComfyUI Python（可选）</label>
        <input
          value={draft.comfyuiPython || ""}
          onChange={(e) => setDraft((d) => ({ ...d, comfyuiPython: e.target.value }))}
          placeholder="D:\\ComfyUI\\.venv\\Scripts\\python.exe"
        />
        <p className="field-hint">
          留空时自动查找 ComfyUI 整合包或 .venv/venv；不会使用本工具的 Python。
        </p>
      </div>
      <div className="field">
        <label>GBC 场景图引擎（外部工具）</label>
        <input
          value={gbc.exePath}
          onChange={(e) => patchGbc({ exePath: e.target.value })}
          placeholder="D:\\tool\\KohakuGBC\\.venv\\Scripts\\gbc.exe"
        />
        <div style={{ display: "flex", gap: 8, marginTop: 6, flexWrap: "wrap" }}>
          <input
            style={{ flex: 1, minWidth: 220 }}
            value={gbc.sam3Dir}
            onChange={(e) => patchGbc({ sam3Dir: e.target.value })}
            placeholder="SAM-3 权重目录（含 *.safetensors）"
          />
          <input
            style={{ flex: 1, minWidth: 220 }}
            value={gbc.embedDir}
            onChange={(e) => patchGbc({ embedDir: e.target.value })}
            placeholder="EmbeddingGemma 权重目录"
          />
        </div>
        <div style={{ display: "flex", gap: 16, alignItems: "center", marginTop: 6, flexWrap: "wrap" }}>
          <label style={{ display: "flex", alignItems: "center", gap: 6, margin: 0 }}>
            检测设备
            <select
              value={gbc.device}
              onChange={(e) => patchGbc({ device: e.target.value === "cpu" ? "cpu" : "cuda" })}
            >
              <option value="cuda">cuda（快，约 5 GB 显存）</option>
              <option value="cpu">cpu（慢，不占显存）</option>
            </select>
          </label>
          <label style={{ display: "flex", alignItems: "center", gap: 6, margin: 0 }}>
            最多分析物体数
            <input
              type="number"
              min={1}
              max={20}
              style={{ width: 72 }}
              value={gbc.maxTopLevel}
              onChange={(e) => patchGbc({ maxTopLevel: Number(e.target.value) || 1 })}
            />
          </label>
        </div>
        <p className="field-hint">
          GBC 不是「丢一个模型目录」就能跑的打标器：它要自己那套 Python + torch，
          外加 SAM-3 与 EmbeddingGemma 两份权重（合计约 4 GB），所以不进安装包，
          这里填本机路径即可（网络共享盘也能填）。VLM 复用「设置 → 模型 → 视觉大模型」：
          本地 Ollama 与 SAM-3 同时驻显存可能放不下（8 GB 卡建议检测设备选 cpu，或用云端 VLM）。
        </p>
      </div>
      <div className="field">
        <label>全局代理（继承本机系统代理）</label>
        {MOBILE_PROFILE && (
          <p className="field-hint">
            手机端：Clash 等代理 App 用「VPN 模式」时**无需配置代理地址**，直连流量即被
            接管；只有代理 App 开「仅代理/允许局域网」模式才需要填端口。PC 上配置的
            代理地址不会在手机上生效（已自动探活降级直连）。
          </p>
        )}
        <input
          value={draft.proxyUrl}
          onChange={(e) => setDraft((d) => ({ ...d, proxyUrl: e.target.value }))}
          placeholder="http://127.0.0.1:7897"
        />
        <p className="field-hint">
          代理开关已移除：各模型选「直连 / 使用代理 / 继承全局」。
          「继承全局」= 实时读<strong>本机系统代理</strong>（Windows 代理设置；读不到再看环境变量），
          探到可用就走代理、没有就自动直连。上面这个地址是<strong>表单兜底</strong>——
          系统没配代理时才用它。
        </p>
        <p className="field-hint" style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span>当前检测：{proxySummary(proxyProbe.status, proxyProbe.checking)}</span>
          <button className="btn" onClick={() => void probeProxy(true)} disabled={proxyProbe.checking}>
            {proxyProbe.checking ? "检测中…" : "重新检测（含外网连通）"}
          </button>
        </p>
      </div>
      <div className="field">
        <label>联网搜索源</label>
        <select
          value={normalizeSearchProvider(draft.searchProvider)}
          onChange={(e) => setDraft((d) => ({ ...d, searchProvider: e.target.value as SearchProvider }))}
        >
          {SEARCH_PROVIDER_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
        <p className="field-hint">
          {SEARCH_PROVIDER_OPTIONS.find((o) => o.value === normalizeSearchProvider(draft.searchProvider))?.hint}
        </p>
        <p className="field-hint">
          「找灵感」与联网检索走这里。两个源都存在时选<strong>自动</strong>即可：
          后端按 Bing 中国 → DuckDuckGo 顺序回落，谁有结果用谁。
          国内直连就能用 Bing 中国，所以<strong>不开代理也能找灵感</strong>；
          DuckDuckGo 只在有代理时连得上，结果更国际化，可手动指定。
        </p>
      </div>
      <div className="field">
        <label>模型目录（models）</label>
        <input
          value={draft.modelsDir}
          onChange={(e) => setDraft((d) => ({ ...d, modelsDir: e.target.value }))}
          placeholder="D:\\tool\\ComfyUI\\...\\ComfyUI\\models"
        />
        <p className="field-hint">
          下载的模型按类型存进此目录的子文件夹（checkpoints/loras/vae 等），ComfyUI 原生识别。
        </p>
      </div>
    </div>
  );
}
