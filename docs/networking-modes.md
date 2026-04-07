# K3s Networking Model

## Overview

Cluster networking is now standardized to Hetzner private networking.
There is no overlay VPN dependency in infrastructure provisioning.

- Control plane API binds on private IP (for node join) and public IP TLS SAN (for admin access)
- Worker nodes join via private IP (`10.0.1.x`)
- Flannel VXLAN uses `enp7s0`

## Operational model

- `app1 <-> db1`: private subnet traffic only
- Additional ML workers: same private subnet, labeled and tainted for `workload=ml`
- SSH administration: public IP of `app1`

## Adding ML capacity

Use the provisioning helper:

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

## Autoscaling

Pod autoscaling for ML workers is supported via KEDA.
To enable:

1. Install KEDA in the cluster.
2. Set `mlWorker.keda.enabled: true` in `infra/environments/prod.yaml`.
3. Keep ML scheduling constraints:
   - `nodeSelector: workload=ml`
   - `toleration: workload=ml:NoSchedule`

For true node-level autoscaling, pair KEDA with a Hetzner cluster autoscaler node group dedicated to `workload=ml`.
