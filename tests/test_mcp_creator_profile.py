from types import ModuleType
import sys
from unittest.mock import Mock


if "groq" not in sys.modules:
    groq = ModuleType("groq")
    groq.Groq = object
    sys.modules["groq"] = groq

if "google.genai" not in sys.modules:
    google = ModuleType("google")
    genai = ModuleType("google.genai")
    genai.Client = object
    genai.types = ModuleType("google.genai.types")
    google.genai = genai
    sys.modules["google"] = google
    sys.modules["google.genai"] = genai
    sys.modules["google.genai.types"] = genai.types

from video_processor import mcp_server


def test_get_current_creator_profile_delegates_days_to_dashboard_client(monkeypatch):
    dashboard_client = Mock()
    dashboard_client.get_current_creator_profile.return_value = {"window": {"days": 45}}
    monkeypatch.setattr(mcp_server, "_dashboard_client", lambda: dashboard_client)

    assert mcp_server.get_current_creator_profile(45) == {"window": {"days": 45}}
    dashboard_client.get_current_creator_profile.assert_called_once_with(45)


def test_creator_workflows_load_guides_and_simple_lookups_remain_direct():
    instructions = mcp_server.mcp.instructions.lower()

    assert "get_workflow('adapt-reel')" in instructions[:512]
    assert "get_workflow('performance-audit')" in instructions[:512]
    assert "get_current_creator_profile" in instructions
    assert "simple data lookups do not require a workflow or profile call" in instructions
