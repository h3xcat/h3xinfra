# Ansible inventory

`production/` is an example inventory. Configure it for your cluster before
use. When this repository is a submodule, use the private repository's root
inventory; the submodule inventory is not a second production input.

| Path under `production/` | Owns |
| --- | --- |
| `hosts.yml` | Server/agent groups, SSH connections and the local virtual host `k8s` |
| `group_vars/all/main.yml`, `network.yml` | Shared settings and networks |
| `group_vars/all/versions.yml` | Central chart and image pins |
| `group_vars/all/secrets.yml` | Shared Vault-encrypted values |
| `group_vars/k3s_cluster/main.yml` | Node and K3s configuration |
| `host_vars/k8s/*.yml` | Platform service settings |

The `k8s` host uses `ansible_connection: local`: Kubernetes tasks run on the
control node. A play targeting `k8s` receives its service variables directly;
other plays can reference `hostvars['k8s']` when needed.

Keep credentials encrypted with Ansible Vault, including service-specific
secrets stored beside their service settings. `ansible.cfg` uses
`bin/h3xinfra-vault-pass` to resolve the Vault password; never commit it.

Run from the repository root with its `ansible.cfg`, or pass the inventory
explicitly:

```bash
ansible-inventory -i inventory/production --graph
ansible-playbook -i inventory/production playbooks/stack-standup.yml
```

See the [operations guide](../docs/operations.md) for layer deployment,
teardown confirmation and host-key verification.
