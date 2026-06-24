#!/usr/bin/env bash
# Distribute the lock_bot SSH PUBLIC key to wxtky nodes' authorized_keys,
# then verify passwordless (BatchMode) login works for GPU usage collection.
#
# Safe by design: only the PUBLIC key (~/.ssh/id_ed25519.pub) is sent.
# The private key never leaves this host.
#
# Usage:
#   tools/distribute_ssh_key.sh            # distribute + verify all nodes
#   tools/distribute_ssh_key.sh --verify   # verify only, no key push
#
# You will be prompted for each node's password during the push step
# (unless the key is already installed there).

set -uo pipefail

SSH_USER="${SSH_USER:-v_qiujie04}"
PUBKEY="${HOME}/.ssh/id_ed25519.pub"

NODES=(
  10.206.192.106
  10.206.192.139
  10.206.192.140
  10.206.192.141
  10.206.192.142
  10.206.192.143
  10.206.192.144
  10.206.192.145
  10.206.192.146
  10.206.192.147
  10.206.192.148
  10.206.192.149
)

VERIFY_ONLY=0
[[ "${1:-}" == "--verify" ]] && VERIFY_ONLY=1

if [[ ! -f "$PUBKEY" ]]; then
  echo "ERROR: public key not found: $PUBKEY" >&2
  exit 1
fi

echo "SSH user : $SSH_USER"
echo "Pub key  : $PUBKEY"
echo "Nodes    : ${#NODES[@]}"
echo "Mode     : $([[ $VERIFY_ONLY -eq 1 ]] && echo 'VERIFY ONLY' || echo 'DISTRIBUTE + VERIFY')"
echo "------------------------------------------------------------"

# Verify passwordless login + xpu-smi availability. Mirrors how the bot
# connects: BatchMode=yes (key-only, never prompts), host checking off.
verify_node() {
  local ip="$1"
  ssh -o BatchMode=yes \
      -o StrictHostKeyChecking=no \
      -o UserKnownHostsFile=/dev/null \
      -o ConnectTimeout=5 \
      "${SSH_USER}@${ip}" 'command -v xpu-smi >/dev/null 2>&1 && echo XPU_OK || echo XPU_MISSING' \
      2>/dev/null
}

push_key() {
  local ip="$1"
  if command -v ssh-copy-id >/dev/null 2>&1; then
    ssh-copy-id -o StrictHostKeyChecking=no -o ConnectTimeout=10 \
                -i "$PUBKEY" "${SSH_USER}@${ip}" >/dev/null 2>&1
  else
    # Fallback: append manually, de-duped, with correct perms.
    local key
    key="$(cat "$PUBKEY")"
    ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 "${SSH_USER}@${ip}" \
      "mkdir -p ~/.ssh && chmod 700 ~/.ssh && \
       touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && \
       grep -qxF '$key' ~/.ssh/authorized_keys || echo '$key' >> ~/.ssh/authorized_keys"
  fi
}

ok=()      # passwordless + xpu-smi present
no_xpu=()  # passwordless but xpu-smi missing
fail=()    # still cannot log in passwordless

for ip in "${NODES[@]}"; do
  printf '%-18s ' "$ip"

  res="$(verify_node "$ip")"
  if [[ "$res" != "XPU_OK" && "$res" != "XPU_MISSING" && $VERIFY_ONLY -eq 0 ]]; then
    echo "key missing -> pushing (enter password if prompted)..."
    push_key "$ip"
    printf '%-18s ' "$ip"
    res="$(verify_node "$ip")"
  fi

  case "$res" in
    XPU_OK)      echo "OK (passwordless + xpu-smi)"; ok+=("$ip") ;;
    XPU_MISSING) echo "WARN passwordless OK, but xpu-smi NOT found"; no_xpu+=("$ip") ;;
    *)           echo "FAIL passwordless login not working"; fail+=("$ip") ;;
  esac
done

echo "------------------------------------------------------------"
echo "Summary:"
echo "  OK        : ${#ok[@]}/${#NODES[@]}  ${ok[*]:-}"
echo "  no xpu-smi: ${#no_xpu[@]}  ${no_xpu[*]:-}"
echo "  FAILED    : ${#fail[@]}  ${fail[*]:-}"

[[ ${#fail[@]} -eq 0 ]]
