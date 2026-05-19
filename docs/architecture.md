# Cluster Architecture

This document describes the architecture of the H3x Kubernetes cluster infrastructure.

## Overview

The cluster is organized as a layered deployment with the following components:

1. **Base Infrastructure** - Physical/virtual servers with network setup
2. **Kubernetes Layer** - K3s deployment with proper configuration
3. **Network Layer** - Cilium CNI with dual-stack support
4. **Service Layer** - MetalLB for load balancing
5. **Security Layer** - Health endpoints, Certificate Manager, External DNS
6. **Ingress Layer** - Envoy Gateway (Gateway API) for HTTP routing, TLS termination, OIDC, and IP allowlisting
7. **Storage Layer** - SMB/CIFS CSI driver and Longhorn for persistent storage
8. **Application Layer** - Mailu email server and Keycloak identity management

## Deployment Flow

The deployment follows this sequence:
1. Server preparation (system settings, packages)
2. K3s installation and configuration
3. Cilium CNI deployment
4. MetalLB configuration
5. Health endpoints configuration
6. Certificate Manager deployment
7. External DNS setup
8. Envoy Gateway controller deployment + shared Gateway with wildcard TLS
9. SMB/CIFS CSI driver deployment
10. Longhorn storage system deployment
11. Mailu email server deployment
12. Keycloak identity management deployment (Operator + CloudNativePG)

## Node Topology

The cluster runs a **dedicated control-plane** model with two node roles:

| Role | Nodes (example) | Schedulable for | Identifying taint |
|---|---|---|---|
| **Server** (control-plane + etcd) | 3 nodes (quorum) | k8s control-plane components, Cilium, kured, other system DaemonSets that explicitly tolerate the taint | `CriticalAddonsOnly=true:NoSchedule` |
| **Agent** (workload) | 5+ nodes | All workloads — Longhorn storage, platform services (Mailu, Keycloak), apps (Plex, *arr stack, etc.) | none |

### Why dedicated control-plane

Stateful workloads (Longhorn replicas, Postgres data, *arr SQLite DBs, Plex library) are kept off the server nodes so:

- A misbehaving app can't OOM kube-apiserver / etcd / kubelet on a quorum-bearing node
- Server-node reboots affect only control-plane availability, not storage availability
- The 3-node etcd quorum stays small and predictable (avoids quorum-size growth that hurts write latency)

### How the taint is honored

The `CriticalAddonsOnly=true:NoSchedule` taint on server nodes means **only pods that explicitly tolerate it can land there**. The two outcomes:

- **System DaemonSets that must run on every node** (Cilium CNI agent, kured reboot daemon, csi-driver-smb node plugin, longhorn-manager, etc.) tolerate **all** taints via `tolerations: [{operator: Exists}]`. They run on the full 8-node fleet so functionality stays uniform.
- **Workloads** (Longhorn replicas, CNPG postgres, Keycloak, *arr apps, Plex, qbittorrent, etc.) carry no special tolerations. They schedule onto the 5 agent nodes only. Longhorn observably advertises 5 storage providers, not 8 — by design.

### Implications

- **Longhorn replica count vs node count**: with 5 storage-capable agent nodes and `defaultReplicaCount: 3`, you can lose 2 agents simultaneously without data loss
- **Workload HA spread**: anti-affinity policies operate within the 5-agent pool
- **Server-node reboots**: only affect control-plane (handled by 3-node etcd quorum tolerating 1 down)

## Network Architecture

### Pod Networking
- **IPv4 Pod CIDR**: 10.42.0.0/16 (with /24 per node)
- **IPv6 Pod CIDR**: 2001:db8:cafe:0::/96 (with /112 per node)

### Service Networking
- **IPv4 Service CIDR**: 10.43.0.0/16
- **IPv6 Service CIDR**: fd00:cafe:43::/112

### Load Balancer Pools
- **IPv4**: 192.168.100.0/24 (MetalLB dynamic allocation)
- **IPv6**: 2001:db8:face:1::/96 (MetalLB dynamic allocation)

### Manual Service IP Allocations
- **Envoy Gateway (shared) IPv4**: 192.168.101.100/32
- **Envoy Gateway (shared) IPv6**: 2001:db8:face::100/128
- **Mailu IPv4**: 192.168.101.101/32
- **Mailu IPv6**: 2001:db8:face::101/128

### Public IP Addresses
- **Mailu Public IPv4**: 203.0.113.10 (example)
- **Mailu Public IPv6**: 2001:db8:face::101

## Deployed Services

### Core Platform Services
- **Cilium CNI**: eBPF-based networking with network policies (v1.17.3)
- **MetalLB**: Load balancer for bare metal clusters (v0.13.10)
- **Cert-Manager**: Automated TLS certificate management (v1.17.2)
- **External-DNS**: Automated DNS record management (v1.16.1)
- **Envoy Gateway**: Gateway API controller for HTTP routing, TLS termination, OIDC, and IP allowlisting (v1.4.2)
- **Longhorn**: Distributed block storage (v1.9.0)

### Application Services
- **Mailu**: Self-hosted email server with web interface (v2.2.2)
  - Domain: example.com
  - Hostnames: mail.app.example.com, mail.example.com
  - External SMTP relay via smtp2go.com
- **Keycloak**: Identity and access management
  - Hostname: identity.app.example.com
  - Deployed via the Keycloak Operator (`k8s.keycloak.org/v2alpha1` CR)
  - PostgreSQL backend managed by CloudNativePG (`postgresql.cnpg.io/v1` Cluster)

### DNS Management
- **Managed Domains**: example.com, *.example.com, example.org, *.example.org, example.net
- **Cloudflare Integration**: Automated DNS-01 ACME challenges
- **Wildcard Certificates**: Automated for *.app.example.com and other domains

## Deployment Patterns

### Custom Helm Charts
The project uses a sophisticated pattern of custom Helm charts to manage configuration:

- **Pre-deployment charts**: Create secrets, ConfigMaps, and prerequisites
- **Main deployment charts**: Deploy primary applications using upstream Helm charts
- **Post-deployment charts**: Configure ClusterIssuers, additional resources, and integrations

### Component Naming Convention
All releases follow the naming pattern: `h3xinfra-{component}-{phase}`
- Example: `h3xinfra-metallb-main`, `h3xinfra-certmanager-post`

### Network Interface Configuration
The cluster is configured to use the primary network interface: `eth0` (or your primary interface)
This is particularly important for Cilium's IPv6 multicast device configuration.

## Development Environment

### Devcontainer Architecture

The project includes a sophisticated devcontainer setup with:

- **Base Environment**: Ubuntu 24.04 with custom user configuration
- **Tool Integration**: Pre-configured Ansible, kubectl, Helm, and Docker CLI
- **Path Management**: Custom scripts automatically available in PATH
- **Volume Strategy**: SSH keys mounted from host, persistent command history
- **Network Access**: Docker-outside-of-Docker for container management
- **IDE Integration**: VS Code extensions for Python and GitHub Copilot

### Container Features

```dockerfile
# Key devcontainer features:
- ghcr.io/devcontainers/features/docker-outside-of-docker:1
- ghcr.io/devcontainers/features/kubectl-helm-minikube:1
```

### Environment Variables

- `WORKSPACE_FOLDER`: Points to project root
- `H3XINFRA_FOLDER`: Points to public h3xinfra project
- `ANSIBLE_CONFIG`: Points to project ansible.cfg
- `PATH`: Extended to include `${WORKSPACE_FOLDER}/bin`

This setup ensures consistent development environment across different machines and operating systems.

## Security Considerations

- Network policies are implemented with Cilium
- Anonymous access to health endpoints is controlled via dedicated configuration
- TLS certificates are automatically managed by Cert-Manager using Let's Encrypt
- DNS records are automatically managed via External-DNS with Cloudflare
- All secrets are encrypted using Ansible Vault
- Mailu email server includes SMTP relay configuration for external delivery
- Keycloak provides centralized identity and access management
- Longhorn storage includes encrypted backup to CIFS share


## Maintenance Procedures

See [operations.md](operations.md) for detailed maintenance procedures.
