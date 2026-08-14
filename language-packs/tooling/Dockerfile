ARG NODE_BASE_IMAGE=node:22-alpine
FROM ${NODE_BASE_IMAGE}

ARG NPM_REGISTRY

RUN apk add --no-cache bash gettext python3 tar

WORKDIR /workspace
COPY src/frontend/package.json src/frontend/package-lock.json src/frontend/
RUN if [ -n "${NPM_REGISTRY}" ]; then npm config set registry "${NPM_REGISTRY}"; fi \
 && npm ci --prefix src/frontend

COPY src/frontend/src/locales/ src/frontend/src/locales/
COPY src/backend/ src/backend/
COPY language-packs/ language-packs/

ENTRYPOINT ["/workspace/language-packs/tooling/build-all.sh"]
