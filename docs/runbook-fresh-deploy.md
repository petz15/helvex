# Runbook: Fresh Infrastructure Deploy

Follow this guide every time you run `terraform apply` from scratch (new servers, rebuild, or disaster recovery).

---

## Prerequisites (one-time setup, survives rebuilds)

These only need to be done once. Skip if already done.

### GitHub repository secrets

Go to **github.com/petz15/helvex → Settings → Secrets and variables → Actions** and ensure all of these exist:

| Secret | Value |
|---|---|
| `DB_URL` | `postgresql://helvex:PASSWORD@helvex-db.helvex-prod.svc.cluster.local:5432/helvex` — use the stable `helvex-db` alias, not `helvex-pg-rw` directly (see note below) |
| `DB_PASSWORD` | The PostgreSQL password alone (used by CloudNativePG bootstrap) |
| `REDIS_URL` | `redis://:PASSWORD@redis-master.helvex-prod.svc.cluster.local:6379/0` |
| `REDIS_PASSWORD` | Redis password |
| `SECRET_KEY` | FastAPI secret key (generate with `openssl rand -hex 32`) |
| `S3_ACCESS_KEY` | Hetzner Object Storage access key |
| `S3_SECRET_KEY` | Hetzner Object Storage secret key |
| `GHCR_PAT` | GitHub Personal Access Token with `write:packages` scope |
| `SMTP_HOST` | SMTP server hostname, e.g. `smtp.mailgun.org` |
| `SMTP_PORT` | SMTP port, typically `587` (STARTTLS) |
| `SMTP_USER` | SMTP login username |
| `SMTP_PASSWORD` | SMTP login password |
| `SMTP_FROM` | Sender address, e.g. `Helvex <noreply@helvex.dicy.ch>` |
| `STORAGEBOX_HOST` | Hetzner Storage Box host, e.g. `u12345.your-storagebox.de` |
| `STORAGEBOX_USER` | Hetzner Storage Box user, e.g. `u12345` |
| `STORAGEBOX_PATH` | Optional export folder, e.g. `/backups/helvex/pg-prod` |
| `STORAGEBOX_PORT` | Storage Box SSH/SFTP port, usually `23` |
| `STORAGEBOX_SSH_PRIVATE_KEY` | Private key (multiline) for Storage Box SFTP authentication |
| NEXT_PUBLIC_GTM_ID | Google Tag Manager container ID for frontend build, e.g. `GTM-XXXXXXX` |
| NEXT_PUBLIC_ADSENSE_CLIENT_ID | Google AdSense publisher client ID for frontend build, e.g. `ca-pub-XXXXXXXXXXXXXXXX` |
| NEXT_PUBLIC_POSTHOG_KEY | PostHog project API key for frontend build, e.g. `phc_xxx...` |
| NEXT_PUBLIC_POSTHOG_HOST | PostHog ingest host for frontend build, e.g. `https://eu.i.posthog.com` |
| NEXT_PUBLIC_UMAMI_WEBSITE_ID | Umami website ID for frontend build |
| NEXT_PUBLIC_UMAMI_SCRIPT_URL | Umami script URL for frontend build, e.g. `https://cloud.umami.is/script.js` |
| `ARC_APP_ID` | GitHub App ID (from App settings page) |
| `ARC_APP_INSTALLATION_ID` | Installation ID (from App → Install → URL contains the ID) |
| `ARC_APP_PRIVATE_KEY` | Contents of the `.pem` file (paste the full multiline value) |

GitHub repository variable (optional, used as fallback in restore selection):

| Variable | Value |
|---|---|
| `POSTGRES_RESTORE_SOURCE` | Backup source `serverName` in S3 (often timestamped), e.g. `helvex-pg-20260331T150000Z` |

> **`DB_URL` hostname:** Always use `helvex-db` (the Helm-managed ExternalName service), never `helvex-pg-rw` directly. The chart routes `helvex-db` → `helvex-pg-rw` when the connection pooler is disabled, and → `helvex-pg-pooler` when it is enabled. This means enabling/disabling the pooler is a Helm values change only — no secret rotation needed.

### GitHub App for ARC

If the GitHub App does not exist yet:
1. Go to **github.com/settings/apps → New GitHub App**
2. Name: `helvex-arc`, Homepage URL: `https://github.com/petz15/helvex`
3. Permissions: **Repository → Actions: Read**, **Repository → Administration: Read**
4. No webhook needed — uncheck "Active"
5. Create the app, note the **App ID**
6. Under "Private keys" → **Generate a private key** → save the `.pem`
7. Go to **Install App** → install on the `petz15/helvex` repository → note the installation ID from the URL (`/installations/XXXXXXX`)
8. Save App ID, Installation ID, and `.pem` contents as GitHub secrets (see table above)

---

## Before `terraform apply`

Make sure all local changes are **pushed to GitHub** before running terraform. Cloud-init clones the repo from GitHub on boot — if your changes are only local, the server will get stale code.

```bash
git push
```

---

## After every `terraform apply`

### Step 1 — Get the server IPs

```bash
cd infra/terraform/envs/prod
terraform output
```

Note:
- `lb_ipv4` — load balancer public IP (for DNS)
- `server_public_ips["app1"]` — control-plane public IP (for SSH)

### Step 2 — Access model

Cluster provisioning uses Hetzner private networking only for K3s control-plane and pod traffic.
Use the public IP of `app1` for SSH administration.

### Step 3 — Update DNS

If the load balancer IP changed, update your DNS A record:

| Record | Type | Value |
|---|---|---|
| `helvex.dicy.ch` | A | `<lb_ipv4>` |

Usually: 162.55.153.183

DNS TTL is usually 300s (5 min). Wait before testing TLS.

### Step 4 — Wait for cloud-init to finish

Cloud-init installs K3s, Helm, Helmfile, the helm-diff plugin, sets up the ubuntu user, and clones the repo. It does **not** run helmfile — that happens in steps 5 and 6. This takes **3–5 minutes**.

SSH in and tail the log:

```bash
ssh-keygen -R <app1-public-ip>
ssh ubuntu@<app1-public-ip>
sudo tail -f /var/log/cloud-init-output.log
```

Wait until you see:

```
Cloud-init v. ... finished at ...
```

Then **log out and back in** so the kubeconfig and group membership take effect:

```bash
exit
ssh ubuntu@<app1-public-ip>
kubectl get nodes
```

Both `app1` (control-plane) and `db1` (worker) should show `Ready`. No `sudo`, no `export KUBECONFIG` needed.

If only `app1` appears:

```bash
# 1) Re-apply Terraform to update firewall rules for private subnet joins
cd infra/terraform/envs/prod
terraform apply

# 2) Check db1 cloud-init / agent logs
ssh ubuntu@<db1-public-ip>
sudo tail -n 200 /var/log/cloud-init-output.log
sudo journalctl -u k3s-agent -n 200 --no-pager

# 3) Restart agent after firewall is fixed
sudo systemctl restart k3s-agent

# 4) Back on app1, confirm both nodes
ssh ubuntu@<app1-public-ip>
kubectl get nodes -o wide
```

### Step 5 — Create the ARC GitHub App secret

ARC needs this secret to authenticate with GitHub. It must exist before helmfile runs.

```bash
kubectl create namespace arc-systems --dry-run=client -o yaml | kubectl apply -f -

cat > /tmp/arc-key.pem << 'EOF'
-----BEGIN RSA PRIVATE KEY-----
<paste your .pem contents here>
-----END RSA PRIVATE KEY-----
EOF

kubectl create secret generic arc-github-app \
  --from-literal=github_app_id="<ARC_APP_ID>" \
  --from-literal=github_app_installation_id="<ARC_APP_INSTALLATION_ID>" \
  --from-file=github_app_private_key=/tmp/arc-key.pem \
  -n arc-systems

rm /tmp/arc-key.pem

kubectl get secret arc-github-app -n arc-systems
```

### Step 6 — Deploy operators and ARC only

> **Important:** Do **not** deploy the `helvex` chart manually. The `helvex-env` secret
> (which holds S3 credentials for PG backup restore, DB passwords, etc.) is created by the
> GitHub Actions deploy workflow. If you deploy the helvex chart before the secret exists,
> the PostgreSQL cluster will get stuck in "Setting up primary" forever.

```bash
cd /opt/helvex
git checkout prod_init
git pull
cd infra

# CRDs first
helmfile -e prod apply --selector name=cert-manager --suppress-diff
helmfile -e prod apply --selector name=cloudnative-pg --suppress-diff
kubectl wait --for condition=established --timeout=120s crd/clusters.postgresql.cnpg.io
kubectl wait --for condition=established --timeout=120s crd/clusterissuers.cert-manager.io

# ARC (self-hosted GitHub Actions runner)
helmfile -e prod apply --selector name=arc-controller --suppress-diff
helmfile -e prod apply --selector name=arc-rbac --suppress-diff
helmfile -e prod apply --selector name=arc-runner-set --suppress-diff
```

Wait for ARC pods to start:

```bash
kubectl get pods -n arc-systems -w
```

You should see `arc-controller-*` and `arc-runner-set-*` pods reach `Running`.

### Step 7 — Trigger the first deploy (make sure it gets the right backup)

Exit the SSH session. On your local machine:

```bash
git commit --allow-empty -m "chore: trigger initial prod deploy [deploy-prod]"
git push
```

Watch the workflow run at **github.com/petz15/helvex → Actions**.

The `deploy` job will run on the `helvex-prod` ARC runner (the pod you started in step 5). It will:

1. Create the `helvex-env` K8s secret (with S3 credentials, DB password, etc.)
2. **Resolve backup names + restore source**:
  - `backupServerName`: generates a unique timestamped name (e.g. `helvex-pg-20260331T150000Z`) and stores it in the `pg-backup-meta` ConfigMap. This isolates the new cluster's backup lineage. Subsequent deploys reuse the same name.
  - `restoreSourceServerName` (CNPG reads backups FROM this server): selected in this order:
    1. Manual workflow input `restore_source` (when using `workflow_dispatch`)
    2. S3 restore-point file `s3://helvex-backups/pg-prod/restore-point.json`
    3. Repo file `restore-point.json` (tracked in git)
    4. Repo variable `POSTGRES_RESTORE_SOURCE`
    5. Existing ConfigMap `pg-backup-meta.data.restoreSource`
    6. Default: `helvex-pg`
  - The selected value should match the **CNPG/Barman `serverName`** used when the backups were written. In this repo that is often timestamped (e.g., `helvex-pg-20260331T150000Z`).
  - Workflow writes/updates `restore-point.json` after selection for next deploy.
3. Deploy the helvex chart via helmfile (PostgreSQL, Redis, app, workers)
4. Wait for PostgreSQL to become healthy (up to 10 minutes — restore from S3 backup)
5. Wait for app rollout

Optional manual run from GitHub Actions UI:
- Open **Actions → Deploy Prod → Run workflow**
- Set `deploy_mode` (`prod`, `app`, `frontend`, `backend`)
- Leave `restore_source` empty to use restore-point file, or set it explicitly for one-off recovery

> **Database restore:** `prod.yaml` has `restoreFromBackup: true`, so the PostgreSQL
> cluster bootstraps by restoring from the auto-detected S3 backup. This only applies
> on first cluster creation — subsequent deploys ignore the bootstrap section because
> the cluster already exists. If this is a first-time deploy with no S3 backup, set
> `restoreFromBackup: false` in `prod.yaml` before pushing.
>
> **Backup pruning:** A weekly CronJob (`helvex-pg-backup-prune`) deletes orphaned backup
> directories older than 14 days, keeping only the active backup path. This prevents S3
> storage from growing unbounded across rebuilds.

### Step 8 — Verify

```bash
ssh ubuntu@<app1-public-ip>
kubectl get pods -n helvex-prod
```

Expected state:

```
helvex-XXXXX                  1/1   Running   # FastAPI app
helvex-frontend-XXXXX         1/1   Running   # Next.js
helvex-zefix-worker-XXXXX     1/1   Running   # Zefix import jobs
helvex-api-worker-XXXXX       1/1   Running   # Scoring/geocode/Claude jobs (2 replicas)
helvex-api-worker-YYYYY       1/1   Running
redis-master-0                1/1   Running
helvex-db-1                   1/1   Running   # CloudNativePG primary
helvex-db-2                   1/1   Running   # CloudNativePG standby
```

> **ml-worker**: Not listed above — KEDA scales it to 0 replicas by default. It will appear as `helvex-ml-worker-XXXXX` only when an ML job (`hdbscan_cluster`, `recompute_keywords`, `cluster_analysis`) is queued. After the job completes and the 5-minute cooldown elapses, the pod terminates automatically.

Then open https://helvex.dicy.ch in a browser. TLS should be valid (cert-manager issues the Let's Encrypt cert on first request — allow up to 60s).

### Step 9 — Add a Hetzner ML node (two options)

Use this after a fresh deploy when `app1` is healthy and you want dedicated ML capacity.

#### Option A: Terraform-managed ML node (recommended)

1. Edit `infra/terraform/envs/prod/terraform.tfvars` and set `ml_nodes`:

```hcl
ml_nodes = {
  ml1 = {
    server_type = "cx43"
    role        = "k3s-worker"
    private_ip  = "10.0.1.21"
    node_labels = ["workload=ml", "location=cloud"]
    node_taints = ["workload=ml:NoSchedule"]
  }
}
```

2. Apply Terraform:

```bash
cd infra/terraform/envs/prod
terraform apply
```

3. Verify node readiness:

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

Windows PowerShell:

```powershell
.\scripts\toggle-terraform-ml-node.ps1 enable `
  -Name helvex-ml-1 `
  -Location nbg1 `
  -NodeType cax21 `
  -PrivateIp 10.0.1.21
```

#### Option B: Script-managed ML node (fast/manual)

1. On `app1`, collect join input:

```bash
ssh ubuntu@<app1-public-ip>
sudo cat /var/lib/rancher/k3s/server/node-token
```

2. Provision and join with helper script:

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

3. Verify labels/taints from `app1`:

```bash
kubectl get nodes -o wide
kubectl describe node helvex-ml-1 | grep -E "Taints|workload=|location="
```

4. Verify pod DNS from the ML node:

```bash
kubectl run dns-test --image=busybox:1.36 --restart=Never \
  --overrides='{"spec":{"nodeSelector":{"workload":"ml"},"tolerations":[{"key":"workload","operator":"Equal","value":"ml","effect":"NoSchedule"}]}}' \
  -- sleep 60
kubectl exec dns-test -- nslookup kubernetes.default.svc.cluster.local
kubectl delete pod dns-test
```

5. Rotate the node join token after successful onboarding (hardening):

```bash
ssh ubuntu@<app1-public-ip>
sudo k3s token rotate
```

#### Deprovisioning

Terraform-managed node:

```bash
# Remove the entry from ml_nodes in terraform.tfvars, then:
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

---

## Installing KEDA (prerequisite for ml-worker scale-to-zero)

KEDA must be installed before enabling `mlWorker.keda.enabled: true` in prod.yaml. Run once on a healthy cluster:

```bash
helm repo add kedacore https://kedacore.github.io/charts
helm repo update
helm install keda kedacore/keda \
  --namespace keda \
  --create-namespace \
  --set watchNamespace=helvex-prod
```

Verify:

```bash
kubectl get pods -n keda
# keda-operator-XXXXX            1/1   Running
# keda-operator-metrics-XXXXX    1/1   Running
```

Then in `infra/environments/prod.yaml` set `mlWorker.keda.enabled: true` and push a `[deploy-prod]` commit. The `ScaledObject` and `TriggerAuthentication` resources will be created by the Helm chart.

---

## Updating a secret value after deploy

The deploy workflow uses `kubectl create ... --dry-run=client -o yaml | kubectl apply -f -`, so it **always updates** `helvex-env` to match the current GitHub Secrets. To rotate a secret:

1. Update the value in **GitHub → Settings → Secrets and variables → Actions**
2. Push a `[deploy-prod]` or `[deploy-app]` commit — the workflow will overwrite the K8s secret with the new values
3. Restart any pods that need the new value (they read env vars at startup):

```bash
ssh ubuntu@<app1-public-ip>
kubectl rollout restart deployment/helvex -n helvex-prod
```

---

## Troubleshooting: GHCR push denied (`write_package`)

If the build job fails with:

```
denied: permission_denied: write_package
```

do the following:

1. Confirm repository secret `GHCR_PAT` exists and is a classic PAT for the account that owns the package namespace (`petz15`) with at least:
  - `write:packages`
  - `read:packages`
  - `delete:packages` (optional, but recommended for cleanup workflows)
2. Ensure SSO is authorized for the token if your org enforces SSO.
3. In GitHub Packages, open the package (for example `helvex-ml`) and verify repository access includes `petz15/helvex` with write/admin package permission.
4. Re-run the workflow.

Notes:
- Deploy workflows log in to GHCR using `GHCR_PAT` when available, with fallback to `GITHUB_TOKEN`.
- `GITHUB_TOKEN` can fail on pre-existing packages not linked to the current repository; PAT avoids this edge case.

---

## Security model

This section explains why no secrets are exposed to the public, even though GitHub Actions orchestrates deployments to a private server.

### Where each secret lives

| Secret | Stored in | Accessible to |
|---|---|---|
| `DB_PASSWORD`, `SECRET_KEY`, etc. | GitHub repo secrets (encrypted at rest by GitHub) | Runner pod at job runtime only — injected as env vars, never logged |
| `arc-github-app` PEM key | K8s secret in `arc-systems` namespace | ARC controller pod only — never leaves the cluster |
| `helvex-env` (DB URL, Redis password, etc.) | K8s secret in `helvex-prod` namespace | The `helvex` pod only — mounted as env vars inside the container |
| `ghcr-pull-secret` | K8s secret in `helvex-prod` namespace | Kubernetes image pull mechanism only — your app code never sees it |
| `terraform.tfvars`, `backend.hcl`, `prod.yaml` | Your local machine only | Gitignored — never committed, never on GitHub |

### Why GitHub cannot see your cluster

ARC works **outbound-only**. Your cluster never opens a port to GitHub. Instead:

1. The ARC controller pod inside your cluster polls the GitHub API (`https://api.github.com`) using a short-lived JWT it generates from the GitHub App private key
2. When GitHub queues a job that needs `runs-on: helvex-prod`, the ARC controller sees it during the next poll and spins up an ephemeral runner pod
3. The runner pod connects outbound to GitHub, claims the job, and executes it
4. When the job finishes the pod is destroyed — no state persists

GitHub never initiates a connection into your cluster. There is no webhook listener, no open port, nothing for an attacker to find.

### Why the PEM paste in step 4 is safe

When you paste the private key into `/tmp/arc-key.pem` during setup:
- The connection is SSH (encrypted in transit)
- The file is immediately loaded into a K8s secret (`kubectl create secret --from-file=...`) and then deleted (`rm /tmp/arc-key.pem`)
- After that the key exists only inside K8s, stored in K3s's embedded database on `app1` — not as a file on disk
- K8s secrets are only readable by pods that have explicit RBAC permission (the ARC controller)

### Why secrets injected by GitHub Actions are safe

GitHub encrypts secrets at the repository level. When a job runs:
- GitHub injects secret values as environment variables directly into the runner pod over an encrypted channel
- The values are masked in all log output — if a secret value appears in a log line, GitHub replaces it with `***`
- Secrets are never written to disk by GitHub — they only exist in the process environment for the duration of the job
- You cannot read a secret back via the GitHub API — they are write-only from the UI

### What is actually public

Only these things are visible to anyone:
- The workflow files in `.github/workflows/` — they reference secret names (e.g. `${{ secrets.DB_PASSWORD }}`) but not the values
- The Docker images pushed to GHCR — these contain your application code but no credentials (credentials come from K8s secrets at runtime)
- The domain `helvex.dicy.ch` and its TLS certificate

---

## Lockout recovery (Hetzner firewall blocks your IP)

If your IP changes and you can no longer SSH in:

1. Go to **console.hetzner.cloud → your project → Servers → app1 → Console**
2. Log in as `ubuntu` (password login is disabled — use the VNC console keyboard)
3. Edit the firewall: **Hetzner Cloud → Firewalls → helvex-prod-fw → Edit rules**
4. Add your new IP to the SSH allow list (port 22)
5. Remove the old IP once done
