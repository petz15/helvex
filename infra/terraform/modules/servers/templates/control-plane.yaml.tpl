#cloud-config
packages:
  - curl
  - netcat-openbsd
  - git

runcmd:
  - |
    curl -sfL https://get.k3s.io | K3S_TOKEN="${token}" sh -s - server \
      --disable=servicelb \
      --node-ip=${private_ip} \
      --advertise-address=${private_ip} \
      --flannel-iface=enp7s0 \
      --tls-san=${public_ip} \
      --cluster-cidr=10.244.0.0/16 \
      --service-cidr=10.96.0.0/12 \
      --write-kubeconfig-mode=640 \
      --write-kubeconfig-group=k3s
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
