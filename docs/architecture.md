# Cluster Architecture

This document describes the architecture of the H3x Kubernetes cluster infrastructure.

## Overview

The cluster is organized as a layered deployment with the following components:

1. **Base Infrastructure** - Physical/virtual servers with network setup
2. **Kubernetes Layer** - K3s deployment with proper configuration
3. **Network Layer** - Cilium CNI with dual-stack support
4. **Service Layer** - MetalLB for load balancing
5. **Security Layer** - Health endpoints, Certificate Manager, External DNS
6. **Ingress Layer** - Ingress NGINX for reverse proxy and TLS termination
7. **Storage Layer** - Longhorn for persistent storage with CIFS backup
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
8. Ingress NGINX controller deployment
9. Longhorn storage system deployment
10. Mailu email server deployment
11. Keycloak identity management deployment

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
- **Ingress NGINX IPv4**: 192.168.101.100/32
- **Ingress NGINX IPv6**: 2001:db8:face::100/128
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
- **Ingress-NGINX**: HTTP/HTTPS ingress controller (v4.12.2)
- **Longhorn**: Distributed block storage (v1.9.0)

### Application Services
- **Mailu**: Self-hosted email server with web interface (v2.2.2)
  - Domain: example.com
  - Hostnames: mail.app.example.com, mail.example.com
  - External SMTP relay via smtp2go.com
- **Keycloak**: Identity and access management
  - Hostname: identity.app.example.com
  - PostgreSQL backend

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
