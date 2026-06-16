#cloud-config
packages:
  - curl
  - netcat-openbsd
  - git

runcmd:
  - |
    mkdir -p /etc/rancher/k3s
    printf 'tls-san:\n  - %s\n' "${public_ip}" > /etc/rancher/k3s/config.yaml

    curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION="${k3s_version}" K3S_TOKEN="${token}" sh -s - server \
      --disable=servicelb \
      --node-ip=${private_ip} \
      --advertise-address=${private_ip} \
      --flannel-iface=enp7s0 \
      --tls-san=${public_ip} \
      --cluster-cidr=10.244.0.0/16 \
      --service-cidr=10.96.0.0/12 \
      --write-kubeconfig-mode=640 \
      --write-kubeconfig-group=k3s
  - |
    # Verify the installed k3s binary against the upstream release checksum.
    # INSTALL_K3S_VERSION above already pins the version (no 'latest' drift);
    # this additionally guards against a corrupted/tampered download.
    cd /tmp
    curl -sfLO "https://github.com/k3s-io/k3s/releases/download/${k3s_version}/sha256sum-amd64.txt"
    grep -E '\sk3s$' sha256sum-amd64.txt | sed 's#k3s$#/usr/local/bin/k3s#' | sha256sum -c -
  - groupadd -f k3s
  - useradd -m -s /bin/bash ubuntu || true
  - usermod -aG k3s ubuntu
  - |
    # Scoped sudo for the ubuntu admin user. kubectl/helm/helmfile do NOT need
    # root (kubeconfig below is chowned to ubuntu) — NOPASSWD ALL was strictly
    # more access than operations require, so this lists only what actually
    # needs root: managing the k3s service, reading its journal, and patching.
    cat > /etc/sudoers.d/ubuntu <<'SUDOERS'
    ubuntu ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart k3s, /usr/bin/systemctl start k3s, /usr/bin/systemctl stop k3s, /usr/bin/systemctl status k3s, /usr/bin/journalctl -u k3s, /usr/bin/journalctl -u k3s -f, /usr/bin/apt-get update, /usr/bin/apt-get upgrade -y, /usr/sbin/reboot
    SUDOERS
    chmod 440 /etc/sudoers.d/ubuntu
  - |
    # Defense-in-depth: restrict SSH login (and therefore sudo, since sudo
    # requires a login session first) to the same admin CIDRs already enforced
    # by the Hetzner Cloud Firewall. This means a firewall misconfiguration or
    # bypass alone isn't enough to reach this account.
    cat > /etc/security/access.conf <<'ACCESSCONF'
    %{ for cidr in admin_cidrs ~}
    + : ALL : ${cidr}
    %{ endfor ~}
    - : ALL : ALL
    ACCESSCONF
    if ! grep -q pam_access /etc/pam.d/sshd; then
      echo "account required pam_access.so accessfile=/etc/security/access.conf" >> /etc/pam.d/sshd
    fi
  - mkdir -p /home/ubuntu/.ssh
  - cp /root/.ssh/authorized_keys /home/ubuntu/.ssh/authorized_keys
  - chown -R ubuntu:ubuntu /home/ubuntu/.ssh
  - chmod 700 /home/ubuntu/.ssh && chmod 600 /home/ubuntu/.ssh/authorized_keys
  - |
    # Helm — fetch the release tarball directly and verify against the
    # upstream-published sha256sum file, instead of piping get-helm-3 to bash.
    HELM_VERSION=3.21.1
    cd /tmp
    curl -sfLO "https://get.helm.sh/helm-v$${HELM_VERSION}-linux-amd64.tar.gz"
    curl -sfLO "https://get.helm.sh/helm-v$${HELM_VERSION}-linux-amd64.tar.gz.sha256sum"
    sha256sum -c "helm-v$${HELM_VERSION}-linux-amd64.tar.gz.sha256sum"
    tar -xzf "helm-v$${HELM_VERSION}-linux-amd64.tar.gz"
    mv linux-amd64/helm /usr/local/bin/helm
    chmod +x /usr/local/bin/helm
  - |
    HELMFILE_VERSION=0.171.0
    cd /tmp
    curl -sfLO "https://github.com/helmfile/helmfile/releases/download/v$${HELMFILE_VERSION}/helmfile_$${HELMFILE_VERSION}_checksums.txt"
    curl -sfLO "https://github.com/helmfile/helmfile/releases/download/v$${HELMFILE_VERSION}/helmfile_$${HELMFILE_VERSION}_linux_amd64.tar.gz"
    grep "helmfile_$${HELMFILE_VERSION}_linux_amd64.tar.gz" "helmfile_$${HELMFILE_VERSION}_checksums.txt" | sha256sum -c -
    tar -xzf "helmfile_$${HELMFILE_VERSION}_linux_amd64.tar.gz" helmfile
    mv helmfile /usr/local/bin/helmfile
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
