#!/usr/bin/env bash
# Copy one verified Enterprise release from TEST to PROD and retain it atomically.
set -euo pipefail

tag=${1:-}
version=${2:-}
hop=${3:-}
prod_host=${4:-}
prod_port=${5:-}
prod_user=${6:-}
store_root=${HFL_PROMOTION_STORE_ROOT:-/root/hfl-release}
hop_root=${HFL_PROMOTION_HOP_ROOT:-/var/tmp}
attempt_timeout_seconds=${HFL_PROMOTION_ATTEMPT_TIMEOUT_SECONDS:-1800}
io_timeout_seconds=${HFL_PROMOTION_IO_TIMEOUT_SECONDS:-120}

for root in "${store_root}" "${hop_root}"; do
	[[ "${root}" =~ ^/[A-Za-z0-9._/-]+$ \
		&& "${root}" != *..* && "${root}" != */ ]] || {
		printf 'ERROR: invalid promotion root path\n' >&2
		exit 2
	}
done
[[ "${tag}" == "v${version}" && "${version}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || {
	printf 'ERROR: invalid Enterprise release identity\n' >&2
	exit 2
}
hop_name=${hop#"${hop_root}/"}
[[ "${hop}" == "${hop_root}/"* \
	&& "${hop_name}" =~ ^hfl-prod-promotion-[A-Za-z0-9._-]+$ ]] || {
	printf 'ERROR: invalid promotion workspace\n' >&2
	exit 2
}
[[ "${prod_host}" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*$ && "${prod_port}" =~ ^[0-9]+$ ]] || {
	printf 'ERROR: invalid PROD SSH endpoint\n' >&2
	exit 2
}
[[ "${prod_user}" =~ ^[a-z_][a-z0-9_-]*$ ]] || {
	printf 'ERROR: invalid PROD SSH user\n' >&2
	exit 2
}
[[ "${attempt_timeout_seconds}" =~ ^[1-9][0-9]*$ \
	&& "${io_timeout_seconds}" =~ ^[1-9][0-9]*$ ]] || {
	printf 'ERROR: invalid promotion timeout\n' >&2
	exit 2
}

source_dir="${store_root}/${tag}"
snapshot_root="${store_root}/.promotion-snapshots"
snapshot="${snapshot_root}/${hop_name}"
# This version-scoped path is intentionally stable across attempts and workflow
# runs. Interrupted rsync partials remain useful until the release is retained.
incoming="${store_root}/.incoming/promotion-${version}"
archive="hyperfilelens-${version}-ee.tar.gz"
known_hosts="${hop}/prod-known-hosts"
store_script="${hop}/store-enterprise-release.sh"
ssh_target="${prod_user}@${prod_host}"
active_state="${hop_root}/hfl-prod-promotion.active"
active_lock="${hop_root}/hfl-prod-promotion.lock"
ssh_options=(
	-o BatchMode=yes
	-o StrictHostKeyChecking=yes
	-o "UserKnownHostsFile=${known_hosts}"
	-o ConnectTimeout=30
	-o ServerAliveInterval=30
	-o ServerAliveCountMax=20
	-o TCPKeepAlive=yes
	-p "${prod_port}"
)

[[ -d "${source_dir}" && ! -L "${source_dir}" ]] || {
	printf 'ERROR: Enterprise release is missing on TEST: %s\n' "${tag}" >&2
	exit 1
}
[[ -s "${known_hosts}" && -s "${store_script}" ]] || {
	printf 'ERROR: promotion support files are incomplete\n' >&2
	exit 1
}
command -v rsync >/dev/null || { printf 'ERROR: rsync is required on TEST\n' >&2; exit 1; }
command -v timeout >/dev/null || { printf 'ERROR: timeout is required on TEST\n' >&2; exit 1; }
chmod 0600 "${known_hosts}"

# Promotion runs in its own session. The stable state allows the workflow
# cleanup step (or the next serialized run) to stop an orphaned transfer
# without matching unrelated SSH or rsync processes.
umask 077
if [[ -e "${active_lock}" ]]; then
	[[ -f "${active_lock}" && ! -L "${active_lock}" ]] || {
		printf 'ERROR: invalid promotion process lock\n' >&2
		exit 1
	}
else
	: >"${active_lock}"
fi
exec 8>"${active_lock}"
flock -n 8 || {
	printf 'ERROR: another Enterprise PROD transfer is still active on TEST\n' >&2
	exit 1
}
promotion_pid=$$
promotion_pgid="$(ps -o pgid= -p "${promotion_pid}" | tr -d '[:space:]')"
promotion_sid="$(ps -o sid= -p "${promotion_pid}" | tr -d '[:space:]')"
[[ "${promotion_pgid}" == "${promotion_pid}" \
	&& "${promotion_sid}" == "${promotion_pid}" ]] || {
	printf 'ERROR: promotion must run in an isolated process session\n' >&2
	exit 1
}
state_tmp="${active_state}.${promotion_pid}"
printf '%s\t%s\t%s\t%s\n' \
	"${promotion_pid}" "${promotion_pgid}" "${hop}" "${snapshot}" \
	>"${state_tmp}"
mv -f -- "${state_tmp}" "${active_state}"
cleanup_process_state() {
	local state_pid= state_pgid= state_hop= state_snapshot=
	if [[ -e "${snapshot}" ]]; then
		[[ -d "${snapshot}" && ! -L "${snapshot}" ]] || {
			printf 'ERROR: invalid promotion source snapshot\n' >&2
			return
		}
		rm -rf -- "${snapshot}"
	fi
	rmdir -- "${snapshot_root}" 2>/dev/null || true
	if [[ -f "${active_state}" && ! -L "${active_state}" ]]; then
		read -r state_pid state_pgid state_hop state_snapshot \
			<"${active_state}" || true
		if [[ "${state_pid}" == "${promotion_pid}" \
			&& "${state_pgid}" == "${promotion_pgid}" \
			&& "${state_hop}" == "${hop}" \
			&& "${state_snapshot}" == "${snapshot}" ]]; then
			rm -f -- "${active_state}"
		fi
	fi
	rm -f -- "${state_tmp}"
}
trap cleanup_process_state EXIT

exec 9<"${store_root}/.lock"
flock -s 9
(
	cd "${source_dir}"
	sha256sum -c SHA256SUMS
	[[ -f "${archive}" ]] || { printf 'ERROR: Enterprise archive is missing\n' >&2; exit 1; }
)
if [[ -e "${snapshot_root}" ]]; then
	[[ -d "${snapshot_root}" && ! -L "${snapshot_root}" ]] || {
		printf 'ERROR: invalid promotion snapshot root\n' >&2
		exit 1
	}
else
	install -d -m 0700 "${snapshot_root}"
fi
[[ ! -e "${snapshot}" ]] || {
	printf 'ERROR: promotion source snapshot already exists\n' >&2
	exit 1
}
install -d -m 0700 "${snapshot}"
ln -- \
	"${source_dir}/${archive}" \
	"${source_dir}/SHA256SUMS" \
	"${source_dir}/MANIFEST.json" \
	"${snapshot}/"
source_archive_digest="$(sha256sum "${snapshot}/${archive}" | awk '{print $1}')"
source_manifest_digest="$(sha256sum "${snapshot}/MANIFEST.json" | awk '{print $1}')"
[[ "${source_archive_digest}" =~ ^[0-9a-f]{64}$ \
	&& "${source_manifest_digest}" =~ ^[0-9a-f]{64}$ ]]
flock -u 9
exec 9<&-

# Exact retries must not resend an already retained immutable release.
if ssh -n "${ssh_options[@]}" "${ssh_target}" \
	"test -d '${store_root}/${tag}' && cd '${store_root}/${tag}' && sha256sum -c SHA256SUMS && test \"\$(sha256sum '${archive}' | awk '{print \$1}')\" = '${source_archive_digest}' && test \"\$(sha256sum MANIFEST.json | awk '{print \$1}')\" = '${source_manifest_digest}'"; then
	printf 'Enterprise version %s is already retained on PROD\n' "${tag}"
	exit 0
fi

ssh -n "${ssh_options[@]}" "${ssh_target}" \
	"command -v rsync >/dev/null && if [ -e '${incoming}' ]; then [ -d '${incoming}' ] && [ ! -L '${incoming}' ]; else install -d -m 0700 '${incoming}'; fi"

printf -v rsync_shell 'ssh'
for option in "${ssh_options[@]}"; do
	printf -v rsync_shell '%s %q' "${rsync_shell}" "${option}"
done

transfer_complete=false
for attempt in 1 2 3; do
	printf 'Enterprise PROD transfer attempt %s/3 (maximum %ss)\n' \
		"${attempt}" "${attempt_timeout_seconds}"
	transfer_status=0
	(
		cd "${snapshot}"
		timeout --signal=TERM --kill-after=30s "${attempt_timeout_seconds}s" \
			rsync --archive --checksum --partial --partial-dir=.rsync-partial \
			--timeout="${io_timeout_seconds}" --outbuf=L \
			--info=progress2 --human-readable \
			-e "${rsync_shell}" \
			"${archive}" SHA256SUMS MANIFEST.json \
			"${ssh_target}:${incoming}/"
	) || transfer_status=$?
	if ((transfer_status == 0)) && ssh -n "${ssh_options[@]}" "${ssh_target}" \
		"cd '${incoming}' && sha256sum -c SHA256SUMS && test -f '${archive}' && rm -rf -- .rsync-partial"; then
		transfer_complete=true
		break
	fi
	if ((transfer_status == 124 || transfer_status == 137)); then
		printf 'WARN: Enterprise PROD transfer attempt %s exceeded %ss; restarting with the retained partial data\n' \
			"${attempt}" "${attempt_timeout_seconds}" >&2
	fi
	printf 'WARN: Enterprise PROD transfer attempt %s failed; retaining partial data\n' \
		"${attempt}" >&2
	sleep $((attempt * 10))
done
[[ "${transfer_complete}" == true ]] || {
	printf 'ERROR: Enterprise PROD transfer failed after 3 attempts\n' >&2
	exit 1
}

ssh "${ssh_options[@]}" "${ssh_target}" \
	"bash -s -- '${tag}' '${incoming}' '${store_root}' 0" <"${store_script}"

# The deploy job may start only after the immutable PROD copy independently
# satisfies the retained checksum contract.
ssh -n "${ssh_options[@]}" "${ssh_target}" \
	"cd '${store_root}/${tag}' && sha256sum -c SHA256SUMS && test -f '${archive}'"
