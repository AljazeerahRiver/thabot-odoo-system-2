from unittest.mock import Mock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import ODOO_METHODS, ODOO_MODELS, _call_odoo, app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("THABOT_API_TOKEN", "test-token")
    return TestClient(app)


def test_health_does_not_connect_to_odoo(client: TestClient) -> None:
    with patch("app.main.requests.post") as post:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    post.assert_not_called()


def test_request_without_bearer_token_is_rejected_before_odoo(
    client: TestClient,
) -> None:
    with patch("app.main.requests.post") as post:
        response = client.get("/v1/companies")

    assert response.status_code == 401
    post.assert_not_called()


@pytest.mark.parametrize("method", ["create", "write", "unlink", "execute", "arbitrary"])
def test_disallowed_odoo_methods_are_rejected(method: str) -> None:
    with patch("app.main.requests.post") as post:
        with pytest.raises(HTTPException) as error:
            _call_odoo("res.company", method, {})

    assert error.value.status_code == 403
    post.assert_not_called()


def test_model_outside_allowlist_is_rejected() -> None:
    with patch("app.main.requests.post") as post:
        with pytest.raises(HTTPException) as error:
            _call_odoo("res.users", "search_read", {})

    assert error.value.status_code == 403
    post.assert_not_called()


def test_client_cannot_select_model_or_method(client: TestClient) -> None:
    response = client.get(
        "/v1/odoo/res.users/search_read",
        headers={"Authorization": "Bearer test-token"},
    )
    assert response.status_code == 404


def test_read_only_allowlists_are_exact() -> None:
    assert ODOO_MODELS == {"res.company", "hr.employee", "fleet.vehicle"}
    assert ODOO_METHODS == {"search_count", "search_read", "read"}


def test_authorized_read_uses_fixed_model_and_method(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ODOO_URL", "https://odoo.example.invalid")
    monkeypatch.setenv("ODOO_DB", "test-db")
    monkeypatch.setenv("ODOO_API_KEY", "test-odoo-key")
    response_mock = Mock(ok=True)
    response_mock.json.return_value = [{"id": 1, "name": "Example"}]

    with patch("app.main.requests.post", return_value=response_mock) as post:
        response = client.get(
            "/v1/companies",
            headers={"Authorization": "Bearer test-token"},
        )

    assert response.status_code == 200
    assert response.json() == [{"id": 1, "name": "Example"}]
    assert "/json/2/res.company/search_read" in post.call_args.args[0]
    assert post.call_args.kwargs["json"]["domain"] == []
