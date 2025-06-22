# H3x Infra Kubernetes Cluster

A production-ready dual-stack (IPv4/IPv6) K3s cluster deployment and management project using Ansible automation.

## Overview

This project provides Ansible playbooks and configurations to deploy and manage a 3-node K3s Kubernetes cluster with:

- Dual-stack IPv4/IPv6 networking
- Cilium CNI for networking with eBPF
- MetalLB for load balancing
- Cert-Manager for automated TLS certificates
- External-DNS for automated DNS management
- Ingress-NGINX for reverse proxy and ingress
- Longhorn for persistent storage with CIFS backup
- Mailu for self-hosted email services
- Keycloak for identity and access management
- Automated server preparation and updates

## Documentation

- [Architecture](docs/architecture.md) - Detailed cluster architecture
- [Operations](docs/operations.md) - Operational procedures


## Project Structure

```
├── bin/                   # Helper scripts
├── docs/                  # Documentation
├── inventory.yml          # Ansible inventory file
├── inventory.example.yml  # Example inventory configuration
├── secrets/               # Encrypted password files
├── playbooks/             # Ansible playbooks
│   ├── stack-standup.yml  # Master deployment playbook
│   ├── stack-teardown.yml # Master teardown playbook
│   ├── 00-prep/           # Server preparation
│   ├── 01-k3s/            # K3s installation
│   ├── 02-cilium/         # Cilium CNI
│   ├── 03-metallb/        # MetalLB load balancer
│   ├── 04-health/         # Health endpoint config
│   ├── 05-certmanager/    # Certificate management
│   ├── 06-externaldns/    # External DNS with Cloudflare
│   ├── 07-ingressnginx/   # Nginx ingress controller
│   ├── 08-longhorn/       # Longhorn storage system
│   ├── 09-mailu/          # Mailu email server
│   ├── 10-keycloak/       # Keycloak identity management
│   └── 99-utils/          # Utility playbooks
└── README.md              # This file
```


## Maintenance

See [Operations Guide](docs/operations.md) for detailed maintenance procedures.

## Requirements

- SSH access to all nodes with user 'ansible'
- Ansible installed on control node
- kubectl and Helm for Kubernetes management
- Properly configured `inventory.yml` file (see `inventory.example.yml`)
- Cloudflare API token for DNS management
- Ansible Vault password file for encrypted secrets

## Development Environment

This project includes a complete devcontainer setup for a consistent development experience.

### Using the Devcontainer

The devcontainer provides:
- **Ubuntu 24.04** base environment
- **Pre-installed tools**: Ansible, kubectl, Helm, Docker CLI
- **Integrated scripts**: All `bin/` scripts are available in PATH
- **SSH key mounting**: Your host SSH keys are available at `/tmp/host-ssh`
- **Persistent history**: Bash history is preserved across container rebuilds

### Setup

1. **Prerequisites**: Docker and VS Code with the Dev Containers extension
2. **Open in container**: Use VS Code's "Reopen in Container" command
3. **SSH setup**: The container automatically mounts your SSH keys from the host
4. **Environment**: All infrastructure management commands are immediately available

### Available Commands

Once in the devcontainer, you can use these commands directly:
```bash
h3xinfra-deploy-stack      # Deploy complete infrastructure
h3xinfra-update-servers    # Update all cluster nodes
kube-connect              # Connect to Kubernetes cluster
h3xinfra-gen-pass                  # Generate encrypted passwords
h3xinfra-generate-token   # Generate new K3s token
```

## Quick Start

1. Copy `inventory.example.yml` to `inventory.yml` and customize with your environment
2. Set up secrets in the `secrets/` directory
3. Deploy the complete stack:
   ```bash
   h3xinfra-deploy-stack
   ```
4. Connect to the cluster:
   ```bash
   kube-connect
   ```
