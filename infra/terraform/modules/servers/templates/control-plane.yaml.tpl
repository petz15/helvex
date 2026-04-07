#cloud-config
packages:
  - curl
  - netcat-openbsd
  - git

runcmd:
  - |
    # Install Tailscale, join, then install k3s with Tailscale IP in TLS SAN.
    # Tailscale is non-fatal: if it fails or takes too long, k3s still installs normally.
    curl -fsSL https://tailscale.com/install.sh | sh
    tailscale up --authkey="${tailscale_auth_key}" --hostname="${node_name}" || true

    # Wait up to 30 s for Tailscale to assign an IP
    TAILSCALE_IP=""
    for i in $(seq 1 6); do
      TAILSCALE_IP=$(tailscale ip -4 2>/dev/null || true)
      [ -n "$TAILSCALE_IP" ] && break
      sleep 5
    done

    # Advertise the Hetzner private subnet so home nodes can reach cluster IPs via Tailscale.
    # Requires one-time approval in admin.tailscale.com → Machines → Edit route settings.
    [ -n "$TAILSCALE_IP" ] && tailscale set --advertise-routes=${subnet_cidr} || true

    # Write k3s config.yaml with Tailscale IP in TLS SAN (only if IP is available)
    mkdir -p /etc/rancher/k3s
    if [ -n "$TAILSCALE_IP" ]; then
      printf 'tls-san:\n  - %s\n  - %s\n' "${public_ip}" "$TAILSCALE_IP" > /etc/rancher/k3s/config.yaml
    fi

    # Install k3s — add Tailscale TLS SAN flag only if IP is available.
    # Always use private IP as advertise-address so flannel binds to the Hetzner private NIC.
    # This keeps app1↔db1 flannel VXLAN on the Hetzner private network (no Tailscale dependency).
    # The home node connects to the API server via the Tailscale TLS SAN, not advertise-address.
    TLS_SAN_FLAGS="--tls-san=${public_ip}"
    [ -n "$TAILSCALE_IP" ] && TLS_SAN_FLAGS="$TLS_SAN_FLAGS --tls-san=$TAILSCALE_IP"

    curl -sfL https://get.k3s.io | K3S_TOKEN="${token}" sh -s - server \
      --disable=servicelb \
      --node-ip=${private_ip} \
      --advertise-address=${private_ip} \
      --flannel-iface=enp7s0 \
      $TLS_SAN_FLAGS \
    - |
      # ============= NETWORKING MODE: ${networking_mode} =============
      # This variable controls whether the cluster uses Tailscale IPs or Hetzner private IPs.
      # To switch modes, update the cluster_networking_mode variable in terraform.
      # ================================================================
      --cluster-cidr=10.244.0.0/16 \
      if [ "${networking_mode}" = "tailscale" ]; then
        # TAILSCALE MODE: Use only Tailscale for all K3s networking
        # Install Tailscale with higher priority
        curl -fsSL https://tailscale.com/install.sh | sh || { echo "Tailscale install failed"; sleep 5; }
        tailscale up --authkey="${tailscale_auth_key}" --hostname="${node_name}" || { echo "Tailscale up failed"; sleep 5; }
      
        # Wait up to 60s for Tailscale IP (more generous timeout for reliability)
        TAILSCALE_IP=""
        for i in $(seq 1 12); do
          TAILSCALE_IP=$(tailscale ip -4 2>/dev/null || true)
          if [ -n "$TAILSCALE_IP" ]; then
            echo "Tailscale IP assigned: $TAILSCALE_IP"
            break
          fi
          echo "Waiting for Tailscale IP... ($i/12)"
          sleep 5
        done
      
        if [ -z "$TAILSCALE_IP" ]; then
          echo "ERROR: Tailscale IP not assigned after 60s. Cluster may not be accessible."
          exit 1
        fi
      
        # Advertise private subnet for home-node access
        tailscale set --advertise-routes=${subnet_cidr} || true
      
        # Use ONLY Tailscale IP for API server binding and TLS SAN
        mkdir -p /etc/rancher/k3s
        printf 'tls-san:\n  - %s\nnode-ip: %s\nadvertise-address: %s\n' "$TAILSCALE_IP" "$TAILSCALE_IP" "$TAILSCALE_IP" > /etc/rancher/k3s/config.yaml
      
        curl -sfL https://get.k3s.io | K3S_TOKEN="${token}" sh -s - server \
          --disable=servicelb \
          --node-ip=$TAILSCALE_IP \
          --advertise-address=$TAILSCALE_IP \
          --flannel-iface=tailscale0 \
          --tls-san=$TAILSCALE_IP \
          --cluster-cidr=10.244.0.0/16 \
          --service-cidr=10.96.0.0/12 \
          --write-kubeconfig-mode=640 \
          --write-kubeconfig-group=k3s
      else
        # PRIVATE IP MODE: Use Hetzner private IPs (original behavior)
        # Install Tailscale for admin access only
        curl -fsSL https://tailscale.com/install.sh | sh || true
        tailscale up --authkey="${tailscale_auth_key}" --hostname="${node_name}" || true
      
        # Wait up to 30s for Tailscale to assign an IP (non-critical path)
        TAILSCALE_IP=""
        for i in $(seq 1 6); do
          TAILSCALE_IP=$(tailscale ip -4 2>/dev/null || true)
          [ -n "$TAILSCALE_IP" ] && break
          sleep 5
        done
      
        # Advertise the Hetzner private subnet so home nodes can reach cluster IPs via Tailscale.
        [ -n "$TAILSCALE_IP" ] && tailscale set --advertise-routes=${subnet_cidr} || true
      
        # Use private IP with optional Tailscale TLS SAN
        mkdir -p /etc/rancher/k3s
        if [ -n "$TAILSCALE_IP" ]; then
          printf 'tls-san:\n  - %s\n  - %s\n' "${public_ip}" "$TAILSCALE_IP" > /etc/rancher/k3s/config.yaml
        fi
      
        TLS_SAN_FLAGS="--tls-san=${public_ip}"
        [ -n "$TAILSCALE_IP" ] && TLS_SAN_FLAGS="$TLS_SAN_FLAGS --tls-san=$TAILSCALE_IP"
      
        curl -sfL https://get.k3s.io | K3S_TOKEN="${token}" sh -s - server \
          --disable=servicelb \
          --node-ip=${private_ip} \
          --advertise-address=${private_ip} \
          --flannel-iface=enp7s0 \
          $TLS_SAN_FLAGS \
          --cluster-cidr=10.244.0.0/16 \
          --service-cidr=10.96.0.0/12 \
          --write-kubeconfig-mode=640 \
          --write-kubeconfig-group=k3s
      fi
      --service-cidr=10.96.0.0/12 \
      --write-kubeconfig-mode=640 \
      --write-kubeconfig-group=k3s
      # ============= NETWORKING MODE: ${networking_mode} =============
      # This variable controls whether the cluster uses Tailscale IPs or Hetzner private IPs.
      # To switch modes, update the cluster_networking_mode variable in terraform.
      # ================================================================

      if [ "${networking_mode}" = "tailscale" ]; then
        # TAILSCALE MODE: Use only Tailscale for all K3s networking
        # Install Tailscale with higher priority
        curl -fsSL https://tailscale.com/install.sh | sh || { echo "Tailscale install failed"; sleep 5; }
        tailscale up --authkey="${tailscale_auth_key}" --hostname="${node_name}" || { echo "Tailscale up failed"; sleep 5; }
      
        # Wait up to 60s for Tailscale IP (more generous timeout for reliability)
        TAILSCALE_IP=""
        for i in $(seq 1 12); do
          TAILSCALE_IP=$(tailscale ip -4 2>/dev/null || true)
          if [ -n "$TAILSCALE_IP" ]; then
            echo "Tailscale IP assigned: $TAILSCALE_IP"
            break
          fi
          echo "Waiting for Tailscale IP... ($i/12)"
          sleep 5
        done
      
        if [ -z "$TAILSCALE_IP" ]; then
          echo "ERROR: Tailscale IP not assigned after 60s. Cluster may not be accessible."
          exit 1
        fi
      
        # Advertise private subnet for home-node access
        tailscale set --advertise-routes=${subnet_cidr} || true
      
        # Use ONLY Tailscale IP for API server binding and TLS SAN
        mkdir -p /etc/rancher/k3s
        printf 'tls-san:\n  - %s\nnode-ip: %s\nadvertise-address: %s\n' "$TAILSCALE_IP" "$TAILSCALE_IP" "$TAILSCALE_IP" > /etc/rancher/k3s/config.yaml
      
        curl -sfL https://get.k3s.io | K3S_TOKEN="${token}" sh -s - server \
          --disable=servicelb \
          --node-ip=$TAILSCALE_IP \
          --advertise-address=$TAILSCALE_IP \
          --flannel-iface=tailscale0 \
          --tls-san=$TAILSCALE_IP \
          --cluster-cidr=10.244.0.0/16 \
          --service-cidr=10.96.0.0/12 \
          --write-kubeconfig-mode=640 \
          --write-kubeconfig-group=k3s
      else
        # PRIVATE IP MODE: Use Hetzner private IPs (original behavior)
        # Install Tailscale for admin access only
        curl -fsSL https://tailscale.com/install.sh | sh || true
        tailscale up --authkey="${tailscale_auth_key}" --hostname="${node_name}" || true
      
        # Wait up to 30s for Tailscale to assign an IP (non-critical path)
        TAILSCALE_IP=""
        for i in $(seq 1 6); do
          TAILSCALE_IP=$(tailscale ip -4 2>/dev/null || true)
          [ -n "$TAILSCALE_IP" ] && break
          sleep 5
        done
      
        # Advertise the Hetzner private subnet so home nodes can reach cluster IPs via Tailscale.
        [ -n "$TAILSCALE_IP" ] && tailscale set --advertise-routes=${subnet_cidr} || true
      
        # Use private IP with optional Tailscale TLS SAN
        mkdir -p /etc/rancher/k3s
        if [ -n "$TAILSCALE_IP" ]; then
          printf 'tls-san:\n  - %s\n  - %s\n' "${public_ip}" "$TAILSCALE_IP" > /etc/rancher/k3s/config.yaml
        fi
      
        TLS_SAN_FLAGS="--tls-san=${public_ip}"
        [ -n "$TAILSCALE_IP" ] && TLS_SAN_FLAGS="$TLS_SAN_FLAGS --tls-san=$TAILSCALE_IP"
      
        curl -sfL https://get.k3s.io | K3S_TOKEN="${token}" sh -s - server \
          --disable=servicelb \
          --node-ip=${private_ip} \
          --advertise-address=${private_ip} \
          --flannel-iface=enp7s0 \
          $TLS_SAN_FLAGS \
          --cluster-cidr=10.244.0.0/16 \
          --service-cidr=10.96.0.0/12 \
          --write-kubeconfig-mode=640 \
          --write-kubeconfig-group=k3s
      fi
  - groupadd -f k3s
  - useradd -m -s /bin/bash ubuntu || true
  - usermod -aG k3s ubuntu
  - usermod -aG sudo ubuntu
  - echo "ubuntu ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/ubuntu
  - mkdir -p /home/ubuntu/.ssh
  - cp /root/.ssh/authorized_keys /home/ubuntu/.ssh/authorized_keys
  - chown -R ubuntu:ubuntu /home/ubuntu/.ssh
  - chmod 700 /home/ubuntu/.ssh && chmod 600 /home/ubuntu/.ssh/authorized_keys
  - curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
  - |
    HELMFILE_VERSION=0.171.0
    curl -Lo /tmp/helmfile.tar.gz https://github.com/helmfile/helmfile/releases/download/v$${HELMFILE_VERSION}/helmfile_$${HELMFILE_VERSION}_linux_amd64.tar.gz
    tar -xzf /tmp/helmfile.tar.gz -C /tmp
    mv /tmp/helmfile /usr/local/bin/helmfile
    chmod +x /usr/local/bin/helmfile
  - git clone https://github.com/petz15/helvex.git /opt/helvex
  - chown -R ubuntu:ubuntu /opt/helvex
  - |
    # Set up kubeconfig for ubuntu user
    mkdir -p /home/ubuntu/.kube
    cp /etc/rancher/k3s/k3s.yaml /home/ubuntu/.kube/config
    chown ubuntu:ubuntu /home/ubuntu/.kube/config
    chmod 600 /home/ubuntu/.kube/config
    echo 'export KUBECONFIG=$HOME/.kube/config' >> /home/ubuntu/.bashrc
  - su -s /bin/bash ubuntu -c "helm plugin install https://github.com/databus23/helm-diff"
  - |
    # Configure Traefik to bind hostPort 80/443 (required since servicelb is disabled)
    until kubectl get deploy traefik -n kube-system &>/dev/null; do sleep 5; done
    kubectl apply -f - <<MANIFEST
    apiVersion: helm.cattle.io/v1
    kind: HelmChartConfig
    metadata:
      name: traefik
      namespace: kube-system
    spec:
      valuesContent: |-
        ports:
          web:
            hostPort: 80
          websecure:
            hostPort: 443
    MANIFEST
