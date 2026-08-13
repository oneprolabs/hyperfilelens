#!/usr/bin/env bash
# Exercise retry, partial retention, verification, and atomic retention without SSH.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
store="${tmp}/store"
remote_store="${tmp}/remote-store"
hop_root="${tmp}/hop"
hop="${hop_root}/hfl-prod-promotion-test"
source_dir="${store}/v1.2.3"
incoming="${store}/.incoming/promotion-1.2.3"
bin="${tmp}/bin"
mkdir -p "${source_dir}" "${remote_store}/.incoming" "${hop}" "${bin}"

printf 'enterprise archive payload\n' >"${source_dir}/hyperfilelens-1.2.3-ee.tar.gz"
printf '{"edition":"enterprise","version":"1.2.3"}\n' >"${source_dir}/MANIFEST.json"
(
	cd "${source_dir}"
	sha256sum hyperfilelens-1.2.3-ee.tar.gz MANIFEST.json >SHA256SUMS
)
printf 'test known host\n' >"${hop}/prod-known-hosts"
cat >"${hop}/store-enterprise-release.sh" <<'STORE'
#!/usr/bin/env bash
set -euo pipefail
tag=$1
incoming=$2
store=$3
(
	cd "${incoming}"
	sha256sum -c SHA256SUMS
)
mv "${incoming}" "${store}/${tag}"
STORE
chmod +x "${hop}/store-enterprise-release.sh"
touch "${store}/.lock"

cat >"${bin}/ssh" <<'SSH'
#!/usr/bin/env bash
set -euo pipefail
while (($#)); do
	case "$1" in
		-n) shift ;;
		-o|-p) shift 2 ;;
		*) shift; break ;;
	esac
done
command_text=${1:-}
[[ -n "${command_text}" ]]
command_text=${command_text//${HFL_PROMOTION_STORE_ROOT:?}/${HFL_FAKE_REMOTE_STORE:?}}
bash -c "${command_text}"
SSH

cat >"${bin}/rsync" <<'RSYNC'
#!/usr/bin/env bash
set -euo pipefail
count_file=${HFL_FAKE_RSYNC_COUNT:?}
count=0
[[ ! -f "${count_file}" ]] || read -r count <"${count_file}"
count=$((count + 1))
printf '%s\n' "${count}" >"${count_file}"
files=()
while (($#)); do
	case "$1" in
		-e) shift 2 ;;
		--*) shift ;;
		*) files+=("$1"); shift ;;
	esac
done
destination=${files[${#files[@]}-1]#*:}
destination=${destination//${HFL_PROMOTION_STORE_ROOT:?}/${HFL_FAKE_REMOTE_STORE:?}}
mkdir -p "${destination}/.rsync-partial"
if ((count == 1)); then
	head -c 8 "${files[0]}" >"${destination}/.rsync-partial/$(basename "${files[0]}")"
	exit 12
fi
[[ -s "${destination}/.rsync-partial/$(basename "${files[0]}")" ]] || {
	printf 'ERROR: retry did not retain the partial archive\n' >&2
	exit 1
}
for ((index = 0; index < ${#files[@]} - 1; index++)); do
	cp "${files[index]}" "${destination}/"
done
RSYNC
cat >"${bin}/sleep" <<'SLEEP'
#!/usr/bin/env bash
exit 0
SLEEP
chmod +x "${bin}/ssh" "${bin}/rsync" "${bin}/sleep"

PATH="${bin}:${PATH}" \
	HFL_FAKE_RSYNC_COUNT="${tmp}/rsync-count" \
	HFL_FAKE_REMOTE_STORE="${remote_store}" \
	HFL_PROMOTION_STORE_ROOT="${store}" \
	HFL_PROMOTION_HOP_ROOT="${hop_root}" \
	"${ROOT}/.github/scripts/promote-enterprise-release.sh" \
	v1.2.3 1.2.3 "${hop}" prod.example.test 22 root

[[ "$(<"${tmp}/rsync-count")" == 2 ]]
[[ -f "${remote_store}/v1.2.3/hyperfilelens-1.2.3-ee.tar.gz" ]]
[[ ! -e "${remote_store}/.incoming/promotion-1.2.3" ]]
(
	cd "${remote_store}/v1.2.3"
	sha256sum -c SHA256SUMS
)
printf 'Enterprise promotion retry checks passed.\n'
