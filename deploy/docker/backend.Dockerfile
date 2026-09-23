# Application runtime on Ubuntu LTS. Dependencies use a virtual environment to avoid system Python conflicts (PEP 668).
ARG BACKEND_BASE_IMAGE=ubuntu:24.04
FROM ${BACKEND_BASE_IMAGE} AS backend-dependencies

LABEL org.opencontainers.image.base.name="ubuntu:24.04" \
    org.opencontainers.image.title="hyperfilelens-backend"

ARG APT_MIRROR
ARG PIP_INDEX_URL
ARG PIP_TRUSTED_HOST
ARG PIP_TIMEOUT=600
ARG KOPIA_BINARY=build/kopia/dist/linux/amd64/kopia

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=${PIP_TIMEOUT} \
    TZ=UTC \
    VIRTUAL_ENV=/opt/venv \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    HFL_KOPIA_PATH=/usr/local/bin/kopia \
    PATH="/opt/venv/bin:${PATH}"

RUN if [ -n "${APT_MIRROR}" ]; then \
      apt-get update && \
      apt-get install -y --no-install-recommends ca-certificates && \
      sed -i "s|http://archive.ubuntu.com/ubuntu/|${APT_MIRROR%/}/ubuntu/|g" /etc/apt/sources.list && \
      sed -i "s|http://security.ubuntu.com/ubuntu/|${APT_MIRROR%/}/ubuntu/|g" /etc/apt/sources.list ; \
      if [ -f /etc/apt/sources.list.d/ubuntu.sources ]; then \
        sed -i "s|http://archive.ubuntu.com/ubuntu/|${APT_MIRROR%/}/ubuntu/|g; s|http://security.ubuntu.com/ubuntu/|${APT_MIRROR%/}/ubuntu/|g" \
          /etc/apt/sources.list.d/ubuntu.sources ; \
      fi ; \
    fi \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    procps \
    python3 \
    python3-pip \
    python3-setuptools \
    python3-wheel \
    python3-venv \
 && rm -rf /var/lib/apt/lists/* \
 && python3 -m venv /opt/venv

COPY --chmod=0755 ${KOPIA_BINARY} /usr/local/bin/kopia
RUN kopia --version

WORKDIR /opt/hyperfilelens/backend

COPY pyproject.toml uv.lock /tmp/backend-project/
RUN --mount=type=cache,target=/root/.cache/pip \
    if [ -n "${PIP_INDEX_URL}" ]; then \
      { \
        echo "[global]"; \
        echo "index-url = ${PIP_INDEX_URL}"; \
        if [ -n "${PIP_TRUSTED_HOST}" ]; then echo "trusted-host = ${PIP_TRUSTED_HOST}"; fi; \
      } > /etc/pip.conf ; \
    fi \
 && pip --version \
 && PIP_CACHE_DIR=/root/.cache/pip pip install --retries 15 --timeout "${PIP_TIMEOUT}" "uv==0.10.2" \
 && cd /tmp/backend-project \
 && uv export --quiet --locked --no-dev --no-emit-project --output-file /tmp/runtime-requirements.txt \
 && PIP_CACHE_DIR=/root/.cache/pip pip install --retries 15 --timeout "${PIP_TIMEOUT}" --require-hashes -r /tmp/runtime-requirements.txt \
 && uv export --quiet --locked --only-group dev --no-emit-project --output-file /tmp/dev-requirements.txt \
 && pip uninstall -y uv \
 && rm -rf /tmp/backend-project /tmp/runtime-requirements.txt

# Local development keeps dependencies in the image and bind-mounts src/backend.
# watchfiles restarts API, worker, and scheduler processes after Python changes.
FROM backend-dependencies AS backend-development

RUN --mount=type=cache,target=/root/.cache/pip \
    PIP_CACHE_DIR=/root/.cache/pip pip install --retries 15 --timeout "${PIP_TIMEOUT}" --require-hashes -r /tmp/dev-requirements.txt \
 && rm -f /tmp/dev-requirements.txt

COPY deploy/bootstrap /opt/hyperfilelens/bootstrap
COPY deploy/docker/backend-entrypoint.sh /entrypoint.sh
COPY deploy/docker/dev-process-supervisor.py /dev-process-supervisor.py

RUN chmod +x /entrypoint.sh /dev-process-supervisor.py

EXPOSE 8000 8001

ENTRYPOINT ["/entrypoint.sh"]

# Production/release image: application source is immutable inside the image.
FROM backend-dependencies AS backend-runtime

ARG IMAGE_VERSION=dev
ARG IMAGE_REVISION=unknown

LABEL org.opencontainers.image.version="${IMAGE_VERSION}" \
    org.opencontainers.image.revision="${IMAGE_REVISION}"

RUN rm -f /tmp/dev-requirements.txt

COPY src/backend /opt/hyperfilelens/backend
COPY deploy/bootstrap /opt/hyperfilelens/bootstrap
COPY deploy/docker/backend-entrypoint.sh /entrypoint.sh
# Optional Open Core extensions staged by release/build.sh or CI (may be empty).
COPY build/release/extensions/ /opt/hyperfilelens/extensions/
ARG HFL_EXTENSIONS=
ENV HFL_EXTENSIONS=${HFL_EXTENSIONS}

RUN chmod +x /entrypoint.sh

EXPOSE 8000 8001

ENTRYPOINT ["/entrypoint.sh"]
