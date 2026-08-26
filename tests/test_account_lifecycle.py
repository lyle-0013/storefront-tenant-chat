import os
from typing import Any

import pytest
from fastapi import HTTPException

os.environ.setdefault("INFRAI_API_KEY", "test-key")

from storefront_chat import AccountState, ChatSessionRequest, TenantChatService, TenantOnboarding


class RecordingRealtime:
    def __init__(self) -> None:
        self.token_requests: list[tuple[str, str]] = []

    def create_channel(self, channel: str) -> dict[str, Any]:
        return {"channel": channel}

    def issue_token(self, client_id: str, channel: str) -> dict[str, Any]:
        self.token_requests.append((client_id, channel))
        return {"token": "browser-session-token"}

    def publish(self, channel: str, event: str, data: dict[str, Any], account_id: str) -> dict[str, Any]:
        return {"published": True}

    def presence(self, channel: str) -> dict[str, Any]:
        return {"channel": channel, "members": []}


def test_suspended_storefront_cannot_receive_a_chat_token() -> None:
    realtime = RecordingRealtime()
    service = TenantChatService(realtime)
    service.onboard(TenantOnboarding(tenant_id="shop-1042", store_name="Northwind Goods", admin_id="owner-7"))
    service.set_account_state("shop-1042", AccountState(status="suspended"))

    with pytest.raises(HTTPException) as rejected:
        service.open_session("shop-1042", ChatSessionRequest(client_id="agent-12"))

    assert rejected.value.status_code == 409
    assert realtime.token_requests == []
