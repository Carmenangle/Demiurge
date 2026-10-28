"""密钥字段清单：后端唯一属主（前端投影是 stores/settings.ts 的 stripSecrets，改字段两边同步）。

两份消费面共用同一张表，禁止各抄一份：
- 配置包导出：默认不进包（`config_bundle._strip_secrets`）；
- user-state 写入：空密钥不得覆盖盘上非空值（`routers/user_state._keep_stored_secrets`）。
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
