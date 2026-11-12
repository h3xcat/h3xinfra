# Operational Procedures

This document gives the usual operational procedures for the H3x Kubernetes cluster.

For development tools and container setup, see the
[README](../README.md#development-environment).

## Regular Maintenance

### Server Updates

Unattended upgrades install security updates only. To also install non-security
package updates on the cluster nodes, run this command:

```bash
ansible k3s_cluster -b -m ansible.builtin.apt -a 'update_cache=true upgrade=safe'
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

To generate a password as an inline Vault-encrypted inventory variable, run this
command from the repository root (or with `ANSIBLE_CONFIG` set to its config):

```bash
h3xinfra-gen-pass service_password
```

The optional argument is the variable name; it defaults to `password`. Only the
encrypted value is printed. The helper uses the Vault password resolver from
`ansible.cfg`.

### Read One Vaulted Value

To inspect one inline `!vault` inventory value, replace `variable_name` below
with its inventory key:

```bash
ansible k8s -m ansible.builtin.debug -a var=variable_name
```

`ansible-vault view` reads files encrypted as a whole; it does not read inline
`!vault` values.

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

Use the directory of the layer you need, and deploy its prerequisites in the
order declared by [stack-standup.yml](../playbooks/stack-standup.yml). Read the
layer guide for recovery, state and teardown constraints first. For example:

```bash
layer=07-gateway
ansible-playbook "playbooks/$layer/standup.yml"
# Destructive: the confirmation requirement above also applies to one layer.
ansible-playbook "playbooks/$layer/teardown.yml" -e confirm_destroy=yes-destroy-data
```

## Troubleshooting

### Host Key Changed

Ansible checks node host keys against `~/.ssh/h3xinfra_known_hosts`. SSH adds the key of a new node on the first connection. SSH stops the connection when the key of a known node changes.

To keep the keys after a devcontainer rebuild, do these steps:

1. On the node console, show the fingerprint of the host key: `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`.
2. In the devcontainer, show the fingerprint that SSH recorded for the node: `ssh-keygen -F <ansible_host> -l -f ~/.ssh/h3xinfra_known_hosts`.
3. If the two fingerprints are not the same, stop. Do not copy the file.
4. Do steps 1 to 3 for each node.
5. Copy `~/.ssh/h3xinfra_known_hosts` to `ssh_known_hosts` in the secrets directory that holds `ansible_ssh_privatekey` (`/h3xinfra-secrets` with the read-only mount, else `/h3xinfra/state/secrets`).

`h3xinfra-setup-ssh` installs that copy when you create or rebuild the devcontainer. A container restart does not install it again. After you change the copy on the share, run `bash "$H3XINFRA_FOLDER/scripts/h3xinfra-setup-ssh"`.

When moving `ansible_ssh_privatekey` to another secrets directory, copy the
reviewed, nonempty `ssh_known_hosts` beside it before rebuilding. The setup
script reads both from the same directory. Verify that the staged
`~/.ssh/h3xinfra_known_hosts` matches that copy before connecting. Without the
pinned copy, a fresh container accepts unknown host keys on first contact;
moving the private key alone does not preserve host-key pinning.

If you reinstall a node, do these steps:

1. On the node console, show the fingerprint of the host key: `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`.
2. Remove the old entry: `ssh-keygen -f ~/.ssh/h3xinfra_known_hosts -R <ansible_host>`.
3. Get the new key: `ssh-keyscan -t ed25519 <ansible_host> > /tmp/new_host_key`.
4. Show the fingerprint of the new key: `ssh-keygen -lf /tmp/new_host_key`.
5. If the two fingerprints are not the same, stop. Do not connect to the node.
6. Add the new key: `cat /tmp/new_host_key >> ~/.ssh/h3xinfra_known_hosts`.
7. If you keep a pinned copy, copy the updated file to `ssh_known_hosts` in that secrets directory.

Do not set `host_key_checking=False` to bypass this error. If you do, Ansible can send credentials to a host that is not your node.

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
kube-connect gateway           # Connect and switch to namespace containing 'gateway'
kube-connect keycloak          # Connect and switch to namespace containing 'keycloak'
```
