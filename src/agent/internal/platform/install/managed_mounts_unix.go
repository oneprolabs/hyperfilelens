//go:build !windows

package install

// unixManagedMountCleanupScript is embedded in the detached Unix uninstaller.
// It only unmounts Agent-managed mounts below DATA_DIR/mounts. Gateway workspace
// mounts are detected separately and block purge rather than being unmounted.
const unixManagedMountCleanupScript = `
collect_gateway_workspace_mount_points() {
  local data_dir="$1" workspace_root="${1%/}/workspace" targets=""

  [[ -n "$data_dir" ]] || return 0
  if command -v findmnt >/dev/null 2>&1; then
    targets="$(LC_ALL=C findmnt -rn -o TARGET 2>/dev/null)" || targets=""
  fi
  if [[ -z "$targets" && -r /proc/mounts ]]; then
    targets="$(awk '{ print $2 }' /proc/mounts)"
  elif [[ -z "$targets" ]]; then
    return 1
  fi
  printf '%s\n' "$targets" | awk -v root="$workspace_root" '
    BEGIN { len = length(root) }
    length($0) >= len && substr($0, 1, len) == root &&
      (length($0) == len || substr($0, len + 1, 1) == "/") {
      print $0
    }
  '
}

collect_agent_mount_points() {
  local mounts_root="$1" targets=""

  [[ -n "$mounts_root" ]] || return 0
  if command -v findmnt >/dev/null 2>&1; then
    if targets="$(LC_ALL=C findmnt -rn -o TARGET 2>/dev/null)"; then
      printf '%s\n' "$targets" | awk -v root="$mounts_root" '
        BEGIN { len = length(root) }
        length($0) >= len && substr($0, 1, len) == root &&
          (length($0) == len || substr($0, len + 1, 1) == "/") {
          print $0
        }
      '
      return 0
    fi
  fi
  if [[ -r /proc/mounts ]]; then
    awk -v root="$mounts_root" '
      BEGIN { len = length(root) }
      length($2) >= len && substr($2, 1, len) == root &&
        (length($2) == len || substr($2, len + 1, 1) == "/") {
        print $2
      }
    ' /proc/mounts
  fi
}

sort_mount_points_deepest_first() {
  awk -F/ '{ print NF, $0 }' | sort -t' ' -k1,1rn | cut -d' ' -f2-
}

agent_mount_point_is_active() {
  local mounts_root="$1" point="$2"
  collect_agent_mount_points "$mounts_root" | grep -Fqx -- "$point"
}

run_managed_umount() {
  if command -v timeout >/dev/null 2>&1; then
    timeout 10 umount "$@"
  else
    umount "$@"
  fi
}

try_umount_point() {
  local mounts_root="$1" point="$2" msg=""

  if run_managed_umount "$point" 2>/dev/null && ! agent_mount_point_is_active "$mounts_root" "$point"; then
    log "unmounted $point"
    return 0
  fi
  if [[ "$(uname -s)" == "Linux" ]] && run_managed_umount -l "$point" 2>/dev/null && ! agent_mount_point_is_active "$mounts_root" "$point"; then
    log "lazy-unmounted $point"
    return 0
  fi
  if [[ "$(uname -s)" == "Linux" ]] && run_managed_umount -f "$point" 2>/dev/null && ! agent_mount_point_is_active "$mounts_root" "$point"; then
    log "force-unmounted $point"
    return 0
  fi
  msg="$(run_managed_umount "$point" 2>&1 || true)"
  log "failed to unmount $point${msg:+: $msg}"
  return 1
}

unmount_agent_mounts() {
  local data_dir="$1" mounts_root="${1%/}/mounts"
  local -a points=() remaining=()
  local point failed=0

  # macOS ships Bash 3.2, which does not provide mapfile/readarray.
  while IFS= read -r point; do
    [[ -n "$point" ]] && points+=("$point")
  done < <(
    collect_agent_mount_points "$mounts_root" | sort -u | sort_mount_points_deepest_first
  )
  if [[ ${#points[@]} -eq 0 ]]; then
    log "no active Agent-managed mounts under $mounts_root"
    return 0
  fi

  log "unmounting Agent-managed NAS shares under $mounts_root"
  for point in "${points[@]}"; do
    [[ -n "$point" ]] || continue
    try_umount_point "$mounts_root" "$point" || failed=1
  done

  while IFS= read -r point; do
    [[ -n "$point" ]] && remaining+=("$point")
  done < <(
    collect_agent_mount_points "$mounts_root" | sort -u | sort_mount_points_deepest_first
  )
  if [[ ${#remaining[@]} -gt 0 ]]; then
    for point in "${remaining[@]}"; do
      log "Agent-managed mount remains active: $point"
    done
    failed=1
  fi
  return "$failed"
}
`
