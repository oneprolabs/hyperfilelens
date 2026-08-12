# HyperFileLens

HyperFileLens is a self-hosted data protection control plane for managing
backup sources, storage repositories, protection policies, restore operations,
and distributed backup agents from one web console.

> **Project status:** HyperFileLens is in public beta. Interfaces,
> configuration, and release packaging may change before a stable release.

The public source distribution is English-only. Additional languages are
designed to be delivered separately as runtime language packs.

## Features

- Central management for backup sources, repositories, policies, snapshots,
  and restore tasks
- Kopia-based Agent for Linux, macOS, and Windows workloads
- Source, proxy, and Data Gateway node roles
- REST API and WebSocket-based Agent orchestration
- Tenant, Platform Operations, and Django administration consoles
- Task history, audit records, alerts, notifications, and system monitoring
- Optional SourceLens integration for data discovery and insight workflows
- Hot-reload Docker Compose development environment
- Image-only, offline release packages for self-hosted deployment

## Architecture

| Component | Technology | Purpose |
| --- | --- | --- |
| Backend | Python 3.12, Django, DRF, Channels, Celery | API, authentication, orchestration, scheduling, and platform services |
| Frontend | Vue 3, TypeScript, Vite, Element Plus | Tenant and Platform Operations web consoles |
| Agent | Go 1.25, Kopia | Source discovery, backup execution, restore, and node communication |
| Gateway | Nginx | HTTPS entry point for the UI, API, WebSockets, and release downloads |
| State | PostgreSQL 17, Redis | Persistent application data, caching, messaging, and Channels |
| SourceLens | Integrated image-only component | Data discovery and insight services |

HyperFileLens currently supports PostgreSQL 17 as its only application
database. SQLite, MySQL, and MariaDB are not supported.

Nginx exposes the browser consoles and application endpoints over HTTPS.
Backend services communicate with Agents through WebSockets and use Celery for
asynchronous work. SourceLens runs as a separate image-only stack on the shared
`hyperfilelens-bridge` Docker network.

## Requirements

### Local development

- Linux amd64, macOS Intel, or macOS Apple Silicon development host
- Git
- Bash
- Python 3
- Go 1.25 with the Go 1.25.10 toolchain
- OpenSSL, curl, rsync, and SHA-256 utilities
- Docker Engine 24.0 or later
- Docker Compose v2.20 or later
- An amd64 host, or an environment capable of running linux/amd64 containers
- Internet access for the initial dependency, Agent, and SourceLens build

Backend and frontend application dependencies, Kopia, PostgreSQL, and Redis are
managed by the development workflow. Python and Go are used by repository
quality, dependency, and Agent build scripts on the host.

On macOS, HFL development containers run as `linux/amd64` through Docker
Desktop or Colima. This is a development-only configuration: macOS is not a
supported Release installation or Data Gateway host. Prepare the host tools
once with:

```bash
./dev/bootstrap-macos.sh
./dev/stack.sh doctor
```

Docker is deliberately not installed or upgraded by the bootstrap. On Apple
Silicon, Docker Desktop must have amd64 emulation enabled; Colima must be
started with an amd64-capable VM configuration.

### Release installation

The offline installer targets **Ubuntu 20.04/22.04/24.04 amd64**. The minimum
host size is 2 CPU cores and 4 GB of memory; 4 CPU cores and 8 GB are
recommended. CPU, memory, and Swap recommendations produce warnings rather
than blocking installation. Runtime containers use fixed, human-readable
resource ceilings across all environments. Data Gateways use the same
Ubuntu/amd64 support matrix. Existing
Docker Engine 24+ and Compose v2.20+ installations are used without being
upgraded or repaired; Docker is installed from the offline bundle only when it
is completely absent. Docker Buildx is used by Release CI but is not required
on installation or Data Gateway hosts.

Before an upgrade, the installer creates one checksummed backup set containing
logical PostgreSQL dumps, Redis persistence, configuration, and deployment
metadata. It retains the latest three valid sets. A backup can also be created
manually with `sudo ./install.sh backup`.

## Quick Start

Start the complete hot-reload development environment from the repository
root:

```bash
./dev/stack.sh up
```

On the first run, the script:

1. Creates `.env` from `.env.example` when it is missing.
2. Validates the repository-pinned default TLS certificates.
3. Builds the pinned, HyperFileLens-patched Kopia matrix and fetches other build dependencies.
4. Builds and publishes Agent packages under `data/media/`.
5. Builds and starts the image-only SourceLens stack.
6. Starts the bind-mounted HFL backend and frontend development services.

The initial run can take several minutes because it prepares all development
artifacts and container images.

Default endpoints:

| Service | URL |
| --- | --- |
| Tenant console | `https://localhost:11443/` |
| Platform Operations | `https://localhost:11444/` |
| Django Admin | `https://localhost:11444/admin/` |
| SourceLens console | `https://localhost:11445/` |
| OpenAPI UI | `https://localhost:11443/swagger` |

Default HFL development administrator:

```text
Email:    admin@hyperfilelens.com
Password: Admin@123
```

The default certificate is signed by the repository-pinned HyperFileLens root
CA. Trust `deploy/nginx/certs/root-ca.crt` on the client to remove browser
warnings for the covered local names. Change the default password after the
first login.

Common lifecycle commands:

```bash
./dev/stack.sh down
./dev/stack.sh down --hfl-only
./dev/stack.sh restart
./dev/stack.sh restart --force
./dev/stack.sh status
./dev/stack.sh doctor
./dev/stack.sh smoke
```

Use `restart --force` after changing dependency manifests or Dockerfiles.
Normal backend and frontend source changes reload automatically.
Unchanged dependency images, Agent packages, and the Gateway LensNode archive
are reused through content fingerprints and archive identity checks.

`down` is intentionally non-destructive: it preserves the shared bridge and
frontend modules volume for the next start. Explicit cleanup is available when
a genuinely clean runtime or data reset is required:

```bash
./dev/stack.sh clean --runtime
./dev/stack.sh clean --cache
./dev/stack.sh clean --data --yes
./dev/stack.sh clean --all --yes
```

The data forms require `--yes` because they delete local databases, logs, and
generated media. After one successful online preparation, a warm-cache stack
can be started or restarted without registry or Git access:

```bash
./dev/stack.sh up --offline
./dev/stack.sh restart --offline
```

Use `--pull` for an explicit runtime-image refresh. Docker pulls and SourceLens
Git operations have configurable finite timeouts and retry limits. The `smoke`
command uses a pinned Playwright version to verify Tenant, Platform Operations,
SourceLens login, and the development HMR WebSocket path.

To inspect options without starting services:

```bash
./dev/stack.sh up --print-config
./dev/stack.sh --help
```

## Configuration

Runtime and build settings are documented in [`.env.example`](.env.example).
The development stack automatically creates the ignored `.env` file from that
template. Release packages include the same template, while the installer
generates production secrets and host-specific values before startup.
When `.env` already exists, development startup and release upgrades append
missing template keys without overwriting existing values. Deprecated keys are
left unchanged and reported as ignored so operators can remove them manually.

Configuration precedence for supported build options is:

1. Command-line option
2. Process environment variable
3. Repository `.env`
4. `.env.example` default

Common settings include:

| Setting | Default | Purpose |
| --- | --- | --- |
| `HFL_WEBSITE_PORT` | `11442` | English product website HTTPS port |
| `HFL_TENANT_PORT` | `11443` | Tenant HTTPS console port |
| `HFL_ADMIN_PORT` | `11444` | Platform Operations and Django Admin port |
| `SOURCELENS_CONSOLE_PORT` | `11445` | SourceLens HTTPS console port |
| `SOURCELENS_MODE` | `bundled` | Use the bundled or an external SourceLens deployment |
| `HFL_EMAIL_SIGNUP_ENABLED` | `false` | Enable public email/password sign-up |
| `HFL_EMAIL_CODE_LOGIN_ENABLED` | `true` | Enable tenant email verification-code sign-in when SMTP is configured |
| `HFL_GOOGLE_OAUTH_ENABLED` | `false` | Enable Google OAuth sign-in when credentials are configured |
| `HFL_INSECURE_TLS` | `1` | Allow self-signed enrollment TLS; SaaS automation forces strict verification |
| `TURNSTILE_ENABLED` | `false` | Require configured Cloudflare Turnstile verification on public auth flows |
| `VITE_ENABLE_DEMO_DATA` | `false` | Enable development-only demo records |

Optional download and package mirrors can be set in `.env` or passed as CLI
options. For example:

```bash
./dev/stack.sh up \
  --github-download-mirror https://ghfast.top \
  --docker-download-mirror docker.m.daocloud.io \
  --apt-mirror https://mirrors.tuna.tsinghua.edu.cn \
  --go-proxy https://goproxy.cn,direct \
  --go-sumdb sum.golang.google.cn \
  --pip-index-url https://pypi.tuna.tsinghua.edu.cn/simple \
  --npm-registry https://registry.npmmirror.com
```

Third-party mirrors are examples only and are never enabled automatically.
For GitHub HTTPS repositories such as the default Kopia source, the selected
GitHub download mirror is also tried for Git clone and fetch before falling
back to the canonical GitHub URL. The canonical URL remains in the Git remote
and generated build metadata; `--kopia-git-url` is only needed for a different
upstream or fork.
The backend keeps `uv.lock` as the single dependency lock and uses the selected
Python index only to download hash-verified packages exported from that lock.

## SourceLens Integration

SourceLens is not vendored into this repository. Development and release
workflows fetch the configured upstream tag into the ignored
`build/sourcelens/source/` build cache and produce deployable images. Running
HFL environments always use SourceLens images; SourceLens source is never bind
mounted into runtime containers.

The default `bundled` mode manages SourceLens with HFL:

```env
SOURCELENS_MODE=bundled
```

To connect to an independently managed SourceLens deployment:

```env
SOURCELENS_MODE=external
LENS_BASE_URL=https://sourcelens.example.com
```

Skip SourceLens preparation for an HFL-only development session with:

```bash
./dev/stack.sh up --no-sourcelens
```

SourceLens is maintained as a separate upstream project at
[HyperBDR/sourcelens](https://github.com/HyperBDR/sourcelens).

## Development

The default Compose stack bind mounts:

- `src/backend/` to `/opt/backend`
- `src/frontend/` to `/app`

PostgreSQL, Redis, Nginx, and SourceLens remain containerized. Persistent local
state is written under the ignored `data/` directory. Generated dependencies,
build caches, and release output are written under the ignored `build/`
directory.

### Backend

The backend package and runtime dependencies are defined in `pyproject.toml`
and resolved reproducibly through the committed `uv.lock`. Backend images use
the lock file directly and fail the build if it is out of date.
After starting the development stack, run the Django test suite in a backend
container:

```bash
docker compose exec worker python manage.py test
```

### Frontend

```bash
docker compose exec web npm run lint
docker compose exec web npm run test
docker compose exec web npm run build
```

`package-lock.json` is generated from the official npm registry and committed
to keep CI and release builds reproducible.

### Agent

Run Agent tests:

```bash
cd src/agent
go test ./...
```

Build and package the default Agent platform matrix:

```bash
./src/agent/scripts/fetch-deps.sh --all
./src/agent/scripts/build.sh --release
./src/agent/scripts/package.sh
```

The default matrix is Linux amd64/arm64, macOS amd64/arm64, and Windows amd64.
Backend and Agent packaging consume the same verified binaries from
`build/kopia/dist/`; they do not resolve Kopia independently.

### Kopia artifacts and S3 compatibility

Kopia build defaults are committed in `tools/kopia/defaults.env`:

```dotenv
KOPIA_ARTIFACT_MODE="${KOPIA_ARTIFACT_MODE:-build}"
KOPIA_GIT_URL="${KOPIA_GIT_URL:-https://github.com/kopia/kopia.git}"
KOPIA_GIT_REF="${KOPIA_GIT_REF:-v0.23.1}"
```

The default `build` mode checks out the pinned ref in the ignored build cache,
applies the repository-owned S3 URL-style patch, and compiles the full platform
matrix. `download` mode fetches official upstream Release archives, verifies
`checksums.txt`, and is intended for diagnostics or upstream comparison.

```bash
./tools/kopia/prepare.sh
./tools/kopia/prepare.sh --kopia-mode download
./tools/kopia/prepare.sh --kopia-ref v0.23.1 --matrix "linux:amd64"
```

CLI options override environment variables, which override
`tools/kopia/defaults.env`. `KOPIA_INFO.json` records the resolved upstream
commit, Go toolchain, patch SHA256, capabilities, and per-platform binary
checksums. HyperFileLens supports AWS S3, Alibaba Cloud OSS, Huawei Cloud OBS,
and general S3-compatible storage. Huawei OBS defaults to virtual-hosted URLs;
AWS, Alibaba Cloud, and custom endpoints default to automatic URL selection.
Official `download` artifacts do not expose `--url-style`; HyperFileLens rejects
Huawei OBS and explicit virtual-hosted custom endpoints with a clear capability
error instead of silently ignoring the repository setting.
Run each script with `--help` for version, platform, mirror, logging, and output
options.

Publish Agent packages for the local control plane:

```bash
./tools/agent/publish.sh --bundle all
```

### Quality checks

The public repository requires English source, comments, and documentation:

```bash
python3 tools/quality/check-english-source.py
python3 -m unittest tools/quality/test_check_english_source.py
./tools/quality/check-release-contracts.sh
```

Run the relevant backend, frontend, and Agent tests before opening a pull
request.

## Release Packages

Build a complete offline package on a connected amd64 build host:

```bash
./release/build.sh
```

The generated archive is written to `build/release/dist/` and contains:

- HFL backend and frontend images
- PostgreSQL and Redis runtime images
- Optional bundled SourceLens images
- Agent installers and enrollment bootstrap files
- Ubuntu 20.04/22.04/24.04 amd64 Docker CE packages
- Repository-pinned default TLS certificates and root CA
- Runtime Compose, Nginx, installer, and license files

GitHub Releases also publish the same root certificate as the standalone
`hyperfilelens-root-ca.crt` asset for client trust-store installation.

Application source code is not copied into the release package. Installed
runtime files are placed under `/opt/hyperfilelens`, with persistent state
under `/opt/hyperfilelens/data`.

Community and Enterprise CI publishing, deployment, artifact retention, and
repository configuration are documented in
[`docs/release-workflows.md`](docs/release-workflows.md).

Install a generated package:

```bash
tar xzf hyperfilelens-<version>.tar.gz
cd hyperfilelens-<version>
sudo ./install.sh install
```

The installer creates a mode-`0600` `.env`, generates internal application and
database secrets, validates the packaged default TLS identity, loads the
packaged images, and starts the stack. Fresh installations use the fixed HFL
login `admin@hyperfilelens.com` / `Admin@123`; bundled SourceLens uses
`admin` / `adminpassword`. An upgrade preserves `.env`, an existing complete
TLS pair, and passwords already changed in either database.

Upgrade an existing installation:

```bash
sudo /opt/hyperfilelens/install.sh upgrade \
  --from /path/to/hyperfilelens-<version>.tar.gz
```

Inspect all build options from the repository and installer options from an
extracted release package with:

```bash
./release/build.sh --help
sudo ./install.sh --help
```

## Repository Layout

```text
hyperfilelens/
├── deploy/              Runtime Compose, Docker, Nginx, bootstrap, and installer assets
├── dev/                 Local development entry points
├── docs/                Reserved documentation directory
├── release/             Offline release build entry points
├── src/
│   ├── agent/           Go Agent source and packaging templates
│   ├── backend/         Django control plane source
│   └── frontend/        Vue web console source
├── tools/               Shared build, dependency, quality, and publishing tools
├── build/               Ignored build output and dependency caches
├── data/                Ignored local runtime state and published artifacts
├── .env.example         Documented configuration template
├── docker-compose.yml   Hot-reload development stack
├── pyproject.toml       Python project metadata and backend dependencies
├── LICENSE              Apache License 2.0
└── README.md
```

## Security

Default login credentials and the default TLS server private key are
intentionally public and convenient. Release installation generates random
internal application and database secrets, but operators must still:

- Trust `root-ca.crt` only where the shared default identity is acceptable, or
  atomically replace `tls.crt` and `tls.key` with a deployment-specific pair.
- Change the fixed default login passwords or enforce equivalent external access controls.
- Review listener bind addresses and restrict administrative access.
- Configure trusted hostnames, CSRF/CORS origins, email, and optional OAuth.
- Protect `.env`, TLS private keys, backups, logs, and `data/`.
- Keep deployment-specific credentials and generated runtime files out of version control.

Do not commit `.env`, deployment-specific private keys, access tokens, runtime
data, build output, or release archives. The only intentional private key in
the repository is the checksum-pinned default `deploy/nginx/certs/tls.key`;
the root CA private key is never committed.

## Contributing

1. Create a focused branch from the current default branch.
2. Keep source code, comments, documentation, commits, and pull requests in
   English.
3. Add or update tests for behavior changes.
4. Run the relevant quality checks and builds.
5. Open a pull request describing the problem, solution, and validation.

## License

HyperFileLens is licensed under the [Apache License 2.0](LICENSE).
