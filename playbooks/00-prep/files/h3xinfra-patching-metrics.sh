#!/usr/bin/env bash
# Write the patch state of this node for the node-exporter textfile
# collector. Installed by playbooks/00-prep (tasks/unattended-upgrades.yml)
# and started by h3xinfra-patching-metrics.timer.
#
# apt's daily job touches the stamp file each time unattended-upgrade
# completes, also when there is nothing to install. After an interrupted
# dpkg run, 'dpkg --audit' lists the broken packages and unattended-upgrade
# stops without an error, so the stamp stops moving and nothing else shows
# it.
set -euo pipefail

STAMP_FILE="${STAMP_FILE:-/var/lib/apt/periodic/unattended-upgrades-stamp}"
METRICS_FILE="${METRICS_FILE:-/var/lib/node_exporter/textfile_collector/h3xinfra_patching.prom}"

stamp=0
if [[ -e "$STAMP_FILE" ]]; then
  stamp="$(stat -c %Y "$STAMP_FILE")"
fi

# dpkg exits 0 also when the audit finds problems. A failing dpkg stops the
# run, and the previous metrics file stays in place.
audit_lines="$(dpkg --audit | wc -l)"

# The collector reads only *.prom files, so it ignores the temporary file.
tmp="$(mktemp "${METRICS_FILE}.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
cat >"$tmp" <<EOF
# HELP h3xinfra_unattended_upgrades_stamp_timestamp_seconds Modification time of the unattended-upgrades stamp (0 if missing).
# TYPE h3xinfra_unattended_upgrades_stamp_timestamp_seconds gauge
h3xinfra_unattended_upgrades_stamp_timestamp_seconds ${stamp}
# HELP h3xinfra_dpkg_audit_lines Lines printed by 'dpkg --audit' (0 when all packages are installed correctly).
# TYPE h3xinfra_dpkg_audit_lines gauge
h3xinfra_dpkg_audit_lines ${audit_lines}
EOF
chmod 0644 "$tmp"
mv -f "$tmp" "$METRICS_FILE"
