#!/bin/sh
set -eu
pids=''
for file in /proc/[0-9]*/stat; do
    read -r pid name state parent rest < "$file" || continue
    case "$name:$parent" in
        '(clamd):1'|'(freshclam):1') pids="$pids $pid" ;;
    esac
done
[ "$(printf '%s\n' "$pids" | wc -w)" -eq 2 ]
for pid in $pids; do kill -TERM "$pid"; done
remaining=80
while [ "$remaining" -gt 0 ]; do
    running=0
    for pid in $pids; do
        [ -r "/proc/$pid/stat" ] || continue
        read -r current name state parent rest < "/proc/$pid/stat" || continue
        if [ "$state" = Z ]; then
            set -- $rest
            [ "${48}" -eq 0 ]
        else
            running=$((running + 1))
        fi
    done
    if [ "$running" -eq 0 ]; then
        printf '%s\n' '{"component":"clamav","writersStopped":true,"processesExited":2}'
        exit 0
    fi
    sleep 1
    remaining=$((remaining - 1))
done
exit 1
