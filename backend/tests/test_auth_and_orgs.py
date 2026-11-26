from __future__ import annotations

import httpx

from app.services.auth_service import provision_supabase_user


async def _register(
    client: httpx.AsyncClient,
    email: str,
    org_name: str,
    password: str = "Str0ngPass!23",
) -> dict:
    resp = await client.post(
        "/v1/auth/register",
        json={
            "email": email,
            "password": password,
            "full_name": "Test Owner",
            "organisation_name": org_name,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_auth_lifecycle_and_password_reset(client: httpx.AsyncClient) -> None:
    reg = await _register(client, "alice@acme.test", "Acme Finance")
    token = reg["access_token"]
    assert reg["role"] == "owner"
    assert reg["organisation"]["name"] == "Acme Finance"

    me = await client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["user"]["email"] == "alice@acme.test"
    assert "api_keys:manage" in me.json()["permissions"]

    logout = await client.post("/v1/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert logout.status_code == 204

    me_after = await client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_after.status_code == 401

    reset_req = await client.post(
        "/v1/auth/password-reset/request",
        json={"email": "alice@acme.test"},
    )
    assert reset_req.status_code == 200
    reset_token = reset_req.json()["dev_reset_token"]
    assert reset_token

    confirm = await client.post(
        "/v1/auth/password-reset/confirm",
        json={"token": reset_token, "new_password": "NewStr0ngPass!99"},
    )
    assert confirm.status_code == 200

    old_login = await client.post(
        "/v1/auth/login",
        json={"email": "alice@acme.test", "password": "Str0ngPass!23"},
    )
    assert old_login.status_code == 401

    new_login = await client.post(
        "/v1/auth/login",
        json={"email": "alice@acme.test", "password": "NewStr0ngPass!99"},
    )
    assert new_login.status_code == 200
    assert new_login.json()["access_token"]


async def test_organisation_isolation_and_rbac(client: httpx.AsyncClient) -> None:
    org_a = await _register(client, "owner-a@alpha.test", "Alpha Corp")
    org_b = await _register(client, "owner-b@beta.test", "Beta Corp")

    headers_a = {"Authorization": f"Bearer {org_a['access_token']}"}
    headers_b = {"Authorization": f"Bearer {org_b['access_token']}"}

    # Add a viewer to Org A
    add_viewer = await client.post(
        "/v1/organisation/members",
        headers=headers_a,
        json={
            "email": "viewer@alpha.test",
            "full_name": "Viewer User",
            "password": "ViewerPass!23",
            "role": "viewer",
        },
    )
    assert add_viewer.status_code == 201

    viewer_login = await client.post(
        "/v1/auth/login",
        json={"email": "viewer@alpha.test", "password": "ViewerPass!23"},
    )
    assert viewer_login.status_code == 200
    viewer_headers = {"Authorization": f"Bearer {viewer_login.json()['access_token']}"}

    # Viewer cannot create API keys or add members
    denied_key = await client.post(
        "/v1/api-keys",
        headers=viewer_headers,
        json={"name": "Viewer Key"},
    )
    assert denied_key.status_code == 403

    denied_member = await client.post(
        "/v1/organisation/members",
        headers=viewer_headers,
        json={
            "email": "other@alpha.test",
            "full_name": "Other",
            "password": "OtherPass!23",
            "role": "viewer",
        },
    )
    assert denied_member.status_code == 403

    # Owner A creates an API key
    key_resp = await client.post(
        "/v1/api-keys",
        headers=headers_a,
        json={"name": "ERP Integration", "scopes": ["invoices:read", "invoices:write"]},
    )
    assert key_resp.status_code == 201
    key_data = key_resp.json()
    assert key_data["secret_key"].startswith("il_live_")

    # Listing keys never returns full secret
    keys_list = await client.get("/v1/api-keys", headers=headers_a)
    assert keys_list.status_code == 200
    assert len(keys_list.json()) == 1
    assert "secret_key" not in keys_list.json()[0]

    # Org B cannot see or revoke Org A's API key
    keys_b = await client.get("/v1/api-keys", headers=headers_b)
    assert keys_b.status_code == 200
    assert keys_b.json() == []

    cross_revoke = await client.delete(f"/v1/api-keys/{key_data['id']}", headers=headers_b)
    assert cross_revoke.status_code == 404

    # Owner A revokes key cleanly
    revoke_ok = await client.delete(f"/v1/api-keys/{key_data['id']}", headers=headers_a)
    assert revoke_ok.status_code == 204



async def test_supabase_provisioning_creates_workspace_without_password_hashing(
    db, test_settings
) -> None:
    identity = {
        "id": "supabase-user-1",
        "email": "New.User@example.com",
        "user_metadata": {"full_name": "New User"},
    }

    first = await provision_supabase_user(
        db, test_settings, identity, "supabase-access-token"
    )

    assert first.user.email == "new.user@example.com"
    assert first.role == "owner"
    assert first.organisation.name == "New User's workspace"

    stored_user = await db.fetchone(
        "SELECT id, email, password_hash FROM users WHERE email = ?",
        ("new.user@example.com",),
    )
    assert stored_user is not None
    assert stored_user["password_hash"] == "supabase_managed"

    # Provisioning is idempotent: repeat calls reuse the existing tenant.
    second = await provision_supabase_user(
        db, test_settings, identity, "supabase-access-token"
    )
    assert second.user.id == first.user.id
    assert second.organisation.id == first.organisation.id

    assert await db.fetchone("SELECT COUNT(*) AS n FROM users") == {"n": 1}
    assert await db.fetchone("SELECT COUNT(*) AS n FROM organisations") == {"n": 1}
    assert await db.fetchone("SELECT COUNT(*) AS n FROM memberships") == {"n": 1}
