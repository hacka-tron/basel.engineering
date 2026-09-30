#!/bin/bash
set -euxo pipefail

# One GiB of emergency swap for the 2 GiB k3s node.
if [ ! -f /swapfile ]; then
  dd if=/dev/zero of=/swapfile bs=1M count=1024 status=none
  chmod 600 /swapfile
  mkswap /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
swapon /swapfile

# Install the single-node server; tolerate active host swap on the kubelet.
# The k3s installer registers a systemd service.
curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="server --kubelet-arg=fail-swap-on=false" sh -

# Install only the Flux CLI. GitHub bootstrap is a separate deployment step.
curl -fsSL https://fluxcd.io/install.sh | bash

# ECR authorization tokens expire every 12 hours - the glassbox image is
# pulled from a private repository, so this must run on a recurring
# schedule, not just once here. Script content is the single source of
# truth at k8s/refresh-ecr-pull-secret.sh; kept in sync via Terraform's
# file() rather than duplicated by hand.
cat > /usr/local/bin/refresh-ecr-pull-secret.sh <<'ECR_REFRESH_SCRIPT'
${ecr_pull_secret_refresh_script}
ECR_REFRESH_SCRIPT
chmod 700 /usr/local/bin/refresh-ecr-pull-secret.sh

cat > /etc/systemd/system/refresh-ecr-pull-secret.service <<'UNIT'
[Unit]
Description=Refresh the ECR pull secret for glassbox workloads
[Service]
Type=oneshot
Environment=KUBECONFIG=/etc/rancher/k3s/k3s.yaml
ExecStart=/usr/local/bin/refresh-ecr-pull-secret.sh
UNIT

cat > /etc/systemd/system/refresh-ecr-pull-secret.timer <<'UNIT'
[Unit]
Description=Run refresh-ecr-pull-secret every 6 hours
[Timer]
OnBootSec=2min
OnUnitActiveSec=6h
[Install]
WantedBy=timers.target
UNIT

systemctl daemon-reload
systemctl enable --now refresh-ecr-pull-secret.timer
