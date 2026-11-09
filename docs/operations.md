# Operational Procedures

This document gives the usual operational procedures for the H3x Kubernetes cluster.

## Development Environment

### Devcontainer Setup

This project has a full devcontainer configuration. Use it to get the same development environment on different machines.

#### Features
- **Base Image**: Ubuntu 22.04 (jammy) with a custom user setup.
- **Pre-installed Tools**:
  - Ansible with all the necessary collections.
  - kubectl and Helm to manage Kubernetes.
  - Docker CLI. The CLI connects to the Docker daemon on the host.
  - Python 3 with the necessary packages.
- **VS Code Extensions**:
  - Python support.
  - GitHub Copilot integration.
- **Environment Configuration**:
  - The `WORKSPACE_FOLDER` environment variable.
  - The `H3XINFRA_FOLDER` environment variable. It points to the public h3xinfra repo.
  - `ANSIBLE_CONFIG`. It points to the ansible.cfg file of the project.
  - The `bin/` directory is in PATH.
- **Volume Mounts**:
  - The host SSH keys are mounted at `/tmp/host-ssh`.
  - The bash history stays when you rebuild the container.

#### Start the Devcontainer

1. **Prerequisites**:
   ```bash
   # Ensure Docker is running
   docker --version
   
   # Install VS Code Dev Containers extension
   code --install-extension ms-vscode-remote.remote-containers
   ```

2. **Open Project**:
   - Open the project folder in VS Code.
   - When VS Code shows the prompt, click "Reopen in Container".
   - As an alternative, use the Command Palette: "Dev Containers: Reopen in Container".

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

The devcontainer automatically mounts your host SSH keys. You use these keys to get access to the cluster nodes:

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

To update all the servers in the cluster, run this command:

```bash
h3xinfra-update-servers
```

### Cluster Status Check

To examine the cluster health, run this command:

```bash
ansible-playbook playbooks/99-utils/cluster-status.yml
```

## Utility Scripts

### Deploy Complete Stack

To deploy the full cluster, use this script:

```bash
h3xinfra-deploy-stack
```

### Generate Passwords

To generate encrypted passwords for services, run this command:

```bash
h3xinfra-gen-pass
```

### Generate K3s Token

To generate a new K3s cluster token, run this command:

```bash
h3xinfra-generate-token
```

## Component Management

### Deploy Complete Cluster

```bash
ansible-playbook playbooks/stack-standup.yml
```

### Tear Down Cluster

Every teardown playbook stops at the start unless you give the confirmation phrase `-e confirm_destroy=yes-destroy-data`.

> **WARNING:** Do not set `confirm_destroy` in the inventory. If you do, every teardown can start without confirmation and delete volume data.

```bash
ansible-playbook playbooks/stack-teardown.yml -e confirm_destroy=yes-destroy-data
```

## Individual Component Management

### Server Preparation

```bash
ansible-playbook playbooks/00-prep/standup.yml
```

### K3s Installation/Removal

```bash
ansible-playbook playbooks/01-k3s/standup.yml
ansible-playbook playbooks/01-k3s/teardown.yml -e confirm_destroy=yes-destroy-data
```

### Cilium Deployment/Removal

```bash
ansible-playbook playbooks/02-cilium/standup.yml
ansible-playbook playbooks/02-cilium/teardown.yml -e confirm_destroy=yes-destroy-data
```

### MetalLB Deployment/Removal

```bash
ansible-playbook playbooks/03-metallb/standup.yml
ansible-playbook playbooks/03-metallb/teardown.yml -e confirm_destroy=yes-destroy-data
```

### Health Endpoints Configuration

```bash
ansible-playbook playbooks/04-health/standup.yml
ansible-playbook playbooks/04-health/teardown.yml -e confirm_destroy=yes-destroy-data
```

### Certificate Manager Deployment/Removal

```bash
ansible-playbook playbooks/05-certmanager/standup.yml
ansible-playbook playbooks/05-certmanager/teardown.yml -e confirm_destroy=yes-destroy-data
```

### External DNS Deployment/Removal

```bash
ansible-playbook playbooks/06-externaldns/standup.yml
ansible-playbook playbooks/06-externaldns/teardown.yml -e confirm_destroy=yes-destroy-data
```

### Envoy Gateway Deployment/Removal

```bash
ansible-playbook playbooks/07-gateway/standup.yml
ansible-playbook playbooks/07-gateway/teardown.yml -e confirm_destroy=yes-destroy-data
```

### SMB/CIFS CSI Driver Deployment/Removal

```bash
ansible-playbook playbooks/08-smb/standup.yml
ansible-playbook playbooks/08-smb/teardown.yml -e confirm_destroy=yes-destroy-data
```

### Longhorn Storage Deployment/Removal

```bash
ansible-playbook playbooks/09-longhorn/standup.yml
ansible-playbook playbooks/09-longhorn/teardown.yml -e confirm_destroy=yes-destroy-data
```

### Mailu Email Server Deployment/Removal

```bash
ansible-playbook playbooks/10-mailu/standup.yml
ansible-playbook playbooks/10-mailu/teardown.yml -e confirm_destroy=yes-destroy-data
```

### Keycloak Identity Management Deployment/Removal

```bash
ansible-playbook playbooks/10-keycloak/standup.yml
ansible-playbook playbooks/10-keycloak/teardown.yml -e confirm_destroy=yes-destroy-data
```

## Troubleshooting

### Devcontainer Issues

#### Container Does Not Start
```bash
# Rebuild the container
# In VS Code: Command Palette > "Dev Containers: Rebuild Container"

# Or manually:
docker system prune -f
# Then reopen in container
```

#### SSH Keys Do Not Work
```bash
# Check if keys are mounted
ls -la /tmp/host-ssh/

# Copy and fix permissions if needed
cp -r /tmp/host-ssh ~/.ssh
chmod 700 ~/.ssh
chmod 600 ~/.ssh/id_*
```

#### Host Key Changed

Ansible checks node host keys against `~/.ssh/h3xinfra_known_hosts`. SSH adds the key of a new node on the first connection. SSH stops the connection when the key of a known node changes.

To keep the keys after a devcontainer rebuild, do these steps:

1. On the node console, show the fingerprint of the host key: `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`.
2. In the devcontainer, show the fingerprint that SSH recorded for the node: `ssh-keygen -F <ansible_host> -l -f ~/.ssh/h3xinfra_known_hosts`.
3. If the two fingerprints are not the same, stop. Do not copy the file.
4. Do steps 1 to 3 for each node.
5. Copy `~/.ssh/h3xinfra_known_hosts` to `ssh_known_hosts` in the secrets directory that holds `ansible_ssh_privatekey` (`/h3xinfra-secrets` with the read-only mount, else `/h3xinfra/state/secrets`).

`h3xinfra-setup-ssh` installs that copy when you create or rebuild the devcontainer. A container restart does not install it again. After you change the copy on the share, run `bash "$H3XINFRA_FOLDER/scripts/h3xinfra-setup-ssh"`.

If you reinstall a node, do these steps:

1. On the node console, show the fingerprint of the host key: `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`.
2. Remove the old entry: `ssh-keygen -f ~/.ssh/h3xinfra_known_hosts -R <ansible_host>`.
3. Get the new key: `ssh-keyscan -t ed25519 <ansible_host> > /tmp/new_host_key`.
4. Show the fingerprint of the new key: `ssh-keygen -lf /tmp/new_host_key`.
5. If the two fingerprints are not the same, stop. Do not connect to the node.
6. Add the new key: `cat /tmp/new_host_key >> ~/.ssh/h3xinfra_known_hosts`.
7. If you keep a pinned copy, copy the updated file to `ssh_known_hosts` in that secrets directory.

Do not set `host_key_checking=False` to bypass this error. If you do, Ansible can send credentials to a host that is not your node.

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

### Access to Cluster Nodes

```bash
ssh ansible@<node-ip-or-hostname>   # e.g. ssh ansible@node01.example.com
```

### Logs

```bash
# On a cluster node
sudo crictl logs <container-id>
sudo journalctl -u k3s

# Using kubectl
kubectl logs -n <namespace> <pod-name>
```

### Kubernetes Connection

To connect to the cluster, run this command. You can also add a namespace substring to change to a specific namespace:

```bash
kube-connect [namespace_substring]
```

Examples:
```bash
kube-connect                    # Connect to default namespace
kube-connect nginx             # Connect and switch to namespace containing 'nginx'
kube-connect keycloak          # Connect and switch to namespace containing 'keycloak'
```
