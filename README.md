# Thabot Odoo integration

This repository contains the `thabot_ai_agent_studio` Odoo 19 addon and a read-only
FastAPI bridge in [`bridge/`](bridge/). The bridge is deployed to Google Cloud Run in
`me-central2` in the `thabot` project.

## Bridge security boundary

The bridge exposes only read operations for `res.company`, `hr.employee`, and
`fleet.vehicle`. Its only Odoo methods are `search_count`, `search_read`, and `read`;
models and methods are selected by fixed server-side routes, not by request parameters.
Every endpoint except `GET /health` requires a valid `Authorization: Bearer` token.
The health endpoint performs no Odoo request. No write operation is exposed.

**Thabot must not be connected to the production Odoo environment until the final
stage, and then only with read-only access.** Use staging credentials for development.
Production access must be separately approved and granted only after the bridge has
been validated.

## Environments

| Git branch | Cloud Run service | Odoo environment | Approval |
|---|---|---|---|
| `dev` | `dev-odoo-thabot-bridge` | Staging | No production approval |
| `main` | `prod-odoo-thabot-bridge` | Production | GitHub Environment `production` |

Both services are deployed with internal ingress and Cloud Run authentication enabled.
The client bearer token is an additional application-level check.

## Configuration names

Configure values in GitHub Actions variables and Google Secret Manager; do not put
credential values in this repository.

GitHub Actions variables: `GCP_PROJECT_ID`, `GCP_WORKLOAD_IDENTITY_PROVIDER`,
`GCP_DEPLOY_SERVICE_ACCOUNT`, `DEV_CLOUD_RUN_RUNTIME_SERVICE_ACCOUNT`,
`PROD_CLOUD_RUN_RUNTIME_SERVICE_ACCOUNT`,
`ODOO_STAGING_URL`, `ODOO_STAGING_DB`,
`ODOO_STAGING_API_KEY_SECRET`, `DEV_THABOT_API_TOKEN_SECRET`,
`ODOO_PRODUCTION_URL`, `ODOO_PRODUCTION_DB`, `ODOO_PRODUCTION_API_KEY_SECRET`,
`PROD_THABOT_API_TOKEN_SECRET`.

Google Secret Manager secrets: a staging Odoo API key, a development Thabot bearer
token, a production Odoo API key, and a production Thabot bearer token. The workflow
binds these to `ODOO_API_KEY` and `THABOT_API_TOKEN` at Cloud Run runtime. The bridge
also reads `ODOO_URL` and `ODOO_DB` as non-secret runtime configuration. Use separate
development and production runtime service identities, each with access only to its
own environment's secrets. Do not create an `ODOO_PASSWORD` variable or print/log
secret values.

## Deployment

Pushes to `dev` run the bridge pytest suite and deploy the development service only if
tests pass. Pushes to `main` run the same suite; the production job waits for approval
through the GitHub Environment named `production`. Configure required reviewers on that
environment in GitHub. Workload Identity Federation is used for GitHub-to-Google Cloud
authentication; configure the provider and deploy service account variables and grant
the account the minimum permissions needed to deploy Cloud Run and build the image.
No service-account JSON key is required.

For local, offline tests:

```bash
cd bridge
python -m pip install -r requirements-dev.txt
pytest -q
```

The tests mock Odoo HTTP calls and never contact a real Odoo instance.

## Odoo addon

The existing Odoo addon documentation and tests are in
[`thabot_ai_agent_studio/README.md`](thabot_ai_agent_studio/README.md).
