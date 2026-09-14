#!/usr/bin/env bash
# Validate retryable Main assets and monotonic cleanup under stale or concurrent runs.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

git init --bare --quiet "${tmp}/remote.git"
git init --quiet "${tmp}/seed"
git -C "${tmp}/seed" config user.name 'HyperFileLens CI'
git -C "${tmp}/seed" config user.email 'ci@hyperfilelens.invalid'
printf 'first\n' >"${tmp}/seed/state.txt"
git -C "${tmp}/seed" add state.txt
git -C "${tmp}/seed" commit --quiet -m first
git -C "${tmp}/seed" branch -M main
first_commit="$(git -C "${tmp}/seed" rev-parse HEAD)"
printf 'second\n' >>"${tmp}/seed/state.txt"
git -C "${tmp}/seed" add state.txt
git -C "${tmp}/seed" commit --quiet -m second
second_commit="$(git -C "${tmp}/seed" rev-parse HEAD)"
git -C "${tmp}/seed" remote add origin "${tmp}/remote.git"
git -C "${tmp}/seed" push --quiet -u origin main
git --git-dir="${tmp}/remote.git" symbolic-ref HEAD refs/heads/main
git -C "${tmp}/seed" switch --quiet -c divergent "${first_commit}"
printf 'divergent\n' >>"${tmp}/seed/state.txt"
git -C "${tmp}/seed" add state.txt
git -C "${tmp}/seed" commit --quiet -m divergent
divergent_commit="$(git -C "${tmp}/seed" rev-parse HEAD)"
git -C "${tmp}/seed" push --quiet origin divergent
git clone --quiet "${tmp}/remote.git" "${tmp}/work"

first_artifact="main-${first_commit:0:7}"
second_artifact="main-${second_commit:0:7}"
divergent_artifact="main-${divergent_commit:0:7}"

mkdir -p "${tmp}/bin"
cat >"${tmp}/bin/gh" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
printf '%q ' "$@" >>"${GH_MOCK_LOG}"
printf '\n' >>"${GH_MOCK_LOG}"

if [[ "${1:-}" == "release" && "${2:-}" == "view" ]]; then
	exit 0
fi
if [[ "${1:-}" == "release" && "${2:-}" == "delete" ]]; then
	exit 0
fi
if [[ "${1:-}" == "api" && "${2:-}" == *"releases?per_page=100" ]]; then
	printf '%b\n' "${GH_MOCK_RELEASES:-}"
	exit 0
fi
if [[ "${1:-}" == "api" && "${2:-}" == *"/compare/"* ]]; then
	[[ "${GH_MOCK_COMPARE_FAIL:-0}" != "1" ]] || exit 1
	pair="${2##*/compare/}"
	base="${pair%%...*}"
	head="${pair#*...}"
	if [[ "${base}" == "${head}" ]]; then
		printf 'identical\n'
	elif git -C "${GH_MOCK_GIT_REPO}" merge-base --is-ancestor "${base}" "${head}"; then
		printf 'ahead\n'
	elif git -C "${GH_MOCK_GIT_REPO}" merge-base --is-ancestor "${head}" "${base}"; then
		printf 'behind\n'
	else
		printf 'diverged\n'
	fi
	exit 0
fi
if [[ "${1:-}" == "api" ]]; then
	exit 0
fi
printf 'unexpected gh invocation: %s\n' "$*" >&2
exit 2
MOCK
chmod +x "${tmp}/bin/gh"

run_cleanup() {
	local artifact_id=$1 main_commit=$2 build_required=$3 publish_result=$4
	local releases=$5 disposition=${6:-incomplete} compare_fail=${7:-0}
	: >"${tmp}/gh.log"
	: >"${tmp}/summary.md"
	if ! (
		cd "${tmp}/work"
		PATH="${tmp}/bin:${PATH}" \
			GH_MOCK_LOG="${tmp}/gh.log" \
			GH_MOCK_RELEASES="${releases}" \
			GH_MOCK_GIT_REPO="${tmp}/seed" \
			GH_MOCK_COMPARE_FAIL="${compare_fail}" \
			GITHUB_REPOSITORY=oneprolabs/hyperfilelens \
			GITHUB_STEP_SUMMARY="${tmp}/summary.md" \
			ARTIFACT_ID="${artifact_id}" \
			MAIN_COMMIT="${main_commit}" \
			BUILD_REQUIRED="${build_required}" \
			PUBLISH_RESULT="${publish_result}" \
			PUBLISH_DISPOSITION="${disposition}" \
			"${ROOT}/.github/scripts/cleanup-main-builds.sh"
	) >"${tmp}/cleanup-output.log" 2>&1; then
		# Prevent an expected fixture annotation from becoming a real Actions
		# annotation while still preserving readable diagnostics on test failure.
		sed 's/^::/%3A%3A/' "${tmp}/cleanup-output.log" >&2
		return 1
	fi
}

release_rows="$(printf '%s\t%s\n%s\t%s\n' \
	"${second_artifact}" "${second_commit}" \
	"${first_artifact}" "${first_commit}")"

run_cleanup "${first_artifact}" "${first_commit}" true skipped "${release_rows}"
if [[ -s "${tmp}/gh.log" ]]; then
	printf 'ERROR: failed Main publish invoked GitHub cleanup APIs\n' >&2
	exit 1
fi
grep -F "Retained retryable draft \`${first_artifact}\`" "${tmp}/summary.md" >/dev/null

run_cleanup "${second_artifact}" "${second_commit}" true success "${release_rows}" published
grep -Fq "release delete ${first_artifact}" "${tmp}/gh.log"
grep -Fq "git/refs/tags/${first_artifact}" "${tmp}/gh.log"
if grep -Fq "release delete ${second_artifact}" "${tmp}/gh.log"; then
	printf 'ERROR: successful Main cleanup deleted the retained release\n' >&2
	exit 1
fi

run_cleanup "${first_artifact}" "${first_commit}" false skipped "${release_rows}"
if [[ -s "${tmp}/gh.log" ]]; then
	printf 'ERROR: stale reused Main run invoked GitHub cleanup APIs\n' >&2
	exit 1
fi
grep -F 'authoritative newer Main release remains unchanged' "${tmp}/summary.md" >/dev/null

run_cleanup "${first_artifact}" "${first_commit}" true success "${release_rows}" superseded
if [[ -s "${tmp}/gh.log" ]]; then
	printf 'ERROR: superseded Main cleanup changed the authoritative newer release\n' >&2
	exit 1
fi

mixed_rows="$(printf '%s\t%s\n%s\t%s\n%s\t%s\n%s\t%s\n' \
	"${second_artifact}" "${second_commit}" \
	"${first_artifact}" "${first_commit}" \
	"${divergent_artifact}" "${divergent_commit}" \
	'main-fffffff' 'main')"
run_cleanup "${second_artifact}" "${second_commit}" true success "${mixed_rows}" published
grep -Fq "release delete ${first_artifact}" "${tmp}/gh.log"
if grep -Fq "release delete ${divergent_artifact}" "${tmp}/gh.log" \
	|| grep -Fq 'release delete main-fffffff' "${tmp}/gh.log"; then
	printf 'ERROR: cleanup deleted a divergent or unresolved Main release\n' >&2
	exit 1
fi
grep -F 'preserved 2 non-ancestor or unresolved release(s)' "${tmp}/summary.md" >/dev/null

run_cleanup "${second_artifact}" "${second_commit}" true success "${release_rows}" published 1
if grep -Fq "release delete ${first_artifact}" "${tmp}/gh.log"; then
	printf 'ERROR: cleanup deleted a release after ancestry verification failed\n' >&2
	exit 1
fi
grep -F 'preserved 1 non-ancestor or unresolved release(s)' "${tmp}/summary.md" >/dev/null

git -C "${tmp}/work" remote set-url origin "${tmp}/missing.git"
run_cleanup "${second_artifact}" "${second_commit}" true success "${release_rows}" published
if [[ -s "${tmp}/gh.log" ]]; then
	printf 'ERROR: cleanup invoked GitHub APIs after Main freshness verification failed\n' >&2
	exit 1
fi
grep -F 'current Main freshness could not be verified' "${tmp}/summary.md" >/dev/null
grep -F '::warning title=Main release cleanup::' \
	"${tmp}/cleanup-output.log" >/dev/null
git -C "${tmp}/work" remote set-url origin "${tmp}/remote.git"

if ARTIFACT_ID=invalid \
	MAIN_COMMIT="${second_commit}" \
	BUILD_REQUIRED=true \
	PUBLISH_RESULT=success \
	GITHUB_REPOSITORY=oneprolabs/hyperfilelens \
	"${ROOT}/.github/scripts/cleanup-main-builds.sh" >/dev/null 2>&1; then
	printf 'ERROR: invalid Main artifact identifier was accepted\n' >&2
	exit 1
fi

printf 'Main release cleanup retry and concurrency contracts passed.\n'
