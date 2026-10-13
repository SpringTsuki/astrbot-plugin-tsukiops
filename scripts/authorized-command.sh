#!/bin/sh
# Install as root-owned and not writable by the monitoring account.
# Verify these executable paths on the target distribution before deployment.
case "$SSH_ORIGINAL_COMMAND" in
    uptime) exec /usr/bin/uptime ;;
    "free -h") exec /usr/bin/free -h ;;
    "df -h") exec /bin/df -h ;;
    "cat /proc/loadavg") exec /bin/cat /proc/loadavg ;;
    *) printf '%s\n' 'Command not permitted' >&2; exit 126 ;;
esac
