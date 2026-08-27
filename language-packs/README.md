# HyperFileLens language packs

HyperFileLens keeps English as its built-in and fallback locale. Official
translations live only below this directory, are built as data-only runtime
packages, and are bundled into Community and Enterprise offline releases.

Official bundled packs currently include Simplified Chinese (`zh-hans`) and
Spanish (`es`). Spanish regional browser tags such as `es-ES` and `es-MX`
resolve to the shared `es` catalog.

The application and language-pack versions are identical. For example, tag
`v0.2.0` produces `hyperfilelens-lang-zh-hans-0.2.0.tar.gz` with an exact
`==0.2.0` application compatibility contract.

## Build

From the repository root, after installing frontend dependencies:

```bash
./language-packs/tooling/build-all.sh --version 0.2.0
```

Generated files are written below `build/language-packs/` and are not tracked.
The development stack builds and installs bundled packs automatically, while
keeping English as the initial locale.

Runtime catalogs are stored below `data/lang-packs/versions/<app-version>/`.
Blue/green deployments therefore keep the previous and candidate application
versions bound to their matching catalogs until traffic switches.

The first release using version-scoped catalogs examines language packs from the
legacy flat `data/lang-packs/<pack-id>/` layout. Compatible packs are migrated
to the target version; incompatible packs remain untouched and the installer
asks the operator to install a version-matched package. Later independently
installed schema 2 packs must be installed separately for each application
version.

## Runtime management

Bundled packs are installed during a fresh installation. The complete bundled
collection is validated before any installed pack is replaced. Operators can
also manage packs independently. Uninstalling a pack records a persistent
disabled state so an upgrade or legacy-layout migration does not silently
reinstall it:

```bash
sudo ./install.sh lang-pack list
sudo ./install.sh lang-pack install --id zh-hans
sudo ./install.sh lang-pack install --file /path/to/language-pack.tar.gz
sudo ./install.sh lang-pack uninstall zh-hans
sudo ./install.sh lang-pack install --id es
sudo ./install.sh lang-pack uninstall es
```

English is built into the application and cannot be uninstalled.
