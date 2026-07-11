# BlazeNXT Hosting Platform v2

Railway-native Telegram bot hosting with a FastAPI website/control plane. Uploaded code is never executed by the website: each workload is provisioned as a separate Railway service using a non-root runner image.

## Included

- Telegram Login verification, signed HttpOnly sessions, CSRF and rate limiting
- Python, Node and ZIP uploads with size, zip-slip, zip-bomb and SHA-256 checks
- Workload-scoped expiring artifact tokens, removed from the child process environment
- Separate Railway service per workload; start, stop, restart, delete and logs
- User/premium/admin/owner roles, quotas and admin controls
- Audit trail, health/readiness endpoints and Prometheus metrics
- Docker Compose for local use, Railway config and GHCR runner workflow
- Manual premium plans initially; billing can be added later

## Security boundary

Railway does not support safe Docker-in-Docker in a normal app service. Isolation therefore uses one Railway service per workload. This is safer than running uploads in the control plane, but nobody can honestly promise “complete security.” Railway enforces the final compute/network boundary, and per-workload egress firewall policy is not available here. Do not accept anonymous or malicious uploads.

## Railway deployment

1. Add Railway Postgres and deploy this repository as the control-plane service.
2. Copy `.env.example` into Railway variables. Generate `APP_SECRET` with `openssl rand -hex 32`.
3. Set `WEB_BASE_URL=https://hosting.blazenxt.in`, Telegram values and `OWNER_IDS`.
4. Set a Railway account/workspace API token plus `RAILWAY_PROJECT_ID` and `RAILWAY_ENVIRONMENT_ID`. This is a high-impact secret.
5. Let GitHub Actions publish `ghcr.io/blazenxt/vps-blazenxt-runner:latest`; make the package public or configure registry credentials in Railway.
6. Add `hosting.blazenxt.in` as a Railway custom domain and follow Railway's DNS instructions.
7. In BotFather use `/setdomain` with `hosting.blazenxt.in`.
8. Deploy or restart the service. On startup, the control plane securely registers its Telegram webhook from `WEB_BASE_URL` and `TELEGRAM_WEBHOOK_SECRET`; no manual curl command is needed.
9. Confirm `/health/ready` returns ready and check the deployment log for `Telegram webhook configured` before enabling users.

Without Railway variables, the dashboard works but deployments safely enter `failed` with a provider-not-configured error.

## Local

```bash
cp .env.example .env
# Set APP_SECRET and use postgresql+psycopg://blaze:change-me@postgres:5432/blaze
docker compose up --build
```

## Production checklist

- Revoke all GitHub tokens shared in chat.
- Keep Railway API credentials only in control-plane secrets—never runner variables.
- Protect `/metrics` using Railway private networking or an authenticated proxy.
- Configure Railway spend/resource limits; one service per bot can be expensive.
- Enable Postgres backups and test restoration.
- Pin the runner image by digest and add a malware-scanning service before public registration.
- Review audit logs and automatically expire dormant workloads.

The original monolithic bot is retained under `legacy/` for reference and is not run by v2.
