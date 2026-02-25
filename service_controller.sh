#!/bin/bash
#
# Drone Service Control Script for AntSDR E200 (old and new firmware).
# It's recommended to first run stop before trying to connect with
# an application such as SDR++, this way upon connect the frequency and gain are set as desired.
#
# Copyright (c) 2025 cemaxecuter
#
# Licensed under the MIT License. You may obtain a copy of the License at:
#   https://opensource.org/licenses/MIT
#
# Author: cemaxecuter
#
# Supports both firmware versions:
#   Old firmware: password "abawavearm", processes: S55drone, droneangle.sh, done_dji_release
#   New firmware: password "1" (dropbear), processes: S55drone, droneangle.sh, drone_dji_rid_decode
#
# Usage:
#   ./service_controller.sh stop   # Stops the service (by killing its processes)
#   ./service_controller.sh start  # Starts the service using the remote init script

# Remote host details
HOST="172.31.100.2"
USER="root"
OLD_PASSWORD="abawavearm"
NEW_PASSWORD="1"

# Will be set by detect_firmware()
PASSWORD=""
FIRMWARE=""

# Validate input parameter
if [ $# -ne 1 ]; then
    echo "Usage: $0 [start|stop]"
    exit 1
fi

ACTION="$1"

# SSH options: bypass host key checking and do not update the known_hosts file.
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5"

# Detect which firmware is running by trying each password.
detect_firmware() {
  echo "Detecting firmware version on $HOST..."

  # Try old firmware password first
  if sshpass -p "$OLD_PASSWORD" ssh $SSH_OPTS "$USER@$HOST" "true" 2>/dev/null; then
    PASSWORD="$OLD_PASSWORD"
    FIRMWARE="old"
    echo "Detected: Old firmware (password: abawavearm)"
    return 0
  fi

  # Try new firmware password
  if sshpass -p "$NEW_PASSWORD" ssh $SSH_OPTS "$USER@$HOST" "true" 2>/dev/null; then
    PASSWORD="$NEW_PASSWORD"
    FIRMWARE="new"
    echo "Detected: New firmware (password: 1, dropbear)"
    return 0
  fi

  echo "ERROR: Could not connect to $HOST with either password."
  echo "Check that the AntSDR is powered on and reachable."
  exit 1
}

# Function: Stop the service by killing target processes.
stop_service() {
  LOCAL_TMP_SCRIPT=$(mktemp /tmp/remote_kill.XXXXXX.sh)

  if [ "$FIRMWARE" = "old" ]; then
    cat << 'EOF' > "$LOCAL_TMP_SCRIPT"
#!/bin/sh
echo "Stopping old firmware services..."

TARGETS="/etc/init.d/S55drone
/usr/sbin/droneangle.sh
/usr/sbin/done_dji_release"

found=0
for target in $TARGETS; do
    echo "Checking for processes matching: $target"
    pids=$(ps auxx | grep -F "$target" | grep -v grep | awk '{print $1}')
    if [ -n "$pids" ]; then
        echo "Found process(es) for [$target]: $pids"
        found=1
        for pid in $pids; do
            echo "Killing PID $pid..."
            kill -9 "$pid" 2>/dev/null
        done
    else
        echo "No processes found for [$target]."
    fi
done

if [ "$found" -eq 1 ]; then
    echo "Target processes killed."
else
    echo "No target processes were running."
fi

echo "Final process list (filtered):"
ps auxx | grep -E "S55drone|droneangle|done_dji_release" | grep -v grep
EOF

  else
    # New firmware uses same S55drone -> droneangle.sh chain, but droneangle.sh
    # is a watchdog loop that respawns drone_dji_rid_decode every second.
    # Must kill droneangle.sh FIRST or it will immediately restart the daemon.
    cat << 'EOF' > "$LOCAL_TMP_SCRIPT"
#!/bin/sh
echo "Stopping new firmware services..."

TARGETS="/etc/init.d/S55drone
/usr/sbin/droneangle.sh
drone_dji_rid_decode"

found=0
for target in $TARGETS; do
    echo "Checking for processes matching: $target"
    pids=$(ps | grep -F "$target" | grep -v grep | awk '{print $1}')
    if [ -n "$pids" ]; then
        echo "Found process(es) for [$target]: $pids"
        found=1
        for pid in $pids; do
            echo "Killing PID $pid..."
            kill -9 "$pid" 2>/dev/null
        done
    else
        echo "No processes found for [$target]."
    fi
done

if [ "$found" -eq 1 ]; then
    echo "Target processes killed."
else
    echo "No target processes were running."
fi

echo "Final process list (filtered):"
ps | grep -E "S55drone|droneangle|drone_dji_rid_decode" | grep -v grep
EOF
  fi

  chmod +x "$LOCAL_TMP_SCRIPT"

  echo "Copying kill script to remote host..."
  sshpass -p "$PASSWORD" scp -O $SSH_OPTS "$LOCAL_TMP_SCRIPT" "$USER@$HOST:/tmp/remote_kill.sh"

  echo "Executing remote kill script..."
  sshpass -p "$PASSWORD" ssh -tt $SSH_OPTS "$USER@$HOST" "sh /tmp/remote_kill.sh; rm /tmp/remote_kill.sh"

  rm "$LOCAL_TMP_SCRIPT"
  echo "Remote kill script executed."
}

# Function: Start the service.
start_service() {
  if [ "$FIRMWARE" = "old" ]; then
    echo "Starting Drone Daemon on remote host (old firmware)..."
    sshpass -p "$PASSWORD" ssh $SSH_OPTS "$USER@$HOST" "nohup /etc/init.d/S55drone start > /dev/null 2>&1 &"
  else
    echo "Starting drone_dji_rid_decode on remote host (new firmware)..."
    sshpass -p "$PASSWORD" ssh $SSH_OPTS "$USER@$HOST" "nohup /etc/init.d/S55drone start > /dev/null 2>&1 &"
  fi
}

# Detect firmware, then execute action.
detect_firmware

case "$ACTION" in
  stop)
    echo "Executing stop command..."
    stop_service
    ;;
  start)
    echo "Executing start command..."
    start_service
    ;;
  *)
    echo "Invalid option: $ACTION"
    echo "Usage: $0 [start|stop]"
    exit 1
    ;;
esac
