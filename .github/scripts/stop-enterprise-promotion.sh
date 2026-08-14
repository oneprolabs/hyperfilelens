#!/usr/bin/env bash
# Stop one verified TEST-side promotion process group and remove its hop files.
set -euo pipefail

expected_hop=${1:-}
hop_root=${HFL_PROMOTION_HOP_ROOT:-/var/tmp}
store_root=${HFL_PROMOTION_STORE_ROOT:-/root/hfl-release}
for root in "${hop_root}" "${store_root}"; do
	[[ "${root}" =~ ^/[A-Za-z0-9._/-]+$ \
		&& "${root}" != *..* && "${root}" != */ ]] || {
		printf 'ERROR: invalid promotion cleanup root path\n' >&2
		exit 2
	}
done
active_state="${hop_root}/hfl-prod-promotion.active"
snapshot_root="${store_root}/.promotion-snapshots"

valid_hop() {
	local candidate=$1
	local name=${candidate#"${hop_root}/"}
	[[ "${candidate}" == "${hop_root}/"* \
		&& "${name}" =~ ^hfl-prod-promotion-[A-Za-z0-9._-]+$ ]]
}

valid_snapshot() {
	local candidate=$1
	local name=${candidate#"${snapshot_root}/"}
	[[ "${candidate}" == "${snapshot_root}/"* \
		&& "${name}" =~ ^hfl-prod-promotion-[A-Za-z0-9._-]+$ ]]
}

if [[ "${expected_hop}" != "--stale" ]]; then
	valid_hop "${expected_hop}" || {
		printf 'ERROR: invalid promotion cleanup workspace\n' >&2
		exit 2
	}
fi

state_pid= state_pgid= state_hop= state_snapshot=
if [[ -e "${active_state}" ]]; then
	[[ -f "${active_state}" && ! -L "${active_state}" ]] || {
		printf 'ERROR: invalid promotion process state\n' >&2
		exit 1
	}
	read -r state_pid state_pgid state_hop state_snapshot \
		<"${active_state}" || true
	[[ "${state_pid}" =~ ^[1-9][0-9]*$ \
		&& "${state_pgid}" =~ ^[1-9][0-9]*$ ]] || {
		printf 'ERROR: invalid promotion process identity\n' >&2
		exit 1
	}
	valid_hop "${state_hop}" || {
		printf 'ERROR: invalid promotion state workspace\n' >&2
		exit 1
	}
	valid_snapshot "${state_snapshot}" || {
		printf 'ERROR: invalid promotion source snapshot state\n' >&2
		exit 1
	}

	if [[ "${expected_hop}" == "--stale" || "${expected_hop}" == "${state_hop}" ]]; then
		if kill -0 "${state_pid}" 2>/dev/null; then
			current_pgid="$(ps -o pgid= -p "${state_pid}" | tr -d '[:space:]')"
			current_sid="$(ps -o sid= -p "${state_pid}" | tr -d '[:space:]')"
			cmdline="$(tr '\0' ' ' <"/proc/${state_pid}/cmdline")"
			[[ "${state_pid}" == "${state_pgid}" \
				&& "${current_pgid}" == "${state_pgid}" \
				&& "${current_sid}" == "${state_pid}" \
				&& "${cmdline}" == *"${state_hop}/promote-enterprise-release.sh"* ]] || {
				printf 'ERROR: refusing to stop an unverified process group\n' >&2
				exit 1
			}
			printf 'Stopping orphaned Enterprise promotion process group %s\n' \
				"${state_pgid}"
			kill -TERM -- "-${state_pgid}"
			for _ in {1..30}; do
				kill -0 -- "-${state_pgid}" 2>/dev/null || break
				sleep 1
			done
			if kill -0 -- "-${state_pgid}" 2>/dev/null; then
				kill -KILL -- "-${state_pgid}"
			fi
		fi
		if [[ -e "${state_snapshot}" ]]; then
			[[ -d "${state_snapshot}" && ! -L "${state_snapshot}" ]] || {
				printf 'ERROR: invalid promotion source snapshot\n' >&2
				exit 1
			}
			rm -rf -- "${state_snapshot}"
		fi
		rm -f -- "${active_state}"
		rm -rf -- "${state_hop}"
	fi
fi

if [[ "${expected_hop}" != "--stale" ]]; then
	expected_name=${expected_hop#"${hop_root}/"}
	expected_snapshot="${snapshot_root}/${expected_name}"
	if [[ -e "${expected_snapshot}" ]]; then
		[[ -d "${expected_snapshot}" && ! -L "${expected_snapshot}" ]] || {
			printf 'ERROR: invalid expected promotion source snapshot\n' >&2
			exit 1
		}
		rm -rf -- "${expected_snapshot}"
	fi
	rm -rf -- "${expected_hop}"
fi
rmdir -- "${snapshot_root}" 2>/dev/null || true
