# Envoy Gateway restricted Pod Security

`standup.yml` defaults the Gateway namespace to `enforce: restricted`. It
also sets `audit` and `warn` to `restricted`. A merge of this source does not
run the playbook. It does not change the live namespace labels. There were no
live admission checks and no rollout for this change. Follow the attended
sequence below. Complete steps 1–3 before you change the label. Complete step 5
before the replacements or the reconciliation of the updated layer.

## Offline evidence and its limits

The reviewed Envoy Gateway pin is `1.10.0`, upstream commit
`02e9f89d1c1dc16b723de48fa3289dfa413a6e01`. Its
[generated default proxy Deployment](https://github.com/envoyproxy/gateway/blob/02e9f89d1c1dc16b723de48fa3289dfa413a6e01/internal/infrastructure/kubernetes/proxy/testdata/deployments/default.yaml)
contains `envoy` and `shutdown-manager`. Both run as non-root with UID/GID
65532. Both have privilege escalation disabled, all capabilities dropped and
RuntimeDefault seccomp. There are no default init containers. The
[generator applies these security defaults independently of resource overrides](https://github.com/envoyproxy/gateway/blob/02e9f89d1c1dc16b723de48fa3289dfa413a6e01/internal/infrastructure/kubernetes/proxy/resource.go#L512-L553).
The Deployment overrides of the local EnvoyProxy change only replicas,
resources and affinity. They include a resources-only StrategicMerge patch for
shutdown-manager.

This evidence describes the pinned generator and the reviewed overrides. It
does not prove the installed version, the live Pods, admission mutations or
future overrides. The controller Helm render does not include the generated
proxies. Review the controller, the certgen hook Job and the generated proxy
workloads separately. Include every normal, init and ephemeral container. A
test that checks only EnvoyProxy objects does not check actual Pod templates.

## Attended deployment gate

1. Verify the target kubeconfig/context, the actual namespace and the current
   PSA labels/version. The effective `gateway.pod_security_enforce` must be
   `restricted`. Before deployment, fix any older inventory override. Record
   the installed and desired Gateway chart/images and Kubernetes version. Do
   not infer them from the source pin. To make sure that a passed check cannot
   bypass the policy, check the namespace, caller and RuntimeClass PSA
   exemptions. Coordinate a window without other rollouts, drains or updates.
   Record the actual controller/proxy readiness and endpoint availability.
   Record which HTTP, HTTPS/TLS and authentication canaries work. Include each
   configured address family.
2. The owner first runs the namespace-label dry run:

   ```sh
   kubectl label --dry-run=server --overwrite ns envoy-gateway-system pod-security.kubernetes.io/enforce=restricted
   ```

   If the inventory uses another namespace name, use the verified namespace.
   Review all warnings and errors. Exit status zero alone is not sufficient.
   If there is a violation, stop. Fix the responsible configuration before you
   try again. This
   [check covers only the current Pods](https://kubernetes.io/docs/tasks/configure-pod-container/enforce-standards-namespace-labels/#add-labels-to-existing-namespaces-with-kubectl).
   It does not cover a completed/absent certgen hook or a future generated Pod.
3. Review the intended controller and certgen hook Pod templates from the
   exact Helm chart/values. Also review the actual generated proxy templates
   against the desired EnvoyProxy configuration. Include shutdown-manager and
   any additional init/ephemeral containers, injected sidecars, volumes and
   host access. Before you continue, fix the differences from the pinned
   evidence.
4. After those checks, the owner applies only the reviewed change to the
   namespace label. Then the owner verifies that `enforce=restricted` is
   active, with restricted audit and warning levels. Do not stop the current
   healthy Pods while you check their replacements. Do not use the full
   standup playbook as a shortcut for this first label change. That playbook
   also reconciles CRDs and Helm releases.
5. Do a server-side dry run of **Pod creation** under the active restricted
   policy. Do it for each intended controller, certgen hook and generated
   proxy template:

   ```sh
   umask 077
   kubectl create --dry-run=server -n envoy-gateway-system -f reviewed-pods.yaml -o yaml > reviewed-pods.admitted.yaml
   ```

   Prepare that file from the complete intended Pod templates. Keep their
   labels, annotations, ServiceAccounts and Pod specs. Use unique review
   names. Omit server-generated identity/status fields. Keep both files in an
   owner-only directory outside Git and shared logs. Check the returned Pod
   specs for admission mutations. Review all warnings/errors. Do not publish
   sensitive contents. A proposed addition of an ephemeral container needs a
   separate review through its `pods/ephemeralcontainers` API path. The
   admission check for Pod creation does not cover that later operation. A
   successful Deployment or Job dry run alone is not proof of enforcement.
   [PSA enforce applies to the Pods that result](https://kubernetes.io/docs/concepts/security/pod-security-admission/#workload-resources-and-pod-templates).
6. Only after these gates, do the reviewed reconciliation and the controlled
   replacements one at a time. Before each replacement, verify the available
   capacity and the healthy endpoints that serve traffic. Wait until the
   replacement becomes Ready. Then check the Gateway/route status,
   admission/events, TLS, routing and authentication canaries again before you
   continue. Check each configured address family. Keep capacity to serve
   traffic at all times. Do not delete all proxies. Do not assume replica
   counts. Do not combine this work with a node drain. Each time the approved
   reconciliation runs the certgen hook, check that the hook completes.
7. Record the actual versions and labels. Also record the warning-free
   admission results, the workload identities and the availability/canary
   results. The source merge, the label enforcement and a successful attended
   rollout are separate milestones.

A dry-run admission does not prove scheduling, image startup or certificate
generation. It also does not prove that the proxies handle traffic. Stop
further replacements if one of these occurs: an admission denial, a failed
hook, lost readiness/endpoints or a failed canary. Keep the healthy instances
in service. Fix the responsible configuration. Do not lower PSA, add
exemptions, remove security fields or bypass checks to make the rollout pass.
Any recovery operation needs its own reviewed scope.

The namespace policy follows `latest` if it has no explicit version. Do this
review again for each Kubernetes/Envoy Gateway version update and each change
to the generated workload overrides. An earlier success is not evidence for a
new version. See
[the cluster PSA guidance](../01-k3s/README.md#pod-security-admission).
