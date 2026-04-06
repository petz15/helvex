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
11b. [Logs: ML Worker on Home Node](#11b-logs-ml-worker-on-home-node)
12. [Debug: Temporarily Enable Verbose Logging](#12-debug-temporarily-enable-verbose-logging)
12b. [Logging: App Loggers Not Emitting](#12b-logging-app-loggers-not-emitting-to-stdout)
13. [Deploy: Node Disk Full Quick Cleanup](#13-deploy-node-disk-full-quick-cleanup)
14. [Node: High CPU Load / k3s API Unresponsive](#14-node-high-cpu-load--k3s-api-unresponsive)
15. [Monetization Ops Checks (Phase 4 and Phase 5)](#15-monetization-ops-checks-phase-4-and-phase-5)
16. [Classification Workflow: Job Sequencing](#16-classification-workflow-job-sequencing)
17. [Home ML Node Rollout (Phases A-C)](#17-home-ml-node-rollout-phases-a-c)

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
git commit -m "restore db from backup [deploy-app]"
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

## 16. Classification Workflow: Job Sequencing

The classification pipeline consists of three independent ML jobs that improve company enrichment. Run them in order to achieve best quality.

### Overview

| Job | Purpose | Duration | Depends On | S3 Artifacts |
|-----|---------|----------|------------|---|
| `hdbscan_cluster` | Train TF-IDF + K-Means, assign clusters, extract keywords | 10–30 min | — | ✅ Uploads SVD, vectorizer, centroids |
| `reextract_keywords` | Re-extract keywords for all companies using cached vectorizer | 1–5 min | `hdbscan_cluster` S3 artifacts | — |
| `reclassify_noga` | Classify companies with NOGA taxonomy + embedding similarity | 2–10 min | — | ✅ Uses NOGA embeddings |

### Recommended Sequence (Initial Setup)

**Step 1: Full clustering run**
```bash
POST /api/v1/jobs
{
  "job_type": "hdbscan_cluster",
  "params": {
    "n_clusters": 150,
    "only_missing_noga": false
  }
}
```
This trains the ML models and uploads artifacts to S3. Monitor progress via the Jobs UI. **Duration: 10–30 min**

**Step 2: Extract keywords with cached vectorizer** (optional but recommended)
```bash
POST /api/v1/jobs
{
  "job_type": "reextract_keywords",
  "params": {
    "only_missing": false
  }
}
```
Ensures all companies have corpus-relative keywords. **Duration: 1–5 min**

**Step 3: Classify with NOGA**
```bash
POST /api/v1/jobs
{
  "job_type": "reclassify_noga",
  "params": {
    "only_missing_noga": false
  }
}
```
Now keywords are available for embedding similarity. **Duration: 2–10 min**

### Ongoing Workflow

**For new companies (detail import):**
- Detail enrichment runs (fetches Zefix full data)
- Keywords are auto-extracted if S3 artifacts exist (non-fatal fallback if not)
- NOGA classification runs immediately

**Periodic retraining:**
- Run `hdbscan_cluster` weekly/monthly to refresh clusters + S3 models
- New S3 artifacts enable better incremental extraction for future companies

**Just recalculate keywords:**
- If stopwords or lemmatization changed, run `reextract_keywords` alone to refresh all companies

### What Each Job Does

#### `hdbscan_cluster`

1. Load all companies with purpose text
2. Strip boilerplate sentences (DB patterns)
3. Lemmatize with spaCy German model
4. TF-IDF vectorization (corpus-wide)
5. Dimensionality reduction (SVD)
6. K-Means clustering
7. Extract keywords per company (same TF-IDF)
8. Assign clusters (multi-label soft assignment)
9. **Save to S3:** TF-IDF vectorizer, SVD transformer, K-Means centroids, cluster registry mapping
10. Filter low-quality clusters (specificity < threshold)
11. Store `tfidf_cluster` + `purpose_keywords` in DB

#### `reextract_keywords`

1. Load S3 artifacts (TF-IDF vectorizer, SVD transformer)
2. For each company with purpose text:
   - Extract keywords using cached vectorizer
   - Apply bigram penalty + deduplication
   - Store in DB
3. **Non-fatal:** if S3 unavailable, skip silently

#### `reclassify_noga`

1. Load NOGA taxonomy + S3 embedding artifacts (sentence-transformers)
2. For each company (optionally filtered):
   - Embed `purpose_keywords` (or fallback to tokens)
   - Similarity-rank against NOGA entries
   - Hybrid re-rank: 60% embedding sim + 40% token match
   - Store code, label, level, confidence, full hierarchy path
3. **Non-fatal:** if S3 embeddings unavailable, use token-only matching

---

## 17. Home ML Node Rollout (Phases A-C)

This chapter is the step-by-step implementation tracker for home-first ML scheduling with cloud fallback.

Current decision: run in home-only mode for now. Cloud fallback (Phase B/C) is deferred.

### Status tracker

- Phase A (add home node): completed
- Phase B (cloud fallback node class): deferred
- Phase C (scheduling policy in Helm): deferred

### Current operating mode (active)

- ML runs on home node only
- No cloud fallback node class is configured
- If home node is down, ML jobs remain queued until home node returns

Operational note:
- Keep KEDA behavior unchanged if desired, but expect Pending pods or queued jobs when no schedulable home ML node is available.

### Networking architecture

```
app1 (10.0.1.10, TS: 100.x.x.x) ←─ enp7s0 ──→ db1 (10.0.1.11, TS: 100.x.x.x)
       │ tailscale0                                      │ tailscale0
       └─────────────── Tailscale ─────────────────────┘
                              │
                    ubuntuserverhome (TS: 100.x.x.x)
```

- **Hetzner ↔ Hetzner**: Flannel VXLAN stays on `enp7s0` (private network). No Tailscale.
- **Home ↔ Hetzner**: Flannel VXLAN via Tailscale. The home node accepts route `10.0.1.0/24`
  via Tailscale subnet routing, so it can send VXLAN to `10.0.1.10/11` through Tailscale.
  **Do NOT annotate app1's flannel public-ip with the Tailscale IP** — that routes db1→app1
  VXLAN through Tailscale and creates a dependency that breaks Hetzner-to-Hetzner comms.

### Repair: current cluster broken (app1↔db1 unreachable after subnet change)

If db1 can no longer reach app1 (or vice versa), the likely cause is either:
- `--accept-routes` was inadvertently set on a Hetzner node, causing it to route `10.0.1.x`
  traffic via Tailscale instead of directly over enp7s0
- The flannel public-ip annotation on app1 is set to the Tailscale IP, forcing db1 VXLAN
  through Tailscale which then broke when subnet settings changed

**On app1 and db1 (run both):**
```bash
# Remove accept-routes so Hetzner nodes route 10.0.1.x locally, not via Tailscale
sudo tailscale set --accept-routes=false

# Remove the flannel annotation if present (it forces VXLAN through Tailscale)
kubectl annotate node helvex-prod-app1 \
  flannel.alpha.coreos.com/public-ip- \
  flannel.alpha.coreos.com/public-ip-overwrite- 2>/dev/null || true

# Restart to flush flannel state
sudo systemctl restart k3s        # on app1
# sudo systemctl restart k3s-agent  # on db1
```

Verify:
```bash
kubectl get nodes -o wide
# Both app1 and db1 should be Ready
# app1's flannel public-ip should show 10.0.1.10 (private IP), not a Tailscale IP
kubectl get node helvex-prod-app1 -o jsonpath='{.metadata.annotations}' | python3 -m json.tool | grep flannel
```

### Phase A replay procedure (fresh control plane + home node)

Use this if the cluster was freshly rebuilt and you need to re-attach the home node.

1) On control-plane node `app1`, collect join inputs:

```bash
ssh ubuntu@<app1-public-ip>
sudo cat /var/lib/rancher/k3s/server/node-token   # K3S_TOKEN
tailscale ip -4                                   # CP_TAILSCALE_IP
```

2) Copy the join script to the home server and run it:

```bash
# On your local machine — copy the script
scp scripts/join-home-node.sh ubuntu@ubuntuserverhome:/tmp/

# On the home server
ssh ubuntu@ubuntuserverhome
sudo bash /tmp/join-home-node.sh \
  <CP_TAILSCALE_IP> \
  <K3S_TOKEN> \
  <TAILSCALE_AUTH_KEY>
```

The script installs Tailscale (if absent), joins with `--accept-routes` (so the home node
can reach Hetzner private IPs via Tailscale subnet routing), removes any old k3s agent,
and installs a fresh agent using the Tailscale IP for the API connection.

3) **One-time Tailscale admin step** (needed once per Terraform rebuild, not per join):

Cloud-init already runs `tailscale set --advertise-routes=10.0.1.0/24` on both Hetzner
nodes. You only need to approve the routes in the admin console:

- **admin.tailscale.com → Machines → app1** → Edit route settings → approve `10.0.1.0/24`
- **admin.tailscale.com → Machines → db1**  → Edit route settings → approve `10.0.1.0/24`

4) Back on control-plane, verify both nodes and confirm labels/taints:

```bash
kubectl get nodes -o wide
kubectl label node ubuntuserverhome workload=ml location=home --overwrite
kubectl taint node ubuntuserverhome workload=ml:NoSchedule --overwrite
kubectl describe node ubuntuserverhome | grep -E "Taints|workload=|location="
```

5) Verify pod DNS works from the home node:

```bash
kubectl run dns-test --image=busybox:1.36 --restart=Never \
  --overrides='{"spec":{"nodeSelector":{"workload":"ml"},"tolerations":[{"key":"workload","operator":"Equal","value":"ml","effect":"NoSchedule"}]}}' \
  -- sleep 60
kubectl exec dns-test -- nslookup kubernetes.default.svc.cluster.local
kubectl delete pod dns-test
```

Acceptance checks (both nodes):
- `app1` is `Ready`
- `ubuntuserverhome` is `Ready`
- Home node has labels `workload=ml`, `location=home`
- Home node has taint `workload=ml:NoSchedule`
- DNS test resolves `kubernetes.default.svc.cluster.local`

Post-step hardening:

```bash
sudo k3s token rotate
```

Run on the control-plane after successful home-node onboarding.

### Home-only ops checklist (small)

Daily checks:
- Verify home node is Ready
- Verify ML labels/taint are still present
- Verify no long-running Pending ML pod

```bash
kubectl get node ubuntuserverhome
kubectl describe node ubuntuserverhome | grep -E "Taints|workload=|location="
kubectl get pods -n helvex-prod -l app.kubernetes.io/component=ml-worker -o wide
```

Planned home shutdown:

```bash
kubectl cordon ubuntuserverhome
kubectl drain ubuntuserverhome --ignore-daemonsets --delete-emptydir-data
```

Expected during shutdown:
- ML jobs stay queued
- No cloud fallback scheduling in current mode

Resume home node:

```bash
kubectl uncordon ubuntuserverhome
kubectl get node ubuntuserverhome
kubectl get pods -n helvex-prod -l app.kubernetes.io/component=ml-worker -o wide
```

### Phase A completion record

Completed and verified:
- Home node `ubuntuserverhome` joined as k3s agent
- Control plane is reachable via Tailscale (`100.95.141.34:6443`)
- Home node labeled: `workload=ml`, `location=home`
- Home node tainted: `workload=ml:NoSchedule`

Post-completion hardening:
- Rotate k3s node token (it was exposed during troubleshooting)

### Phase B next steps (cloud fallback node class, deferred)

Goal: ensure cloud fallback nodes are also ML-capable and satisfy the same required scheduling key.

1) Pick at least one cloud node (or autoscaled node template/group) for ML fallback.

2) Apply labels on cloud fallback nodes:

```bash
kubectl label node <cloud-ml-node> workload=ml location=cloud --overwrite
```

3) If using taints for dedicated ML nodes, apply the same taint model:

```bash
kubectl taint node <cloud-ml-node> workload=ml:NoSchedule --overwrite
```

4) Validate labels and taints:

```bash
kubectl get nodes --show-labels | grep -E "workload=ml|location=cloud|location=home"
kubectl describe node <cloud-ml-node> | grep -E "Taints|workload=|location="
kubectl describe node ubuntuserverhome | grep -E "Taints|workload=|location="
```

5) if logs dont show up on control plae

```bash
The systemctl edit approach with k3s is tricky because k3s rewrites its own service file on restart. Instead, edit the service file directly:


sudo nano /etc/systemd/system/k3s.service
# Change: '--advertise-address=10.0.1.10'
# To:     '--advertise-address=100.102.98.50'

sudo systemctl daemon-reload
sudo systemctl restart k3s
```

Phase B acceptance checks:
- At least one cloud node has `workload=ml` and `location=cloud`
- Home node remains `workload=ml` and `location=home`
- Taint/toleration model is consistent across ML nodes

### Phase C next steps (home preferred, cloud fallback scheduling, deferred)

Goal: configure ML worker to require ML nodes, prefer home, and tolerate ML taints.

1) Update Helm chart to support `mlWorker.affinity` in the ML worker Deployment template.

2) Configure prod values for ML worker scheduling:

```yaml
mlWorker:
  enabled: true
  # keda.enabled should be true once KEDA is installed and active
  nodeSelector:
    workload: ml
  tolerations:
    - key: workload
      operator: Equal
      value: ml
      effect: NoSchedule
  affinity:
    nodeAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
        nodeSelectorTerms:
          - matchExpressions:
              - key: workload
                operator: In
                values: ["ml"]
      preferredDuringSchedulingIgnoredDuringExecution:
        - weight: 100
          preference:
            matchExpressions:
              - key: location
                operator: In
                values: ["home"]
```

3) Deploy and verify scheduling behavior:

```bash
# Trigger one ML job so worker scales from 0 to 1
kubectl get pods -n helvex-prod -l app.kubernetes.io/component=ml-worker -o wide -w

# Confirm pod lands on home node when available
kubectl get pod -n helvex-prod -l app.kubernetes.io/component=ml-worker -o wide

# Simulate planned home downtime
kubectl cordon ubuntuserverhome
kubectl drain ubuntuserverhome --ignore-daemonsets --delete-emptydir-data

# Trigger ML job again and verify fallback to cloud node
kubectl get pods -n helvex-prod -l app.kubernetes.io/component=ml-worker -o wide -w

# Recover home node
kubectl uncordon ubuntuserverhome
```

Phase C acceptance checks:
- ML worker scales from 0 when queue has jobs
- With home node healthy, ML pod schedules to `ubuntuserverhome`
- With home node unavailable, ML pod schedules to cloud `workload=ml` node
- ML worker does not land on non-ML nodes

### Update log

- 2026-04-03: Phase A completed; Phase B/C tasks documented.
- 2026-04-03: Decision recorded to defer Phase B/C and continue in home-only ML mode.



