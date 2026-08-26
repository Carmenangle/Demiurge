"""V1.5 高潮模式视频提示词 + 视频参数探针：用真实后端链路 dry-run 供人工核对。

不依赖视频模型/工作流：直接构造一个剧情高潮 rec（scene_spec + motion + video_config），
走 agent_graph._streamed_illustration_events → 事件里附带 video_prompt（提示词）
+ video_params（结构化视频参数）。

用途：测试「高潮模式生成视频提示词是否符合要求」+「视频参数有没有正确上传」——
区块完整性 / 无破甲残留 / 动作·运镜·特效随 motion 强度 / 模型名·画幅·时长·参考图·缺图警告。
"""
import json
import sys

sys.path.insert(0, r"D:\tool\Demiurge\backend")

from app.services import agent_graph as ag

# 模拟真实请求里的视频配置：未配置视频模型/工作流时 model/base_url 为空，画幅默认 16:9
VIDEO_CONFIG = {
    "base_url": "",          # 没配视频工作流 → 空
    "model": "",             # 没配视频模型 → 空
    "size": "1280x720",      # 视频默认 16:9（R9）
    "proxy": "",
}

# 三档 motion 各跑一遍，核对运镜/特效是否随强度变化
SPECS = [
    {
        "label": "motion=3（强动态）",
        "motion": 3,
        "scene_spec": {
            "narrative": "温知夏猛地起身，椅子向后倒去，一把攥住林屿的手腕",
            "appearance": "温知夏米色针织开衫，栗色长发",
            "wardrobe": "全员日常私服",
            "locale": "温暖小面馆内景，暖黄吊灯",
            "actors": ["温知夏", "林屿"],
            "rating": "sfw",
            "negative_prompt": "低质量；畸形手；文字水印",
            "aspect_ratio": "16:9",
        },
    },
    {
        "label": "motion=2（中动态）",
        "motion": 2,
        "scene_spec": {
            "narrative": "林屿抬眼，与温知夏目光相撞",
            "appearance": "林屿深色夹克，短寸",
            "wardrobe": "全员日常私服",
            "locale": "面馆靠窗位，雨夜玻璃",
            "actors": ["林屿", "温知夏"],
            "rating": "sfw",
            "aspect_ratio": "16:9",
        },
    },
    {
        "label": "motion=0（弱动态）",
        "motion": 0,
        "scene_spec": {
            "narrative": "三人围坐，举杯轻碰",
            "appearance": "温知夏米色针织开衫",
            "wardrobe": "全员日常私服",
            "locale": "面馆内景",
            "actors": ["温知夏", "林屿", "苏绾"],
            "rating": "sfw",
            "aspect_ratio": "16:9",
        },
    },
    {
        "label": "motion=3 + 画面级要素（优先用主模型提炼的英文画面，不用陈旧叙事）",
        "motion": 3,
        "scene_spec": {
            "narrative": "陈旧叙事：孤儿院院长看向石凳上的黑色匣子。",
            "appearance": "冷倾雪(白衣+长剑)",
            "wardrobe": "战斗装束",
            "locale": "雷雨中的山巅",
            "actors": ["冷倾雪"],
            "rating": "sfw",
            "subjects": [{"name": "冷倾雪", "description": "drawing sword, crimson cloak in lightning", "weight": 1.2}],
            "visual_facts": [{"kind": "action", "fact": "leaps upward with blade raised", "evidence": "拔剑跃起"}],
            "composition": "low-angle dynamic shot",
            "camera": "fast tracking push-in",
            "aspect_ratio": "16:9",
        },
    },
]

for i, spec in enumerate(SPECS):
    rec = {
        "id": f"slot-{i + 1}",
        "prompt": "booru prompt placeholder",
        "motion": spec["motion"],
        "actors": spec["scene_spec"]["actors"],
        "anchor_offset": 0,
        "scene_spec": spec["scene_spec"],
        "video_config": dict(VIDEO_CONFIG),
    }
    events = ag._streamed_illustration_events([rec])
    request = events[0]["illustrate_request"]
    print("=" * 70)
    print(f"【{spec['label']}】")
    print("-" * 70)
    print(request.get("video_prompt", "<无 video_prompt>"))
    print()
    print("[视频参数]（dry-run，测试「参数有没有上传」）")
    print(json.dumps(request.get("video_params", {}), ensure_ascii=False, indent=2))
    print()

# 同时导出完整 wire（含 video_prompt + video_params）供前端契约测试复用
from app.services import chat_stream_protocol as protocol

wire = [protocol.encode_event(ev) for ev in ag._streamed_illustration_events([
    {
        "id": "slot-1",
        "prompt": "booru prompt",
        "motion": 3,
        "actors": ["温知夏", "林屿"],
        "anchor_offset": 0,
        "video_config": dict(VIDEO_CONFIG),
        "scene_spec": {
            "narrative": "温知夏猛地起身，攥住林屿手腕",
            "appearance": "温知夏米色针织开衫",
            "wardrobe": "全员日常私服",
            "locale": "面馆内景",
            "actors": ["温知夏", "林屿"],
            "rating": "sfw",
            "negative_prompt": "低质量；畸形手",
            "aspect_ratio": "16:9",
        },
    },
])]
out_path = r"D:\tool\Demiurge\frontend\src\api\__fixtures__\b2_climax_video_prompt.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(wire, f, ensure_ascii=False, indent=2)
print(f"wire 已导出 → {out_path}")
