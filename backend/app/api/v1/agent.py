from typing import Any

from fastapi import APIRouter

from app.services.paypal_client import get_gemini_paypal_tools, get_mcp_tool_definitions

router = APIRouter(prefix="/agent", tags=["agent"])


@router.get("/tools")
def agent_tools() -> dict[str, Any]:
    """Agentic-commerce tool schemas for Gemini function calling and MCP ``tools/list``."""
    return {
        "gemini_function_declarations": get_gemini_paypal_tools(),
        "mcp_tools": get_mcp_tool_definitions(),
    }
