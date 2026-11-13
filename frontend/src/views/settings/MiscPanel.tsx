import { Plus, Trash2 } from "lucide-react";
import type { Settings, UserPersona } from "../../stores/settings";
import type { PanelProps } from "./GeneralPanel";

export function MiscPanel({ draft, setDraft }: PanelProps) {
  const set = (patch: Partial<Settings>) => setDraft((d) => ({ ...d, ...patch }));
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
    <>
      <div className="settings-section">
        <h4>聊天室</h4>
        <p className="field-hint" style={{ margin: "0 0 12px" }}>
          私聊/群聊是独立会话线。这里的开关只影响聊天室，不改变剧情模式。
        </p>
        <label style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 16, padding: "2px 0", cursor: "pointer" }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>聊天室自动对话</div>
            <div style={{ fontSize: 12, color: "var(--text-muted)", lineHeight: 1.5 }}>
              开启后，打开私聊时若已有剧情沉淀、好感度够高且闲置够久，角色会主动来找你说话。
            </div>
          </div>
          <span className="regex-toggle" style={{ marginTop: 4, flexShrink: 0 }} title={draft.chatRoomAutoChat !== false ? "已启用" : "已禁用"}>
            <input
              type="checkbox"
              checked={draft.chatRoomAutoChat !== false}
              onChange={(e) => set({ chatRoomAutoChat: e.target.checked })}
            />
            <span className="regex-toggle-track" />
          </span>
        </label>
        <label style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 16, padding: "8px 0 2px", cursor: "pointer" }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>聊天室插入多元数据</div>
            <div style={{ fontSize: 12, color: "var(--text-muted)", lineHeight: 1.5 }}>
              开启后，角色可在聊天里主动发一张自拍/视频（先生成到作品资产库，再插进当前聊天）；仍受顶部「自动插画」和「多元数据」预设影响。
            </div>
          </div>
          <span className="regex-toggle" style={{ marginTop: 4, flexShrink: 0 }} title={draft.chatRoomMediaInsert === true ? "已启用" : "已禁用"}>
            <input
              type="checkbox"
              checked={draft.chatRoomMediaInsert === true}
              onChange={(e) => set({ chatRoomMediaInsert: e.target.checked })}
            />
            <span className="regex-toggle-track" />
          </span>
        </label>
        <label style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 16, padding: "8px 0 2px", cursor: "pointer" }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>聊天头像自动更新</div>
            <div style={{ fontSize: 12, color: "var(--text-muted)", lineHeight: 1.5 }}>
              开启后，角色认为有纪念意义的非常规装扮自拍会自动替换其聊天头像；普通自拍不会覆盖。关闭则不发起这类头像自拍，头像始终保持角色卡/底图。
            </div>
          </div>
          <span className="regex-toggle" style={{ marginTop: 4, flexShrink: 0 }} title={draft.chatRoomAvatarUpdate !== false ? "已启用" : "已禁用"}>
            <input
              type="checkbox"
              checked={draft.chatRoomAvatarUpdate !== false}
              onChange={(e) => set({ chatRoomAvatarUpdate: e.target.checked })}
            />
            <span className="regex-toggle-track" />
          </span>
        </label>
        <div style={{ marginTop: 14, paddingTop: 12, borderTop: "1px solid var(--border)" }}>
          <div style={{ fontWeight: 600, marginBottom: 6 }}>聊天节奏与预算</div>
          <div className="field-hint" style={{ margin: "0 0 8px" }}>
            这些是聊天室的行为上限；0 在对应项表示默认/关闭（见各标签）。
          </div>
          <div className="field-row">
            <div className="field">
              <label>防拦截头保留字符（0=默认 900）</label>
              <input
                type="number" min={0} max={5000} step={100}
                value={draft.chatGuardMaxChars ?? 900}
                onChange={(e) => set({ chatGuardMaxChars: Number(e.target.value) })}
              />
            </div>
            <div className="field">
              <label>聊天输出上限 tokens（0=跟随全局）</label>
              <input
                type="number" min={0} max={131072} step={256}
                value={draft.chatDefaultMaxTokens ?? 0}
                onChange={(e) => set({ chatDefaultMaxTokens: Number(e.target.value) })}
              />
            </div>
          </div>
          <div className="field-row">
            <div className="field">
              <label>追问次数上限（0=关闭追问）</label>
              <input
                type="number" min={0} max={10} step={1}
                value={draft.chatFollowupMax ?? 3}
                onChange={(e) => set({ chatFollowupMax: Number(e.target.value) })}
              />
            </div>
            <div className="field">
              <label>重开次数上限（0=关闭重开）</label>
              <input
                type="number" min={0} max={10} step={1}
                value={draft.chatReopenMax ?? 2}
                onChange={(e) => set({ chatReopenMax: Number(e.target.value) })}
              />
            </div>
          </div>
          <div className="field-row">
            <div className="field">
              <label>主动找上门闲置（分钟，0=不设门槛）</label>
              <input
                type="number" min={0} max={10080} step={30}
                value={draft.chatInitiativeIdleMinutes ?? 120}
                onChange={(e) => set({ chatInitiativeIdleMinutes: Number(e.target.value) })}
              />
            </div>
            <div className="field">
              <label>主动消息冷却（分钟，0=不设冷却）</label>
              <input
                type="number" min={0} max={10080} step={30}
                value={draft.chatInitiativeCooldownMinutes ?? 360}
                onChange={(e) => set({ chatInitiativeCooldownMinutes: Number(e.target.value) })}
              />
            </div>
            <div className="field">
              <label>每日主动上限（次，0=不限）</label>
              <input
                type="number" min={0} max={100} step={1}
                value={draft.chatInitiativeDailyMax ?? 6}
                onChange={(e) => set({ chatInitiativeDailyMax: Number(e.target.value) })}
              />
            </div>
          </div>
          <div className="field-row">
            <div className="field">
              <label>追问/重开频率</label>
              <select
                value={draft.chatCadence ?? "normal"}
                onChange={(e) => set({
                  chatCadence: e.target.value as "quiet" | "normal" | "frequent",
                })}
              >
                <option value="quiet">安静（间隔更长）</option>
                <option value="normal">正常</option>
                <option value="frequent">频繁（间隔更短）</option>
              </select>
            </div>
            <div className="field">
              <label>群聊活跃度</label>
              <select
                value={draft.chatGroupActivity ?? "normal"}
                onChange={(e) => set({
                  chatGroupActivity: e.target.value as "quiet" | "normal" | "lively",
                })}
              >
                <option value="quiet">安静（少人少话）</option>
                <option value="normal">正常</option>
                <option value="lively">热闹（更多接话）</option>
              </select>
            </div>
          </div>
          <div className="field-row">
            <div className="field">
              <label>会话窗口（分钟）</label>
              <input
                type="number" min={0} max={1440} step={5}
                value={draft.chatActiveWindowMinutes ?? 15}
                onChange={(e) => set({ chatActiveWindowMinutes: Number(e.target.value) })}
              />
            </div>
            <div className="field">
              <label>连续 bot 气泡上限</label>
              <input
                type="number" min={1} max={50} step={1}
                value={draft.chatMaxBotTurns ?? 5}
                onChange={(e) => set({ chatMaxBotTurns: Number(e.target.value) })}
              />
            </div>
            <div className="field">
              <label>主动/追问最低好感度</label>
              <input
                type="number" min={-100} max={100} step={5}
                value={draft.chatMinAffinity ?? 30}
                onChange={(e) => set({ chatMinAffinity: Number(e.target.value) })}
              />
            </div>
          </div>
          <div className="field-row">
            <div className="field">
              <label>深夜安静开始（空=关闭）</label>
              <input
                type="time"
                value={draft.chatQuietStart ?? "00:30"}
                onChange={(e) => set({ chatQuietStart: e.target.value })}
              />
            </div>
            <div className="field">
              <label>深夜安静结束（空=关闭）</label>
              <input
                type="time"
                value={draft.chatQuietEnd ?? "08:30"}
                onChange={(e) => set({ chatQuietEnd: e.target.value })}
              />
            </div>
          </div>
        </div>
      </div>
      <div className="settings-section">
        <h4>资产库</h4>
        <p className="field-hint" style={{ margin: "0 0 12px" }}>
          控制资产库删除图片记录时的行为。开启后，删除资产库条目时一并删除本机图片文件，不再弹窗询问。
        </p>
        <label style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 16, padding: "2px 0", cursor: "pointer" }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>资产库删除时默认删除本机文件</div>
            <div style={{ fontSize: 12, color: "var(--text-muted)", lineHeight: 1.5 }}>
              开启：直接删除本机图片文件。关闭：只移除资产库记录，保留本机文件。
            </div>
          </div>
          <span className="regex-toggle" style={{ marginTop: 4, flexShrink: 0 }} title={draft.galleryRemoveFile ? "已启用" : "已禁用"}>
            <input
              type="checkbox"
              checked={draft.galleryRemoveFile === true}
              onChange={(e) => set({ galleryRemoveFile: e.target.checked })}
            />
            <span className="regex-toggle-track" />
          </span>
        </label>
      </div>
      <div className="settings-section">
        <div className="settings-actions-head" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <h4 style={{ margin: 0 }}>用户人设（可多档，选中的注入剧情）</h4>
          <button className="btn" onClick={addPersona}>
            <Plus size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />新增人设
          </button>
        </div>
        <p className="field-hint" style={{ marginTop: 8 }}>
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
    </>
  );
}
