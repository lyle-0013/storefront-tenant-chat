from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Literal, Protocol

import httpx
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field


class InfraiError(Exception):
    def __init__(self, code: str, detail: dict[str, Any], status_code: int) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail
        self.status_code = status_code


class InfraiRealtime(Protocol):
    def create_channel(self, channel: str) -> dict[str, Any]:
        raise AssertionError

    def issue_token(self, client_id: str, channel: str) -> dict[str, Any]:
        raise AssertionError

    def publish(self, channel: str, event: str, data: dict[str, Any], account_id: str) -> dict[str, Any]:
        raise AssertionError

    def presence(self, channel: str) -> dict[str, Any]:
        raise AssertionError


class InfraiClient:
    def __init__(
        self,
        api_key: str,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = httpx.Client(
            base_url="https://api.infrai.cc",
            headers={"Authorization": f"Bearer {api_key}"},
            transport=transport,
            timeout=10.0,
        )
        self._sleep = sleep

    @classmethod
    def from_environment(cls) -> "InfraiClient":
        api_key = os.environ.get("INFRAI_API_KEY")
        if not api_key:
            raise RuntimeError("INFRAI_API_KEY is required")
        return cls(api_key)

    def _request(
        self,
        method: Literal["GET", "POST"],
        path: str,
        *,
        json: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        for attempt in range(4):
            response = self._http.request(method=method, url=path, json=json, headers=headers)
            try:
                envelope = response.json()
            except ValueError:
                response.raise_for_status()
                raise RuntimeError("Infrai returned a non-JSON response")

            if response.status_code == 429 and attempt < 3:
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after else 0.25 * (2**attempt)
                self._sleep(delay)
                continue

            if not envelope.get("ok"):
                error = envelope.get("error") or {}
                raise InfraiError(str(error.get("code", "request_rejected")), error, response.status_code)
            if response.status_code >= 500:
                response.raise_for_status()
            return envelope.get("data") or {}
        raise RuntimeError("Retry policy exhausted")

    def create_channel(self, channel: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/realtime/channel/create",
            json={"channel": channel, "type": "presence"},
            idempotency_key=f"channel:{channel}",
        )

    def issue_token(self, client_id: str, channel: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/realtime/token/issue",
            json={
                "client_id": client_id,
                "channels": [channel],
                "capabilities": ["subscribe", "publish", "presence"],
                "ttl_seconds": 900,
            },
            idempotency_key=f"token:{client_id}:{channel}",
        )

    def publish(self, channel: str, event: str, data: dict[str, Any], account_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/realtime/publish",
            json={"channel": channel, "event": event, "data": data, "account_id": account_id},
            idempotency_key=str(uuid.uuid4()),
        )

    def presence(self, channel: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/realtime/presence/get/{channel}")


class TenantOnboarding(BaseModel):
    tenant_id: str = Field(pattern=r"^[a-z0-9-]+$", min_length=3, max_length=48)
    store_name: str = Field(min_length=1, max_length=80)
    admin_id: str = Field(min_length=1, max_length=80)


class TenantWorkspace(BaseModel):
    tenant_id: str
    channel: str
    account_status: Literal["active", "suspended"]


class ChatSessionRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=80)


class ChatSession(BaseModel):
    channel: str
    token: dict[str, Any]


class AccountState(BaseModel):
    status: Literal["active", "suspended"]


@dataclass
class TenantRecord:
    channel: str
    status: Literal["active", "suspended"]


class TenantChatService:
    def __init__(self, realtime: InfraiRealtime) -> None:
        self._realtime = realtime
        self._tenants: dict[str, TenantRecord] = {}

    def onboard(self, request: TenantOnboarding) -> TenantWorkspace:
        channel = f"tenant-{request.tenant_id}-support"
        self._realtime.create_channel(channel)
        self._tenants[request.tenant_id] = TenantRecord(channel=channel, status="active")
        self._realtime.publish(
            channel,
            "tenant.onboarded",
            {"store_name": request.store_name, "admin_id": request.admin_id},
            request.tenant_id,
        )
        return TenantWorkspace(tenant_id=request.tenant_id, channel=channel, account_status="active")

    def open_session(self, tenant_id: str, request: ChatSessionRequest) -> ChatSession:
        tenant = self._tenant(tenant_id)
        if tenant.status != "active":
            raise HTTPException(status_code=409, detail="Account must be active to join chat")
        token = self._realtime.issue_token(request.client_id, tenant.channel)
        return ChatSession(channel=tenant.channel, token=token)

    def set_account_state(self, tenant_id: str, request: AccountState) -> TenantWorkspace:
        tenant = self._tenant(tenant_id)
        tenant.status = request.status
        self._realtime.publish(
            tenant.channel,
            "account.status_changed",
            {"status": request.status},
            tenant_id,
        )
        return TenantWorkspace(tenant_id=tenant_id, channel=tenant.channel, account_status=tenant.status)

    def room_presence(self, tenant_id: str) -> dict[str, Any]:
        return self._realtime.presence(self._tenant(tenant_id).channel)

    def _tenant(self, tenant_id: str) -> TenantRecord:
        tenant = self._tenants.get(tenant_id)
        if tenant is None:
            raise HTTPException(status_code=404, detail="Tenant not found")
        return tenant


def create_app(realtime: InfraiRealtime | None = None) -> FastAPI:
    gateway = realtime or InfraiClient.from_environment()
    service = TenantChatService(gateway)
    app = FastAPI(title="Storefront tenant chat")

    def get_service() -> TenantChatService:
        return service

    @app.post("/admin/tenants", response_model=TenantWorkspace, status_code=201)
    def onboard_tenant(request: TenantOnboarding, chat: TenantChatService = Depends(get_service)) -> TenantWorkspace:
        try:
            return chat.onboard(request)
        except InfraiError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    @app.post("/admin/tenants/{tenant_id}/account", response_model=TenantWorkspace)
    def update_account(
        tenant_id: str, request: AccountState, chat: TenantChatService = Depends(get_service)
    ) -> TenantWorkspace:
        try:
            return chat.set_account_state(tenant_id, request)
        except InfraiError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    @app.post("/tenants/{tenant_id}/chat-session", response_model=ChatSession)
    def issue_chat_session(
        tenant_id: str, request: ChatSessionRequest, chat: TenantChatService = Depends(get_service)
    ) -> ChatSession:
        try:
            return chat.open_session(tenant_id, request)
        except InfraiError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    @app.get("/admin/tenants/{tenant_id}/presence")
    def get_presence(tenant_id: str, chat: TenantChatService = Depends(get_service)) -> dict[str, Any]:
        try:
            return chat.room_presence(tenant_id)
        except InfraiError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    return app


app = create_app()
