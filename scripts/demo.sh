#!/usr/bin/env bash
# Runs one version end to end and saves the client output and service logs to evidence/<version>/.
#
#   bash scripts/demo.sh rest
#   bash scripts/demo.sh grpc
#
# 1. start Inventory + Order, send a successful order and the error cases
# 2. restart Inventory with --delay 5 (longer than the 2s timeout/deadline); Order keeps running
#    -> the order fails cleanly, and the health check shows the Order Service is still up
# 3. restart Inventory without the delay -> the same Order Service process confirms orders again
#
# Works in Git Bash on Windows, and on macOS/Linux. Needs the .venv from the README.
set -u
VERSION=${1:-}
[[ $VERSION == rest || $VERSION == grpc ]] || { echo "usage: bash scripts/demo.sh rest|grpc" >&2; exit 2; }

cd "$(dirname "$0")/.."
export PYTHONUNBUFFERED=1
export PATH="$PWD/.venv/Scripts:$PWD/.venv/bin:$PATH"
DIR=evidence/$VERSION
rm -rf "$DIR" && mkdir -p "$DIR"

start() {  # LOGFILE CMD... -> prints the background pid
  local log=$1; shift
  "$@" > "$log" 2>&1 &
  echo $!
}
port_open() { (echo > "/dev/tcp/127.0.0.1/$1") 2>/dev/null; }
wait_port() {
  for _ in $(seq 1 100); do port_open "$1" && return 0; sleep 0.2; done
  echo "port $1 never opened" >&2; exit 1
}
stop() {  # PID PORT
  if [[ -r /proc/$1/winpid ]]; then  # Git Bash: kill the venv launcher and the interpreter under it
    taskkill //F //T //PID "$(cat "/proc/$1/winpid")" > /dev/null 2>&1
  else
    kill "$1" 2>/dev/null
  fi
  for _ in $(seq 1 50); do port_open "$2" || return 0; sleep 0.1; done
}
run() {  # reads one command from stdin, shows it and its output, and appends both to $OUT
  local cmd; cmd=$(cat)
  printf '$ %s\n' "$cmd" | tee -a "$OUT"
  bash -c "$cmd" 2>&1 | tee -a "$OUT"
  printf '\n' | tee -a "$OUT"
}

if [[ $VERSION == rest ]]; then
  INV_PORT=8001; ORD_PORT=8000
  inventory() { start "$1" python rest_version/inventory_service.py "${@:2}"; }
  order_service() { start "$DIR/order_service.log" python rest_version/order_service.py --timeout 2; }
  place() { run <<CMD
curl -s -i ${3:+$3 }-X POST http://127.0.0.1:8000/orders -H "Content-Type: application/json" -d '{"item_id": "$1", "quantity": $2}'
CMD
  }
  health() { run <<< 'curl -s -i http://127.0.0.1:8000/health'; }
else
  INV_PORT=50051; ORD_PORT=50052
  inventory() { start "$1" python grpc_version/inventory_server.py "${@:2}"; }
  order_service() { start "$DIR/order_service.log" python grpc_version/order_server.py --deadline 2; }
  place() { run <<< "python grpc_version/order_client.py $1 $2"; }
  health() { run <<< 'python grpc_version/order_client.py --health'; }
fi

INV=""; ORD=""
trap '[[ -n $INV ]] && stop "$INV" $INV_PORT; [[ -n $ORD ]] && stop "$ORD" $ORD_PORT' EXIT

echo "### 1. Normal operation"
OUT=$DIR/client_1_normal.txt
INV=$(inventory "$DIR/inventory_1_normal.log")
ORD=$(order_service)
wait_port $INV_PORT; wait_port $ORD_PORT
place laptop 2
if [[ $VERSION == rest ]]; then  # the Order -> Inventory call on its own
  run <<'CMD'
curl -s -i -X POST http://127.0.0.1:8001/inventory/check -H "Content-Type: application/json" -d '{"item_id": "laptop", "quantity": 2}'
CMD
fi
place monitor 5
place phone 1
place laptop 0

echo "### 2. Inventory restarted with --delay 5 (Order Service keeps running)"
stop "$INV" $INV_PORT
OUT=$DIR/client_2_slow_inventory.txt
INV=$(inventory "$DIR/inventory_2_delay.log" --delay 5)
wait_port $INV_PORT
place laptop 1 "-w '\ntime_total=%{time_total}s\n'"
health
# let the slow Inventory finish (REST) or notice the expired deadline (gRPC) so its log shows it
for _ in $(seq 1 40); do grep -qE "200 OK|abandoning" "$DIR/inventory_2_delay.log" && break; sleep 0.2; done

echo "### 3. Inventory restarted without the delay (same Order Service process)"
stop "$INV" $INV_PORT
OUT=$DIR/client_3_recovered.txt
INV=$(inventory "$DIR/inventory_3_normal.log")
wait_port $INV_PORT
place laptop 1
health

echo "Saved to $DIR/"
