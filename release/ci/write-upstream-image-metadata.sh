#!/usr/bin/env bash
# Write immutable registry metadata for one upstream runtime image.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

[[ $# -eq 2 ]] || {
	printf 'Usage: %s COMPONENT OUTPUT\n' "$0" >&2
	exit 2
}

component=$1
output=$2
global_ref=""
cn_ref=""
local_ref=""
digest=""
role=""

case "${component}" in
sourcelens-backend | sourcelens-frontend | sourcelens-lensnode)
	name=${component#sourcelens-}
	readarray -t values < <(python3 - \
		"${ROOT}/deploy/online/sourcelens/runtime.json" "${name}" <<'PY'
import json
import pathlib
import sys

runtime = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
name = sys.argv[2]
image = (runtime.get("images") or {}).get(name) or {}
sources = image.get("sources") or {}
for value in (
    image.get("local_ref"),
    image.get("digest"),
    sources.get("global"),
    sources.get("cn"),
    runtime.get("version"),
    runtime.get("git_ref"),
    runtime.get("git_commit"),
):
    print(str(value or ""))
PY
	)
	[[ ${#values[@]} -eq 7 ]]
	local_ref=${values[0]}
	digest=${values[1]}
	global_ref=${values[2]}
	cn_ref=${values[3]}
	role=${component}
	;;
postgres | redis | sourcelens-nginx)
	# shellcheck source=../../tools/dependencies/versions/runtime-images.env
	source "${ROOT}/tools/dependencies/versions/runtime-images.env"
	case "${component}" in
	postgres) pinned=${POSTGRES_IMAGE}; role=shared ;;
	redis) pinned=${REDIS_IMAGE}; role=shared ;;
	sourcelens-nginx) pinned=${NGINX_IMAGE}; role=sourcelens-nginx ;;
	esac
	local_ref=${pinned%@*}
	digest=${pinned##*@}
	global_ref="docker.io/library/${local_ref}"
	cn_ref="dockerproxy.net/library/${local_ref}"
	;;
*)
	printf 'ERROR: unsupported upstream image component: %s\n' "${component}" >&2
	exit 2
	;;
esac

[[ "${component}" =~ ^[a-z0-9][a-z0-9-]*$ ]]
[[ "${role}" =~ ^[a-z0-9][a-z0-9-]*$ ]]
[[ "${global_ref}" == */*:* && "${cn_ref}" == */*:* ]]
[[ "${local_ref}" =~ ^[a-z0-9][a-z0-9._/-]*:[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ \
	&& "${local_ref}" != *//* && "${local_ref}" != *..* ]]
[[ "${digest}" =~ ^sha256:[0-9a-f]{64}$ ]]

mkdir -p "$(dirname "${output}")"
jq -n \
	--arg component "${component}" \
	--arg role "${role}" \
	--arg local_ref "${local_ref}" \
	--arg digest "${digest}" \
	--arg global_ref "${global_ref}" \
	--arg cn_ref "${cn_ref}" \
	'{
	  component: $component,
	  role: $role,
	  local_ref: $local_ref,
	  digest: $digest,
	  platform: "linux/amd64",
	  sources: [
	    {region: "cn", ref: $cn_ref},
	    {region: "global", ref: $global_ref}
	  ]
	}' >"${output}"

if [[ "${component}" == "sourcelens-backend" \
	|| "${component}" == "sourcelens-frontend" \
	|| "${component}" == "sourcelens-lensnode" ]]; then
	temporary="${output}.tmp"
	jq \
		--arg version "${values[4]}" \
		--arg git_ref "${values[5]}" \
		--arg git_commit "${values[6]}" \
		'. + {
		  sourcelens_version: $version,
		  sourcelens_git_ref: $git_ref,
		  sourcelens_git_commit: $git_commit
		}' "${output}" >"${temporary}"
	mv "${temporary}" "${output}"
fi
