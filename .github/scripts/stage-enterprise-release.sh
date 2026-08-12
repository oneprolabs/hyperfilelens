#!/usr/bin/env bash
# Upload an Enterprise release candidate to the TEST host with bounded,
# retryable parallel transfers. The caller publishes the staged directory only
# after the downstream release verification succeeds.
set -euo pipefail

source_dir=${1:-}
incoming=${2:-}
parallel=${HFL_ENTERPRISE_TRANSFER_PARALLEL:-6}
attempts=${HFL_ENTERPRISE_TRANSFER_ATTEMPTS:-3}
transfer_timeout=${HFL_ENTERPRISE_TRANSFER_TIMEOUT:-20m}

[[ -d "${source_dir}" && ! -L "${source_dir}" ]] || {
	printf 'ERROR: Enterprise release source directory is invalid\n' >&2
	exit 2
}
[[ "${incoming}" =~ ^/root/hfl-release/\.incoming/[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || {
	printf 'ERROR: Enterprise incoming directory is invalid\n' >&2
	exit 2
}
[[ "${parallel}" =~ ^[1-8]$ ]] || { printf 'ERROR: invalid transfer parallelism\n' >&2; exit 2; }
[[ "${attempts}" =~ ^[1-5]$ ]] || { printf 'ERROR: invalid transfer attempts\n' >&2; exit 2; }
[[ "${transfer_timeout}" =~ ^[1-9][0-9]*[smh]$ ]] || {
	printf 'ERROR: invalid transfer timeout\n' >&2
	exit 2
}
[[ "${TEST_SSH_HOST:-}" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*$ \
	&& "${TEST_SSH_PORT:-}" =~ ^[0-9]+$ ]] || {
	printf 'ERROR: invalid TEST SSH endpoint\n' >&2
	exit 2
}
[[ "${TEST_SSH_USER:-}" =~ ^[a-z_][a-z0-9_-]*$ ]] || {
	printf 'ERROR: invalid TEST SSH user\n' >&2
	exit 2
}
[[ -n "${TEST_SSH_KNOWN_HOSTS:-}" && -n "${TEST_SSH_PRIVATE_KEY:-}" ]] || {
	printf 'ERROR: TEST SSH trust material is missing\n' >&2
	exit 2
}

(
	cd "${source_dir}"
	[[ -s SHA256SUMS && -s MANIFEST.json ]] || {
		printf 'ERROR: Enterprise release metadata is incomplete\n' >&2
		exit 1
	}
	sha256sum -c SHA256SUMS
	archive_count="$({
		find . -mindepth 1 -maxdepth 1 -type f \
			\( -name 'hyperfilelens-*-ee.tar.gz' \
			-o -name 'hyperfilelens-*-ee.tar.gz.part-000' \) -print
	} | wc -l)"
	if [[ "${archive_count}" -ne 1 ]]; then
		printf 'ERROR: Enterprise release archive is missing\n' >&2
		exit 1
	fi
	while IFS= read -r -d '' asset; do
		name=${asset#./}
		[[ "${name}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || {
			printf 'ERROR: unsafe Enterprise release asset name: %s\n' "${name}" >&2
			exit 1
		}
	done < <(find . -mindepth 1 -maxdepth 1 -type f -print0)
)

install -d -m 0700 ~/.ssh
printf '%s\n' "${TEST_SSH_KNOWN_HOSTS}" >~/.ssh/known_hosts
printf '%s\n' "${TEST_SSH_PRIVATE_KEY}" >~/.ssh/hyperfilelens_test
chmod 0600 ~/.ssh/known_hosts ~/.ssh/hyperfilelens_test

ssh_options=(
	-i ~/.ssh/hyperfilelens_test
	-o BatchMode=yes
	-o StrictHostKeyChecking=yes
	-o ConnectTimeout=30
	-o ServerAliveInterval=30
	-o ServerAliveCountMax=6
)
remote="${TEST_SSH_USER}@${TEST_SSH_HOST}"
ssh "${ssh_options[@]}" -p "${TEST_SSH_PORT}" "${remote}" \
	"install -d -m 0700 '${incoming}'"

export TEST_SSH_HOST TEST_SSH_PORT TEST_SSH_USER
export incoming attempts transfer_timeout
export HFL_ENTERPRISE_SSH_KEY=~/.ssh/hyperfilelens_test
upload_asset() {
	local asset=$1 attempt
	for ((attempt = 1; attempt <= attempts; attempt++)); do
		if timeout "${transfer_timeout}" scp \
			-i "${HFL_ENTERPRISE_SSH_KEY}" \
			-o BatchMode=yes \
			-o StrictHostKeyChecking=yes \
			-o ConnectTimeout=30 \
			-o ServerAliveInterval=30 \
			-o ServerAliveCountMax=6 \
			-o Compression=no \
			-P "${TEST_SSH_PORT}" "${asset}" \
			"${TEST_SSH_USER}@${TEST_SSH_HOST}:${incoming}/"; then
			return 0
		fi
		printf 'WARN: transfer attempt %d/%d failed for %s\n' \
			"${attempt}" "${attempts}" "$(basename "${asset}")" >&2
		((attempt == attempts)) || sleep $((attempt * 5))
	done
	return 1
}
export -f upload_asset

find "${source_dir}" -mindepth 1 -maxdepth 1 -type f -print0 \
	| xargs -0 -r -n 1 -P "${parallel}" bash -c 'upload_asset "$1"' _

# The remote checksum closes the transfer gate before verification downloads
# the staged candidate. Split parts are reconstructed only after verification.
ssh "${ssh_options[@]}" -p "${TEST_SSH_PORT}" "${remote}" \
	"cd '${incoming}' && sha256sum -c SHA256SUMS"
