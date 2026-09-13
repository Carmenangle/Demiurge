import pytest
from pydantic import ValidationError

from app.routers.ai_agent import MultiAgentRequest


def test_edit_mode_request_contract():
    assert MultiAgentRequest().workspace_mode == "story"
    assert MultiAgentRequest(workspace_mode="edit").workspace_mode == "edit"
    with pytest.raises(ValidationError):
        MultiAgentRequest(workspace_mode="code")
