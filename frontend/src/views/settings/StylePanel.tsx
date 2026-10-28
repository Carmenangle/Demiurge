import { useCallback, useEffect, useState } from "react";
import { Pencil, Plus } from "lucide-react";
import type { PanelProps } from "./GeneralPanel";
import { PromptTemplateModal, TemplateStructureView } from "../../components/PromptTemplateModal";
import {
  KIND_LABELS, listPromptTemplates,
  type PromptTemplate,
} from "../../api/ai/promptTemplates";
import {
  templateIsStale, templateKindGroup, type TemplateGroup,
} from "../../lib/promptTemplates";

// 提示词模版（2026-10-25）：原「生图风格存档」整改为提示词模版。
// 内置默认模版**直接公开全文**（只读）——进设置页就能读到，不用点开弹窗；
// 「新增模版」分别放在「生图提示词」「视频提示词」两个分组标题右侧，克隆后编辑。
const SECTIONS: {
  group: TemplateGroup;
  title: string;
  hint: string;
}[] = [
  {
    group: "image",
    title: "生图提示词模版",
    hint: "决定生图提示词的输出结构与文案：Anima 固定两行（质量行 + 英文描述）、自然语言整段、Niji 四段。"
      + "对话框「提示词模版」下拉与「多元数据插入 → 生图提示词模式」都从这里取。",
  },
  {
    group: "video",
    title: "视频提示词模版",
    hint: "决定视频提示词的六段骨架、运镜文案、节奏名与默认时长；输出结构与分镜逻辑由协议族固定。"
      + "「多元数据插入 → 视频提示词模式」按当前视频模式选用对应种类。",
  },
];

export function StylePanel({ draft, setDraft }: PanelProps) {
  const [builtins, setBuiltins] = useState<PromptTemplate[]>([]);
  const [customs, setCustoms] = useState<PromptTemplate[]>([]);
  const [modal, setModal] = useState<
    { group: TemplateGroup; mode: "create" | "edit"; templateId?: string } | null
  >(null);

  const reload = useCallback(async () => {
    try {
      const r = await listPromptTemplates();
      setBuiltins(r.builtins);
      setCustoms(r.items);
    } catch { /* 后端不可用：保留空列表，进对话页时会重拉 */ }
  }, []);

  // 进面板即拉取：内置全文要能直接看到，不能等点击
  useEffect(() => { void reload(); }, [reload]);

  const legacy = (draft.stylePresets || []).length;

  return (
    <div>
      <div className="settings-section">
        <h4 style={{ margin: 0 }}>提示词模版</h4>
        <p className="field-hint" style={{ marginTop: 8 }}>
          内置默认模版公开可读（内置协议族决定校验与兜底，只读不可改）；要自定义就点对应分组的
          「新增模版」，在克隆出来的副本上改。当前自带 {builtins.length || 6} 个内置 ·
          我的 {customs.length} 个（生图 {customs.filter((t) => templateKindGroup(t.kind) === "image").length} /
          视频 {customs.filter((t) => templateKindGroup(t.kind) === "video").length}）。
        </p>
        {legacy > 0 && (
          <p className="field-hint" style={{ color: "#c98a1a" }}>
            还有 {legacy} 个旧「风格存档」未迁移（上次迁移时后端不可用）。进入任意对话页会自动重试；
            迁移后内容会原样搬进自定义模版的追加段，不会丢失。
          </p>
        )}
      </div>

      {SECTIONS.map(({ group, title, hint }) => {
        const groupBuiltins = builtins.filter((t) => templateKindGroup(t.kind) === group);
        const groupCustoms = customs.filter((t) => templateKindGroup(t.kind) === group);
        return (
          <div className="settings-section" key={group}>
            <div className="settings-actions-head" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10 }}>
              <h4 style={{ margin: 0 }}>{title}</h4>
              <button className="btn" onClick={() => setModal({ group, mode: "create" })}>
                <Plus size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />新增模版
              </button>
            </div>
            <p className="field-hint" style={{ marginTop: 8 }}>{hint}</p>

            <div style={{ display: "grid", gap: 10, marginTop: 12 }}>
              {groupBuiltins.map((tpl) => (
                <BuiltinTemplateCard key={tpl.id} tpl={tpl} />
              ))}
            </div>

            {groupCustoms.length > 0 && (
              <>
                <p className="field-hint" style={{ marginTop: 14, marginBottom: 6 }}>我的模版</p>
                <div style={{ display: "grid", gap: 8 }}>
                  {groupCustoms.map((tpl) => (
                    <div className="image-model-card" key={tpl.id}>
                      <div className="row-head">
                        <strong style={{ flex: 1 }}>
                          {tpl.name || "未命名模版"}
                          <span className="field-hint" style={{ marginLeft: 8, fontWeight: 400 }}>
                            {KIND_LABELS[tpl.kind]}
                          </span>
                          {templateIsStale(tpl, builtins) && (
                            <span style={{ color: "#c98a1a", marginLeft: 8, fontSize: 12 }}>有更新</span>
                          )}
                        </strong>
                        <button
                          className="btn"
                          onClick={() => setModal({ group, mode: "edit", templateId: tpl.id })}
                        >
                          <Pencil size={13} style={{ verticalAlign: "-2px", marginRight: 4 }} />编辑
                        </button>
                      </div>
                      {templateKindGroup(tpl.kind) === "image" ? (
                        <ReadOnlyBlock label="追加指令" text={tpl.extra_instructions} height={88} />
                      ) : (
                        <TemplateStructureView tpl={tpl} />
                      )}
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
        );
      })}

      {modal && (
        <PromptTemplateModal
          builtins={builtins}
          customs={customs}
          initialGroup={modal.group}
          initialTemplateId={modal.mode === "edit" ? modal.templateId || "" : ""}
          createForGroup={modal.mode === "create" ? modal.group : undefined}
          onChanged={reload}
          onClose={() => setModal(null)}
        />
      )}
    </div>
  );
}

/** 内置模版卡：内容直接公开（只读），不藏在弹窗里。 */
function BuiltinTemplateCard({ tpl }: { tpl: PromptTemplate }) {
  return (
    <div className="image-model-card">
      <div className="row-head">
        <strong style={{ flex: 1 }}>{KIND_LABELS[tpl.kind]}</strong>
        <span className="field-hint">内置 · 只读 · {tpl.content_hash}</span>
      </div>
      {templateKindGroup(tpl.kind) === "image" ? (
        <>
          <ReadOnlyBlock label="生成指令全文（system）" text={tpl.system_text} height={240} />
          {tpl.kind === "anima_tags" && (
            <>
              <ReadOnlyBlock label="固定质量词" text={tpl.quality_tags} height={84} />
              <ReadOnlyBlock label="固定负面词" text={tpl.negative_tags} height={110} />
            </>
          )}
        </>
      ) : (
        <TemplateStructureView tpl={tpl} />
      )}
    </div>
  );
}

/** 只读全文块（可滚动；样式内联，不动全局 css）。 */
function ReadOnlyBlock({ label, text, height }: { label: string; text: string; height: number }) {
  return (
    <div className="field">
      <label>{label}</label>
      <pre
        style={{
          margin: 0,
          maxHeight: height,
          overflow: "auto",
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          fontSize: 12,
          lineHeight: 1.6,
          padding: "8px 10px",
          borderRadius: 6,
          background: "rgba(127, 127, 127, 0.14)",   // 明暗主题都成立的中性底
          color: "inherit",
        }}
      >{text || "（空）"}</pre>
    </div>
  );
}
