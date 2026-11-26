# Shared CloudNativePG operator ownership

`10-keycloak` owns the shared `cloudnative-pg` Helm release in `cnpg-system`.
It installs the operator before any Keycloak CNPG Cluster. The private Open
WebUI database uses the same operator. It must not install a second copy. The
Keycloak teardown keeps the shared release, the namespace and the CRDs.

The owner's private `20-openwebui` handoff completed on 2026-11-13. Both old
Terraform addresses are absent. The fresh private plan has no changes. The
operator-only rerun without acknowledgement passed. The resource UIDs and
specifications did not change. The Helm release was at revision 10. The
operator did not restart.
The completed private forget-only blocks are retired. Normal runs use the
current ownership marker. They keep the checks that this document describes
below.

The chart repository is `https://cloudnative-pg.github.io/charts`. The version
comes from `chart_versions.cloudnative_pg`. `cnpg-values.yaml` keeps the
operator budgets. The namespace keeps its enforce/audit/warn PSA labels at
`restricted`. The playbook manages these labels with the current
`h3xinfra-pod-security` field manager.
Helm upgrades reuse the current values. They do not use these features:

- A reset of the values.
- Replacement.
- Forced ownership.
- Automatic uninstall.
- Cleanup after a failure.

After adoption, ordinary runs can reconcile reviewed changes to the chart pin
and the values file. The exact comparisons of current values, pinned manifests
and live CRDs are one-time adoption gates. The checks of release identity,
state and controller ownership/image still run. Helm merges the reviewed
values file into the retained values. Helm reuses the current values that the
file does not specify. Normal configuration updates of an owned release need
no manual Helm edit and no additional override. If you delete a key from the
desired file, this alone does not clear its installed value. A future removal
needs a reviewed explicit reset or migration.

## Fresh installations

On a fresh cluster, the ordinary Keycloak standup installs the operator. Then
it waits for the Cluster CRD and the controller. After that, it creates the
Keycloak database. The private stack runs Keycloak before the Terraform
application layers, for example Open WebUI. The installer supports a
pre-created empty `cnpg-system` namespace. It checks the native
workloads/configuration. It refuses orphaned operator objects. It also refuses
namespace ownership/security settings that conflict.

You can also install the operator by itself:

```sh
ansible-playbook -i inventory/production playbooks/10-keycloak/standup.yml --tags cnpg-operator
```

From a private checkout, prefix the playbook path with `h3xinfra/`. This tag
runs only the connection guards and the operator tasks. It does not change
Keycloak clients/groups, deploy Keycloak charts or apply Keycloak Terraform.

## Adopting an existing installation

Source preparation does not authorize the migration of a live operator.
First, the old owner must give up both the Helm release and the namespace PSA
labels. It must not delete either of them. The current source no longer
contains the completed private `20-openwebui` transition. A restored
pre-handoff state needs its
[compatible historical source](README.md#terraform-state). It also needs a
separately reviewed state upgrade before the current automation starts again.
The namespace itself was not a separate Terraform namespace resource.

The coordinated source can merge after review and CI, before the deployment
window. For an installation that exists, a normal stack run will then stop at
this adoption guard until the owner completes the handoff. Earlier stack
stages can run before the guard. The guard stops this new owner before it
changes the unadopted operator.

Freeze normal Keycloak/full-stack and old-owner runs during the handoff.
Verify these items:

- The actual release identity.
- All stored values.
- The controller image.
- The served CRD schemas.
- The PSA labels.
- The health of the databases.

Freeze chart updates for this change. The deployed release must match the
current pinned chart and the rendered manifests exactly. If there is a
mismatch, do not reset values, change resource names or uninstall/reinstall
the chart to make it pass.

Apply the reviewed forget-only plan of the old owner. Verify the result. Then
run only the operator task path one time with the explicit acknowledgement:

```sh
ansible-playbook -i inventory/production playbooks/10-keycloak/standup.yml \
  --tags cnpg-operator -e cnpg_operator_handoff=yes-terraform-forgot-cnpg
```

The acknowledgement does not bypass these checks:

- Identity.
- Values.
- Chart/manifest.
- Controller ownership.
- Live CRD schema.

An unmarked release that exists must have the deployed status. It must also
have its expected controller. A foreign release, a release in a `pending-*`
state, or an unmarked failed release needs a separate investigation. The
playbook does not force a new Helm owner onto resources that exist.

The namespace annotation `h3xinfra.io/cnpg-operator-owner: 10-keycloak`
records the orchestration owner for later runs. **It is not proof that a
Terraform state forgot the release or the labels.** That evidence comes from
the owner's state handoff. The playbook writes the marker after the preflight
checks and before the Helm reconciliation. Thus, a failed public install can
leave the marker behind. A retry still checks identity, state and controller
ownership. A failed release must match the current pin. Do not remove the
marker or supply the acknowledgement to hide a mismatch.

Before you start normal automation again, verify the continuous operator
identity, the namespace labels, the CRD identities/schemas and both databases.
Review a fresh plan of the old owner. This plan must not try to recreate or
delete the operator or its labels. Do not apply a revert of the resource
declarations of the old owner. A return of ownership needs a separate reviewed
handoff with frozen public runs. Keep the evidence of the state handoff, the
resource identity and the no-acknowledgement rerun with the completion record.
Older recovery copies follow [state recovery](README.md#terraform-state). They
do not need the completed transition blocks in the current source.

## Source validation limits

Tests run the real task conditions against isolated stub Kubernetes/Helm
modules. They cover these cases:

- Fresh installations.
- Releases that the layer already owns.
- Older installations.
- Mismatches.
- Partial failures.

Real local Helm renders verify the pinned controller and CRD source. These
checks do not read a cluster, a Terraform state or live schema drift. Before
deployment, the owner must still verify these items.

The playbook explicitly rejects failures of the required Kubernetes discovery.
An unavailable API is not evidence that resources are absent. But the pinned
`kubernetes.core` 6.6.0 `k8s_info` implementation converts an HTTP 400 read
into `api_found: true, resources: []`. Its result cannot distinguish this from
an empty response. Before a fresh install or handoff, the owner must
independently verify successful reads of the required namespace, controller
and chart-object identities. The owner must also fix API/query errors. The
local mocks do not prove successful live reads. These guards do not remove
that module limitation.
