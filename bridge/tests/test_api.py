import logging
from pathlib import Path
from typing import Optional
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


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/v1/companies"),
        ("PATCH", "/v1/employees/7"),
        ("DELETE", "/v1/vehicles/7"),
        ("GET", "/v1/odoo/res.users/search_read"),
        ("POST", "/v1/odoo/res.company/create"),
    ],
)
def test_authorized_clients_cannot_invoke_unlisted_operations(
    client: TestClient, method: str, path: str
) -> None:
    with patch("app.main.requests.post") as post:
        response = client.request(method, path, headers={"Authorization": "Bearer test-token"})
    assert response.status_code in (404, 405)
    post.assert_not_called()


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


@pytest.mark.parametrize(
    ("path", "model", "method"),
    [
        ("/v1/companies", "res.company", "search_read"),
        ("/v1/companies/count", "res.company", "search_count"),
        ("/v1/companies/7", "res.company", "read"),
        ("/v1/employees", "hr.employee", "search_read"),
        ("/v1/employees/count", "hr.employee", "search_count"),
        ("/v1/employees/7", "hr.employee", "read"),
        ("/v1/vehicles", "fleet.vehicle", "search_read"),
        ("/v1/vehicles/count", "fleet.vehicle", "search_count"),
        ("/v1/vehicles/7", "fleet.vehicle", "read"),
    ],
)
def test_routes_use_only_allowlisted_odoo_operations(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    model: str,
    method: str,
) -> None:
    monkeypatch.setenv("ODOO_URL", "https://odoo.example.invalid")
    monkeypatch.setenv("ODOO_DB", "placeholder-db")
    monkeypatch.setenv("ODOO_API_KEY", "placeholder-credential")
    result = Mock(ok=True)
    result.json.return_value = [] if method != "search_count" else 0

    with patch("app.main.requests.post", return_value=result) as post:
        response = client.get(path, headers={"Authorization": "Bearer test-token"})

    assert response.status_code == 200
    post.assert_called_once()
    assert post.call_args.args[0].endswith(f"/json/2/{model}/{method}")
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer placeholder-credential"
    assert post.call_args.kwargs["headers"]["X-Odoo-Database"] == "placeholder-db"
    assert post.call_args.kwargs["timeout"] == 10
    if method == "read":
        assert post.call_args.kwargs["json"]["ids"] == [7]


@pytest.mark.parametrize(
    ("method", "path", "authorization", "status"),
    [
        ("GET", "/v1/companies", None, 401),
        ("GET", "/v1/employees/count", "Basic test-token", 401),
        ("GET", "/v1/vehicles/7", "Bearer invalid", 401),
        ("GET", "/not-a-route", None, 401),
        ("POST", "/health", None, 401),
        ("POST", "/v1/companies", None, 401),
        ("DELETE", "/v1/employees/7", None, 401),
    ],
)
def test_all_non_health_requests_reject_invalid_auth_before_odoo(
    client: TestClient, method: str, path: str, authorization: Optional[str], status: int
) -> None:
    headers = {"Authorization": authorization} if authorization else {}
    with patch("app.main.requests.post") as post:
        response = client.request(method, path, headers=headers)
    assert response.status_code == status
    post.assert_not_called()


def test_missing_client_secret_fails_closed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("THABOT_API_TOKEN", raising=False)
    with patch("app.main.requests.post") as post:
        response = client.get("/v1/companies", headers={"Authorization": "Bearer test-token"})
    assert response.status_code == 503
    post.assert_not_called()


def test_missing_odoo_secret_fails_without_contacting_odoo(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ODOO_URL", "https://odoo.example.invalid")
    monkeypatch.setenv("ODOO_DB", "placeholder-db")
    monkeypatch.delenv("ODOO_API_KEY", raising=False)
    with patch("app.main.requests.post") as post:
        response = client.get("/v1/companies", headers={"Authorization": "Bearer test-token"})
    assert response.status_code == 503
    post.assert_not_called()


def test_no_request_or_credentials_are_logged(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("ODOO_URL", "https://odoo.example.invalid")
    monkeypatch.setenv("ODOO_DB", "placeholder-db")
    monkeypatch.setenv("ODOO_API_KEY", "placeholder-credential")
    result = Mock(ok=True)
    result.json.return_value = []
    caplog.set_level(logging.DEBUG)
    with patch("app.main.requests.post", return_value=result):
        response = client.post(
            "/v1/companies",
            headers={"Authorization": "Bearer test-token"},
            json={"private-marker": "private-body-marker"},
        )
        read_response = client.get(
            "/v1/companies", headers={"Authorization": "Bearer test-token"}
        )
    assert response.status_code == 405
    assert read_response.status_code == 200
    for value in ("test-token", "placeholder-credential", "private-body-marker", "private-marker"):
        assert value not in caplog.text


def test_workflow_binds_secrets_and_gates_deploys() -> None:
    workflow = (Path(__file__).resolve().parents[2] / ".github/workflows/bridge-deploy.yml").read_text()
    image = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text()
    assert "ODOO_API_KEY=${{ vars.ODOO_STAGING_API_KEY_SECRET }}:latest" in workflow
    assert "ODOO_API_KEY=${{ vars.ODOO_PRODUCTION_API_KEY_SECRET }}:latest" in workflow
    assert "THABOT_API_TOKEN=${{ vars.DEV_THABOT_API_TOKEN_SECRET }}:latest" in workflow
    assert "THABOT_API_TOKEN=${{ vars.PROD_THABOT_API_TOKEN_SECRET }}:latest" in workflow
    assert "ODOO_URL=${{ vars.ODOO_STAGING_URL }},ODOO_DB=${{ vars.ODOO_STAGING_DB }}" in workflow
    assert "environment: production" in workflow
    assert workflow.count("needs: test") == 2
    assert workflow.count("github.event_name == 'push'") == 2
    assert workflow.count("--ingress internal") == 2
    assert workflow.count("--no-allow-unauthenticated") == 2
    assert "--no-access-log" in image
    assert "${PORT:-8080}" in image
