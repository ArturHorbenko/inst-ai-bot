import asyncio
import sys
from types import ModuleType


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


EXPECTED_TOOL_PARAMETERS = {
    "index_video_from_url": {"url"},
    "run_prompt": {"artifact_hash", "prompt", "model", "label", "metadata"},
    "get_artifact": {"content_hash"},
    "search_videos": {"query", "limit", "trait_schema", "prompt_version"},
    "get_video_context": {"content_hash", "media_id", "trait_schema", "prompt_version"},
    "get_current_creator_profile": {"days"},
    "list_recent_content": {"limit"},
    "get_content_analytics": {"media_id", "days"},
    "content_audit": {"days"},
}

READ_ONLY_TOOLS = {
    "get_artifact",
    "search_videos",
    "get_video_context",
    "get_current_creator_profile",
    "list_recent_content",
    "get_content_analytics",
    "content_audit",
}


def test_existing_single_creator_tool_contract_stays_available():
    tools = asyncio.run(mcp_server.mcp.list_tools())
    tools_by_name = {tool.name: tool for tool in tools}

    assert set(tools_by_name) == set(EXPECTED_TOOL_PARAMETERS)
    for tool_name, parameter_names in EXPECTED_TOOL_PARAMETERS.items():
        assert set(tools_by_name[tool_name].inputSchema["properties"]) == parameter_names

    assert mcp_server.mcp.settings.streamable_http_path == "/mcp"
    instructions = mcp_server.mcp.instructions.lower()
    assert "get_current_creator_profile" in instructions
    assert "one creator configured by the server" in instructions


def test_tools_advertise_titles_and_safety_annotations():
    tools = asyncio.run(mcp_server.mcp.list_tools())

    for tool in tools:
        assert tool.title
        assert tool.annotations is not None
        assert tool.annotations.destructiveHint is False
        assert tool.annotations.readOnlyHint is (tool.name in READ_ONLY_TOOLS)

    tools_by_name = {tool.name: tool for tool in tools}
    assert tools_by_name["index_video_from_url"].annotations.idempotentHint is True
    assert tools_by_name["index_video_from_url"].annotations.openWorldHint is True
    assert tools_by_name["run_prompt"].annotations.idempotentHint is False
    assert tools_by_name["run_prompt"].annotations.openWorldHint is True


def test_tools_advertise_structured_output_schemas():
    tools = asyncio.run(mcp_server.mcp.list_tools())
    tools_by_name = {tool.name: tool for tool in tools}

    for tool in tools:
        assert tool.outputSchema is not None, f"{tool.name} is missing outputSchema"
        assert tool.outputSchema["type"] == "object"

    assert "content_hash" in tools_by_name["index_video_from_url"].outputSchema["properties"]
    assert "run_id" in tools_by_name["run_prompt"].outputSchema["properties"]
    assert "window" in tools_by_name["get_current_creator_profile"].outputSchema["properties"]
    assert "media" in tools_by_name["get_content_analytics"].outputSchema["properties"]
    assert "coverage" in tools_by_name["content_audit"].outputSchema["properties"]

    # List return values are wrapped by FastMCP so structuredContent stays an object.
    assert "result" in tools_by_name["search_videos"].outputSchema["properties"]
    assert "result" in tools_by_name["list_recent_content"].outputSchema["properties"]
