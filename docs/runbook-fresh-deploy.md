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
| `ARC_APP_ID` | GitHub App ID (from App settings page) |
| `ARC_APP_INSTALLATION_ID` | Installation ID (from App → Install → URL contains the ID) |
| `ARC_APP_PRIVATE_KEY` | Contents of the `.pem` file (paste the full multiline value) |

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

### Step 2 — Update DNS

If the load balancer IP changed, update your DNS A record:

| Record | Type | Value |
|---|---|---|
| `helvex.dicy.ch` | A | `<lb_ipv4>` |

DNS TTL is usually 300s (5 min). Wait before testing TLS.

### Step 3 — Wait for cloud-init to finish

Cloud-init installs K3s, Helm, Helmfile, the helm-diff plugin, sets up the ubuntu user, and clones the repo. It does **not** run helmfile — that happens in steps 4 and 5. This takes **3–5 minutes**.

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

### Step 4 — Create the ARC GitHub App secret

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

### Step 5 — Run helmfile to deploy the full stack

```bash
cd /opt/helvex
git checkout prod_init
git pull
cd infra

helmfile -e prod apply --selector name=cert-manager --suppress-diff
helmfile -e prod apply --selector name=cloudnative-pg --suppress-diff
kubectl wait --for condition=established --timeout=120s crd/clusters.postgresql.cnpg.io
kubectl wait --for condition=established --timeout=120s crd/clusterissuers.cert-manager.io
helmfile -e prod apply --suppress-diff
```

Wait for ARC pods to start:

```bash
kubectl get pods -n arc-systems -w
```

You should see `arc-controller-*` and `arc-runner-set-*` pods reach `Running`.

### Step 6 — Restore database (if rebuilding with existing data)

Skip this step if the database is empty (first-time deploy).

If you have existing data backed up in S3, restore it now while still SSH'd into `app1`. This uses `--set` to override the value at apply time — **no code change or commit needed**:

```bash
cd /opt/helvex/infra
helmfile -e prod apply --selector name=helvex \
  --set postgres.restoreFromBackup=true \
  --suppress-diff
```

CloudNativePG will read the latest base backup + WAL from `s3://helvex-backups/pg/` and replay them. Wait until the cluster is healthy:

```bash
kubectl get cluster -n helvex-prod -w
```

Wait for `STATUS: Cluster in healthy state`.

### Step 7 — Trigger the first deploy

Exit the SSH session. On your local machine:

```bash
git commit --allow-empty -m "chore: trigger initial prod deploy [deploy-prod]"
git push
```

Watch the workflow run at **github.com/petz15/helvex → Actions**.

The `deploy` job will run on the `helvex-prod` ARC runner (the pod you started in step 5).

> The deploy workflow uses `restoreFromBackup: false` (the default). No toggle commit needed — the `--set` override from step 6 only applied to that one manual helmfile run.

### Step 7 — Verify

```bash
ssh-keygen -R <app1-public-ip>
ssh ubuntu@<app1-public-ip>
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml

kubectl get pods -n helvex-prod
```

Expected state:

```
helvex-XXXXX          1/1   Running
helvex-frontend-XXXXX 1/1   Running
redis-master-0        1/1   Running
helvex-db-1           1/1   Running   # CloudNativePG primary
```

Then open https://helvex.dicy.ch in a browser. TLS should be valid (cert-manager issues the Let's Encrypt cert on first request — allow up to 60s).

---

## Updating a secret value after deploy

The deploy workflow only **creates** secrets if missing — it does not update them. To rotate a secret:

```bash
ssh-keygen -R <app1-public-ip>
ssh ubuntu@<app1-public-ip>
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml

# Delete and let the next deploy recreate it
kubectl delete secret helvex-env -n helvex-prod

# Then update the value in GitHub Secrets and push a [deploy-prod] commit
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
