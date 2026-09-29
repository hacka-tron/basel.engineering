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
