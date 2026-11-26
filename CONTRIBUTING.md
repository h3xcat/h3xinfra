# How to contribute to h3xinfra

This repository is the public core of h3xinfra. It contains the Ansible
playbooks, the bespoke charts and an example inventory for a dual-stack K3s
cluster. [README.md](README.md) describes the stack. This document tells you
how to send a change. The repository uses the [MIT License](LICENSE).

## Before you start

Read these documents before you change a layer:

- [README.md](README.md): the stack, the CI checks and the devcontainer.
- [CONVENTIONS.md](CONVENTIONS.md): the layer structure, the names, the Helm
  patterns, the inventory and the security patterns.
- [inventory/README.md](inventory/README.md): the structure of the inventory.
- [docs/architecture.md](docs/architecture.md) and
  [docs/operations.md](docs/operations.md): the cluster design and the
  operation procedures.
- The `README.md` of the layer, if it has one. An example is
  [playbooks/01-k3s/README.md](playbooks/01-k3s/README.md).

To add a layer, follow
[Add a New Infrastructure Layer](CONVENTIONS.md#add-a-new-infrastructure-layer).
Do not copy rules from `CONVENTIONS.md` into other documents. Link to them.

## Branches and pull requests

`main` is the default branch. A ruleset protects it. The ruleset requires the
`ci-ok` check from GitHub Actions. It also blocks force pushes and the
deletion of `main`.

1. Create a branch from `main`. Name it `<type>/<topic>`, for example
   `fix/mailu-externaldns-canonical-annotations`.
2. Keep one topic in each pull request.
3. Run the [checks](#run-the-checks-locally) on your computer.
4. Open a pull request to `main`. Write its title in the
   [commit message style](#commit-messages).
5. Wait until the `ci-ok` check passes.

GitHub Copilot reviews new pull requests to `main` automatically. It does not
review draft pull requests. The owner reviews your pull request and merges it
with a squash merge. GitHub adds the number of the pull request to the commit
title, for example `(#314)`.

### Pull requests from a fork

If you do not have write access, fork the repository. Then open the pull
request from a branch of your fork.

- CI uses no secrets. It runs the same checks on a pull request from a fork.
- GitHub Actions does not run CI on a pull request from a first-time
  contributor before the owner approves the run.
- If the owner asks for changes, push them to the same branch.

## Commit messages

Use the Conventional Commits form that the history of this repository uses:

```text
<type>(<scope>): <summary>
```

- `<type>` is one of `feat`, `fix`, `refactor`, `docs`, `chore`, `ci`,
  `test`, `security` or `revert`.
- `<scope>` is the layer without its number, or the area of the change. For
  example: `k3s`, `prep`, `mailu`, `keycloak`, `argocd`, `longhorn` or
  `renovate`. The scope is optional.
- `<summary>` starts with a verb in the base form and a lowercase letter. It
  has no period at the end.
- The body tells why the change is necessary. Its lines have a maximum of 72
  characters.

Examples from the history:

```text
fix(mailu): run Redis from the official image in the pre-chart
feat(argocd): render Helm charts from Git again; Jsonnet stays off
```

Renovate writes its own messages with the prefix `chore(deps):`.

## Run the checks locally

CI runs the `minimal` job and the `ci-ok` job. `ci-ok` fails if the `minimal`
job fails or if GitHub Actions cancels it.
[Continuous Integration](README.md#continuous-integration) lists the checks.
`.github/workflows/ci.yml` gives the exact commands and the pinned tool
versions.

The checks do not need a cluster or secrets.

CAUTION: Do not run a `standup.yml` or `teardown.yml` playbook to test a
change. These playbooks change the nodes and the cluster of the inventory.
The Kubernetes playbooks also write `~/.kube/config`.

Run the checks from the root of the repository:

```bash
# Unit tests of each layer
for test_dir in playbooks/*/tests; do
  compgen -G "$test_dir/test_*.py" >/dev/null || continue
  python3 -m unittest discover -s "$test_dir" -p 'test_*.py' -v
done

# YAML lint
yamllint -c .yamllint.yml playbooks inventory renovate.json requirements.yml .github

# Syntax check of one playbook (CI checks all playbooks)
ansible-galaxy collection install -r requirements.yml
ANSIBLE_VAULT_PASSWORD=ci-dummy ansible-playbook --syntax-check playbooks/01-k3s/standup.yml

# Lint of one chart (CI also renders each chart with helm template)
helm lint --strict playbooks/10-mailu/charts/h3xinfra-mailu-pre/

# Terraform of the Keycloak layer
(cd playbooks/10-keycloak/terraform && terraform fmt -check -diff \
  && terraform init -backend=false -input=false && terraform validate)

# Renovate configuration (ci.yml gives RENOVATE_VERSION)
npx --yes --package "renovate@${RENOVATE_VERSION}" renovate-config-validator --strict --no-global renovate.json

# Documentation
python3 docs/style/ste_check.py CONTRIBUTING.md
```

Some tests need `helm` or `ansible-playbook`. Most of these tests skip
themselves if the tool is not installed. The Cilium rollout tests always need
`helm`. They download the pinned Cilium chart.

If you change the behavior of a layer, add or update a test in
`playbooks/<NN>-<name>/tests/`. Update the `README.md` of the layer in the
same pull request.

The STE checker and the style guide are in `docs/style/`. From the root of
the repository, run the checker as `python3 docs/style/ste_check.py <file>`.

## Secrets

WARNING: This repository is public. Never commit a secret to it. A secret is
a password, a token, a private key, a kubeconfig or the Vault password.

- Do not commit a secret that Ansible Vault encrypts. The owner keeps the
  real inventory and its encrypted secrets in a private repository.
- The inventory in this repository is an example. Use only placeholders, as
  `inventory/production/group_vars/all/secrets.yml` does.
- In your own inventory, encrypt each secret with Ansible Vault.
  `h3xinfra-gen-pass [name]` makes a random password and encrypts it with
  Ansible Vault.
- `bin/h3xinfra-vault-pass` gets the Vault password from
  `ANSIBLE_VAULT_PASSWORD` or from a file in a secrets directory, for example
  `/h3xinfra-secrets/vault_pass.txt`. Never commit that file.
- CI uses only the placeholder Vault password `ci-dummy`.
- GitHub secret scanning and push protection are on for this repository.
  Push protection blocks a push that contains a known type of secret. It does
  not find all secrets.

If you push a secret by mistake, tell the owner immediately through a
[security advisory](https://github.com/coderpunch/h3xinfra/security/advisories/new).
The owner must rotate the secret. A new commit does not remove the secret
from the history.

Also report other security problems through a security advisory. Do not open
a public issue for them.

## Renovate

Renovate keeps the pinned versions current. `renovate.json` holds the
configuration. Each entry in `packageRules` has a `description` that tells
why the rule exists.

- Renovate opens a maximum of 5 pull requests at the same time and 2 in one
  hour.
- Renovate waits 3 days after the publication of a new version
  (`minimumReleaseAge`). Some sources give no timestamps. Their updates do
  not wait.
- `platformAutomerge` is `false`. Renovate merges a pull request only when
  each check run on it passes. Thus, `ci-ok` controls each automerge.
- Do not add a `paths` filter to `on.pull_request` in `ci.yml`. Each Renovate
  pull request must get the `ci-ok` check run.

Renovate merges these updates without the owner:

- Patch, pin and digest updates.
- Minor updates of the Renovate CLI pin `renovatebot/renovate`.

These updates wait for a review by the owner:

- All GitHub Actions updates, also digest updates.
- All other minor updates and all major updates.

Renovate merges a group pull request automatically only if it can do this for
each update in the group. Renovate uses these groups:

- `Helm charts`
- `Docker images`
- `Kubernetes`: K3s, the Gateway API CRDs and `kubectl`.
- `Cilium`
- `GitHub Actions`
- `CI tooling`: the tool pins in `.github/workflows/ci.yml`.

Renovate creates the `Kubernetes` and `Cilium` branches only on Monday from
00:00 to 09:59 (`America/Los_Angeles`).

Renovate does not propose these updates:

- The devcontainer features. Update them by hand with `devcontainer upgrade`.
- The Terraform `required_version`.
- Major updates of `ghcr.io/cloudnative-pg/postgresql`. A major update makes
  CNPG run `pg_upgrade` on the live databases.

Some rules set a version ceiling (`allowedVersions`). `kubectl` stays below
1.39.0 and `ansible-core` stays below 2.22.0. Redis stays on 8.2 for Mailu
and on 8.6 for Argo CD.

To get updates for a new version pin, add a
`# renovate: datasource=<datasource> depName=<name>` comment directly above
the pin. The marker works only in the files that `customManagers` in
`renovate.json` lists. The owner keeps the tool versions in `ci.yml` equal to
`tests/tool-versions.env` in the private repository `h3xinfra-private`.

<!-- BEGIN documentation section -->
## Documentation

Write all documentation in ASD-STE100 Simplified Technical English (STE).
This section is the same in all repositories of the `coderpunch`
organization. The [style guide](docs/style/STYLE.md) gives more examples
and the full rules of the checker.

### The seven rules

These are the rules of the owner, word for word:

1. Write short sentences. Use a maximum of 20 words for an instruction and 25
   words for a description.
2. Give one instruction in each sentence.
3. Use the active voice.
4. Use the simple tenses: present, past, and future.
5. Do not use the -ing form of a verb.
6. Use one word for one meaning. Do not use a second word for the same thing.
7. Names of APIs, headers, fields, and products stay as they are.

### How to apply the rules

- **Frozen text.** Do not change code blocks, inline code spans, commands,
  configuration snippets, URLs, link targets, quoted output or headings.
  Keep all facts: numbers, versions, dates, IDs, paths, causes and
  decisions. The rules do not apply to frozen text.
- **Instruction or description.** An instruction tells the reader to do
  something. It starts with a verb, with "Do not", or with a short
  condition and then a verb: "If the check fails, stop." All other
  sentences are descriptions.
- **Word count.** A word has a letter or a digit. An inline code span, a
  URL and a quoted string count as one word each. A word with a hyphen or
  a slash is one word.
- **Lists and tables.** Each list item and each table cell is a separate
  unit. Use a numbered list for steps, with one instruction in each step.
  Make a list from a series of more than four items.
- **One instruction.** Split "Do X, then do Y" and "Do X and do Y" into two
  sentences. A condition and one action is one instruction.
- **Active voice.** Name the actor: you, the owner, Renovate, CI, the
  playbook, Terraform, Argo CD, the controller or kubelet. If you do not
  know the actor, do not guess. Make the event the subject: "The pod
  restarted." A state is correct: "The volume is degraded."
- **Tenses.** Use the present tense for descriptions and rules. Use the past
  tense for events and in dated records. Use "will" for a result of an
  instruction. Do not write "is running", "has been" or "have done".
- **The -ing form.** Write "the current version", not "the existing
  version". Write "before you apply", not "before applying". Technical
  names and nouns are correct, for example "networking", "logging",
  "setting", "warning" and "during".
- **The reader and the owner.** Write "you" for the reader. Write "the
  owner" for the person who owns and runs this infrastructure. Do not
  write "we".
- **Maintained documents and dated records.** A `README.md`, a runbook or a
  guide tells the current state. A file under `docs/records/`, or a file
  with a date in its name, is a dated record. In a dated record, change
  only the language. Keep the names and the facts of that time.
- **Notices.** Start a notice with WARNING (data loss or a security risk),
  CAUTION (an outage) or NOTE (useful information). Put it before the step.

### Check your text

Run `ste_check.py` on each document that you change. The script uses only
the Python 3 standard library. It does not change files.

```bash
python3 ste_check.py README.md
python3 ste_check.py --summary docs/
```

The checker uses simple patterns. It can miss a problem, and it can flag
correct text. Fix each finding, or make sure that the text is correct.
Then compare your change with `git diff --word-diff`. Make sure that no
fact, code span, link target or heading changed.

### Glossary

Use the word in the "Use" column. Do not use the words in the "Do not use"
column. Some words are wrong only in one meaning. The "Do not use" column
shows them after "in this meaning". The checker does not flag these words.
You must decide.

<!-- ste-check off -->
#### People

| Use | Meaning | Do not use | Notes |
| --- | --- | --- | --- |
| **you** | The reader of the document. | in this meaning: the reader, one (as a pronoun) | In decapcms-gateway, "the reader" is the report reader of that application. Keep it. |
| **the owner** | The person who owns and runs this infrastructure and the coderpunch repositories. | we, our; in this meaning: the user, the operator, the admin, the administrator, us | Keep "user" for a person who signs in to an application. Keep "operator" for a Kubernetes operator. Keep "admin" in role and console names. In a dated record, do not guess who did an action. |
| **user** | A person who signs in to an application, for example Keycloak, Decap CMS, Open WebUI or Grafana. | end user, end users, end-user, end-users | - |

#### Computers

| Use | Meaning | Do not use | Notes |
| --- | --- | --- | --- |
| **node** | A computer in the k3s cluster. | in this meaning: host, server, machine, box | Keep "machine check" (a CPU error report). |
| **control-plane node** | A node in the `server` inventory group. It runs the k3s server, etcd and the API server. | master node, master nodes; in this meaning: server node, server, master | Keep "k3s server" for the k3s process and role. Keep "master" in the Keycloak "master realm". |
| **agent node** | A node in the `agent` inventory group. It runs the k3s agent and the workloads. | worker node, worker nodes; in this meaning: worker, workers | - |
| **host** | An entry in the Ansible inventory: a node or the virtual `k8s` host. Also in fixed terms: hostname, host key, hostPath, host network, host firewall. | in this meaning: host (for a cluster computer: use node) | - |
| **Ansible controller** | The computer that runs Ansible and Terraform for the cluster, normally the devcontainer. | control node; in this meaning: controller (alone), workstation | Keep "controller" alone for a Kubernetes controller. The workstation of the owner is a different computer. |

#### Repositories and GitHub

| Use | Meaning | Do not use | Notes |
| --- | --- | --- | --- |
| **repository** | A Git repository. | repo, repos | - |
| **pull request** | A GitHub pull request. | PR, PRs, merge request, MR | - |
| **organization** | The GitHub organization `coderpunch`. | org, orgs | - |
| **GitHub Actions** | The CI service of GitHub. | GHA; in this meaning: Actions (alone) | - |
| **pass** | A check or a test gives the expected result. | green; in this meaning: OK, succeed | Keep "succeed" for a command or an operation. |

#### Kubernetes and the stack

| Use | Meaning | Do not use | Notes |
| --- | --- | --- | --- |
| **Kubernetes** | The container platform. The cluster runs k3s. | K8s, k8s, kube | Write the virtual Ansible host as a code span: `k8s`. |
| **namespace** | A Kubernetes namespace. | in this meaning: ns | Keep "ns" for nanoseconds. |
| **Secret** | A Kubernetes Secret object. Write it with a capital S. | secret object, secret objects, Kubernetes secret, Kubernetes secrets | - |
| **secret** | A sensitive value: a password, a token or a private key. | in this meaning: credential (for one value) | Keep "credentials" for a user name together with its password or token. |
| **Service** | A Kubernetes Service object. Write it with a capital S. | in this meaning: service (for an application) | Keep lowercase "service" for a systemd service, for example `k3s-agent.service`. |
| **application** | Software that the cluster runs for users, for example Plex, Mailu or Open WebUI. | app, apps | Keep "GitHub App" (a product name), the Argo CD `Application` kind and paths such as `apps/`. |
| **image** | An OCI container image. | container image, container images, Docker image, Docker images, docker image | Keep "engine image" (a Longhorn name). |
| **chart** | A Helm chart. | Helm package, Helm packages | - |
| **bespoke chart** | A Helm chart that a repository of the organization contains (`h3xinfra-*`). | custom chart, custom charts, custom Helm chart, custom Helm charts, local chart, local charts, local Helm charts | Keep "wrapper chart" and "umbrella chart" for those chart patterns. |
| **upstream chart** | A Helm chart that an external project publishes. | external chart, external charts, external Helm chart, third-party chart, third-party charts | - |
| **release** | A Helm release: one installed copy of a chart in a namespace. Also a GitHub release object. | in this meaning: release (for a version number: use version) | - |
| **version** | A numbered state of software, a chart or an image, for example k3s 1.37.2. | in this meaning: release, build | - |
| **layer** | One numbered unit of the stack: a playbook directory `playbooks/<NN>-<name>/` or a Terraform root module `terraform/layers/<NN>-<name>/`. | in this meaning: module, tier, component | Keep "module" for a Terraform module. |
| **stack** | All layers, in the order that `playbooks/stack-standup.yml` deploys them. Also "dual stack" (IPv4 and IPv6). | - | - |
| **playbook** | An Ansible YAML file that you give to `ansible-playbook`. | in this meaning: play, script | Keep "play" for one play inside a playbook. Keep "script" for shell and Python scripts. |
| **Argo CD** | The GitOps controller. | ArgoCD, Argo-CD | - |
| **PostgreSQL** | The database. | Postgres, PG | - |
| **CNPG** | The CloudNativePG operator and its PostgreSQL `Cluster` objects. | in this meaning: CloudNativePG | Write "CloudNativePG (CNPG)" only at the first mention in a document. |
| **VIP** | A LoadBalancer IP address that MetalLB announces. | LB IP, LB IPs, virtual IP, virtual IPs | - |
| **Gateway** | The shared Gateway API object `h3xinfra-gateway-pre-shared`. All application routes attach to it. | in this meaning: ingress (for the Gateway), proxy | "Envoy Gateway" is the controller product. In decapcms-gateway, "the gateway" is that application. |

#### Actions

| Use | Meaning | Do not use | Notes |
| --- | --- | --- | --- |
| **deploy** | Put a change into the live cluster or onto the nodes. Noun: deployment. | roll out, rolls out, rolled out, ship, ships, shipped, push out; in this meaning: apply, install | Keep "apply" for `terraform apply`, `kubectl apply` and server-side apply. Keep "install" for packages, tools and files. For "the chart ships X", write "the chart contains X". |
| **rollout** | A step-by-step deployment of one change to many nodes or workloads. Also a Kubernetes workload rollout (`kubectl rollout`). | roll-out, roll-outs | - |
| **run** | Start a command, a playbook, a script or a job. | execute, executes, executed, invoke, invokes, invoked, kick off; in this meaning: execution, invocation | Keep "remote code execution" and the systemd "invocation ID". |
| **start** | Make a process, a service or a job begin. | launch, launches, launched, spin up, spins up, fire up, bring up, boot up | Keep "boot" for the operating system of a node. |
| **stop** | Make a process, a service or a job end. | halt, halts, halted; in this meaning: terminate, kill | Keep "kill" for signals (SIGKILL) and "Terminating" for the pod state. Keep "shut down" for the power-off of a node. |
| **reboot** | Stop and start the operating system of a node. | in this meaning: restart (for a node) | Keep "power-cycle" for a cut of AC power. |
| **restart** | Stop and start a process, a service or a pod. | bounce, bounces, bounced; in this meaning: reboot (for a process) | - |
| **delete** | Destroy an object, a file or data. After this, the item does not exist. | wipe, wipes, wiped, erase, erases, erased, nuke, get rid of; in this meaning: purge, destroy, drop | Keep "purge" for the Longhorn snapshot purge and "destroy" for `terraform destroy`. |
| **remove** | Take an item out of a configuration, a list, the code or a node. This does not delete data. | strip out, take out; in this meaning: drop | Keep "drop" for network packets and Linux capabilities. |
| **configure** | Give settings to software. | set up, sets up | - |
| **configuration** | The settings of software, as a whole. | config, setup; in this meaning: settings (for the whole configuration) | Write a volume, a key or a file named config as a code span: `config`. |
| **make sure** | Do what is necessary so that a condition is true. | ensure, ensures, ensured, assure, assures | - |
| **check** | Look at something to find its state. Also a CI check or a test check. | examine, examines, examined, inspect, inspects, inspected, double-check; in this meaning: validate, validation | Keep "validate" in technical names: `terraform validate`, admission validation, schema validation. |
| **verify** | Check and get proof that something is correct. "Verified" means that the check passed. Also TLS, signature and checksum verification. | in this meaning: confirm, confirmed | Keep "confirm" for the `confirm_destroy` prompt. |
| **update** | Change to a newer version, or change content to the current state. | bump, bumps, bumped; in this meaning: upgrade | Keep "upgrade" in named operations: `apt upgrade`, `helm upgrade`, the Longhorn engine upgrade. |
| **change** | Make something different. | modify, modifies, modified, modification, modifications, alter, alters, altered, tweak, tweaks, tweaked; in this meaning: edit, adjust | Keep "edit" for a change to the text of a file or a CMS entry. |
| **use** | Do work with a tool, a method or a value. | utilize, utilizes, utilized, utilise, leverage, leverages, employ, employs, make use of | - |
| **get** | Come to have something. | obtain, obtains, obtained, retrieve, retrieves, retrieved; in this meaning: acquire, fetch | Keep "acquire" for a lock and "fetch" for `git fetch` and HTTP requests. |
| **fix** | Make a bug, a fault or a problem go away. | remediate, remediates, remediated; in this meaning: resolve, repair, correct (as a verb), remediation | Keep "resolve" for DNS resolution and the RESOLVED status. |
| **allow** | Give permission for an action or for traffic. | permit, permits, permitted; in this meaning: enable, let | Keep "enable" for a feature switch, for example `enabled: true`. |
| **sign in** | Give credentials to an application to get access. Noun: sign-in. | log in, logs in, logged in, log on, logon; in this meaning: login | Keep "login" for an SSH login name, for example `ansible`, and in product names. |
| **restore** | Put data back from a backup or a snapshot. | in this meaning: recover (for data) | - |
| **recover** | Bring a node, a service or a volume back to normal operation. | in this meaning: restore (for a node or a service), heal | - |

#### States and results

| Use | Meaning | Do not use | Notes |
| --- | --- | --- | --- |
| **retired** | No longer used or deployed. Only the history keeps it. | obsolete; in this meaning: legacy, deprecated, former, decommissioned | Keep "legacy" in technical names (legacy BIOS) and "deprecated" for an upstream deprecation. |
| **problem** | Something that does not work as expected. | in this meaning: issue, defect | Keep "issue" for a GitHub issue. Use "bug" for a defect in software and "fault" for a defect in hardware. |
| **alert** | A Prometheus or Alertmanager alert. | alarm, alarms | - |

#### Documents

| Use | Meaning | Do not use | Notes |
| --- | --- | --- | --- |
| **directory** | A directory in a file system or a repository. | dir, dirs; in this meaning: folder | Keep "folder" for a Decap CMS media folder. |
| **documentation** | Text that tells how the system works or what to do. One file is a document. | doc, docs | Write the `docs/` directory as a code span. |
| **environment variable** | A variable in the environment of a process. | env var, env vars, envvar | - |
| **authentication** | Proof of who a user or a client is. | auth, authn, AuthN | - |
| **authorization** | The decision about what a user or a client can do. | authz, AuthZ | - |
| **certificate** | An X.509 certificate. | cert, certs | - |

#### Small words

| Use | Meaning | Do not use | Notes |
| --- | --- | --- | --- |
| **must** | An obligation. | have to, has to, need to, needs to, is required to, are required to, should | For an expected result, use the present or the future tense: "The command returns 0." |
| **because** | A cause. | due to, owing to; in this meaning: since (as a cause), as (as a cause) | Keep "since" for time, for example "since 2026-10-04". |
| **thus** | A result. | therefore, hence, consequently, accordingly; in this meaning: so (as a result) | For a purpose, write "to", not "so that" or "in order to". |
| **to** | A purpose. | in order to; in this meaning: so that | - |
| **for example** | One or more examples. | e.g., for instance; in this meaning: such as | - |
| **that is** | The same thing in different words. | i.e. | - |
| **and other** | The list continues. Better: give the full list. | etc., and so on | - |
| **but** | A contrast. | however, although, though, whereas, nevertheless, nonetheless | - |
| **also** | An addition. | additionally, in addition, furthermore, moreover | - |
| **if** | A condition. | in case, provided that, in the event that | - |
| **after** | Later than an event. | once, subsequently | For "once" in the meaning "one time", write "one time". |
| **before** | Earlier than an event. | prior to | - |
| **about** | On the topic of. | regarding, concerning, with respect to, with regard to, in terms of | - |
| **approximately** | Near a quantity, but not exact. | roughly, circa, approx., about + number, around + number | "~" is correct in table cells and code only. |
| **through** | By means of a path, a tool or a step. | via | - |
<!-- ste-check on -->
<!-- END documentation section -->
