#!/usr/bin/env bash
# Resolve a supported Docker Compose command without changing the host.
# Callers use HFL_COMPOSE as an array: "${HFL_COMPOSE[@]}" ...

if [[ "${_HFL_COMPOSE_RUNTIME_LOADED:-0}" == "1" ]]; then
	return 0 2>/dev/null || exit 0
fi
_HFL_COMPOSE_RUNTIME_LOADED=1

HFL_COMPOSE=()
HFL_COMPOSE_VERSION=""
HFL_COMPOSE_SOURCE=""

hfl_compose_version_ge() {
	local have="${1#v}" want="${2#v}"
	have="${have#V}"
	want="${want#V}"
	[[ -n "${have}" && -n "${want}" ]] || return 1
	if command -v dpkg >/dev/null 2>&1; then
		dpkg --compare-versions "${have}" ge "${want}"
		return
	fi
	python3 - "${have}" "${want}" <<'PY'
import sys

def parse(value):
    parts = []
    for chunk in value.replace('-', '.').split('.'):
        digits = ''.join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits or 0))
    return tuple(parts)

sys.exit(0 if parse(sys.argv[1]) >= parse(sys.argv[2]) else 1)
PY
}

hfl_compose_candidate_version() {
	local -a candidate=("$@")
	local version output
	version="$("${candidate[@]}" version --short 2>/dev/null || true)"
	if [[ -z "${version}" ]]; then
		output="$("${candidate[@]}" version 2>/dev/null || true)"
		# Ubuntu commonly ships mawk, whose match() has no capture-array
		# argument.  grep -Eo keeps this parser portable across supported hosts.
		version="$(grep -Eo '[vV]?[0-9]+\.[0-9]+(\.[0-9]+)?' <<<"${output}" | head -1 || true)"
	fi
	version="${version#v}"
	version="${version#V}"
	printf '%s' "${version}"
}

hfl_compose_try_candidate() {
	local source=$1 min_version=$2
	shift 2
	local -a candidate=("$@")
	local version
	version="$(hfl_compose_candidate_version "${candidate[@]}")"
	[[ -n "${version}" ]] || return 1
	hfl_compose_version_ge "${version}" "${min_version}" || return 1
	HFL_COMPOSE=("${candidate[@]}")
	HFL_COMPOSE_VERSION="${version}"
	HFL_COMPOSE_SOURCE="${source}"
	return 0
}

hfl_compose_resolve() {
	local min_version="${1:-2.20.0}"
	if ((${#HFL_COMPOSE[@]})) \
		&& hfl_compose_version_ge "${HFL_COMPOSE_VERSION}" "${min_version}"; then
		return 0
	fi
	HFL_COMPOSE=()
	HFL_COMPOSE_VERSION=""
	HFL_COMPOSE_SOURCE=""

	if command -v docker >/dev/null 2>&1 \
		&& hfl_compose_try_candidate plugin "${min_version}" docker compose; then
		return 0
	fi
	if command -v docker-compose >/dev/null 2>&1 \
		&& hfl_compose_try_candidate standalone "${min_version}" docker-compose; then
		return 0
	fi
	return 1
}

hfl_compose_failure_detail() {
	local plugin="unavailable" standalone="unavailable"
	if command -v docker >/dev/null 2>&1; then
		plugin="$(hfl_compose_candidate_version docker compose)"
		[[ -n "${plugin}" ]] || plugin="unavailable"
	fi
	if command -v docker-compose >/dev/null 2>&1; then
		standalone="$(hfl_compose_candidate_version docker-compose)"
		[[ -n "${standalone}" ]] || standalone="unavailable"
	fi
	printf 'Compose plugin=%s, standalone=%s (required >= %s)' \
		"${plugin}" "${standalone}" "${1:-2.20.0}"
}
