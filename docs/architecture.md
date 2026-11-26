# Cluster Architecture

This document describes the architecture of the H3x Kubernetes cluster infrastructure.

## Overview

The cluster has a layered deployment. These are the layers:

1. **Base Infrastructure** - Physical/virtual computers with network configuration
2. **Kubernetes Layer** - K3s deployment with the correct configuration
3. **Network Layer** - Cilium CNI with dual-stack support
4. **Service Layer** - MetalLB for load balancing
5. **Security Layer** - Health endpoints, Certificate Manager, External DNS
6. **Ingress Layer** - Envoy Gateway (Gateway API) for HTTP routing, TLS termination, OIDC, and IP allowlists
7. **Storage Layer** - SMB/CIFS CSI driver and Longhorn for persistent storage
8. **Application Layer** - Mailu email server and Keycloak identity management

## Deployment Flow

The imports in [stack-standup.yml](../playbooks/stack-standup.yml) give the
authoritative order. This order includes PriorityClasses, the CoreDNS replica
count after Cilium, and kured after Longhorn. Keycloak has its own deployment
entry point. Provision its realm, clients and groups before the applications
that use them. The [Keycloak guide](../playbooks/10-keycloak/README.md)
describes this. [Argo CD](../playbooks/10-argocd/README.md) also has a separate
deployment entry point. It runs after the Keycloak Terraform layer configures
its Keycloak client.

## Node Topology

The cluster uses a **dedicated control-plane** model. There are two node roles:

| Role | Nodes (example) | Schedulable for | Taint |
|---|---|---|---|
| **Server** (control-plane + etcd) | 3 nodes (quorum) | Kubernetes control-plane components, Cilium, kured, and other system DaemonSets that explicitly tolerate the taint | `CriticalAddonsOnly=true:NoSchedule` |
| **Agent** (workload) | 5+ nodes | All workloads: Longhorn storage, platform applications (Mailu, Keycloak) and other applications (for example Plex and the *arr stack) | none |

### Why dedicated control-plane

Stateful workloads stay off the control-plane nodes. These workloads include Longhorn replicas, PostgreSQL data, *arr SQLite databases, and the Plex library. This gives these results:

- An application with a fault cannot cause an OOM kill of kube-apiserver / etcd / kubelet on a node of the quorum.
- A reboot of a control-plane node has an effect only on control-plane availability. It has no effect on storage availability.
- The 3-node etcd quorum stays small and predictable. A larger quorum increases the write latency.

### How the taint is honored

Control-plane nodes have the `CriticalAddonsOnly=true:NoSchedule` taint. Thus, **only pods that explicitly tolerate the taint can go on these nodes**. There are two results:

- **System DaemonSets that must run on every node** tolerate **all** taints with `tolerations: [{operator: Exists}]`. Examples are the Cilium CNI agent, the kured reboot daemon, the csi-driver-smb node plugin, and longhorn-manager. These DaemonSets run on the full 8-node fleet. Thus, the functionality is the same on all nodes.
- **Workloads** have no special tolerations. Examples are Longhorn replicas, CloudNativePG (CNPG) PostgreSQL, Keycloak, the *arr applications, Plex, and qbittorrent. These workloads go only on the 5 agent nodes. Longhorn shows 5 storage providers, not 8. This is the intended design.

### Implications

- **Longhorn replica count vs node count**: 5 agent nodes can hold storage, and the replica count is `defaultReplicaCount: 3`. Thus, 2 agent nodes can fail at the same time without data loss.
- **Workload HA spread**: Anti-affinity policies operate in the 5-agent pool.
- **Control-plane node reboots**: A reboot has an effect only on the control-plane. The 3-node etcd quorum tolerates 1 node that is down.

## Network Architecture

### Pod Networking
- **IPv4 Pod CIDR**: 10.42.0.0/16 (with /24 per node)
- **IPv6 Pod CIDR**: 2001:db8:cafe:0::/96 (with /112 per node)

### Service Networking
- **IPv4 Service CIDR**: 10.43.0.0/16
- **IPv6 Service CIDR**: fd00:cafe:43::/112

### Load Balancer Pools

MetalLB announces dedicated pools for each Service through L2 ARP/NDP. Each
pool disables automatic assignment. Each LoadBalancer Service names its pool.
The core layer creates no general-purpose pool.

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

The [overview](../README.md#overview) lists the platform components. Their
chart and image pins are in
[`inventory/production/group_vars/all/versions.yml`](../inventory/production/group_vars/all/versions.yml).
These pins are repository inputs. They are not evidence of the versions that
run in a cluster.

### Application Services
- **Mailu**: Self-hosted email server with web interface
  - Domain: example.com
  - Hostnames: mail.app.example.com, mail.example.com
  - External SMTP relay through smtp2go.com
- **Keycloak**: Identity and access management
  - Hostname: identity.app.example.com
  - Deployed with the Keycloak Operator (`k8s.keycloak.org/v2beta1` CR)
  - PostgreSQL backend that CNPG manages (`postgresql.cnpg.io/v1` Cluster)

### DNS Management
- **Managed Domains**: example.com, *.example.com, example.org, *.example.org, example.net
- **Cloudflare Integration**: Automatic DNS-01 ACME challenges
- **Wildcard Certificates**: Automatic for *.app.example.com and other domains

## Deployment Patterns

### Custom Helm Charts
The project uses bespoke charts to manage configuration. There are three types of chart:

- **Pre-deployment charts**: Create Secrets, ConfigMaps, and prerequisites.
- **Main deployment charts**: Deploy the primary applications with upstream charts.
- **Post-deployment charts**: Configure ClusterIssuers, more resources, and integrations.

### Component Naming Convention
All releases use this name pattern: `h3xinfra-{component}-{phase}`
- Example: `h3xinfra-metallb-main`, `h3xinfra-certmanager-post`

### Network Interface Configuration
The cluster uses the primary network interface: `eth0` (or your primary interface).
This setting is important for the Cilium IPv6 multicast device configuration.

## Security Considerations

- Cilium implements the network policies.
- A dedicated configuration controls anonymous access to health endpoints.
- cert-manager automatically manages the TLS certificates with Let's Encrypt.
- ExternalDNS automatically manages the DNS records with Cloudflare.
- Ansible Vault encrypts all secrets.
- The Mailu email server includes an SMTP relay configuration for external delivery.
- Keycloak gives centralized identity and access management.
- Longhorn storage includes an encrypted backup to a CIFS share.


## Maintenance Procedures

See [operations.md](operations.md) for detailed maintenance procedures.
