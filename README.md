# BlazeNXT Control Plane v1

**Public release line: v1.x.** Internal development iterations do not change the public major version.

Official branding assets are served from `static/blazenxt-logo.png` and `static/blazenxt-favicon.png` and are used across the public site, authenticated panel, project canvas, favicon, and social previews.

Railway-native Telegram bot hosting with a FastAPI website/control plane. Uploaded code is never executed by the website: each workload is provisioned as a separate Railway service using a non-root runner image.

## Included

- Telegram Login, Google OAuth, GitHub OAuth and one-time email magic links
- Signed HttpOnly sessions, OAuth state/session binding, CSRF protection and rate limiting
- Multiple verified identities linked to one synchronized BlazeNXT account
- Python, Node and ZIP uploads with size, zip-slip, zip-bomb and SHA-256 checks
- Workload-scoped expiring artifact tokens, removed from the child process environment
- Original Pterodactyl + Railway + KataBump-inspired responsive control plane (no proprietary UI/code copied)
- Role-aware infrastructure canvas for workloads, containers, databases and control-plane relationships
- Unified observability workspace with live health refresh, allocation totals, activity and webhook delivery status
- Production staged-change review/apply/discard workflow for startup, resources and secrets
- Railway-generated and custom domain provisioning with returned DNS requirements
- Persistent dark/light themes, collapsible navigation, Ctrl+K command palette and mobile workspace UI
- Form progress states, drag/drop feedback, keyboard navigation and accessibility-focused controls
- One synchronized identity and workload state across Telegram bot and website
- Telegram bot auto-start verification (`getMe`), webhook registration, command registration and health reporting
- Telegram file uploads, server lists, account/platform status, logs, backups and start/stop/restart controls
- Bidirectional action notifications between web controls and Telegram
- Separate Railway service per workload with auto-refreshing runtime logs
- Artifact file explorer, UTF-8 editor, file creation/deletion, secure downloads and package replacement
- Automatic pre-change backups, downloadable snapshots and one-click backup restore
- Server rename, current-artifact reinstall and automatic isolated-service redeployment
- Encrypted environment variables synchronized to Railway and masked in the panel
- Manual artifact backups, lifecycle schedules and permission-based server collaborators
- Pterodactyl-style application templates/eggs and editable startup configuration
- Scoped API keys with authenticated `/api/v1` server and power endpoints
- HMAC-SHA256 outbound webhooks, SSRF destination checks and delivery history
- TOTP two-factor authentication with encrypted one-use recovery codes
- Startup/isolation details, notifications and unified web/bot/API/webhook/scheduler activity timeline
- User/premium/admin/owner roles, quotas and upgraded admin controls
- Railway deployment history with validated rollback controls
- KataBump-style plan catalog, manual upgrade ordering and synchronized support tickets
- Railway Pro defaults: unlimited BlazeNXT workloads (`GLOBAL_WORKLOAD_LIMIT=0`), configurable CPU/RAM, replicas and restart policies
- Managed per-workload PostgreSQL services with persistent Railway volumes and private `DATABASE_URL` injection
- Suspend/unsuspend operations and retained-volume destructive-action safeguards
- Public aggregate status page, health snapshots, incident history and maintenance announcements
- Notification center with web/Telegram broadcasts and dashboard notices
- Customer portal with onboarding, referral credits, plan history, API usage and recent sign-ins
- Admin emergency deployment switch, incident controls and CSV audit export
- Admin support queue, user notifications, audit trail, health/readiness and Prometheus metrics
- Docker Compose for local use, Railway config and GHCR runner workflow
- Manual premium plans initially; billing can be added later

## Security boundary

Railway does not support safe Docker-in-Docker in a normal app service. Isolation therefore uses one Railway service per workload. This is safer than running uploads in the control plane, but nobody can honestly promise “complete security.” Railway enforces the final compute/network boundary, and per-workload egress firewall policy is not available here. Do not accept anonymous or malicious uploads.

## Railway deployment

1. Add Railway Postgres and deploy this repository as the control-plane service.
2. Copy `.env.example` into Railway variables. Generate `APP_SECRET` with `openssl rand -hex 32`.
3. Set `WEB_BASE_URL=https://hosting.blazenxt.in`, Telegram values and `OWNER_IDS`.
4. Optionally configure Google callback `https://hosting.blazenxt.in/auth/google/callback`, GitHub callback `https://hosting.blazenxt.in/auth/github/callback`, and SMTP variables for email magic links.
5. Set a Railway account/workspace API token plus `RAILWAY_PROJECT_ID` and `RAILWAY_ENVIRONMENT_ID`. This is a high-impact secret.
6. Let GitHub Actions publish `ghcr.io/blazenxt/vps-blazenxt-runner:latest`; make the package public or configure registry credentials in Railway.
7. Add `hosting.blazenxt.in` as a Railway custom domain and follow Railway's DNS instructions.
8. In BotFather use `/setdomain` with `hosting.blazenxt.in`.
9. Deploy or restart the service. On startup, the control plane securely registers its Telegram webhook from `WEB_BASE_URL` and `TELEGRAM_WEBHOOK_SECRET`; no manual curl command is needed.
10. Confirm `/health/ready` returns ready and check the deployment log for `Telegram bot ... started with webhook sync` before enabling users.

Without Railway variables, the dashboard works but deployments safely enter `failed` with a provider-not-configured error.

## API v1

Create a scoped key under **Login & Security**, then use:

```bash
curl -H "Authorization: Bearer $BLAZENXT_API_KEY" https://hosting.blazenxt.in/api/v1/servers
curl -X POST -H "Authorization: Bearer $BLAZENXT_API_KEY" https://hosting.blazenxt.in/api/v1/servers/1/power/restart
```

Webhook deliveries include `X-BlazeNXT-Event` and `X-BlazeNXT-Signature-256: sha256=<hmac>` headers. On Railway Pro, database provisioning is enabled by default and each managed PostgreSQL database creates a separately billed service plus persistent volume. BlazeNXT intentionally retains database volumes when deleting their service.

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

The original monolithic bot is retained under `legacy/` for reference and is not run by BlazeNXT v1.
