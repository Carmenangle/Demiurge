import { useEffect, useState } from "react";
import type { Template } from "../api/workflows";
import {
  SEMANTIC_PROMPT, SEMANTIC_NEGATIVE_PROMPT, SEMANTIC_LORA_NAME, SEMANTIC_BASE_IMAGE,
  SEMANTIC_LATENT_WIDTH, SEMANTIC_LATENT_HEIGHT,
} from "../api/workflows";
import { availableLoras, type AvailableLora } from "../api/loras";
import { getProfilePromptDefaults } from "../api/ai";
import { uploadChatBg } from "../api/userState";
import { localViewUrl } from "../api/comfyui";
import type { MediaInsertPreset, CharacterLoraBinding } from "../stores/settings";
import {
  normalizePromptProfile, PROMPT_PROFILE_OPTIONS, workflowFieldBinding,
} from "../lib/imagePromptProfiles";

interface Props {
  templates: Template[];
  cardName?: string;   // 本作品主角色卡名，用于预填首行角色
  cardNames?: string[]; // 本作品绑定的全部角色卡
  modelsDir: string;   // ComfyUI models 目录，用于直接扫 loras 子目录列出可选模型
  preset?: MediaInsertPreset;
  onSave: (preset: MediaInsertPreset) => void;
  onClose: () => void;
}

// 一行「角色名 + LoRA + 权重 + 底图」的可编辑结构（内部态，保存时回填 characterLoras）。
interface CharRow extends CharacterLoraBinding {
  name: string;
}

function loraStatusSuffix(lora: AvailableLora): string {
  if (lora.trigger_status === "not_required") return "（通用·无需触发词）";
  if (lora.has_triggers || lora.trigger_status === "configured") return "";
  return "（触发词未确认·仍可使用）";
}

export function suggestedWeightForLora(loras: readonly AvailableLora[], loraName: string): number {
  return loras.find((lora) => lora.lora_name === loraName)?.suggested_weight ?? 0.8;
}

function initialRows(preset?: MediaInsertPreset, cardName?: string, cardNames: string[] = []): CharRow[] {
  const map = preset?.characterLoras || {};
  const rows: CharRow[] = Object.entries(map).map(([name, b]) => ({ name, ...b }));
  // 不再自动预填未配置 LoRA 的绑定卡——避免用户删除后下次又被自动加回
  return rows.length ? rows : [{ name: "" }];
}

// 多元数据插入：为本作品预设剧情高潮点异步出图用的 ComfyUI 工作流模板 + 按角色 LoRA/底图。
// 提示词由后端高潮点提取，运行时按在场角色取该角色 LoRA/底图，无则回退风格 LoRA + 风格底图。
export function MediaInsertModal({ templates, cardName, cardNames = [], modelsDir, preset, onSave, onClose }: Props) {
  const [templateId, setTemplateId] = useState(preset?.templateId || "");
  const [loraMode, setLoraMode] = useState<"none" | "single" | "multi">(
    preset?.loraMode || "single",
  );
  const [appearanceSource, setAppearanceSource] = useState<"worldbook" | "character_card">(
    preset?.appearanceSource === "character_card" ? "character_card" : "worldbook",
  );
  const [promptProfile, setPromptProfile] = useState(() => normalizePromptProfile(preset?.promptProfile));
  const [qualityPrompt, setQualityPrompt] = useState(preset?.qualityPrompt || "");
  const [negativePrompt, setNegativePrompt] = useState(preset?.negativePrompt || "");
  const [latentLongEdge, setLatentLongEdge] = useState<1024 | 2048 | 4096>(
    preset?.latentLongEdge === 2048 || preset?.latentLongEdge === 4096
      ? preset.latentLongEdge : 1024,
  );
  const [rows, setRows] = useState<CharRow[]>(() => initialRows(preset, cardName, cardNames));
  const [styleLora, setStyleLora] = useState(preset?.styleLora || "");
  const [styleLoraWeight, setStyleLoraWeight] = useState(preset?.styleLoraWeight ?? 0.8);
  const [styleBaseImage, setStyleBaseImage] = useState(preset?.styleBaseImage || "");
  const [videoTemplateId, setVideoTemplateId] = useState(preset?.videoTemplateId || "");
  const [smartVideo, setSmartVideo] = useState(preset?.smartVideo ?? false);
  const [loras, setLoras] = useState<AvailableLora[]>([]);
  const [loraLoadState, setLoraLoadState] = useState<"idle" | "loading" | "ready" | "error">(
    modelsDir ? "loading" : "idle",
  );
  const [loraLoadError, setLoraLoadError] = useState("");
  const [loraReloadKey, setLoraReloadKey] = useState(0);
  const [uploading, setUploading] = useState("");  // 正在上传底图的行 key（""=无）

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    let active = true;
    let retryTimer: number | undefined;
    if (!modelsDir) {
      setLoras([]);
      setLoraLoadState("idle");
      setLoraLoadError("");
      return () => { active = false; };
    }
    setLoras([]);
    setLoraLoadState("loading");
    setLoraLoadError("");
    const load = async (attempt: number) => {
      try {
        const response = await availableLoras(modelsDir);
        if (!active) return;
        setLoras(response.items || []);
        setLoraLoadState("ready");
      } catch (error) {
        if (!active) return;
        if (attempt < 2) {
          retryTimer = window.setTimeout(() => { void load(attempt + 1); }, 500 * (attempt + 1));
          return;
        }
        setLoraLoadState("error");
        setLoraLoadError(error instanceof Error ? error.message : "未知错误");
      }
    };
    void load(0);
    return () => {
      active = false;
      if (retryTimer !== undefined) window.clearTimeout(retryTimer);
    };
  }, [modelsDir, loraReloadKey]);

  useEffect(() => {
    if (promptProfile !== "anima_tags" || (qualityPrompt && negativePrompt)) return;
    getProfilePromptDefaults(promptProfile).then((defaults) => {
      setQualityPrompt((current) => current || defaults.quality_prompt);
      setNegativePrompt((current) => current || defaults.negative_prompt);
    }).catch(() => {});
  }, [promptProfile, qualityPrompt, negativePrompt]);

  const tpl = templates.find((t) => t.id === templateId);
  const hasPrompt = !!tpl?.exposed.some((f) => workflowFieldBinding(f) === SEMANTIC_PROMPT);
  const hasNegativePrompt = !!tpl?.exposed.some(
    (f) => workflowFieldBinding(f) === SEMANTIC_NEGATIVE_PROMPT,
  );
  const hasLoraSlot = !!tpl?.exposed.some((f) => workflowFieldBinding(f) === SEMANTIC_LORA_NAME);
  const hasImageSlot = !!tpl?.exposed.some((f) => workflowFieldBinding(f) === SEMANTIC_BASE_IMAGE);
  const hasLatentWidth = !!tpl?.exposed.some((f) => workflowFieldBinding(f) === SEMANTIC_LATENT_WIDTH);
  const hasLatentHeight = !!tpl?.exposed.some((f) => workflowFieldBinding(f) === SEMANTIC_LATENT_HEIGHT);

  const setRow = (i: number, patch: Partial<CharRow>) =>
    setRows((prev) => prev.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  const addRow = () => setRows((prev) => [...prev, { name: "" }]);
  const delRow = (i: number) => setRows((prev) => prev.filter((_, idx) => idx !== i));

  const pickBase = async (i: number, f: File | null | undefined) => {
    if (!f) return;
    setUploading(String(i));
    try {
      const r = await uploadChatBg(f);
      if (r.ok) setRow(i, { baseImage: r.path });
    } catch { /* 忽略：用户可重试 */ }
    finally { setUploading(""); }
  };

  const pickStyleBase = async (f: File | null | undefined) => {
    if (!f) return;
    setUploading("style");
    try {
      const r = await uploadChatBg(f);
      if (r.ok) setStyleBaseImage(r.path);
    } catch { /* 忽略 */ }
    finally { setUploading(""); }
  };

  // 校验：角色无 LoRA 时必须给底图（设定要求）。有名字的行才校验。
  const missingBase = rows.some((r) => r.name.trim()
    && (loraMode === "none" || !r.loraName)
    && !r.baseImage
    && (loraMode === "none" || !styleLora)
    && !styleBaseImage);

  const save = () => {
    const characterLoras: Record<string, CharacterLoraBinding> = {};
    for (const r of rows) {
      const name = r.name.trim();
      if (!name) continue;
      characterLoras[name] = {
        loraName: loraMode === "none" ? undefined : r.loraName || undefined,
        loraWeight: loraMode !== "none" && r.loraName ? r.loraWeight ?? 0.8 : undefined,
        baseImage: r.baseImage || undefined,
      };
    }
    onSave({
      templateId,
      loraMode,
      appearanceSource,
      promptProfile,
      qualityPrompt: promptProfile === "anima_tags" ? qualityPrompt.trim() : undefined,
      negativePrompt: promptProfile === "anima_tags" ? negativePrompt.trim() : undefined,
      latentLongEdge,
      characterLoras: Object.keys(characterLoras).length ? characterLoras : undefined,
      styleLora: loraMode === "none" ? undefined : styleLora || undefined,
      styleLoraWeight: loraMode !== "none" && styleLora ? styleLoraWeight : undefined,
      styleBaseImage: styleBaseImage || undefined,
      videoTemplateId: videoTemplateId || undefined,
      smartVideo,
    });
  };

  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal" style={{ width: 560, maxWidth: "94vw", maxHeight: "86vh", overflowY: "auto" }} onClick={(e) => e.stopPropagation()}>
        <h3>多元数据插入</h3>
        <label style={{ display: "block", marginBottom: 12 }}>
          <span style={{ display: "block", marginBottom: 4, fontSize: 13 }}>角色外貌来源</span>
          <select value={appearanceSource}
            onChange={(event) => setAppearanceSource(event.target.value as "worldbook" | "character_card")}
            style={{ width: "100%" }}>
            <option value="worldbook">条目模式</option>
            <option value="character_card">角色卡模式</option>
          </select>
        </label>
        <p className="bind-hint" style={{ marginTop: -8, marginBottom: 12 }}>
          {appearanceSource === "worldbook"
            ? "从当前小仓库世界书中命中的角色视觉条目读取外貌。"
            : "从本作品绑定角色卡的描述读取外貌，适合纯机制世界书。"}
        </p>
        <p style={{ color: "#666", marginTop: 0, fontSize: 13 }}>
          预设本作品剧情高潮点自动出图用的工作流模板。保存后会自动开启「剧情插画」；提示词由剧情自动提取，LoRA 无触发词记录也不会阻断出图。
        </p>
        <div style={{ marginBottom: 12 }}>
          <span style={{ display: "block", marginBottom: 4, fontSize: 13 }}>LoRA 模式</span>
          <div className="lora-mode-switch" role="group" aria-label="LoRA 模式">
            {([
              ["none", "无 LoRA"], ["single", "单 LoRA"], ["multi", "多 LoRA"],
            ] as const).map(([value, label]) => (
              <button
                key={value}
                type="button"
                className={`btn${loraMode === value ? " primary" : ""}`}
                aria-pressed={loraMode === value}
                onClick={() => setLoraMode(value)}
              >{label}</button>
            ))}
          </div>
          <p className="bind-hint" style={{ margin: "5px 0 0" }}>
            {loraMode === "none" && "只使用角色底图，不加载角色或风格 LoRA。"}
            {loraMode === "single" && "串联高潮画面中已绑定的角色 LoRA；均未命中时才回退风格 LoRA。"}
            {loraMode === "multi" && "固定加载默认风格 LoRA，并叠加全部在场角色 LoRA。"}
          </p>
        </div>
        <label style={{ display: "block", marginBottom: 12 }}>
          <span style={{ display: "block", marginBottom: 4, fontSize: 13 }}>提示词模式</span>
          <select
            value={promptProfile}
            onChange={(event) => setPromptProfile(normalizePromptProfile(event.target.value))}
            style={{ width: "100%" }}
          >
            {PROMPT_PROFILE_OPTIONS.map((option) => (
              <option key={option.id} value={option.id}>{option.label}</option>
            ))}
          </select>
        </label>
        {promptProfile === "anima_tags" && (
          <>
            <label style={{ display: "block", marginBottom: 12 }}>
              <span style={{ display: "block", marginBottom: 4, fontSize: 13 }}>固定质量提示词</span>
              <textarea
                value={qualityPrompt}
                onChange={(event) => setQualityPrompt(event.target.value)}
                rows={5}
                style={{ width: "100%", minHeight: 110, resize: "vertical" }}
              />
            </label>
            <label style={{ display: "block", marginBottom: 12 }}>
              <span style={{ display: "block", marginBottom: 4, fontSize: 13 }}>固定负面提示词</span>
              <textarea
                value={negativePrompt}
                onChange={(event) => setNegativePrompt(event.target.value)}
                rows={6}
                style={{ width: "100%", minHeight: 130, resize: "vertical" }}
              />
            </label>
          </>
        )}
        <label style={{ display: "block", marginBottom: 12 }}>
          <span style={{ display: "block", marginBottom: 4, fontSize: 13 }}>工作流模板</span>
          <select value={templateId} onChange={(e) => setTemplateId(e.target.value)} style={{ width: "100%" }}>
            <option value="">不启用（关闭高潮点自动出图）</option>
            {templates.map((t) => (
              <option key={t.id} value={t.id}>{t.name}</option>
            ))}
          </select>
        </label>
        {templateId && !hasPrompt && (
          <p style={{ color: "#c0392b", fontSize: 12, marginTop: -6 }}>
            该模板未标注「提示词」字段，无法注入剧情提示词。请先到「工作流模板」编辑并标注。
          </p>
        )}
        {templateId && promptProfile === "anima_tags" && negativePrompt && !hasNegativePrompt && (
          <p style={{ color: "#c98a1a", fontSize: 12, marginTop: -6 }}>
            该模板未标注有效的「负面提示词」字段，固定负面提示词不会参与采样。
          </p>
        )}
        <label style={{ display: "block", marginBottom: 12 }}>
          <span style={{ display: "block", marginBottom: 4, fontSize: 13 }}>Latent 最长边</span>
          <select
            value={latentLongEdge}
            onChange={(event) => setLatentLongEdge(Number(event.target.value) as 1024 | 2048 | 4096)}
            style={{ width: "100%" }}
          >
            <option value={1024}>1K（1024）</option>
            <option value={2048}>2K（2048）</option>
            <option value={4096}>4K（4096）</option>
          </select>
        </label>
        {templateId && (!hasLatentWidth || !hasLatentHeight) && (
          <p style={{ color: "#c98a1a", fontSize: 12, marginTop: -6 }}>
            该模板未同时标注「Latent 宽度」和「Latent 高度」，所选尺寸不会注入工作流。
          </p>
        )}

        {appearanceSource === "worldbook" && (<>
          <hr style={{ border: "none", borderTop: "1px solid #eee", margin: "16px 0" }} />
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
            <strong style={{ fontSize: 13 }}>按角色配置（LoRA + 底图）</strong>
            <button className="btn" onClick={addRow}>+ 添加角色</button>
          </div>
        {loraMode !== "none" && templateId && !hasLoraSlot && (
          <p style={{ color: "#c0392b", fontSize: 12, marginTop: -2 }}>该模板未标注 LoRA 字段，角色 LoRA 选了也不生效。</p>
        )}
        {templateId && !hasImageSlot && (
          <p style={{ color: "#c98a1a", fontSize: 12, marginTop: -2 }}>该模板是纯文生图配置，没有「角色底图」槽位；不选底图时可正常出图，只有需要图生图锁定角色时才要更换模板或补充该语义字段。</p>
        )}
        {loraMode !== "none" && loraLoadState === "loading" && (
          <p style={{ color: "#777", fontSize: 12, marginTop: -2 }}>正在读取 LoRA 目录…</p>
        )}
        {loraMode !== "none" && loraLoadState === "error" && (
          <p style={{ color: "#c98a1a", fontSize: 12, marginTop: -2 }}>
            LoRA 列表读取失败，当前绑定仍会保留：{loraLoadError}
            <button type="button" className="btn" style={{ marginLeft: 8 }}
              onClick={() => setLoraReloadKey((value) => value + 1)}>重试</button>
          </p>
        )}
        {loraMode !== "none" && loraLoadState === "ready" && loras.length === 0 && (
          <p style={{ color: "#c98a1a", fontSize: 12, marginTop: -2 }}>
            在 {modelsDir}/loras 下没扫到模型文件，请确认设置里的 ComfyUI 路径。
          </p>
        )}
        {loraMode !== "none" && loraLoadState === "idle" && (
          <p style={{ color: "#c98a1a", fontSize: 12, marginTop: -2 }}>
            未设置 ComfyUI 路径，无法列出 LoRA。请先到设置里填 ComfyUI 路径或 models 目录。
          </p>
        )}
        {rows.map((r, i) => (
          <div key={i} style={{ border: "1px solid #eee", borderRadius: 8, padding: 10, marginBottom: 8 }}>
            <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 6 }}>
              <input
                placeholder="角色名（与剧情中一致）" value={r.name}
                onChange={(e) => setRow(i, { name: e.target.value })}
                style={{ flex: 1 }}
              />
              <button className="btn" onClick={() => delRow(i)} title="删除该角色">✕</button>
            </div>
            {loraMode !== "none" && <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 6 }}>
              <select value={r.loraName || ""} onChange={(e) => {
                const loraName = e.target.value;
                setRow(i, {
                  loraName: loraName || undefined,
                  loraWeight: loraName ? suggestedWeightForLora(loras, loraName) : undefined,
                });
              }} style={{ flex: 1 }}>
                <option value="">无角色 LoRA（用风格 LoRA + 底图）</option>
                {r.loraName && !loras.some((lora) => lora.lora_name === r.loraName) && (
                  <option value={r.loraName}>{r.loraName}（当前绑定，目录列表未包含）</option>
                )}
                {loras.map((l) => (
                  <option key={l.lora_name} value={l.lora_name}>
                    {l.lora_name}{loraStatusSuffix(l)}
                  </option>
                ))}
              </select>
              {r.loraName && (
                <input
                  type="number" step={0.05} min={0} max={2} value={r.loraWeight ?? 0.8}
                  onChange={(e) => setRow(i, { loraWeight: Number(e.target.value) })}
                  style={{ width: 80 }} title="建议权重已自动填入，可手动调整"
                />
              )}
            </div>}
            <div style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13 }}>
              {r.baseImage
                ? <img src={localViewUrl(r.baseImage)} alt="" style={{ width: 40, height: 40, objectFit: "cover", borderRadius: 4 }} />
                : <span style={{ color: "#999" }}>底图（可选，用于角色一致性）</span>}
              <label className="btn" style={{ cursor: "pointer" }}>
                {uploading === String(i) ? "上传中…" : (r.baseImage ? "更换底图" : "选择底图")}
                <input type="file" accept="image/*" hidden onChange={(e) => { pickBase(i, e.target.files?.[0]); e.target.value = ""; }} />
              </label>
              {r.baseImage && <button className="btn" onClick={() => setRow(i, { baseImage: undefined })}>清除</button>}
            </div>
          </div>
        ))}
        </>)}

        {loraMode !== "none" && <>
          <hr style={{ border: "none", borderTop: "1px solid #eee", margin: "16px 0" }} />
          <strong style={{ fontSize: 13, display: "block", marginBottom: 8 }}>
            {appearanceSource === "character_card"
              ? "全局风格 LoRA（可选）"
              : "兜底风格 LoRA"}
          </strong>
          <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8 }}>
          <select value={styleLora} onChange={(e) => {
            const loraName = e.target.value;
            setStyleLora(loraName);
            if (loraName) setStyleLoraWeight(suggestedWeightForLora(loras, loraName));
          }} style={{ flex: 1 }}>
            <option value="">无风格 LoRA</option>
            {styleLora && !loras.some((lora) => lora.lora_name === styleLora) && (
              <option value={styleLora}>{styleLora}（当前绑定，目录列表未包含）</option>
            )}
            {loras.map((l) => (
              <option key={l.lora_name} value={l.lora_name}>
                {l.lora_name}{loraStatusSuffix(l)}
              </option>
            ))}
          </select>
          {styleLora && (
            <input
              type="number" step={0.05} min={0} max={2} value={styleLoraWeight}
              onChange={(e) => setStyleLoraWeight(Number(e.target.value))}
              style={{ width: 80 }} title="建议权重已自动填入，可手动调整"
            />
          )}
          </div>
        </>}
        {appearanceSource === "worldbook" && <div style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13, marginBottom: 8 }}>
          {styleBaseImage
            ? <img src={localViewUrl(styleBaseImage)} alt="" style={{ width: 40, height: 40, objectFit: "cover", borderRadius: 4 }} />
            : <span style={{ color: "#999" }}>风格底图（可选）</span>}
          <label className="btn" style={{ cursor: "pointer" }}>
            {uploading === "style" ? "上传中…" : (styleBaseImage ? "更换底图" : "选择底图")}
            <input type="file" accept="image/*" hidden onChange={(e) => { pickStyleBase(e.target.files?.[0]); e.target.value = ""; }} />
          </label>
          {styleBaseImage && <button className="btn" onClick={() => setStyleBaseImage("")}>清除</button>}
        </div>}

        <hr style={{ border: "none", borderTop: "1px solid #eee", margin: "16px 0" }} />
        <label style={{ display: "block", marginBottom: 12 }}>
          <span style={{ display: "block", marginBottom: 4, fontSize: 13 }}>视频工作流模板（可选）</span>
          <select value={videoTemplateId} onChange={(e) => setVideoTemplateId(e.target.value)} style={{ width: "100%" }}>
            <option value="">不出视频（仅出图）</option>
            {templates.map((t) => (
              <option key={t.id} value={t.id}>{t.name}</option>
            ))}
          </select>
        </label>
        {videoTemplateId && (
          <label style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12, fontSize: 13 }}>
            <input type="checkbox" checked={smartVideo} onChange={(e) => setSmartVideo(e.target.checked)} />
            智能模态：剧情动作剧烈时（如奔跑/律动）自动改用视频模板，静态画面仍出图
          </label>
        )}
        {appearanceSource === "worldbook" && missingBase && (
          <p style={{ color: "#c0392b", fontSize: 12 }}>有角色既无 LoRA 也无底图，且无兜底风格——该角色出图将缺少一致性锚点。</p>
        )}
        <div className="modal-actions">
          <button className="btn" onClick={onClose}>取消</button>
          <button className="btn primary" disabled={!templateId || !hasPrompt} onClick={save}>保存</button>
        </div>
      </div>
    </div>
  );
}
