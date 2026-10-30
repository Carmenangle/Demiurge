"""密钥字段清单 + 整对象写入的空密钥护栏：后端唯一属主（前端投影是 stores/settings.ts 的 stripSecrets，改字段两边同步）。

三份消费面共用同一张表，禁止各抄一份：
- 配置包导出：默认不进包（`config_bundle._strip_secrets`）；
- user-state 写入：空密钥不得覆盖盘上非空值（`keep_stored_secrets`）；
- 手机本机模型写入：同一护栏（`services/mobile_local_models`）。
"""
SECRET_KEYS = frozenset({
    "apiKey",
    "hfToken",
    "civitaiToken",
    "smitheryKey",
    "pixivRefreshToken",
    "danbooruLogin",
    "danbooruApiKey",
})
MODEL_LIST_KEYS = ("chatModels", "imageModels", "videoModels")
MODEL_OBJ_KEYS = ("embedModel", "vlmModel")


def keep_stored_secrets(incoming: object, stored: object, key: str = "") -> object:
    """空密钥不覆盖盘上非空值（多客户端整对象覆盖写的写侧护栏）。

    localStorage 副本按 `stripSecrets` 清空了密钥，客户端启动回填一旦失败（后端忙/中继
    502）就会把这份空副本整份推回来；没有这层护栏，一次离线启动就能把盘上真 key 抹成
    空串（2026-10-28 实测：8 个模型 + embed + 各 token 全空，之后每轮生成 401）。
    非空值照常覆盖（换 key 生效）；模型条目按 id 对齐，被删掉的条目不会因此复活。
    密钥字段清单单一属主＝本模块 SECRET_KEYS（与配置包导出共用）。
    """
    if isinstance(incoming, dict):
        base = stored if isinstance(stored, dict) else {}
        merged = {k: keep_stored_secrets(v, base.get(k), k) for k, v in incoming.items()}
        # 旧 APK / 旧前端的 Settings 可能还没有某个密钥字段：缺席也按「空」处理，
        # 否则换端升级期一次整份上传就会把新字段从盘上删掉。
        for name in SECRET_KEYS:
            old = base.get(name)
            if name not in merged and isinstance(old, str) and old.strip():
                merged[name] = old
        return merged
    if isinstance(incoming, list):
        pool = stored if isinstance(stored, list) else []
        by_id = {it["id"]: it for it in pool if isinstance(it, dict) and isinstance(it.get("id"), str)}
        kept = []
        for index, item in enumerate(incoming):
            key_of = item.get("id") if isinstance(item, dict) else None
            if isinstance(key_of, str):
                ref = by_id.get(key_of)
            else:
                ref = pool[index] if index < len(pool) else None
            kept.append(keep_stored_secrets(item, ref, key))
        return kept
    if key in SECRET_KEYS and isinstance(stored, str) and stored.strip():
        if incoming is None or (isinstance(incoming, str) and not incoming.strip()):
            return stored
    return incoming
