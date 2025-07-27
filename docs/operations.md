# Operational Procedures

This document describes common operational procedures for the H3x Kubernetes cluster.

## Development Environment

### Devcontainer Setup

This project includes a complete devcontainer configuration for consistent development across different machines.

#### Features
- **Base Image**: Ubuntu 24.04 with custom user setup
- **Pre-installed Tools**:
  - Ansible with all required collections
  - kubectl and Helm for Kubernetes management
  - Docker CLI (connected to host Docker daemon)
  - Python 3 with required packages
- **VS Code Extensions**:
  - Python support
  - GitHub Copilot integration
- **Environment Configuration**:
  - `WORKSPACE_FOLDER` environment variable
  - `H3XINFRA_FOLDER` environment variable pointing to public h3xinfra repo
  - `ANSIBLE_CONFIG` pointing to project ansible.cfg
  - `bin/` directory added to PATH
- **Volume Mounts**:
  - Host SSH keys mounted at `/tmp/host-ssh`
  - Persistent bash history across container rebuilds

#### Getting Started with Devcontainer

1. **Prerequisites**:
   ```bash
   # Ensure Docker is running
   docker --version
   
   # Install VS Code Dev Containers extension
   code --install-extension ms-vscode-remote.remote-containers
   ```

2. **Open Project**:
   - Open the project folder in VS Code
   - Click "Reopen in Container" when prompted
   - Or use Command Palette: "Dev Containers: Reopen in Container"

3. **Verify Setup**:
   ```bash
   # Check available tools
   ansible --version
   kubectl version --client
   helm version
   
   # Verify custom commands are available
   which h3xinfra-deploy-stack
   which kube-connect
   ```

#### SSH Key Configuration

The devcontainer automatically mounts your host SSH keys for accessing cluster nodes:

```bash
# SSH keys are available at:
ls -la /tmp/host-ssh/

# If needed, copy to standard location:
cp -r /tmp/host-ssh ~/.ssh
chmod 700 ~/.ssh
chmod 600 ~/.ssh/*
```

## Regular Maintenance

### Server Updates

To update all servers in the cluster:

```bash
h3xinfra-update-servers
```

### Cluster Status Check

To verify the cluster health:

```bash
ansible-playbook playbooks/99-utils/cluster-status.yml
```

## Utility Scripts

### Deploy Complete Stack

Use the convenience script to deploy the entire cluster:

```bash
h3xinfra-deploy-stack
```

### Generate Passwords

Generate encrypted passwords for services:

```bash
h3xinfra-gen-pass
```

### Generate K3s Token

Generate a new K3s cluster token:

```bash
h3xinfra-generate-token
```

## Component Management

### Deploy Complete Cluster

```bash
ansible-playbook playbooks/stack-standup.yml
```

### Tear Down Cluster

```bash
ansible-playbook playbooks/stack-teardown.yml
```

## Individual Component Management

### Server Preparation

```bash
ansible-playbook playbooks/00-prep/standup.yml
```

### K3s Installation/Removal

```bash
ansible-playbook playbooks/01-k3s/standup.yml
ansible-playbook playbooks/01-k3s/teardown.yml
```

### Cilium Deployment/Removal

```bash
ansible-playbook playbooks/02-cilium/standup.yml
ansible-playbook playbooks/02-cilium/teardown.yml
```

### MetalLB Deployment/Removal

```bash
ansible-playbook playbooks/03-metallb/standup.yml
ansible-playbook playbooks/03-metallb/teardown.yml
```

### Health Endpoints Configuration

```bash
ansible-playbook playbooks/04-health/standup.yml
ansible-playbook playbooks/04-health/teardown.yml
```

### Certificate Manager Deployment/Removal

```bash
ansible-playbook playbooks/05-certmanager/standup.yml
ansible-playbook playbooks/05-certmanager/teardown.yml
```

### External DNS Deployment/Removal

```bash
ansible-playbook playbooks/06-externaldns/standup.yml
ansible-playbook playbooks/06-externaldns/teardown.yml
```

### Ingress NGINX Deployment/Removal

```bash
ansible-playbook playbooks/07-ingressnginx/standup.yml
ansible-playbook playbooks/07-ingressnginx/teardown.yml
```

### Longhorn Storage Deployment/Removal

```bash
ansible-playbook playbooks/08-longhorn/standup.yml
ansible-playbook playbooks/08-longhorn/teardown.yml
```

### Mailu Email Server Deployment/Removal

```bash
ansible-playbook playbooks/09-mailu/standup.yml
ansible-playbook playbooks/09-mailu/teardown.yml
```

### Keycloak Identity Management Deployment/Removal

```bash
ansible-playbook playbooks/10-keycloak/standup.yml
ansible-playbook playbooks/10-keycloak/teardown.yml
```

## Troubleshooting

### Devcontainer Issues

#### Container Won't Start
```bash
# Rebuild the container
# In VS Code: Command Palette > "Dev Containers: Rebuild Container"

# Or manually:
docker system prune -f
# Then reopen in container
```

#### SSH Keys Not Working
```bash
# Check if keys are mounted
ls -la /tmp/host-ssh/

# Copy and fix permissions if needed
cp -r /tmp/host-ssh ~/.ssh
chmod 700 ~/.ssh
chmod 600 ~/.ssh/id_*
```

#### Missing Environment Variables
```bash
# Check environment setup
echo $WORKSPACE_FOLDER
echo $H3XINFRA_FOLDER
echo $ANSIBLE_CONFIG
echo $PATH | grep bin

# If missing, restart the container
```

#### Command Not Found Errors
```bash
# Verify bin directory is in PATH
echo $PATH | grep -o "${H3XINFRA_FOLDER}/bin"

# If missing, restart the container or manually add:
export PATH="${PATH}:${H3XINFRA_FOLDER}/bin"
```

### Cluster Access Issues

### Accessing Cluster Nodes

```bash
ssh ansible@10.12.0.15  # For h3xsrv01
ssh ansible@10.12.0.16  # For h3xsrv02
ssh ansible@10.12.0.17  # For h3xsrv03
```

### Checking Logs

```bash
# On a cluster node
sudo crictl logs <container-id>
sudo journalctl -u k3s

# Using kubectl
kubectl logs -n <namespace> <pod-name>
```

### Kubernetes Connection

To connect to the cluster and optionally switch to a specific namespace:

```bash
kube-connect [namespace_substring]
```

Examples:
```bash
kube-connect                    # Connect to default namespace
kube-connect nginx             # Connect and switch to namespace containing 'nginx'
kube-connect keycloak          # Connect and switch to namespace containing 'keycloak'
```
