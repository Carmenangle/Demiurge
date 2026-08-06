import { useEffect, useState } from "react";
import { ImagePlus, SmilePlus, X } from "lucide-react";
import {
  avatarUrl, characterMedia, expressionUrl,
  uploadCharacterAvatar, uploadCharacterExpression,
  type CharacterMedia,
} from "../api/characters";

export function CharacterMediaModal({ base, name, onClose, onChanged }: {
  base: string;
  name: string;
  onClose: () => void;
  onChanged?: () => void;
}) {
  const [media, setMedia] = useState<CharacterMedia | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [version, setVersion] = useState(0);

  const load = () => characterMedia(base, name).then(setMedia).catch((error) => setErr(String(error)));
  useEffect(() => { void load(); }, [base, name]);

  const uploadAvatar = async (file?: File) => {
    if (!file) return;
    setBusy(true); setErr("");
    try {
      await uploadCharacterAvatar(base, name, file);
      await load();
      setVersion((value) => value + 1);
      onChanged?.();
    } catch (error) { setErr(String((error as Error).message)); }
    finally { setBusy(false); }
  };

  const uploadExpressions = async (files: FileList | null) => {
    if (!files?.length) return;
    setBusy(true); setErr("");
    try {
      for (const file of Array.from(files)) {
        const expression = file.name.replace(/\.[^.]+$/, "");
        await uploadCharacterExpression(base, name, expression, file);
      }
      await load();
      setVersion((value) => value + 1);
      onChanged?.();
    } catch (error) { setErr(String((error as Error).message)); }
    finally { setBusy(false); }
  };

  const mediaBase = media?.base || base;
  return (
    <div className="modal-mask" onClick={(event) => { event.stopPropagation(); onClose(); }}>
      <div className="modal character-media-modal" onClick={(event) => event.stopPropagation()}>
        <div className="character-modal-head">
          <h3>{name} · 头像与表情</h3>
          <button className="icon-btn" onClick={onClose} title="关闭"><X size={16} /></button>
        </div>
        {err && <p className="character-card-error">{err}</p>}

        <section className="character-media-section">
          <div className="character-media-avatar">
            {media?.has_avatar
              ? <img src={`${avatarUrl(mediaBase, media.folder)}&v=${version}`} alt={`${name}头像`} />
              : <span>暂无头像</span>}
          </div>
          <div>
            <strong>角色头像</strong>
            <p className="bind-hint">对话未命中具体表情时显示该头像。</p>
            <label className="btn">
              <ImagePlus size={14} /> {media?.has_avatar ? "更换头像" : "添加头像"}
              <input type="file" accept="image/png" hidden disabled={busy}
                onChange={(event) => { void uploadAvatar(event.target.files?.[0]); event.target.value = ""; }} />
            </label>
          </div>
        </section>

        <section>
          <div className="character-media-section-head">
            <div>
              <strong>表情</strong>
              <p className="bind-hint">文件名作为情绪标签，例如“开心.png”“愤怒.png”；剧情命中时自动切换。</p>
            </div>
            <label className="btn">
              <SmilePlus size={14} /> 添加表情
              <input type="file" accept="image/png" multiple hidden disabled={busy}
                onChange={(event) => { void uploadExpressions(event.target.files); event.target.value = ""; }} />
            </label>
          </div>
          <div className="character-expression-grid">
            {media?.expressions.map((item) => (
              <figure key={item.file}>
                <img src={`${expressionUrl(mediaBase, media.folder, item.file)}&v=${version}`} alt={item.name} />
                <figcaption>{item.name}</figcaption>
              </figure>
            ))}
            {media && media.expressions.length === 0 && <p className="bind-hint">还没有表情图片。</p>}
          </div>
        </section>

        <div className="modal-actions"><button className="btn" onClick={onClose}>关闭</button></div>
      </div>
    </div>
  );
}
