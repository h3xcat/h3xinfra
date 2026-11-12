# H3xInfra Conventions and Patterns

This document describes the architecture patterns, the naming conventions, and the structure standards of the h3xinfra framework. This framework deploys the core infrastructure.

## Table of Contents
- [Overview](#overview)
- [Repository Structure](#repository-structure)
- [Layer Architecture](#layer-architecture)
- [Playbook Patterns](#playbook-patterns)
- [Naming Conventions](#naming-conventions)
- [Helm Deployment Patterns](#helm-deployment-patterns)
- [Inventory Structure](#inventory-structure)
- [Security Patterns](#security-patterns)
- [Node Scheduling](#node-scheduling)
- [Network Architecture](#network-architecture)
- [Extension Guidelines](#extension-guidelines)

---

## Overview

H3xInfra is a declarative infrastructure framework that uses Helm. It deploys Kubernetes clusters for production use, with a full platform stack. The framework has these primary properties:

- **Layered Architecture**: The framework deploys dependent components in a set order.
- **Dual-Stack Networking**: All parts of the stack have full IPv4/IPv6 support.
- **GitOps-Ready**: All configuration is code, with Ansible and Helm.
- **Extensible Design**: Clear patterns show how to add custom services.
- **Security-First**: Vault encryption, network policies, and TLS automation.

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
│   ├── 01-priorityclasses/ # Cluster-wide PriorityClass definitions
│   ├── 02-cilium/         # CNI networking layer
│   ├── 03-metallb/        # Load balancer layer
│   ├── 04-health/         # Health endpoint layer
│   ├── 05-certmanager/    # Certificate management layer
│   ├── 06-externaldns/    # DNS automation layer
│   ├── 07-gateway/        # Envoy Gateway (Gateway API) controller layer
│   ├── 08-smb/            # SMB CSI driver layer
│   ├── 09-longhorn/       # Storage system layer
│   ├── 10-kured/          # Rolling reboot daemon (kured)
│   ├── 10-mailu/          # Email server layer
│   ├── 10-keycloak/       # Identity provider layer (Operator + CNPG)
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
│   ├── h3xinfra-gen-pass
│   ├── kube-connect
│   └── ...
├── ansible.cfg            # Ansible configuration
└── README.md
```

---

## Layer Architecture

### Layer Numbering System

Each layer has a number. The numbers group related layers. The imports in `playbooks/stack-standup.yml` set the deployment order:

#### Core Infrastructure (00-09)
- **00-prep**: Server preparation, system configuration, and package installation.
- **01-k3s**: Deploys the K3s Kubernetes cluster.
- **01-priorityclasses**: Custom `PriorityClass` definitions (`h3xinfra-platform-critical`, `h3xinfra-app-background`). Their numeric priorities influence scheduling, preemption and node-pressure eviction; resource usage relative to requests also affects eviction.
- **02-cilium**: Cilium CNI with eBPF networking.
- **03-metallb**: MetalLB load balancer for bare metal.
- **04-health**: Health check endpoints and monitoring.
- **05-certmanager**: Cert-manager for automated TLS certificates.
- **06-externaldns**: External-DNS for automated DNS management.
- **07-gateway**: The Envoy Gateway controller and a shared Gateway with wildcard TLS.
- **08-smb**: SMB/CIFS CSI driver for network storage.
- **09-longhorn**: Longhorn distributed block storage.

#### Platform Services (10-19)
- **10-kured**: Kubernetes Reboot Daemon. It does rolling reboots of the nodes when `/var/run/reboot-required` is set. For example, kernel updates from unattended-upgrades set this file.
- **10-mailu**: Self-hosted email server (Mailu).
- **10-keycloak**: Identity provider (Keycloak Operator + CloudNativePG). Terraform manages the realm/clients.
- **10-argocd**: GitOps controller with native Keycloak SSO. Deploy its layer directly after provisioning Keycloak; the public stack does not include it. Existing resources keep their current owners.

#### Utilities (99)
- **99-utils**: Shared utilities, for example kube-connect and cluster status.

### Dependency Flow
```
00-prep (servers ready)
  ↓
01-k3s (cluster running)
  ↓
01-priorityclasses (scheduling classes ready)
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
10-mailu / 10-keycloak (platform services — independent, deploy in any order)
  ↓
11+ (downstream applications deploy)
```

---

## Playbook Patterns

### Standard Layer Structure
All numbered layers use this structure:

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

    # 4. Create namespace with Pod Security labels (see playbooks/01-k3s/README.md)
    - name: Create {Service} namespace
      kubernetes.core.k8s:
        state: present
        wait: true
        definition:
          apiVersion: v1
          kind: Namespace
          metadata:
            name: "{{ servicename.namespace }}"
            labels:
              pod-security.kubernetes.io/enforce: "{{ servicename.pod_security_enforce | default('restricted') }}"
              pod-security.kubernetes.io/audit: restricted
              pod-security.kubernetes.io/warn: restricted

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
        chart_version: "{{ chart_versions.servicename }}"
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
- name: Require confirmation before teardown
  import_playbook: ../99-utils/confirm-destroy.yml

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
All Helm releases must use this naming convention:

```
h3xinfra-{service}-{phase}
```

**Components:**
- `h3xinfra-`: The fixed prefix for all releases.
- `{service}`: The service name. Use lowercase letters and digits, with hyphens between words when needed.
- `{phase}`: The deployment phase: `pre`, `main`, or `post`.

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
- Creates the prerequisites before the main deployment.
- Typical resources: Secrets, ConfigMaps, CRDs, ServiceAccounts.
- Examples:
  - External-DNS: Cloudflare API token secret.
  - Cert-Manager: Cloudflare DNS solver secret.
  - Envoy Gateway: shared wildcard `Certificate` + MetalLB `IPAddressPool` + `Gateway` listener. `h3xinfra-gateway-pre` renders these resources.
  - Longhorn: RecurringJob CRDs, backup secrets.

#### `main` Phase
- The primary application deployment.
- It usually references an external Helm chart or a custom chart.
- It always uses: `fullnameOverride: "{{ service.release_name_prefix }}-main"`

#### `post` Phase
- Configuration after the deployment.
- Typical resources: ClusterIssuers and other integrations.
- Examples:
  - Cert-Manager: Let's Encrypt ClusterIssuer.
  - Longhorn: RecurringJob instances.

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
    chart_version: "{{ chart_versions.service }}"
    # ... main application

# Post-chart creates ClusterIssuers, etc.
- name: Deploy {Service} post-configuration via Helm
  kubernetes.core.helm:
    name: "{{ service.release_name_prefix }}-post"
    chart_ref: "{{ playbook_dir }}/charts/h3xinfra-service-post"
    # ... post-configuration
```

### Pattern 2: Simple External Chart

**Used by:** SMB CSI driver

**Structure:**
```yaml
- name: Deploy {Service} via Helm
  kubernetes.core.helm:
    name: "{{ service.release_name_prefix }}-main"
    chart_ref: "{repo}/{chart}"
    chart_version: "{{ chart_versions.service }}"
    values:
      # Simple inline configuration
```

### Pattern 3: Fully Custom Chart

**Used by:** Custom applications and complex configurations

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

All Kubernetes service configurations use a **virtual `k8s` host**. This host keeps the infrastructure configuration clearly separate from the service configuration.

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

Service files in `host_vars/k8s/` hold configuration. Chart versions live in
`group_vars/all/versions.yml` as `chart_versions.<service>`. Use each chart's
values schema; this is an example for a service with Gateway routing:

```yaml
---
servicename:
  namespace: "servicename"
  release_name_prefix: "h3xinfra-servicename"
  
  # Service exposure
  service:
    type: "ClusterIP"  # or LoadBalancer
  
  # Ingress configuration
  ingress:
    enabled: true
    hostname: "service.domain.com"
    gateway:
      name: "h3xinfra-gateway-pre-shared"
      namespace: "envoy-gateway-system"
  
  # Persistence
  persistence:
    enabled: true
    storageClass: "longhorn"
    size: "10Gi"
  
  # Service-specific configuration
  # ...
```

### Secrets Management

**Shared secrets in `group_vars/all/secrets.yml`:**
```yaml
---
# Global secrets used across multiple services
cloudflare_api_token: !vault |
  $ANSIBLE_VAULT;1.1;AES256
  ...

k3s_token: !vault |
  $ANSIBLE_VAULT;1.1;AES256
  ...
```

**Service-specific secrets in the service files:**
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

One shared **Envoy Gateway** Gateway does all HTTP routing. The name of
this Gateway is `{{ gateway.release_name_prefix }}-pre-shared`, in the
namespace `{{ gateway.namespace }}`. The Gateway terminates TLS with one
wildcard certificate configured for the Gateway hostnames. The chart of each
app renders Gateway-API resources, not `Ingress` objects.

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
| Upstream TLS verify | `gateway.networking.k8s.io/v1 BackendTLSPolicy` |

#### Public Services
Gateway-facing apps normally use the shared wildcard certificate instead of
a `Certificate` for each app. Mailu retains a separate certificate for its
mail protocols and HTTPS backend. The shared wildcard is an intentional
privacy decision: a certificate for one host goes into the public **Certificate
Transparency logs**. Then anyone can easily find each service with CT
search engines, for example crt.sh. A wildcard (`*.app.example.com`)
shows the wildcard label instead of each service name in that certificate's
CT entry. ExternalDNS watches Service and Ingress resources with the
`external-dns.alpha.kubernetes.io/publish=true` label. The shared Gateway's
managed Service has no publish metadata in this repository; creating a
Gateway or HTTPRoute does not itself publish a DNS record. Configure DNS
for the Gateway addresses separately.

#### Internal Services
A service that only internal networks can access uses `trustedIPs`
together with the `oidc.enabled: false` block. The chart renders a
`SecurityPolicy` with `authorization.{defaultAction: Deny, rules:[{action: Allow,
principal.clientCIDRs: <trustedIPs>}]}`. This policy replaces the previous
`nginx.ingress.kubernetes.io/whitelist-source-range` annotation.

#### Pre-Provisioned / Upstream HTTPS Backends
Some backends serve HTTPS themselves. The Mailu pre-chart renders a
`BackendTLSPolicy` targeting its front Service, with
`wellKnownCACertificates: System` and the certificate hostname. For a backend
with a private CA, configure a trusted CA reference instead of disabling
certificate verification; a ConfigMap CA reference uses the key `ca.crt`.

### Network Policy Pattern

Use this pattern for services that must have network isolation (for example, Mailu):

```yaml
networkPolicy:
  enabled: true
  allowedIPv6Networks:
  - "2001:db8::/32"
  - "fd00::/8"
```

### IP Allowlist Standard

```yaml
trusted_ips:
- "10.0.0.0/8"         # RFC1918 - Private IPv4
- "172.16.0.0/12"      # RFC1918 - Private IPv4  
- "192.168.0.0/16"     # RFC1918 - Private IPv4
- "fd00::/8"           # RFC4193 - ULA IPv6
```

---

## Node Scheduling

Priority values influence eviction, but they do not define a complete eviction
order. Kubernetes also considers usage relative to resource requests; see
[pod selection for kubelet eviction](https://kubernetes.io/docs/concepts/scheduling-eviction/node-pressure-eviction/#pod-selection-for-kubelet-eviction).

### Dedicated control-plane with the `CriticalAddonsOnly` taint

Server nodes (k3s control-plane + etcd) have the taint
`CriticalAddonsOnly=true:NoSchedule`. This taint keeps stateful
workloads off the nodes that hold the quorum. The results are:

- An app with a fault cannot cause an OOM of `kube-apiserver` / `etcd` /
  `kubelet` on a node whose loss would affect quorum.
- A server reboot affects only the control-plane availability. It does
  not affect the storage.
- Longhorn replicas, CNPG Postgres data, *arr SQLite DBs, and other
  stateful data all live on agent nodes.

### When to add `tolerations: [{operator: Exists}]`

A chart DaemonSet (or other workload) needs the universal toleration
**only if it must run on all nodes, also on the tainted servers**.
Examples in this repository:

| Pattern | Tolerate `CriticalAddonsOnly`? | Why |
|---|---|---|
| CNI agent (Cilium) | yes | networking must be available on every node |
| Reboot daemon (kured) | yes | a node without a kured pod does not reboot automatically |
| Node-local CSI plugin (csi-driver-smb) | yes | mounts occur on the node where the pod runs |
| Longhorn manager / engine-image / csi-plugin DaemonSets | **no** (intentionally) | storage is a workload tier. Servers do not hold storage. |
| App workloads (Plex, *arr, qbittorrent, Keycloak, Mailu, …) | **no** | never run on a server node |

In the `values.yaml` of a chart or in the `helm.values` of a playbook, use this form:

```yaml
tolerations:
- operator: Exists       # tolerate any taint — system DaemonSet pattern
```

If you set `tolerations` in the helm values, your value **replaces** the
default tolerations of the chart. It does not add to them. The upstream
chart can already tolerate `node-role.kubernetes.io/control-plane` or
other taints. If it does, the universal toleration above includes all of
those tolerations.

### When NOT to add it

Do not add the universal toleration for ordinary apps. Ordinary apps are
single-replica or multi-replica workloads that are not node-local. Let
the scheduler limit them to the agent pool. That is the purpose of the
taint.

---

## Network Architecture

### Dual-Stack Configuration

All services support IPv4 and IPv6:

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
    ipv6: "2001:db8:7c20:a475:face::100/128"
```

### DNS Management

**External-DNS configuration:**
```yaml
externaldns:
  domain_filters:
  - "example.com"
  - "*.example.com"
```

The playbook uses Service/Ingress sources and a publish-label filter. To
publish a Service, its metadata must include the label and the desired DNS
hostname annotation; these are not `externaldns` inventory fields:

```yaml
metadata:
  labels:
    external-dns.alpha.kubernetes.io/publish: "true"
  annotations:
    external-dns.alpha.kubernetes.io/hostname: "service.example.com"
```

**Wildcard certificate for the shared Gateway:**
```yaml
gateway:
  domain: "example.com"  # Certificate covers apex, *.example.com and *.app.example.com.
```

Gateway chart 0.4.0 derives its five listeners and certificate DNS names from
`gateway.domain`. Replace the old `gateway.dns_names` and `gateway.listeners`
inventory fields with this apex domain when upgrading the chart. HTTP stays
on port 80 and HTTPS on port 443; the listener names and TLS Secret stay the same.

---

## Extension Guidelines

### Add a New Infrastructure Layer

#### 1. Select the Layer Number
- Core infrastructure: 00-09
- Platform services: 10-19
- Utilities: 99

#### 2. Create the Layer Structure
```bash
mkdir -p playbooks/XX-servicename/charts
```

#### 3. Create the Playbooks

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
# Stops here unless run with -e confirm_destroy=yes-destroy-data.
- name: Require confirmation before teardown
  import_playbook: ../99-utils/confirm-destroy.yml

- import_playbook: ../99-utils/kube-connect.yml

- name: Teardown NewService deployment
  hosts: k8s
  gather_facts: false
  tasks:
    # Follow teardown pattern...
```

#### 4. Create the Inventory Variables

**`inventory/production/host_vars/k8s/newservice.yml`:**
```yaml
---
newservice:
  namespace: "newservice"
  release_name_prefix: "h3xinfra-newservice"
  # ... configuration
```

Add its upstream chart pin to `inventory/production/group_vars/all/versions.yml`
and consume it as `chart_versions.newservice` in the Helm task.

#### 5. Add the Layer to the Stack Orchestration

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

When you create wrapper charts, use these files:

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
{{- define "h3xinfra-service-pre.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "h3xinfra-service-pre.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
app.kubernetes.io/name: {{ default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}
```

Resource-only charts need `fullname` and `labels` helpers. Add selector or
ServiceAccount helpers only when the chart creates resources that use them.

---

## Best Practices

### ✅ DO:

- **Use the virtual `k8s` host** for all Kubernetes service configurations.
- **Use the three-phase pattern** (pre/main/post) when services need prerequisites or post-configuration.
- **Set `fullnameOverride`** to keep the resource names consistent.
- **Use `wait: true`** with correct timeouts for all Helm deployments.
- **Use the `to_json` filter** with `set_values` for complex data structures.
- **Document service-specific requirements** in the README files of the layers.
- **Encrypt all secrets** with Ansible Vault.
- **Import the `kube-connect`** utility in all k8s-related playbooks.
- **Pin upstream chart versions** in `chart_versions` and pass the matching value to the Helm task's `chart_version`. Bespoke charts keep their version in `Chart.yaml`.
- **Tolerate all taints with `[{operator: Exists}]`** for DaemonSets that must run on every node (CNI agents, reboot daemon, node-local CSI plugins). Refer to [Node Scheduling](#node-scheduling).

### ❌ DO NOT:

- **Do not hard-code values** in playbooks. Use inventory variables.
- **Do not mix different naming patterns** for releases.
- **Do not deploy without correct namespace** isolation.
- **Do not skip the `kube-connect` import** for Kubernetes playbooks.
- **Do not use `shell` or `command` modules** when native k8s Ansible modules exist.
- **Do not forget to add services to the stack** orchestration playbooks.
- **Do not create ingress with cert-manager** for services that must not appear in CT logs.
- **Do not deploy external charts** without a pinned version.

---

## Tests and Validation

### Deploy Single Layer
```bash
ansible-playbook playbooks/XX-servicename/standup.yml -i inventory/production/hosts.yml
```

### Teardown Single Layer
```bash
ansible-playbook playbooks/XX-servicename/teardown.yml -i inventory/production/hosts.yml -e confirm_destroy=yes-destroy-data
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
kubectl get gateways,httproutes -A
```

---

## Helper Scripts

These scripts are in the `bin/` directory:

- **`h3xinfra-deploy-stack`** - Deploys the complete infrastructure stack.
- **`kube-connect`** - Makes a kubectl connection to the cluster.
- **`h3xinfra-gen-pass [variable_name]`** - Generates a named password that Ansible Vault encrypts.
- **`h3xinfra-setup-dependencies`** - Installs the Ansible dependencies.
- **`h3xinfra-setup-perms`** - Sets the file permissions.
- **`h3xinfra-setup-ssh`** - Configures SSH access.

---

## Summary

The h3xinfra framework is a reliable foundation for Kubernetes infrastructure that you can extend. It has these properties:

- **📦 Layered Architecture** - Clear dependency management and ordered deployment.
- **🏷️ Consistent Naming** - Resource names that you can predict, in all services.
- **⎈ Helm-Centric** - Uses the Helm ecosystem with custom configuration.
- **🔒 Security-First** - Vault encryption, network policies, and TLS automation.
- **🌐 Dual-Stack Ready** - Full IPv4/IPv6 support in all parts of the stack.
- **📚 Well-Documented** - Clear patterns and conventions.
- **🔧 Extensible** - It is easy to add new services with the established patterns.

Use these conventions to make sure that the full infrastructure deployment is consistent, easy to maintain, and reliable.
