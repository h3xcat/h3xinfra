# Ansible Inventory Structure

This directory contains the restructured Ansible inventory following best practices for maintainability and organization.

## Structure Overview

```
inventory/
└── production/
    ├── hosts.yml                    # Host definitions and groups
    ├── group_vars/
    │   ├── all/                     # Variables for all hosts
    │   │   ├── main.yml            # Global configuration
    │   │   ├── network.yml         # Network settings
    │   │   └── secrets.yml         # Encrypted vault variables
    │   └── k3s_cluster/
    │       └── main.yml            # K3s cluster configuration
    └── host_vars/
        └── k8s/                    # Kubernetes service configurations
            ├── authentik.yml       # Authentik identity provider
            ├── certmanager.yml     # Certificate management
            ├── cilium.yml          # CNI configuration  
            ├── externaldns.yml     # DNS management
            ├── ingressnginx.yml    # Ingress controller
            ├── longhorn.yml        # Storage configuration
            ├── mailu.yml           # Email server
            ├── metallb.yml         # Load balancer
            └── smb.yml             # SMB/CIFS support
```

## Key Design Decisions

### 1. **k8s Virtual Host**
- Created a virtual `k8s` host to organize all Kubernetes service configurations
- Uses `ansible_connection: local` for execution on the control node
- All service variables are organized under `host_vars/k8s/`

### 2. **Separation of Concerns**
- **hosts.yml**: Only host definitions and connection info
- **group_vars/all/**: Global variables used across all hosts
- **group_vars/k3s_cluster/**: Variables specific to the K3s cluster nodes
- **host_vars/k8s/**: Service-specific configurations

### 3. **Security**
- All vault-encrypted secrets consolidated in `group_vars/all/secrets.yml`
- Service-specific secrets remain in their respective service files
- Consistent use of variable references (e.g., `{{ storage_password }}`)

## Usage

### Basic Commands
```bash
# List all inventory
ansible-inventory --list

# Show specific host variables
ansible-inventory --host k8s --yaml

# Test connectivity
ansible all -m ping
```

### Running Playbooks
```bash
# Standard playbook execution (uses ansible.cfg inventory setting)
ansible-playbook playbooks/stack-standup.yml

# Override inventory if needed
ansible-playbook -i inventory/production playbooks/stack-standup.yml
```

### Accessing Service Variables
```yaml
# In playbooks, access service variables via the k8s host
- name: Deploy Mailu
  hosts: k8s
  vars:
    mailu_config: "{{ hostvars['k8s']['mailu'] }}"
```

## Benefits of This Structure

1. **Maintainability**: Each service has its own configuration file
2. **Scalability**: Easy to add new services or environments
3. **Security**: Centralized secret management with proper encryption
4. **Clarity**: Clear separation between infrastructure and service configs
5. **Flexibility**: Can easily target specific services or infrastructure components

## Setup Instructions

1. **Copy the example structure**: Use the provided example files as templates
2. **Customize hosts.yml**: Update with your actual server hostnames and IP addresses
3. **Configure group variables**: Set your network ranges and global settings
4. **Set up service configurations**: Customize each service in `host_vars/k8s/`
5. **Encrypt secrets**: Use `ansible-vault encrypt` on sensitive files like `secrets.yml`
6. **Test configuration**: Run `ansible-inventory --list` to verify structure