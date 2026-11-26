# Documentation style guide

This guide tells you how to write documentation for the repositories of the
`coderpunch` organization. It uses ASD-STE100 Simplified Technical English
(STE). The owner chose seven STE rules. This guide gives the rules, tells you
how to apply them, and gives the glossary.

The guide is for all Markdown documentation in these repositories:

- `h3xinfra`
- `h3xinfra-private`
- `h3xinfra-apps`
- `decapcms-gateway`
- `qb-natpmp`
- `reports` (only `README.md`)

The files under `reports/reports/` are report content, not documentation.
Do not apply this guide to them.

Two tools come with this guide:

- `glossary.json` is the source of the glossary.
- `ste_check.py` finds text that can break the rules. See
  [The checker](#the-checker).

## The seven rules

These are the rules of the owner, word for word:

1. Write short sentences. Use a maximum of 20 words for an instruction and 25
   words for a description.
2. Give one instruction in each sentence.
3. Use the active voice.
4. Use the simple tenses: present, past, and future.
5. Do not use the -ing form of a verb.
6. Use one word for one meaning. Do not use a second word for the same thing.
7. Names of APIs, headers, fields, and products stay as they are.

The sections below tell you how to apply each rule.

## Text that you must not change

Some text is frozen. The rules do not apply to frozen text. Do not change it:

- Fenced code blocks and inline code spans.
- Commands, YAML, JSON, HCL and other configuration snippets.
- URLs and link targets. You can change the text of a link.
- Quoted log output and quoted terminal output.
- Markdown headings. Other documents, code comments and link anchors use
  the text of a heading.
- Names of APIs, headers, fields, products, Kubernetes kinds, CLI flags,
  configuration keys, files and directories.
- Facts: numbers, versions, dates, times, IDs, measurements, causes and
  decisions.

If a rule and frozen text do not agree, the frozen text wins. Write the
words around the frozen text again.

Do not remove content. Do not add a claim that the repository does not
support.

## Instructions and descriptions

An instruction tells the reader to do something. The limit is 20 words.

A description tells what a thing is, what it does, or what happened. The
limit is 25 words.

A sentence is an instruction if it has one of these forms:

- It starts with a verb in the base form: "Run the playbook."
- It starts with "Do not", "Never" or "Always".
- It starts with a condition, a time or a purpose, and then a verb in the
  base form: "If the check fails, stop the rollout."
- It tells the reader what they must do: "You must run the playbook as
  root." Use the base form of the verb for this, not "you must".

All other sentences are descriptions. Here are two examples from these
documents:

| Type | Sentence |
| --- | --- |
| Instruction | Use the inventory login `ansible`, the configured node key and the pinned host keys. |
| Description | The managed file is `/etc/needrestart/conf.d/50-h3xinfra-k3s.conf`. |

## How to count words

Count each word as the reader sees it:

- A word is a group of characters between spaces. It must have a letter or a
  digit.
- An inline code span is one word, also a long one.
- A URL is one word.
- A string in double quotes is one word. Quoted text is frozen text.
- A word with a hyphen or a slash is one word: "read-only", "IPv4/IPv6",
  "30-second".
- A number is one word. A number and its unit are two words: "5 GiB".
- The text of a link counts. The link target does not count.
- Punctuation, Markdown marks and symbols such as "—" or "→" are not words.

This description has 17 words. "30-second", "five-minute" and "180-second"
count as one word each:

> The watchdog uses a 30-second systemd timer, a five-minute boot grace, and
> a 180-second K3s startup grace.

This instruction has 4 words. Each code span is one word:

> Add `argocd.argoproj.io/sync-wave: "-1"` to `metadata.annotations`.

## Lists and table cells

Each list item and each table cell is a separate unit. The word limits
apply to each sentence in it.

- A list item can be a full sentence or a short label, for example
  "Kubelet scrape failure." Use the same form for all items of one list.
- Put a lead-in sentence before a list. End the lead-in with a colon:
  "The alerts cover these failures:".
- Use a numbered list for steps in a sequence. Give one instruction in each
  step. A step can have more sentences that describe the step.
- Use a bullet list for items without a sequence.
- If a sentence has a series of more than four items, make a list.
- Write a prose table cell in STE. Keep code cells and identifiers as they
  are. Do not put a long sentence in a cell. Move the long text below the
  table.

## One instruction in each sentence

Each sentence tells the reader to do one action. Split a sentence that has
two actions:

| Do not write | Write |
| --- | --- |
| "Run the checks under CI locally, then open a pull request." | Run the checks under CI locally. Then open a pull request. |
| "Do not repeat the request after an error or a timeout; check the result instead." | Do not repeat the request after an error or a timeout. Check the result. |
| "If authentication fails, have the operator restore the intended SSH agent/key setup and verify the host identity." | If authentication fails, ask the owner to restore the SSH agent and the key. Then verify the host identity. |

These forms are one instruction:

- A condition, a time or a purpose and one action: "To apply one layer, run
  `terraform -chdir=terraform/layers/<NN> apply`."
- One action on more than one object: "Delete the PVC and the PV."
- A choice between two actions: "Restart or reboot the node."

Put a WARNING or a CAUTION before the instruction that it is about.

## Active voice

In the active voice, the subject does the action. Name the actor. These are
the usual actors in these documents:

- you (the reader)
- the owner
- Renovate, CI, GitHub Actions
- the playbook, Ansible, Terraform, the layer, the chart, Helm
- Argo CD, the controller, the operator (a Kubernetes operator)
- Kubernetes, the API server, kubelet, k3s, the scheduler
- the script, the gateway, the application

Find the actor in the text or in the code around it. Here are examples from
these documents:

| Do not write | Write |
| --- | --- |
| "The Decap UI is bundled in the image and served from /admin/." | The image contains the Decap UI. The gateway serves it from `/admin/`. |
| "Merges are checked against the write scope at the time of the merge." | The gateway checks each merge against the write scope at the time of the merge. |
| "Secrets are committed only as SealedSecrets." | Commit secrets only as SealedSecrets. |
| "X is required." | X is necessary. Or: Y needs X. Or: You must have X. |
| "X is allowed." | Y allows X. Or: X can happen. |

If you do not know the actor, do not guess. This is very important in dated
records. Use one of these methods:

- Make the thing the subject, with a verb that has no object: "The pod
  restarted." "The value changed to 3."
- Use "there is" or "there was": "There was no test of this form here."
- Use a past participle as an adjective: "the pinned version", "an encrypted
  file".

A past participle after "is" or "are" can describe a state. This is not a
passive. These are correct: "The volume is degraded." "The node is
cordoned." "The feature is enabled." But "was" or "were" and a past
participle tells about an event. Name the actor for an event.

## Tenses

Use only these tenses:

| Tense | Use it for | Example |
| --- | --- | --- |
| Present | Descriptions, rules and the current state. | The playbook installs the file. |
| Past | Events and results. Dated records use the past tense. | On 2026-09-20, the owner approved the policy. |
| Future | A result that comes after an instruction, or a planned event. | The pod will restart. |

Use the present tense for a result that is always true. Use "can",
"cannot", "must" and "must not" for possibility and obligation. Do not use
"should". Do not use "would", "might" or "may" when a simple tense or "can"
is possible.

Do not use the progressive or the perfect forms. Change them like this:

| Do not write | Write |
| --- | --- |
| "while the operator is still running" | while the operator runs |
| "If that entry has moved, this route redirects to its match." | If that entry moved, this route redirects to its match. |
| "These changes were checked with local renders; they have not been verified live." | Local renders checked these changes. No live check verified them. |
| "The three temporary address moves have therefore been retired from current source." | Thus, the current source does not contain the three temporary address moves. |

Do not write "now" or "currently" when the present tense gives the same
meaning.

## The -ing form

Do not use the -ing form of a verb. This rule is for these forms:

- The progressive tense: "is running", "was failing".
- A verb used as a noun (a gerund): "Removing the source removes ...".
- A participle after a noun: "clients using the standard flow".
- A participle before a noun: "the existing app", "the remaining work".
- A verb after a preposition: "before applying", "by using", "without
  disabling".

Here are rewrites of real sentences from these documents:

| Do not write | Write |
| --- | --- |
| "Grafana keeps its existing session behavior." | Grafana keeps its current session behavior. |
| "It destroys the realm, the Helm releases and the namespace, including its PVCs." | It destroys the realm, the Helm releases and the namespace with its PVCs. |
| "missing files, keys or decryption credentials fail the lookup." | If a file, a key or a decryption credential is not there, the lookup fails. |
| "Delete the remaining workloads, and check the namespace for data." | Delete the other workloads. Then check the namespace for data. |
| "Keycloak clients using the standard flow require at least one redirect URI." | A Keycloak client that uses the standard flow must have one or more redirect URIs. |
| "Before applying a Plex chart or image change, review the plan." | Before you apply a Plex chart or image change, review the plan. |
| "Verify the resulting reservation and available memory per node." | Verify the reservation and the available memory on each node after the change. |
| "The role's join check waits on each joining server until every server in the inventory has joined." | The join check of the role waits for each new control-plane node. It waits until all control-plane nodes of the inventory are in the cluster. |

Use these replacements for the -ing words that these documents use most:

| Word | Replacement |
| --- | --- |
| "existing" | Remove the word, or write "current" or "that exists". |
| "including" | "with", "and", "also", or "for example" (only for examples). |
| "missing" | "that is not there", "that does not exist", "absent". |
| "remaining" | "other", "the rest of", "that remain". |
| "matching" | "that matches", "the same", "related". |
| "running" | "that runs", "live"; "is running" becomes "runs". |
| "pending" | "open", "not done", "that waits". |
| "following" | "these", "the steps that follow"; as a preposition, "after". |
| "according to" | "as given in", "as specified in". |
| "depending on" | "if", or "it depends on". |
| "corresponding" | "related", "applicable". |
| "using" | "with", "that uses", "through". |
| "before/after doing" | "before/after you do". |
| "without doing" | "and do not do", or a separate instruction. |

### Words that end in -ing but are correct

These words are not verb forms. You can use them:

- during, thing, nothing, something, anything, everything, string,
  substring, ceiling, morning, evening, sibling, spring, ring, king, wing.
- Base verbs: bring, ping, sing, swing, sting.
- Nouns: setting, warning, heading, meaning, mapping, binding, padding,
  spacing, wiring.

Technical names can end in -ing. STE allows them. These are correct in
these documents:

- networking, logging, monitoring, routing, scheduling, tracing,
  provisioning, versioning, hardening, timing, billing, accounting,
  engineering, spoofing
- load balancing, port forwarding, IP masquerading, storage fencing, CPU
  throttling, streaming replication
- rolling update, rolling reboot, rolling restart, recurring job, sealing
  key, sealing certificate, operating system, listening port

Names stay as they are, also when they end in -ing:

- Kubernetes states: `Running`, `Pending`, `Terminating`,
  `ContainerCreating`.
- Product names, field names, CLI flags, configuration keys and file names,
  for example `host_key_checking` or `runbook-network-policy-rollout.md`.
- Headings. Headings are frozen.

## Short sentences

Use one idea in each sentence. Use these methods to split a long sentence:

1. Split at a semicolon, a colon, ", and", ", but", ", so" and ", which".
2. Make a list from a series of more than four items.
3. Move text in parentheses into a separate sentence.
4. Change a "which" or "that" clause into a new sentence that starts with
   "It" or "This".
5. Put a condition first: "If X, do Y." If the condition is long, write it
   as a separate sentence: "X can occur. In this case, do Y."
6. Do not put more than three nouns in a row. Write "the alert for a failed
   check", not "the check failure alert".

This example from `STANDARDS.md` has 31 words and an -ing word:

> "In matching `playbooks/<layer>/` directories, only the explicitly allowed
> Open WebUI and Uptime Kuma configuration seeds may remain as YAML, plus
> exactly `playbooks/30-postgres-backup/teardown.yml`, an unconditional
> refusal with no connection or deployment tasks."

This rewrite keeps all facts:

> A `playbooks/<layer>/` directory for a Terraform layer can keep only these
> YAML files:
>
> - The Open WebUI and Uptime Kuma configuration seeds.
> - `playbooks/30-postgres-backup/teardown.yml`. This playbook always
>   refuses to run. It has no connection tasks and no deployment tasks.

## The reader and the owner

Write "you" for the reader. In an instruction, "you" is not necessary:
"Run the playbook."

Write "the owner" for the person who owns and runs this infrastructure. Use
it for approvals, decisions and actions that only the owner can do: "Only
the owner can approve the plan."

Do not write "we", "the user" or "the operator" for the owner. Keep "user"
for a person who signs in to an application. Keep "operator" for a
Kubernetes operator.

In a dated record, change "we", "the user" or "the operator" to "the owner"
only for an action of the owner. The record must show that the owner did
the action. If the actor is not clear, make the event the subject. See
[Active voice](#active-voice).

## Notices

Use these three words at the start of a notice paragraph:

| Word | Use it for |
| --- | --- |
| WARNING | A risk of data loss, a security exposure, or damage that you cannot reverse. |
| CAUTION | A risk of an outage, or damage that you can repair. |
| NOTE | Information that helps the reader. |

Put the notice before the step that it is about.

## Maintained documents and dated records

A maintained document tells the current state. These are maintained
documents:
`README.md` files, runbooks, `CONVENTIONS.md`, `STANDARDS.md`,
`CONTRIBUTING.md` and guides.

- Use the present tense.
- Change a statement that is not true now only if the repository clearly
  shows the correct fact. Do not guess.
- Give each such change of fact in the pull request.

A dated record tells what occurred at one time. These are dated records:

- All files under `docs/records/`.
- Files with a date in the name, for example `maintenance-2026-11-12-*.md`.
- Files whose name starts with `followup-`, `complexity-audit-`,
  `security-review-`, `analysis-`, `review-` or `incident-`.

In a dated record, change only the language. Keep the names, numbers and
facts as they were at that time. Use the past tense for events.

### Names of the organization

The repositories moved from the GitHub account `h3xcat` to the organization
`coderpunch`. In a maintained document, write the current names:

- `github.com/coderpunch/<repository>`
- `ghcr.io/coderpunch/decapcms-gateway` and `ghcr.io/coderpunch/qb-natpmp`

Keep `h3xcat` in these places:

- The GitHub user and the Docker Hub user, for example the owner of a
  token or a registry user name.
- The GitHub App names `h3xcat-argocd` and `h3xcat-reports-cms`.
- License and copyright holders.
- Keycloak and Grafana user names, and e-mail addresses.
- All dated records.

## Glossary

Use one word for one meaning. Use the word in the "Use" column. Do not use
the words in the "Do not use" column.

Some words are correct in one meaning and wrong in another. The "Do not
use" column shows them after "in this meaning". The checker does not flag
them. You must decide.

`glossary.json` is the source of these tables. If you change the glossary,
change `glossary.json`. Then run `python3 ste_check.py --glossary-md`. Paste
the output here.

<!-- ste-check off -->
### People

| Use | Meaning | Do not use | Notes |
| --- | --- | --- | --- |
| **you** | The reader of the document. | in this meaning: the reader, one (as a pronoun) | In decapcms-gateway, "the reader" is the report reader of that application. Keep it. |
| **the owner** | The person who owns and runs this infrastructure and the coderpunch repositories. | we, our; in this meaning: the user, the operator, the admin, the administrator, us | Keep "user" for a person who signs in to an application. Keep "operator" for a Kubernetes operator. Keep "admin" in role and console names. In a dated record, do not guess who did an action. |
| **user** | A person who signs in to an application, for example Keycloak, Decap CMS, Open WebUI or Grafana. | end user, end users, end-user, end-users | - |

### Computers

| Use | Meaning | Do not use | Notes |
| --- | --- | --- | --- |
| **node** | A computer in the k3s cluster. | in this meaning: host, server, machine, box | Keep "machine check" (a CPU error report). |
| **control-plane node** | A node in the `server` inventory group. It runs the k3s server, etcd and the API server. | master node, master nodes; in this meaning: server node, server, master | Keep "k3s server" for the k3s process and role. Keep "master" in the Keycloak "master realm". |
| **agent node** | A node in the `agent` inventory group. It runs the k3s agent and the workloads. | worker node, worker nodes; in this meaning: worker, workers | - |
| **host** | An entry in the Ansible inventory: a node or the virtual `k8s` host. Also in fixed terms: hostname, host key, hostPath, host network, host firewall. | in this meaning: host (for a cluster computer: use node) | - |
| **Ansible controller** | The computer that runs Ansible and Terraform for the cluster, normally the devcontainer. | control node; in this meaning: controller (alone), workstation | Keep "controller" alone for a Kubernetes controller. The workstation of the owner is a different computer. |

### Repositories and GitHub

| Use | Meaning | Do not use | Notes |
| --- | --- | --- | --- |
| **repository** | A Git repository. | repo, repos | - |
| **pull request** | A GitHub pull request. | PR, PRs, merge request, MR | - |
| **organization** | The GitHub organization `coderpunch`. | org, orgs | - |
| **GitHub Actions** | The CI service of GitHub. | GHA; in this meaning: Actions (alone) | - |
| **pass** | A check or a test gives the expected result. | green; in this meaning: OK, succeed | Keep "succeed" for a command or an operation. |

### Kubernetes and the stack

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

### Actions

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

### States and results

| Use | Meaning | Do not use | Notes |
| --- | --- | --- | --- |
| **retired** | No longer used or deployed. Only the history keeps it. | obsolete; in this meaning: legacy, deprecated, former, decommissioned | Keep "legacy" in technical names (legacy BIOS) and "deprecated" for an upstream deprecation. |
| **problem** | Something that does not work as expected. | in this meaning: issue, defect | Keep "issue" for a GitHub issue. Use "bug" for a defect in software and "fault" for a defect in hardware. |
| **alert** | A Prometheus or Alertmanager alert. | alarm, alarms | - |

### Documents

| Use | Meaning | Do not use | Notes |
| --- | --- | --- | --- |
| **directory** | A directory in a file system or a repository. | dir, dirs; in this meaning: folder | Keep "folder" for a Decap CMS media folder. |
| **documentation** | Text that tells how the system works or what to do. One file is a document. | doc, docs | Write the `docs/` directory as a code span. |
| **environment variable** | A variable in the environment of a process. | env var, env vars, envvar | - |
| **authentication** | Proof of who a user or a client is. | auth, authn, AuthN | - |
| **authorization** | The decision about what a user or a client can do. | authz, AuthZ | - |
| **certificate** | An X.509 certificate. | cert, certs | - |

### Small words

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

## The checker

`ste_check.py` finds text that can break the rules. It is a review aid. It
does not change files. It always exits with status 0. Python 3 is
necessary. The script uses only the standard library.

```bash
# All findings, with line numbers.
python3 ste_check.py README.md docs/

# Only the counts for each file.
python3 ste_check.py --summary README.md

# Only some rules.
python3 ste_check.py --rules long-desc,long-instr,ing README.md
```

The checker reports these rules:

| Rule | It finds |
| --- | --- |
| `long-desc` | A description with more than 25 words. |
| `long-instr` | An instruction with more than 20 words. |
| `ing` | An -ing word that is not on the list of correct words. |
| `passive` | A form of "be" or "get" and a past participle. |
| `tense` | A progressive or a perfect form. |
| `glossary` | A word from a "Do not use" list. |
| `multi` | An instruction with ", then", "; <verb>" or "and <verb>". |

The checker does not read frozen text. It removes code blocks, headings,
HTML comments, link targets and front matter. It changes each code span,
URL and double-quoted string into one word. It does not read text between
`<!-- ste-check off -->` and `<!-- ste-check on -->`. Use these markers
only for text that quotes words from a "Do not use" list.

The checker uses simple patterns. Thus, it can miss a problem, and it can
flag correct text. It does not find these problems:

- An instruction in the form "you must".
- A participle after a noun without "is": "fields owned by another
  manager".
- A word from the "in this meaning" lists.

The docstring of `ste_check.py` gives the full patterns.

## Checklist for a rewrite

1. Read the full document before you change it.
2. Change only the prose. Keep all frozen text.
3. Run `ste_check.py` on the document.
4. Fix each finding, or make sure that the text is correct.
5. Compare the old and the new text with `git diff --word-diff`.
6. Make sure that no number, date, version, path, code span, link target or
   heading changed.
