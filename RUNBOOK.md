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
9b. [Deploy: Selective Build/Deploy Modes (Backend, Frontend, ML)](#9b-deploy-selective-builddeploy-modes-backend-frontend-ml)
10. [Useful kubectl Commands](#10-useful-kubectl-commands)
11. [Logs: Where to Find Them](#11-logs-where-to-find-them)
11b. [Logs: ML Worker on Home Node](#11b-logs-ml-worker-on-home-node)
12. [Debug: Temporarily Enable Verbose Logging](#12-debug-temporarily-enable-verbose-logging)
12b. [Logging: App Loggers Not Emitting](#12b-logging-app-loggers-not-emitting-to-stdout)
13. [Deploy: Node Disk Full Quick Cleanup](#13-deploy-node-disk-full-quick-cleanup)
14. [Node: High CPU Load / k3s API Unresponsive](#14-node-high-cpu-load--k3s-api-unresponsive)
15. [Monetization Ops Checks (Phase 4 and Phase 5)](#15-monetization-ops-checks-phase-4-and-phase-5)
16. [ML Pipeline: Clustering, Keywords, and NOGA](#16-ml-pipeline-clustering-keywords-and-noga)
17. [Home ML Node Rollout (Phases A-C)](#17-home-ml-node-rollout-phases-a-c)
18. [Email Notifications: Low-Credit Alert](#18-email-notifications-low-credit-alert)
19. [Saved View Alerts](#19-saved-view-alerts)
20. [Admin Analytics Dashboard](#20-admin-analytics-dashboard)
21. [Email Notification Opt-Out (per org)](#21-email-notification-opt-out-per-org)
22. [Boilerplate & Stopword Maintenance](#22-boilerplate--stopword-maintenance)
23. [New Company Classification (Incremental)](#23-new-company-classification-incremental)
24. [Semantic Search Tuning](#24-semantic-search-tuning)
25. [Keeping K3s and the Servers Up to Date](#25-keeping-k3s-and-the-servers-up-to-date)
26. [Dev Tooling: CodeGraphContext MCP — Keeping the Code Graph in Sync](#26-dev-tooling-codegraphcontext-mcp--keeping-the-code-graph-in-sync)
27. [Dev Tooling: code-review-graph MCP — PR/Diff Review Assistant](#27-dev-tooling-code-review-graph-mcp--prdiff-review-assistant)

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
# For payments, WORLDLINE_API_BASE_URL (and WORLDLINE_CUSTOMER_ID / WORLDLINE_TERMINAL_ID) should also appear.
```

If they're missing, add them to the GitHub Actions secrets (`S3_ACCESS_KEY`, `S3_SECRET_KEY`) and re-run the deploy workflow to recreate the secret.

**4. Ensure restore source is set correctly**

The deploy workflow automatically resolves the restore source in priority order:
1. Manual `workflow_dispatch` input (`restore_source`) — overrides everything
2. Repo variable `POSTGRES_RESTORE_SOURCE`
3. S3 pointer file `s3://helvex-backups/pg-prod/restore-point.json` (persisted by cronjob)
4. Repo file `restore-point.json`
5. Existing ConfigMap
6. Default: `helvex-pg`

The restore source must match the **CNPG/Barman `serverName`** used when the backups were written. In this repo that is often timestamped (e.g., `helvex-pg-20260331T150000Z`).

**5. Set `restoreFromBackup: true` in `infra/environments/prod.yaml`**
```yaml
postgres:
  restoreFromBackup: true   # ← add this line
```

**6. Deploy the app**
```bash
git add infra/environments/prod.yaml
git commit -m "restore db from backup [deploy-prod]"
git push
```

CloudNativePG will:
- Use the resolved restore source server name
- Find the latest base backup under that server name in S3
- Restore it into a fresh PVC
- Replay all WAL segments up to the latest available
- Promote to primary

**7. Watch recovery progress**
```bash
kubectl get cluster helvex-pg -n helvex-prod -w
# Status moves: Restoring → Running

kubectl logs -n helvex-prod helvex-pg-1 -f
# Look for "database system is ready to accept connections"
```

**8. Flip `restoreFromBackup` back to `false` immediately**

Leaving it as `true` means the next `helmfile apply` will try to re-recover and conflict with the running cluster.

```yaml
postgres:
  restoreFromBackup: false   # ← revert
```

```bash
git add infra/environments/prod.yaml
git commit -m "restore complete — reset restoreFromBackup [deploy-prod]"
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

## 9b. Deploy: Selective Build/Deploy Modes (Backend, Frontend, ML)

Use selective modes/tags to avoid rebuilding or rolling out unchanged components.

### Supported prod commit tags

- `[deploy-prod]` → full deploy path (infra/bootstrap + app)
- `[deploy-all]` → backend + frontend + ML image rollout only (no infra/bootstrap)
- `[deploy-app]` → backend + frontend image rollout only
- `[deploy-frontend]` → frontend deployment only
- `[deploy-backend]` → backend app deployment only
- `[deploy-ml]` → ML worker deployment only

### Supported prod workflow-dispatch modes

- `prod`
- `all`
- `app`
- `frontend`
- `backend`
- `ml`

### Image types and Dockerfiles

There are four distinct images, each with its own Dockerfile:

| Image | Dockerfile | GHCR tag | What's in it |
|---|---|---|---|
| Backend | `Dockerfile` | `ghcr.io/<repo>:<sha>` | Python deps, app code. spaCy + geocoding DB excluded. Fast build (~10 min). |
| ML base | `Dockerfile.ml-base` | `ghcr.io/<repo>-ml-base:latest` | Python deps + spaCy model + 143 MB geocoding DB. **Rebuilt only when `requirements.txt` or `geocoding_client.py` change.** GHA cache hit = near-instant. |
| ML worker | `Dockerfile.ml` | `ghcr.io/<repo>-ml:<sha>` | Inherits from `ml-base`, adds app code. Fast build (~5 min). |
| Frontend | `frontend/Dockerfile` | `ghcr.io/<repo>-frontend:<sha>` | Node 22 Alpine, Next.js standalone output. |

The `ml-base` / `ml-worker` split is the key optimization: the heavy geocoding DB download and QEMU arm64 emulation only happen when the base layer is actually invalidated, not on every code push.

### Build pipeline (prod, parallel jobs)

```
push [deploy-*]
       │
       ├── build-backend ──────────────────────────────┐
       │                                               │
       ├── build-ml-base ──► build-ml ─────────────────┤
       │                                               │
       └── build-frontend ─────────────────────────────┘
                                                       │
                                                   deploy
```

All three build tracks run in parallel. `build-ml` is a child of `build-ml-base` (it needs the base image published first). Total wall-clock time on a code-only push: ~12 min vs ~90 min before.

### Dev deploy behavior

`[deploy-dev]` uses changed-path detection and only builds changed areas (backend/frontend/ml) in parallel. Dev ml builds reuse `ml-base:latest` from GHCR — no heavy rebuild.

Dev Helm deploy uses stable tags (`dev`) for all three images so unchanged components are not forced to a new SHA tag.

### Optional QEMU / multi-arch controls

By default, builds run amd64-only. That keeps backend and frontend images fast.

Where to set the flags:
- For manual prod deploys, open GitHub Actions, run [Deploy Prod](.github/workflows/deploy-prod.yml), and set the `qemu_*` inputs in the dispatch form.
- For push-based dev deploys, set the repo variables in GitHub Actions settings: `BUILD_QEMU_BACKEND`, `BUILD_QEMU_ML_BASE`, `BUILD_QEMU_ML`, and `BUILD_QEMU_FRONTEND`.

Enable QEMU only for the image you actually need to publish as multi-arch:

Prod workflow dispatch inputs:
- `qemu_backend`
- `qemu_ml_base`
- `qemu_ml`
- `qemu_frontend`

Dev / push-based deploy repo variables:
- `BUILD_QEMU_BACKEND`
- `BUILD_QEMU_ML_BASE`
- `BUILD_QEMU_ML`
- `BUILD_QEMU_FRONTEND`

Notes:
- The ML base and ML worker images share the same architecture setting in practice. If you want arm64 ML images, enable both `qemu_ml_base` and `qemu_ml` together, or set both dev variables to `true`.
- Leaving all of them off gives you the fastest possible amd64-only builds.
- QEMU is only worth it when you actually need arm64 images for that release.

### Quick examples

Frontend-only production rollout:
```bash
git commit --allow-empty -m "frontend hotfix [deploy-frontend]"
git push
```

ML-only production rollout:
```bash
git commit --allow-empty -m "tune clustering params [deploy-ml]"
git push
```

Backend-only production rollout:
```bash
git commit --allow-empty -m "api bugfix [deploy-backend]"
git push
```

### If a selective rollout appears stale

1. Confirm expected image tag is deployed:
```bash
kubectl get deploy helvex -n helvex-prod -o jsonpath='{.spec.template.spec.containers[0].image}'
kubectl get deploy helvex-frontend -n helvex-prod -o jsonpath='{.spec.template.spec.containers[0].image}'
kubectl get deploy helvex-ml-worker -n helvex-prod -o jsonpath='{.spec.template.spec.containers[0].image}'
```

2. Confirm rollout status for the targeted deployment:
```bash
kubectl rollout status deployment/helvex -n helvex-prod --timeout=360s
kubectl rollout status deployment/helvex-frontend -n helvex-prod --timeout=180s
kubectl rollout status deployment/helvex-ml-worker -n helvex-prod --timeout=360s
```

3. If needed, force restart the specific deployment only:
```bash
kubectl rollout restart deployment/helvex -n helvex-prod
kubectl rollout restart deployment/helvex-frontend -n helvex-prod
kubectl rollout restart deployment/helvex-ml-worker -n helvex-prod
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

# Connect to Redis directly
kubectl exec -it -n helvex-prod helvex-redis-0 -- redis-cli
#then in the interactive enter the following: AUTH default [password-here]

# Conenct to Redis one-line
redis-cli -h helvex-redis-master.helvex-prod.svc.cluster.local -p 6379 -a "password-here"


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
ssh -t -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -L 5432:localhost:5432 ubuntu@195.201.219.240 "sudo kubectl -n helvex-prod port-forward --address 127.0.0.1 svc/helvex-pg-rw 5432:5432"
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

# Scale pods
kubectl scale deployment/helvex -n helvex-prod --replicas=2
kubectl scale deployment/helvex -n helvex-prod --replicas=1

```

## 10b. TLS Troubleshooting: Let's Encrypt Rate Limit (429)

Symptom in browser:
- `Dies ist keine sichere Verbindung`
- `net::ERR_CERT_AUTHORITY_INVALID`

Common cluster signs:
- `kubectl describe certificate helvex-tls -n helvex-prod` shows `Failed to create Order: 429`
- Message includes `too many certificates (5) already issued for this exact set of identifiers in the last 168h`
- Ingress serves Traefik default self-signed cert until issuance succeeds

This is not usually a cert-manager or Ingress misconfiguration. It is a Let's Encrypt policy limit for the exact same host set.

### What to do

1. Stop forcing re-issues until the `retry after ... UTC` timestamp shown in the cert-manager error.
2. At/after that timestamp, trigger a single clean retry:

```bash
kubectl delete certificaterequest -n helvex-prod --all
kubectl delete order -n helvex-prod --all
kubectl delete challenge -n helvex-prod --all
kubectl delete certificate helvex-tls -n helvex-prod --ignore-not-found
kubectl delete secret helvex-tls -n helvex-prod --ignore-not-found
kubectl annotate ingress helvex -n helvex-prod cert-manager.io/cluster-issuer=letsencrypt-prod --overwrite

kubectl get certificate -n helvex-prod -w
kubectl describe certificate helvex-tls -n helvex-prod
```

### Notes

- Re-running Helm or deleting/recreating resources before the retry timestamp will not bypass the ACME limit.
- If valid TLS is needed immediately, use an alternative certificate source temporarily (for example a manually provisioned cert from another CA).

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

## 11b. Logs: ML Worker on Home Node

If `kubectl logs` fails only for `helvex-ml-worker-*` pods while other pods work, the control-plane usually cannot reach the home node kubelet (`10250/tcp`).

### Fast path: read logs directly on the home node

From the control-plane server:

```bash
# 1) Find current ml-worker pod name
kubectl get pods -n helvex-prod -l app.kubernetes.io/component=ml-worker -o wide

# 2) SSH to the home node (replace host if needed)
ssh ubuntu@ubuntuserverhome

# 3) On home node: find container and tail logs via CRI
sudo crictl ps --name ml-worker
sudo crictl logs -f <CONTAINER_ID>

# Optional fallback if crictl output is empty
sudo journalctl -u k3s-agent -f
```

### Make `kubectl logs` work from control-plane (recommended fix)

Run these checks in order:

```bash
# 1) Confirm ml-worker pod is on the home node
kubectl get pod -n helvex-prod -l app.kubernetes.io/component=ml-worker -o wide

# 2) Get the node InternalIP used by the API server for kubelet calls
kubectl get node ubuntuserverhome -o jsonpath='{.status.addresses[?(@.type=="InternalIP")].address}'
echo

# 3) From control-plane, verify kubelet port reachability
nc -vz <HOME_NODE_INTERNAL_IP> 10250
```

If step 3 fails, fix networking/firewall/NAT so control-plane can reach home node `10250/tcp`.

Then ensure the home node advertises a routable address to the cluster:

```bash
# On home node
sudo grep -E 'node-ip|node-external-ip' /etc/rancher/k3s/config.yaml

# If missing/wrong, set node-ip to an address reachable from control-plane,
# then restart the agent:
sudo systemctl restart k3s-agent
```

Re-check:

```bash
kubectl get node ubuntuserverhome -o wide
kubectl logs -n helvex-prod -l app.kubernetes.io/component=ml-worker --tail=200
```

Notes:
- `kubectl logs` is proxied API server -> kubelet on the node hosting the pod.
- If only home-node pods fail, this is almost always reachability to that node's kubelet, not an app logging issue.

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

---

## 12b. Logging: App Loggers Not Emitting to Stdout

**Symptom:** Uvicorn startup logs visible, HTTP access logs visible via `print()` fallbacks, but app-level `logger.info()` calls (e.g. from payments, billing services) produce no output.

**Root cause:** Alembic's `fileConfig()` call during migrations defaults to `disable_existing_loggers=True`, which sets `logger.disabled = True` on all loggers not listed in `alembic.ini`. This persists after migrations complete, silencing all `app.*` loggers even if handlers are attached.

**Fix:** In `alembic/env.py`, line 14, add `disable_existing_loggers=False`:

```python
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)
```

**Why:** This prevents `fileConfig` from disabling loggers it doesn't know about (i.e. `app`, `app.services.payments`, etc.), even though those loggers aren't defined in `alembic.ini`. With this flag, the `app` logger can attach its own handler and emit normally.

**Verification:**
```bash
# After redeploy, check that app-level logs appear:
kubectl logs -n helvex-prod -l app.kubernetes.io/component=app | grep -E "^202[0-9]-.*INFO.*app\."
# Should see entries like: "2026-04-02 14:30:15,123 INFO app.services.payments - ..."
```

---

## 13. Deploy: Node Disk Full Quick Cleanup

Use this when a large job or rollout filled the node and new pods cannot start (ImagePullBackOff, ErrImagePull, Evicted, or node DiskPressure).

### 1) Confirm disk pressure and biggest folders

```bash
# Node-level free space
df -h

# Biggest K3s/container/log paths (run on the node via SSH)
sudo du -xh --max-depth=1 /var/lib/rancher /var/lib/containerd /var/log 2>/dev/null | sort -h

# Kubernetes signal
kubectl describe node $(kubectl get nodes -o name | head -1) | grep -A5 -i "DiskPressure\|Allocated resources"
```

### 2) Fast safe cleanup (usually enough to unblock deploy)

```bash
# Remove completed and failed pods (frees writable layers)
kubectl delete pod -A --field-selector=status.phase==Succeeded
kubectl delete pod -A --field-selector=status.phase==Failed

# Remove Evicted pods
kubectl get pods -A | awk '$4=="Evicted" {print $1, $2}' | xargs -r -n2 sh -c 'kubectl delete pod -n "$0" "$1"'

# Shrink systemd journal logs
sudo journalctl --vacuum-time=3d

# Prune unused container images (K3s/containerd)
sudo k3s crictl rmi --prune
```

### 3) If still full, free space aggressively

```bash
# Truncate very large plain-text logs
sudo find /var/log -type f -name "*.log" -size +200M -exec truncate -s 0 {} \;

# Remove temporary Helm/Helmfile artifacts
rm -rf ~/.cache/helm ~/.cache/helmfile /tmp/helmfile*
```

### 4) Retry deploy and verify

```bash
df -h
kubectl get nodes
kubectl get pods -A | grep -E "ImagePullBackOff|ErrImagePull|Evicted|CrashLoopBackOff" || true
```

Notes:
- `k3s crictl rmi --prune` only removes images not used by running containers; images may be re-pulled on next deploy.
- If DiskPressure returns quickly, reduce job concurrency and avoid running large batch jobs during full image rollouts.

---

## 14. Node: High CPU Load / k3s API Unresponsive

**Symptoms:**
- `kubectl` commands hang or return `TLS handshake timeout` to `127.0.0.1:6443`
- k3s logs (`journalctl -u k3s`) full of `Slow SQL` messages
- Deploy stuck at `helmfile apply` with no progress after the "Comparing release=..." lines
- SSH still works but the cluster is unresponsive

**Why this happens:** k3s uses an embedded SQLite database for cluster state. When a pod crash-loops at high frequency (hundreds of restarts), each restart floods k3s with state writes (pod status, events, conditions). SQLite serialises all writes, so under CPU or I/O pressure the queue backs up — the API server stops responding to new connections even though the k3s process is still running.

### 1) Confirm it's CPU load

```bash
# On the node via SSH — check load average vs CPU count
top -bn1 | head -5
nproc  # number of CPUs; load average should be <= this

# Which processes are burning CPU
top -bn1 -o %CPU | head -20
```

### 2) Find the crash-looping pod

```bash
# Sort all pods by restart count — the top entry is usually the culprit
kubectl get pods -A --sort-by='.status.containerStatuses[0].restartCount' | tail -10

# Quick visual check — look for high RESTARTS column
kubectl get pods -A | grep -v "0   \|1   \|2   "
```

A restart count in the hundreds (e.g. `646`) with status `1/2` or `CrashLoopBackOff` is the root cause.

### 3) Stop the crash loop (even if kubectl is slow)

If kubectl is too slow, bypass it with `crictl` directly on the node:

```bash
# Find the container
sudo crictl ps | grep <pod-name-fragment>

# Stop it (use the CONTAINER ID from above)
sudo crictl stop <CONTAINER_ID>

# Or stop all containers matching a name
sudo crictl ps | grep prometheus | awk '{print $1}' | xargs -r sudo crictl stop
```

Once the crash loop stops, SQLite write pressure drops and kubectl becomes responsive within ~30–60 seconds.

### 4) Properly scale down the offending workload

Once kubectl responds:

```bash
# Example for Prometheus StatefulSet
kubectl scale statefulset prometheus-monitoring-kube-prometheus-prometheus \
  -n monitoring --replicas=0

# Verify it stopped
kubectl get pods -n monitoring
```

### 5) Investigate why the pod was crashing

```bash
# Check last termination reason and exit code
kubectl describe pod <pod-name> -n <namespace> | grep -A 10 "Last State"

# Common reasons:
# OOMKilled (exit 137) → increase memory limit
# Error (exit 1/2)     → check logs from previous instance
kubectl logs <pod-name> -n <namespace> -p  # -p = previous container instance
```

### 6) Fix and re-enable

For **Prometheus OOMKill** (the most common trigger), increase its memory limit in `infra/charts/monitoring/values.yaml`:

```yaml
prometheus:
  prometheusSpec:
    resources:
      requests:
        memory: 512Mi
      limits:
        memory: 1Gi   # was 512Mi — OOMKill trigger
```

Then scale back up:

```bash
kubectl scale statefulset prometheus-monitoring-kube-prometheus-prometheus \
  -n monitoring --replicas=1
```

### 7) Retry the stuck deploy

Once the node is stable, re-trigger the deploy from GitHub Actions (re-run the failed workflow). The helmfile apply will resume cleanly.

---

## 15. Monetization Ops Checks (Phase 4 and Phase 5)

Use this section after deploying queue-priority routing and credit enforcement.

### A) Queue Priority Routing (Phase 4)

1) Confirm worker pods are up:

```bash
kubectl get pods -n helvex-prod -l app.kubernetes.io/component=api-worker
kubectl get pods -n helvex-prod -l app.kubernetes.io/component=zefix-worker
kubectl get pods -n helvex-prod -l app.kubernetes.io/component=ml-worker
```

2) Confirm startup log shows p4..p0 queue list (highest first):

```bash
kubectl logs -n helvex-prod -l app.kubernetes.io/component=api-worker --tail=100 | grep "Starting RQ"
kubectl logs -n helvex-prod -l app.kubernetes.io/component=zefix-worker --tail=100 | grep "Starting RQ"
```

Expected queues:
- API worker: helvex-api-p4, helvex-api-p3, helvex-api-p2, helvex-api-p1, helvex-api-p0
- Zefix worker: helvex-zefix-p4, helvex-zefix-p3, helvex-zefix-p2, helvex-zefix-p1, helvex-zefix-p0
- ML worker: helvex-ml

3) Trigger one low-tier org job and one high-tier org job of the same type.

4) Check Redis queue depth directly:

```bash
kubectl exec -n helvex-prod -it statefulset/helvex-redis -- sh -lc '
  for q in helvex-api-p4 helvex-api-p3 helvex-api-p2 helvex-api-p1 helvex-api-p0; do
    n=$(redis-cli -a "$REDIS_PASSWORD" LLEN rq:queue:$q)
    echo "$q: $n"
  done
'
```

5) Verify high-priority queue drains first:

```bash
kubectl logs -n helvex-prod -l app.kubernetes.io/component=api-worker -f
```

When both p4 and p0 have jobs, p4 should be processed before p0 by each free worker.

### B) Credit Deductions and Ledger (Phase 5)

1) Check org balance before enqueue:

```sql
SELECT id, name, tier, credits_balance, monthly_rescore_used
FROM organizations
WHERE id = <org_id>;
```

2) Enqueue a billable job for that org (example: Claude classify).

3) Verify balance decreased (or entitlement path used):

```sql
SELECT id, tier, credits_balance, monthly_rescore_used
FROM organizations
WHERE id = <org_id>;
```

4) Verify ledger row exists:

```sql
SELECT id, org_id, amount, type, action_type, reference_id, credits_before, credits_after, created_at
FROM org_credit_transactions
WHERE org_id = <org_id>
ORDER BY created_at DESC
LIMIT 20;
```

Expected:
- Deductions use type='deduction' and negative amount.
- Simple tier first flex rescore can produce amount=0 with monthly_rescore_used=true.
- Explorer and above flex rescore should not consume credits.

5) Insufficient-balance behavior:

- Enqueue returns HTTP 400 with insufficient-credits error text.
- No new job row should be created for that request.
- No deduction row should be written.

6) Superadmin bypass behavior:

- Jobs enqueued by a superadmin should bypass credit checks.
- No deduction row is required for bypassed jobs.

---

## 16. ML Pipeline: Clustering, Keywords, and NOGA

Three complementary ML pipelines enrich company data. They share the same text preprocessing stack.

### Correct execution order

```
recompute_keywords       ← extract purpose_keywords from raw purpose text
       ↓
tfidf_kmeans_cluster     ← TF-IDF K-Means (n_clusters=50, use_keywords=True)
       ↓ (auto-triggers)
discover_stopwords       ← 4-phase boilerplate/stopword discovery
       ↓
reclassify_noga          ← needs keywords + cluster labels for best accuracy
       ↓
claude_classify          ← AI scoring (needs org API key)
       ↓
recalculate_scores       ← recompute combined_score (new formula: AI×0.60 + NOGA×0.25 + keywords×0.15)
```

NOGA uses `purpose_keywords` and `tfidf_cluster` as input signals. Running it before clustering degrades accuracy.

---

### Job overview

| Job type | What it does | Runtime (700K) | S3 artifacts |
|---|---|---|---|
| `recompute_keywords` | **Fit new TF-IDF** on full corpus + extract keywords. No S3 needed. Uploads new vectorizer. | ~20 min | Writes vectorizer |
| `reextract_keywords` | Extract keywords using **frozen S3 vectorizer** (no refit). Consistent IDF with existing corpus. | ~3–5 min | Reads + requires vectorizer |
| `tfidf_kmeans_cluster` | TF-IDF K-Means clustering, multi-label soft assignment. 50 clusters (default). | ~25 min | Writes vectorizer + SVD + centroids |
| `discover_stopwords` | 4-phase boilerplate/stopword discovery (IDF analysis, sentence dedup, cross-cluster staging, optional Claude review). Auto-triggered after each clustering run. | ~5 min + optional Claude call | — |
| `reclassify_noga` | NOGA taxonomy + 2-stage embedding classification (section vote → within-section re-rank). Confidence threshold ≥ 0.50. | ~10–30 min | Reads NOGA embeddings |
| `cluster_analysis` | Write cross-cluster stopword candidates to file | ~1 min | — |

---

### Step-by-step: Initial Full Pipeline

**Step 1 — Extract keywords**
```bash
POST /api/v1/scoring/recompute-keywords
Body: {}
```
Writes `purpose_keywords` (comma-separated domain terms) for every company with a `purpose` text. Takes ~20 min for 700K.

**Step 2 — Cluster (TF-IDF K-Means)**
```bash
POST /api/v1/jobs/enqueue/tfidf-cluster
Body: {
  "n_clusters": 50,
  "use_keywords": true
}
```
`use_keywords=true` (default) uses the extracted keywords from Step 1 instead of raw purpose text — cleaner because boilerplate is already stripped. Takes ~25 min for 700K. After completion, `discover_stopwords` is automatically enqueued to mine the fitted vectorizer for boilerplate candidates.

After clustering, check the cluster_analysis output (`app/static/cluster_analysis.txt`) for additional stopword candidates.

**Step 3 — NOGA classification**
```bash
POST /api/v1/jobs/enqueue/reclassify-noga
Body: {}
```
Maps each company to Swiss NOGA industry code using a 2-stage embedding approach: first votes on the NOGA section letter (A–U) from top-K candidates, then re-ranks within the winning section. Confidence threshold ≥ 0.50 — companies below threshold get no NOGA code rather than a low-quality assignment. Takes ~10–30 min depending on whether S3 NOGA embeddings are built (see §4b-NOGA below).

---

### TF-IDF K-Means (`tfidf_kmeans_cluster`)

**Properties:**
- Default `n_clusters=50` — targeted at a clean, non-fragmented cluster set for the Swiss corpus
- Soft multi-label: each company gets 1–3 clusters via cosine similarity threshold
- All companies assigned (except those filtered by `min_cluster_specificity`)
- After labeling, near-duplicate cluster labels (cosine similarity > 0.88) are merged
- Uploads S3 artifacts → incremental assignment for new companies works automatically

**Tuning `n_clusters`:**
- Too few (< 30): Over-broad clusters
- Too many (> 80): Fragmented near-duplicate clusters; UI clutter

---

### Step-by-step: Ongoing Updates

**After large import batches (100K+ new companies):**
```
recompute_keywords        (all companies, ~20 min)
tfidf_kmeans_cluster      (use_keywords=True, ~25 min) → triggers discover_stopwords
reclassify_noga           (all companies)
```

**Incremental (daily SHAB import, small batches):**
No action needed. The `initial` detail-fetch job automatically:
1. Extracts keywords for new companies using S3-cached vectorizer
2. Assigns the nearest cluster using S3-cached centroids
3. Detects `purpose_language` (DE/FR/IT/EN) via lingua/langdetect

Only re-run full pipeline when S3 artifacts are stale (corpus has shifted significantly).

**After changing stopwords / boilerplate patterns:**
```
recompute_keywords    ← re-extract with new stopwords applied
tfidf_kmeans_cluster  ← re-cluster with cleaner text
```

---

### Improving Cluster Quality

**1. Run cross-cluster analysis** (happens automatically after each cluster job)
```bash
POST /api/v1/jobs/enqueue/cluster-analysis
```
Output: `app/static/cluster_analysis.txt` — terms appearing across many cluster labels.

**2. Automated boilerplate discovery** (auto-triggered after every clustering run)
The `discover_stopwords` job runs 4 phases:
- **Phase 1 (IDF analysis):** Terms with IDF < 0.92 (appearing in >40% of docs) staged as stopword candidates in `tfidf_stopwords` with `enabled=False`.
- **Phase 2 (sentence dedup):** Sentences appearing in >300 companies are staged as boilerplate patterns (`enabled=False`). Works across all languages.
- **Phase 3 (cross-cluster staging):** Terms in labels of >60% of clusters are staged.
- **Phase 4 (optional Claude review):** `POST /api/v1/jobs/enqueue/discover-stopwords` with `{"use_ai": true}` sends top candidates to Claude Haiku, which classifies them per language and auto-promotes `always_boilerplate` ones immediately.

Approved patterns (`enabled=True`) take effect on the next clustering run automatically via `_strip_purpose_boilerplate()`.

**3. Add to DB stopwords manually** (Admin → Settings → TF-IDF Stopwords):
```
POST /api/v1/settings
Body: {"key": "tfidf_stopwords", "value": "beratung\nhandel\ndienstleistung"}
```

**4. Re-run** `recompute_keywords` + `tfidf_kmeans_cluster` to apply changes.

**Common symptoms and fixes:**

| Symptom | Cause | Fix |
|---|---|---|
| All clusters labelled "verwaltung gesellschaft holding" | Boilerplate not stripped | Run `discover_stopwords --use_ai`; add patterns manually |
| Many similar-sounding duplicate clusters | `n_clusters` too high | Reduce below 50 |
| One giant cluster containing everything | `n_clusters` too low | Increase; or increase TF-IDF `min_df` |
| `purpose_keywords` contains "bezweckt", "insbesondere" | Missing stopwords | Run cluster_analysis, add candidates |

---

### Cluster Registry Management

The cluster registry stores stable canonical names across pipeline runs. Renaming a cluster in the registry immediately updates `tfidf_cluster` on all company rows.

**UI:** Superadmin → Clusters (`/app/admin/clusters`)
- Table of all clusters (active + inactive) with live company counts
- Hover any row → pencil icon → inline rename
- `PATCH /api/v1/clusters/registry/{id}` with `{"new_name": "..."}`
- Inactive entries = clusters not produced in the latest pipeline run; still referenced in historical company data

**Rename via API:**
```bash
curl -X PATCH https://helvex.dicy.ch/api/v1/clusters/registry/42 \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"new_name": "Software · Cloud · API"}'
```

**Note:** Rename updates company rows with a `LIKE %old_name%` query. For very large datasets (>100K matching companies) this may take a few seconds. Always rename in the UI or via API, never directly in the database.

---

### 4b-NOGA: NOGA Embeddings Setup

NOGA classification uses a hybrid token + embedding approach. The embedding artifacts must be built once:

```bash
# Build NOGA embeddings (one-time, uploads to S3)
python scripts/build_noga_embeddings.py
```

Without embeddings, classification falls back to token-only matching (works but lower accuracy). Check S3 for `models/noga_embeddings.npy` to confirm they exist.

---

### What each job does internally

#### `recompute_keywords` vs `reextract_keywords`

Two jobs write `purpose_keywords`. They are fundamentally different:

| | `recompute_keywords` | `reextract_keywords` |
|---|---|---|
| TF-IDF model | **Fits a new model** on current corpus | **Loads frozen model** from S3 |
| IDF weights | Fresh — computed from today's companies | Frozen from last clustering run |
| spaCy lemmatization | Yes | No |
| S3 required | No (uploads a new vectorizer afterwards) | Yes — aborts if missing |
| Speed (700K) | ~20 min | ~3–5 min |
| Keyword consistency | May shift after large imports | Guaranteed same scale as existing companies |

**The key difference — IDF weights:**

TF-IDF IDF scores are corpus-relative: a term's importance is measured against all other documents in the corpus. If you fit a new model on only 500 new companies, a term rare in those 500 but common in the full 700K gets an artificially high score. `reextract_keywords` avoids this by using the same IDF weights as the existing corpus — new companies' keywords are directly comparable and searchable alongside existing ones.

**When to use each:**

- **`recompute_keywords`** — after large imports (50K+) that introduce new industries; after stopword/boilerplate changes; to reset the model entirely. Uploads a fresh S3 vectorizer so subsequent incremental extraction uses up-to-date weights.
- **`reextract_keywords`** — after small batches of new companies; when you want fast keyword refresh without disrupting the existing model. Also what runs automatically during the `initial` detail-fetch job via `extract_keywords_incremental()`.

**Steps for each:**

`recompute_keywords`:
1. Load all companies with `purpose`
2. Strip boilerplate (DB patterns)
3. Lemmatize with spaCy `de_core_news_md`
4. Fit new TF-IDF vectorizer on full corpus
5. Extract top-10 per-company terms with bigram deduplication
6. Write `purpose_keywords` to DB
7. Upload new TF-IDF vectorizer to S3

`reextract_keywords`:
1. Load S3 TF-IDF vectorizer (aborts if missing)
2. Strip boilerplate
3. `vectorizer.transform([text])` — no fitting
4. Extract top-10 terms with same bigram deduplication
5. Write `purpose_keywords` to DB

#### `tfidf_kmeans_cluster`

1. Load companies + preprocess (same as keywords)
2. TF-IDF + SVD (50 components) + L2 normalise
3. MiniBatchKMeans (default 50 clusters)
4. c-TF-IDF cluster labeling with bigram deduplication
5. Merge near-duplicate cluster labels (cosine similarity > 0.88)
6. Quality filter: suppress clusters with low mean IDF of top terms
7. Cluster registry: match labels to canonical names (Jaccard similarity) for label stability across runs
8. Soft multi-label assignment: cosine similarity to each centroid, assign top-3 above threshold
9. Extract per-company keywords (unless `use_keywords=True`, which preserves existing keywords)
10. Write `tfidf_cluster` + `purpose_keywords` to DB
11. Upload artifacts to S3
12. Auto-enqueue `discover_stopwords` job

#### `reclassify_noga`

1. Load NOGA taxonomy from repo-resident JSON
2. Load S3 NOGA embeddings (float32, shape N_codes × 384)
3. For each company: embed `purpose_keywords` with `paraphrase-multilingual-MiniLM-L12-v2`
4. **Stage 1:** Score all NOGA codes, group top-K candidates by section letter (A–U), pick the section with most votes
5. **Stage 2:** Re-rank only the codes within the winning section by embedding cosine similarity
6. Skip classification if best confidence < 0.50 (avoids low-quality code assignment)
7. Walk NOGA hierarchy to build full path
8. Write `noga_code`, `noga_label`, `noga_level`, `noga_confidence`, `noga_path` to DB

---

## 17. Home ML Node Rollout (Phases A-C)

This chapter tracks dedicated ML capacity using Hetzner nodes on private networking only.

### Current operating mode

- K3s node traffic runs on Hetzner private subnet (`10.0.1.0/24`)
- ML workers run on nodes labeled `workload=ml`
- KEDA scales `ml-worker` pods based on queue depth

If you only see `app1` in `kubectl get nodes`, run:

```bash
cd infra/terraform/envs/prod
terraform apply

ssh ubuntu@<db1-public-ip>
sudo tail -n 200 /var/log/cloud-init-output.log
sudo journalctl -u k3s-agent -n 200 --no-pager
sudo systemctl restart k3s-agent

ssh ubuntu@<app1-public-ip>
kubectl get nodes -o wide
```

### Add a Hetzner ML node (two approaches)

Approach A: Terraform-managed (recommended for stable infra state)

1) Add ML node entry in `infra/terraform/envs/prod/terraform.tfvars`:

```hcl
ml_nodes = {
  ml1 = {
    server_type = "cpx31"
    role        = "k3s-worker"
    private_ip  = "10.0.1.21"
    node_labels = ["workload=ml", "location=cloud"]
    node_taints = ["workload=ml:NoSchedule"]
  }
}
```

2) Apply Terraform:

```bash
cd infra/terraform/envs/prod
terraform apply
```

3) Validate:

```bash
ssh ubuntu@<app1-public-ip>
kubectl get nodes -o wide
kubectl describe node <terraform-node-name> | grep -E "Taints|workload=|location="
```

Quick helper (one command):

```bash
bash scripts/toggle-terraform-ml-node.sh enable \
  --name helvex-ml-1 \
  --private-ip 10.0.1.21
```

ML node convention:
- Use `workload=ml` as the required label/taint pair for ML scheduling.
- Do not add a second taint such as `helvex.io/role=ml-worker:NoSchedule` unless the deployment also tolerates it.
- If an older node has that extra taint, remove it before expecting the ML worker to schedule there.

```bash
kubectl taint node helvex-prod-ml1 helvex.io/role=ml-worker:NoSchedule-
```

If `helvex-ml-worker` fails immediately with `exec /usr/local/bin/python: exec format error`, the image and node architectures do not match. Rebuild and push a multi-arch image, then make sure the ML node is running the architecture that image supports.

Windows PowerShell:

```powershell
.\scripts\toggle-terraform-ml-node.ps1 enable `
  -Name helvex-ml-1 `
  -Location nbg1 `
  -NodeType cax21 `
  -PrivateIp 10.0.1.21
```

Approach B: Script-managed (fast/manual)

1) On control-plane node `app1`, get the join token:

```bash
ssh ubuntu@<app1-public-ip>
sudo cat /var/lib/rancher/k3s/server/node-token
```

2) Run provisioning helper:

```bash
bash scripts/provision-hetzner-ml-node.sh \
  --name helvex-ml-1 \
  --type cpx31 \
  --image ubuntu-24.04 \
  --location nbg1 \
  --network helvex-prod-net \
  --private-ip 10.0.1.21 \
  --ssh-key-name helvex_prod_sshkey_v1 \
  --ssh-user ubuntu \
  --cp-host ubuntu@<app1-public-ip> \
  --cp-private-ip 10.0.1.10
```

Windows PowerShell:

```powershell
.\scripts\provision-hetzner-ml-node.ps1 `
  -Name helvex-ml-1 `
  -NodeType cax21 `
  -Image ubuntu-24.04 `
  -NodeLocation nbg1 `
  -Network helvex-prod-net `
  -PrivateIp 10.0.1.21 `
  -SshKeyName helvex_prod_sshkey_v1 `
  -SshUser ubuntu `
  -CpHost "ubuntu@<app1-public-ip>" `
  -CpPrivateIp 10.0.1.10
```

3) Validate:

```bash
kubectl get nodes -o wide
kubectl describe node helvex-ml-1 | grep -E "Taints|workload=|location="
```

4) Validate pod DNS on the ML node:

```bash
kubectl run dns-test --image=busybox:1.36 --restart=Never \
  --overrides='{"spec":{"nodeSelector":{"workload":"ml"},"tolerations":[{"key":"workload","operator":"Equal","value":"ml","effect":"NoSchedule"}]}}' \
  -- sleep 60
kubectl exec dns-test -- nslookup kubernetes.default.svc.cluster.local
kubectl delete pod dns-test
```

5) Rotate join token after onboarding:

```bash
sudo k3s token rotate
```

### Remove a Hetzner ML node

Terraform-managed node:

```bash
# Remove the node from ml_nodes in terraform.tfvars
cd infra/terraform/envs/prod
terraform apply
```

Quick helper (one command):

```bash
bash scripts/toggle-terraform-ml-node.sh disable --name helvex-ml-1
```

Windows PowerShell:

```powershell
.\scripts\toggle-terraform-ml-node.ps1 disable -Name helvex-ml-1
```

Script-managed node:

```bash
bash scripts/deprovision-hetzner-ml-node.sh \
  --name helvex-ml-1 \
  --cp-host ubuntu@<app1-public-ip>
```

Windows PowerShell:

```powershell
.\scripts\deprovision-hetzner-ml-node.ps1 `
  -Name helvex-ml-1 `
  -CpHost "ubuntu@<app1-public-ip>"
```

### Autoscaling policy

- Pod-level autoscaling: KEDA ScaledObject for `ml-worker` (0 -> N based on Redis queue)
- Node-level autoscaling: managed by Hetzner Cluster Autoscaler node group for ML nodes
- Scheduling guardrails:
  - Node selector: `workload=ml`
  - Toleration: `workload=ml:NoSchedule`

### Acceptance checks

- ML node is `Ready`
- Node has label `workload=ml`
- Node has taint `workload=ml:NoSchedule`
- `ml-worker` schedules only on ML nodes

---

## 18. Email Notifications: Low-Credit Alert

**Trigger:** Org credit balance drops below `low_credit_alert_at` (OrgSetting). Default: disabled (NULL). Sent at most once per day per org.

### Check whether alerts are enabled for an org

```sql
SELECT key, value FROM org_settings
WHERE org_id = <org_id> AND key IN ('low_credit_alert_at', 'low_credit_alert_sent_at', 'email_notifications');
```

| Key | Meaning |
|---|---|
| `low_credit_alert_at` | Threshold. NULL = disabled. |
| `low_credit_alert_sent_at` | ISO date of last alert. Prevents repeat same-day emails. |
| `email_notifications` | `"0"` = opted out. Default `"1"` (on). |

### Clear today's alert cooldown (re-send if balance still low)

```sql
DELETE FROM org_settings
WHERE org_id = <org_id> AND key = 'low_credit_alert_sent_at';
```

### Disable low-credit alerts for an org

```sql
DELETE FROM org_settings
WHERE org_id = <org_id> AND key = 'low_credit_alert_at';
```

### Check org admin email (who receives the alert)

```sql
SELECT u.email, u.username, om.role
FROM org_members om
JOIN users u ON u.id = om.user_id
WHERE om.org_id = <org_id> AND om.role IN ('admin', 'owner');
```

If no admin/owner has an email set, the alert is silently skipped. Check user rows and ensure at least one admin has `email` populated.

---

## 19. Saved View Alerts

**How it works:** The `saved_view_alerts` job sweeps all orgs. For each `UserView` with `alert_enabled=True`, it counts matching companies. If the count grew since last check, it emails the view owner.

### Trigger manually (e.g. to test or re-run a missed nightly run)

```bash
curl -X POST https://helvex.dicy.ch/api/v1/admin/jobs/saved-view-alerts \
  -H "Authorization: Bearer <superadmin-token>"
```

Or via the superadmin UI: Admin → Analytics → (future: Alerts tab).

### Check sweep results in worker logs

```bash
kubectl logs -n helvex-prod -l app.kubernetes.io/component=worker --tail=200 | grep -i "saved_view"
```

Expected log entries:
```
INFO:app.services.saved_view_alerts:saved_view_alerts: checked=12 alerted=2 errors=0
```

### Debug: why didn't a specific view alert?

1. Confirm `alert_enabled = true`:
```sql
SELECT id, name, alert_enabled, alert_last_count, alert_last_checked_at
FROM user_views WHERE id = <view_id>;
```

2. Check `email_notifications` for the view owner:
```sql
SELECT value FROM org_settings
WHERE org_id = (SELECT org_id FROM users WHERE id = (SELECT user_id FROM user_views WHERE id = <view_id>))
AND key = 'email_notifications';
```
If `"0"`, the org has opted out.

3. Check whether the user is active:
```sql
SELECT is_active, email FROM users WHERE id = (SELECT user_id FROM user_views WHERE id = <view_id>);
```

4. Check baseline: if `alert_last_count IS NULL`, the first sweep only sets the baseline — no email is sent. The next sweep will compare against that baseline.

### Reset the baseline (force-alert on next sweep even if count is same)

```sql
UPDATE user_views SET alert_last_count = NULL WHERE id = <view_id>;
```

---

## 20. Admin Analytics Dashboard

**Access:** `/app/admin/analytics` — superadmin only (enforced in backend by `_require_superadmin` dependency).

### What the MRR estimate means

`mrr_estimate_chf` is the sum of `tier_monthly_price_chf` for all orgs on paid tiers. It is:
- A **subscription revenue estimate only** — does not include credit top-ups or one-off charges
- Based on list prices, not actual invoiced amounts (discounts not reflected)
- Intended as a quick health check, not a financial report

### Check the numbers directly

```sql
-- Orgs by tier
SELECT tier, COUNT(*) FROM organizations GROUP BY tier ORDER BY COUNT(*) DESC;

-- New orgs in last 30 days
SELECT COUNT(*) FROM organizations WHERE created_at > NOW() - INTERVAL '30 days';

-- Active orgs (any job or credit activity in last 30 days)
SELECT COUNT(DISTINCT org_id) FROM job_runs WHERE created_at > NOW() - INTERVAL '30 days';

-- Top credit consumers
SELECT o.name, SUM(ABS(t.amount)) AS spent
FROM org_credit_transactions t
JOIN organizations o ON o.id = t.org_id
WHERE t.type = 'deduction' AND t.created_at > NOW() - INTERVAL '30 days'
GROUP BY o.name ORDER BY spent DESC LIMIT 10;

-- Job volume by type
SELECT job_type, COUNT(*) FROM job_runs
WHERE created_at > NOW() - INTERVAL '30 days'
GROUP BY job_type ORDER BY COUNT(*) DESC;
```

### Refresh

The dashboard has a **Refresh** button (top-right) that re-fetches via `GET /api/v1/admin/analytics`. Data is not cached — each load hits the DB.

---

## 21. Email Notification Opt-Out (per org)

Users can toggle `email_notifications` at `/app/billing` (Notifications section). Admins can also set it directly:

### Check current preference

```sql
SELECT value FROM org_settings WHERE org_id = <org_id> AND key = 'email_notifications';
-- '1' = enabled (default), '0' = opted out, NULL = not set (defaults to enabled)
```

### Force opt-in (override via DB)

```sql
INSERT INTO org_settings (org_id, key, value) VALUES (<org_id>, 'email_notifications', '1')
ON CONFLICT (org_id, key) DO UPDATE SET value = '1';
```

### Force opt-out (override via DB)

```sql
INSERT INTO org_settings (org_id, key, value) VALUES (<org_id>, 'email_notifications', '0')
ON CONFLICT (org_id, key) DO UPDATE SET value = '0';
```

This affects **all** transactional emails for the org: low-credit alerts, export-ready, job-failed, and saved-view alerts.

---

## 22. Boilerplate & Stopword Maintenance

Boilerplate and stopword discovery runs automatically via the `discover_stopwords` job (4-phase pipeline, auto-triggered after every `tfidf_kmeans_cluster` run). It populates the `boilerplate_patterns` and `tfidf_stopwords` DB tables with `enabled=False` for review, and auto-promotes high-confidence patterns.

### Review pending candidates

```sql
-- Boilerplate patterns staged but not yet approved
SELECT pattern, description, active FROM boilerplate_patterns
WHERE active = false
ORDER BY id DESC
LIMIT 50;

-- Staged TF-IDF stopwords not yet approved
SELECT value, description, active FROM tfidf_stopwords
WHERE active = false
ORDER BY id DESC
LIMIT 50;
```

To approve a staged pattern (enables it for the next pipeline run):
```sql
UPDATE boilerplate_patterns SET active = true WHERE id = <id>;
UPDATE tfidf_stopwords SET active = true WHERE id = <id>;
```

Or use the Admin UI: **Admin → Boilerplate Settings** / **Admin → Settings → TF-IDF Stopwords**.

### Trigger Claude-assisted review pass

```bash
POST /api/v1/jobs/enqueue/discover-stopwords
Body: {"use_ai": true}
```

This runs all 4 phases including a single Claude Haiku call. Claude groups candidates by language and classifies each as `always_boilerplate` / `sometimes_meaningful` / `keep_as_signal`. `always_boilerplate` ones are immediately activated (`enabled=True`). Requires `ANTHROPIC_API_KEY` configured in org settings.

### After adding new patterns: refresh keywords

```bash
POST /api/v1/scoring/recompute-keywords
# Then re-cluster:
POST /api/v1/jobs/enqueue/tfidf-cluster
```

---

## 23. New Company Classification (Incremental)

New companies added via Zefix daily SHAB import are classified automatically by `enrich_company()` in `collection.py`. This covers cluster assignment and language detection.

NOGA classification for new companies runs via the batch `reclassify_noga` job (not inline) because it depends on cluster labels being set first.

### Verify new companies are being classified

```sql
-- Companies added in the last 7 days without cluster assignment
SELECT count(*) FROM companies
WHERE created_at > now() - interval '7 days'
  AND purpose IS NOT NULL
  AND tfidf_cluster IS NULL;

-- Companies added in the last 7 days without NOGA
SELECT count(*) FROM companies
WHERE created_at > now() - interval '7 days'
  AND purpose IS NOT NULL
  AND noga_code IS NULL;
```

If many new companies lack clusters, S3 artifacts are likely missing (run a full clustering job first). If NOGA is missing, run `reclassify_noga`.

### Backfill all unclassified companies

Use this after upgrading the classification pipeline or after any batch import that bypassed `enrich_company()`:

```python
from app.database import SessionLocal
from app.services.incremental_classify import backfill_unclassified

db = SessionLocal()
stats = backfill_unclassified(
    db,
    batch_size=500,
    run_noga=True,
    run_clusters=True,
)
print(stats)
db.close()
```

Expected runtime: ~5–10 min per 50K companies. S3 artifacts must exist for cluster assignment.

### Fix "0 companies" in Explorer

**Clusters:**
1. Check logs: `kubectl logs -n helvex-prod deploy/helvex | grep "Junction table stale"`
2. If stale warning appears: run a full clustering job to repopulate `company_tfidf_clusters`
3. The fallback path (denormalized `tfidf_cluster` column) is active automatically while stale

**NOGA codes:**
1. Verify `noga_path` populated: `SELECT count(*) FROM companies WHERE noga_path IS NOT NULL;`
2. If 0: run `reclassify_noga` — NOGA hierarchy browsing requires `noga_path`
3. NOGA stats use path-based matching (`noga_path LIKE 'J|%'`); `noga_code` alone is insufficient for hierarchy aggregation

---

## 24. Semantic Search Tuning

The semantic search endpoint (`GET /api/v1/companies/semantic-search`) uses the shared multilingual embedding model to rank taxonomy entries by cosine similarity.

**Model:** `paraphrase-multilingual-mpnet-base-v2` (loaded lazily, cached in memory)
**Threshold:** Results with `similarity < 0.20` are filtered out

### If search results are poor

1. **Check model is loaded:** `kubectl logs -n helvex-prod deploy/helvex | grep "Loaded SentenceTransformer"` — model loads on first search request.
2. **Query language mismatch:** The model handles DE/FR/IT/EN natively. Very short queries (<3 chars) may produce low similarity scores — this is expected.
3. **Taxonomy stale:** The endpoint scores the in-memory taxonomy snapshot. If new clusters were added in the last 2 hours, they may not appear in search results until the 2-hour cache refreshes.

### Force taxonomy cache refresh

```bash
kubectl rollout restart -n helvex-prod deployment/helvex
```

---

## 25. Keeping K3s and the Servers Up to Date

See [roadmap.md](roadmap.md) for the review status of the security hardening this section documents.

### Why this can't just be a Terraform apply

K3s version is pinned (not "latest") in `infra/terraform/envs/prod/variables.tf` (`k3s_version`), but changing it and running `terraform apply` does **not** patch already-running nodes. Hetzner's `hcloud_server.user_data` (cloud-init) forces full server **replacement** on change. For the control-plane node that means cluster downtime; for `db1` it means **data loss** — `db_volume_size_gb = 0` in prod, so CloudNativePG's PostgreSQL volumes are local-path PVs on the node's root disk, not a separate Hetzner Volume that survives a recreate.

`k3s_version` in Terraform only governs newly-provisioned or replaced nodes. Upgrading nodes that are already running is always a manual, in-place step (below).

### Upgrading K3s on a running node

1. Check the current stable release: https://update.k3s.io/v1-release/channels/stable
2. SSH in as **root**, not `ubuntu` — the install script needs unrestricted root, and the `ubuntu` user's passwordless sudo is intentionally scoped to a short allowlist (see next section):
   ```bash
   ssh root@<node-public-ip>
   ```
3. Re-run the install script with the target version. It detects the existing install, replaces the binary, and restarts the `k3s`/`k3s-agent` systemd service automatically — idempotent, a few seconds of API server downtime, no data loss:
   ```bash
   curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION=v1.35.x+k3s1 sh -
   ```
4. Control-plane first: confirm with `kubectl get nodes -o wide` (new version shown) and `kubectl get pods -A` (everything recovers), then repeat the same command on each worker/agent node.
5. Once every node is confirmed upgraded, bump `k3s_version` in `infra/terraform/envs/prod/variables.tf` to match and commit. This documents the running state and is what *future* node replacements will install — it is not itself an upgrade trigger.
6. Renovate (see below) opens a PR against that same Terraform variable whenever a new K3s release ships — treat it as a reminder to run this procedure, not something to merge-and-forget; merging alone changes nothing running.

### Patching the underlying Ubuntu servers

The `ubuntu` admin user has a deliberately narrow passwordless-sudo allowlist (`/etc/sudoers.d/ubuntu`, provisioned by `control-plane.yaml.tpl`): `systemctl {restart,start,stop,status} k3s`, `journalctl -u k3s [-f]`, `apt-get update`, `apt-get upgrade -y`, `reboot`. That's enough for routine OS patching:

```bash
ssh ubuntu@<node-public-ip>
sudo apt-get update
sudo apt-get upgrade -y
# If the upgrade pulled a new kernel, reboot to apply it:
sudo reboot
```

Patch one node at a time; control-plane last if it's the same day as a K3s upgrade — rebooting it briefly drops `kubectl` access, while workers keep serving traffic via Traefik in the meantime. For anything beyond the allowlisted commands (`apt-get dist-upgrade`, package removal, root-owned config edits), use the root SSH path from step 2 above — the same SSH key authenticates as both `ubuntu` and `root`.

Both SSH paths are further restricted at the PAM layer (`/etc/security/access.conf`, wired into `/etc/pam.d/sshd`) to the `admin_cidrs` Terraform variable — connections from any other source IP are rejected before a password/key prompt, regardless of the Hetzner Cloud Firewall rules. If your admin IP changes, update `admin_cidrs` in `infra/terraform/envs/prod/variables.tf` and run `terraform plan` first to confirm it only touches the firewall/cloud-init resources, not a node recreate.

### Renovate-managed dependency updates

`renovate.json` (repo root) watches pip (`requirements*.txt`), npm (`frontend/`), Dockerfiles, GitHub Actions (SHA-pinned — Renovate updates both the digest and the version comment), Helmfile chart versions (cert-manager, CloudNativePG operator, ARC), plus two custom regex watchers for the Terraform-pinned K3s version and the CNPG-bundled PostgreSQL image tag.

- **Patch/digest updates** (including GitHub Actions digest bumps) auto-merge once CI is green — low risk, no manual step.
- **Minor/major updates** open a PR but never auto-merge — review the changelog, especially for `cloudnative-pg`/CNPG operator and `cert-manager` (both manage stateful/TLS-critical infra), before merging and running `helmfile -e prod apply`.
- **K3s, the CNPG Postgres image, Helm, and Helmfile** never auto-merge even on patch — merging the PR only updates the *pinned version string*; you still need to run the manual upgrade procedure above (K3s) or `helmfile apply` (Helm chart versions). Merging alone changes nothing running.
- Renovate runs before 6am on weekdays (Europe/Zurich). Check PRs weekly; an accumulating backlog of major-version PRs is a signal to schedule a maintenance window, not something to ignore.

---

## 26. Dev Tooling: codegraph MCP — Keeping the Code Graph in Sync

[codegraph](https://github.com/colbymchenry/codegraph) indexes this repo's source into a local SQLite knowledge graph (`.codegraph/`, git-ignored), exposing a single `codegraph_explore` MCP tool that returns verbatim source + call graph + blast-radius impact in one call. It replaced **CodeGraphContext/KuzuDB** (used previously) specifically because KuzuDB took an exclusive OS-level lock that made a second consumer (another IDE window, a standalone visualizer) impossible — see the git history of this section if you need the old tool's gotchas for reference.

**Config:**
- `.mcp.json` (repo root — **note the leading dot**; this is the standard Claude Code project MCP config file. A prior mistake put the config in a non-dotted `mcp.json`, which is silently never read — if codegraph tools ever stop showing up after an edit, check you edited the right file).
- `codegraph.json` (repo root, optional) — custom file-extension mappings and **exclude patterns**. Currently excludes `.terraform/` explicitly (vendored Terraform provider binaries under `infra/terraform/envs/prod/.terraform/` aren't covered by any `.gitignore`). `.next/` and `node_modules/` are already covered by `frontend/.gitignore`, which codegraph respects, but are also listed explicitly for safety.
- Global CLI config/telemetry preference: `C:\Users\<you>\.codegraph\telemetry.json`.

### Concurrency — this is the actual reason we switched tools

`codegraph status` reports `Backend: node:sqlite - built-in (full WAL)`. **SQLite in WAL mode allows concurrent readers without blocking on a writer**, and codegraph runs a shared background daemon (local sockets) that multiple clients — two Claude Code windows, a terminal, the MCP server — can all connect to at once. This is a real architectural fix, not a workaround: unlike the old KuzuDB setup, you do **not** need to choose between "watch mode" and "query through the assistant" — both can run simultaneously without lock errors.

### Keeping the graph current

The background daemon watches files and incrementally syncs changes automatically (debounced — default ~2s, tune via `CODEGRAPH_WATCH_DEBOUNCE_MS`, clamped 100ms–60s). In normal use you don't need to do anything manually. If you want to force a sync (e.g. after a large external change, a branch switch, or before an important query):

```powershell
cd C:\D\coding_projects\zefix_analyzer
codegraph sync        # incremental — only files changed since last index
codegraph index        # full rebuild from scratch (same as a fresh `codegraph init`)
codegraph status        # check current stats + confirm "Index is up to date"
```

### Managing the daemon

```powershell
codegraph daemon        # interactive — lists running daemons, pick one + Enter to stop it
codegraph unlock .      # remove a stale lock file if indexing reports one blocking it
codegraph uninit .      # remove codegraph entirely from this project (deletes .codegraph/)
```

The daemon is spawned on demand by whichever command/MCP connection needs it first — there is no separate "start the daemon" step, and (unlike the old tool's in-memory watch state) it is not tied to a single IDE window's lifecycle. We have not separately verified it survives a full computer restart; if `codegraph status` ever reports a stale index after a reboot, run `codegraph sync` to catch it up.

### Manual review from a terminal (no MCP/assistant needed)

```powershell
codegraph explore "score_result web_enrichment"   # same output as the codegraph_explore MCP tool
codegraph callers score_result                    # who calls this symbol
codegraph callees score_result                    # what this symbol calls
codegraph impact score_result                     # blast-radius: what's affected by changing it
codegraph query score_result                      # plain symbol search
codegraph node score_result                       # one symbol's source + caller/callee trail
codegraph files                                   # project file structure from the index
```

### Known gotchas

1. **Config file must be `.mcp.json` (dotfile), not `mcp.json`.** We hit this directly during setup — a `codegraph` entry in the non-dotted file silently never connects, with no error surfaced anywhere.
2. **`.terraform/` needs an explicit exclude** in `codegraph.json` — it's the one directory in this repo not already covered by a `.gitignore` that codegraph would otherwise respect.
3. **Telemetry is on by default.** Disabled globally via `codegraph telemetry off` (confirm with `codegraph telemetry status`) and via `DO_NOT_TRACK=1` in the MCP server's env in `.mcp.json`. Per the project's own docs, no code/paths/names are collected even when enabled — see `TELEMETRY.md` in the repo if you want to verify that claim yourself.
4. **Two similarly-named tools can coexist and cause confusion.** This repo also has a separate `code-review-graph` MCP server (also in `.mcp.json`) for PR/diff-focused review workflows (risk scoring, blast-radius on git diffs, community detection) — a different tool for a different job, not a duplicate/alternative to `codegraph`. If a query returns unexpected results, check which server's tools you actually called (`mcp__codegraph__*` vs `mcp__code-review-graph__*`).

### Quick health check

```
1. `codegraph status` — files/nodes/edges should be non-zero and roughly match the real source file count; look for "[OK] Index is up to date"
2. Ask the assistant to call `codegraph_explore` (or run `codegraph explore` yourself) with a function you know is called somewhere (grep for it first) — it should return that real caller in its "Relationships" / blast-radius section, plus verbatim source.
```

---

## 27. Dev Tooling: code-review-graph MCP — PR/Diff Review Assistant

[code-review-graph](https://github.com/tirth8205/code-review-graph) is a **separate** tool from `codegraph` (section 26) — different job, not a duplicate. It also parses this repo with Tree-sitter into its own SQLite graph, but it's built for **PR/diff-focused review**: risk scoring, blast-radius on git diffs, community/flow detection, wiki generation. Reach for `codegraph_explore` to understand how code works; reach for `code-review-graph`'s tools when reviewing what a change affects.

**Install location — deliberately isolated:** `pip install code-review-graph` into the project's `.venv` pulled in newer `pydantic`/`uvicorn`/`watchdog` than `requirements.backend.txt` pins, which would have silently broken the running app's dependency versions (shared venv). It is instead installed in its own standalone venv at `C:\Users\<you>\.code-review-graph-venv`, never touching the app's `.venv`. If this is ever reinstalled or upgraded, **do not** `pip install` it into the project `.venv` — use the isolated venv (or `pipx`) again.

**PATH requirement:** `C:\Users\<you>\.code-review-graph-venv\Scripts` was added to the **user** PATH so the bare `code-review-graph` command resolves for the git hook and Claude Code hooks below. This only takes effect for *new* processes started after the change — an already-running terminal/IDE/Claude Code session won't see it until restarted. If the hooks below silently no-op (e.g. the SessionStart hook prints "Not a git repo, skipping" even inside the repo), that's the symptom: PATH hasn't refreshed for that process yet, not an actual git-detection failure.

**Config (project-scoped only — see "Known gotchas" for why):**
- `.mcp.json`, `.vscode/mcp.json`, `.qoder/mcp.json`, `.opencode.json` — MCP server registration, one per platform, all pointing at the isolated venv's Python.
- `.claude/settings.json` (repo root, checked in) — hooks, see below.
- `.gitignore` — `.code-review-graph/` (the graph DB) is excluded.

### Keeping the graph current — automatic, two mechanisms

Unlike `codegraph`'s background daemon, `code-review-graph` has **no persistent watcher by default**; it's kept in sync by hooks that fire on specific events:

1. **Claude Code hooks** (`.claude/settings.json`):
   - `PostToolUse` (matcher `Edit|Write|Bash`) → runs `code-review-graph update --skip-flows --repo <path>` after every edit/command, best-effort (`|| true`).
   - `SessionStart` → runs `code-review-graph status --repo <path>` so a fresh session reports current graph stats.
2. **Git pre-commit hook** (`.git/hooks/pre-commit`, local only — not versioned, so a fresh clone won't have it until `code-review-graph install` is re-run there) → runs `code-review-graph update` then `code-review-graph detect-changes --brief` before each commit, both best-effort/non-blocking (`|| true`) so it can never fail a commit.

In normal use you don't need to run anything manually — editing files in a Claude Code session or committing keeps the graph current. A manual full rebuild is only needed after a large external change (e.g. `git pull` of a big branch) or if the graph looks stale:

```powershell
cd C:\D\coding_projects\zefix_analyzer
code-review-graph update          # incremental — only files changed since last build
code-review-graph build           # full re-parse of all files
code-review-graph status          # graph stats
```

### Manual commands from a terminal (no MCP/assistant needed)

```powershell
code-review-graph status                     # graph stats
code-review-graph detect-changes --brief      # read-only impact analysis of current uncommitted diff
code-review-graph visualize                   # interactive HTML graph
code-review-graph wiki                        # markdown wiki from community structure
code-review-graph embed                       # compute vector embeddings for semantic search
code-review-graph postprocess                  # rebuild flows/communities/FTS without re-parsing
code-review-graph daemon                       # multi-repo watch daemon control (start/stop/status)
```

Full command list: `code-review-graph --help`.

### Excluding directories

`.code-review-graphignore` (repo root) adds extra exclude patterns on top of the built-in defaults (`.git/`, `node_modules/`, `.venv/`, `__pycache__/`, etc.). **Patterns are `fnmatch` globs, not gitignore syntax** — a bare `tests/` matches nothing; it must be `tests/**`. After editing the file, a **full rebuild** is required (`code-review-graph build` / `build_or_update_graph_tool` with `full_rebuild=true`) — incremental `update` doesn't re-evaluate exclusions for already-indexed files. Current excludes: `tests/**`, `infra/**`, `frontend/**`, `alembic/**`, `.vscode/**`, `docs/**`, `data/**` (backend app code only — verify with `list_graph_stats_tool`: no `Test` node kind and no `javascript`/`typescript`/`tsx` in the languages list).

### Known gotchas

1. **Never `pip install` this into the project `.venv`.** It has its own dependency tree (`fastmcp`, `pydantic` ≥2.11.7, etc.) that conflicts with `requirements.backend.txt` pins. Use the isolated venv described above.
2. **`code-review-graph install` reaches far beyond this repo if you let it.** Running it interactively offered to write global config for Codex (`~/.codex/hooks.json`), GitHub Copilot CLI (`~/.copilot/mcp-config.json` — which hardcoded this repo's path even though the file is global), and OpenCode (`~/.config/opencode/plugins/`). Those would have made **every** repo you touch with those tools try to run `code-review-graph` commands. We deliberately removed all three and kept the tool scoped to this repo's own config files only. If re-running `install`, decline (or immediately delete) anything it writes outside this project directory.
3. **PATH must be refreshed** after adding the venv's `Scripts` dir — see above. New terminal/session required.
4. **Config file must be `.mcp.json` (dotfile)** — same gotcha as `codegraph` (section 26.4); a non-dotted `mcp.json` is silently never read by Claude Code.
5. **Two similarly-named tools.** If a query returns unexpected results, check which server's tools you actually called (`mcp__codegraph__*` vs `mcp__code-review-graph__*`).

### Quick health check

```
1. `code-review-graph status` — files/nodes/edges non-zero, roughly matching real source file count.
2. `code-review-graph detect-changes --brief` after editing a file — should list that file and its blast-radius, not error out.
3. Make a commit — the pre-commit hook should run silently (check `.git/hooks/pre-commit` ran via `--tb`/verbose git output if you suspect it isn't firing); it never blocks the commit either way.
```

