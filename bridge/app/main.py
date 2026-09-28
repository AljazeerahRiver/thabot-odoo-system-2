import hmac
import os
from typing import Any, Optional
from urllib.parse import quote

import requests
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


ODOO_MODELS = frozenset({"res.company", "hr.employee", "fleet.vehicle"})
ODOO_METHODS = frozenset({"search_count", "search_read", "read"})

MODEL_FIELDS = {
    "res.company": ["id", "name"],
    "hr.employee": [
        "id",
        "name",
        "work_email",
        "work_phone",
        "job_title",
        "company_id",
        "active",
    ],
    "fleet.vehicle": [
        "id",
        "name",
        "license_plate",
        "model_id",
        "driver_id",
        "company_id",
        "active",
    ],
}

bearer_scheme = HTTPBearer(auto_error=False)
app = FastAPI(
    title="Thabot Odoo Bridge",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.middleware("http")
async def authenticate_request(request: Request, call_next: Any) -> Any:
    if request.method == "GET" and request.url.path == "/health":
        return await call_next(request)

    scheme, separator, token = request.headers.get("authorization", "").partition(" ")
    credentials = (
        HTTPAuthorizationCredentials(scheme=scheme, credentials=token)
        if separator and scheme.lower() == "bearer"
        else None
    )
    try:
        require_client_token(credentials)
    except HTTPException as error:
        return JSONResponse(
            status_code=error.status_code,
            content={"detail": error.detail},
            headers={"WWW-Authenticate": "Bearer"} if error.status_code == 401 else None,
        )
    return await call_next(request)


def require_client_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> None:
    expected = os.getenv("THABOT_API_TOKEN")
    if not expected:
        raise HTTPException(status_code=503, detail="Service authentication is not configured")
    if credentials is None or not hmac.compare_digest(credentials.credentials, expected):
        raise HTTPException(status_code=401, detail="Bearer token required")


def _call_odoo(model: str, method: str, params: dict[str, Any]) -> Any:
    if model not in ODOO_MODELS or method not in ODOO_METHODS:
        raise HTTPException(status_code=403, detail="Odoo operation is not allowed")

    required = ("ODOO_URL", "ODOO_DB", "ODOO_API_KEY")
    config = {name: os.getenv(name) for name in required}
    if any(not value for value in config.values()):
        raise HTTPException(status_code=503, detail="Odoo connection is not configured")

    url = f"{config['ODOO_URL'].rstrip('/')}/json/2/{quote(model)}/{quote(method)}"
    headers = {
        "Authorization": f"Bearer {config['ODOO_API_KEY']}",
        "X-Odoo-Database": config["ODOO_DB"],
        "Content-Type": "application/json",
    }
    try:
        response = requests.post(url, headers=headers, json=params, timeout=10)
    except requests.RequestException:
        raise HTTPException(status_code=502, detail="Odoo request failed") from None

    if not response.ok:
        raise HTTPException(status_code=502, detail="Odoo request failed")
    try:
        return response.json()
    except ValueError:
        raise HTTPException(status_code=502, detail="Odoo returned an invalid response") from None


def _search_read(model: str, limit: int, offset: int) -> Any:
    return _call_odoo(
        model,
        "search_read",
        {"domain": [], "fields": MODEL_FIELDS[model], "limit": limit, "offset": offset},
    )


def _search_count(model: str) -> Any:
    return _call_odoo(model, "search_count", {"domain": []})


def _read(model: str, record_id: int) -> Any:
    return _call_odoo(model, "read", {"ids": [record_id], "fields": MODEL_FIELDS[model]})


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/companies", dependencies=[Depends(require_client_token)])
def companies(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> Any:
    return _search_read("res.company", limit, offset)


@app.get("/v1/companies/count", dependencies=[Depends(require_client_token)])
def companies_count() -> Any:
    return _search_count("res.company")


@app.get("/v1/companies/{record_id}", dependencies=[Depends(require_client_token)])
def company(record_id: int) -> Any:
    return _read("res.company", record_id)


@app.get("/v1/employees", dependencies=[Depends(require_client_token)])
def employees(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> Any:
    return _search_read("hr.employee", limit, offset)


@app.get("/v1/employees/count", dependencies=[Depends(require_client_token)])
def employees_count() -> Any:
    return _search_count("hr.employee")


@app.get("/v1/employees/{record_id}", dependencies=[Depends(require_client_token)])
def employee(record_id: int) -> Any:
    return _read("hr.employee", record_id)


@app.get("/v1/vehicles", dependencies=[Depends(require_client_token)])
def vehicles(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> Any:
    return _search_read("fleet.vehicle", limit, offset)


@app.get("/v1/vehicles/count", dependencies=[Depends(require_client_token)])
def vehicles_count() -> Any:
    return _search_count("fleet.vehicle")


@app.get("/v1/vehicles/{record_id}", dependencies=[Depends(require_client_token)])
def vehicle(record_id: int) -> Any:
    return _read("fleet.vehicle", record_id)
