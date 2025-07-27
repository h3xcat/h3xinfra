#!/bin/bash
set -euo pipefail
IFS=$'\n\t'

bash "$H3XINFRA_FOLDER/bin/h3xinfra-setup-perms"
bash "$H3XINFRA_FOLDER/bin/h3xinfra-setup-ssh"
bash "$H3XINFRA_FOLDER/bin/h3xinfra-setup-dependencies"

