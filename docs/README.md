# Documentation sources

The public website and user-documentation sources live under `website/`.

- `website/en/index.md` and `website/zh/index.md` are the product homepage entries.
- `website/zh/docs/` contains the Simplified Chinese user guide.
- `website/.vitepress/navigation/` owns locale-specific navigation and sidebars.
- `website/.vitepress/theme/docs.css` contains documentation-only visual overrides.

User documentation describes released product behavior. Verify terminology,
screenshots, supported platforms, and workflow steps against the target release
before publishing a revision. Do not include credentials, customer data, access
tokens, or environment-specific secrets in examples or screenshots.

The public guide describes the latest supported Community release. A change that
alters a documented workflow, UI label, support boundary, or deployment command
should update the corresponding guide in the same release change. Before
publishing, build the website, validate internal links, and review the affected
journey against the release candidate. Users of older versions should follow the
matching release notes and assets instead of assuming the latest guide applies.
