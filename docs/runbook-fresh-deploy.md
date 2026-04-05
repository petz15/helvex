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

If tailscale is necessary, set up the tailscale_auth_key in the terafrom.tfvars

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

### Step 2 — Tailscale is pre-installed and joined automatically

Both `app1` and `db1` automatically install and join your Tailscale network during cloud-init (from the reusable auth key in `terraform.tfvars`). They are assigned stable Tailscale hostnames (`helvex-app1`, `helvex-db1`) for consistent identification.

You do **not** need to run any manual Tailscale setup on `app1` or `db1` — it's done at boot.

To verify:

```bash
ssh ubuntu@<app1-public-ip>
tailscale ip -4
# Should show app1's Tailscale IP immediately
tailscale status
# Should list both app1 and db1 as connected peers
```

**Note:** Tailscale is only installed on cluster servers (app1, db1). To add additional compute nodes later (e.g., your home server), you manually install Tailscale on those nodes and join them to the cluster using k3s agent — see Step 9.

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
    2. Repo variable `POSTGRES_RESTORE_SOURCE`
    3. S3 restore-point file `s3://helvex-backups/pg-prod/restore-point.json`
    4. Repo file `restore-point.json` (tracked in git)
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

### Step 9 — Add home node to a fresh control plane (Phase A)

Use this after a fresh deploy when `app1` is healthy and you want ML jobs to run on your home server.

The control-plane (`app1`) now automatically advertises its Tailscale IP in its k3s TLS SAN during cloud-init setup. No manual k3s configuration is needed on the server side.

1. On `app1`, retrieve the k3s agent join token and control-plane Tailscale IP:

```bash
ssh ubuntu@<app1-public-ip>
sudo cat /var/lib/rancher/k3s/server/node-token
tailscale ip -4
```

Save both values.

2. On the home server, install and join Tailscale (if not already done):

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --authkey <TAILSCALE_AUTH_KEY> --hostname ubuntuserverhome
tailscale ip -4
```

Verify the home server and `app1` can ping each other over Tailscale:

```bash
tailscale ping helvex-app1
```

3. On the home server, join k3s as an agent via the control-plane Tailscale IP:

If k3s agent is already installed from a previous cluster, uninstall it first:

```bash
/usr/local/bin/k3s-agent-uninstall.sh
```

Then install the k3s agent:

```bash
curl -sfL https://get.k3s.io | K3S_URL=https://<SERVER_TAILSCALE_IP>:6443 K3S_TOKEN=<K3S_TOKEN-from-step-1> sh -s - agent
```

4. On `app1`, verify both nodes are Ready and label/taint the home node for ML workloads:

```bash
kubectl get nodes -o wide
kubectl label node ubuntuserverhome workload=ml location=home --overwrite
kubectl taint node ubuntuserverhome workload=ml:NoSchedule --overwrite
kubectl describe node ubuntuserverhome | grep -E "Taints|workload=|location="
```

Expected result:
- `app1` is `Ready` (control-plane)
- `ubuntuserverhome` is `Ready` (agent)
- Home node has labels `workload=ml`, `location=home`
- Home node has taint `workload=ml:NoSchedule`

5. Rotate the node join token after successful onboarding (hardening):

```bash
ssh ubuntu@<app1-public-ip>
sudo k3s token rotate
```

This prevents the token from being used to join additional unauthorized nodes.

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
