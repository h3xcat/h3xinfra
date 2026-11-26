# Ansible inventory

`production/` is an example inventory. Configure it for your cluster before
you use it. If this repository is a submodule, use the root inventory of the
private repository. The submodule inventory is not a second production input.

| Path under `production/` | Owns |
| --- | --- |
| `hosts.yml` | The `server` and `agent` groups, SSH connections and the local virtual host `k8s` |
| `group_vars/all/main.yml`, `network.yml` | Shared settings and networks |
| `group_vars/all/versions.yml` | Central chart and image pins |
| `group_vars/all/secrets.yml` | Shared Vault-encrypted values |
| `group_vars/k3s_cluster/main.yml` | Node and K3s configuration |
| `host_vars/k8s/*.yml` | Platform service settings |

The `k8s` host uses `ansible_connection: local`. Thus, Kubernetes tasks run
on the Ansible controller. A play that targets `k8s` gets its service
variables directly. Other plays can reference `hostvars['k8s']` if necessary.

Keep all credentials encrypted with Ansible Vault. This also applies to
service-specific secrets next to their service settings. `ansible.cfg` uses
`bin/h3xinfra-vault-pass` to get the Vault password. Never commit the Vault
password.

Run Ansible from the repository root with its `ansible.cfg`, or pass the
inventory explicitly:

```bash
ansible-inventory -i inventory/production --graph
ansible-playbook -i inventory/production playbooks/stack-standup.yml
```

See the [operations guide](../docs/operations.md) for layer deployment,
teardown confirmation and host-key verification.
