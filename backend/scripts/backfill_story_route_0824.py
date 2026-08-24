# 一次性迁移：给旧快照（route/system 字段引入前）的助手文本消息回填剧情/状态标签。
#
# 背景：剧情楼层判定已改为「剧情专家标签 allowlist」——只有 route ∈ {roleplay, answer}
# 的消息才算剧情楼层，system/顶层媒体/非剧情 route 一律不是；无标签的消息默认不是。
# 老快照里：剧情正文没有 route（会从画布消失）、状态/Toast 没有 system（会被误判成楼层）。
# 本脚本按 backfill_story_tags 的保守规则回填：
#   - 文本像状态/Toast（含 ComfyUI/prompt_id/命令/运行状态词）→ 补 system:true
#   - 其余纯正文 → 补 route:"roleplay"
#   - 已有 route / 有媒体或卡字段 / 用户消息 / 空文本 → 不动
# 幂等：重复运行不再改任何消息。
#
# 用法：
#   python scripts/backfill_story_route_0824.py            # 预演（dry-run，只打印）
#   python scripts/backfill_story_route_0824.py --apply    # 真正写回
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import DATA_DIR
from app.services import chat_snapshot, repo_meta

LEGACY_DIR = DATA_DIR / "chat_snapshots"


def snapshot_files() -> list[Path]:
    files: list[Path] = []
    if LEGACY_DIR.is_dir():
        files.extend(sorted(LEGACY_DIR.glob("*.json")))
    output_dir = repo_meta.output_dir_from_state()
    if output_dir and Path(output_dir).is_dir():
        # 仓库文件夹：<作品目录>/**/chat.json（含嵌套子仓库），跳过画布/其它 json
        files.extend(sorted(Path(output_dir).rglob("chat.json")))
    seen: set[Path] = set()
    return [p for p in files if p not in seen and not seen.add(p)]


def main() -> int:
    apply = "--apply" in sys.argv
    files = snapshot_files()
    total = {"changed": 0, "story": 0, "status": 0, "generate": 0, "files": 0}
    for path in files:
        try:
            messages = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"  skip {path}: {exc}")
            continue
        if not isinstance(messages, list):
            print(f"  skip {path}: 非消息数组")
            continue
        migrated, stats = chat_snapshot.backfill_story_tags(messages)
        if stats["changed"] == 0:
            continue
        total["files"] += 1
        total["changed"] += stats["changed"]
        total["story"] += stats["story"]
        total["status"] += stats["status"]
        total["generate"] += stats["generate"]
        print(f"  {path}: {stats}")
        if apply:
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(migrated, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
    print(f"\n扫描 {len(files)} 个快照文件；需迁移 {total['files']} 个："
          f"回填剧情 {total['story']} 条、状态 {total['status']} 条、生成提示词 {total['generate']} 条"
          f"（共 {total['changed']} 条）。")
    print("已写回" if apply else "（dry-run，未写回；加 --apply 生效）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
