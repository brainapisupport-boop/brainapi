# BrainAPI (Starter)

A minimal FastAPI backend that exposes 4 API services:

1. AI text generation  
2. AI image generation  
3. Speech transcription  
4. Automation APIs (webhook + delay workflow)

Includes SaaS-ready baseline controls:

- API key auth on all protected endpoints
- Per-key rate limiting
- CORS policy configuration
- Request ID + request logging
- Security response headers
- Automation webhook safety checks (host allowlist + private network blocking)
- Persistent API keys in database
- Usage metering and admin analytics endpoint
- Redis-backed distributed rate limiting (fallback to in-memory)

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload
```

Open Swagger UI: `http://127.0.0.1:8000/docs`
Open built-in UI: `http://127.0.0.1:8000/`
Open Developer Hub: `http://127.0.0.1:8000/ui/developer.html`
Open Customer Dashboard: `http://127.0.0.1:8000/ui/dashboard.html`

## Full automatic run (recommended)

This project now includes full containerized startup with PostgreSQL + Redis.

### One command (Windows PowerShell)

```powershell
./start.ps1
```

`start.ps1` now auto-creates `.env` from `.env.example` if missing and waits for `/health` before reporting success.

If Docker Desktop engine is unavailable, run local fallback:

```powershell
./start-local.ps1
```

`start-local.ps1` now auto-creates `.venv` (if needed), installs `requirements.txt`, and creates `.env` from `.env.example` when missing.

Stop everything:

```powershell
./stop.ps1
```

Run one-command API smoke tests:

```powershell
./smoke-test.ps1
```

Optional overrides:

```powershell
./smoke-test.ps1 -BaseUrl http://localhost:8000 -UserApiKey <user-key> -AdminApiKey <admin-key>
```

Write results to a JSON report file:

```powershell
./smoke-test.ps1 -ReportPath ./smoke-test-report.json
```

Run end-to-end verification (start if needed + smoke tests + summary):

```powershell
./verify.ps1
```

Useful modes:

```powershell
./verify.ps1 -Mode existing
./verify.ps1 -Mode docker
./verify.ps1 -Mode auto -BaseUrl http://localhost:8000
```

## CI

GitHub Actions workflow is included at `.github/workflows/ci.yml`.

It runs on push/PR and validates all key endpoints in mock mode:

- pytest-based API tests (auth + admin flows)

- health
- text generation
- image generation
- speech transcription
- automation run
- admin create/list/delete key
- admin usage summary

### Manual Docker command

```powershell
docker compose up -d --build
```

Then open: `http://localhost:8000/docs`
Or open built-in UI: `http://localhost:8000/`

Important before first run: edit `.env` and set strong values for:

- `API_KEYS`
- `ADMIN_API_KEY`
- `AUTH_TOKEN_SECRET`

## Provider mode

By default, `.env.example` sets:

```env
PROVIDER=auto
PROVIDER_FALLBACK_ORDER=ollama,groq,gemini,together,openai
```

This startup-friendly mode tries providers in order and falls back automatically when one provider is unavailable.

Recommended low-cost setup:

```env
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_TEXT_MODEL=llama3.2:3b
GROQ_API_KEY=your_key
GEMINI_API_KEY=your_key
```

Optional paid backups in the same chain:

```env
TOGETHER_API_KEY=your_key
OPENAI_API_KEY=your_key
```

`mock` mode is still available for CI and smoke testing when explicitly set.

## SaaS security setup

Set at least one API key and keep `REQUIRE_API_KEY=true`:

```env
REQUIRE_API_KEY=true
API_KEYS=prod-key-1,prod-key-2
RATE_LIMIT_PER_MINUTE=60
```

Admin APIs require `X-Admin-Key` and `ADMIN_API_KEY`.

For production, configure PostgreSQL + Redis:

```env
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/brainapi
REDIS_URL=redis://localhost:6379/0
ENABLE_USAGE_METERING=true
AUTO_CREATE_TABLES=true
```

Send API key via either header:

- `X-API-Key: <key>`
- `Authorization: Bearer <key>`

Admin endpoints use:

- `X-Admin-Key: <admin key>`

Public paths (no key required by default):

- `/health`
- `/docs`
- `/openapi.json`
- `/redoc`

Restrict automation targets in production:

```env
AUTOMATION_ALLOWED_HOSTS=api.your-partner.com,api.razorpay.com
ALLOW_PRIVATE_WEBHOOK_TARGETS=false
AUTOMATION_MAX_STEPS=20
```

If `AUTOMATION_ALLOWED_HOSTS` is set, only those hosts (and their subdomains) are allowed.

To force Together only:

```env
PROVIDER=together
TOGETHER_API_KEY=your_key
TOGETHER_TEXT_MODEL=meta-llama/Llama-3.3-70B-Instruct-Turbo-Free
TOGETHER_IMAGE_MODEL=black-forest-labs/FLUX.1-schnell-Free
TOGETHER_TRANSCRIPTION_MODEL=openai/whisper-large-v3
```

To use OpenAI:

```env
PROVIDER=openai
OPENAI_API_KEY=your_key
```

`/api/v1/speech/transcribe` works with both Together and OpenAI providers.

Capability notes in `auto` mode:

- Text generation supports `ollama`, `groq`, `gemini`, `together`, and `openai`.
- Image generation supports `together` and `openai`.
- Speech transcription supports `together` and `openai`.

## Endpoints

- `GET /health`
- `GET /api/v1/metrics`
- `POST /api/v1/auth/signup`
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/request-reset`
- `POST /api/v1/auth/reset-password`
- `POST /api/v1/text/generate`
- `POST /api/v1/image/generate`
- `POST /api/v1/speech/transcribe` (multipart form-data with `file`)
- `POST /api/v1/automation/run`
- `GET /api/v1/me/usage?hours=24`
- `POST /api/v1/me/api-key/rotate` (requires session token)
- `POST /api/v1/admin/api-keys` (requires `X-Admin-Key`)
- `GET /api/v1/admin/api-keys` (requires `X-Admin-Key`)
- `DELETE /api/v1/admin/api-keys/{key_id}` (requires `X-Admin-Key`)
- `PATCH /api/v1/admin/api-keys/{key_id}/billing` (requires `X-Admin-Key`)
- `POST /api/v1/admin/billing/razorpay/order` (requires `X-Admin-Key`)
- `POST /api/v1/admin/billing/razorpay/verify` (requires `X-Admin-Key`)
- `POST /api/v1/billing/razorpay/webhook` (public webhook endpoint)
- `GET /api/v1/public/plans` (public)
- `POST /api/v1/public/signup-trial` (public)
- `POST /api/v1/admin/emails/schedule-trial-reminders` (requires `X-Admin-Key`)
- `POST /api/v1/admin/emails/send-pending?limit=50` (requires `X-Admin-Key`)
- `GET /api/v1/admin/usage?hours=24` (requires `X-Admin-Key`)

## Billing model (India / Razorpay friendly)

New DB API keys now support a free trial model:

- Default free trial: `30` days (`trial_days` on key creation)
- If trial expires and key is unpaid, protected endpoints return `402 Payment required`
- After successful Razorpay payment, mark key as paid using admin billing endpoint

Create key with custom trial:

```json
{
  "name": "acme-production",
  "rate_limit_per_minute": 120,
  "trial_days": 30
}
```

Mark key as paid:

```json
{
  "is_paid": true
}
```

## Razorpay integration (India)

Configure in `.env`:

```env
PUBLIC_BASE_URL=https://your-domain.com
RAZORPAY_KEY_ID=rzp_live_xxx
RAZORPAY_KEY_SECRET=your_secret
RAZORPAY_WEBHOOK_SECRET=your_webhook_secret
DEFAULT_PLAN_AMOUNT_INR=499
DEFAULT_PLAN_NAME=BrainAPI Pro
```

Create order (admin):

```json
{
  "api_key_id": "<key-id>",
  "amount_inr": 499,
  "plan_name": "BrainAPI Pro",
  "customer_name": "User Name",
  "customer_email": "user@example.com",
  "customer_phone": "+919999999999"
}
```

Verify payment (admin fallback):

```json
{
  "api_key_id": "<key-id>",
  "razorpay_order_id": "order_xxx",
  "razorpay_payment_id": "pay_xxx",
  "razorpay_signature": "signature_xxx"
}
```

Preferred production flow:

1. Create Razorpay order via admin endpoint.
2. Complete checkout in frontend.
3. Razorpay sends `payment.captured` webhook to `/api/v1/billing/razorpay/webhook`.
4. API key is marked paid automatically when `notes.api_key_id` is present.

## User acquisition flow (live onboarding)

Homepage now includes a public signup section so users can self-start:

1. User submits name + email on `/`.
2. API creates a lead record and issues a trial API key instantly.
3. User can begin calling APIs immediately.
4. After trial period, calls return `402` until payment is completed.

Public trial signup request body:

```json
{
  "name": "Sumit",
  "email": "sumit@example.com",
  "company": "BrainAPI Labs",
  "use_case": "Build AI automations",
  "source": "website",
  "consent": true
}
```

## Email lifecycle automation

Automated conversion emails are supported with SMTP:

- Welcome email queued when user signs up for trial
- Payment success email queued when key is marked paid
- Trial reminder emails can be queued for day 7/3/1 and expiry day

Configure SMTP in `.env`:

```env
SMTP_HOST=smtp.your-provider.com
SMTP_PORT=587
SMTP_USERNAME=apikey_or_user
SMTP_PASSWORD=secret
SMTP_USE_TLS=true
EMAIL_FROM_ADDRESS=noreply@your-domain.com
EMAIL_FROM_NAME=BrainAPI
EMAIL_REPLY_TO=support@your-domain.com
```

Run reminder + send jobs (admin):

1. `POST /api/v1/admin/emails/schedule-trial-reminders`
2. `POST /api/v1/admin/emails/send-pending?limit=100`

For production, run these endpoints from a scheduler (cron/GitHub Actions/Cloud Scheduler) at least once daily.

### GitHub Actions daily scheduler

Workflow file: `.github/workflows/email-lifecycle-cron.yml`

It runs every day and can also be triggered manually from Actions UI.

Set repository secrets:

- `BRAINAPI_BASE_URL` (example: `https://api.your-domain.com`)
- `BRAINAPI_ADMIN_API_KEY`
- `BRAINAPI_EMAIL_SEND_LIMIT` (optional, default `100`)

What it does:

1. Calls `POST /api/v1/admin/emails/schedule-trial-reminders`
2. Calls `POST /api/v1/admin/emails/send-pending?limit=<value>`

### One-command local runner

If you want to run the same jobs locally right now:

```powershell
./run-email-jobs.ps1
```

Optional:

```powershell
./run-email-jobs.ps1 -BaseUrl http://localhost:8000 -AdminApiKey <admin-key> -SendLimit 100
```

### One-command lifecycle verifier

Validate health, auth, SMTP config, and optionally run lifecycle jobs:

```powershell
./verify-lifecycle.ps1
```

Optional examples:

```powershell
./verify-lifecycle.ps1 -SkipRun
./verify-lifecycle.ps1 -BaseUrl http://localhost:8000 -AdminApiKey <admin-key> -SendLimit 50
```

## Production credential bootstrap

If you do not have live credentials yet, generate placeholder production entries:

```powershell
./bootstrap-production-config.ps1 -UsePlaceholders
```

When live credentials are available, apply real values:

```powershell
./bootstrap-production-config.ps1 \
  -PublicBaseUrl https://api.your-domain.com \
  -SmtpHost smtp.your-provider.com \
  -SmtpPort 587 \
  -SmtpUsername <smtp-user> \
  -SmtpPassword (ConvertTo-SecureString '<smtp-pass>' -AsPlainText -Force) \
  -EmailFromAddress noreply@your-domain.com \
  -EmailFromName BrainAPI \
  -EmailReplyTo support@your-domain.com \
  -RazorpayKeyId <rzp_live_key_id> \
  -RazorpayKeySecret <rzp_live_key_secret> \
  -RazorpayWebhookSecret <rzp_webhook_secret>
```

Related operator docs:

- `LEGAL_REVIEW_CHECKLIST.md`
- `GITHUB_SECRETS_SETUP.md`

## Deployment-ready checklist

- Production domain + HTTPS (reverse proxy / ingress)
- Strong secrets (`ADMIN_API_KEY`, provider keys, Razorpay secrets)
- PostgreSQL + Redis managed instances
- Monitoring + alerting + log retention
- Daily database backups + restore drill
- Legal pages live (`/ui/privacy.html`, `/ui/terms.html`, `/ui/refund.html`)
- Billing tested in Razorpay test mode before live mode
- Trial expiry and paid-upgrade flow validated end-to-end

## SEO baseline implemented

Technical SEO now included:

- Improved homepage title/description/OpenGraph/Twitter metadata
- Structured data (`SoftwareApplication`) on home page
- `robots.txt` endpoint
- `sitemap.xml` endpoint
- Crawlable legal pages linked from home page

To improve ranking further, add:

- A dedicated marketing site with content pages targeting long-tail keywords
- Backlinks from developer communities, directories, and case studies
- Search Console + Bing Webmaster verification
- Regular technical/performance audits and content publishing

## Built-in UI (MVP)

Open `http://localhost:8000/`.

The page includes sections for:

- Text generation
- Image generation
- Speech transcription (file upload)
- Automation run
- Admin key management + usage view

How to use it:

1. Paste `User API Key` and `Admin Key` from `.env`.
2. Click `Save Keys` (stored in browser localStorage).
3. Use each section to call endpoints and inspect JSON responses.

Notes:

- `GET /health` is public.
- Protected user routes require user key.
- `/api/v1/admin/*` requires admin key.

### Admin create key example

```json
{
  "name": "acme-production",
  "rate_limit_per_minute": 120
}
```

### Sample automation request

```json
{
  "name": "notify-and-wait",
  "steps": [
    {
      "type": "webhook",
      "url": "https://httpbin.org/post",
      "method": "POST",
      "headers": {"Content-Type": "application/json"},
      "body": {"event": "hello"}
    },
    {
      "type": "delay",
      "seconds": 1.5
    }
  ]
}
```
