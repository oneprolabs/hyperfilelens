# Gateway image: hyperfilelens-frontend (SPA build + nginx reverse proxy).
# Development targets keep dependencies in the image and bind-mount source.
ARG FRONTEND_NODE_BASE_IMAGE=node:22-alpine
ARG FRONTEND_NGINX_BASE_IMAGE=nginx:stable-alpine
FROM ${FRONTEND_NODE_BASE_IMAGE} AS frontend-dependencies

LABEL org.opencontainers.image.title="hyperfilelens-frontend"

ARG NPM_REGISTRY
ARG VITE_SHOW_EULA=false
ARG SENTRY_URL=
ARG SENTRY_ORG=
ARG SENTRY_FRONTEND_PROJECT=
ARG SENTRY_RELEASE=

WORKDIR /app
COPY src/frontend/package.json src/frontend/package-lock.json ./
RUN if [ -n "${NPM_REGISTRY}" ]; then npm config set registry "${NPM_REGISTRY}"; fi
RUN npm ci \
 && sha256sum package-lock.json | awk '{print $1}' > node_modules/.hfl-package-lock.sha

FROM frontend-dependencies AS frontend-development

RUN apk add --no-cache nginx

COPY deploy/docker/frontend-dev-entrypoint.sh /usr/local/bin/hfl-frontend-dev
COPY deploy/nginx/development-web.conf /etc/nginx/nginx.conf
RUN chmod 0755 /usr/local/bin/hfl-frontend-dev

EXPOSE 8080 8081 8082

ENTRYPOINT ["/usr/local/bin/hfl-frontend-dev"]

# Release build stage.
FROM frontend-dependencies AS frontend-build

COPY src/frontend/ ./
# Optional Open Core extensions for Vite discovery at build time (may be empty).
COPY build/release/extensions/ /opt/hfl/extensions/
ARG HFL_EXTENSIONS=
ENV HFL_EXTENSIONS=${HFL_EXTENSIONS}
ENV VITE_SHOW_EULA=${VITE_SHOW_EULA}
RUN --mount=type=secret,id=sentry_auth_token \
    SENTRY_AUTH_TOKEN="$(cat /run/secrets/sentry_auth_token 2>/dev/null || true)" \
    npm run build \
 && find dist -type f -name '*.map' -delete

# Serve the SPA, standalone Website artifact, and reverse proxy through Nginx.
FROM ${FRONTEND_NGINX_BASE_IMAGE}

ARG IMAGE_VERSION=dev
ARG IMAGE_REVISION=unknown

LABEL org.opencontainers.image.version="${IMAGE_VERSION}" \
    org.opencontainers.image.revision="${IMAGE_REVISION}"

ENV TZ=UTC \
    LOGROTATE_INTERVAL_SECONDS=3600 \
    LOGROTATE_CONF=/etc/logrotate.d/hyperfilelens \
    LOGROTATE_STATE=/var/log/hyperfilelens/.logrotate.status \
    LOGROTATE_LOCK=/var/log/hyperfilelens/.logrotate.lock

RUN apk add --no-cache logrotate

COPY --from=frontend-build /app/dist /usr/share/nginx/html
COPY build/website/public /usr/share/nginx/website
COPY deploy/nginx/default.conf /etc/nginx/conf.d/default.conf
COPY deploy/nginx/snippets /etc/nginx/snippets
COPY deploy/logrotate/hyperfilelens.conf /etc/logrotate.d/hyperfilelens
COPY deploy/docker/frontend-logrotate-loop.sh /usr/local/bin/logrotate-loop.sh
COPY deploy/docker/frontend-runtime-config.sh /docker-entrypoint.d/20-hfl-frontend-runtime-config.sh

RUN mkdir -p /usr/share/nginx/runtime \
 && chmod 0644 /etc/logrotate.d/hyperfilelens \
 && chmod 0755 /usr/local/bin/logrotate-loop.sh /docker-entrypoint.d/20-hfl-frontend-runtime-config.sh \
 && printf '%s\n' '#!/bin/sh' 'exec /usr/local/bin/logrotate-loop.sh --daemon' \
    > /docker-entrypoint.d/99-logrotate-loop.sh \
 && chmod 0755 /docker-entrypoint.d/99-logrotate-loop.sh
