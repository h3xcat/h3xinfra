# Cluster Architecture

This document describes the architecture of the H3x Kubernetes cluster infrastructure.

## Overview

The cluster has a layered deployment. These are the layers:

1. **Base Infrastructure** - Physical/virtual servers with network setup
2. **Kubernetes Layer** - K3s deployment with the correct configuration
3. **Network Layer** - Cilium CNI with dual-stack support
4. **Service Layer** - MetalLB for load balancing
5. **Security Layer** - Health endpoints, Certificate Manager, External DNS
6. **Ingress Layer** - Envoy Gateway (Gateway API) for HTTP routing, TLS termination, OIDC, and IP allowlisting
7. **Storage Layer** - SMB/CIFS CSI driver and Longhorn for persistent storage
8. **Application Layer** - Mailu email server and Keycloak identity management

## Deployment Flow

The deployment uses this sequence:
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

The cluster uses a **dedicated control-plane** model. There are two node roles:

| Role | Nodes (example) | Schedulable for | Identifying taint |
|---|---|---|---|
| **Server** (control-plane + etcd) | 3 nodes (quorum) | k8s control-plane components, Cilium, kured, other system DaemonSets that explicitly tolerate the taint | `CriticalAddonsOnly=true:NoSchedule` |
| **Agent** (workload) | 5+ nodes | All workloads — Longhorn storage, platform services (Mailu, Keycloak), apps (Plex, *arr stack, etc.) | none |

### Why dedicated control-plane

Stateful workloads stay off the server nodes. These workloads include Longhorn replicas, Postgres data, *arr SQLite DBs, and the Plex library. This gives these results:

- An app with a fault cannot cause an OOM kill of kube-apiserver / etcd / kubelet on a node that is part of the quorum.
- A server-node reboot has an effect only on control-plane availability. It has no effect on storage availability.
- The 3-node etcd quorum stays small and predictable. A larger quorum increases the write latency.

### How the taint is honored

Server nodes have the `CriticalAddonsOnly=true:NoSchedule` taint. Thus, **only pods that explicitly tolerate the taint can go on these nodes**. There are two results:

- **System DaemonSets that must run on every node** tolerate **all** taints with `tolerations: [{operator: Exists}]`. Examples are the Cilium CNI agent, the kured reboot daemon, the csi-driver-smb node plugin, and longhorn-manager. These DaemonSets run on the full 8-node fleet, so the functionality is the same on all nodes.
- **Workloads** have no special tolerations. Examples are Longhorn replicas, CNPG postgres, Keycloak, *arr apps, Plex, and qbittorrent. These workloads go only on the 5 agent nodes. Longhorn shows 5 storage providers, not 8. This is the intended design.

### Implications

- **Longhorn replica count vs node count**: There are 5 agent nodes that can hold storage, and `defaultReplicaCount: 3`. Thus, 2 agents can fail at the same time without data loss.
- **Workload HA spread**: Anti-affinity policies operate in the 5-agent pool.
- **Server-node reboots**: A reboot has an effect only on the control-plane. The 3-node etcd quorum tolerates 1 node that is down.

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
- **Cert-Manager**: Automatic TLS certificate management (v1.17.2)
- **External-DNS**: Automatic DNS record management (v1.16.1)
- **Envoy Gateway**: Gateway API controller for HTTP routing, TLS termination, OIDC, and IP allowlisting (v1.4.2)
- **Longhorn**: Distributed block storage (v1.9.0)

### Application Services
- **Mailu**: Self-hosted email server with web interface (v2.2.2)
  - Domain: example.com
  - Hostnames: mail.app.example.com, mail.example.com
  - External SMTP relay through smtp2go.com
- **Keycloak**: Identity and access management
  - Hostname: identity.app.example.com
  - Deployed with the Keycloak Operator (`k8s.keycloak.org/v2alpha1` CR)
  - PostgreSQL backend that CloudNativePG manages (`postgresql.cnpg.io/v1` Cluster)

### DNS Management
- **Managed Domains**: example.com, *.example.com, example.org, *.example.org, example.net
- **Cloudflare Integration**: Automatic DNS-01 ACME challenges
- **Wildcard Certificates**: Automatic for *.app.example.com and other domains

## Deployment Patterns

### Custom Helm Charts
The project uses custom Helm charts to manage configuration. There are three types of chart:

- **Pre-deployment charts**: Create secrets, ConfigMaps, and prerequisites.
- **Main deployment charts**: Deploy the primary applications with upstream Helm charts.
- **Post-deployment charts**: Configure ClusterIssuers, more resources, and integrations.

### Component Naming Convention
All releases use this naming pattern: `h3xinfra-{component}-{phase}`
- Example: `h3xinfra-metallb-main`, `h3xinfra-certmanager-post`

### Network Interface Configuration
The cluster uses the primary network interface: `eth0` (or your primary interface).
This setting is important for the Cilium IPv6 multicast device configuration.

## Development Environment

### Devcontainer Architecture

The project includes a devcontainer setup with these items:

- **Base Environment**: Ubuntu 22.04 (jammy) with custom user configuration
- **Tool Integration**: Pre-configured Ansible, kubectl, Helm, and Docker CLI
- **Path Management**: Custom scripts are automatically available in PATH
- **Volume Strategy**: SSH keys mounted from the host, persistent command history
- **Network Access**: Docker-outside-of-Docker for container management
- **IDE Integration**: VS Code extensions for Python and GitHub Copilot

### Container Features

```dockerfile
# Key devcontainer features:
- ghcr.io/devcontainers/features/docker-outside-of-docker:1
```

A feature does not install kubectl and Helm. They are pinned as `ARG KUBECTL_VERSION` / `ARG HELM_VERSION` in `.devcontainer/Dockerfile`.
These pins have `# renovate:` markers, so Renovate bumps them.
The project pins the two tools in the Dockerfile for these reasons:

- The `kubectl-helm-minikube` feature installed `latest` over those pins.
- The kubectl version must stay within +/-1 minor of the k3s cluster version.

### Environment Variables

- `WORKSPACE_FOLDER`: Points to the project root
- `H3XINFRA_FOLDER`: Points to the public h3xinfra project
- `ANSIBLE_CONFIG`: Points to the project ansible.cfg
- `PATH`: Extended to include `${WORKSPACE_FOLDER}/bin`

This setup makes sure that the development environment is the same on different machines and operating systems.

## Security Considerations

- Cilium implements the network policies.
- A dedicated configuration controls anonymous access to health endpoints.
- Cert-Manager automatically manages the TLS certificates with Let's Encrypt.
- External-DNS automatically manages the DNS records with Cloudflare.
- Ansible Vault encrypts all secrets.
- The Mailu email server includes an SMTP relay configuration for external delivery.
- Keycloak gives centralized identity and access management.
- Longhorn storage includes an encrypted backup to a CIFS share.


## Maintenance Procedures

See [operations.md](operations.md) for detailed maintenance procedures.
