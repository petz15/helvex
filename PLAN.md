# Helvex – Work Plan

## Phase 4 — Production deployment (60 min)

Work through these in order — each step unblocks the next.

### 4.1 Hetzner Object Storage bucket
- [ ] Create bucket `helvex-backups` in Hetzner Console (region: fsn1)
- [ ] Generate S3-compatible access/secret key pair
- [ ] Update `infra/terraform/envs/prod/backend.hcl` with actual bucket endpoint + credentials
- [ ] Update `infra/environments/prod.yaml` S3 fields:
  - `s3EndpointUrl: "https://fsn1.your-objectstorage.com"` → real endpoint
  - `s3BucketPath: "s3://helvex-backups/pg/"` → correct if bucket name matches

### 4.2 Terraform config
- [ ] Copy `infra/terraform/envs/prod/terraform.tfvars.example` → `terraform.tfvars`
- [ ] Fill in:
  - `hcloud_token` — from Hetzner Console → API tokens
  - `admin_cidrs` — your current public IP + `/32` (run `curl ifconfig.me` on your dev machine)
  - `ssh_keys` — names of SSH keys already uploaded in Hetzner Console
  - `k3s_token` — generate with `openssl rand -hex 32`
- [ ] Replace all `OWNER` placeholders in `infra/environments/prod.yaml` with actual GitHub username/org

> **Dynamic IP warning:** You are on a home network. If your IP changes you will be locked out of SSH and kubectl.
>
> **If locked out — regain access via Hetzner Console:**
> 1. Go to [console.hetzner.com](https://console.hetzner.com) → **Firewalls** → select `helvex-firewall`
> 2. Edit the inbound rule for port 22 (SSH) and 6443 (K3s API) — change the source IP to your new IP + `/32`
> 3. Click **Apply Changes** — takes effect in seconds, no reboot needed
> 4. Alternatively: open **Server → Console** (VNC in-browser) — gives root shell without SSH entirely, no firewall rules needed
> 5. After regaining access, run `terraform apply` with the updated `admin_cidrs` to keep state in sync

### 4.3 Provision infrastructure
- [ ] `cd infra/terraform/envs/prod && terraform init -backend-config=backend.hcl`
- [ ] `terraform plan` — review: 3 servers (cx32×2, cx22×1), 1 LB, 1 network, 1 firewall, 1 volume
- [ ] `terraform apply` — note the output `lb_ipv4` address
- [ ] Point DNS: `helvex.dicy.ch` A → `lb_ipv4`

### 4.4 K8s access + secrets
- [ ] SSH to `app1` (control-plane): `ssh root@<app1-ip>`
- [ ] Copy kubeconfig: `cat /etc/rancher/k3s/k3s.yaml` → save locally, replace `127.0.0.1` with `app1` public IP
- [ ] Add to GitHub Actions secret `KUBECONFIG_PROD` (base64 encoded)
- [ ] Create namespace: `kubectl create namespace helvex-prod`
- [ ] Create env secret (fill all values):
  ```bash
  kubectl create secret generic helvex-env \
    --from-literal=DATABASE_URL="postgresql://helvex:PASS@helvex-pg-rw:5432/helvex" \
    --from-literal=REDIS_URL="redis://:REDIS_PASS@helvex-redis-master:6379/0" \
    --from-literal=SECRET_KEY="$(openssl rand -hex 32)" \
    --from-literal=S3_ACCESS_KEY="..." \
    --from-literal=S3_SECRET_KEY="..." \
    -n helvex-prod
  ```
- [ ] Create GHCR pull secret:
  ```bash
  kubectl create secret docker-registry ghcr-pull-secret \
    --docker-server=ghcr.io \
    --docker-username=OWNER \
    --docker-password=GHCR_PAT \
    -n helvex-prod
  ```

### 4.5 GitHub Actions runner
- [ ] On `app1`, follow GitHub → Settings → Actions → Runners → New self-hosted runner (Linux)
- [ ] Add runner labels: `self-hosted`, `helvex-prod`
- [ ] Install as a systemd service so it survives reboots

### 4.6 First Helm deploy
- [ ] From local machine with kubeconfig set:
  ```bash
  cd infra
  helmfile -e prod apply
  ```
- [ ] Watch rollout: `kubectl -n helvex-prod get pods -w`
- [ ] Check cert-manager issued TLS: `kubectl -n helvex-prod get certificate`

### 4.7 Data migration
- [ ] `pg_dump -Fc -d postgresql://localhost:5432/zefix_analyzer > dump.pgc`
- [ ] Copy to control-plane: `scp dump.pgc root@app1:/tmp/`
- [ ] Restore into CloudNativePG:
  ```bash
  kubectl -n helvex-prod exec -it helvex-pg-1 -- bash
  pg_restore -d $DATABASE_URL /tmp/dump.pgc
  ```
- [ ] Verify row counts

### 4.8 Post-deploy validation
- [ ] Open `https://helvex.dicy.ch` — check TLS padlock
- [ ] Login and confirm dashboard loads
- [ ] Settings → Re-geocode all companies (validate the fix from Phase 1)
- [ ] Map → zoom in → confirm clusters and popups work
- [ ] Check Jobs page shows re-geocode job running


## Phase 5 - Extensions

### 5.1 Change Dashboard filtering
Move the filtering to the top of the page and make collapseable. Filters such as TF-IDF, Purpose and Claude (AI) can be freeform with autocomplete features as well as list the top 20 then show more. Also showing the number of companies with those keywords. Add an exclude feature (potentially press twice on a keyword). Add save view (potentially make the view a json format or similar so it can be saved per user and quickly ingested)

### 5.2 Extend LLM Search/AI Classification
Add ChatGPT integration for the search next to Claude. Allow the users to make more adjustments on how LLM classify the companies. 

### 5.3 Company Profile
General overhaul, which allows user to change the website if they think its incorrect (add logic to backend where webiste is changed if too many users switch). Add classifications from tf-idf cluster, purpose keywords and claude classification. 

### 5.4 Rename Zefix and Google Scoring
Rename both scoring to reflect their true purpose. Zefix should be Individual based scoring or Company purpose scoring. Google should be Web scoring (as webcrawlers might be added later which would be included in that scoring). 

### 5.5 Change review and Proposal categories
Have some generic defaults but allow users to create their own categories.

# 6 Make App more user customizable/specific
Such has having their own scoring logic, tags, custom categories, LLM scoring, running their own jobs. -> implement tiers, topups and ads. 

# 7 add multi Lang support 
Specifically DE/FR/IT

