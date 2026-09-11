# Local AI Frontend - ComfyUI 锁定扩展
# 纯 web 扩展，不注册任何节点；通过 extra_model_paths 外挂加载，不改 ComfyUI 本体。
WEB_DIRECTORY = "./js"
NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
