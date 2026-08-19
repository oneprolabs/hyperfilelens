#!/usr/bin/env bash
# Reconcile one deployment-managed AI model without exposing its credentials.
set -euo pipefail

role=${1:-}
case "${role}" in
agent)
	provider=${AI_MODEL_PROVIDER:-}
	model_id=${AI_MODEL_ID:-}
	display_name=${AI_MODEL_DISPLAY_NAME:-}
	required=1
	label="AI model"
	;;
multimodal)
	provider=${AI_MULTIMODAL_MODEL_PROVIDER:-}
	model_id=${AI_MULTIMODAL_MODEL_ID:-}
	display_name=${AI_MULTIMODAL_MODEL_DISPLAY_NAME:-}
	required=0
	label="Multimodal model"
	;;
*)
	printf 'Usage: %s agent|multimodal\n' "$0" >&2
	exit 2
	;;
esac

summary() {
	[[ -z "${GITHUB_STEP_SUMMARY:-}" ]] || printf '### %s deployment\n\n%s\n' \
		"${label}" "$1" >>"${GITHUB_STEP_SUMMARY}"
}

warn_and_preserve() {
	printf '::warning title=%s configuration::%s; the installed value will be preserved.\n' \
		"${label}" "$1"
	summary "Warning: $1; the installed value was preserved."
	exit 0
}

if [[ -z "${provider}" ]]; then
	summary "Skipped: no deployment-managed ${label,,} is configured."
	exit 0
fi

values=(
	"${provider}" "${model_id}" "${display_name}"
	"${AI_MODEL_API_BASE:-}" "${AI_MODEL_API_KEY:-}"
)
valid=1
for value in "${values[@]}"; do
	[[ -n "${value}" && "${value}" != *$'\n'* && "${value}" != *$'\r'* ]] || valid=0
done
[[ "${provider}" =~ ^[a-z0-9_]+$ ]] || valid=0
[[ "${AI_MODEL_API_BASE:-}" == https://* ]] || valid=0

if ((valid == 0)); then
	if ((required == 1)); then
		printf '::error title=%s configuration::Configuration is incomplete or malformed.\n' \
			"${label}"
		summary "Failed: required configuration is incomplete or malformed."
		exit 1
	fi
	warn_and_preserve "Configuration is incomplete or malformed"
fi

payload="$(mktemp "${RUNNER_TEMP:-/tmp}/hyperfilelens-${role}-model.XXXXXX.json")"
trap 'rm -f -- "${payload}"' EXIT
chmod 0600 "${payload}"
ROLE="${role}" PROVIDER="${provider}" MODEL_ID="${model_id}" \
	DISPLAY_NAME="${display_name}" python3 - "${payload}" <<'PY'
import json
import os
import pathlib
import sys

payload = {
    "role": os.environ["ROLE"],
    "provider": os.environ["PROVIDER"],
    "model_id": os.environ["MODEL_ID"],
    "display_name": os.environ["DISPLAY_NAME"],
    "api_base": os.environ["AI_MODEL_API_BASE"],
    "api_key": os.environ["AI_MODEL_API_KEY"],
}
# SourceLens >= 0.39 requires multimodal models to declare vision capability
# before assistant creation (MODEL_NOT_VISION_CAPABLE).
if os.environ["ROLE"] == "multimodal":
    payload["supports_vision"] = True
pathlib.Path(sys.argv[1]).write_text(json.dumps(payload), encoding="utf-8")
PY

set +e
output="$(ssh -i ~/.ssh/hyperfilelens_saas \
	-o BatchMode=yes -o StrictHostKeyChecking=yes \
	-o ConnectTimeout=20 -o ServerAliveInterval=30 -o ServerAliveCountMax=20 \
	-o TCPKeepAlive=yes -p "${DEPLOY_SSH_PORT}" \
	"${DEPLOY_SSH_USER}@${DEPLOY_SSH_HOST}" \
	'/opt/hyperfilelens/install.sh manage ensure_platform_ai_model' \
	<"${payload}" 2>&1)"
command_status=$?
set -e
printf '%s\n' "${output}"

failure=""
if ((command_status != 0)); then
	failure="The configured model could not be created or updated"
elif grep -Fq 'HFL_AI_MODEL_APPLIED=false' <<<"${output}"; then
	failure="The candidate failed validation"
elif grep -Fq 'HFL_AI_MODEL_CONNECTIVITY=failed' <<<"${output}"; then
	failure="The configured model failed connectivity validation"
fi

if [[ -n "${failure}" ]]; then
	if ((required == 1)); then
		printf '::error title=%s deployment::%s.\n' "${label}" "${failure}"
		summary "Failed: ${failure}."
		exit 1
	fi
	warn_and_preserve "${failure}"
fi

summary "Passed: the deployment-managed ${label,,} was applied and verified."
