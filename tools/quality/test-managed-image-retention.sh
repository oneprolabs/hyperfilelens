#!/usr/bin/env bash
set -euo pipefail

ROOT_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
installer="${ROOT_REPO}/deploy/installer/install.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

ROOT="${tmp}/install"
mkdir -p "${ROOT}/backup/upgrade-20260727-010000" "${tmp}/bin"
printf 'HFL_GATEWAY_VERSION=0.1.5\n' >"${ROOT}/.env"
cat >"${ROOT}/MANIFEST.json" <<'JSON'
{"images":[{"refs":["hyperfilelens-backend:0.1.8","hyperfilelens-backend:latest"]}]}
JSON
cat >"${ROOT}/backup/upgrade-20260727-010000/MANIFEST.json" <<'JSON'
{"images":[{"refs":["hyperfilelens-backend:0.1.7"]}]}
JSON

cat >"${tmp}/bin/docker" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
case "$*" in
"info") ;;
"ps -aq --no-trunc") printf 'foreign-container\n' ;;
"inspect --format {{.Image}} foreign-container") printf 'sha256:in-use\n' ;;

"image ls -q --no-trunc")
	printf '%s\n' sha256:current sha256:previous sha256:in-use sha256:unused sha256:digest-old sha256:shared sha256:foreign-owned sha256:foreign
	;;
"image inspect hyperfilelens-backend:latest") printf '%s\n' '{"Id":"sha256:current","RepoTags":["hyperfilelens-backend:latest"],"RepoDigests":[]}' ;;
"image inspect hyperfilelens-backend:0.1.8") printf '%s\n' '{"Id":"sha256:current","RepoTags":["hyperfilelens-backend:0.1.8"],"RepoDigests":[]}' ;;
"image inspect hyperfilelens-backend:0.1.7") printf '%s\n' '{"Id":"sha256:previous","RepoTags":["hyperfilelens-backend:0.1.7"],"RepoDigests":[]}' ;;
"image inspect sha256:current") printf '%s\n' '{"Id":"sha256:current","RepoTags":["hyperfilelens-backend:0.1.8"],"RepoDigests":[]}' ;;
"image inspect sha256:previous") printf '%s\n' '{"Id":"sha256:previous","RepoTags":["hyperfilelens-backend:0.1.7"],"RepoDigests":[]}' ;;
"image inspect sha256:in-use") printf '%s\n' '{"Id":"sha256:in-use","RepoTags":["hyperfilelens-backend:0.1.6"],"RepoDigests":[]}' ;;
"image inspect sha256:unused") printf '%s\n' '{"Id":"sha256:unused","RepoTags":["hyperfilelens-backend:0.1.5"],"RepoDigests":[]}' ;;
"image inspect sha256:foreign") printf '%s\n' '{"Id":"sha256:foreign","RepoTags":["customer-backend:0.1.5"],"RepoDigests":[]}' ;;
"image inspect sha256:digest-old") printf '%s\n' '{"Id":"sha256:digest-old","RepoTags":[],"RepoDigests":["oneprolabs/hyperfilelens-backend@sha256:old"]}' ;;
"image inspect sha256:shared") printf '%s\n' '{"Id":"sha256:shared","RepoTags":["hyperfilelens-backend:0.1.4","customer-backend:0.1.4"],"RepoDigests":[]}' ;;
"image inspect sha256:foreign-owned") printf '%s\n' '{"Id":"sha256:foreign-owned","RepoTags":[],"RepoDigests":["customer/hyperfilelens-backend@sha256:foreign"]}' ;;
"image inspect sha256:unused --format {{.Id}}") printf 'sha256:unused\n' ;;
"image inspect sha256:in-use --format {{.Id}}") printf 'sha256:in-use\n' ;;
"image inspect unused --format {{.Id}}") printf 'sha256:unused\n' ;;
"image inspect in-use --format {{.Id}}") printf 'sha256:in-use\n' ;;
"image inspect digest-old --format {{.Id}}") printf 'sha256:digest-old\n' ;;
"image rm -f unused")
	printf '%s\n' 'sha256:unused' >>"${HFL_TEST_REMOVED_IMAGES}"
	;;
"image rm -f digest-old")
	printf '%s\n' 'sha256:digest-old' >>"${HFL_TEST_REMOVED_IMAGES}"
	;;
"image ls --format {{.Repository}}:{{.Tag}}")
	printf '%s\n' \
		hyperfilelens-backend:latest \
		hyperfilelens-backend:0.1.8 \
		hyperfilelens-backend:0.1.7 \
		hyperfilelens-backend:0.1.6 \
		hyperfilelens-backend:0.1.5 \
		hyperfilelens-backend:0.1.4 \
		hyperfilelens-frontend:0.1.5 \
		customer-backend:0.1.5
	;;
*) printf 'unexpected fake Docker invocation: %s\n' "$*" >&2; exit 1 ;;
esac
SH
chmod +x "${tmp}/bin/docker"
export PATH="${tmp}/bin:${PATH}"
export HFL_TEST_REMOVED_IMAGES="${tmp}/removed-images"

warn() { printf 'WARN: %s\n' "$*"; }
log() { printf 'INFO: %s\n' "$*"; }
source <(sed -n '/^managed_image_ref_is_in_use()/,/^apply_upgrade_files()/p' "${installer}" | sed '$d')

prune_old_managed_image_refs
[[ "$(<"${HFL_TEST_REMOVED_IMAGES}")" == $'sha256:digest-old\nsha256:unused' ]]

# A malformed protected manifest fails closed and must not remove anything.
printf '{invalid\n' >"${ROOT}/MANIFEST.json"
rm -f "${HFL_TEST_REMOVED_IMAGES}"
prune_old_managed_image_refs
[[ ! -e "${HFL_TEST_REMOVED_IMAGES}" ]]
printf 'Managed image retention checks passed.\n'
