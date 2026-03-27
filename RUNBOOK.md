# Helvex — Operational Runbook

General fixes, recovery procedures, and operational checklists.

---

## Table of Contents

1. [Database: Restore from S3 Backup (full rebuild)](#1-database-restore-from-s3-backup-full-rebuild)
2. [Database: Point-in-Time Recovery (PITR)](#2-database-point-in-time-recovery-pitr)
3. [Database: Verify Backups Are Actually Running](#3-database-verify-backups-are-actually-running)
4. [Jobs: Stuck in `running` State](#4-jobs-stuck-in-running-state)
5. [Auth: Tokens Rejected After Redeploy](#5-auth-tokens-rejected-after-redeploy)
6. [Email: Verification Not Sending](#6-email-verification-not-sending)
7. [Google Search: Quota Exhausted](#7-google-search-quota-exhausted)
8. [Pod: OOMKilled](#8-pod-oomkilled)
9. [Deploy: Migration Fails on Startup](#9-deploy-migration-fails-on-startup)
10. [Useful kubectl Commands](#10-useful-kubectl-commands)
11. [Logs: Where to Find Them](#11-logs-where-to-find-them)
12. [Debug: Temporarily Enable Verbose Logging](#12-debug-temporarily-enable-verbose-logging)

---

## 1. Database: Restore from S3 Backup (full rebuild)

Use this after destroying and recreating the Hetzner infra from scratch (Terraform), or after a catastrophic DB failure.

**How it works:** CloudNativePG restores the most recent base backup from S3, then replays every WAL segment produced after it — recovering to the last archived segment (typically within seconds of the crash).

### Steps

**1. Rebuild infra (if needed)**
```bash
cd infra/terraform/envs/prod
terraform apply
```

**2. Bootstrap the K8s cluster and operators**
```bash
# Trigger [deploy-prod] — this installs cert-manager, CloudNativePG, ARC, then the app
git commit --allow-empty -m "rebuild [deploy-prod]"
git push
```

**3. Before deploying, confirm S3 credentials are in the secret**
```bash
kubectl get secret helvex-env -n helvex-prod -o jsonpath='{.data}' | \
  python3 -c "import sys,json,base64; d=json.load(sys.stdin); [print(k) for k in d]"
# S3_ACCESS_KEY and S3_SECRET_KEY must appear in the output
```

If they're missing, add them to the GitHub Actions secrets (`S3_ACCESS_KEY`, `S3_SECRET_KEY`) and re-run the deploy workflow to recreate the secret.

**4. Set `restoreFromBackup: true` in `infra/environments/prod.yaml`**
```yaml
postgres:
  restoreFromBackup: true   # ← add this line
```

**5. Deploy the app**
```bash
git add infra/environments/prod.yaml
git commit -m "restore db from backup [deploy-app]"
git push
```

CloudNativePG will:
- Find the latest base backup in `s3://helvex-backups/pg/`
- Restore it into a fresh 20 Gi PVC
- Replay all WAL segments up to the latest available
- Promote to primary

**6. Watch recovery progress**
```bash
kubectl get cluster helvex-pg -n helvex-prod -w
# Status moves: Restoring → Running

kubectl logs -n helvex-prod helvex-pg-1 -f
# Look for "database system is ready to accept connections"
```

**7. Flip `restoreFromBackup` back to `false` immediately**

Leaving it as `true` means the next `helmfile apply` will try to re-recover and conflict with the running cluster.

```yaml
postgres:
  restoreFromBackup: false   # ← revert
```

```bash
git add infra/environments/prod.yaml
git commit -m "restore complete — reset restoreFromBackup [deploy-app]"
git push
```

---

## 2. Database: Point-in-Time Recovery (PITR)

Use this when you need to recover to a specific moment — e.g. before a bad migration, a `DELETE` without `WHERE`, or accidental data corruption.

WAL segments are archived continuously (within seconds of each transaction), so you can target any point within the retention window (48h in prod by default).

### Steps

Follow all steps from [Section 1](#1-database-restore-from-s3-backup-full-rebuild), but in **step 4** also add a `recoveryTarget` to the cluster template.

**Edit `infra/charts/helvex/templates/postgres-cluster.yaml`** — change the `recovery` bootstrap block:

```yaml
bootstrap:
  recovery:
    source: helvex-backup
    recoveryTarget:
      targetTime: "2026-03-25 14:30:00"   # UTC — replay stops at this moment
```

Other `recoveryTarget` options (use only one):

```yaml
# Stop at a specific LSN (log sequence number — from pg_current_wal_lsn())
recoveryTarget:
  targetLSN: "0/5000060"

# Stop immediately after a named restore point
# (created with: SELECT pg_create_restore_point('before-migration-42'))
recoveryTarget:
  targetName: "before-migration-42"

# Stop after a specific transaction ID
recoveryTarget:
  targetXID: "1234567"

# Stop at the end of the latest available WAL (default — same as not setting a target)
recoveryTarget:
  targetImmediate: false
```

**After recovery is confirmed**, revert the `recoveryTarget` block and the `restoreFromBackup` flag, then redeploy.

### Finding the right timestamp

If you know roughly when the bad event happened:

```bash
# Connect to the DB pod (while it's still running / before full rebuild)
kubectl exec -n helvex-prod -it helvex-pg-1 -- psql -U helvex -d helvex

-- Find when a specific row was last touched (requires audit_logs table)
SELECT timestamp, action, resource_type, resource_id
FROM audit_logs
ORDER BY timestamp DESC
LIMIT 50;

-- Current WAL position (useful for snapshotting a restore point)
SELECT pg_current_wal_lsn(), now();
```

---

## 3. Database: Verify Backups Are Actually Running

Backups silently do nothing if S3 credentials are wrong. Check regularly.

```bash
# List completed backups
kubectl get backup -n helvex-prod

# Describe the most recent one — look for status: completed
kubectl describe backup -n helvex-prod | grep -A 5 "Status:"

# Check the scheduled backup object
kubectl describe scheduledbackup helvex-pg-backup -n helvex-prod

# If you see "hourly" backups in object storage, you almost certainly have more
# than one ScheduledBackup resource in the namespace (e.g. an old/manual one).
kubectl get scheduledbackup -n helvex-prod

# Delete any unexpected schedules (keep only the one managed by Helm)
kubectl delete scheduledbackup -n helvex-prod <NAME>

# Check barman logs on the primary pod
kubectl logs -n helvex-prod helvex-pg-1 -c postgres | grep -i "barman\|backup\|WAL"
```

Notes on S3 usage:
- WAL growth in object storage is expected when backups are enabled: WAL is archived continuously for PITR.
- Your PITR window is effectively bounded by the oldest retained base backup; if you retain 2 days, you may store up to ~2 days of WAL.
- To reduce object storage usage, reduce `postgres.backupRetention` (shorter PITR window) and/or enable compression (`postgres.backupWalCompression`, `postgres.backupDataCompression`) in Helm values.

Expected output for a healthy backup:
```
Starting barman-cloud-backup
Backup completed successfully
WAL file archived successfully
```

If you see `AccessDenied` or `NoSuchBucket` — the S3 credentials or bucket name are wrong.

---

## 3b. Database: Weekly Export to Storage Box (long retention)

Prod also runs a weekly logical export (`pg_dump`) to a Storage Box as a second backup target (separate failure domain) with ~2 months retention.

The job also prunes old exports automatically (deletes `helvex-*.dump` older than `retentionDays`).

**Helm values (prod):** `postgres.weeklyExport.enabled: true`

**Required GitHub Secrets (propagate into `helvex-env`):**
- `STORAGEBOX_HOST` (e.g. `u12345.your-storagebox.de`)
- `STORAGEBOX_USER` (e.g. `u12345`)
- `STORAGEBOX_PATH` (optional; dedicated folder, e.g. `/backups/helvex/pg-prod`; if empty, uses the Storage Box user home)
- `STORAGEBOX_PORT` (Hetzner Storage Box SSH is commonly `23`)
- `STORAGEBOX_SSH_PRIVATE_KEY` (private key used for SFTP)

**Verify it exists and is scheduled:**
```bash
kubectl get cronjob -n helvex-prod | grep pg-weekly-export
kubectl describe cronjob -n helvex-prod helvex-pg-weekly-export
```

**Manually trigger once to test:**
```bash
kubectl create job -n helvex-prod --from=cronjob/helvex-pg-weekly-export pg-weekly-export-manual
kubectl logs -n helvex-prod job/pg-weekly-export-manual -f
```

---

## 4. Jobs: Stuck in `running` State

A job showing `status=running` with no progress means the worker pod crashed mid-job without a graceful shutdown. The job never cleaned up its own state.

**Fix:** `requeue_interrupted_jobs()` runs automatically on app startup and resets these to `queued`. A pod restart is enough:

```bash
kubectl rollout restart deployment/helvex -n helvex-prod
# or restart the worker pod specifically
kubectl rollout restart deployment/helvex-worker -n helvex-prod
```

To check what happened before the crash:
```bash
# Job event log
kubectl exec -n helvex-prod deploy/helvex -- \
  python3 -c "
from app.database import SessionLocal
from app import crud
with SessionLocal() as db:
    for e in crud.list_job_events(db, job_id=<JOB_ID>):
        print(e.level, e.created_at, e.message)
"
```

Or query directly:
```bash
kubectl exec -n helvex-prod -it helvex-pg-1 -- psql -U helvex -d helvex \
  -c "SELECT level, message, created_at FROM job_run_events WHERE job_id = <ID> ORDER BY created_at DESC LIMIT 20;"
```

---

## 5. Auth: Tokens Rejected After Redeploy

**Symptom:** All users get 401 / kicked to login after a redeploy.

**Cause:** `SECRET_KEY` rotated. Dev uses an ephemeral random key on every startup — intentional. In prod this means the `SECRET_KEY` in the `helvex-env` K8s Secret is either missing or was regenerated.

**Check:**
```bash
kubectl get secret helvex-env -n helvex-prod -o jsonpath='{.data.SECRET_KEY}' | base64 -d | wc -c
# Should be >= 32 characters
```

**Fix:** Ensure `SECRET_KEY` is set in GitHub Actions secrets and the deploy workflow is recreating the K8s secret with it. Re-running the deploy pipeline recreates the secret.

Note: rotating `SECRET_KEY` intentionally (e.g. after a leak) is fine — it immediately invalidates all active sessions, which is the desired effect.

---

## 6. Email: Verification Not Sending

**Check SMTP config is present in the secret:**
```bash
kubectl get secret helvex-env -n helvex-prod -o jsonpath='{.data}' | \
  python3 -c "import sys,json,base64; d=json.load(sys.stdin); [print(k,'=',base64.b64decode(v).decode()[:6]+'...') for k,v in d.items() if 'SMTP' in k]"
```

**Test SMTP connectivity from inside the cluster:**
```bash
kubectl exec -n helvex-prod deploy/helvex -- python3 -c "
import smtplib
smtp = smtplib.SMTP('your.smtp.host', 587, timeout=10)
smtp.ehlo(); smtp.starttls(); smtp.ehlo()
smtp.login('user', 'password')
print('SMTP OK')
smtp.quit()
"
```

**Check the app logs for the actual error:**
```bash
kubectl logs -n helvex-prod deploy/helvex | grep -i "smtp\|email\|verification"
```

**Common causes:**
- `SMTP_FROM` missing or malformed — must be `"Display Name <addr@domain.com>"` or plain `addr@domain.com`
- Port 587 blocked by Hetzner (they block port 25; 587 STARTTLS is fine)
- `APP_BASE_URL` wrong — verification links point to the wrong domain

---

## 7. Google Search: Quota Exhausted

**Symptom:** Batch enrichment job completes instantly with 0 searches performed.

**Check current quota state:**
```bash
curl -s -H "Authorization: Bearer <token>" https://helvex.dicy.ch/api/v1/settings | \
  python3 -m json.tool | grep -i google
```

**Reset quota manually** (if the daily reset didn't fire):
```bash
curl -s -X PATCH -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"google_searches_today": "0"}' \
  https://helvex.dicy.ch/api/v1/settings
```

**Increase daily limit** (paid Serper plan):
```bash
curl -s -X PATCH -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"google_daily_quota": "500"}' \
  https://helvex.dicy.ch/api/v1/settings
```

---

## 8. Pod: OOMKilled

**Identify which pod was killed:**
```bash
kubectl get pods -n helvex-prod
# Look for OOMKilled in the REASON column

kubectl describe pod <pod-name> -n helvex-prod | grep -A 5 "Last State"
```

**Memory-heavy operations and where to run them:**

| Operation | Pod | Notes |
|---|---|---|
| Claude classify job | `helvex-worker` | Loads Anthropic SDK + large response batches |
| TF-IDF cluster job | `helvex-worker` | scikit-learn loads all purpose text into RAM |
| Geocoding (swisstopo) | `helvex` (app) | SQLite DB is mmap'd; ~150 MB cold, stays resident |
| Bulk import | `helvex` or `helvex-worker` | Minimal memory; safe anywhere |

If the **app pod** is OOMKilled during a classification or clustering job, those jobs should only be triggered via the RQ worker (`USE_RQ=true`, `worker.enabled: true`), not run in-process.

**Increase memory limit** in `infra/environments/prod.yaml`:
```yaml
resources:
  limits:
    memory: 2Gi   # up from 1Gi
```

---

## 9. Deploy: Migration Fails on Startup

**Symptom:** App pod crash-loops; logs show `alembic.exc.OperationalError` or connection refused.

**Most common causes:**

1. **DB not ready yet** — CloudNativePG cluster still initialising. Check:
   ```bash
   kubectl get cluster helvex-pg -n helvex-prod
   # Wait for Ready: true
   ```

2. **Wrong DATABASE_URL** — verify the secret:
   ```bash
   kubectl get secret helvex-env -n helvex-prod \
     -o jsonpath='{.data.DATABASE_URL}' | base64 -d
   ```

3. **Migration conflict** — two pods running `alembic upgrade head` simultaneously on first deploy. The second pod will fail with a lock error; it will restart and succeed once the first finishes.

4. **Failed migration that can't be rolled back** — connect directly and inspect:
   ```bash
   kubectl exec -n helvex-prod -it helvex-pg-1 -- psql -U helvex -d helvex \
     -c "SELECT version_num FROM alembic_version;"
   # Compare to the head in your local alembic/versions/
   ```

---

## 10. Useful kubectl Commands

```bash
# Tail backend logs (use -l selector — deploy/helvex matches frontend pods too)
kubectl logs -n helvex-prod -l app.kubernetes.io/component=app -f

# Tail worker logs
kubectl logs -n helvex-prod -l app.kubernetes.io/component=worker -f

# Tail frontend logs
kubectl logs -n helvex-prod -l app.kubernetes.io/component=frontend -f

# Tail DB logs
kubectl logs -n helvex-prod helvex-pg-1 -f

# Get all pods and their status
kubectl get pods -n helvex-prod -o wide

# Describe a crashing pod (shows OOMKill, image pull errors, etc.)
kubectl describe pod <pod-name> -n helvex-prod

# Open a shell in the app pod
kubectl exec -n helvex-prod -it deploy/helvex -- bash

# Connect to Postgres directly (in-cluster)
kubectl exec -n helvex-prod -it helvex-pg-1 -- \
  env PGPASSWORD=$(kubectl get secret helvex-env -n helvex-prod -o jsonpath='{.data.password}' | base64 -d) \
  psql -U helvex -d helvex -h 127.0.0.1


# Connect to Postgres via local pgAdmin (SSH tunnel via control plane — no local kubectl needed)
# Run on your local machine, keep the terminal open while using pgAdmin.
#
# Notes:
# - Prefer the CloudNativePG read-write Service (stable across failover): svc/helvex-pg-rw
# - On k3s, /etc/rancher/k3s/k3s.yaml is root-readable by default, so run kubectl via sudo.
# - If kubectl works when you SSH in interactively but fails in the one-liner, it's usually because
#   non-interactive SSH commands don't load your shell init files (where KUBECONFIG or kubectl aliases are set).
ssh -t -L 5432:localhost:5432 ubuntu@<your-server-ip> \
  "sudo kubectl -n helvex-prod port-forward svc/helvex-pg-rw 5432:5432"
# pgAdmin credentials: host=localhost, port=5432, user=helvex, password=<from secret above>
#
# If your local port 5432 is already used, change the *left* side:
# ssh -t -L 5433:localhost:5432 ubuntu@<your-server-ip> "sudo kubectl -n helvex-prod port-forward svc/helvex-pg-rw 5432:5432"
# Then use host=localhost, port=5433 in pgAdmin.
#
# Optional: one-time setup on the control plane so you can run kubectl without sudo:
# ssh ubuntu@<your-server-ip>
#   sudo install -d -m 0700 -o ubuntu -g ubuntu /home/ubuntu/.kube
#   sudo cp /etc/rancher/k3s/k3s.yaml /home/ubuntu/.kube/config
#   sudo chown ubuntu:ubuntu /home/ubuntu/.kube/config
#   sudo chmod 0600 /home/ubuntu/.kube/config
#
# If you already have ~/.kube/config (or KUBECONFIG) set up for the ubuntu user, you can run:
# ssh -t -L 5432:localhost:5432 ubuntu@<your-server-ip> \
#   "bash -lc 'kubectl -n helvex-prod port-forward svc/helvex-pg-rw 5432:5432'"

# Force restart a deployment (e.g. after updating a secret)
kubectl rollout restart deployment/helvex -n helvex-prod

# Watch rollout progress
kubectl rollout status deployment/helvex -n helvex-prod --timeout=120s

# List all K8s secrets (not their values)
kubectl get secrets -n helvex-prod

#List configs of secrets
kubectl get secret monitoring-grafana -n monitoring -o yaml

# Get a secret
kubectl get secret <secret-name> -n <namespace> -o jsonpath='{.data.<key>}' | base64 -d

#specifically for Grafana
kubectl get secret monitoring-grafana -n monitoring -o jsonpath='{.data.admin-password}' | base64 -d


# Check cert-manager certificate status
kubectl get certificate -n helvex-prod
kubectl describe certificate helvex-tls -n helvex-prod

# Check CloudNativePG cluster health
kubectl get cluster -n helvex-prod
kubectl describe cluster helvex-pg -n helvex-prod

# List completed/scheduled backups
kubectl get backup -n helvex-prod
kubectl get scheduledbackup -n helvex-prod
```

---

## 11. Logs: Where to Find Them

### Pod logs (stdout — the primary source)

All Python logging goes to stdout at `INFO` level and is captured by Kubernetes.

```bash
# Backend app — live tail
kubectl logs -n helvex-prod -l app.kubernetes.io/component=app -f

# RQ worker — live tail
kubectl logs -n helvex-prod -l app.kubernetes.io/component=worker -f

# Next.js frontend — live tail
kubectl logs -n helvex-prod -l app.kubernetes.io/component=frontend -f

# Postgres — live tail
kubectl logs -n helvex-prod helvex-pg-1 -f

# Last 500 lines (no follow)
kubectl logs -n helvex-prod -l app.kubernetes.io/component=app --tail=500

# Logs since a point in time
kubectl logs -n helvex-prod -l app.kubernetes.io/component=app --since=1h
kubectl logs -n helvex-prod -l app.kubernetes.io/component=app --since-time="2025-03-26T08:00:00Z"

# Previous pod instance (after a crash-loop restart)
kubectl logs -n helvex-prod -l app.kubernetes.io/component=app -p
```

Log format is `LEVEL:logger_name:message` (e.g. `INFO:app.api.routes.auth:auth.login_ok user_id=3`).

### Grafana (metrics + dashboards)

URL: **https://grafana.helvex.dicy.ch**

Credentials: username `admin`, password from:
```bash
kubectl get secret monitoring-grafana -n monitoring -o jsonpath='{.data.admin-password}' | base64 -d
```

Useful dashboards to check:
- **Node Exporter / Full** — CPU, memory, disk I/O, network on the host
- **Kubernetes / Pods** — per-pod CPU/memory, restart counts
- **FastAPI** — request rate, latency, error rate (if the `/metrics` endpoint is scraped)

Grafana shows metrics only — it does **not** aggregate pod logs (no Loki installed).

### Structured job logs (in the database)

Job-level events (progress, warnings, errors) are stored in the `job_run_events` table and visible in the UI under each job's detail panel. To query directly:

```bash
# Connect to Postgres (see section 10 for full connection command), then:
SELECT j.job_type, j.label, j.status, e.level, e.message, e.created_at
FROM job_run_events e
JOIN job_runs j ON j.id = e.job_id
WHERE j.id = <job_id>
ORDER BY e.created_at;

# Last 50 error/warn events across all jobs
SELECT j.job_type, j.label, e.level, e.message, e.created_at
FROM job_run_events e
JOIN job_runs j ON j.id = e.job_id
WHERE e.level IN ('error', 'warn')
ORDER BY e.created_at DESC
LIMIT 50;
```

---

## 12. Debug: Temporarily Enable Verbose Logging

> **Always revert after diagnosis.** Debug logging is very noisy and will fill pod memory buffers quickly in production.

### Option A — Uvicorn HTTP debug (request-level detail)

Patches the app Deployment to pass `--log-level debug` to uvicorn. This logs every HTTP request/response, including headers. Does **not** change Python app-level log verbosity.

```bash
# Enable
kubectl set env deployment/helvex \
  UVICORN_LOG_LEVEL=debug \
  -n helvex-prod

# The Deployment will roll out a new pod automatically.
# Tail to see debug output:
kubectl logs -n helvex-prod -l app.kubernetes.io/component=app -f

# Revert
kubectl set env deployment/helvex \
  UVICORN_LOG_LEVEL- \
  -n helvex-prod
```

> `UVICORN_LOG_LEVEL` is read by uvicorn automatically from the environment — no code change needed.

### Option B — Python app-level DEBUG logging

The app hardcodes `logging.INFO` in `app/main.py`. To temporarily drop to DEBUG, patch the Deployment to override the startup command:

```bash
# Enable (overrides the Dockerfile CMD with --log-level debug added)
kubectl patch deployment helvex -n helvex-prod --type=json -p='[
  {"op":"replace","path":"/spec/template/spec/containers/0/args",
   "value":["uvicorn","app.main:app","--host","0.0.0.0","--port","8000","--log-level","debug"]}
]'

# Tail
kubectl logs -n helvex-prod -l app.kubernetes.io/component=app -f

# Revert — remove the args override so the Dockerfile CMD takes over again
kubectl patch deployment helvex -n helvex-prod --type=json -p='[
  {"op":"remove","path":"/spec/template/spec/containers/0/args"}
]'
```

For **persistent** debug support without patching, add `LOG_LEVEL` to `app/config.py` and read it in `app/main.py`:
```python
# app/main.py — replace the hardcoded INFO with:
logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO), ...)
# app/config.py — add:
log_level: str = "INFO"
```
Then set `LOG_LEVEL=DEBUG` in `helvex-env` secret for a targeted pod restart.

### Option C — SQLAlchemy query logging

To see every SQL query the app executes (very verbose — use only for a specific investigation):

```bash
# Open a shell in the app pod
kubectl exec -n helvex-prod -it deploy/helvex -- bash

# From inside the pod — start a Python REPL against the live DB
python3 - <<'EOF'
import logging
logging.getLogger("sqlalchemy.engine").setLevel(logging.INFO)
# Now import and run whatever you need to trace
EOF
```

This only affects the current shell session. The running uvicorn process is not affected — restart the pod to reset.

### Option D — Worker debug logging

Same options apply to the worker Deployment (`deployment/helvex-worker`):

```bash
kubectl set env deployment/helvex-worker UVICORN_LOG_LEVEL=debug -n helvex-prod
# Revert:
kubectl set env deployment/helvex-worker UVICORN_LOG_LEVEL- -n helvex-prod
```
