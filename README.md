# H3x Infra Kubernetes Cluster

This project deploys and manages a production-ready dual-stack (IPv4/IPv6) K3s cluster with Ansible automation.

## Overview

This project gives Ansible playbooks and configurations to deploy and manage a K3s cluster with separate server and agent nodes. The cluster has these items:

- Dual-stack IPv4/IPv6 networking
- Cilium CNI for networking with eBPF
- MetalLB for load balancing
- Cert-Manager for automatic TLS certificates
- External-DNS for automatic DNS management
- Envoy Gateway (Gateway API) for HTTP routing, TLS termination, and OIDC
- Longhorn for persistent storage with CIFS backup
- Mailu for self-hosted email services
- Keycloak for identity and access management
- Argo CD for GitOps delivery with native Keycloak SSO (deployed separately)
- Automatic server preparation and updates

## Documentation

- [Architecture](docs/architecture.md) - Detailed cluster architecture
- [Operations](docs/operations.md) - Operation procedures


## Project Structure

```
├── bin/                   # Helper scripts
├── docs/                  # Documentation
├── inventory/             # Structured Ansible inventory
│   ├── production/        # Production environment
│   │   ├── hosts.yml      # Host definitions
│   │   ├── group_vars/    # Group variables
│   │   └── host_vars/     # Host variables
│   └── README.md          # Inventory documentation
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
│   ├── 07-gateway/        # Envoy Gateway (Gateway API) controller
│   ├── 08-smb/            # SMB/CIFS CSI driver
│   ├── 09-longhorn/       # Longhorn storage system
│   ├── 10-mailu/          # Self-hosted email server
│   ├── 10-keycloak/       # Keycloak identity provider (Operator + CNPG)
│   ├── 10-argocd/         # Argo CD with native Keycloak SSO (separate deployment)
│   └── 99-utils/          # Utility playbooks
└── README.md              # This file
```


## Continuous Integration

`.github/workflows/ci.yml` validates every pull request and push to `main`,
and runs weekly or on demand. It does not need a cluster or private-repository
credentials. The `minimal` job runs these checks:

- `renovate-config-validator`
- yamllint
- `ansible-playbook --syntax-check` against `inventory/production`
- `helm lint`/`template` of the charts under `playbooks/*/charts`
- Local behavior tests discovered under every `playbooks/*/tests` directory
- `terraform validate` for `playbooks/10-keycloak/terraform`

The single check to monitor is `ci-ok`.
The Renovate automerge waits for this check. The tool versions are `env:` pins with
`# renovate:` markers at the top of the workflow.

## Maintenance

See the [Argo CD layer guide](playbooks/10-argocd/README.md) for Keycloak client
configuration, deployment, access controls and recovery.

See the [Operations Guide](docs/operations.md) for detailed maintenance procedures.

## Requirements

- SSH access to all nodes with the user 'ansible'
- Ansible installed on the control node
- kubectl and Helm for Kubernetes management
- A correctly configured inventory in `inventory/production/` (see `inventory/README.md`)
- A DNS provider API token (for example, Cloudflare) for automatic DNS management
- An Ansible Vault password file for encrypted secrets

Kubernetes playbooks prepare `~/.kube/config`, which the private Terraform
providers also use. `KUBECONFIG` and `K8S_AUTH_KUBECONFIG` must be unset or name
that exact absolute file; file lists and other paths are rejected. Separate
context overrides are also rejected before connection setup. To use the
configured current context, run:

```bash
unset KUBECONFIG K8S_AUTH_KUBECONFIG K8S_AUTH_CONTEXT HELM_KUBECONTEXT
```

## Development Environment

This project includes a complete devcontainer setup. The devcontainer gives the same development environment on all machines.

### Using the Devcontainer

The devcontainer gives these items:
- **Ubuntu 24.04 (noble)** base environment
- **Pre-installed tools**: Ansible, kubectl, Helm, Docker CLI
- **Integrated scripts**: All `bin/` scripts are available in PATH
- **SSH key mounting**: Your host SSH keys are available at `/tmp/host-ssh`
- **Persistent history**: The Bash history stays after a container rebuild

### Setup

1. **Prerequisites**: Install Docker and VS Code with the Dev Containers extension.
2. **Open in container**: In VS Code, use the "Reopen in Container" command.
3. **SSH setup**: The container automatically mounts your SSH keys from the host.
4. **Environment**: All infrastructure management commands are available immediately.

### Available Commands

In the devcontainer, you can use these commands directly:
```bash
h3xinfra-deploy-stack      # Deploy complete infrastructure
kube-connect              # Connect to Kubernetes cluster
h3xinfra-gen-pass [name]   # Generate a named vault-encrypted password
```

## Quick Start

1. **Configure Inventory**: Set up your inventory in `inventory/production/`. For the structure, see `inventory/README.md`.
2. **Set up secrets**: Add vault-encrypted variables to the inventory. `ansible.cfg` uses `bin/h3xinfra-vault-pass` to resolve the Vault password.
3. **Deploy the complete stack**:
   ```bash
   h3xinfra-deploy-stack
   ```
4. Connect to the cluster:
   ```bash
   kube-connect
   ```
