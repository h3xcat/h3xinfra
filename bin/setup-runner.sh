#!/bin/bash
# Setup network connectivity for GitHub Actions workflows
set -euo pipefail
IFS=$'\n\t'

# WireGuard network configuration variables
WIREGUARD_CLIENT_IP="${WIREGUARD_CLIENT_IP}"
WIREGUARD_GATEWAY_IP="${WIREGUARD_GATEWAY_IP}"
WIREGUARD_NETWORK_CIDR="${WIREGUARD_NETWORK_CIDR}"
WIREGUARD_PEER_PUBLIC_KEY="${WIREGUARD_PEER_PUBLIC_KEY}"
WIREGUARD_ENDPOINT="${WIREGUARD_ENDPOINT}"

# Check if running in GitHub Actions environment
if [ "${GITHUB_ACTIONS:-}" != "true" ]; then
  echo "Error: This script must be run in a GitHub Actions runner environment."
  echo "The GITHUB_ACTIONS environment variable is not set to 'true'."
  exit 1
fi

# Check for runner temp directory
if [ -z "${RUNNER_TEMP:-}" ]; then
  echo "Error: RUNNER_TEMP environment variable is not set."
  echo "This is required for storing temporary WireGuard keys."
  exit 1
fi

# Check for required environment variables
if [ -z "${WIREGUARD_CLIENT_IP:-}" ]; then
  echo "Error: WIREGUARD_CLIENT_IP environment variable is not set."
  exit 1
fi

if [ -z "${WIREGUARD_GATEWAY_IP:-}" ]; then
  echo "Error: WIREGUARD_GATEWAY_IP environment variable is not set."
  exit 1
fi

if [ -z "${WIREGUARD_NETWORK_CIDR:-}" ]; then
  echo "Error: WIREGUARD_NETWORK_CIDR environment variable is not set."
  exit 1
fi

if [ -z "${WIREGUARD_PEER_PUBLIC_KEY:-}" ]; then
  echo "Error: WIREGUARD_PEER_PUBLIC_KEY environment variable is not set."
  exit 1
fi

if [ -z "${WIREGUARD_ENDPOINT:-}" ]; then
  echo "Error: WIREGUARD_ENDPOINT environment variable is not set."
  exit 1
fi

if [ -z "${WIREGUARD_PRIVATE_KEY:-}" ]; then
  echo "Error: WIREGUARD_PRIVATE_KEY environment variable is not set."
  exit 1
fi

if [ -z "${WIREGUARD_PRESHARED_KEY:-}" ]; then
  echo "Error: WIREGUARD_PRESHARED_KEY environment variable is not set."
  exit 1
fi

if [ -z "${SSH_KNOWN_HOSTS:-}" ]; then
  echo "Error: SSH_KNOWN_HOSTS environment variable is not set."
  exit 1
fi

# Check for required environment variables
if [ -z "${ANSIBLE_VAULT_PASSWORD:-}" ]; then
  echo "Error: ANSIBLE_VAULT_PASSWORD environment variable is not set."
  exit 1
fi

if [ -z "${SSH_PRIVATE_KEY:-}" ]; then
  echo "Error: SSH_PRIVATE_KEY environment variable is not set."
  exit 1
fi

# Install WireGuard only if not already installed
if ! command -v wg &> /dev/null; then
  echo "Installing WireGuard..."
  sudo apt-get update
  sudo apt-get install -y wireguard
else
  echo "WireGuard is already installed, skipping installation."
fi

# Set up WireGuard
sudo ip link add dev wg0 type wireguard
sudo ip address add dev wg0 ${WIREGUARD_NETWORK_CIDR}

# Create temporary key files
echo "${WIREGUARD_PRIVATE_KEY}" > "${RUNNER_TEMP}/wg_privatekey"
echo "${WIREGUARD_PRESHARED_KEY}" > "${RUNNER_TEMP}/wg_presharedkey"

# Configure WireGuard
sudo wg set wg0 \
  private-key "${RUNNER_TEMP}/wg_privatekey" \
  peer ${WIREGUARD_PEER_PUBLIC_KEY} \
  preshared-key "${RUNNER_TEMP}/wg_presharedkey" \
  endpoint ${WIREGUARD_ENDPOINT} \
  persistent-keepalive 5 \
  allowed-ips 0.0.0.0/0

# Activate the interface
sudo ip link set up dev wg0

# Set up routing
sudo ip route replace 10.0.0.0/8 via ${WIREGUARD_GATEWAY_IP} dev wg0
sudo ip route replace 172.16.0.0/12 via ${WIREGUARD_GATEWAY_IP} dev wg0
sudo ip route replace 192.168.0.0/16 via ${WIREGUARD_GATEWAY_IP} dev wg0

# Configure DNS
# First check if systemd-resolved is managing DNS
if [ -f /etc/systemd/resolved.conf ] && systemctl is-active systemd-resolved >/dev/null 2>&1; then
  # Use systemd-resolved's configuration methods
  sudo mkdir -p /etc/systemd/resolved.conf.d/
  cat << EOF | sudo tee /etc/systemd/resolved.conf.d/dns-servers.conf
[Resolve]
DNS=${WIREGUARD_GATEWAY_IP} 8.8.8.8
EOF
  sudo systemctl restart systemd-resolved
elif command -v resolvconf >/dev/null 2>&1; then
  # Use resolvconf if available
  echo -e "nameserver ${WIREGUARD_GATEWAY_IP}\nnameserver 8.8.8.8" | sudo resolvconf -a wg0
else
  # Fallback to direct file modification, but first backup the original
  sudo cp /etc/resolv.conf /etc/resolv.conf.bak
  # Check if it's a symlink and handle appropriately
  if [ -L /etc/resolv.conf ]; then
    sudo rm /etc/resolv.conf
  fi
  echo -e "nameserver ${WIREGUARD_GATEWAY_IP}\nnameserver 8.8.8.8" | sudo tee /etc/resolv.conf
fi


# Set up SSH Known Hosts
mkdir -p ~/.ssh/
echo "${SSH_KNOWN_HOSTS}" >> ~/.ssh/known_hosts

mkdir -p "${GITHUB_WORKSPACE}/secrets"

# Set up SSH private key
echo "${SSH_PRIVATE_KEY}" > "${GITHUB_WORKSPACE}/secrets/ansible_ssh_privatekey"
chmod 600 "${GITHUB_WORKSPACE}/secrets/ansible_ssh_privatekey"

# Setup Ansible Vault password
echo "${ANSIBLE_VAULT_PASSWORD}" > "${GITHUB_WORKSPACE}/secrets/vault_pass.txt"
chmod 600 "${GITHUB_WORKSPACE}/secrets/vault_pass.txt"

echo "Network setup completed successfully"
