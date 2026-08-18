#!/usr/bin/env bash
# Record one image published under the same version tag in global and CN registries.
set -euo pipefail

[[ $# -eq 7 ]] || {
	printf 'Usage: %s COMPONENT GLOBAL_REF CN_REF DIGEST LOCAL_REF ROLE OUTPUT\n' "$0" >&2
	exit 2
}

component=$1
global_ref=$2
cn_ref=$3
digest=$4
local_ref=$5
role=$6
output=$7

[[ "${component}" =~ ^[a-z0-9][a-z0-9-]*$ ]]
[[ "${role}" =~ ^[a-z0-9][a-z0-9-]*$ ]]
[[ "${global_ref}" == */*:* && "${cn_ref}" == */*:* ]]
[[ "${local_ref}" =~ ^hyperfilelens-[a-z0-9-]+:[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]]
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
