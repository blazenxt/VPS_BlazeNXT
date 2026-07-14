# BlazeNXT v1.0.0 — Complete Step-by-Step Setup Guide

BlazeNXT is a Railway-native hosting control plane for Python and Node.js bots. The web panel and Telegram bot share the same PostgreSQL database, while every uploaded workload runs as a separate Railway service using the BlazeNXT runner image.

## Architecture

```text
Users
  ├─ Web panel (FastAPI)
  └─ Telegram bot (secure webhook)
          │
          ▼
BlazeNXT control plane ─── PostgreSQL
          │
          ├─ Railway workload service #1
          ├─ Railway workload service #2
          ├─ Managed PostgreSQL services
          └─ Optional S3/R2 offsite backups
```

Uploaded user code never executes inside the control-plane container.

---

## 1. Requirements

Before starting, prepare:

- Railway account and a paid plan (Pro recommended)
- GitHub account
- This GitHub repository
- Telegram bot from `@BotFather`
- Your numeric Telegram user ID
- Railway PostgreSQL service
- Railway account/workspace API token
- Optional custom domain such as `panel.your-domain.example`

Optional integrations:

- Google OAuth
- GitHub OAuth
- SMTP email
- S3, Cloudflare R2, Backblaze B2 or MinIO

### Choose the public hostname used throughout this guide

Use exactly one option:

```text
Option A — Railway domain: YOUR-APP.up.railway.app
Option B — Custom domain:  YOUR-DOMAIN
```

In the remaining examples, `YOUR-HOST` means the hostname selected above.

Examples:

```text
YOUR-HOST = your-app-production.up.railway.app
YOUR-HOST = panel.example.com
```

Do not include `http://`, `https://` or a trailing slash when a field asks only for the hostname. Use `https://YOUR-HOST` when a complete URL is required.

---

## 2. Build the isolated runner image

BlazeNXT workloads use:

```text
ghcr.io/blazenxt/vps-blazenxt-runner:latest
```

In GitHub:

1. Open **Actions**.
2. Select **Build isolated runner**.
3. Click **Run workflow**.
4. Wait for the workflow to complete successfully.
5. Open the repository/package settings.
6. Make `vps-blazenxt-runner` public, or configure private registry access in Railway Pro.

Do not continue until the runner image exists.

---

## 3. Create the Railway project

1. Open Railway.
2. Create a new project.
3. Select **Deploy from GitHub repo**.
4. Choose `blazenxt/VPS_BlazeNXT`.
5. Add a PostgreSQL service to the same project.
6. Keep the application and database in the same Railway environment and region where possible.

Railway automatically detects the root `Dockerfile` and `railway.toml`.

---

## 4. Generate application secrets

Run locally:

```bash
openssl rand -hex 32
openssl rand -hex 32
```

Use the first value for `APP_SECRET` and the second for `TELEGRAM_WEBHOOK_SECRET`.

Never reuse your Telegram token, Google secret, GitHub secret or Railway token as `APP_SECRET`.

---

## 5. Required Railway variables

Open the BlazeNXT service → **Variables** and add:

```env
APP_ENV=production
MIGRATIONS_ENABLED=true
JSON_LOGS=true
APP_SECRET=REPLACE_WITH_64_CHARACTER_HEX_SECRET
WEB_BASE_URL=https://YOUR-HOST

DATABASE_URL=${{Postgres.DATABASE_URL}}

BOT_TOKEN=REPLACE_WITH_BOTFATHER_TOKEN
BOT_USERNAME=VPS_BlazeNXTbot
TELEGRAM_WEBHOOK_SECRET=REPLACE_WITH_RANDOM_SECRET
OWNER_IDS=REPLACE_WITH_YOUR_NUMERIC_TELEGRAM_ID

RAILWAY_API_TOKEN=REPLACE_WITH_RAILWAY_ACCOUNT_OR_WORKSPACE_TOKEN
RAILWAY_RUNNER_IMAGE=ghcr.io/blazenxt/vps-blazenxt-runner:latest
RAILWAY_API_URL=https://backboard.railway.com/graphql/v2

GLOBAL_WORKLOAD_LIMIT=0
MAX_UPLOAD_MB=10
SESSION_TTL_SECONDS=86400
RUNNER_TOKEN_TTL_SECONDS=2592000

ENABLE_DATABASE_PROVISIONING=true
DEFAULT_CPU_VCPUS=0.5
DEFAULT_MEMORY_MB=512
MAX_DATABASES_PER_WORKLOAD=1
```

Railway normally injects these automatically:

```env
RAILWAY_PROJECT_ID
RAILWAY_ENVIRONMENT_ID
```

If `/health/ready` reports `railway_configured: false`, add both IDs manually from the Railway project/environment settings.

### Important variable formatting

Do not add extra quotes or equals signs.

Correct:

```env
GOOGLE_CLIENT_ID=123456.apps.googleusercontent.com
```

Incorrect:

```env
GOOGLE_CLIENT_ID==123456.apps.googleusercontent.com
```

---

## 6. Railway API token

Create a Railway account or workspace token with permission to manage the selected project.

BlazeNXT uses it to:

- Create workload services
- Set environment variables
- Redeploy services
- Start, stop and restart deployments
- Apply CPU/RAM/replica limits
- Create managed databases and volumes
- Add Railway/custom domains
- Read deployment logs

Store the token only as a Railway secret. Never commit it or send it in chat.

---

## 7. Configure the Telegram bot

### Create the bot

In `@BotFather`:

```text
/newbot
```

Copy the complete bot token into `BOT_TOKEN`.

Set the domain:

```text
/setdomain
```

Enter the same hostname selected earlier:

```text
YOUR-HOST
```

Do not include `https://` in BotFather.

### Webhook

No manual `curl` command is required.

On startup, BlazeNXT automatically:

- Calls Telegram `getMe`
- Registers the webhook
- Adds a secret webhook header
- Registers bot commands
- Monitors webhook health
- Repairs webhook URL mismatches

Bot health:

```text
https://YOUR-HOST/health/bot
```

Admin diagnostics:

```text
https://YOUR-HOST/admin/bot
```

---

## 8. Configure the public domain

### Option A — Railway-generated domain

1. Open the BlazeNXT service.
2. Open **Settings → Networking**.
3. Click **Generate Domain**.
4. Copy the hostname, for example `your-app-production.up.railway.app`.
5. Set:

```env
WEB_BASE_URL=https://YOUR-APP.up.railway.app
```

### Option B — Your custom domain

1. Open the BlazeNXT service.
2. Open **Settings → Networking**.
3. Add `YOUR-DOMAIN`, for example `panel.example.com`.
4. Add Railway’s required DNS record at your DNS provider.
5. Wait for SSL certificate issuance.
6. Set:

```env
WEB_BASE_URL=https://YOUR-DOMAIN
```

Use only one public URL and do not include a trailing slash.

---

## 9. Optional Google login

Create a Google OAuth client with application type:

```text
Web application
```

Authorized origin:

```text
https://YOUR-HOST
```

Authorized redirect URI:

```text
https://YOUR-HOST/auth/google/callback
```

Railway variables:

```env
GOOGLE_CLIENT_ID=YOUR_FULL_CLIENT_ID.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=YOUR_CLIENT_SECRET
```

Add your Google account under **Google Auth Platform → Audience → Test users** while the app is in Testing mode.

---

## 10. Optional GitHub login

Create a GitHub OAuth App.

Homepage:

```text
https://YOUR-HOST
```

Callback:

```text
https://YOUR-HOST/auth/github/callback
```

Railway variables:

```env
GITHUB_CLIENT_ID=YOUR_GITHUB_CLIENT_ID
GITHUB_CLIENT_SECRET=YOUR_GITHUB_CLIENT_SECRET
```

---

## 11. Optional email and magic links

Configure SMTP:

```env
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USERNAME=YOUR_SMTP_USERNAME
SMTP_PASSWORD=YOUR_SMTP_PASSWORD
SMTP_FROM=BlazeNXT <notifications@example.com>
SMTP_STARTTLS=true
MAGIC_LINK_TTL_SECONDS=900
```

SMTP is used for:

- Email magic-link login
- Deployment notifications
- Security notifications
- Support updates
- Billing/plan events
- Incident announcements

### Browser Web Push (VAPID)

Generate a VAPID key pair locally:

```bash
npx web-push generate-vapid-keys
```

Add the generated values to Railway:

```env
VAPID_PUBLIC_KEY=YOUR_URL_SAFE_PUBLIC_KEY
VAPID_PRIVATE_KEY=YOUR_PRIVATE_KEY
VAPID_SUBJECT=mailto:admin@YOUR-DOMAIN
```

Users can enable Push per device under `/account/notifications`. Expired browser subscriptions are automatically disabled after push providers return `404` or `410`.

User preferences:

```text
/account/notifications
```

Admin delivery queue:

```text
/admin/deliveries
```

---

## 12. Optional S3/R2 offsite backups

Cloudflare R2 example:

```env
S3_ENDPOINT_URL=https://ACCOUNT_ID.r2.cloudflarestorage.com
S3_REGION=auto
S3_BUCKET=blazenxt-backups
S3_ACCESS_KEY_ID=YOUR_ACCESS_KEY
S3_SECRET_ACCESS_KEY=YOUR_SECRET_KEY
S3_PREFIX=blazenxt
S3_FORCE_PATH_STYLE=true
OFFSITE_BACKUP_MAX_MB=50
```

BlazeNXT verifies object size and SHA-256 metadata before download or restore.

Storage health:

```text
/health/storage
```

---

## 13. Optional iframe configuration

Default behavior blocks third-party embedding.

```env
FRAME_ANCESTORS='none'
FRAME_SOURCES=https://oauth.telegram.org
EMBED_TOOLS_JSON=[]
```

Trusted example:

```env
FRAME_ANCESTORS=https://portal.example.com
FRAME_SOURCES=https://oauth.telegram.org,https://grafana.example.com
EMBED_TOOLS_JSON=[{"name":"Grafana","url":"https://grafana.example.com/"}]
```

Never use `*` in production.

---

## 14. Deploy and verify

Redeploy the BlazeNXT service after adding variables.

Check:

```text
https://YOUR-HOST/health/live
https://YOUR-HOST/health/ready
https://YOUR-HOST/health/bot
https://YOUR-HOST/health/storage
```

Expected readiness response:

```json
{
  "status": "ready",
  "railway_configured": true,
  "telegram_online": true
}
```

Open:

```text
https://YOUR-HOST
```

Sign in with Telegram, Google, GitHub or email magic link.

The Telegram ID listed in `OWNER_IDS` becomes the owner.

### First-login onboarding

A new account without workloads is redirected to:

```text
/onboarding
```

The guided checklist verifies platform readiness, multiple login identities, TOTP 2FA, Telegram linking, notification preferences and the first workload. Existing users with workloads go directly to the dashboard. Users may skip the wizard and reopen it later from **Personal → Setup guide**.

### Admin branding

Owners can customize the panel from:

```text
/admin/branding
```

Available settings include panel name, navigation tagline, landing headline/subtitle, footer description, primary/accent colors and a custom PNG/JPEG/WebP logo. Uploaded logos are normalized to PNG and limited to 512×512. Use **Reset to defaults** to restore BlazeNXT branding.

---

## 15. Upload a workload from the website

Recommended Python ZIP:

```text
bot.zip
├── main.py
└── requirements.txt
```

Recommended Node.js ZIP:

```text
bot.zip
├── index.js
├── package.json
└── package-lock.json
```

Do not include secrets in source files. Configure secrets from the workload’s **Variables** page.

---

## 16. Upload and run from Telegram

Send the bot:

```text
/deploy
```

Then upload:

```text
.py
.js
.zip
```

BlazeNXT will:

1. Validate the upload
2. Detect Python or Node.js
3. Detect the entrypoint
4. Show a deployment preview
5. Wait for **Deploy & Run**
6. Create/reconcile the Railway service
7. Install dependencies
8. Start the workload
9. Return power/log/backup controls

Optional caption:

```text
name=MyBot runtime=python entry=main.py
```

Never send secrets through Telegram captions.

---

## 17. Install as a mobile/desktop app

BlazeNXT is an installable PWA.

Open **Account → Mobile App** and choose **Install application**.

On iPhone/iPad:

```text
Safari → Share → Add to Home Screen
```

Private pages and API responses are never cached offline.

---

## 18. Recover failed provisioning

If a workload shows `failed`:

1. Open the workload.
2. Read the phase-specific failure message.
3. Click **Retry & reconcile**.

BlazeNXT searches Railway for an orphan service with the expected name and reuses it instead of creating duplicates.

Common phases:

```text
service discovery
service creation
environment synchronization
deployment trigger
```

---

## Database migrations and production diagnostics

BlazeNXT v1.0.0 uses Alembic. The schema baseline is:

```text
0001_blazenxt_v1
```

The current v1.0.0 schema revision is:

```text
0002_web_push
```

On the first deployment of an existing pre-Alembic installation, startup creates missing current tables and stamps the current migration head without dropping data. New databases run the baseline and subsequent migrations normally. PostgreSQL replicas coordinate startup with an advisory migration lock. Destructive baseline downgrade is disabled.

Useful diagnostics:

```text
/health/ready       database revision and migration state
X-Request-ID        unique ID returned on every dynamic response
```

Production logs are structured JSON when `JSON_LOGS=true` and include method, path, status, duration, client IP and request ID. API errors remain JSON.

Browser requests receive dedicated responsive frontends for:

```text
400 Invalid request
401 Sign in required
403 Access denied
404 Page not found
409 Action conflict
413 Upload too large
422 Validation failed
429 Too many requests
500 Internal server error
502 Provider request failed
503 Service temporarily unavailable
```

Each page includes a code-specific visual, safe explanation, context-aware action, back/status controls and a copyable request ID. Technical details appear only for safe client errors; 5xx stack traces remain private.

Create future migrations with:

```bash
alembic revision --autogenerate -m "describe change"
alembic upgrade head
```

Review generated migrations before deploying.

---

## 19. Local development

```bash
cp .env.example .env
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8080
```

Or use Docker:

```bash
docker compose up --build
```

Run tests:

```bash
pytest -q
```

---

## 20. Production security checklist

- Use a unique 32-byte `APP_SECRET`
- Rotate every token ever exposed publicly
- Never commit `.env`
- Keep the Railway API token only in Railway variables
- Enable TOTP 2FA for owner/admin accounts
- Link at least two login methods before enabling unlink controls
- Keep the runner image public or configure secure registry access
- Review `/admin/audit`
- Review `/admin/deliveries`
- Review `/admin/bot`
- Configure offsite backups
- Test backup restore regularly
- Configure Railway spend limits
- Keep `FRAME_ANCESTORS='none'` unless embedding is required
- Do not accept anonymous/untrusted malware uploads

---

## Important Railway Pro cost note

Each workload is a separate Railway service. Replicas, managed databases, persistent volumes, network transfer and storage are billed by Railway.

Start conservatively:

```env
DEFAULT_CPU_VCPUS=0.5
DEFAULT_MEMORY_MB=512
MAX_DATABASES_PER_WORKLOAD=1
```

Increase resources only after reviewing Railway usage metrics.

---

## Main URLs

```text
/dashboard                 Workloads and deployment
/project                   Infrastructure canvas
/observability             Health and activity
/account/security          Login methods, 2FA and API keys
/account/notifications     Notification preferences
/admin                     Administration
/admin/bot                 Telegram diagnostics
/admin/deliveries          Notification delivery queue
/status                    Public status page
```

BlazeNXT version: **v1.0.0**.
