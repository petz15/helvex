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

WAL segments are archived continuously (within seconds of each transaction), so you can target any point within the 7-day retention window.

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

# Check barman logs on the primary pod
kubectl logs -n helvex-prod helvex-pg-1 -c postgres | grep -i "barman\|backup\|WAL"
```

Expected output for a healthy backup:
```
Starting barman-cloud-backup
Backup completed successfully
WAL file archived successfully
```

If you see `AccessDenied` or `NoSuchBucket` — the S3 credentials or bucket name are wrong.

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
# Tail all app logs
kubectl logs -n helvex-prod deploy/helvex -f

# Tail worker logs
kubectl logs -n helvex-prod deploy/helvex-worker -f

# Tail frontend logs
kubectl logs -n helvex-prod deploy/helvex-frontend -f

# Tail DB logs
kubectl logs -n helvex-prod helvex-pg-1 -f

# Get all pods and their status
kubectl get pods -n helvex-prod -o wide

# Describe a crashing pod (shows OOMKill, image pull errors, etc.)
kubectl describe pod <pod-name> -n helvex-prod

# Open a shell in the app pod
kubectl exec -n helvex-prod -it deploy/helvex -- bash

# Connect to Postgres directly
kubectl exec -n helvex-prod -it helvex-pg-1 -- psql -U helvex -d helvex

# Force restart a deployment (e.g. after updating a secret)
kubectl rollout restart deployment/helvex -n helvex-prod

# Watch rollout progress
kubectl rollout status deployment/helvex -n helvex-prod --timeout=120s

# List all K8s secrets (not their values)
kubectl get secrets -n helvex-prod

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
