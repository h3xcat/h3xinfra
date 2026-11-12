# Ansible Inventory Structure

This directory contains the restructured Ansible inventory. The structure follows best practices to make the inventory easy to maintain and organize.

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
            ├── certmanager.yml     # Certificate management
            ├── cilium.yml          # CNI configuration  
            ├── externaldns.yml     # DNS management
            ├── gateway.yml         # Envoy Gateway controller + shared Gateway
            ├── longhorn.yml        # Storage configuration
            ├── mailu.yml           # Email server
            ├── metallb.yml         # Load balancer
            └── smb.yml             # SMB/CIFS support
```

## Key Design Decisions

### 1. **k8s Virtual Host**
- A virtual `k8s` host organizes all the Kubernetes service configurations.
- The host uses `ansible_connection: local`. Thus, its tasks run on the control node.
- All the service variables are in `host_vars/k8s/`.

### 2. **Separation of Concerns**
- **hosts.yml**: Only the host definitions and the connection information.
- **group_vars/all/**: Global variables that all hosts use.
- **group_vars/k3s_cluster/**: Variables that apply only to the K3s cluster nodes.
- **host_vars/k8s/**: Configurations that apply to one service.

### 3. **Security**
- All the vault-encrypted secrets are together in `group_vars/all/secrets.yml`.
- Secrets that apply to one service stay in the file of that service.
- The files use variable references in the same way everywhere. For example, `{{ dns_api_token }}`.

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

### Run Playbooks
```bash
# Standard playbook execution (uses ansible.cfg inventory setting)
ansible-playbook playbooks/stack-standup.yml

# Override inventory if needed
ansible-playbook -i inventory/production playbooks/stack-standup.yml
```

### Access Service Variables
```yaml
# In playbooks, access service variables via the k8s host
- name: Deploy Mailu
  hosts: k8s
  vars:
    mailu_config: "{{ hostvars['k8s']['mailu'] }}"
```

## Benefits of This Structure

1. **Maintainability**: Each service has its own configuration file.
2. **Scalability**: It is easy to add new services or environments.
3. **Security**: The secrets are in one location, with correct encryption.
4. **Clarity**: The infrastructure configuration and the service configuration are clearly separate.
5. **Flexibility**: You can easily target specific services or infrastructure components.

## Setup Instructions

1. **Copy the example structure**: Use the example files as templates.
2. **Customize hosts.yml**: Write your actual server hostnames and IP addresses in this file.
3. **Configure group variables**: Set your network ranges and global settings.
4. **Set up service configurations**: Customize each service in `host_vars/k8s/`.
5. **Encrypt secrets**: Use `ansible-vault encrypt` on sensitive files, for example `secrets.yml`.
6. **Test configuration**: Run `ansible-inventory --list` to make sure that the structure is correct.