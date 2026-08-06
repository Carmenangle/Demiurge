"""角色卡落盘：每张卡 = 一个文件夹（小仓库式），内含卡本体 + 内嵌世界书/正则 + 对话记录。

布局（base = 设置里的「角色卡文件夹」）：
    <base>/<安全卡名>/
        card.json        归一后的角色卡（NormalizedCard.to_dict）
        worldbook.json    内嵌世界书（有才写）
        regex.json        内嵌正则脚本（有才写）
        avatar.png        PNG 卡的原图（从 PNG 导入才写）
        chat.json         该卡的对话记录（对话侧写入，导入不碰）

同名=同文件夹。覆盖前若已有 chat.json，调用方应先问是否导出保留（见 routers/characters.py）。
本模块只做文件读写编排，不解析卡格式（那是 character_card 的事）。
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.character_card import NormalizedCard
from app.services.pathnames import safe_dir

CARD_FILE = "card.json"
WORLDBOOK_FILE = "worldbook.json"
REGEX_FILE = "regex.json"
AVATAR_FILE = "avatar.png"
EXPRESSIONS_DIR = "expressions"
CHAT_FILE = "chat.json"


@dataclass
class CardSummary:
    name: str
    folder: str
    has_worldbook: bool
    has_regex: bool
    has_chat: bool
    has_avatar: bool


def card_dir(base: str, name: str) -> Path:
    return Path(base) / safe_dir(name)


def card_exists(base: str, name: str) -> bool:
    return (card_dir(base, name) / CARD_FILE).is_file()


# 作品仓库内嵌的卡快照子目录名：<作品文件夹>/角色卡/<safe(卡名)>/{card,worldbook,regex}.json
# 让每个作品自带用到的卡+世界书+正则「记录」，改源卡不回灌已建作品（快照隔离）。
WORK_CARD_SUBDIR = "角色卡"


def work_card_base(output_dir: str, card_name: str) -> str | None:
    """作品仓库内卡快照的 base 路径（供运行时快照优先读）。仅当快照 card.json 已存在才返回。

    父作品文件夹名 = 卡名（addCardWork 建父仓库时 name=cardName），故可由 output_dir+card_name
    确定性推导：<output_dir>/<safe(卡名)>/角色卡。无 output_dir/卡名/快照 → None（回退源库）。
    """
    if not ((output_dir or "").strip() and (card_name or "").strip()):
        return None
    base = card_dir(output_dir, card_name) / WORK_CARD_SUBDIR
    if (card_dir(str(base), card_name) / CARD_FILE).is_file():
        return str(base)
    return None


def repo_card_base(output_dir: str, repo_id: str, card_name: str) -> str | None:
    """当前小仓库绑定卡的读取 base：当前仓库优先，再查父作品快照和旧版卡名目录。"""
    if not ((output_dir or "").strip() and (repo_id or "").strip() and (card_name or "").strip()):
        return None
    from app.services import repo_meta

    folder = repo_meta.repo_folder_path(output_dir, repo_id)
    candidates = [folder / WORK_CARD_SUBDIR]
    if repo_meta.parent_folder_seg(repo_id):
        candidates.append(folder.parent / WORK_CARD_SUBDIR)
    for base in candidates:
        if (card_dir(base, card_name) / CARD_FILE).is_file():
            return str(base)
    return work_card_base(output_dir, card_name)


def snapshot_to_work(character_dir: str, card_name: str, work_folder: str) -> bool:
    """把源库卡（card.json+worldbook.json+regex.json+avatar.png）快照进作品文件夹。

    目标 = <work_folder>/角色卡/<safe(卡名)>/。**幂等 + 隔离**：目标已有 card.json → 直接 False
    （不覆盖，保快照隔离——日后改源卡不回灌已建作品）。源库无此卡 → False。成功拷贝 → True。
    """
    if not ((character_dir or "").strip() and (card_name or "").strip() and (work_folder or "").strip()):
        return False
    src = card_dir(character_dir, card_name)
    if not (src / CARD_FILE).is_file():
        return False
    dst = card_dir(Path(work_folder) / WORK_CARD_SUBDIR, card_name)
    if (dst / CARD_FILE).is_file():
        return False  # 已快照过 → 隔离不覆盖
    dst.mkdir(parents=True, exist_ok=True)
    for fname in (CARD_FILE, WORLDBOOK_FILE, REGEX_FILE, AVATAR_FILE):
        sp = src / fname
        if sp.is_file():
            shutil.copy2(sp, dst / fname)
    expressions = src / EXPRESSIONS_DIR
    if expressions.is_dir():
        shutil.copytree(expressions, dst / EXPRESSIONS_DIR, dirs_exist_ok=True)
    return True


def snapshot_cards_to_repo(
    character_dir: str, card_names: list[str], output_dir: str, repo_id: str,
) -> dict[str, list[str]]:
    """把绑定角色卡快照到指定仓库；已有卡保持隔离，不覆盖。"""
    result: dict[str, list[str]] = {"created": [], "existing": [], "missing": []}
    if not ((character_dir or "").strip() and (output_dir or "").strip() and (repo_id or "").strip()):
        return result
    from app.services import repo_meta

    folder = repo_meta.repo_folder(output_dir, repo_id)
    for raw_name in dict.fromkeys(card_names):
        name = str(raw_name or "").strip()
        if not name:
            continue
        if not card_exists(character_dir, name):
            result["missing"].append(name)
        elif snapshot_to_work(character_dir, name, str(folder)):
            result["created"].append(name)
        else:
            result["existing"].append(name)
    return result


def write_avatar(base: str, name: str, image: bytes) -> None:
    folder = card_dir(base, name)
    if not (folder / CARD_FILE).is_file():
        raise FileNotFoundError(name)
    (folder / AVATAR_FILE).write_bytes(image)


def write_expression(base: str, name: str, expression: str, image: bytes) -> str:
    folder = card_dir(base, name)
    if not (folder / CARD_FILE).is_file():
        raise FileNotFoundError(name)
    safe_name = safe_dir(Path(expression).stem)
    if not safe_name:
        raise ValueError("表情名称为空")
    target = folder / EXPRESSIONS_DIR / f"{safe_name}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(image)
    return target.name


def list_expressions(base: str, name: str) -> list[dict[str, str]]:
    folder = card_dir(base, name) / EXPRESSIONS_DIR
    if not folder.is_dir():
        return []
    return [
        {"name": path.stem, "file": path.name}
        for path in sorted(folder.glob("*.png")) if path.is_file()
    ]


# 作品仓库绑定的用户人设快照：<作品文件夹>/persona.json（{name, content}）。
# 与卡/世界书同样「快照隔离」：新建作品时把当时选中的人设写进作品文件夹，
# 日后在设置里改人设不回灌已建作品；运行时快照优先读（见 agent_graph）。
PERSONA_FILE = "persona.json"


def snapshot_persona_to_work(
    output_dir: str, card_name: str, user_name: str, user_persona: str,
) -> bool:
    """把当前选中的用户人设快照进作品文件夹（<output_dir>/<safe(卡名)>/persona.json）。

    幂等+隔离：文件已存在 → False（不覆盖）。名与描述皆空 → 不写、False。成功写 → True。
    """
    if not ((output_dir or "").strip() and (card_name or "").strip()):
        return False
    if not ((user_name or "").strip() or (user_persona or "").strip()):
        return False
    p = card_dir(output_dir, card_name) / PERSONA_FILE
    if p.is_file():
        return False  # 已快照过 → 隔离不覆盖
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"name": user_name or "", "content": user_persona or ""},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return True


def read_work_persona(output_dir: str, card_name: str) -> dict | None:
    """读作品绑定的用户人设快照。无/损坏 → None（运行时回退前端透传的人设）。"""
    if not ((output_dir or "").strip() and (card_name or "").strip()):
        return None
    p = card_dir(output_dir, card_name) / PERSONA_FILE
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def read_worldbook(base: str, name: str) -> dict | None:
    """读卡内嵌世界书 worldbook.json（⑤ 条目级 CRUD 用）。无/损坏 → None。"""
    p = card_dir(base, name) / WORLDBOOK_FILE
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def write_worldbook(base: str, name: str, book: dict) -> None:
    """写回卡内嵌世界书（条目编辑后落盘）。卡文件夹须已存在。"""
    p = card_dir(base, name) / WORLDBOOK_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(book, ensure_ascii=False, indent=2), encoding="utf-8")


def has_chat(base: str, name: str) -> bool:
    """该卡是否已有对话记录（覆盖导入前用来决定是否提示导出）。"""
    p = card_dir(base, name) / CHAT_FILE
    if not p.is_file():
        return False
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    if isinstance(data, list):
        return len(data) > 0
    if isinstance(data, dict):
        return bool(data.get("messages"))
    return False


def read_chat(base: str, name: str) -> Any:
    """读该卡的对话记录（供覆盖前导出）。无则返回 None。"""
    p = card_dir(base, name) / CHAT_FILE
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save_card(
    base: str,
    card: NormalizedCard,
    *,
    avatar: bytes | None = None,
    overwrite: bool = False,
    keep_chat: bool = True,
) -> CardSummary:
    """把归一后的卡写入 <base>/<名字>/。

    overwrite=False 且目标已存在 → 抛 FileExistsError（调用方决定覆盖/导出）。
    keep_chat=True：覆盖时保留已有 chat.json（对话记录不因重装丢失）。
    """
    folder = card_dir(base, card.name)
    if folder.exists() and (folder / CARD_FILE).is_file() and not overwrite:
        raise FileExistsError(card.name)

    existing_chat = read_chat(base, card.name) if keep_chat else None
    folder.mkdir(parents=True, exist_ok=True)

    (folder / CARD_FILE).write_text(
        json.dumps(card.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_or_clear(folder / WORLDBOOK_FILE, card.character_book if card.has_worldbook else None)
    _write_or_clear(folder / REGEX_FILE, card.regex_scripts if card.has_regex else None)
    if avatar is not None:
        (folder / AVATAR_FILE).write_bytes(avatar)
    if existing_chat is not None:
        (folder / CHAT_FILE).write_text(
            json.dumps(existing_chat, ensure_ascii=False), encoding="utf-8"
        )
    return _summary(folder, card.name)


def _write_or_clear(path: Path, value: Any) -> None:
    """有值写 JSON；无值则删除旧文件（避免覆盖导入后残留上一张卡的世界书/正则）。"""
    if value:
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    elif path.is_file():
        path.unlink()


def list_cards(base: str) -> list[CardSummary]:
    root = Path(base)
    if not root.is_dir():
        return []
    out: list[CardSummary] = []
    for child in sorted(root.iterdir()):
        cf = child / CARD_FILE
        if not cf.is_file():
            continue
        try:
            name = json.loads(cf.read_text(encoding="utf-8")).get("name") or child.name
        except (json.JSONDecodeError, OSError):
            name = child.name
        out.append(_summary(child, name))
    return out


def scan_loose_cards(base: str, worldbook_dir: str = "") -> dict[str, list[str]]:
    """扫描角色卡文件夹**根目录**下用户手动放入的散装卡文件（.json/.png），解析后按标准结构
    落盘，成功后删除散装源文件。

    worldbook_dir 已设时：新入库卡 + 已存量卡的内嵌世界书都外拆成独立世界书（名=卡名）并从卡剥离，
    使内嵌书可见于独立世界书库、运行时不再叠加注入（幂等，独立库已有同名不覆盖）。

    这样"把卡丢进文件夹→刷新即出现"。已存在同名卡的散装文件跳过（不覆盖、保留源文件供用户手动处置）。
    返回 {imported:[名], skipped:[文件名], failed:[文件名]}。
    """
    from app.services import character_card
    root = Path(base)
    result: dict[str, list[str]] = {"imported": [], "skipped": [], "failed": []}
    if not root.is_dir():
        return result
    # 存量迁移：已入库卡若仍带内嵌世界书且已设独立世界书目录，外拆并剥离（幂等）
    if (worldbook_dir or "").strip():
        for c in list_cards(base):
            if c.has_worldbook:
                try:
                    extract_embedded_worldbook(base, c.name, worldbook_dir)
                except Exception:  # noqa: BLE001  单卡迁移失败不阻断扫描
                    pass
    for child in sorted(root.iterdir()):
        # 只处理根目录下的散装文件；卡文件夹（子目录）与非卡后缀跳过
        if not child.is_file() or child.suffix.lower() not in (".json", ".png"):
            continue
        try:
            raw = child.read_bytes()
            card = character_card.parse_card_bytes(raw, child.name)
        except Exception:  # noqa: BLE001  非卡/损坏文件：留着不动，不算失败
            result["failed"].append(child.name)
            continue
        if card_exists(base, card.name):
            result["skipped"].append(child.name)  # 已有同名卡 → 不覆盖，保留散装文件
            continue
        is_png = raw.startswith(character_card.PNG_SIGNATURE) or child.suffix.lower() == ".png"
        try:
            save_card(base, card, avatar=raw if is_png else None, overwrite=False)
        except Exception:  # noqa: BLE001  落盘失败：保留源文件
            result["failed"].append(child.name)
            continue
        if (worldbook_dir or "").strip():
            try:
                extract_embedded_worldbook(base, card.name, worldbook_dir)  # 内嵌世界书外拆+剥离
            except Exception:  # noqa: BLE001
                pass
        try:
            child.unlink()  # 成功入库 → 删散装源，避免重复扫描
        except OSError:
            pass
        result["imported"].append(card.name)
    return result


def extract_embedded_worldbook(base: str, name: str, worldbook_dir: str) -> bool:
    """把卡内嵌世界书外拆成独立世界书（名=卡名），并从卡里剥离，卡变干净。

    目的：内嵌书出现在「独立世界书」库里（可见、可编辑），且运行时不再与独立书叠加注入。
    仅当已设 worldbook_dir 且卡确有内嵌世界书才动。独立库已有同名书 → 不覆盖（保用户编辑），
    但仍剥离卡内嵌（自动绑会加载同名独立书）。成功剥离返回 True，无可拆/未设目录返回 False。
    """
    if not ((worldbook_dir or "").strip() and (base or "").strip() and (name or "").strip()):
        return False
    card = read_card(base, name)
    if not isinstance(card, dict):
        return False
    book = card.get("character_book")
    if not (isinstance(book, dict) and book.get("entries")):
        return False
    from app.services import worldbook_store
    if not worldbook_store.exists(worldbook_dir, name):
        try:
            worldbook_store.save(worldbook_dir, name, book, overwrite=False)
        except (FileExistsError, ValueError, OSError):
            return False
    # 从卡剥离：清 character_book + 删卡内嵌 worldbook.json（运行时改由独立书+自动绑加载）
    card["character_book"] = None
    folder = card_dir(base, name)
    (folder / CARD_FILE).write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    wb = folder / WORLDBOOK_FILE
    if wb.is_file():
        wb.unlink()
    return True


def read_card(base: str, name: str) -> dict[str, Any] | None:
    p = card_dir(base, name) / CARD_FILE
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


EDITABLE_CARD_FIELDS = ("description", "first_mes", "creator_notes")


def update_card_fields(base: str, name: str, updates: dict[str, Any]) -> dict[str, Any]:
    """只更新角色卡可编辑正文，保留其余格式字段、侧车与媒体。"""
    card = read_card(base, name)
    if card is None:
        raise FileNotFoundError(name)
    for field_name in EDITABLE_CARD_FIELDS:
        if field_name in updates:
            card[field_name] = str(updates[field_name] or "")
    target = card_dir(base, name) / CARD_FILE
    target.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    return card


def read_regex(base: str, name: str) -> list[dict[str, Any]]:
    """读该卡的内嵌正则脚本（regex.json，ST 格式数组）。无卡/无文件返回空。"""
    if not (base and name):
        return []
    p = card_dir(base, name) / REGEX_FILE
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return [s for s in data if isinstance(s, dict)] if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def delete_card(base: str, name: str) -> bool:
    folder = card_dir(base, name)
    if not folder.is_dir():
        return False
    shutil.rmtree(folder, ignore_errors=True)
    return not folder.exists()


def _summary(folder: Path, name: str) -> CardSummary:
    return CardSummary(
        name=name,
        folder=folder.name,
        has_worldbook=(folder / WORLDBOOK_FILE).is_file(),
        has_regex=(folder / REGEX_FILE).is_file(),
        has_chat=(folder / CHAT_FILE).is_file(),
        has_avatar=(folder / AVATAR_FILE).is_file(),
    )
