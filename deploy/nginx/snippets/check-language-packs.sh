#!/bin/sh
set -eu

language_root="${HFL_LANGUAGE_PACK_HEALTH_ROOT:-/opt/hyperfilelens/lang-packs}"
base_url="${HFL_LANGUAGE_PACK_HEALTH_BASE_URL:-https://127.0.0.1:11443}"

wget --no-check-certificate --spider -q "${base_url}/locales/installed.json"
for pack_dir in "${language_root}"/*; do
	[ -d "${pack_dir}" ] || continue
	pack_id="${pack_dir##*/}"
	messages="${pack_dir}/frontend/messages.json"
	components="${pack_dir}/frontend/element-plus.json"
	[ -f "${messages}" ] || exit 1
	wget --no-check-certificate --spider -q \
		"${base_url}/locales/${pack_id}/frontend/messages.json"
	if [ -f "${components}" ]; then
		wget --no-check-certificate --spider -q \
			"${base_url}/locales/${pack_id}/frontend/element-plus.json"
	fi
done
