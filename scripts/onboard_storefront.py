import json
import os
import sys

import httpx


def main() -> None:
    tenant_id = sys.argv[1] if len(sys.argv) > 1 else "shop-1042"
    response = httpx.request(
        method="POST",
        url=os.environ.get("CHAT_SERVICE_URL", "http://127.0.0.1:8000") + "/admin/tenants",
        json={"tenant_id": tenant_id, "store_name": "Northwind Goods", "admin_id": "owner-7"},
        timeout=10.0,
    )
    response.raise_for_status()
    print(json.dumps(response.json(), indent=2))


if __name__ == "__main__":
    main()
