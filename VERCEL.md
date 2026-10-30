# Vercel deployment — hosted dashboard and email alerts

The hosted dashboard is served from the production root. The protected `/api/run` endpoint refreshes commodity analysis; GitHub Actions schedules a full email digest every two hours while your laptop is off.

```
GitHub Actions (every two hours, UTC)
        |
        v
GET /api/run?digest=1  (protected Python function)
        |
        +--> Lighter public commodity quotes + Yahoo Finance historical candles + RSS/Google News
        +--> Upstash Redis  (persistent alert state)
        +--> Gmail SMTP     (scheduled digest + material changes)
```

Vercel functions are ephemeral and have a read-only filesystem, so the alert baseline lives in
Upstash Redis, not on disk. The local `reports/alert_state.json` is only a mirror for one
invocation.

---

## Prerequisites

- A Vercel project containing this directory.
- An Upstash Redis database (free tier is plenty: roughly 600 commands/day).
- The Gmail App Password you already created.
- **Scheduled email digest**: this repository includes a GitHub Actions workflow for every-two-hour updates. On Vercel Hobby, use the workflow rather than native Vercel Cron, which is limited to once daily.

Vercel's Hobby cron limit is documented here:
<https://vercel.com/docs/cron-jobs/usage-and-pricing>

---

## 1. Add Upstash Redis

In the Vercel dashboard:

1. Open the project → **Storage** → **Marketplace**.
2. Add **Upstash Redis** (free plan).
3. Connect it to the project.

Vercel injects either:

```text
UPSTASH_REDIS_REST_URL
UPSTASH_REDIS_REST_TOKEN
```

or the older KV names:

```text
KV_REST_API_URL
KV_REST_API_TOKEN
```

The function accepts either pair.

---

## 2. Set environment variables

Project → **Settings** → **Environment Variables** (Production):

| Name | Value |
|---|---|
| `CRON_SECRET` | A long random string; protects the endpoint |
| `GMAIL_USER` | `you@gmail.com` |
| `GMAIL_APP_PASSWORD` | The 16-character Google App Password |
| `ALERT_TO` | Where to send alerts (can be the same address) |
| `UPSTASH_REDIS_REST_URL` | Auto-filled by the Marketplace integration |
| `UPSTASH_REDIS_REST_TOKEN` | Auto-filled by the Marketplace integration |
| `NEWS_DAYS` | Optional, default `4` |
| `ACCOUNT_SIZE` | Optional, default `50000` |
| `RISK_PCT` | Optional, default `1.0` |
| `LIGHTER_QUOTES_ENABLED` | Optional, default `1`; set `0` to disable Lighter quotes |
| `LIGHTER_API_BASE_URL` | Optional; defaults to `https://mainnet.zklighter.elliot.ai` |

Generate a secret without PowerShell, for example in the Vercel dashboard or with any password
manager. Do not commit `gmail.env`; `.vercelignore` already excludes it.

`GMAIL_SMTP_PORT=587` is optional if Vercel cannot reach port 465.

---

## 3. Deploy

From this directory:

```bash
vercel --prod
```

Or import the repository in the Vercel dashboard. The settings are in `vercel.json`:

- `api/run.py` is the Python function
- `maxDuration: 60`
- `includeFiles: commodity_agent/**` so the function can import the agent

No build command is needed. `requirements.txt` intentionally has no runtime dependencies.

---

## 4. Verify

### Data only (no email, no state)

```bash
curl -s \
  -H "Authorization: Bearer $CRON_SECRET" \
  "https://YOUR-PROJECT.vercel.app/api/run?preview=1"
```

You should get JSON with `"preview": true` and all five signals.

### Gmail from Vercel

This sends the same test message as `alert-test`, but from the Vercel function:

```bash
curl -s \
  -H "Authorization: Bearer $CRON_SECRET" \
  "https://YOUR-PROJECT.vercel.app/api/run?test_email=1"
```

If this fails with an SMTP error, check Vercel logs and try `GMAIL_SMTP_PORT=587`. Vercel
blocks port 25 but leaves 465/587 open for SMTP from Functions.

### First real run

```bash
curl -s \
  -H "Authorization: Bearer $CRON_SECRET" \
  "https://YOUR-PROJECT.vercel.app/api/run"
```

The first run is a **baseline**: `"alert_status": "baseline"` and no email. Later runs email
only when an action flips, confidence moves 15+ points, or price crosses a published
stop/target/trigger.

---

## 5. Schedule every two hours and email a full snapshot

The GitHub Actions workflow at `.github/workflows/two-hour-refresh.yml` calls the production API every two hours (UTC) with `digest=1`. Each successful run emails the current five-instrument book and any material changes. Unlike normal change-only mode, a scheduled digest is sent even if no signal changed.

This project is on Vercel Hobby, so do **not** add a Vercel Cron expression that runs more than once per day. Hobby deployments reject more frequent cron jobs. GitHub Actions is used as the external scheduler instead.

One-time setup:

1. Open the GitHub repository **Settings → Secrets and variables → Actions → New repository secret**.
2. Name it `CRON_SECRET` and set its value to the same secret as the Vercel project's **Production** `CRON_SECRET` environment variable. Do not paste the secret into source code or a chat.
3. Go to **Actions → Commodity Signal Desk — two-hour refresh**. Enable workflows if GitHub prompts you.
4. Use **Run workflow** for a one-off first run; it sends a real digest email. Otherwise, the schedule begins at the next even UTC hour.

The workflow uses the protected production API and does not expose the secret. GitHub scheduled workflows can sometimes start late, so this is approximately every two hours rather than a guaranteed exact-time service.

### Option A — Vercel Pro (native, recommended)

Add this to `vercel.json` and redeploy:

```json
{
  "functions": {
    "api/run.py": {
      "maxDuration": 60,
      "includeFiles": "commodity_agent/**"
    }
  },
  "crons": [
    { "path": "/api/run", "schedule": "*/5 * * * *" }
  ]
}
```

Vercel sends `Authorization: Bearer $CRON_SECRET` automatically when `CRON_SECRET` is set.
Cron schedules are UTC and have no retries.

### Option B — Vercel Hobby (external scheduler)

Hobby cannot run a cron more than once per day. Keep the function on Vercel and point a free
scheduler at it:

1. Create a free account at <https://cron-job.org>.
2. New job → URL `https://YOUR-PROJECT.vercel.app/api/run`.
3. Schedule: every **5 minutes**.
4. Request method: **GET**.
5. Add the header `Authorization: Bearer <CRON_SECRET>`.
6. Enable the job.

If the project has **Deployment Protection** on, the request will get a 401 before it reaches
the function. Either turn protection off for Production or use a protection-bypass header.

The endpoint also accepts `?token=<CRON_SECRET>` for schedulers that cannot send headers, but
the Authorization header is preferred because query strings can appear in logs.

---

## 6. Operations

- **Logs**: Vercel dashboard → project → **Logs** → `/api/run`.
- **State**: Upstash key `commodity:alert_state:v1`.
- **Re-baseline**: delete that key; the next run saves a fresh baseline and sends nothing.
- **Change cadence**: edit the cron schedule (Pro) or the external job (Hobby).
- **Pause**: disable the cron job or external job; the function does nothing until called.
- **Cost**: Vercel Hobby includes 2 daily crons; a Pro plan is needed for native 5-minute
  cron. Upstash free tier is sufficient. Function runtime is roughly 5–8 seconds per run.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `503 Persistent alert state is not configured` | Add Upstash and redeploy so env vars are present |
| `503 CRON_SECRET is not configured` | Add the env var and redeploy |
| `401 Unauthorized` | Header/token does not match `CRON_SECRET`, or Deployment Protection is intercepting |
| No email after first run | First run is a baseline by design; wait for a change |
| No email at all | Check `/api/run?test_email=1`, Vercel logs, Gmail spam |
| `Upstash request failed` | Bad REST URL/token, or Upstash database paused |
| Yahoo timeout/429 | Cloud IP rate limit; retry next cycle; Vercel logs show the warning |
| Function timeout | Increase `maxDuration` if your plan allows; usually 5–8 seconds is enough |

## Notes

- **The dashboard is hosted** at the production root URL. The email includes the current book and plans.
- **Scheduled digest mode emails every two hours** even when no material signal changed. The ordinary `/api/run` call without `digest=1` remains change-only.
- **Turn off the local watcher when Vercel is live.** The laptop and Vercel use separate
  alert-state stores, so running both will produce duplicate emails. Stop the `pythonw`
  watcher and remove the Startup shortcut once the Vercel job is working.
- **Gmail SMTP** is used directly, the same code as local. If Google blocks the Vercel egress
  IP or your Workspace disables SMTP, switch to an HTTPS email API such as Resend; that would
  require a small provider adapter in `alerts.py`.
- **Do not put `GMAIL_APP_PASSWORD` in the repository.** Use Vercel env vars only.
- **The scheduler is not guaranteed-once.** A rare duplicate email is possible if a run times
  out after sending but before saving state.
