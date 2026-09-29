#!/usr/bin/env bash
# Exercise the real auth_request/error_page chain with a local, isolated Nginx.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf -- "${tmp}"' EXIT
mkdir -p "${tmp}/media"
printf 'agent fixture\n' >"${tmp}/media/agent.zip"
cat >"${tmp}/nginx.conf" <<'NGINX'
events {}
http {
    upstream hfl_api_http { server 127.0.0.1:18081; }
    server {
        listen 18080;
        set $hfl_site tenant;
        include /etc/nginx/snippets/hfl-agent-release-locations.conf;
    }
    server {
        listen 18081;
        location = /api/v1/node/enrollment/agent-releases/auth {
            if ($http_x_original_uri ~ "t=capacity") {
                add_header X-HFL-Download-Denial capacity always;
                add_header Retry-After 30 always;
                return 403;
            }
            if ($http_x_original_uri ~ "t=invalid") { return 401; }
            if ($http_x_original_uri ~ "t=denied") { return 403; }
            return 204;
        }
    }
}
NGINX

docker run --rm --network none --entrypoint sh \
    -v "${tmp}/nginx.conf:/etc/nginx/nginx.conf:ro" \
    -v "${tmp}/media:/opt/hyperfilelens/backend/media/agent-releases:ro" \
    -v "${ROOT}/deploy/nginx/snippets/hfl-agent-release-locations.conf:/etc/nginx/snippets/hfl-agent-release-locations.conf:ro" \
    nginx:stable-alpine -ec '
nginx -t
nginx
check() {
    result="$({ printf "GET /media/agent-releases/agent.zip?t=%s HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n" "$1"; sleep 1; } | nc -w 3 127.0.0.1 18080)"
    printf "%s\n" "$result" | grep -F "HTTP/1.1 $2" >/dev/null || {
        printf "unexpected status for %s:\n%s\n" "$1" "$result" >&2
        exit 1
    }
}
check capacity 429
printf "%s\n" "$result" | grep -Ei "Retry-After: 30" >/dev/null || {
    printf "capacity response omitted Retry-After:\n%s\n" "$result" >&2
    exit 1
}
check invalid 401
! printf "%s\n" "$result" | grep -Ei "Retry-After:|X-HFL-Download-Denial:" >/dev/null
check denied 403
! printf "%s\n" "$result" | grep -Ei "Retry-After:|X-HFL-Download-Denial:" >/dev/null
check allowed 200
printf "Agent download Nginx auth_request checks passed.\n"
'
