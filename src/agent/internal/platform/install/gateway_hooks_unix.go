//go:build !windows

package install

const unixGatewaySidecarUpgradeHook = `
run_gateway_sidecar_upgrade_if_needed() {
  # DATA_DIR is the persisted unified Agent Root. Detached runners also know
  # the root through INSTALL_SH (.../Agent/bin/install.sh), so macOS does not
  # accidentally fall back to the Linux /opt path.
  local agent_root="${DATA_DIR:-}"
  [[ -n "$agent_root" ]] || agent_root="${INSTALL_SH%/bin/install.sh}"
  [[ -n "$agent_root" && "$agent_root" != "$INSTALL_SH" ]] || agent_root="/opt/hyperfilelens-agent"
  local env_file="$agent_root/config/agent.env"
  local api_base org token node_id insecure bootstrap script tmp purge_flag curl_tls
  [[ -f "$env_file" ]] || return 0
  grep -q '^HFL_NODE_ROLE=gateway' "$env_file" || return 0
  api_base="$(grep -E '^HFL_API_BASE=' "$env_file" | head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^["'\'' ]//; s/["'\'' ]$//')"
  org="$(grep -E '^HFL_ORG_KEY=' "$env_file" | head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^["'\'' ]//; s/["'\'' ]$//')"
  token="$(grep -E '^HFL_NODE_CREDENTIAL=' "$env_file" | head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^["'\'' ]//; s/["'\'' ]$//')"
  [[ -n "$token" ]] || token="$(grep -E '^HFL_NODE_TOKEN=' "$env_file" | head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^["'\'' ]//; s/["'\'' ]$//')"
  node_id="$(grep -E '^HFL_NODE_ID=' "$env_file" | head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^["'\'' ]//; s/["'\'' ]$//')"
  insecure="$(grep -E '^HFL_INSECURE_TLS=' "$env_file" | head -1 | cut -d= -f2- | tr -d '\r' || true)"
  [[ -n "$api_base" && -n "$org" && -n "$token" && -n "$node_id" ]] || {
    log "WARN " "Gateway sidecar upgrade skipped (incomplete agent.env credentials)."
    return 0
  }
  curl_tls=()
  [[ "$insecure" != "0" ]] && curl_tls=(-k)
  script="${INSTALL_SH%/install.sh}/libexec/gateway-lifecycle.sh"
  tmp=""
  if [[ ! -x "$script" ]]; then
    bootstrap="${api_base%/}/media/gateway-bootstrap"
    tmp="$(mktemp -d)"
    script="${tmp}/gateway-lifecycle.sh"
    if ! curl "${curl_tls[@]}" -fsSL "${bootstrap}/gateway-lifecycle.sh" -o "$script"; then
      log "FAIL " "Failed to obtain gateway-lifecycle.sh for sidecar upgrade."
      rm -rf "$tmp"
      return 1
    fi
    chmod +x "$script"
  fi
  log "INFO " "Running gateway sidecar upgrade."
  HFL_AGENT_ENV_FILE="$env_file" HFL_INSECURE_TLS="${insecure:-1}" bash "$script" upgrade-sidecar
  local rc=$?
  [[ -z "$tmp" ]] || rm -rf "$tmp"
  return $rc
}
`

const unixGatewaySidecarUninstallHook = `
run_gateway_sidecar_uninstall_if_needed() {
  local env_file="$DATA_DIR/config/agent.env"
  local api_base org token node_id insecure bootstrap script tmp curl_tls purge_args
  [[ -f "$env_file" ]] || return 0
  grep -q '^HFL_NODE_ROLE=gateway' "$env_file" || return 0
  api_base="$(grep -E '^HFL_API_BASE=' "$env_file" | head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^["'\'' ]//; s/["'\'' ]$//')" || api_base=""
  org="$(grep -E '^HFL_ORG_KEY=' "$env_file" | head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^["'\'' ]//; s/["'\'' ]$//')" || org=""
  token="$(grep -E '^HFL_NODE_CREDENTIAL=' "$env_file" | head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^["'\'' ]//; s/["'\'' ]$//')" || token=""
  [[ -n "$token" ]] || token="$(grep -E '^HFL_NODE_TOKEN=' "$env_file" | head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^["'\'' ]//; s/["'\'' ]$//')" || token=""
  node_id="$(grep -E '^HFL_NODE_ID=' "$env_file" | head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^["'\'' ]//; s/["'\'' ]$//')" || node_id=""
  insecure="$(grep -E '^HFL_INSECURE_TLS=' "$env_file" | head -1 | cut -d= -f2- | tr -d '\r' || true)"
  curl_tls=()
  [[ "$insecure" != "0" ]] && curl_tls=(-k)
  script="$INSTALL_DIR/libexec/gateway-lifecycle.sh"
  tmp=""
  if [[ ! -x "$script" ]]; then
    [[ -n "$api_base" && -n "$org" && -n "$token" && -n "$node_id" ]] || {
      log "FAIL " "Gateway sidecar uninstall cannot continue because the local lifecycle script and download credentials are unavailable."
      return 1
    }
    bootstrap="${api_base%/}/media/gateway-bootstrap"
    tmp="$(mktemp -d)"
    script="${tmp}/gateway-lifecycle.sh"
    if ! curl "${curl_tls[@]}" -fsSL "${bootstrap}/gateway-lifecycle.sh" -o "$script"; then
      log "FAIL " "Failed to obtain gateway-lifecycle.sh; keeping the Agent installed for retry."
      rm -rf "$tmp"
      return 1
    fi
    chmod +x "$script"
  fi
  purge_args=()
  [[ "$KEEP_DATA" == "0" ]] && purge_args=(--purge-all)
  log "INFO " "Running gateway sidecar uninstall."
  HFL_AGENT_ENV_FILE="$env_file" HFL_INSECURE_TLS="${insecure:-1}" bash "$script" uninstall-sidecar "${purge_args[@]}"
  local rc=$?
  [[ -z "$tmp" ]] || rm -rf "$tmp"
  return $rc
}
`
