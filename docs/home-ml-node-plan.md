# Home ML Node Plan (k3s + KEDA)

This runbook is optimized for quick LLM parsing and execution support.
It assumes this repository's current architecture:
- ML jobs are routed to queue `helvex-ml`
- ML worker is a dedicated deployment
- KEDA scales ML worker pods from Redis queue depth
- Node-level fallback is handled by Kubernetes scheduling + node autoscaler (not KEDA itself)

## 0) Goal

Use a home server as preferred compute for ML jobs, while keeping cloud fallback available when the home node is unavailable.

## 1) Scope and non-goals

### In scope
- Add home server as k3s worker node
- Prefer home node for ML workloads
- Keep cloud fallback possible
- Define recurring operational routine
- Define safety checks and rollback

### Not in scope
- Re-architecting queue system to per-user ML nodes (optional later)
- Replacing KEDA with another platform right now

## 2) High-level architecture

### Responsibilities
- KEDA: scales ML pods (0 -> 1) based on Redis queue depth
- Scheduler + affinity/taints: places pod on home vs cloud node
- Node autoscaler (optional): creates cloud fallback nodes when no suitable node exists

### Key idea
Use:
1. `required` constraint for ML-capable nodes (`workload=ml`)
2. `preferred` affinity for home location (`location=home`)
3. Cloud nodes also labeled `workload=ml` and `location=cloud`

This gives home-first, cloud-fallback behavior.

## 3) Pre-flight checklist

- [ ] Home server has stable Linux install and enough RAM/CPU for ML jobs
- [ ] Private networking available between cluster and worker node(s)
- [ ] Firewall rules restricted to required k3s control-plane traffic only
- [ ] Time sync (NTP) and DNS are stable on home node
- [ ] Home node has reliable disk and swap policy understood
- [ ] You can tolerate queued jobs if home node is temporarily unavailable

## 4) Phase A - Add home server as k3s worker

### Steps
1. Prepare home host OS updates, container runtime prerequisites, and hostname.
2. Join as k3s agent using secure token and private network path.
3. Verify node is `Ready`.
4. Add labels and taints:
   - Label: `workload=ml`
   - Label: `location=home`
   - Optional taint: `workload=ml:NoSchedule` (forces explicit toleration)
   - Do not introduce a second ML taint key unless every ML workload tolerates it; keep the scheduling contract to one canonical ML taint.

### Validate
- `kubectl get nodes -o wide`
- `kubectl describe node <home-node-name>`
- Confirm labels and taints appear exactly as expected

## 5) Phase B - Define cloud fallback node class

### Steps
1. Ensure at least one cloud node profile/group exists for ML fallback.
2. Cloud fallback nodes must include:
   - Label: `workload=ml`
   - Label: `location=cloud`
3. If using taints, ensure ML worker tolerates the same taint key/effect.
   - Avoid legacy taints such as `helvex.io/role=ml-worker:NoSchedule` on newly provisioned ML nodes unless the deployment has been updated to tolerate them.

### Validate
- Existing cloud nodes show proper labels
- Or autoscaled nodes come up with correct labels at creation

## 6) Phase C - Scheduling policy (home preferred, cloud fallback)

Use all three controls:

1. `nodeSelector` or required node affinity for `workload=ml`
2. preferred node affinity for `location=home`
3. tolerations matching ML taints

### Why this combination
- Required `workload=ml`: never place ML worker on web/db nodes
- Preferred `location=home`: scheduler tries home first
- Cloud fallback: if home unavailable, cloud still satisfies required rule

## 7) Phase D - KEDA behavior

Keep KEDA as the pod scaler for ML queue.

### Desired behavior
- Queue empty -> ML replicas 0
- Queue has jobs -> scale to 1
- Scheduler picks home node first due to preference
- If no home node is schedulable, cloud node can host pod (if available/created)

### Watchouts
- KEDA cannot create nodes by itself
- If no node matches constraints, pod remains Pending
- Node autoscaler must be present for true zero-node cloud fallback

## 8) Phase E - Optional queue split by user/tier

Do this only if you need deterministic user-to-node routing.

### Pattern
- Queue A: `helvex-ml-premium` (home-preferred)
- Queue B: `helvex-ml-standard` (cloud-only or lower-priority)
- Separate worker deployments + separate KEDA ScaledObjects per queue
- Route jobs at enqueue-time by org tier/user policy

### When to adopt
- Strict SLO differences by customer tier
- Strong cost isolation between user groups

## 9) Operational routine (regular use)

### Daily/normal
- Keep home node online
- Let KEDA scale ML pod on demand
- Monitor queue lag and Pending pods

### Planned home downtime
1. `kubectl cordon <home-node>`
2. `kubectl drain <home-node> --ignore-daemonsets --delete-emptydir-data`
3. Shut down home server
4. Confirm ML jobs schedule to cloud fallback (or queue until available)

### Resume
1. Power on home server
2. Ensure node returns `Ready`
3. `kubectl uncordon <home-node>`
4. Verify next ML pod lands on home node again

## 10) What to watch out for

## Networking and security
- Do not expose cluster API broadly to internet
- Use private mesh/VPN between cloud and home
- Rotate join tokens and limit blast radius

## Scheduling pitfalls
- Typos in labels/taints cause silent Pending pods
- Overly strict required affinity can block fallback
- Missing toleration blocks scheduling even if labels are correct

## Data and performance
- Home uplink bandwidth may become bottleneck for large payloads
- Home power/network instability can elongate queue latency
- Large memory spikes in clustering jobs can OOM home node

## Cost and operations
- Autoscaler misconfiguration can create unnecessary cloud nodes
- Lack of queue alarms hides user-facing delays
- No drain process before shutdown can interrupt active jobs

## 11) Monitoring and alerting minimum set

- Alert: `ml-worker` Pending for more than N minutes
- Alert: `helvex-ml` queue length above threshold for N minutes
- Alert: home node NotReady
- Dashboard: queue depth, job duration, retries, OOM kills

## 12) Rollback plan

If instability appears:
1. Remove home preference affinity
2. Force ML worker to cloud nodes only
3. Disable home node scheduling via taint or cordon
4. Keep KEDA unchanged
5. Re-test end-to-end enqueue and completion

## 13) Automation roadmap

### Level 1 (quick wins)
- Script node label/taint bootstrap
- Script cordon/drain/uncordon lifecycle
- Add health checks in CI/CD post-deploy

### Level 2
- Add autoscaler cloud fallback with min=0, max=N
- Add alerts for queue lag and Pending state

### Level 3
- Add queue sharding for per-tier or per-user compute routing
- Add policy tests to prevent drift in labels/taints/affinity

## 14) Acceptance criteria

- [ ] ML pod scales from 0 when queue receives job
- [ ] With home node healthy, ML pod schedules to home node
- [ ] With home node unavailable, ML job still runs on cloud fallback
- [ ] No ML pod schedules to web/db-only nodes
- [ ] Planned shutdown/resume procedure works without manual firefighting

## 15) Decision log template

Use this section to track implementation decisions.

- Date:
- Decision:
- Reason:
- Tradeoff:
- Follow-up:

## 16) Suggested next implementation task in this repo

1. Extend Helm values/template to support ML `affinity` in addition to existing `nodeSelector`/`tolerations`.
2. Configure prod values for:
   - required `workload=ml`
   - preferred `location=home`
   - tolerations for ML taints
3. (Optional) Introduce second ML queue and worker deployment for tier-based routing.
