import { Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { fetchProxyStatus } from "../../api/userState";
import type { UserPersona } from "../../stores/settings";
import type { PanelProps } from "./GeneralPanel";

export function PathsPanel({ draft, setDraft }: PanelProps) {
  const [proxyProbe, setProxyProbe] = useState<{ listening: boolean; checking: boolean }>(
    { listening: !!draft.proxyEnabled, checking: false },
  );
  const probeProxy = async () => {
    setProxyProbe((p) => ({ ...p, checking: true }));
    try {
      const st = await fetchProxyStatus();
      setProxyProbe({ listening: st.listening, checking: false });
      setDraft((d) => ({ ...d, proxyEnabled: st.listening }));
    } catch {
      setProxyProbe((p) => ({ ...p, checking: false }));
    }
  };
  useEffect(() => { void probeProxy(); /* eslint-disable-line react-hooks/exhaustive-deps */ }, []);
  const personas = draft.userPersonas || [];
  const addPersona = () => {
    const id = crypto.randomUUID();
    setDraft((d) => ({
      ...d,
      userPersonas: [...(d.userPersonas || []), { id, name: "", content: "" }],
      activeUserPersonaId: d.activeUserPersonaId || id,  // 首个自动选中
    }));
  };
  const updatePersona = (id: string, patch: Partial<UserPersona>) =>
    setDraft((d) => ({
      ...d,
      userPersonas: (d.userPersonas || []).map((p) => (p.id === id ? { ...p, ...patch } : p)),
    }));
  const removePersona = (id: string) =>
    setDraft((d) => ({
      ...d,
      userPersonas: (d.userPersonas || []).filter((p) => p.id !== id),
      activeUserPersonaId: d.activeUserPersonaId === id ? undefined : d.activeUserPersonaId,
    }));
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
      <div className="field">
        <label>仓库文件夹（图片 + 会话记录）</label>
        <input
          value={draft.outputDir}
          onChange={(e) => setDraft((d) => ({ ...d, outputDir: e.target.value }))}
          placeholder="D:\\ComfyUI\\output"
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
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <label style={{ margin: 0 }}>用户人设（可多档，选中的注入剧情）</label>
          <button className="btn" onClick={addPersona}>
            <Plus size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />新增人设
          </button>
        </div>
        <p className="field-hint">
          定制多个「我是谁」，按情况自由切换。选中档的名字填 {"{{user}}"} 宏、描述让角色知道用户是谁。不选则不注入用户人设。
        </p>
        {personas.length === 0 && (
          <p className="field-hint" style={{ opacity: 0.7 }}>还没有用户人设，点「新增人设」创建一个。</p>
        )}
        {personas.map((p) => {
          const active = draft.activeUserPersonaId === p.id;
          return (
            <div
              key={p.id}
              style={{
                border: active ? "2px solid var(--accent, #6F7F5D)" : "1px solid var(--border, #ccc)",
                borderRadius: 8, padding: 10, marginTop: 8,
              }}
            >
              <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 6 }}>
                <label style={{ display: "flex", alignItems: "center", gap: 4, margin: 0, cursor: "pointer" }}>
                  <input
                    type="radio"
                    name="active-user-persona"
                    checked={active}
                    onChange={() => setDraft((d) => ({ ...d, activeUserPersonaId: p.id }))}
                  />
                  {active ? "使用中" : "设为使用"}
                </label>
                <input
                  style={{ flex: 1 }}
                  value={p.name}
                  onChange={(e) => updatePersona(p.id, { name: e.target.value })}
                  placeholder="人设名（填 {{user}}）"
                />
                <button className="btn" onClick={() => removePersona(p.id)} title="删除此人设">
                  <Trash2 size={15} />
                </button>
              </div>
              <textarea
                value={p.content}
                onChange={(e) => updatePersona(p.id, { content: e.target.value })}
                placeholder="你是谁：身份、外貌、性格等。让角色知道'用户是谁'。"
                rows={3}
                style={{ width: "100%" }}
              />
            </div>
          );
        })}
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
        <label>全局代理地址</label>
        <input
          value={draft.proxyUrl}
          onChange={(e) => setDraft((d) => ({ ...d, proxyUrl: e.target.value }))}
          placeholder="http://127.0.0.1:7897"
        />
        <p className="field-hint">
          代理开关已移除：各模型选「直连 / 使用代理 / 继承全局」；继承全局会实时检测
          本机该地址是否在听——在听走代理，没在听自动直连。
        </p>
        <p className="field-hint" style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span>
            当前检测：{proxyProbe.checking ? "检测中…" : proxyProbe.listening ? "代理已开启，继承全局走代理" : "代理未开启，继承全局走直连"}
          </span>
          <button className="btn" onClick={() => void probeProxy()} disabled={proxyProbe.checking}>
            重新检测
          </button>
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
