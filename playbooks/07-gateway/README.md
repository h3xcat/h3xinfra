# Envoy Gateway restricted Pod Security

`standup.yml` defaults the Gateway namespace to `enforce: restricted`, with
`audit` and `warn` also set to `restricted`. Merging this source does not run
the playbook or change live namespace labels. No live admission checks or
rollout were performed for this change. Follow the attended sequence below:
complete steps 1–3 before changing the label, and step 5 before replacements
or the updated layer's reconciliation.

## Offline evidence and its limits

The reviewed Envoy Gateway pin is `1.10.0`, upstream commit
`02e9f89d1c1dc16b723de48fa3289dfa413a6e01`. Its
[generated default proxy Deployment](https://github.com/envoyproxy/gateway/blob/02e9f89d1c1dc16b723de48fa3289dfa413a6e01/internal/infrastructure/kubernetes/proxy/testdata/deployments/default.yaml)
contains `envoy` and `shutdown-manager`, both non-root with UID/GID 65532,
privilege escalation disabled, all capabilities dropped and RuntimeDefault
seccomp. There are no default init containers. The
[generator applies these security defaults independently of resource overrides](https://github.com/envoyproxy/gateway/blob/02e9f89d1c1dc16b723de48fa3289dfa413a6e01/internal/infrastructure/kubernetes/proxy/resource.go#L512-L553).
The local EnvoyProxy's Deployment overrides change only replicas, resources
and affinity, including a resources-only StrategicMerge patch for
shutdown-manager.

This evidence describes the pinned generator and reviewed overrides. It does
not establish the installed version, live Pods, admission mutations or future
overrides. The controller Helm render does not include the generated proxies.
Review the controller, certgen hook Job and generated proxy workloads
separately, including every normal, init and ephemeral container. A test that
checks only EnvoyProxy objects has checked no actual Pod templates.

## Attended deployment gate

1. Confirm the target kubeconfig/context, actual namespace and current PSA
   labels/version. The effective `gateway.pod_security_enforce` must be
   `restricted`; resolve any older inventory override before deployment.
   Record the installed and desired
   Gateway chart/images and Kubernetes version; do not infer them from the
   source pin. Check namespace, caller and RuntimeClass PSA exemptions so a
   passing check cannot bypass the policy. Coordinate a window without other
   rollouts, drains or upgrades. Record actual controller/proxy readiness,
   endpoint availability and working HTTP, HTTPS/TLS and authentication
   canaries, including both address families where configured.
2. The owner first runs the namespace-label dry run:

   ```sh
   kubectl label --dry-run=server --overwrite ns envoy-gateway-system pod-security.kubernetes.io/enforce=restricted
   ```

   Substitute the verified namespace if inventory uses another name. Review
   all warnings and errors; exit status zero alone is insufficient. Stop on
   any violation and fix the owning configuration before retrying. This
   [check examines existing Pods only](https://kubernetes.io/docs/tasks/configure-pod-container/enforce-standards-namespace-labels/#add-labels-to-existing-namespaces-with-kubectl):
   a completed/absent certgen hook or a future generated Pod is not covered.
3. Review the intended controller and certgen hook Pod templates from the
   exact Helm chart/values, and the actual generated proxy templates against
   the desired EnvoyProxy configuration. Include shutdown-manager and any
   additional init/ephemeral containers, injected sidecars, volumes and host
   access. Resolve differences from the pinned evidence before proceeding.
4. After those checks, the owner applies only the reviewed namespace label
   change and verifies `enforce=restricted` is active, with restricted audit
   and warning levels. Keep existing healthy Pods running while validating
   their replacements. Do not use the full standup playbook as a shortcut for
   this first label change: it also reconciles CRDs and Helm releases.
5. Under the active restricted policy, server-side dry-run **Pod creation**
   for each intended controller, certgen hook and generated proxy template:

   ```sh
   umask 077
   kubectl create --dry-run=server -n envoy-gateway-system -f reviewed-pods.yaml -o yaml > reviewed-pods.admitted.yaml
   ```

   Prepare that file from the complete intended Pod templates, preserving
   labels, annotations, ServiceAccounts and Pod specs. Use unique review names
   and omit server-generated identity/status fields. Keep both files in an
   owner-only directory outside Git and shared logs. Inspect the returned Pod
   specs for admission mutations and review all warnings/errors without
   publishing sensitive contents. Any proposed ephemeral-container addition
   requires a separate review through its `pods/ephemeralcontainers` API path;
   Pod creation does not validate that later operation. A successful Deployment or Job dry run
   alone is not enforcement proof: [PSA enforce applies to resulting Pods](https://kubernetes.io/docs/concepts/security/pod-security-admission/#workload-resources-and-pod-templates).
6. Only after these gates, perform the reviewed reconciliation and controlled
   replacements one at a time. Verify available capacity and healthy serving
   endpoints before each replacement; wait for the replacement to become
   Ready and recheck Gateway/route status, admission/events, TLS, routing and
   authentication canaries before continuing. Check both address families
   where configured. Retain serving capacity throughout; do not delete all
   proxies, assume replica counts, or combine this with a node drain. Inspect
   certgen hook completion whenever the approved reconciliation runs it.
7. Record the actual versions, labels, warning-free admission results,
   workload identities and availability/canary results. Source merge, label
   enforcement and successful attended rollout are separate milestones.

Dry-run admission does not prove scheduling, image startup, certificate
generation or traffic handling. Stop further replacements on an admission
denial, failed hook, lost readiness/endpoints or failed canary. Keep healthy
instances serving and correct the owning configuration. Do not lower PSA,
add exemptions, remove security fields or bypass checks to make the rollout
pass. Any recovery operation needs its own reviewed scope.

The namespace policy follows `latest` unless explicitly versioned. Repeat
this review for Kubernetes/Envoy Gateway upgrades or changes to generated
workload overrides; earlier success is not evidence for a new version. See
[the cluster PSA guidance](../01-k3s/README.md#pod-security-admission).
