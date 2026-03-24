# Helvex – Work Plan

## Phase 1 — Quick fixes (30 min)

### 1.1 Settings buttons broken
**Root cause:** `handleTrigger` has no error handling and no loading/feedback state — if the API call fails (or the user sees nothing happen), there's no indication. Additionally the boilerplate `<form>` is nested inside the main settings `<form>`, which is invalid HTML (browsers silently ignore inner forms).

**Fix:**
- [ ] Extract the boilerplate add-form outside the outer `<form>` tag (move it below the closing `</form>`)
- [ ] Add per-button loading state + toast/inline feedback (e.g. "Job queued → redirecting to Jobs…" or show error text on failure)
- [ ] Wrap `triggerJob` calls in try/catch so errors surface visibly

### 1.2 Settings UX – make action buttons pop
- [ ] Style the three trigger buttons with distinct color-coded icons (Zefix → blue, Google → green, Re-geocode → amber) and add a spinner while in-flight
- [ ] Add a small toast banner at the top on success ("Re-geocode job queued") or error ("Failed: …")

---

## Phase 2 — Company detail page (45 min)

Current state: functional but visually flat — all slate/gray, score bars have no numbers, no map, no direct Zefix link.

### 2.1 Header
- [ ] Add a colored left-border accent or gradient hero strip based on `combined_score` (green → strong match, amber → medium, red → weak)
- [ ] Show `combined_score` as a large number (e.g. "82 / 100") next to the name
- [ ] Add "View on Zefix" link using `https://www.zefix.ch/en/search/entity/list/firm/{uid}` alongside the website link

### 2.2 Scores card — make colorful
- [ ] Replace plain `ScoreBar` with a styled card for each score:
  - Show the numeric value (e.g. "74") in bold with color (green ≥ 70, amber 40–69, red < 40)
  - Keep the bar but color-code it (green/amber/red fill)
- [ ] Add a small "last scored" badge with relative time (e.g. "3 days ago")

### 2.3 Company info card
- [ ] Show a mini Leaflet map (static or interactive) if `lat`/`lon` are present — same map component reused from the map page
- [ ] Highlight `status = "cancelled"` in red
- [ ] Make `purpose` text expandable (truncated to 3 lines with "Show more" toggle)

### 2.4 Status card — color coded selects
- [ ] Replace plain `<select>` with colored pill/badge buttons for review status (row of clickable badges instead of a dropdown)
- [ ] Show a loading spinner overlay while `saving`

### 2.5 Notes
- [ ] Add a character counter on the textarea
- [ ] Show user name / avatar placeholder next to each note

---

## Phase 3 — Dashboard UX & color (45 min)

### 3.1 Stats bar — more colorful
- [ ] Add colored icon to each stat (e.g. a green checkmark for "Confirmed proposal", yellow lightning for "Interesting")
- [ ] Use colored pill backgrounds instead of just colored text (e.g. `bg-green-100` for confirmed proposal stat)
- [ ] Highlight the currently active filter stat with a solid border/underline

### 3.2 Company table — visual scan-ability
- [ ] Color-code the Review badge cells (already done for badges, but row highlight is just `bg-blue-50`) — add a 3px colored left border per review status:
  - confirmed_proposal → green left border
  - potential_proposal → blue
  - interesting → amber
  - rejected → red
  - pending → transparent
- [ ] Show `combined_score` as a colored number+bar instead of just bar
- [ ] Dim rows for cancelled companies

### 3.3 Preview panel — richer
- [ ] Show company initials avatar (colored circle with first letter) in the header
- [ ] Add quick-action buttons inline: "Mark interesting", "Mark rejected", "Mark confirmed" — single-click status change without opening full profile
- [ ] Show map pin mini-icon + municipality more prominently

### 3.4 Filter sidebar — discoverability
- [ ] Add section headers with counts (e.g. "Canton (12 options)")
- [ ] Make active filters visually prominent (chip strip at top showing active filters with × to remove)

---

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
  - `admin_cidrs` — your current public IP + `/32` (run `curl ifconfig.me`)
  - `ssh_keys` — names of SSH keys already uploaded in Hetzner Console
  - `k3s_token` — generate with `openssl rand -hex 32`
- [ ] Replace all `OWNER` placeholders in `infra/environments/prod.yaml` and `.github/workflows/build.yml` with actual GitHub username/org

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
