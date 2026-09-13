"""B1 端到端验证：用真实后端链路生成 wire JSON（供前端真实代码消费）。

链路：agent_graph._streamed_illustration_events → chat_stream_protocol.encode_event → JSON。
"""
import json
import sys

sys.path.insert(0, r"D:\tool\Demiurge\backend")

from app.services import agent_graph as ag
from app.services import chat_stream_protocol as protocol

rec = {
    "id": "slot-1",
    "prompt": "三人举杯同框，暖黄吊灯",
    "motion": 3,
    "actors": ["温知夏", "林屿", "苏绾"],
    "anchor_offset": 12,
    "video_mode": "firstlast",
    "first_frame_desc": "雨夜门口，温知夏收伞，暖黄灯笼倒影",
    "last_frame_desc": "三人举杯同框，温情对视",
    "prev_tail_desc": "上一楼层：林屿在门口抽烟回望",
    "last_frame_url": "data:image/png;base64,ZmFrZS10YWlsLWZyYW1l",
    "scene_spec": {
        "narrative": "三人围坐举杯，气氛升温",
        "appearance": "温知夏米色针织开衫",
        "wardrobe": "全员日常私服",
        "locale": "温暖小面馆内景",
        "actors": ["温知夏", "林屿", "苏绾"],
        "rating": "sfw",
        "aspect_ratio": "16:9",
    },
}

# 流式插画事件：正文已流式，只发槽位 + 偏移
sink_events = ag._streamed_illustration_events([rec])
wire = [protocol.encode_event(ev) for ev in sink_events]

payload = json.dumps(wire, ensure_ascii=False, indent=2)
print(payload)

with open(r"D:\tool\Demiurge\frontend\src\api\__fixtures__\b1_illustrate_request.json",
          "w", encoding="utf-8") as f:
    f.write(payload)
print("\n--- written to frontend/src/api/__fixtures__/b1_illustrate_request.json ---")
