# H3xInfra Conventions and Patterns

This document describes the architectural patterns, naming conventions, and structural standards used in the h3xinfra core infrastructure deployment framework.

## Table of Contents
- [Overview](#overview)
- [Repository Structure](#repository-structure)
- [Layer Architecture](#layer-architecture)
- [Playbook Patterns](#playbook-patterns)
- [Naming Conventions](#naming-conventions)
- [Helm Deployment Patterns](#helm-deployment-patterns)
- [Inventory Structure](#inventory-structure)
- [Security Patterns](#security-patterns)
- [Network Architecture](#network-architecture)
- [Extension Guidelines](#extension-guidelines)

---

## Overview

H3xInfra is a declarative, Helm-based infrastructure framework for deploying production-ready Kubernetes clusters with a comprehensive platform stack. The framework emphasizes:

- **Layered Architecture**: Ordered deployment of dependent components
- **Dual-Stack Networking**: Full IPv4/IPv6 support throughout
- **GitOps-Ready**: All configuration as code with Ansible and Helm
- **Extensible Design**: Clear patterns for adding custom services
- **Security-First**: Vault encryption, network policies, TLS automation

---

## Repository Structure

### Core Structure
```
h3xinfra/
├── playbooks/             # Infrastructure layer playbooks
│   ├── stack-standup.yml  # Master deployment orchestration
│   ├── stack-teardown.yml # Master teardown orchestration
│   ├── 00-prep/           # Server preparation layer
│   ├── 01-k3s/            # Kubernetes cluster layer
│   ├── 02-cilium/         # CNI networking layer
│   ├── 03-metallb/        # Load balancer layer
│   ├── 04-health/         # Health endpoint layer
│   ├── 05-certmanager/    # Certificate management layer
│   ├── 06-externaldns/    # DNS automation layer
│   ├── 07-gateway/        # Envoy Gateway (Gateway API) controller layer
│   ├── 08-smb/            # SMB CSI driver layer
│   ├── 09-longhorn/       # Storage system layer
│   ├── 10-mailu/          # Email server layer
│   └── 99-utils/          # Utility playbooks
├── inventory/             # Example inventory structure
│   ├── production/
│   │   ├── hosts.yml
│   │   ├── group_vars/
│   │   └── host_vars/
│   └── README.md
├── docs/                  # Documentation
│   ├── architecture.md    # Detailed architecture
│   └── operations.md      # Operational procedures
├── bin/                   # Helper scripts
│   ├── h3xinfra-deploy-stack
│   ├── h3xinfra-update-servers
│   ├── kube-connect
│   └── ...
├── ansible.cfg            # Ansible configuration
└── README.md
```

---

## Layer Architecture

### Layer Numbering System

Layers are numbered to enforce deployment order and dependency relationships:

#### Core Infrastructure (00-09)
- **00-prep**: Server preparation, system configuration, package installation
- **01-k3s**: K3s Kubernetes cluster deployment
- **02-cilium**: Cilium CNI with eBPF networking
- **03-metallb**: MetalLB load balancer for bare metal
- **04-health**: Health check endpoints and monitoring
- **05-certmanager**: Cert-manager for automated TLS certificates
- **06-externaldns**: External-DNS for automated DNS management
- **07-gateway**: Envoy Gateway controller + shared Gateway with wildcard TLS
- **08-smb**: SMB/CIFS CSI driver for network storage
- **09-longhorn**: Longhorn distributed block storage

#### Platform Services (10-19)
- **10-mailu**: Self-hosted email server (Mailu)

#### Utilities (99)
- **99-utils**: Shared utilities (kube-connect, apt updates, etc.)

### Dependency Flow
```
00-prep (servers ready)
  ↓
01-k3s (cluster running)
  ↓
02-cilium (networking ready)
  ↓
03-metallb (load balancing ready)
  ↓
04-health (monitoring ready)
  ↓
05-certmanager (TLS automation ready)
  ↓
06-externaldns (DNS automation ready)
  ↓
07-gateway (Gateway API ready)
  ↓
08-smb (network storage ready)
  ↓
09-longhorn (persistent storage ready)
  ↓
10+ (applications deploy)
```

---

## Playbook Patterns

### Standard Layer Structure
Every numbered layer follows this consistent structure:

```
XX-servicename/
├── standup.yml              # Deployment playbook
├── teardown.yml             # Removal playbook  
├── README.md                # Layer documentation (optional)
├── {service}-values.yaml    # Helm values (if simple)
└── charts/                  # Custom Helm charts (if complex)
    ├── h3xinfra-{service}-pre/    # Prerequisites chart
    ├── h3xinfra-{service}-main/   # Main wrapper chart
    └── h3xinfra-{service}-post/   # Post-config chart
```

### Standup Playbook Template

```yaml
---
# 1. Import kube-connect utility (establishes k8s connection)
- import_playbook: ../99-utils/kube-connect.yml

# 2. Main deployment play
- name: Deploy {Service} via Helm
  hosts: k8s
  gather_facts: false

  tasks:
    # 3. Add external Helm repository (if using external chart)
    - name: Add {Service} Helm repository
      kubernetes.core.helm_repository:
        name: {repo-name}
        repo_url: https://{repo-url}
        state: present

    # 4. Create namespace
    - name: Create {Service} namespace
      kubernetes.core.k8s:
        api_version: v1
        kind: Namespace
        name: "{{ servicename.namespace }}"
        state: present
        wait: true

    # 5. Deploy pre-configuration (optional)
    - name: Deploy {Service} configuration via Helm
      kubernetes.core.helm:
        name: "{{ servicename.release_name_prefix }}-pre"
        chart_ref: "{{ playbook_dir }}/charts/h3xinfra-servicename-pre"
        namespace: "{{ servicename.namespace }}"
        create_namespace: false
        values:
          fullnameOverride: "{{ servicename.release_name_prefix }}-pre"
          # Pre-configuration values (secrets, configmaps, CRDs)
        state: present
        wait: true
        timeout: "120s"

    # 6. Deploy main application
    - name: Deploy {Service} via Helm
      kubernetes.core.helm:
        name: "{{ servicename.release_name_prefix }}-main"
        chart_ref: "{repo-name}/{chart-name}"
        chart_version: "{{ servicename.chart_version }}"
        namespace: "{{ servicename.namespace }}"
        create_namespace: false
        values:
          fullnameOverride: "{{ servicename.release_name_prefix }}-main"
          # Main application configuration
        set_values:
          # For complex types (arrays, objects), use JSON
          - value: "key={{ servicename.list_value | to_json }}"
            value_type: json
        state: present
        wait: true
        timeout: "300s"

    # 7. Deploy post-configuration (optional)
    - name: Deploy {Service} post-configuration via Helm
      kubernetes.core.helm:
        name: "{{ servicename.release_name_prefix }}-post"
        chart_ref: "{{ playbook_dir }}/charts/h3xinfra-servicename-post"
        namespace: "{{ servicename.namespace }}"
        values:
          fullnameOverride: "{{ servicename.release_name_prefix }}-post"
          # Post-configuration values (ClusterIssuers, etc.)
        state: present
        wait: true
        timeout: "120s"
```

### Teardown Playbook Template

```yaml
---
- import_playbook: ../99-utils/kube-connect.yml

- name: Teardown {Service} deployment
  hosts: k8s
  gather_facts: false

  tasks:
    # Remove in reverse order: post -> main -> pre
    
    - name: Remove {Service} post-configuration
      kubernetes.core.helm:
        name: "{{ servicename.release_name_prefix }}-post"
        release_namespace: "{{ servicename.namespace }}"
        state: absent
        wait: true

    - name: Remove {Service} Helm release
      kubernetes.core.helm:
        name: "{{ servicename.release_name_prefix }}-main"
        release_namespace: "{{ servicename.namespace }}"
        state: absent
        wait: true
        timeout: "180s"

    - name: Remove {Service} pre-configuration
      kubernetes.core.helm:
        name: "{{ servicename.release_name_prefix }}-pre"
        release_namespace: "{{ servicename.namespace }}"
        state: absent
        wait: true

    - name: Remove {Service} namespace
      kubernetes.core.k8s:
        api_version: v1
        kind: Namespace
        name: "{{ servicename.namespace }}"
        state: absent
        wait: true
        wait_timeout: 300
```

---

## Naming Conventions

### Release Naming Pattern
All Helm releases follow this strict naming convention:

```
h3xinfra-{service}-{phase}
```

**Components:**
- `h3xinfra-`: Fixed prefix for all releases
- `{service}`: Service name (lowercase, no hyphens within name)
- `{phase}`: Deployment phase (`pre`, `main`, or `post`)

**Examples:**
```
h3xinfra-metallb-main
h3xinfra-certmanager-post
h3xinfra-certmanager-post-default-clusterissuer  (resource name)
h3xinfra-gateway-pre
h3xinfra-longhorn-post
```

### Phase Definitions

#### `pre` Phase
- Creates prerequisites before main deployment
- Typical resources: Secrets, ConfigMaps, CRDs, ServiceAccounts
- Examples:
  - External-DNS: Cloudflare API token secret
  - Cert-Manager: Cloudflare DNS solver secret
  - Envoy Gateway: shared wildcard `Certificate` + MetalLB `IPAddressPool` + `Gateway` listener (rendered by `h3xinfra-gateway-pre`)
  - Longhorn: RecurringJob CRDs, backup secrets

#### `main` Phase
- Primary application deployment
- Usually references external Helm chart or custom chart
- Always uses: `fullnameOverride: "{{ service.release_name_prefix }}-main"`

#### `post` Phase
- Post-deployment configuration
- Typical resources: ClusterIssuers, additional integrations
- Examples:
  - Cert-Manager: Let's Encrypt ClusterIssuer
  - Longhorn: RecurringJob instances

### Namespace Conventions

```yaml
# System namespaces (fixed)
namespace: "kube-system"
namespace: "cert-manager"
namespace: "longhorn-system"

# Service namespaces (variable-based)
namespace: "{{ servicename.namespace }}"

# Typical service namespace values
namespace: "envoy-gateway-system"
namespace: "mailu"
```

---

## Helm Deployment Patterns

### Pattern 1: External Chart with Pre/Post Configuration

**Used by:** cert-manager, external-dns, gateway (Envoy Gateway), longhorn, mailu

**Structure:**
```yaml
# Pre-chart creates secrets and prerequisites
- name: Deploy {Service} configuration via Helm
  kubernetes.core.helm:
    name: "{{ service.release_name_prefix }}-pre"
    chart_ref: "{{ playbook_dir }}/charts/h3xinfra-service-pre"
    # ... creates secrets, configmaps

# Main deployment uses external/upstream chart
- name: Deploy {Service} via Helm
  kubernetes.core.helm:
    name: "{{ service.release_name_prefix }}-main"
    chart_ref: "{repo}/{chart}"
    chart_version: "{{ service.chart_version }}"
    # ... main application

# Post-chart creates ClusterIssuers, etc.
- name: Deploy {Service} post-configuration via Helm
  kubernetes.core.helm:
    name: "{{ service.release_name_prefix }}-post"
    chart_ref: "{{ playbook_dir }}/charts/h3xinfra-service-post"
    # ... post-configuration
```

**Example:** cert-manager
```yaml
# Pre: Creates Cloudflare API secret
chart_ref: "{{ playbook_dir }}/charts/h3xinfra-certmanager-pre"

# Main: Deploys cert-manager
chart_ref: "jetstack/cert-manager"
chart_version: "v1.17.2"

# Post: Creates Let's Encrypt ClusterIssuer
chart_ref: "{{ playbook_dir }}/charts/h3xinfra-certmanager-post"
```

### Pattern 2: Simple External Chart

**Used by:** SMB CSI driver

**Structure:**
```yaml
- name: Deploy {Service} via Helm
  kubernetes.core.helm:
    name: "{{ service.release_name_prefix }}-main"
    chart_ref: "{repo}/{chart}"
    chart_version: "{{ service.chart_version }}"
    values:
      # Simple inline configuration
```

### Pattern 3: Fully Custom Chart

**Used by:** Custom applications, complex configurations

**Structure:**
```yaml
- name: Deploy {Service} via Helm
  kubernetes.core.helm:
    name: "{{ service.release_name_prefix }}-main"
    chart_ref: "{{ playbook_dir }}/charts/h3xinfra-service-main"
    values:
      # Custom chart values
```

### Values Override Strategies

#### Simple Values (Inline)
```yaml
values:
  fullnameOverride: "{{ service.release_name_prefix }}-main"
  
  persistence:
    enabled: "{{ service.persistence.enabled | default(true) }}"
    storageClass: "{{ service.persistence.storageClass | default('longhorn') }}"
    size: "{{ service.persistence.size | default('10Gi') }}"
  
  ingress:
    enabled: "{{ service.ingress.enabled | default(false) }}"
    className: "{{ service.ingress.class | default('nginx') }}"
```

#### Complex Values (JSON)
For lists, dictionaries, or complex structures, use `set_values` with JSON:

```yaml
set_values:
  # Array/list values
  - value: "domainFilters={{ externaldns.domain_filters | to_json }}"
    value_type: json
  
  # Nested object values
  - value: "certificate.dnsNames={{ service.dns_names | to_json }}"
    value_type: json
  
  # Complex structures
  - value: "hostnames={{ service.hostnames | to_json }}"
    value_type: json
```

**Example from external-dns:**
```yaml
set_values:
  - value: "domainFilters={{ externaldns.domain_filters | to_json }}"
    value_type: json

# Expands to: --set-json domainFilters='["example.com","*.example.com"]'
```

---

## Inventory Structure

### Virtual Host Pattern

All Kubernetes service configurations use a **virtual `k8s` host** to cleanly separate infrastructure from service configuration.

**In `inventory/production/hosts.yml`:**
```yaml
all:
  children:
    # Physical K3s cluster nodes
    k3s_cluster:
      hosts:
        node1:
          ansible_host: 192.168.1.10
        node2:
          ansible_host: 192.168.1.11
        node3:
          ansible_host: 192.168.1.12
    
    # Virtual host for Kubernetes services
    k8s_virtual:
      hosts:
        k8s:
          ansible_connection: local
```

### Variable Organization

**Directory structure:**
```
inventory/production/
├── hosts.yml                      # Host definitions
├── group_vars/
│   ├── all/
│   │   ├── main.yml              # Global configuration
│   │   ├── network.yml           # Network settings
│   │   └── secrets.yml           # Vault-encrypted secrets
│   └── k3s_cluster/
│       └── main.yml              # K3s cluster settings
└── host_vars/
    └── k8s/                      # Service-specific configs
        ├── certmanager.yml
        ├── cilium.yml
        ├── externaldns.yml
        ├── gateway.yml
        ├── longhorn.yml
        ├── mailu.yml
        └── metallb.yml
```

### Service Variable Structure

Each service file in `host_vars/k8s/` follows this pattern:

```yaml
---
servicename:
  namespace: "servicename"
  release_name_prefix: "h3xinfra-servicename"
  chart_version: "x.y.z"
  
  # Service exposure
  service:
    type: "ClusterIP"  # or LoadBalancer
  
  # Ingress configuration
  ingress:
    enabled: true
    hostname: "service.domain.com"
    class: "nginx"
  
  # Persistence
  persistence:
    enabled: true
    storageClass: "longhorn"
    size: "10Gi"
  
  # Service-specific configuration
  # ...
```

**Example: `certmanager.yml`**
```yaml
---
certmanager:
  namespace: "cert-manager"
  release_name_prefix: "h3xinfra-certmanager"
  chart_version: "v1.17.2"
  
  cloudflare_email: "admin@example.com"
  
  clusterissuer:
    name: "default-clusterissuer"
    acme:
      email: "admin@example.com"
      server: "https://acme-v02.api.letsencrypt.org/directory"
      solvers:
      - dns01:
          cloudflare:
            email: "admin@example.com"
```

### Secrets Management

**Shared secrets in `group_vars/all/secrets.yml`:**
```yaml
---
# Global secrets used across multiple services
cloudflare_api_token: !vault |
  $ANSIBLE_VAULT;1.1;AES256
  ...

storage_password: !vault |
  $ANSIBLE_VAULT;1.1;AES256
  ...
```

**Service-specific secrets in service files:**
```yaml
servicename:
  admin_password: !vault |
    $ANSIBLE_VAULT;1.1;AES256
    ...
  
  db_password: !vault |
    $ANSIBLE_VAULT;1.1;AES256
    ...
```

---

## Security Patterns

### TLS/Certificate Strategy

All HTTP routing is provided by a single shared **Envoy Gateway** Gateway
(`{{ gateway.release_name_prefix }}-pre-shared` in namespace
`{{ gateway.namespace }}`). The Gateway terminates TLS using one wildcard
certificate that covers every internal/external hostname; per-app charts
render Gateway-API resources rather than `Ingress` objects.

#### Standard chart `ingress` schema
```yaml
ingress:
  enabled: true
  hostname: "service.app.example.com"
  gateway:
    name: "h3xinfra-gateway-pre-shared"
    namespace: "envoy-gateway-system"
  trustedIPs:                                  # rendered into SecurityPolicy.authorization
  - "10.0.0.0/8"
  - "192.168.0.0/16"
  - "fd00::/8"
  timeout: "300s"                              # HTTPRoute.timeouts.{request,backendRequest}
  oidc:                                        # optional OIDC SecurityPolicy
    enabled: true
    issuerUrl: "https://idp.example.com/..."
    clientId: "<from idp>"
    clientSecret: "<from idp, vaulted>"
    scopes: ["openid", "profile", "email"]
    secretRef:
      name: "{{ service.release_name_prefix }}-oidc"
```

#### Resource mapping
| Concern             | CRD                                             |
|---------------------|-------------------------------------------------|
| Routing             | `gateway.networking.k8s.io/v1 HTTPRoute`        |
| AuthN/AuthZ         | `gateway.envoyproxy.io/v1alpha1 SecurityPolicy` |
| Upstream TLS verify | `gateway.networking.k8s.io/v1alpha3 BackendTLSPolicy` |

#### Public Services
The shared wildcard certificate covers all hostnames; no per-app
`Certificate` is provisioned. external-dns publishes a record pointing
at the Gateway's MetalLB IP via labels on the Gateway service.

#### Internal Services
Services restricted to internal networks combine `trustedIPs` with the
`oidc.enabled: false` block. The chart renders a `SecurityPolicy` with
`authorization.{defaultAction: Deny, rules:[{action: Allow,
principal.clientCIDRs: <trustedIPs>}]}`. This replaces the previous
`nginx.ingress.kubernetes.io/whitelist-source-range` annotation.

#### Pre-Provisioned / Upstream HTTPS Backends
Backends that themselves serve HTTPS (e.g. Home Assistant via
nginxproxy) get a `BackendTLSPolicy` from the chart, targeting the
backing `Service` with `validation.{caCertificateRefs |
wellKnownCACertificates, hostname, subjectAltNames}`. Self-signed
backends require a `ConfigMap` with key `ca.crt`.

### Network Policy Pattern

Services requiring network isolation (example: Mailu):

```yaml
networkPolicy:
  enabled: true
  allowedIPv6Networks:
  - "2001:db8::/32"
  - "fd00::/8"
```

### IP Whitelisting Standard

```yaml
trusted_ips:
- "10.0.0.0/8"         # RFC1918 - Private IPv4
- "172.16.0.0/12"      # RFC1918 - Private IPv4  
- "192.168.0.0/16"     # RFC1918 - Private IPv4
- "fd00::/8"           # RFC4193 - ULA IPv6
```

---

## Network Architecture

### Dual-Stack Configuration

All services support both IPv4 and IPv6:

**Cluster CIDRs:**
```yaml
cluster_cidr:
  ipv4: "10.42.0.0/16"          # K3s default
  ipv6: "2001:db8:cafe:0::/96"  # Custom IPv6
```

**Service CIDRs:**
```yaml
service_cidr:
  ipv4: "10.43.0.0/16"          # K3s default
  ipv6: "fd00:cafe:43::/112"    # Custom IPv6
```

### MetalLB Load Balancer Pools

**Dynamic pool for general use:**
```yaml
metallb:
  loadbalancer_pools:
    ipv4:
    - "192.168.100.10-192.168.100.250"
    ipv6:
    - "2001:db8:face:1::10-2001:db8:face:1::fa"
```

**Dedicated pool for the shared Envoy Gateway:**
```yaml
gateway:
  loadbalancer_pools:
    ipv4: "10.12.96.100/32"
    ipv6: "2600:1700:7c20:a475:face::100/128"
```

### DNS Management

**External-DNS configuration:**
```yaml
externaldns:
  domain_filters:
  - "example.com"
  - "*.example.com"
  
  # Services publish via label
  extraLabels:
    external-dns.alpha.kubernetes.io/publish: "true"
```

**Shared Gateway wildcard certificate:**
```yaml
gateway:
  dns_names:
  - "*.app.example.com"
  - "*.example.com"
  - "example.com"
```

---

## Extension Guidelines

### Adding a New Infrastructure Layer

#### 1. Determine Layer Number
- Core infrastructure: 00-09
- Platform services: 10-19
- Utilities: 99

#### 2. Create Layer Structure
```bash
mkdir -p playbooks/XX-servicename/charts
```

#### 3. Create Playbooks

**`standup.yml`:**
```yaml
---
- import_playbook: ../99-utils/kube-connect.yml

- name: Deploy NewService via Helm
  hosts: k8s
  gather_facts: false
  tasks:
    # Follow standard pattern...
```

**`teardown.yml`:**
```yaml
---
- import_playbook: ../99-utils/kube-connect.yml

- name: Teardown NewService deployment
  hosts: k8s
  gather_facts: false
  tasks:
    # Follow teardown pattern...
```

#### 4. Create Inventory Variables

**`inventory/production/host_vars/k8s/newservice.yml`:**
```yaml
---
newservice:
  namespace: "newservice"
  release_name_prefix: "h3xinfra-newservice"
  chart_version: "1.0.0"
  # ... configuration
```

#### 5. Add to Stack Orchestration

**In `playbooks/stack-standup.yml`:**
```yaml
- import_playbook: XX-newservice/standup.yml
```

**In `playbooks/stack-teardown.yml`:**
```yaml
- import_playbook: XX-newservice/teardown.yml
```

#### 6. Document the Layer

Create `XX-newservice/README.md`:
```markdown
# NewService Layer

## Overview
Brief description of the service and its purpose.

## Configuration
Key variables and their purpose.

## Usage
Deployment and management instructions.
```

### Custom Helm Charts

When creating wrapper charts:

**`charts/h3xinfra-service-pre/Chart.yaml`:**
```yaml
apiVersion: v2
name: h3xinfra-service-pre
description: Prerequisites for Service
type: application
version: 0.1.0
appVersion: "1.0"
```

**`charts/h3xinfra-service-pre/values.yaml`:**
```yaml
fullnameOverride: ""

# Service-specific values
```

**`charts/h3xinfra-service-pre/templates/_helpers.tpl`:**
```yaml
{{- define "h3xinfra-service-pre.name" -}}
{{- default .Chart.Name .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}
```

---

## Best Practices

### ✅ DO:

- **Use the virtual `k8s` host** for all Kubernetes service configurations
- **Follow the three-phase pattern** (pre/main/post) when services need prerequisites or post-configuration
- **Set `fullnameOverride`** to maintain consistent resource naming
- **Use `wait: true`** with appropriate timeouts for all Helm deployments
- **Use `to_json` filter** with `set_values` for complex data structures
- **Document service-specific requirements** in layer README files
- **Encrypt all secrets** with Ansible Vault
- **Import `kube-connect`** utility in all k8s-related playbooks
- **Version pin** all Helm charts with `chart_version`

### ❌ DON'T:

- **Hard-code values** in playbooks (use inventory variables)
- **Mix different naming patterns** for releases
- **Deploy without proper namespace** isolation
- **Skip the `kube-connect` import** for Kubernetes playbooks
- **Use `shell` or `command` modules** when native k8s Ansible modules exist
- **Forget to add services to stack** orchestration playbooks
- **Create ingress with cert-manager** for services that need to avoid CT logs
- **Deploy external charts** without version pinning

---

## Testing and Validation

### Deploy Single Layer
```bash
ansible-playbook playbooks/XX-servicename/standup.yml -i inventory/production/hosts.yml
```

### Teardown Single Layer
```bash
ansible-playbook playbooks/XX-servicename/teardown.yml -i inventory/production/hosts.yml
```

### Deploy Full Stack
```bash
# Using helper script
h3xinfra-deploy-stack

# Or directly
ansible-playbook playbooks/stack-standup.yml -i inventory/production/hosts.yml
```

### Verify Deployment
```bash
# Connect to cluster
kube-connect

# Check resources
kubectl get all -n <namespace>
kubectl get certificates -A
kubectl get ingress -A
```

---

## Helper Scripts

Located in `bin/` directory:

- **`h3xinfra-deploy-stack`** - Deploy complete infrastructure stack
- **`h3xinfra-update-servers`** - Update all cluster nodes
- **`kube-connect`** - Establish kubectl connection to cluster
- **`h3xinfra-gen-pass`** - Generate Ansible Vault encrypted passwords
- **`h3xinfra-generate-token`** - Generate K3s cluster token
- **`h3xinfra-decrypt-vault`** - Decrypt vault files
- **`h3xinfra-setup-dependencies`** - Install Ansible dependencies
- **`h3xinfra-setup-perms`** - Set up file permissions
- **`h3xinfra-setup-ssh`** - Configure SSH access

---

## Summary

The h3xinfra framework provides a robust, extensible foundation for Kubernetes infrastructure with:

- **📦 Layered Architecture** - Clear dependency management and ordered deployment
- **🏷️ Consistent Naming** - Predictable resource names across all services
- **⎈ Helm-Centric** - Leverage Helm ecosystem with custom configuration
- **🔒 Security-First** - Vault encryption, network policies, TLS automation
- **🌐 Dual-Stack Ready** - Full IPv4/IPv6 support throughout
- **📚 Well-Documented** - Clear patterns and conventions
- **🔧 Extensible** - Easy to add new services following established patterns

Following these conventions ensures consistency, maintainability, and reliability across the entire infrastructure deployment.
