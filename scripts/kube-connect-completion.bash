#!/bin/bash

# Bash completion for kube-connect script
_kube_connect_completion() {
    local cur prev opts
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    # Check if kubectl is available and configured
    if ! command -v kubectl >/dev/null 2>&1; then
        return 0
    fi

    # If we're completing the first argument (namespace)
    if [[ ${COMP_CWORD} -eq 1 ]]; then
        # Get available namespaces from kubectl
        local namespaces
        if namespaces=$(kubectl get namespaces -o custom-columns=NAME:.metadata.name --no-headers 2>/dev/null); then
            COMPREPLY=($(compgen -W "$namespaces" -- "$cur"))
        fi
        COMPREPLY+=($(compgen -W "--help -h" -- "$cur"))
    fi
    return 0
}

complete -F _kube_connect_completion kube-connect
