# Shared CloudNativePG operator ownership

`10-keycloak` owns the shared `cloudnative-pg` Helm release in `cnpg-system`.
It installs the operator before any Keycloak CNPG Cluster. The private Open
WebUI database consumes the same operator; it must not install a second copy.
Keycloak teardown retains the shared release, namespace and CRDs.

The chart repository is `https://cloudnative-pg.github.io/charts`; the version
comes from `chart_versions.cloudnative_pg`. `cnpg-values.yaml` retains the
operator budgets. The namespace keeps enforce/audit/warn PSA labels at
`restricted`, using the existing `h3xinfra-pod-security` field manager.
Helm upgrades reuse existing values without reset, replacement, forced
ownership, automatic uninstall or failure cleanup.

After adoption, ordinary runs can reconcile reviewed chart-pin and values-file
changes. Exact existing-values, pinned-manifest and live-CRD comparisons are
one-time adoption gates. Release identity, state and controller ownership/image
checks still run. Helm merges the reviewed values file into retained values;
unspecified existing values are reused. No manual Helm edit or additional
override is needed for an owned release's normal configuration updates.
Deleting a key from the desired file alone does not clear its installed value;
future removals need a reviewed explicit reset or migration.

## Fresh installations

On a fresh cluster, ordinary Keycloak standup installs the operator and waits
for the Cluster CRD and controller before creating the Keycloak database.
The private stack runs Keycloak before Terraform application layers, including
Open WebUI. A pre-created empty `cnpg-system` namespace is supported; the
installer checks native workloads/configuration and refuses orphaned operator
objects or conflicting namespace ownership/security settings.

The operator can also be installed by itself:

```sh
ansible-playbook -i inventory/production playbooks/10-keycloak/standup.yml --tags cnpg-operator
```

From a private checkout, prefix the playbook path with `h3xinfra/`. This tag
runs the connection guards and operator tasks only. It does not change
Keycloak clients/groups, deploy Keycloak charts or apply Keycloak Terraform.

## Adopting an existing installation

Source preparation is not authorization to migrate a live operator. The
existing owner must first relinquish both the Helm release and namespace PSA
labels without deleting either. For the private `20-openwebui` migration,
use its reviewed `removed` blocks with `lifecycle { destroy = false }` for
`helm_release.cnpg` and `kubernetes_labels.cnpg_pod_security`. The private
migration runbook supplies the state backup, forget-only plan and verification
steps. The namespace itself is not a separate Terraform namespace resource.

The coordinated source can merge after review and CI, before the deployment
window. An existing normal stack will then stop at this adoption guard until
the owner completes the handoff. Earlier stack stages may already have run;
the guard prevents this new owner from modifying the unadopted operator.

Freeze normal Keycloak/full-stack and old-owner runs during the handoff.
Verify the actual release identity, all stored values, controller image,
served CRD schemas, PSA labels and healthy databases. Freeze chart upgrades
for this change: the existing release must match the current pinned chart
and rendered manifests exactly. Do not make a mismatch pass by resetting
values, changing resource names or uninstalling/reinstalling the chart.

After applying and verifying the old owner's reviewed forget-only plan,
run only the operator task path once with the explicit acknowledgement:

```sh
ansible-playbook -i inventory/production playbooks/10-keycloak/standup.yml \
  --tags cnpg-operator -e cnpg_operator_handoff=yes-terraform-forgot-cnpg
```

The acknowledgement does not bypass identity, values, chart/manifest,
controller ownership or live CRD schema checks. An existing unmarked release
must be deployed and have its expected controller. A foreign, pending or
unmarked failed release requires separate investigation. No new Helm owner
is forced onto existing resources.

The namespace annotation `h3xinfra.io/cnpg-operator-owner: 10-keycloak`
records the orchestration owner for later runs. **It is not proof that a
Terraform state forgot the release or labels.** That evidence comes from the
owner's state handoff. The marker is written after preflight checks, before
Helm reconciliation, so a failed public install can leave it behind. A retry
still checks identity, state and controller ownership; a failed release must
match the current pin. Do not remove the marker or supply acknowledgement to
hide a mismatch.

Before resuming normal automation, verify continuous operator identity,
namespace labels, CRD identities/schemas and both databases. Review a fresh
old-owner plan and require no attempt to recreate or delete the operator or
its labels. Do not revert the old owner's resource declarations and apply;
returning ownership requires a separate reviewed handoff with public runs
frozen. Keep the migration blocks until every affected old state is proven
migrated.

## Source validation limits

Tests run the real task conditions against isolated stub Kubernetes/Helm
modules for fresh, already-owned, legacy, mismatched and partial-failure
cases. Real local Helm renders verify the pinned controller and CRD source.
These checks do not inspect a cluster, Terraform state or live schema drift;
owner verification remains required before deployment.

Required Kubernetes discovery failures are rejected explicitly; an unavailable
API is not evidence that resources are absent. The pinned `kubernetes.core`
6.6.0 `k8s_info` implementation nevertheless converts an HTTP 400 read into
`api_found: true, resources: []`, which its result cannot distinguish from an
empty response. Before a fresh install or handoff, the owner must independently
verify successful reads of the required namespace, controller and chart-object
identities and resolve API/query errors. The local mocks do not prove successful
live reads, and these guards do not remove that module limitation.
