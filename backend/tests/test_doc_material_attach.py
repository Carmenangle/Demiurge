"""链路③ 文档插图与预览回归（2026-09-10）。

用户诉求（原话）：整理的文档「会不会智能插入对应的素材图片」。
三层缺口此前都在：
1. 没有能力把素材放进文档可引用的位置 → 新增 `doc.attach_material`
   （素材在 `_web_materials/`、文档在 `docs/`，模型手写图片路径必裂：绝对路径在
   用户迁移文档目录后失效，相对路径又对不上）；
2. 产物白名单只认 card.json / worldbook.json → `docs/*.md` 既不被收集也不许预览下载，
   `docs/assets/*` 图片也不可访问（插在图里的素材必裂）；
3. 预览弹层按纯文本 `<pre>` 渲染 → md 不渲染、图不显示。
   本文件覆盖 1、2 的后端部分（3 的前端部分见 frontend/src/lib/docAssets.test.ts）。
"""
from __future__ import annotations

import base64

import pytest

from app.services import capability_handlers, capability_registry as cr, collection_artifacts

# 1x1 PNG（合法魔数）
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.fixture
def work(tmp_path):
    """作品根：含一张联网素材。"""
    root = tmp_path / "作品"
    (root / "_web_materials").mkdir(parents=True)
    (root / "_web_materials" / "m1.png").write_bytes(_PNG)
    return root


# ── 1. doc.attach_material ─────────────────────────────────────────────────

def test_能力登记与安全等级():
    cap = cr.get("doc.attach_material")
    assert cap is not None
    # 复制素材、同名不覆盖 → 可重复执行不破坏已插图（reversible）
    assert cap.side_effect_level == cr.SIDE_EFFECT_REVERSIBLE
    assert cap.category == "repo"
    assert "doc.attach_material" in cr.FABRIC_DOC_DELIVERY_OPS
    assert cr.validate_handlers() == []
    # B3/A（2026-09-10）：`repo_id` 从死参数→真生效的注入项。
    # B2 删它是对的（当时 base 注入的就是作品库根，落点无从收窄）；B3/A 让 base+repo_id
    # 能算出作品域（`<base>/<作品文件夹名>`），落点因此真能按作品收窄 → 参数恢复。
    assert cr.env_injected_params("doc.attach_material") == ("base", "repo_id")
    assert "repo_id" in (cap.params_schema or {}).get("properties", {})


def test_素材复制进docs_assets并回传可直接粘贴的markdown(work):
    result = capability_handlers.attach_material(
        base=str(work), src=str(work / "_web_materials" / "m1.png"),
        title="八千代立绘", doc_rel="设定总集.md")
    assert result["rel"] == "assets/m1.png"
    assert result["markdown"] == "![八千代立绘](assets/m1.png)"
    copied = work / "docs" / "assets" / "m1.png"
    assert copied.is_file() and copied.read_bytes() == _PNG
    assert result["path"] == str(copied)


def test_同名不覆盖追加序号(work):
    first = capability_handlers.attach_material(
        base=str(work), src=str(work / "_web_materials" / "m1.png"))
    second = capability_handlers.attach_material(
        base=str(work), src=str(work / "_web_materials" / "m1.png"))
    assert first["name"] == "m1.png"
    assert second["name"] == "m1-2.png"
    assert first["rel"] != second["rel"]
    # 第一次插入的图仍在（可重复执行不破坏已插图）
    assert (work / "docs" / "assets" / "m1.png").is_file()


def test_子目录文档回退上级目录(work):
    result = capability_handlers.attach_material(
        base=str(work), src=str(work / "_web_materials" / "m1.png"),
        doc_rel="子目录/设定.md")
    assert result["rel"] == "../assets/m1.png"


def test_作品域外素材被拒(tmp_path, work):
    outside = tmp_path / "外面.png"
    outside.write_bytes(_PNG)
    with pytest.raises(ValueError) as ei:
        capability_handlers.attach_material(base=str(work), src=str(outside))
    assert "作品目录内" in str(ei.value)


def test_非图片素材被拒(work):
    txt = work / "_web_materials" / "note.txt"
    txt.write_text("不是图片", encoding="utf-8")
    with pytest.raises(ValueError) as ei:
        capability_handlers.attach_material(base=str(work), src=str(txt))
    assert "仅支持图片素材" in str(ei.value)


def test_空src报错(work):
    """schema 已声明 src 必填；空串（模型传 ""）走可操作报错而不是 500。"""
    with pytest.raises(ValueError) as ei:
        capability_handlers.attach_material(base=str(work), src="   ")
    assert "src" in str(ei.value)


# ── 1b. 素材大小上限（2026-09-10 C1）─────────────────────────────────────────
# 此前 attach_material 无任何上限：一个被误指的巨型文件（视频/压缩包/超大原图）
# 会被整块读进内存并复制进 docs/assets/。上限单一属主 = collection_artifacts。

def test_插图上限常量与白名单同属主(work):
    """上限必须来自 collection_artifacts（与后缀白名单同一处），且足够容纳正常生图产物。"""
    assert capability_handlers.DOC_ASSET_MAX_BYTES == collection_artifacts.DOC_ASSET_MAX_BYTES
    # 对齐 generation_store 的生图产物上限（30MB）：正常生图产物一定能插图
    assert capability_handlers.DOC_ASSET_MAX_BYTES == 30 * 1024 * 1024


def test_超过单文件上限的素材被拒并说明实际大小(work, monkeypatch):
    monkeypatch.setattr(capability_handlers, "DOC_ASSET_MAX_BYTES", 1024)
    big = work / "_web_materials" / "big.png"
    big.write_bytes(_PNG + b"\x00" * 2048)
    with pytest.raises(ValueError) as ei:
        capability_handlers.attach_material(base=str(work), src=str(big))
    msg = str(ei.value)
    assert "单文件上限" in msg and "0.0MB" in msg
    # 拒绝发生在落盘前：不留下半张素材
    assert not (work / "docs" / "assets").exists()


def test_上限边界值本身放行且严格大于才拒(work, monkeypatch):
    """`>` 而非 `>=`：恰好等于上限的素材合法（否则边界值会成为例外）。"""
    monkeypatch.setattr(capability_handlers, "DOC_ASSET_MAX_BYTES", len(_PNG))
    exact = work / "_web_materials" / "exact.png"
    exact.write_bytes(_PNG)
    result = capability_handlers.attach_material(base=str(work), src=str(exact))
    assert result["bytes"] == len(_PNG)
    over = work / "_web_materials" / "over.png"
    over.write_bytes(_PNG + b"\x00")
    with pytest.raises(ValueError) as ei:
        capability_handlers.attach_material(base=str(work), src=str(over))
    assert "单文件上限" in str(ei.value)


# ── 2. 产物白名单与收集支持文档 ─────────────────────────────────────────────

def _seed_doc_work(work):
    docs = work / "docs"
    (docs / "assets").mkdir(parents=True, exist_ok=True)
    (docs / "设定总集.md").write_text("# 设定总集\n\n![图](assets/pic.png)\n", encoding="utf-8")
    (docs / "assets" / "pic.png").write_bytes(_PNG)
    return docs


def test_白名单放行文档与插图素材(work):
    _seed_doc_work(work)
    root = work.resolve()
    # 放行：docs/*.md、docs/assets/*.图片
    assert collection_artifacts.resolve_artifact(str(root), str(work / "docs" / "设定总集.md"))
    assert collection_artifacts.resolve_artifact(str(root), str(work / "docs" / "assets" / "pic.png"))
    # 拒绝：docs 下的非 md、assets 下的非图片、作品根下的普通文件
    (work / "docs" / "note.txt").write_text("x", encoding="utf-8")
    (work / "docs" / "assets" / "note.txt").write_text("x", encoding="utf-8")
    (work / "随便.json").write_text("{}", encoding="utf-8")
    for bad in ("docs/note.txt", "docs/assets/note.txt", "随便.json"):
        with pytest.raises(collection_artifacts.ArtifactAccessError) as ei:
            collection_artifacts.resolve_artifact(str(root), str(work / bad))
        assert ei.value.status == 403
    # 目录 jail 仍生效（越出作品根）
    outside = work.parent / "外面.md"
    outside.write_text("x", encoding="utf-8")
    with pytest.raises(collection_artifacts.ArtifactAccessError) as ei:
        collection_artifacts.resolve_artifact(str(root), str(outside))
    assert ei.value.status == 403


def test_artifact_kind单一属主(work):
    assert collection_artifacts.artifact_kind(work / "a" / "card.json") == "card"
    assert collection_artifacts.artifact_kind(work / "a" / "worldbook.json") == "worldbook"
    assert collection_artifacts.artifact_kind(work / "docs" / "设定总集.md") == "doc"
    assert collection_artifacts.artifact_kind(work / "x.png") == "file"


def test_kind只描述形态不构成授权(work):
    """B4（2026-09-10 措辞修正）：`artifact_kind` 与 `is_allowed_artifact` 共用同一批常量，
    但**不等价**——前者只看名字/后缀（形态，前端据此选渲染方式），后者才校验位置。

    此前文档里写成「与白名单同源判定」并不严格成立：`docs/sub/x.md` 的 kind 是 `doc`，
    而白名单是 False。若有人据此认为「kind=doc 就能放行」，就会把未授权路径也放出去。
    """
    root = work.resolve()
    nested = root / "docs" / "sub" / "x.md"
    assert collection_artifacts.artifact_kind(nested) == "doc"          # 形态像文档
    assert not collection_artifacts.is_allowed_artifact(nested, root)   # 但不是授权
    stray_png = root / "x.png"
    assert collection_artifacts.artifact_kind(stray_png) == "file"
    assert not collection_artifacts.is_allowed_artifact(stray_png, root)
    # 反方向同样说明「kind 不是授权」：白名单放行的插图素材 kind 是 file（图片不在
    # card|worldbook|doc 三态里），它只经 /asset 供 <img> 用、从不进产物列表——
    # 能不能访问一律由白名单决定，没有任何地方读 kind 来放行。
    pic = root / "docs" / "assets" / "pic.png"
    assert collection_artifacts.is_allowed_artifact(pic, root) is True
    assert collection_artifacts.artifact_kind(pic) == "file"


def test_插图后缀与产物白名单同源(work):
    """B1（2026-09-10）：attach_material 能写进 docs/assets/ 的后缀，白名单必须能预览。

    两份后缀列表分头维护的后果是「静默裂图」——图写进 docs/assets/ 但收集与预览
    一律 403，产物卡上看不到、日志也不报错，只有用户打开文档才发现图裂了。
    因此单一属主 = `collection_artifacts.DOC_ASSET_SUFFIXES`，本能力只允许派生。
    """
    root = work.resolve()
    allowed = {suffix.lstrip(".").lower() for suffix in collection_artifacts.DOC_ASSET_SUFFIXES}
    assert capability_handlers._DOC_IMAGE_EXTS == allowed
    assert "jpeg" in allowed  # 魔数检测返回 "jpeg"（不是 "jpg"），派生口径必须含它
    for ext in sorted(allowed):
        target = root / "docs" / "assets" / f"x.{ext}"
        assert collection_artifacts.is_allowed_artifact(target, root), ext


def test_魔数检测结果落在插图白名单内(work):
    """把「检测名 → 后缀集合」钉死：别名表里不在白名单的格式（heic/tiff）必须被拒。"""
    from app.services.image_magic import IMAGE_EXTENSION_ALIASES, detect_image_format

    assert detect_image_format(_PNG) == "png"
    assert detect_image_format(b"\xff\xd8\xff" + b"\x00" * 20) == "jpeg"
    assert IMAGE_EXTENSION_ALIASES["jpg"] == "jpeg"
    writable = capability_handlers._DOC_IMAGE_EXTS
    assert {"png", "jpeg", "webp", "gif", "bmp", "avif"} <= writable
    assert "heic" not in writable and "tiff" not in writable


def test_纯文档交付也能收集(work):
    """固化04 场景：没有卡、没有世界书，只有 docs/设定总集.md——旧实现在此处 return []。"""
    _seed_doc_work(work)
    items = collection_artifacts.collect_artifacts(str(work))
    assert [i["kind"] for i in items] == ["doc"]
    assert items[0]["name"] == "文档 · 设定总集"
    # 插图素材不进卡片列表（只作为文档内联资源可访问）
    assert all("assets" not in i["path"] for i in items)


def test_卡与文档并存都展示(work):
    _seed_doc_work(work)
    card_dir = work / "玫瑰与繁花"
    card_dir.mkdir()
    (card_dir / "card.json").write_text('{"name":"玫瑰与繁花"}', encoding="utf-8")
    items = collection_artifacts.collect_artifacts(str(work))
    kinds = {i["kind"] for i in items}
    assert {"card", "doc"} <= kinds


def test_since过滤只收本轮写入(work):
    _seed_doc_work(work)
    future = (work / "docs" / "设定总集.md").stat().st_mtime + 60
    assert collection_artifacts.collect_artifacts(str(work), since=future) == []
