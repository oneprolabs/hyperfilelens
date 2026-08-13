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

source_dir="${store_root}/${tag}"
# This version-scoped path is intentionally stable across attempts and workflow
# runs. Interrupted rsync partials remain useful until the release is retained.
incoming="${store_root}/.incoming/promotion-${version}"
archive="hyperfilelens-${version}-ee.tar.gz"
known_hosts="${hop}/prod-known-hosts"
store_script="${hop}/store-enterprise-release.sh"
ssh_target="${prod_user}@${prod_host}"
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
chmod 0600 "${known_hosts}"

exec 9<"${store_root}/.lock"
flock -s 9
(
	cd "${source_dir}"
	sha256sum -c SHA256SUMS
	[[ -f "${archive}" ]] || { printf 'ERROR: Enterprise archive is missing\n' >&2; exit 1; }
)
source_archive_digest="$(sha256sum "${source_dir}/${archive}" | awk '{print $1}')"
source_manifest_digest="$(sha256sum "${source_dir}/MANIFEST.json" | awk '{print $1}')"
[[ "${source_archive_digest}" =~ ^[0-9a-f]{64}$ \
	&& "${source_manifest_digest}" =~ ^[0-9a-f]{64}$ ]]

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
	printf 'Enterprise PROD transfer attempt %s/3\n' "${attempt}"
	if (
		cd "${source_dir}"
		rsync --archive --checksum --partial --partial-dir=.rsync-partial \
			--timeout=900 --info=progress2 --human-readable \
			-e "${rsync_shell}" \
			"${archive}" SHA256SUMS MANIFEST.json \
			"${ssh_target}:${incoming}/"
	) && ssh -n "${ssh_options[@]}" "${ssh_target}" \
		"cd '${incoming}' && sha256sum -c SHA256SUMS && test -f '${archive}' && rm -rf -- .rsync-partial"; then
		transfer_complete=true
		break
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
