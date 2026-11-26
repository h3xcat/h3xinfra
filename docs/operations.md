# Operational Procedures

This document gives the usual operational procedures for the H3x Kubernetes cluster.

For development tools and the container configuration, see the
[README](../README.md#development-environment).

## Regular Maintenance

### Server Updates

Unattended upgrades install security updates only. To also install non-security
package updates on the cluster nodes, run this command:

```bash
ansible k3s_cluster -b -m ansible.builtin.apt -a 'update_cache=true upgrade=safe'
```

Kured reboots a node when `/run/reboot-required` exists. Livepatch is enabled
on the x86 nodes. On these nodes, Ubuntu does not create that file for a kernel
install. `00-prep` adds the kernel hook
`/etc/kernel/postinst.d/zz-h3xinfra-reboot-required`. This hook creates the
file.

`00-prep` also installs `h3xinfra-patching-metrics.timer`. Every 15 minutes it
writes `/var/lib/node_exporter/textfile_collector/h3xinfra_patching.prom`:

| Metric | Value |
| --- | --- |
| `h3xinfra_unattended_upgrades_stamp_timestamp_seconds` | The mtime of `/var/lib/apt/periodic/unattended-upgrades-stamp`. After each completed unattended-upgrades run, apt touches this file. The value is 0 if the file does not exist. |
| `h3xinfra_dpkg_audit_lines` | The number of lines that `dpkg --audit` prints. The value is not 0 after an interrupted dpkg run. In this case, unattended-upgrades skips every run without an error. Fix the node by hand. |

### Cluster Status Check

To check the cluster health, run this command:

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

To generate a password as an inline Vault-encrypted inventory variable, run the
command below. Run it from the repository root, or set `ANSIBLE_CONFIG` to the
`ansible.cfg` of the repository:

```bash
h3xinfra-gen-pass service_password
```

The optional argument is the variable name. Its default is `password`. The
command prints only the encrypted value. The helper uses the Vault password
resolver from `ansible.cfg`.

### Read One Vaulted Value

To read one inline `!vault` inventory value, replace `variable_name` below
with its inventory key:

```bash
ansible k8s -m ansible.builtin.debug -a var=variable_name
```

`ansible-vault view` reads only whole encrypted files. It does not read inline
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

Use the directory of the layer that you need. Deploy its prerequisites in the
order that [stack-standup.yml](../playbooks/stack-standup.yml) gives. Before you
deploy a layer, read its guide for recovery, state and teardown constraints.
For example:

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
5. Copy `~/.ssh/h3xinfra_known_hosts` to `ssh_known_hosts` in the secrets directory that holds `ansible_ssh_privatekey`. With the read-only mount, this directory is `/h3xinfra-secrets`. Without it, the directory is `/h3xinfra/state/secrets`.

`h3xinfra-setup-ssh` installs that copy when you create or rebuild the devcontainer. A container restart does not install it again. After you change the copy on the share, run `bash "$H3XINFRA_FOLDER/scripts/h3xinfra-setup-ssh"`.

If you move `ansible_ssh_privatekey` to another secrets directory, copy the
reviewed, nonempty `ssh_known_hosts` to the same directory. Do this before you
rebuild the devcontainer. `h3xinfra-setup-ssh` reads both files from the same
directory. Before you connect, verify that the staged
`~/.ssh/h3xinfra_known_hosts` matches that copy. Without the pinned copy, a
fresh container accepts unknown host keys on the first contact. A move of the
private key alone does not keep the host-key pins.

If you reinstall a node, do these steps:

1. On the node console, show the fingerprint of the host key: `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`.
2. Remove the old entry: `ssh-keygen -f ~/.ssh/h3xinfra_known_hosts -R <ansible_host>`.
3. Get the new key: `ssh-keyscan -t ed25519 <ansible_host> > /tmp/new_host_key`.
4. Show the fingerprint of the new key: `ssh-keygen -lf /tmp/new_host_key`.
5. If the two fingerprints are not the same, stop. Do not connect to the node.
6. Add the new key: `cat /tmp/new_host_key >> ~/.ssh/h3xinfra_known_hosts`.
7. If you keep a pinned copy, copy the updated file to `ssh_known_hosts` in that secrets directory.

Do not set `host_key_checking=False` to bypass this error. If you do, Ansible can send credentials to a host that is not your node.

### Recovering Older Node Images

The November 2026 audit recorded all eight nodes clean of the retired iTCO
modules-load file and Pi hugepage migration remnants. On each run, the normal
`00-prep` no longer removes that file or the Pi `hugepages=N` boot arguments.
It also no longer sets the Pi hugepage pool to zero. Because of that last
change, `00-prep` no longer fixes later Pi hugepage drift automatically.
`00-prep` still loads the hardware watchdog. The CPU C-state limits and the
separate capture-kernel zero-hugepage controls also remain active.

For an image from before May 2026, do one of these steps before you run the
current automation:

- Use a fresh supported installation.
- Review the
  [pre-retirement source](https://github.com/coderpunch/h3xinfra/blob/82e7d2d7ee9ec5a654fdc8e267ee92ea74038b4d/playbooks/00-prep/standup.yml).
  Do only the applicable retired cleanup steps from it.

When you select those steps, check the configuration and the runtime state of
the restored node. Do not replay the entire old prep playbook. This recovery
constraint does not mean that you must delete retained images or backups. The
installed amd64 Longhorn V2 sysctl files and hugepage pools still need their
separate attended cleanup. The retirement of the Pi migration tasks does not
clean that node state.

### Access to Cluster Nodes

```bash
ssh ansible@<node-ip-or-hostname>   # e.g. ssh ansible@node01.example.com
```

`00-prep` allows only key-based SSH logins through
`/etc/ssh/sshd_config.d/10-h3xinfra.conf`. Before the first run on a node,
check that key login works for each account that you use. Also keep console
access.

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
