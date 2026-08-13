#!/usr/bin/env bash
set -euo pipefail

ROOT_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1090
source "${ROOT_REPO}/deploy/installer/install.sh"

tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
ROOT="${tmp}/install"
mkdir -p "${ROOT}/deploy" "${ROOT}/backup/upgrade-20260813-010000"
printf 'verified backup\n' >"${ROOT}/backup/upgrade-20260813-010000/data.dump"
backup_sha="$(sha256sum "${ROOT}/backup/upgrade-20260813-010000/data.dump" | awk '{print $1}')"
cat >"${ROOT}/backup/upgrade-20260813-010000/backup-manifest.json" <<EOF
{"complete":true,"files":[{"name":"data.dump","size":16,"sha256":"${backup_sha}"}]}
EOF

artifact_sha="$(printf b%.0s {1..64})"
UPGRADE_TARGET_VERSION=1.2.3
initialize_upgrade_transaction "${artifact_sha}" "${UPGRADE_TARGET_VERSION}"
[[ "${UPGRADE_TRANSACTION_INITIALIZED}" == 1 ]]
[[ "$(stat -c '%a' "${UPGRADE_TRANSACTION_DIR}/state")" == 600 ]]
[[ "$(read_upgrade_transaction_value status)" == active ]]
[[ "$(read_upgrade_transaction_value phase)" == validated ]]

record_upgrade_transaction_phase backup_complete \
	"${ROOT}/backup/upgrade-20260813-010000"
[[ "$(reusable_upgrade_backup)" == \
	"${ROOT}/backup/upgrade-20260813-010000" ]]
printf 'corrupt\n' >"${ROOT}/backup/upgrade-20260813-010000/data.dump"
if reusable_upgrade_backup >/dev/null 2>&1; then
	printf 'ERROR: corrupted transaction backup was reused\n' >&2
	exit 1
fi
printf 'verified backup\n' >"${ROOT}/backup/upgrade-20260813-010000/data.dump"

mkdir -p "${tmp}/outside-backup"
cp "${ROOT}/backup/upgrade-20260813-010000/backup-manifest.json" \
	"${tmp}/outside-backup/backup-manifest.json"
cp "${ROOT}/backup/upgrade-20260813-010000/data.dump" \
	"${tmp}/outside-backup/data.dump"
if (write_upgrade_transaction_state failed backup_complete \
	"${tmp}/outside-backup") >/dev/null 2>&1; then
	printf 'ERROR: transaction state accepted a backup outside the managed root\n' >&2
	exit 1
fi
record_upgrade_transaction_phase backup_complete \
	"${ROOT}/backup/upgrade-20260813-010000"

mark_upgrade_transaction_status failed
chmod 644 "${UPGRADE_TRANSACTION_DIR}/state"
initialize_upgrade_transaction "${artifact_sha}" "${UPGRADE_TARGET_VERSION}"
[[ "${UPGRADE_TRANSACTION_PHASE}" == backup_complete ]]
[[ "$(read_upgrade_transaction_value status)" == failed ]]
[[ "$(stat -c '%a' "${UPGRADE_TRANSACTION_DIR}/state")" == 600 ]]

write_upgrade_transaction_state complete complete \
	"${ROOT}/backup/upgrade-20260813-010000"
[[ "$(read_upgrade_transaction_value status)" == complete ]]
[[ "$(read_upgrade_transaction_value phase)" == complete ]]

printf 'status=unknown\n' >>"${UPGRADE_TRANSACTION_DIR}/state"
UPGRADE_TRANSACTION_INITIALIZED=0
if (initialize_upgrade_transaction "${artifact_sha}" \
	"${UPGRADE_TARGET_VERSION}") >/dev/null 2>&1; then
	printf 'ERROR: malformed upgrade transaction state passed validation\n' >&2
	exit 1
fi
[[ "${UPGRADE_TRANSACTION_INITIALIZED}" == 0 ]]

main_sha="$(printf c%.0s {1..64})"
UPGRADE_TARGET_VERSION=main-abcdef0
initialize_upgrade_transaction "${main_sha}" "${UPGRADE_TARGET_VERSION}"
initialize_upgrade_transaction "${main_sha}" "${UPGRADE_TARGET_VERSION}"
[[ "$(read_upgrade_transaction_value target_version)" == main-abcdef0 ]]

identity_root="${tmp}/identity-package"
mkdir -p "${identity_root}"
printf '1.2.3\n' >"${identity_root}/VERSION"
cat >"${identity_root}/MANIFEST.json" <<'JSON'
{"schema_version":2,"product":"hyperfilelens","edition":"community","image_version":"build-1","runtime_images":{"backend":"hyperfilelens-backend:build-1","frontend":"hyperfilelens-frontend:build-1"},"channel":"release","artifact_id":"v1.2.3","version":"1.2.3","git_commit":"0123456789abcdef0123456789abcdef01234567","images":[{"role":"sourcelens-app","refs":["hyperfilelens-backend:build-1","hyperfilelens-frontend:build-1"]}]}
JSON
if (validate_package_identity "${identity_root}") >/dev/null 2>&1; then
	printf 'ERROR: HFL runtime images were accepted from a non-HFL archive\n' >&2
	exit 1
fi
sed -i 's/"role":"sourcelens-app"/"role":"hyperfilelens"/' \
	"${identity_root}/MANIFEST.json"
validate_package_identity "${identity_root}"

package_root="${tmp}/package"
mkdir -p "${package_root}"
printf '%s\n' '{"minimum_upgrade_version":"0.1.34"}' \
	>"${package_root}/MANIFEST.json"
[[ "$(read_minimum_upgrade_version_from_dir "${package_root}")" == 0.1.34 ]]
version_lt 0.1.33 0.1.34
if version_lt 0.1.34 0.1.34; then
	printf 'ERROR: supported upgrade baseline was rejected\n' >&2
	exit 1
fi
upgrade_body="$(declare -f cmd_upgrade)"
grep -F 'create_managed_backup "${backup_stamp}" 1' <<<"${upgrade_body}" >/dev/null
channel_gate_line="$(grep -n -F 'main channel packages require --allow-main-build' \
	<<<"${upgrade_body}" | head -1 | cut -d: -f1)"
complete_shortcut_line="$(grep -n -F 'is already complete' \
	<<<"${upgrade_body}" | head -1 | cut -d: -f1)"
[[ -n "${channel_gate_line}" && -n "${complete_shortcut_line}" \
	&& "${channel_gate_line}" -lt "${complete_shortcut_line}" ]]

printf 'Upgrade transaction checks passed.\n'
