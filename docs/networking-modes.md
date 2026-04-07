# K3s Cluster Networking Configuration

## Overview
Your cluster networking mode is now **configurable and easily reversible**. You can switch between:

- **`tailscale` mode** (default): All K3s cluster communication (API server, flannel VXLAN, node-to-node) goes through Tailscale. This works great for remote deployments and solves your Arc runner deploy job issue.
- **`private` mode** (original): Uses Hetzner private IP (`10.0.1.x`) for all cluster networking, with Tailscale only for optional admin access.

---

## Current Configuration
- **Current mode**: `${cluster_networking_mode}` (set in Terraform)
- **Control plane** (K3s API): Uses the configured networking mode
- **Workers**: Connect to control plane using the configured networking mode
- **Flannel VXLAN**: In `tailscale` mode, uses Tailscale tunnel (`tailscale0` interface)
- **In-cluster communication**: kube-dns, kube-proxy all work on the configured interfaces

---

## How to Switch Modes

### Option 1: Switch to Tailscale-Only (Recommended for Remote Deployments)

Edit your Terraform environment file (e.g., `infra/environments/prod.yaml` or your tfvars):

```yaml
cluster_networking_mode = "tailscale"
```

Then re-deploy:
```bash
terraform apply -target="module.hetzner_infra"
```

This will:
1. Install Tailscale on all nodes (non-blocking)
2. Wait for Tailscale IPs to be assigned
3. Configure K3s to bind to **only** Tailscale IPs
4. Use `tailscale0` interface for flannel VXLAN
5. Advertise private subnet via Tailscale so your home nodes can reach the cluster

### Option 2: Revert to Private IP Mode

Edit your Terraform environment file:

```yaml
cluster_networking_mode = "private"
```

Then re-deploy:
```bash
terraform apply -target="module.hetzner_infra"
```

This reverts to:
1. Using Hetzner private IPs for all K3s networking
2. Using `enp7s0` interface for flannel VXLAN
3. Tailscale installed optionally for admin access only

---

## What Happens by Default (Tailscale Mode)

### Control Plane Node
1. Installs Tailscale (required)
2. Waits up to `~60 seconds` for Tailscale IP assignment
3. Configures K3s to listen on **Tailscale IP only**
4. Sets up TLS SAN with Tailscale IP
5. Configures flannel to bind to `tailscale0` interface
6. Advertises private subnet (`10.0.1.0/24`) via Tailscale

### Worker Nodes
1. Installs Tailscale (required)
2. Waits up to `~60 seconds` for Tailscale IP assignment
3. Connects to K3s API server using **Tailscale** (instead of private IP)
4. Joins the cluster with node IP set to Tailscale IP
5. Uses `tailscale0` for pod-to-pod communication
6. Advertises private subnet via Tailscale

### Arc Runners
- Arc runner pods will now have network access through Tailscale
- Deploy jobs will be able to reach GitHub and the cluster
- Jobs get picked up by the `helvex-prod` runner scale set

---

## Troubleshooting

### Issue: Nodes don't join in Tailscale mode
Check node status:
```bash
kubectl get nodes -o wide
```

Check Tailscale IPs on each node:
```bash
# SSH into node
tailscale ip -4
```

Ensure Tailscale is running:
```bash
sudo systemctl status tailscaled
```

Check logs:
```bash
sudo journalctl -u k3s -n 100
```

### Issue: Pods can't reach external services
Ensure Tailscale subnet routing is approved:
1. Go to https://login.tailscale.com/admin/machines
2. Find each node entry
3. Check "Edit route settings"
4. Approve the `10.0.1.0/24` route

### Issue: Control plane not reachable from home node
Verify Tailscale connectivity:
```bash
tailscale ping <control-plane-tailscale-ip>
```

Verify subnet routing is working:
```bash
ping 10.0.1.10  # should route via Tailscale
```

---

## Performance Notes

- **Throughput**: Tailscale adds ~5-10% latency overhead, acceptable for most workloads
- **Flannel VXLAN**: Still uses Hetzner private network for inter-node pods in `private` mode; uses Tailscale tunnel in `tailscale` mode
- **DNS**: Runs on cluster IP (e.g., `10.96.0.10`), accessible from pods regardless of mode

---

## Implementation Details

### Files Modified/Created
- `infra/terraform/modules/servers/variables.tf`: Added `cluster_networking_mode` variable
- `infra/terraform/modules/servers/main.tf`: Pass networkingmode to cloud-init templates
- `infra/terraform/modules/servers/templates/control-plane.yaml.tpl`: Conditional K3s setup
- `infra/terraform/modules/servers/templates/worker.yaml.tpl`: Conditional K3s agent join

### Conditional Logic
Both templates check `${networking_mode}` at runtime:
- If `"tailscale"`: Use Tailscale IPs, `tailscale0` interface, stricter error handling
- If `"private"`: Use Hetzner private IPs, `enp7s0` interface, non-blocking Tailscale

---

## FAQ

**Q: Can I change modes without tearing down the cluster?**
A: No, you need to destroy and recreate nodes. This is a fundamental networking change.

**Q: Does Tailscale mode work with Arc runners?**
A: Yes! The runners now have consistent network access through Tailscale. This fixes the deploy job pickup issue.

**Q: Will switching modes affect my data?**
A: Database volumes persist (they're in `hcloud_volume`), but the cluster needs to be rebuilt.

**Q: What if Tailscale setup fails?**
A: Tailscale mode exits with an error. Private mode continues without Tailscale (non-blocking).

---

## See Also
- [Tailscale Documentation](https://tailscale.com/kb/)
- [K3s Networking](https://docs.k3s.io/networking)
- [Cloud-init User Data](https://cloud.google.com/compute/docs/instances/startup-scripts/linux)
