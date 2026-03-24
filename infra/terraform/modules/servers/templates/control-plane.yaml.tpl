#cloud-config
packages:
  - curl
  - netcat-openbsd

runcmd:
  - |
    curl -sfL https://get.k3s.io | K3S_TOKEN="${token}" sh -s - server \
      --disable=servicelb \
      --node-ip=${private_ip} \
      --advertise-address=${private_ip} \
      --flannel-iface=enp7s0 \
      --cluster-cidr=10.244.0.0/16 \
      --service-cidr=10.96.0.0/12 \
      --write-kubeconfig-mode=640 \
      --write-kubeconfig-group=k3s
  - groupadd -f k3s
  - usermod -aG k3s ubuntu
