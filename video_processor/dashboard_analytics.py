"""Read-only client for the Instagram analytics dashboard's MCP data surface."""
from typing import Literal, Optional

import httpx

ContentTypeFilter = Literal["all", "reels", "posts"]
CommercialContextFilter = Literal["organic", "sponsored_partner", "unclear"]


class DashboardAnalyticsClient:
    def __init__(self, base_url: Optional[str], api_key: Optional[str], transport=None):
        self._base_url = (base_url or "").rstrip("/")
        self._api_key = api_key
        self._transport = transport or httpx.get

    def _get(self, path: str, params: dict) -> dict:
        if not self._base_url or not self._api_key:
            raise RuntimeError(
                "Dashboard analytics is not configured. Set ANALYTICS_DASHBOARD_URL and ANALYTICS_DASHBOARD_API_KEY."
            )
        try:
            response = self._transport(
                f"{self._base_url}{path}",
                params=params,
                headers={"X-MCP-Read-Secret": self._api_key},
                timeout=15,
            )
        except httpx.HTTPError as e:
            raise RuntimeError(f"Dashboard analytics request failed: {e}") from e
        try:
            payload = response.json()
        except ValueError as e:
            raise RuntimeError("Dashboard analytics returned invalid JSON.") from e
        if not response.is_success or not payload.get("ok"):
            raise RuntimeError(payload.get("error") or f"Dashboard analytics HTTP {response.status_code}")
        return payload

    def list_recent_content(
        self,
        limit: int = 10,
        *,
        content_type: ContentTypeFilter = "all",
        commercial_context: Optional[CommercialContextFilter] = None,
        page: int = 1,
    ) -> list[dict]:
        params = {"limit": limit}
        if content_type != "all":
            params["contentType"] = content_type
        if commercial_context is not None:
            params["commercialContext"] = commercial_context
        if page != 1:
            params["page"] = page
        return self._get("/api/internal/mcp/content", params)["content"]

    def get_content_analytics(self, media_id: str, days: int = 30) -> dict:
        return self._get("/api/internal/mcp/content", {"mediaId": media_id, "days": days})["content"]

    def get_content_audit(self, days: int = 30) -> dict:
        return self._get(
            "/api/internal/mcp/audit",
            {"days": days, "includeArchived": "true"},
        )["audit"]

    def get_current_creator_profile(self, days: int = 60) -> dict:
        return self._get("/api/internal/mcp/profile", {"days": days})["profile"]
