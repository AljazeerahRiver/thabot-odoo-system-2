# Thabot Odoo integration

This README describes the implementation on **`dev`**, not a verified live service.
The repository contains two components:

| Component | Implemented scope |
|---|---|
| [`thabot_ai_agent_studio/`](thabot_ai_agent_studio/README.md) | Odoo 19 addon for agents, provider configuration, conversations, tool declarations, and token/cost tracking; Gemini, Vertex AI, OpenAI, and custom HTTP provider implementations. |
| [`bridge/`](bridge/) | Separate FastAPI bridge exposing fixed, read-only company, employee, and vehicle routes through Odoo's JSON-2 API. |

There is no bundled Odoo server, database, or end-to-end Thabot client integration.
Provider implementations and deployment configuration do not establish successful
Odoo/GCP connectivity, provider compatibility, or service availability.

## Prerequisites and installation

**Addon:** an existing Odoo 19 installation and PostgreSQL database, Odoo modules
`base`, `mail`, and `web`, and the Python package `requests`. Add this repository to
Odoo's addons path (or copy `thabot_ai_agent_studio` into an existing addons directory),
update the apps list, and install **Asem's Odoo 19.0 AI Agent Studio - Thab-out**.
Use a disposable development database, not production.

Start Odoo using your installation's configuration, including its database connection
and addons path. Configure a provider through **AI Agent Studio → Configuration →
Providers**, then activate an agent and start a conversation. See the
[addon guide](thabot_ai_agent_studio/README.md) for provider configuration and usage.
Provider keys are stored in `ir.config_parameter`, never in source or on the provider
record. When that parameter is empty, the addon reads the environment variable named
by the provider's `api_key_env_var` field; the seeded configurations use
`GEMINI_API_KEY` and `VERTEX_AI_ACCESS_TOKEN`. Live chat sends conversation content
to the configured provider.

**Bridge:** Python 3.12 is the version used by CI and the Docker image. From the
repository root, create an isolated environment and install the bridge dependencies:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r bridge/requirements.txt
cd bridge
uvicorn app.main:app --host 127.0.0.1 --port 8080
```

In another terminal, `curl http://127.0.0.1:8080/health` returns
`{"status":"ok"}` without contacting Odoo. This is process health only, not a
connection or authorization check. The Docker entrypoint uses `PORT` (default `8080`).

For data reads, supply these environment variables through a secure local mechanism:

| Variable | Purpose |
|---|---|
| `THABOT_API_TOKEN` | Shared client bearer token; clients use the `Authorization` header with the `Bearer` scheme. |
| `ODOO_URL` | Approved staging Odoo base URL supporting `/json/2/...`. |
| `ODOO_DB` | Staging database name. |
| `ODOO_API_KEY` | Odoo API key for a dedicated, least-privilege read-only identity. |

The server can start without these values, but data requests return `503` when
required configuration is absent; missing/invalid client authentication returns
`401` when a client token is configured. Upstream request failures return `502`.
Do not create an `ODOO_PASSWORD` variable, log secrets, or put real data in examples.

## Tests

Commands below are taken from the repository's workflow, Docker entrypoint, and addon
guide; they are not evidence of a live integration.

For the bridge, with the virtual environment active, run from the repository root:

```bash
python -m pip install -r bridge/requirements-dev.txt
cd bridge
pytest -q
```

Bridge tests mock Odoo HTTP calls. For the addon, use your configured Odoo 19
installation with a **disposable test database** and this repository on its addons path:

```bash
odoo -d <test_database> -i thabot_ai_agent_studio --test-enable --stop-after-init
```

Addon tests use Odoo `TransactionCase` and mocked provider behavior. They require the
Odoo/PostgreSQL environment even though provider calls are mocked. The bridge workflow
does not run addon tests; neither suite proves deployment or real provider connectivity.

## Security boundaries and limitations

The bridge exposes `GET /v1/companies`, `/v1/employees`, and `/v1/vehicles`, plus
`/count` and `/{record_id}` under each. List pagination defaults to 50 records,
allows limits of 1–100, and requires a nonnegative offset. Models and returned fields
are fixed in [`bridge/app/main.py`](bridge/app/main.py); only `res.company`,
`hr.employee`, and `fleet.vehicle` and the methods `search_count`, `search_read`,
and `read` are allowed. There are no write, payment, or accounting routes, and
interactive API documentation is disabled.

Every data route requires the shared bearer token; only `GET /health` is public at
the application layer. All callers use the same Odoo API identity. The bridge sends
an empty search domain and does not implement per-client company selection or
isolation: accessible records depend on that identity's Odoo permissions and record
rules. Employee contact details and vehicle/driver fields may be sensitive even
when access is read-only.

The addon has its own access groups and record rules; it is not covered by the
bridge's read-only allowlist. Tool declarations are passed to providers, but the
current conversation flow does not execute returned tool calls. There is no wired
addon-to-bridge workflow or demonstrated end-to-end business integration.
Token/cost tracking is not financial settlement.

**Do not connect Thabot to production Odoo until the final approved stage, and then
only with read-only access.** Use staging credentials during development. Review
Odoo access and provider data handling separately before using any real data.

## Branch and deployment behavior

[`bridge-deploy.yml`](.github/workflows/bridge-deploy.yml) triggers only on pushes
to `dev` or `main` that change `bridge/**` or the workflow file itself. It has no
pull-request or manual-dispatch trigger.

| Qualifying push | Configured deployment target | Gate |
|---|---|---|
| `dev` | `dev-odoo-thabot-bridge`, staging Odoo | Bridge pytest must pass; no production environment gate. |
| `main` | `prod-odoo-thabot-bridge`, production Odoo | Bridge pytest must pass, then GitHub Environment `production`. |

A `docs/` or other review-branch push cannot trigger this deployment workflow.
README-only changes are also excluded by its path filter. **Merging a PR into `dev`
may deploy the development Cloud Run service if the resulting push includes bridge
or workflow changes.** Review the complete PR diff before merging. Do not push
directly to `dev`/`main`, merge, or deploy as part of a documentation update.

The workflow requests Cloud Run region `me-central2`, internal ingress, and
authenticated access. Network reachability, Cloud Run IAM/client authentication,
and the bridge bearer check are separate requirements; this is not a public API.
The `production` environment name alone does not guarantee approval: required
reviewers must be configured in GitHub.

Deployment requires preconfigured Workload Identity Federation, deployment/runtime
service accounts, IAM permissions, and Secret Manager secrets. The repository does
not provision or verify these resources. Configuration names are:

- Shared GitHub variables: `GCP_PROJECT_ID`, `GCP_WORKLOAD_IDENTITY_PROVIDER`,
  `GCP_DEPLOY_SERVICE_ACCOUNT`.
- Development variables: `DEV_CLOUD_RUN_RUNTIME_SERVICE_ACCOUNT`, `ODOO_STAGING_URL`,
  `ODOO_STAGING_DB`, `ODOO_STAGING_API_KEY_SECRET`, `DEV_THABOT_API_TOKEN_SECRET`.
- Production variables: `PROD_CLOUD_RUN_RUNTIME_SERVICE_ACCOUNT`,
  `ODOO_PRODUCTION_URL`, `ODOO_PRODUCTION_DB`, `ODOO_PRODUCTION_API_KEY_SECRET`,
  `PROD_THABOT_API_TOKEN_SECRET`.

Secret-name variables reference Secret Manager entries, not credential values.
The workflow binds their `latest` versions to `ODOO_API_KEY` and `THABOT_API_TOKEN`
at runtime. Use separate development/production runtime identities with access
only to their own secrets. No service-account JSON key is required.
