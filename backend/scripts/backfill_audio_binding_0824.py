# 一次性迁移：给已保存模板里音频节点的 exposed 字段回填音频语义 binding，并清理 widget_N 噪声。
#
# 背景：音频节点的语义绑定（voice_reference / voice_text / voice_emotion_<key>）此前只在
# 图片节点上有推断规则（inferWorkflowFieldBinding），音频节点（LoadAudio / IndexTTS 系）选中后
# binding 为空，导致自动配音时注入不到台词/音轨/情感。同时后端 WIDGET_NAMES 缺音频节点，
# UI 格式解析出的 widgets_values 回退成 widget_0/widget_1… 噪声字段。
#
# 本脚本按与前端 inferWorkflowFieldBinding 一致的规则回填：
#   - LoadAudio.audio            → voice_reference
#   - IndexTTS*.text             → voice_text
#   - IndexTTS*.Happy/Angry/…    → voice_emotion_<key>（字段名大小写归一）
#   - 字段名形如 widget_<N> 的噪声字段（WIDGET_NAMES 缺失产物）直接删除
# 幂等：已有 binding 的字段不再改。
#
# 用法：
#   python scripts/backfill_audio_binding_0824.py            # 预演（dry-run，只打印）
#   python scripts/backfill_audio_binding_0824.py --apply    # 真正写回
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import DATA_DIR

TEMPLATES_DIR = DATA_DIR / "templates"

EMOTION_KEYS = ("happy", "angry", "sad", "fear", "hate", "low", "surprise", "neutral")


def infer_audio_binding(node_type: str, field: str) -> str:
    """对齐前端 inferWorkflowFieldBinding 的音频分支；非音频返回空串。"""
    t = (node_type or "").lower()
    name = (field or "").lower()
    if "loadaudio" in t and name == "audio":
        return "voice_reference"
    if "indextts" in t:
        if name == "text":
            return "voice_text"
        if name in EMOTION_KEYS:
            return f"voice_emotion_{name}"
    return ""


def is_widget_noise(field: str) -> bool:
    import re
    return bool(re.fullmatch(r"widget_\d+", field or ""))


def migrate(data: dict) -> dict:
    nodes = (data.get("workflow_data") or {}).get("nodes") or []
    node_types = {
        str(n.get("id")): (n.get("type") or "")
        for n in nodes if isinstance(n, dict)
    }
    exposed = data.get("exposed")
    if not isinstance(exposed, list):
        return data
    kept = []
    stats = {"binding": 0, "cleaned_widget": 0}
    for ef in exposed:
        if not isinstance(ef, dict):
            kept.append(ef)
            continue
        field = str(ef.get("field") or "")
        node_type = node_types.get(str(ef.get("node_id")), "")
        if is_widget_noise(field):
            stats["cleaned_widget"] += 1
            continue  # 丢弃 WIDGET_NAMES 缺失产生的噪声字段
        if not ef.get("binding"):
            binding = infer_audio_binding(node_type, field)
            if binding:
                ef["binding"] = binding
                stats["binding"] += 1
        kept.append(ef)
    data["exposed"] = kept
    return data, stats


def main() -> int:
    apply = "--apply" in sys.argv
    total = {"files": 0, "binding": 0, "cleaned_widget": 0}
    for path in sorted(TEMPLATES_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"  skip {path}: {exc}")
            continue
        migrated, stats = migrate(data)
        if stats["binding"] == 0 and stats["cleaned_widget"] == 0:
            continue
        total["files"] += 1
        total["binding"] += stats["binding"]
        total["cleaned_widget"] += stats["cleaned_widget"]
        print(f"  {path.name}: 回填 binding {stats['binding']} 个、清理 widget_N {stats['cleaned_widget']} 个")
        if apply:
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(migrated, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(path)
    print(f"\n扫描 {len(list(TEMPLATES_DIR.glob('*.json')))} 个模板；"
          f"需迁移 {total['files']} 个：回填 binding {total['binding']} 个、清理 widget_N {total['cleaned_widget']} 个。")
    print("已写回" if apply else "（dry-run，未写回；加 --apply 生效）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
