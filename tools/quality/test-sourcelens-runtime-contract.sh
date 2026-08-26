#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
temporary="$(mktemp -d)"
trap 'rm -rf "${temporary}"' EXIT
repository="${temporary}/source"
git init --quiet "${repository}"
git -C "${repository}" config user.name "Runtime Contract Test"
git -C "${repository}" config user.email "runtime-contract@example.invalid"
printf 'fixture\n' >"${repository}/fixture.txt"
git -C "${repository}" add fixture.txt
git -C "${repository}" commit --quiet -m "Create fixture"
commit="$(git -C "${repository}" rev-parse HEAD)"
git -C "${repository}" tag v1.2.3
git -C "${repository}" tag -a v1.2.4 -m "Annotated fixture"
tag_object="$(git -C "${repository}" rev-parse v1.2.4)"
[[ "${tag_object}" != "${commit}" ]]

# A same-named branch must never shadow the release tag used by the contract.
git -C "${repository}" switch --quiet --detach "${commit}"
git -C "${repository}" switch --quiet -c v1.2.4
printf 'branch-only\n' >"${repository}/fixture.txt"
git -C "${repository}" add fixture.txt
git -C "${repository}" commit --quiet -m "Diverge same-named branch"
branch_commit="$(git -C "${repository}" rev-parse HEAD)"
[[ "${branch_commit}" != "${commit}" ]]
git -C "${repository}" switch --quiet --detach "${commit}"

lightweight_contract="${temporary}/lightweight.json"
annotated_contract="${temporary}/annotated.json"
"${ROOT}/tools/sourcelens/update-runtime-contract.sh" v1.2.3 \
	--git-url "${repository}" --output "${lightweight_contract}"
"${ROOT}/tools/sourcelens/update-runtime-contract.sh" v1.2.4 \
	--git-url "${repository}" --output "${annotated_contract}"
"${ROOT}/tools/sourcelens/update-runtime-contract.sh" --check \
	--git-url "${repository}" --output "${annotated_contract}"
python3 - "${lightweight_contract}" "${annotated_contract}" "${commit}" <<'PY'
import json
import pathlib
import sys

expected = sys.argv[3]
for name in sys.argv[1:3]:
    contract = json.loads(pathlib.Path(name).read_text(encoding="utf-8"))
    assert contract["git_commit"] == expected
PY

# shellcheck source=../sourcelens/common.sh
source "${ROOT}/tools/sourcelens/common.sh"
SOURCELENS_GIT_REF=v1.2.4
SOURCELENS_VERSION=1.2.4
SOURCELENS_RUNTIME_CONTRACT_FILE="${annotated_contract}"
sourcelens_verify_runtime_contract "${repository}"

branch_checkout="${temporary}/branch-checkout"
git clone --quiet "${repository}" "${branch_checkout}"
git -C "${branch_checkout}" checkout --quiet -b v1.2.4 "${branch_commit}"
if (sourcelens_verify_runtime_contract "${branch_checkout}") >/dev/null 2>&1; then
	printf 'ERROR: checkout validation accepted a same-named branch instead of the release tag\n' >&2
	exit 1
fi

python3 - "${annotated_contract}" "${tag_object}" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
contract = json.loads(path.read_text(encoding="utf-8"))
contract["git_commit"] = sys.argv[2]
path.write_text(json.dumps(contract, indent=2) + "\n", encoding="utf-8")
PY
if "${ROOT}/tools/sourcelens/update-runtime-contract.sh" --check \
	--git-url "${repository}" --output "${annotated_contract}" >/dev/null 2>&1; then
	printf 'ERROR: annotated tag object was accepted as the SourceLens source commit\n' >&2
	exit 1
fi

if (sourcelens_verify_runtime_contract "${repository}") >/dev/null 2>&1; then
	printf 'ERROR: checkout validation accepted an annotated tag object as the source commit\n' >&2
	exit 1
fi

printf 'SourceLens runtime contract checks passed.\n'
