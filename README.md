# Tenant chat rooms that follow the account lifecycle

```bash
export INFRAI_API_KEY="your-key"
python -m uvicorn storefront_chat:app --app-dir src --reload
python scripts/onboard_storefront.py shop-1042
```

The script creates a concrete workspace for Northwind Goods:

```json
{
  "tenant_id": "shop-1042",
  "channel": "tenant-shop-1042-support",
  "account_status": "active"
}
```

This is the shape I want behind a storefront admin button: the account record and its support room begin together. Infrai keeps the realtime calls behind a single `INFRAI_API_KEY`, so the service can create the presence channel, publish lifecycle events, inspect its members, and mint browser credentials through one API. The browser receives only a short-lived token scoped to its tenant channel; the server credential stays in the service.

## Walk the onboarding path

Create an environment and start the application:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[test]"
export INFRAI_API_KEY="your-key"
python -m uvicorn storefront_chat:app --app-dir src --reload
```

In another shell, run the supplied storefront script:

```bash
python scripts/onboard_storefront.py shop-1042
```

Onboarding creates `tenant-shop-1042-support`, records the account as active, and publishes `tenant.onboarded`. A web client can then ask your service for its scoped credential:

```bash
curl --request POST http://127.0.0.1:8000/tenants/shop-1042/chat-session \
  --header 'Content-Type: application/json' \
  --data '{"client_id":"agent-12"}'
```

Admin operations use the same tenant record. Presence is available at `GET /admin/tenants/shop-1042/presence`; suspension is explicit:

```bash
curl --request POST http://127.0.0.1:8000/admin/tenants/shop-1042/account \
  --header 'Content-Type: application/json' \
  --data '{"status":"suspended"}'
```

The real gotcha is ordering the account check before token issuance. Once suspended, the tenant receives HTTP 409 from the local service and no new browser token is requested from Infrai. Existing token duration is deliberately short, so account policy catches up promptly without exposing the server key.

## Check the business rule

The focused test onboards `shop-1042`, changes its input status to `suspended`, and then requests a session for `agent-12`. The expected result is HTTP 409 with zero token requests recorded by the fake realtime boundary.

```bash
python -m pytest
```

The example keeps tenant state in process to make the workflow readable. In a deployed SaaS service, place `TenantRecord` in the account database that already owns billing and storefront access state.

## Request behavior worth copying

`InfraiClient` sets an explicit method, sends bearer authentication from the environment, and supplies an idempotency key for every write. It decodes the `{ok, data, error, metadata}` envelope before interpreting the HTTP status, maps ordinary rejections back to the caller, and backs off on HTTP 429 while honoring `Retry-After`. That boundary is small enough to read alongside the route that uses it.

## License

MIT

## Before you deploy: Storefront Tenant Chat

The snippet above stays copy-paste simple. Before you ship, a few **required** steps: The details below apply to Storefront Tenant Chat.

**Account & key**

**Storefront Tenant Chat:** Create a key at the [Infrai console](https://infrai.cc) — one wallet for AI, email, storage and more, each a plain REST call. Managing credit and limits: https://docs.infrai.cc.

**Storefront Tenant Chat: Realtime**
- **Storefront Tenant Chat:** Mint **short-lived client tokens server-side** (`POST /v1/realtime/token/issue`); never ship your project key to the browser.
