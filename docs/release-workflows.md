# Release Workflows

HyperFileLens publishes Community and Enterprise from the OSS repository while
keeping their artifacts and deployment paths separate.

## Enterprise

Pushing an existing `vX.Y.Z` tag starts `HFL - Enterprise Build & Deploy`. The
same immutable tag must exist in both the OSS and private Enterprise
repositories. The quality gates load that exact Enterprise tag, so backend and
frontend extension tests are explicitly discovered and run together with the
OSS checks before any image is built. An Enterprise extension without backend
tests fails the gate. The workflow builds the HFL application images with the
`X.Y.Z-ee` tag, packages `hyperfilelens-X.Y.Z-ee.tar.gz`, verifies a complete
offline installation, and stores the result on the TEST host under
`/root/hfl-release/vX.Y.Z/`. It does not publish the final Enterprise package
in a GitHub Release. The TEST store retains the ten highest Enterprise
versions by semantic version.

Manual rebuilding uses the source and release tooling committed in the
selected tag. A tag created before the edition-aware release contract is
rejected up front; the workflow never combines historical product source with
newer packaging code under the old version number.

Rerunning an existing tag never overwrites its stored Enterprise package or
registry image tags. The workflow validates the retained package and confirms
that its OSS and Enterprise source commits still match both tags, then skips
the build and continues with TEST deployment and optional PROD promotion. If
either tag resolves to a different commit, or the retained package is damaged,
the workflow fails instead of silently reusing or replacing a different build.

TEST and PROD deployment are enabled unless `TEST_AUTO_DEPLOY` or
`PROD_AUTO_DEPLOY` is explicitly set to `false`. Automatic PROD promotion runs
only after TEST deployment succeeds in the same workflow run.

The package manifest and SHA-256 are the release identity; the archive filename
is only a human-facing convention. Deployment never derives runtime image tags
from the product version and never pulls a missing image from a registry. The
manifest supplies complete Backend and Frontend image references, and every
Compose start uses only verified offline image archives.

`HFL - Enterprise PROD Promotion` is the manual promotion path. It accepts a
valid Enterprise version already present on the TEST host, validates its
manifest and checksum, copies that package from TEST to PROD, and deploys it.
It is not controlled by `PROD_AUTO_DEPLOY` and does not require an additional
status marker. Package deployment still follows the installer's compatibility
rules; database rollback uses a verified managed backup rather than installing
an older package over newer data.

## Upgrade transactions

Each package SHA-256 owns one upgrade transaction under
`deploy/upgrades/<sha256>/`. The transaction records validation, image loading,
the managed backup, migration, HFL cutover, SourceLens convergence, Gateway
verification, and completion. Retrying the same package reuses its verified
backup and safely replays idempotent gates instead of creating another large
backup. Backups referenced by unfinished transactions are excluded from
retention cleanup. Reapplying an already-completed identical package returns
success without touching running services.

Release packages declare `minimum_upgrade_version` in `MANIFEST.json`, keeping
compatibility policy with the target release rather than hard-coding it in the
installed bootstrap. The current supported upgrade baseline is `0.1.34`.

## Community

`HFL - Community Release & Deploy` is started manually with an existing
`vX.Y.Z` OSS tag from the `main` branch. It always disables Enterprise
extension sources, packages `hyperfilelens-X.Y.Z.tar.gz`, and publishes the
verified assets as the formal GitHub Release. Community deployment is enabled unless
`COMMUNITY_AUTO_DEPLOY` is explicitly set to `false`.

New Community releases use the stable package name above. Deployment also
accepts one unambiguous legacy `hyperfilelens-X.Y.Z-SHA7.tar.gz` asset (or its
split parts), so an already-published historical tag remains deployable. The
stable name always takes precedence, and multiple legacy candidates are
rejected. A historical published release can be deployed without rebuilding;
an unpublished historical tag that predates the edition-aware build contract
cannot be repackaged by this workflow.

## Repository configuration

Configure these repository variables and secrets before enabling the new
workflows:

| Kind | Name | Purpose |
| --- | --- | --- |
| Variable | `ENTERPRISE_EXTENSION_REPOSITORY` | Credential-free HTTPS URL of the private Enterprise repository |
| Secret | `ENTERPRISE_EXTENSION_GIT_TOKEN` | Token with read access to the private Enterprise repository |
| Variable | `TEST_AUTO_DEPLOY` | Set to `false` to retain Enterprise packages without deploying TEST |
| Variable | `PROD_AUTO_DEPLOY` | Set to `false` to disable automatic PROD promotion |
| Variable | `COMMUNITY_AUTO_DEPLOY` | Set to `false` to publish Community without deploying it |
| Variable/secret prefix | `COMMUNITY_*` | Community host and runtime configuration, replacing `PREPROD_*` |

The existing `TEST_*` and `PROD_*` SSH and runtime configuration remains in
use. Rename old settings as follows:

| Previous name | New name |
| --- | --- |
| `HFL_EXTENSION_SOURCES` | `ENTERPRISE_EXTENSION_REPOSITORY` |
| `HFL_EXTENSION_GIT_TOKEN` | `ENTERPRISE_EXTENSION_GIT_TOKEN` |
| `TEST_DEPLOY_ENABLED` | `TEST_AUTO_DEPLOY` |
| `PROD_DEPLOY_ENABLED` | `PROD_AUTO_DEPLOY` |
| `PREPROD_*` | `COMMUNITY_*` |

During migration, the workflows prefer the new Enterprise and Community
secret names and fall back to the existing `HFL_EXTENSION_GIT_TOKEN` and
`PREPROD_*` secrets. Variables use only the new names. This allows credentials
to be rotated into the new names without exposing or copying existing values.

The three automatic-deploy variables deliberately use opt-out semantics:
missing or empty means enabled, and only the exact value `false` disables the
corresponding deployment.
