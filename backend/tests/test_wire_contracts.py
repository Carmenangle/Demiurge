from app.generated.wire_contracts import AGENT_INVOCATION_WIRE_FIELDS
from app.routers.ai_agent import MultiAgentRequest


def test_multi_agent_request_covers_generated_wire_contract() -> None:
    assert AGENT_INVOCATION_WIRE_FIELDS <= set(MultiAgentRequest.model_fields)
