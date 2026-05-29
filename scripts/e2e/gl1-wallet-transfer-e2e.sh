#!/usr/bin/env bash
set -Eeuo pipefail

# End-to-end GL1 wallet compliance demo.
#
# This script proves the local path:
# GEDA -> QVI -> LE -> OOR -> GL1 Wallet Attestation -> vLEI Oracle
# -> vlei-comply registries -> compliant wvLEI transfer.
#
# It intentionally uses shell as the orchestrator so every protocol boundary is
# visible: KLI drives KERI/vLEI issuance and IPEX, Forge/Anvil deploy contracts,
# Cast performs local contract checkpoints, and vlei-oracle handles EIP-712
# challenge/signature and verifier/oracle service behavior.
#
# Generated artifacts live under RUN_DIR and include KERI config, deployment
# logs, contract addresses, actor exports, wallet typed data, wallet ACDCs,
# oracle status JSON, and the final balance/CCID result. By default the script
# starts local services and cleans them up on exit. Set KEEP_SERVICES=1 to leave
# started services alive. Set E2E_STOP_AFTER=source-chain or oracle-posted to
# build a reusable fixture for the human vlei-comply guide.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORACLE_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
GL1_DIR="$(cd "${ORACLE_DIR}/.." && pwd)"
WORKSPACE_DIR="$(cd "${GL1_DIR}/../.." && pwd)"
COMPLY_DIR="${GL1_DIR}/vlei-comply"
KERIPY_DIR="${WORKSPACE_DIR}/core/python/keripy"
KERI_DEMO_DIR="${WORKSPACE_DIR}/core/python/keripy/scripts/demo"
VLEI_SCHEMA_TOOLS_DIR="${WORKSPACE_DIR}/credentials/schema-tools/vLEI"

KLI="${KLI:-${ORACLE_DIR}/.venv/bin/kli}"
VLEI_ORACLE="${VLEI_ORACLE:-${ORACLE_DIR}/.venv/bin/vlei-oracle}"
VLEI_SERVER="${VLEI_SERVER:-${ORACLE_DIR}/.venv/bin/vLEI-server}"
FORGE="${FORGE:-${HOME}/.foundry/bin/forge}"
CAST="${CAST:-${HOME}/.foundry/bin/cast}"
ANVIL="${ANVIL:-${HOME}/.foundry/bin/anvil}"
JQ="${JQ:-jq}"

RPC_URL="${RPC_URL:-http://127.0.0.1:8545}"
CHAIN_ID="${CHAIN_ID:-31337}"
ANVIL_PORT="${ANVIL_PORT:-8545}"
VLEI_SCHEMA_PORT="${VLEI_SCHEMA_PORT:-7723}"
ORACLE_HTTP_PORT="${ORACLE_HTTP_PORT:-9723}"
ORACLE_URL="${ORACLE_URL:-http://127.0.0.1:${ORACLE_HTTP_PORT}}"

ADMIN_PK="${ADMIN_PK:-0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80}"
ORIGINATOR_PK="${ORIGINATOR_PK:-0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d}"
BENEFICIARY_PK="${BENEFICIARY_PK:-0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a}"

RUN_ID="${RUN_ID:-gl1-wallet-transfer-$(date +%Y%m%d-%H%M%S)}"
RUN_DIR="${RUN_DIR:-${ORACLE_DIR}/runs/${RUN_ID}}"
KERI_BASE="${KERI_BASE:-${RUN_ID}}"
KERI_RUN_CONFIG_DIR="${RUN_DIR}/keri-config"
DEPLOY_LOG="${DEPLOY_LOG:-${RUN_DIR}/deploy-all.log}"
CONTRACTS_JSON="${CONTRACTS_JSON:-${RUN_DIR}/contracts.json}"
ACTORS_ENV="${ACTORS_ENV:-${RUN_DIR}/actors.env}"
ACTORS_JSON="${ACTORS_JSON:-${RUN_DIR}/actors.json}"
E2E_STOP_AFTER="${E2E_STOP_AFTER:-}"

QVI_SCHEMA="EBfdlu8R27Fbx-ehrqwImnK-8Cm79sqbAQ4MmvEAYqao"
LE_SCHEMA="ENPXp1vQzRF6JwIuS-mp2U8Uf1MoADoP_GqQ62VsDZWY"
OOR_AUTH_SCHEMA="EKA57bKBKxr_kN7iN5i7lMUxpMG-s19dRcmov1iDxz-E"
OOR_SCHEMA="EBNaNu-M9P5cgrnfl2Fvymy4E_jvxxyjb70PRtiANlJy"
ECR_SCHEMA="EEy9PkikFcANV1l7EHukCeXqrzT1hNZjGlUk7wuMO5jw"  # ECR vLEI Credential
ECR_AUTH_SCHEMA="EH6ekLjSr8V32WyFbGe1zXjTzFs9PkTYmupJ9H65O14g"  # ECR Auth vLEI Credential
IXBRL_ATTESTATION_SCHEMA="EMhvwOlyEJ9kN4PrwCpr9Jsv7TxPhiYveZ0oP3lJzdEi"  # iXBRL Data Value Attestation
WALLET_SCHEMA="EORcMutKdscrCxXLEhMnnjxrZocxDwSo7-VItx6lh6Z7"

QVI_LEI="${QVI_LEI:-6383001AJTYIGC8Y1X37}"
ORIGINATOR_LEI="${ORIGINATOR_LEI:-5493001KJTIIGC8Y1R17}"
BENEFICIARY_LEI="${BENEFICIARY_LEI:-54930084UKLVMY22DS16}"
TRANSFER_AMOUNT="${TRANSFER_AMOUNT:-1000000000000000000}"
SEED_AMOUNT="${SEED_AMOUNT:-5000000000000000000}"
GAS_FUND_AMOUNT="${GAS_FUND_AMOUNT:-1000000000000000000}"

PIDS=()

# Print a timestamped progress marker. These markers are the primary way to
# locate the failing phase in long KLI/Forge runs.
log() {
  printf '\n[%s] %s\n' "$(date +%H:%M:%S)" "$*"
}

# Require an executable path or command name before the run mutates local state.
# Args: executable path or command name.
need() {
  if [[ ! -x "$1" ]] && ! command -v "$1" >/dev/null 2>&1; then
    printf 'Missing required executable: %s\n' "$1" >&2
    exit 1
  fi
}

# Stop services started by this script unless KEEP_SERVICES=1 was set.
cleanup() {
  if [[ "${KEEP_SERVICES:-0}" == "1" ]]; then
    return
  fi
  for pid in "${PIDS[@]:-}"; do
    if kill -0 "$pid" >/dev/null 2>&1; then
      kill "$pid" >/dev/null 2>&1 || true
    fi
  done
}
trap cleanup EXIT

# Wait until a TCP port is listening.
# Args: port, human-readable service label.
wait_for_port() {
  local port="$1"
  local label="$2"
  for _ in $(seq 1 80); do
    if nc -z 127.0.0.1 "$port" >/dev/null 2>&1; then
      return
    fi
    sleep 0.25
  done
  printf 'Timed out waiting for %s on port %s\n' "$label" "$port" >&2
  exit 1
}

# Reuse an already-listening local service or start one and track its PID.
# Args: port, label, log file, command...
start_service_if_needed() {
  local port="$1"
  local label="$2"
  local log_file="$3"
  shift 3

  if nc -z 127.0.0.1 "$port" >/dev/null 2>&1; then
    log "${label} already listening on ${port}; reusing it"
    return
  fi

  log "Starting ${label}"
  if [[ "${KEEP_SERVICES:-0}" == "1" ]]; then
    nohup "$@" >"$log_file" 2>&1 &
  else
    "$@" >"$log_file" 2>&1 &
  fi
  PIDS+=("$!")
  wait_for_port "$port" "$label"
}

# Require that a local HTTP endpoint serves expected content.
# Args: URL, human-readable service label.
require_http() {
  local url="$1"
  local label="$2"
  for _ in $(seq 1 40); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      return
    fi
    sleep 0.25
  done
  printf '%s is not serving the expected endpoint: %s\n' "$label" "$url" >&2
  printf 'Stop the process occupying the port or run the expected local service before re-running.\n' >&2
  exit 1
}

# Verify the local KERIpy demo witness config has both TCP and HTTP curls.
# The witness database reads this config once at initialization, so a stale or
# incomplete file leads to AIDs that cannot publish usable witness OOBIs.
require_demo_witness_config() {
  local alias="$1"
  local tcp_url="$2"
  local http_url="$3"
  local config="${KERIPY_DIR}/scripts/keri/cf/main/${alias}.json"

  if [[ ! -f "$config" ]]; then
    printf 'Missing demo witness config: %s\n' "$config" >&2
    exit 1
  fi

  if ! "$JQ" -e --arg alias "$alias" --arg tcp "$tcp_url" --arg http "$http_url" \
    '.[$alias].curls | index($tcp) and index($http)' "$config" >/dev/null; then
    printf 'Demo witness config %s does not contain required curls %s and %s\n' \
      "$config" "$tcp_url" "$http_url" >&2
    exit 1
  fi
}

# Clear persisted demo witness stores when no witness process is already using
# the ports. KERI witnesses bootstrap curls once, so this reset is what makes a
# corrected local config take effect for fresh e2e runs.
reset_demo_witness_state() {
  if [[ "${RESET_DEMO_WITNESSES:-1}" != "1" ]]; then
    return
  fi

  for port in 5632 5633 5634 5635 5636 5637 5642 5643 5644 5645 5646 5647; do
    if nc -z 127.0.0.1 "$port" >/dev/null 2>&1; then
      log "Demo witness port ${port} is already active; leaving existing witness stores intact"
      return
    fi
  done

  log "Clearing persisted demo witness stores so curls config is reapplied"
  for name in wan wil wes wit wub wyz; do
    rm -rf \
      "${HOME}/.keri/ks/${name}" \
      "${HOME}/.keri/db/${name}" \
      "${HOME}/.keri/reg/${name}" \
      "${HOME}/.keri/mbx/${name}"
  done
}

# Write KLI init configs for regular actors and the oracle actor.
# Regular actors need GL1 wallet schema OOBIs to issue/admit credentials. The
# oracle actor intentionally does not: the service pins accepted GL1 schemas
# internally during startup.
write_kli_config() {
  local config_file="${KERI_RUN_CONFIG_DIR}/keri/cf/gl1-demo-oobis-schema.json"
  local oracle_config_file="${KERI_RUN_CONFIG_DIR}/keri/cf/gl1-demo-oobis-schema-oracle.json"
  mkdir -p "$(dirname "$config_file")"

  write_kli_config_file "$config_file" true
  write_kli_config_file "$oracle_config_file" false
}

# Emit one KLI config JSON file.
# Args: output file, include wallet schema OOBI boolean.
write_kli_config_file() {
  local config_file="$1"
  local include_wallet="$2"

  "$JQ" -n \
    --argjson includeWallet "$include_wallet" \
    --arg dt "2022-01-20T12:57:59.823350+00:00" \
    --arg qvi "$QVI_SCHEMA" \
    --arg le "$LE_SCHEMA" \
    --arg oorAuth "$OOR_AUTH_SCHEMA" \
    --arg oor "$OOR_SCHEMA" \
    --arg ixbrl "$IXBRL_ATTESTATION_SCHEMA" \
    --arg ecr "$ECR_SCHEMA" \
    --arg ecrAuth "$ECR_AUTH_SCHEMA" \
    --arg wallet "$WALLET_SCHEMA" \
    --arg port "$VLEI_SCHEMA_PORT" \
    '{
      dt: $dt,
      iurls: [
        "http://127.0.0.1:5642/oobi/BBilc4-L3tFUnfM_wJr4S4OJanAv_VmF_dJNN6vkf2Ha/controller",
        "http://127.0.0.1:5643/oobi/BLskRTInXnMxWaGqcpSyMgo0nYbalW99cGZESrz3zapM/controller",
        "http://127.0.0.1:5644/oobi/BIKKuvBwpmDVA4Ds-EpL5bt9OqPzWPja2LigFYZN2YfX/controller"
      ],
      durls: ([
        ("http://127.0.0.1:" + $port + "/oobi/" + $oor),
        ("http://127.0.0.1:" + $port + "/oobi/" + $ixbrl),
        ("http://127.0.0.1:" + $port + "/oobi/" + $oorAuth),
        ("http://127.0.0.1:" + $port + "/oobi/" + $ecr),
        ("http://127.0.0.1:" + $port + "/oobi/" + $le),
        ("http://127.0.0.1:" + $port + "/oobi/" + $ecrAuth),
        ("http://127.0.0.1:" + $port + "/oobi/" + $qvi)
      ] + (if $includeWallet then [("http://127.0.0.1:" + $port + "/oobi/" + $wallet)] else [] end))
    }' >"$config_file"
}

# Run a command with a simple wall-clock timeout for KLI operations that can
# block when remote OOBIs or mailboxes are not yet reachable.
# Args: timeout seconds, command...
run_with_timeout() {
  local seconds="$1"
  shift
  "$@" &
  local pid="$!"
  for _ in $(seq 1 "$seconds"); do
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      wait "$pid"
      return $?
    fi
    sleep 1
  done
  kill "$pid" >/dev/null 2>&1 || true
  wait "$pid" >/dev/null 2>&1 || true
  printf 'Timed out after %ss: %s\n' "$seconds" "$*" >&2
  return 124
}

# Send a state-changing local transaction through Cast and discard stdout.
# Args: private key, contract address, signature, signature args...
cast_send() {
  "$CAST" send --rpc-url "$RPC_URL" --private-key "$1" "${@:2}" >/dev/null
}

# Return success when the parsed deployment points at live code on the current
# RPC chain. This prevents a fresh Anvil process from reusing stale addresses
# from a previous run directory.
contracts_have_code() {
  if [[ ! -f "$CONTRACTS_JSON" ]]; then
    return 1
  fi

  local wrapper
  wrapper="$("$JQ" -r '.wrapper // empty' "$CONTRACTS_JSON")"
  if [[ -z "$wrapper" || "$wrapper" == "null" ]]; then
    return 1
  fi

  local code
  code="$("$CAST" code --rpc-url "$RPC_URL" "$wrapper" 2>/dev/null || true)"
  [[ -n "$code" && "$code" != "0x" ]]
}

# Fund a participant EOA with local Anvil ETH for later token/wrapper calls.
# Args: wallet address.
fund_gas_wallet() {
  "$CAST" send --rpc-url "$RPC_URL" --private-key "$ADMIN_PK" --value "$GAS_FUND_AMOUNT" "$1" >/dev/null
}

# Assert a staticcall reverts. Used before wallet allowlisting, where policy is
# expected to fail hard rather than return false.
# Args: checkpoint label.
expect_transfer_revert() {
  local label="$1"
  set +e
  local output
  output=$("$CAST" call --rpc-url "$RPC_URL" --from "$ORIGINATOR_WALLET" "$WRAPPER" \
    "transfer(address,uint256)(bool)" "$BENEFICIARY_WALLET" "$TRANSFER_AMOUNT" 2>&1)
  local status=$?
  set -e
  if [[ "$status" -eq 0 ]]; then
    printf 'Expected transfer revert for %s, but call succeeded: %s\n' "$label" "$output" >&2
    exit 1
  fi
  log "Expected revert observed: ${label}"
}

# Assert wrapper transfer policy returns a boolean result.
# Args: expected boolean string, checkpoint label.
expect_transfer_bool() {
  local expected="$1"
  local label="$2"
  local output
  output=$("$CAST" call --rpc-url "$RPC_URL" --from "$ORIGINATOR_WALLET" "$WRAPPER" \
    "transfer(address,uint256)(bool)" "$BENEFICIARY_WALLET" "$TRANSFER_AMOUNT")
  if [[ "$expected" == "false" ]]; then
    if [[ "$output" != *"false"* && "$output" != *"0000000000000000000000000000000000000000000000000000000000000000"* ]]; then
      printf 'Expected false transfer result for %s, got: %s\n' "$label" "$output" >&2
      exit 1
    fi
  else
    if [[ "$output" != *"true"* && "$output" != *"0000000000000000000000000000000000000000000000000000000000000001"* ]]; then
      printf 'Expected true transfer result for %s, got: %s\n' "$label" "$output" >&2
      exit 1
    fi
  fi
  log "Expected ${expected} transfer result observed: ${label}"
}

# Wait for an oracle operation to reach the posted terminal state.
# Args: wallet credential SAID, output status JSON path.
wait_oracle_posted() {
  wait_oracle_state "$1" posted "$2"
}

# Poll the oracle presentation status endpoint until an expected state appears.
# Args: wallet credential SAID, expected state, output status JSON path.
wait_oracle_state() {
  local wallet_said="$1"
  local expected="$2"
  local out="$3"
  for _ in $(seq 1 120); do
    if curl -fsS "${ORACLE_URL}/presentations/${wallet_said}" >"$out" 2>/dev/null; then
      local state
      state="$("$JQ" -r .state "$out")"
      if [[ "$state" == "$expected" ]]; then
        return
      fi
      if [[ "$state" == "failed" ]]; then
        cat "$out" >&2
        exit 1
      fi
    fi
    sleep 1
  done
  printf 'Timed out waiting for vLEI Oracle presentation %s to reach state %s\n' "$wallet_said" "$expected" >&2
  exit 1
}

# Return an AID prefix from a KLI habitat.
# Args: KLI name, alias.
aid_for() {
  "$KLI" status --name "$1" --base "$KERI_BASE" --alias "$2" \
    | awk '/^Identifier:/ {print $2; exit}'
}

# Map the three demo witness AIDs used by this script to their HTTP ports.
# Args: witness AID.
witness_http_port() {
  case "$1" in
    BBilc4-L3tFUnfM_wJr4S4OJanAv_VmF_dJNN6vkf2Ha) printf '5642\n' ;;
    BLskRTInXnMxWaGqcpSyMgo0nYbalW99cGZESrz3zapM) printf '5643\n' ;;
    BIKKuvBwpmDVA4Ds-EpL5bt9OqPzWPja2LigFYZN2YfX) printf '5644\n' ;;
    *) printf 'unknown witness %s\n' "$1" >&2; exit 1 ;;
  esac
}

# Build a witness OOBI URL for a newly incepted actor.
# Args: KLI name, alias, witness AID.
oobi_for() {
  local aid
  local port
  aid="$(aid_for "$1" "$2")"
  port="$(witness_http_port "$3")"
  printf 'http://127.0.0.1:%s/oobi/%s/witness/%s\n' "$port" "$aid" "$3"
}

# Resolve one participant OOBI into a KLI database.
# Args: receiver KLI name, OOBI URL, local alias for the subject.
resolve_oobi() {
  local name="$1"
  local oobi="$2"
  local alias="$3"
  run_with_timeout 45 "$KLI" oobi resolve --name "$name" --base "$KERI_BASE" \
    --oobi "$oobi" --oobi-alias "$alias" >/dev/null
}

# Resolve one schema OOBI into a KLI database.
# Args: receiver KLI name, schema SAID, local alias.
resolve_schema_oobi() {
  local name="$1"
  local schema="$2"
  local alias="$3"
  "$KLI" oobi resolve --name "$name" --base "$KERI_BASE" \
    --oobi "http://127.0.0.1:${VLEI_SCHEMA_PORT}/oobi/${schema}" \
    --oobi-alias "$alias" >/dev/null
}

# Initialize and incept one single-sig AID with one witness.
# Args: KLI name, alias, salt, witness AID, optional config-file name.
incept_actor() {
  local name="$1"
  local alias="$2"
  local salt="$3"
  local wit="$4"
  local config_file="${5:-gl1-demo-oobis-schema}"

  "$KLI" init --name "$name" --base "$KERI_BASE" --salt "$salt" --nopasscode \
    --config-dir "$KERI_RUN_CONFIG_DIR" --config-file "$config_file" >/dev/null
  "$KLI" incept --name "$name" --base "$KERI_BASE" --alias "$alias" \
    --transferable \
    --wits "$wit" --toad 1 --icount 1 --isith 1 --ncount 1 --nsith 1 >/dev/null

  local aid
  local port
  aid="$(aid_for "$name" "$alias")"
  port="$(witness_http_port "$wit")"
  for _ in $(seq 1 20); do
    if curl -fsS "http://127.0.0.1:${port}/oobi/${aid}/witness/${wit}" >/dev/null 2>&1; then
      return
    fi
    sleep 0.5
  done
  printf 'Witness %s did not publish OOBI for %s (%s).\n' "$wit" "$alias" "$aid" >&2
  printf 'The usual cause is another process occupying the demo witness ports without running a usable `kli witness demo`.\n' >&2
  exit 1
}

# Create a credential registry for one actor.
# Args: KLI name, alias, registry name.
incept_registry() {
  "$KLI" vc registry incept --name "$1" --base "$KERI_BASE" --alias "$2" --registry-name "$3" >/dev/null
}

# Ask KERIpy to inject a SAID into a JSON SAD file in place.
# Args: JSON file path.
saidify_file() {
  "$KLI" saidify --file "$1" >/dev/null
}

# Return the latest issued credential SAID for an actor and schema.
# Args: KLI name, alias, schema SAID.
latest_issued_said() {
  "$KLI" vc list --name "$1" --base "$KERI_BASE" --alias "$2" --issued --said --schema "$3" \
    | tail -n 1
}

# Extract the credential SAID from `kli vc create` output.
# Args: raw KLI output.
created_said_from_output() {
  local output="$1"
  local said
  said="$(printf '%s\n' "$output" | awk '/has been created/ {print $1; exit}')"
  if [[ -z "$said" ]]; then
    printf 'Unable to determine created credential SAID from KLI output:\n%s\n' "$output" >&2
    exit 1
  fi
  printf '%s\n' "$said"
}

# Extract the EXN SAID from `kli ipex grant` output.
# Args: raw KLI output.
grant_said_from_output() {
  local output="$1"
  local said
  said="$(printf '%s\n' "$output" | awk '/^Sending message / {print $3; exit}')"
  if [[ -z "$said" ]]; then
    printf 'Unable to determine IPEX grant SAID from KLI output:\n%s\n' "$output" >&2
    exit 1
  fi
  printf '%s\n' "$said"
}

# Admit a normal vLEI source-chain Grant into a holder's KLI database.
# The oracle wallet presentation deliberately does not use this helper; the
# running oracle reacts to the IPEX Grant notification directly.
# Args: recipient KLI name, recipient alias, grant EXN SAID.
admit_grant() {
  local name="$1"
  local alias="$2"
  local grant_said="$3"

  for _ in $(seq 1 30); do
    if "$KLI" ipex list --name "$name" --base "$KERI_BASE" --alias "$alias" --poll --said --type grant 2>/dev/null \
      | grep -Fx "$grant_said" >/dev/null; then
      break
    fi
    sleep 1
  done
  if ! "$KLI" ipex list --name "$name" --base "$KERI_BASE" --alias "$alias" --said --type grant 2>/dev/null \
    | grep -Fx "$grant_said" >/dev/null; then
    printf 'Timed out waiting for grant IPEX message %s for %s/%s\n' "$grant_said" "$name" "$alias" >&2
    exit 1
  fi
  "$KLI" ipex admit --name "$name" --base "$KERI_BASE" --alias "$alias" --said "$grant_said" >/dev/null
}

# Issue a vLEI credential, send it as an IPEX Grant, and admit it at the holder.
# This is used for source-chain credentials where the holder's KLI store must
# contain the admitted credential before later credentials can reference it.
# Args: issuer name, issuer alias, registry, schema, recipient AID, recipient
# name, recipient alias, data file, optional edges file, optional rules file.
issue_and_grant() {
  local issuer_name="$1"
  local issuer_alias="$2"
  local registry="$3"
  local schema="$4"
  local recipient_aid="$5"
  local recipient_name="$6"
  local recipient_alias="$7"
  local data_file="$8"
  local edges_file="${9:-}"
  local rules_file="${10:-${KERI_DEMO_DIR}/data/rules.json}"

  local args=(
    vc create
    --name "$issuer_name"
    --base "$KERI_BASE"
    --alias "$issuer_alias"
    --registry-name "$registry"
    --schema "$schema"
    --recipient "$recipient_aid"
    --data "@${data_file}"
    --rules "@${rules_file}"
  )

  if [[ -n "$edges_file" ]]; then
    args+=(--edges "@${edges_file}")
  fi

  local create_output
  create_output="$("$KLI" "${args[@]}")"
  local said
  said="$(created_said_from_output "$create_output")"
  local grant_output
  grant_output="$("$KLI" ipex grant --name "$issuer_name" --base "$KERI_BASE" --alias "$issuer_alias" \
    --said "$said" --recipient "$recipient_aid")"
  local grant_said
  grant_said="$(grant_said_from_output "$grant_output")"
  admit_grant "$recipient_name" "$recipient_alias" "$grant_said"
  printf '%s\n' "$said"
}

# Write a JSON file with jq.
# Args: output file, jq arguments/expression.
write_json() {
  local out="$1"
  shift
  "$JQ" -n "$@" >"$out"
}

# Issue one party's LE and OOR branch under the shared QVI.
# Args: party label, LE name/alias/AID, OOR name/alias/AID, LEI, person, role.
build_party_chain() {
  local party="$1"
  local le_name="$2"
  local le_alias="$3"
  local le_aid="$4"
  local oor_name="$5"
  local oor_alias="$6"
  local oor_aid="$7"
  local lei="$8"
  local person="$9"
  local role="${10}"

  local dir="${RUN_DIR}/${party}"
  mkdir -p "$dir"

  write_json "$dir/legal-entity-data.json" --arg LEI "$lei" '{LEI:$LEI}'
  write_json "$dir/oor-auth-data.json" \
    --arg AID "$oor_aid" --arg LEI "$lei" --arg person "$person" --arg role "$role" \
    '{AID:$AID, LEI:$LEI, personLegalName:$person, officialRole:$role}'
  write_json "$dir/oor-data.json" \
    --arg LEI "$lei" --arg person "$person" --arg role "$role" \
    '{LEI:$LEI, personLegalName:$person, officialRole:$role}'

  write_json "$dir/legal-entity-edges.json" \
    --arg qvi "$QVI_SAID" --arg schema "$QVI_SCHEMA" \
    '{d:"", qvi:{n:$qvi, s:$schema}}'
  saidify_file "$dir/legal-entity-edges.json"

  local le_said
  le_said="$(issue_and_grant qvi qvi vLEI-qvi "$LE_SCHEMA" "$le_aid" "$le_name" "$le_alias" \
    "$dir/legal-entity-data.json" "$dir/legal-entity-edges.json")"

  write_json "$dir/oor-auth-edges.json" \
    --arg le "$le_said" --arg schema "$LE_SCHEMA" \
    '{d:"", le:{n:$le, s:$schema}}'
  saidify_file "$dir/oor-auth-edges.json"

  local auth_said
  auth_said="$(issue_and_grant "$le_name" "$le_alias" "vLEI-${le_name}" "$OOR_AUTH_SCHEMA" "$QVI_AID" qvi qvi \
    "$dir/oor-auth-data.json" "$dir/oor-auth-edges.json")"

  write_json "$dir/oor-edges.json" \
    --arg auth "$auth_said" --arg schema "$OOR_AUTH_SCHEMA" \
    '{d:"", auth:{n:$auth, s:$schema, o:"I2I"}}'
  saidify_file "$dir/oor-edges.json"

  local oor_said
  oor_said="$(issue_and_grant qvi qvi vLEI-qvi "$OOR_SCHEMA" "$oor_aid" "$oor_name" "$oor_alias" \
    "$dir/oor-data.json" "$dir/oor-edges.json")"

  "$JQ" -n \
    --arg leAid "$le_aid" \
    --arg oorAid "$oor_aid" \
    --arg leSaid "$le_said" \
    --arg oorSaid "$oor_said" \
    --arg lei "$lei" \
    '{legalEntityAid:$leAid, oorAid:$oorAid, leCredentialSaid:$leSaid, oorCredentialSaid:$oorSaid, lei:$lei}' \
    >"${RUN_DIR}/${party}-chain.json"
}

# Issue the GL1 wallet attestation and send it to the running oracle by IPEX
# Grant. This creates the ACDC only; the Ethereum wallet signature is external
# evidence and is posted later by present_wallet_attestation.
# Args: party label, EVM wallet address, EVM wallet private key, OOR KLI name,
# OOR alias.
issue_wallet_attestation() {
  local party="$1"
  local wallet="$2"
  local wallet_pk="$3"
  local oor_name="$4"
  local oor_alias="$5"

  local chain="${RUN_DIR}/${party}-chain.json"
  local dir="${RUN_DIR}/${party}"
  local issued_at
  local expires_at
  issued_at="$(date +%s)"
  expires_at="$((issued_at + 3600))"

  "$VLEI_ORACLE" challenge \
    --legal-entity-aid "$("$JQ" -r .legalEntityAid "$chain")" \
    --lei "$("$JQ" -r .lei "$chain")" \
    --oor-aid "$("$JQ" -r .oorAid "$chain")" \
    --le-credential-said "$("$JQ" -r .leCredentialSaid "$chain")" \
    --oor-credential-said "$("$JQ" -r .oorCredentialSaid "$chain")" \
    --wallet "$wallet" \
    --chain-id "$CHAIN_ID" \
    --vlei-token "$VLEI_TOKEN" \
    --wrapper "$WRAPPER" \
    --identity-registry "$IDENTITY_REGISTRY" \
    --credential-registry "$CREDENTIAL_REGISTRY" \
    --oracle-registry "$ORACLE_REGISTRY" \
    --nonce "${party}-${RUN_ID}" \
    --issued-at "$issued_at" \
    --expires-at "$expires_at" \
    --purpose GL1_WVLEI_TRANSFER \
    --out "$dir/wallet-challenge.json"

  "$VLEI_ORACLE" wallet-data \
    --typed-data "$dir/wallet-challenge.json" \
    --out "$dir/wallet-data.json"

  # Wallet attestations are self-attested by the OOR holder. NI2I is required
  # here because the OOR issuer is not the issuer of the LE or OOR source
  # credentials.
  write_json "$dir/wallet-edges.json" \
    --arg le "$("$JQ" -r .leCredentialSaid "$chain")" \
    --arg leSchema "$LE_SCHEMA" \
    --arg oor "$("$JQ" -r .oorCredentialSaid "$chain")" \
    --arg oorSchema "$OOR_SCHEMA" \
    '{d:"", le:{n:$le, s:$leSchema, o:"NI2I"}, oor:{n:$oor, s:$oorSchema, o:"NI2I"}}'
  saidify_file "$dir/wallet-edges.json"

  local create_output
  create_output="$("$KLI" vc create \
    --name "$oor_name" \
    --base "$KERI_BASE" \
    --alias "$oor_alias" \
    --registry-name "vLEI-${oor_name}" \
    --schema "$WALLET_SCHEMA" \
    --recipient "$("$JQ" -r .legalEntityAid "$chain")" \
    --data "@${dir}/wallet-data.json" \
    --edges "@${dir}/wallet-edges.json")"

  local wallet_said
  wallet_said="$(created_said_from_output "$create_output")"
  local grant_output
  grant_output="$("$KLI" ipex grant --name "$oor_name" --base "$KERI_BASE" --alias "$oor_alias" \
    --said "$wallet_said" --recipient "$ORACLE_AID")"
  local grant_said
  grant_said="$(grant_said_from_output "$grant_output")"

  "$JQ" -n \
    --arg walletSaid "$wallet_said" \
    --arg grantSaid "$grant_said" \
    '{walletSaid:$walletSaid, grantSaid:$grantSaid}' \
    >"$dir/wallet-presentation.json"
}

# Complete the wallet presentation by waiting for the oracle-created pending
# operation, signing the typed data with the EVM key, and POSTing the
# CESR-encoded signature to the oracle.
# Args: party label, EVM wallet private key, OOR KLI name.
present_wallet_attestation() {
  local party="$1"
  local wallet_pk="$2"
  local oor_name="$3"
  local dir="${RUN_DIR}/${party}"
  local wallet_said
  wallet_said="$("$JQ" -r .walletSaid "$dir/wallet-presentation.json")"

  wait_oracle_state "$wallet_said" awaiting_signature "$dir/oracle-awaiting-signature.json"

  "$VLEI_ORACLE" client post-signature \
    --oracle-url "$ORACLE_URL" \
    --wallet-said "$wallet_said" \
    --typed-data "$dir/wallet-challenge.json" \
    --private-key "$wallet_pk"

  wait_oracle_posted "$wallet_said" "$dir/oracle-status.json"
  "$JQ" .verified "$dir/oracle-status.json" >"$dir/verified.json"
  "$JQ" -r .legalEntityAid "$dir/verified.json" >"$dir/legal-entity-aid.txt"
}

# Write reusable fixture exports for the human CLI guide and vlei-comply init
# script. Source-chain runs stop with AIDs and credential SAIDs. Oracle-posted
# runs add wallet credential SAIDs, AID CCIDs, and verified oracle artifacts.
# Args: fixture stage label.
write_actor_outputs() {
  local stage="$1"
  local originator_le_said
  local originator_oor_said
  local beneficiary_le_said
  local beneficiary_oor_said
  local originator_wallet_said=""
  local beneficiary_wallet_said=""
  local originator_grant_said=""
  local beneficiary_grant_said=""
  local originator_ccid=""
  local beneficiary_ccid=""

  originator_le_said="$("$JQ" -r .leCredentialSaid "$RUN_DIR/originator-chain.json")"
  originator_oor_said="$("$JQ" -r .oorCredentialSaid "$RUN_DIR/originator-chain.json")"
  beneficiary_le_said="$("$JQ" -r .leCredentialSaid "$RUN_DIR/beneficiary-chain.json")"
  beneficiary_oor_said="$("$JQ" -r .oorCredentialSaid "$RUN_DIR/beneficiary-chain.json")"
  originator_ccid="$("$CAST" keccak "$ORIGINATOR_LE_AID")"
  beneficiary_ccid="$("$CAST" keccak "$BENEFICIARY_LE_AID")"

  if [[ -f "$RUN_DIR/originator/wallet-presentation.json" ]]; then
    originator_wallet_said="$("$JQ" -r .walletSaid "$RUN_DIR/originator/wallet-presentation.json")"
    originator_grant_said="$("$JQ" -r .grantSaid "$RUN_DIR/originator/wallet-presentation.json")"
  fi
  if [[ -f "$RUN_DIR/beneficiary/wallet-presentation.json" ]]; then
    beneficiary_wallet_said="$("$JQ" -r .walletSaid "$RUN_DIR/beneficiary/wallet-presentation.json")"
    beneficiary_grant_said="$("$JQ" -r .grantSaid "$RUN_DIR/beneficiary/wallet-presentation.json")"
  fi

  "$JQ" -n \
    --arg stage "$stage" \
    --arg runId "$RUN_ID" \
    --arg runDir "$RUN_DIR" \
    --arg keriBase "$KERI_BASE" \
    --arg rpcUrl "$RPC_URL" \
    --arg chainId "$CHAIN_ID" \
    --arg oracleUrl "$ORACLE_URL" \
    --arg contractsJson "$CONTRACTS_JSON" \
    --arg adminControls "$ADMIN_CONTROLS" \
    --arg identityRegistry "$IDENTITY_REGISTRY" \
    --arg credentialRegistry "$CREDENTIAL_REGISTRY" \
    --arg oracleRegistry "$ORACLE_REGISTRY" \
    --arg vleiToken "$VLEI_TOKEN" \
    --arg wrapper "$WRAPPER" \
    --arg adminPk "$ADMIN_PK" \
    --arg originatorPk "$ORIGINATOR_PK" \
    --arg beneficiaryPk "$BENEFICIARY_PK" \
    --arg originatorWallet "$ORIGINATOR_WALLET" \
    --arg beneficiaryWallet "$BENEFICIARY_WALLET" \
    --arg gedaAid "$GEDA_AID" \
    --arg qviAid "$QVI_AID" \
    --arg oracleAid "$ORACLE_AID" \
    --arg originatorLeAid "$ORIGINATOR_LE_AID" \
    --arg originatorOorAid "$ORIGINATOR_OOR_AID" \
    --arg beneficiaryLeAid "$BENEFICIARY_LE_AID" \
    --arg beneficiaryOorAid "$BENEFICIARY_OOR_AID" \
    --arg qviSaid "$QVI_SAID" \
    --arg originatorLeSaid "$originator_le_said" \
    --arg originatorOorSaid "$originator_oor_said" \
    --arg beneficiaryLeSaid "$beneficiary_le_said" \
    --arg beneficiaryOorSaid "$beneficiary_oor_said" \
    --arg originatorWalletSaid "$originator_wallet_said" \
    --arg beneficiaryWalletSaid "$beneficiary_wallet_said" \
    --arg originatorGrantSaid "$originator_grant_said" \
    --arg beneficiaryGrantSaid "$beneficiary_grant_said" \
    --arg originatorLei "$ORIGINATOR_LEI" \
    --arg beneficiaryLei "$BENEFICIARY_LEI" \
    --arg originatorCcid "$originator_ccid" \
    --arg beneficiaryCcid "$beneficiary_ccid" \
    '{
      stage:$stage,
      runId:$runId,
      runDir:$runDir,
      keriBase:$keriBase,
      rpcUrl:$rpcUrl,
      chainId:$chainId,
      oracleUrl:$oracleUrl,
      contractsJson:$contractsJson,
      contracts:{
        adminControls:$adminControls,
        identityRegistry:$identityRegistry,
        credentialRegistry:$credentialRegistry,
        oracleRegistry:$oracleRegistry,
        vleiToken:$vleiToken,
        wrapper:$wrapper
      },
      wallets:{
        adminPrivateKey:$adminPk,
        originatorPrivateKey:$originatorPk,
        beneficiaryPrivateKey:$beneficiaryPk,
        originator:$originatorWallet,
        beneficiary:$beneficiaryWallet
      },
      aids:{
        geda:$gedaAid,
        qvi:$qviAid,
        oracle:$oracleAid,
        originatorLegalEntity:$originatorLeAid,
        originatorOor:$originatorOorAid,
        beneficiaryLegalEntity:$beneficiaryLeAid,
        beneficiaryOor:$beneficiaryOorAid
      },
      credentials:{
        qvi:$qviSaid,
        originatorLegalEntity:$originatorLeSaid,
        originatorOor:$originatorOorSaid,
        beneficiaryLegalEntity:$beneficiaryLeSaid,
        beneficiaryOor:$beneficiaryOorSaid,
        originatorWallet:$originatorWalletSaid,
        beneficiaryWallet:$beneficiaryWalletSaid
      },
      grants:{
        originatorWallet:$originatorGrantSaid,
        beneficiaryWallet:$beneficiaryGrantSaid
      },
      leis:{
        originator:$originatorLei,
        beneficiary:$beneficiaryLei
      },
      ccids:{
        originator:$originatorCcid,
        beneficiary:$beneficiaryCcid
      }
    }' >"$ACTORS_JSON"

  cat >"$ACTORS_ENV" <<ENV
# Generated by gl1-wallet-transfer-e2e.sh.
export FIXTURE_STAGE='${stage}'
export RUN_ID='${RUN_ID}'
export RUN_DIR='${RUN_DIR}'
export KERI_BASE='${KERI_BASE}'
export RPC_URL='${RPC_URL}'
export CHAIN_ID='${CHAIN_ID}'
export ORACLE_URL='${ORACLE_URL}'
export CONTRACTS_JSON='${CONTRACTS_JSON}'
export ACTORS_JSON='${ACTORS_JSON}'
export ADMIN_PK='${ADMIN_PK}'
export ORIGINATOR_PK='${ORIGINATOR_PK}'
export BENEFICIARY_PK='${BENEFICIARY_PK}'
export ADMIN_CONTROLS='${ADMIN_CONTROLS}'
export IDENTITY_REGISTRY='${IDENTITY_REGISTRY}'
export CREDENTIAL_REGISTRY='${CREDENTIAL_REGISTRY}'
export ORACLE_REGISTRY='${ORACLE_REGISTRY}'
export VLEI_TOKEN='${VLEI_TOKEN}'
export WRAPPER='${WRAPPER}'
export ORIGINATOR_WALLET='${ORIGINATOR_WALLET}'
export BENEFICIARY_WALLET='${BENEFICIARY_WALLET}'
export GEDA_AID='${GEDA_AID}'
export QVI_AID='${QVI_AID}'
export ORACLE_AID='${ORACLE_AID}'
export ORIGINATOR_LE_AID='${ORIGINATOR_LE_AID}'
export ORIGINATOR_OOR_AID='${ORIGINATOR_OOR_AID}'
export BENEFICIARY_LE_AID='${BENEFICIARY_LE_AID}'
export BENEFICIARY_OOR_AID='${BENEFICIARY_OOR_AID}'
export QVI_CREDENTIAL_SAID='${QVI_SAID}'
export ORIGINATOR_LE_CREDENTIAL_SAID='${originator_le_said}'
export ORIGINATOR_OOR_CREDENTIAL_SAID='${originator_oor_said}'
export BENEFICIARY_LE_CREDENTIAL_SAID='${beneficiary_le_said}'
export BENEFICIARY_OOR_CREDENTIAL_SAID='${beneficiary_oor_said}'
export ORIGINATOR_WALLET_CREDENTIAL_SAID='${originator_wallet_said}'
export BENEFICIARY_WALLET_CREDENTIAL_SAID='${beneficiary_wallet_said}'
export ORIGINATOR_WALLET_GRANT_SAID='${originator_grant_said}'
export BENEFICIARY_WALLET_GRANT_SAID='${beneficiary_grant_said}'
export ORIGINATOR_LEI='${ORIGINATOR_LEI}'
export BENEFICIARY_LEI='${BENEFICIARY_LEI}'
export ORIGINATOR_CCID='${originator_ccid}'
export BENEFICIARY_CCID='${beneficiary_ccid}'
export TRANSFER_AMOUNT='${TRANSFER_AMOUNT}'
export SEED_AMOUNT='${SEED_AMOUNT}'
export GAS_FUND_AMOUNT='${GAS_FUND_AMOUNT}'
ENV

  log "Wrote actor fixture exports: ${ACTORS_ENV}"
  log "Wrote actor fixture JSON: ${ACTORS_JSON}"
}

# Orchestrate the local GL1 compliance scenario from fresh services through the
# final successful wrapper transfer.
main() {
  need "$KLI"
  need "$VLEI_ORACLE"
  need "$VLEI_SERVER"
  need "$FORGE"
  need "$CAST"
  need "$ANVIL"
  command -v curl >/dev/null
  command -v "$JQ" >/dev/null

  mkdir -p "$RUN_DIR/schema/acdc"
  log "Run directory: ${RUN_DIR}"

  # KLI issuers still need schema OOBIs to create and admit credentials. The
  # oracle service separately pins the GL1 wallet schema at startup and does
  # not rely on this schema server for its accepted-schema policy.
  cp -R "${VLEI_SCHEMA_TOOLS_DIR}/schema/acdc/." "$RUN_DIR/schema/acdc/"
  cp "${ORACLE_DIR}/schemas/acdc/gl1-ethereum-wallet-attestation.json" "$RUN_DIR/schema/acdc/"

  require_demo_witness_config wan "tcp://127.0.0.1:5632/" "http://127.0.0.1:5642/"
  require_demo_witness_config wil "tcp://127.0.0.1:5633/" "http://127.0.0.1:5643/"
  require_demo_witness_config wes "tcp://127.0.0.1:5634/" "http://127.0.0.1:5644/"
  reset_demo_witness_state
  start_service_if_needed 5642 "kli witness demo" "$RUN_DIR/kli-witness-demo.log" \
    bash -lc "cd '${KERIPY_DIR}' && exec '${KLI}' witness demo"
  require_http "http://127.0.0.1:5642/oobi/BBilc4-L3tFUnfM_wJr4S4OJanAv_VmF_dJNN6vkf2Ha/controller" \
    "kli witness demo"
  start_service_if_needed "$VLEI_SCHEMA_PORT" "vLEI schema server" "$RUN_DIR/vlei-server.log" \
    "$VLEI_SERVER" -p "$VLEI_SCHEMA_PORT" \
    -s "$RUN_DIR/schema/acdc" \
    -c "${VLEI_SCHEMA_TOOLS_DIR}/samples/acdc" \
    -o "${VLEI_SCHEMA_TOOLS_DIR}/samples/oobis"
  require_http "http://127.0.0.1:${VLEI_SCHEMA_PORT}/oobi/${WALLET_SCHEMA}" "vLEI schema server"
  write_kli_config
  start_service_if_needed "$ANVIL_PORT" "Anvil" "$RUN_DIR/anvil.log" \
    "$ANVIL" --host 127.0.0.1 --port "$ANVIL_PORT" --chain-id "$CHAIN_ID"

  log "Deploying vlei-comply"
  if [[ "${REDEPLOY:-0}" != "1" ]] && contracts_have_code; then
    log "Reusing deployed vlei-comply contracts: ${CONTRACTS_JSON}"
  else
    if [[ -f "$CONTRACTS_JSON" && "${REDEPLOY:-0}" != "1" ]]; then
      log "Ignoring stale contract addresses because no wrapper code exists on ${RPC_URL}"
    fi
    (
      cd "$COMPLY_DIR"
      # Foundry parallel broadcast can leave local Anvil runs polling receipts
      # indefinitely. --slow submits sequentially and makes the e2e deterministic.
      PRIVATE_KEY="$ADMIN_PK" "$FORGE" script script/DeployAll.s.sol --rpc-url "$RPC_URL" --broadcast --slow -vvvv
    ) | tee "$DEPLOY_LOG" >/dev/null

    "$VLEI_ORACLE" parse-deploy-log --log "$DEPLOY_LOG" --out "$CONTRACTS_JSON"
  fi

  ADMIN_CONTROLS="$("$JQ" -r .adminControls "$CONTRACTS_JSON")"
  IDENTITY_REGISTRY="$("$JQ" -r .identityRegistry "$CONTRACTS_JSON")"
  CREDENTIAL_REGISTRY="$("$JQ" -r .credentialRegistry "$CONTRACTS_JSON")"
  ORACLE_REGISTRY="$("$JQ" -r .oracleRegistry "$CONTRACTS_JSON")"
  VLEI_TOKEN="$("$JQ" -r .vleiToken "$CONTRACTS_JSON")"
  WRAPPER="$("$JQ" -r .wrapper "$CONTRACTS_JSON")"

  ORIGINATOR_WALLET="$("$CAST" wallet address --private-key "$ORIGINATOR_PK")"
  BENEFICIARY_WALLET="$("$CAST" wallet address --private-key "$BENEFICIARY_PK")"

  log "Incepting single-sig AIDs with one demo witness each"
  incept_actor geda geda 0ACDEyMzQ1Njc4OWxtbm9GhI BBilc4-L3tFUnfM_wJr4S4OJanAv_VmF_dJNN6vkf2Ha
  incept_actor qvi qvi 0ACDEyMzQ1Njc4OWxtbm9aBc BIKKuvBwpmDVA4Ds-EpL5bt9OqPzWPja2LigFYZN2YfX
  incept_actor originator-le originator-le 0ACDEyMzQ1Njc4OWxtbm9AbC BLskRTInXnMxWaGqcpSyMgo0nYbalW99cGZESrz3zapM
  incept_actor originator-oor originator-oor 0ACDEyMzQ1Njc4OWxtbm9dEf BBilc4-L3tFUnfM_wJr4S4OJanAv_VmF_dJNN6vkf2Ha
  incept_actor beneficiary-le beneficiary-le 0ACDEyMzQ1Njc4OWxtbm9HiJ BIKKuvBwpmDVA4Ds-EpL5bt9OqPzWPja2LigFYZN2YfX
  incept_actor beneficiary-oor beneficiary-oor 0ACDEyMzQ1Njc4OWxtbm9KlM BLskRTInXnMxWaGqcpSyMgo0nYbalW99cGZESrz3zapM
  # The oracle KLI database intentionally omits the GL1 wallet schema OOBI.
  # Successful verification therefore proves service-level schema pinning.
  incept_actor oracle oracle 0ACDEyMzQ1Njc4OWxtbm9NoP BBilc4-L3tFUnfM_wJr4S4OJanAv_VmF_dJNN6vkf2Ha gl1-demo-oobis-schema-oracle

  GEDA_AID="$(aid_for geda geda)"
  QVI_AID="$(aid_for qvi qvi)"
  ORIGINATOR_LE_AID="$(aid_for originator-le originator-le)"
  ORIGINATOR_OOR_AID="$(aid_for originator-oor originator-oor)"
  BENEFICIARY_LE_AID="$(aid_for beneficiary-le beneficiary-le)"
  BENEFICIARY_OOR_AID="$(aid_for beneficiary-oor beneficiary-oor)"
  ORACLE_AID="$(aid_for oracle oracle)"

  log "Resolving participant OOBIs and wallet schema OOBIs for KLI issuers"
  declare -a ACTOR_NAMES=(geda qvi originator-le originator-oor beneficiary-le beneficiary-oor oracle)
  declare -a ACTOR_ALIASES=(geda qvi originator-le originator-oor beneficiary-le beneficiary-oor oracle)
  declare -a ACTOR_WITS=(
    BBilc4-L3tFUnfM_wJr4S4OJanAv_VmF_dJNN6vkf2Ha
    BIKKuvBwpmDVA4Ds-EpL5bt9OqPzWPja2LigFYZN2YfX
    BLskRTInXnMxWaGqcpSyMgo0nYbalW99cGZESrz3zapM
    BBilc4-L3tFUnfM_wJr4S4OJanAv_VmF_dJNN6vkf2Ha
    BIKKuvBwpmDVA4Ds-EpL5bt9OqPzWPja2LigFYZN2YfX
    BLskRTInXnMxWaGqcpSyMgo0nYbalW99cGZESrz3zapM
    BBilc4-L3tFUnfM_wJr4S4OJanAv_VmF_dJNN6vkf2Ha
  )
  declare -a OOBIS=()
  for i in "${!ACTOR_NAMES[@]}"; do
    OOBIS[$i]="$(oobi_for "${ACTOR_NAMES[$i]}" "${ACTOR_ALIASES[$i]}" "${ACTOR_WITS[$i]}")"
  done
  for receiver_index in "${!ACTOR_NAMES[@]}"; do
    local receiver="${ACTOR_NAMES[$receiver_index]}"
    for subject_index in "${!ACTOR_NAMES[@]}"; do
      local subject="${ACTOR_NAMES[$subject_index]}"
      if [[ "$receiver" != "$subject" ]]; then
        resolve_oobi "$receiver" "${OOBIS[$subject_index]}" "$subject"
      fi
    done
    if [[ "$receiver" != "oracle" ]]; then
      resolve_schema_oobi "$receiver" "$WALLET_SCHEMA" gl1-ethereum-wallet-attestation
    fi
  done

  log "Creating credential registries"
  incept_registry geda geda vLEI-geda
  incept_registry qvi qvi vLEI-qvi
  incept_registry originator-le originator-le vLEI-originator-le
  incept_registry originator-oor originator-oor vLEI-originator-oor
  incept_registry beneficiary-le beneficiary-le vLEI-beneficiary-le
  incept_registry beneficiary-oor beneficiary-oor vLEI-beneficiary-oor

  log "Issuing GEDA -> QVI credential"
  write_json "$RUN_DIR/qvi-data.json" --arg LEI "$QVI_LEI" '{LEI:$LEI}'
  QVI_SAID="$(issue_and_grant geda geda vLEI-geda "$QVI_SCHEMA" "$QVI_AID" qvi qvi "$RUN_DIR/qvi-data.json")"

  log "Issuing originator LE/OOR chain"
  build_party_chain originator originator-le originator-le "$ORIGINATOR_LE_AID" \
    originator-oor originator-oor "$ORIGINATOR_OOR_AID" "$ORIGINATOR_LEI" \
    "Pat Originator" "Chief Compliance Officer"

  log "Issuing beneficiary LE/OOR chain"
  build_party_chain beneficiary beneficiary-le beneficiary-le "$BENEFICIARY_LE_AID" \
    beneficiary-oor beneficiary-oor "$BENEFICIARY_OOR_AID" "$BENEFICIARY_LEI" \
    "Bea Beneficiary" "Chief Compliance Officer"

  if [[ "$E2E_STOP_AFTER" == "source-chain" ]]; then
    write_actor_outputs source-chain
    log "Stopped after source-chain fixture setup"
    return
  fi

  if [[ "$E2E_STOP_AFTER" == "oracle-posted" ]]; then
    start_service_if_needed "$ORACLE_HTTP_PORT" "vLEI Oracle service" "$RUN_DIR/vlei-oracle.log" \
      "$VLEI_ORACLE" server start \
        --name oracle \
        --base "$KERI_BASE" \
        --alias oracle \
        --http-port "$ORACLE_HTTP_PORT" \
        --trusted-geda-aid "$GEDA_AID" \
        --contracts "$CONTRACTS_JSON" \
        --rpc-url "$RPC_URL" \
        --private-key "$ADMIN_PK"
    require_http "${ORACLE_URL}/health" "vLEI Oracle service"

    log "Issuing wallet attestations via standard KLI IPEX Grant to vLEI Oracle"
    issue_wallet_attestation originator "$ORIGINATOR_WALLET" "$ORIGINATOR_PK" originator-oor originator-oor
    issue_wallet_attestation beneficiary "$BENEFICIARY_WALLET" "$BENEFICIARY_PK" beneficiary-oor beneficiary-oor

    log "Presenting wallet attestations to vLEI Oracle and posting on-chain evidence"
    present_wallet_attestation originator "$ORIGINATOR_PK" originator-oor
    present_wallet_attestation beneficiary "$BENEFICIARY_PK" beneficiary-oor

    write_actor_outputs oracle-posted
    log "Stopped after oracle-posted fixture setup"
    return
  fi

  log "Funding participant gas wallets"
  fund_gas_wallet "$ORIGINATOR_WALLET"
  fund_gas_wallet "$BENEFICIARY_WALLET"

  log "Minting and approving seed funds"
  cast_send "$ADMIN_PK" "$VLEI_TOKEN" "mint(address,uint256)" "$ORIGINATOR_WALLET" "$SEED_AMOUNT"
  cast_send "$ORIGINATOR_PK" "$VLEI_TOKEN" "approve(address,uint256)" "$WRAPPER" "$SEED_AMOUNT"

  # Before wallet allowlisting, the wrapper should fail at the wallet policy
  # boundary without consulting oracle-posted AID evidence.
  expect_transfer_revert "before AdminControls wallet allowlist"

  log "Allowlisting wallets at AdminControls"
  cast_send "$ADMIN_PK" "$ADMIN_CONTROLS" "setAllowed(address,bool)" "$ORIGINATOR_WALLET" true
  cast_send "$ADMIN_PK" "$ADMIN_CONTROLS" "setAllowed(address,bool)" "$BENEFICIARY_WALLET" true

  # Wallet allowlisting alone is insufficient. The AID-keyed oracle registry
  # has not yet received verified GL1 evidence for either party.
  expect_transfer_bool false "before oracle posting"

  start_service_if_needed "$ORACLE_HTTP_PORT" "vLEI Oracle service" "$RUN_DIR/vlei-oracle.log" \
    "$VLEI_ORACLE" server start \
      --name oracle \
      --base "$KERI_BASE" \
      --alias oracle \
      --http-port "$ORACLE_HTTP_PORT" \
      --trusted-geda-aid "$GEDA_AID" \
      --contracts "$CONTRACTS_JSON" \
      --rpc-url "$RPC_URL" \
      --private-key "$ADMIN_PK"
  require_http "${ORACLE_URL}/health" "vLEI Oracle service"

  log "Issuing wallet attestations via standard KLI IPEX Grant to vLEI Oracle"
  issue_wallet_attestation originator "$ORIGINATOR_WALLET" "$ORIGINATOR_PK" originator-oor originator-oor
  issue_wallet_attestation beneficiary "$BENEFICIARY_WALLET" "$BENEFICIARY_PK" beneficiary-oor beneficiary-oor

  log "Presenting wallet attestations to vLEI Oracle and posting on-chain evidence"
  present_wallet_attestation originator "$ORIGINATOR_PK" originator-oor
  present_wallet_attestation beneficiary "$BENEFICIARY_PK" beneficiary-oor

  # The oracle posts evidence but does not make the admin allowlist decision.
  # That explicit policy step stays with the vLEI Oracle admin.
  expect_transfer_bool false "before AID allowlist"

  ORIGINATOR_CCID="$("$CAST" keccak "$(cat "$RUN_DIR/originator/legal-entity-aid.txt")")"
  BENEFICIARY_CCID="$("$CAST" keccak "$(cat "$RUN_DIR/beneficiary/legal-entity-aid.txt")")"
  write_actor_outputs oracle-posted

  log "Allowlisting AID CCIDs at VLEIOracleRegistry"
  cast_send "$ADMIN_PK" "$ORACLE_REGISTRY" "setAidAllowed(bytes32,bool)" "$ORIGINATOR_CCID" true
  cast_send "$ADMIN_PK" "$ORACLE_REGISTRY" "setAidAllowed(bytes32,bool)" "$BENEFICIARY_CCID" true

  # With both wallet and AID policies satisfied, a static transfer check should
  # pass before the script submits the real transfer transaction.
  expect_transfer_bool true "after oracle posting and allowlists"

  ORIGINATOR_BEFORE="$("$CAST" call --rpc-url "$RPC_URL" "$VLEI_TOKEN" "balanceOf(address)(uint256)" "$ORIGINATOR_WALLET")"
  BENEFICIARY_BEFORE="$("$CAST" call --rpc-url "$RPC_URL" "$VLEI_TOKEN" "balanceOf(address)(uint256)" "$BENEFICIARY_WALLET")"
  cast_send "$ORIGINATOR_PK" "$WRAPPER" "transfer(address,uint256)" "$BENEFICIARY_WALLET" "$TRANSFER_AMOUNT"
  ORIGINATOR_AFTER="$("$CAST" call --rpc-url "$RPC_URL" "$VLEI_TOKEN" "balanceOf(address)(uint256)" "$ORIGINATOR_WALLET")"
  BENEFICIARY_AFTER="$("$CAST" call --rpc-url "$RPC_URL" "$VLEI_TOKEN" "balanceOf(address)(uint256)" "$BENEFICIARY_WALLET")"

  "$JQ" -n \
    --arg runDir "$RUN_DIR" \
    --arg originatorBefore "$ORIGINATOR_BEFORE" \
    --arg originatorAfter "$ORIGINATOR_AFTER" \
    --arg beneficiaryBefore "$BENEFICIARY_BEFORE" \
    --arg beneficiaryAfter "$BENEFICIARY_AFTER" \
    --arg originatorCcid "$ORIGINATOR_CCID" \
    --arg beneficiaryCcid "$BENEFICIARY_CCID" \
    '{runDir:$runDir, originatorBefore:$originatorBefore, originatorAfter:$originatorAfter, beneficiaryBefore:$beneficiaryBefore, beneficiaryAfter:$beneficiaryAfter, originatorCcid:$originatorCcid, beneficiaryCcid:$beneficiaryCcid}' \
    >"$RUN_DIR/result.json"

  log "E2E complete. Result: ${RUN_DIR}/result.json"
}

main "$@"
