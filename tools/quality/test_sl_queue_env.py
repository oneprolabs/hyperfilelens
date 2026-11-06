"""Offline contracts for inheriting SL queue defaults across deployment paths."""

from pathlib import Path
import json
import os
import re
import runpy
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).resolve().parents[2]
ADAPTER = runpy.run_path(str(ROOT / "deploy/installer/sourcelens/patch-env-runtime.py"))
CONCURRENCY_SETTINGS = (
    ("LENSNODE_MAX_CONCURRENT_RUNS=1\n", "1"),
    ("LENSNODE_MAX_CONCURRENT_RUNS=4\n", "4"),
    ("export LENSNODE_MAX_CONCURRENT_RUNS=2\n", "2"),
    ("  LENSNODE_MAX_CONCURRENT_RUNS=4\n", "4"),
    ("LENSNODE_MAX_CONCURRENT_RUNS = 2\n", "2"),
    ("\texport\tLENSNODE_MAX_CONCURRENT_RUNS \t= '4' # local limit\n", "4"),
    ('LENSNODE_MAX_CONCURRENT_RUNS="2" # local limit\n', "2"),
    ("LENSNODE_MAX_CONCURRENT_RUNS=\n", "8"),
    (
        "export LENSNODE_MAX_CONCURRENT_RUNS=2\nLENSNODE_MAX_CONCURRENT_RUNS=4\n",
        "4",
    ),
)


class RunConcurrencyEnvironmentTests(unittest.TestCase):
    """Keep Q&A concurrency defaults separate from Chat preparation limits."""

    def test_missing_value_defaults_to_eight_and_is_idempotent(self):
        for original in (
            "SECRET_KEY=keep-me\n",
            "# export LENSNODE_MAX_CONCURRENT_RUNS=2\n",
            "LENSNODE_MAX_CONCURRENT_RUNS_OTHER=2\n",
        ):
            with self.subTest(original=original):
                text = ADAPTER["apply_runtime_env"](original)
                self.assertIn(original, text)
                self.assertIn("LENSNODE_MAX_CONCURRENT_RUNS=8\n", text)
                self.assertEqual(ADAPTER["apply_runtime_env"](text), text)

    def test_explicit_runtime_values_survive_repeated_normalization(self):
        for setting, _expected in CONCURRENCY_SETTINGS:
            with self.subTest(setting=setting):
                text = ADAPTER["apply_runtime_env"](setting)
                self.assertIn(setting, text)
                assignments = [
                    match.group(0)
                    for match in ADAPTER["QUEUE_ASSIGNMENT"].finditer(text)
                    if match.group(1) == "LENSNODE_MAX_CONCURRENT_RUNS"
                ]
                self.assertEqual("".join(f"{line}\n" for line in assignments), setting)
                self.assertEqual(ADAPTER["apply_runtime_env"](text), text)

    def test_dev_and_installer_merge_preserve_existing_limits(self):
        for relative in (
            "tools/sourcelens/common.sh",
            "deploy/installer/sourcelens/install.sh",
        ):
            source = (ROOT / relative).read_text()
            position = source.index("existing = set(re.findall")
            start = source.rfind("<<'PY'\n", 0, position) + len("<<'PY'\n")
            body = source[start : source.index("\nPY", position)]
            for setting, _expected in (("", "8"), *CONCURRENCY_SETTINGS):
                with (
                    self.subTest(path=relative, setting=setting),
                    TemporaryDirectory() as directory,
                ):
                    env = Path(directory) / ".env"
                    example = Path(directory) / ".env.example"
                    env.write_text(setting)
                    example.write_text("LENSNODE_MAX_CONCURRENT_RUNS=8\n")
                    import sys
                    from unittest.mock import patch

                    for _ in range(2):
                        with patch.object(
                            sys, "argv", ["merge", str(env), str(example)]
                        ):
                            exec(compile(body, relative, "exec"), {})
                        env.write_text(ADAPTER["apply_runtime_env"](env.read_text()))
                    text = env.read_text()
                    if setting:
                        self.assertIn(setting, text)
                    else:
                        self.assertIn("LENSNODE_MAX_CONCURRENT_RUNS=8\n", text)
                    assignments = [
                        match.group(0)
                        for match in ADAPTER["QUEUE_ASSIGNMENT"].finditer(text)
                        if match.group(1) == "LENSNODE_MAX_CONCURRENT_RUNS"
                    ]
                    self.assertEqual(
                        "".join(f"{line}\n" for line in assignments),
                        setting or "LENSNODE_MAX_CONCURRENT_RUNS=8\n",
                    )

    def test_public_compose_uses_env_override_instead_of_hard_coded_one(self):
        template = (
            ROOT / "deploy/installer/sourcelens/docker-compose.template.yml"
        ).read_text()
        self.assertIn(
            "LENSNODE_MAX_CONCURRENT_RUNS: ${LENSNODE_MAX_CONCURRENT_RUNS:-8}",
            template,
        )
        self.assertIn(
            "LENSNODE_MAX_CONCURRENT_RUNS=8\n",
            (ROOT / "deploy/online/sourcelens/env.example").read_text(),
        )
        if not shutil.which("docker"):
            self.skipTest("Docker Compose is unavailable for config-only validation")
        if subprocess.run(
            ["docker", "compose", "version"], capture_output=True
        ).returncode:
            self.skipTest("Docker Compose is unavailable for config-only validation")
        with TemporaryDirectory() as directory:
            root = Path(directory)
            # No images are started or pulled: only resolve the deployment YAML.
            template = re.sub(
                r"__SOURCELENS_\w+_IMAGE__", "fixture/image:test", template
            )
            template = template.replace(
                "__SOURCELENS_CONSOLE_BIND_ADDRESS__", "127.0.0.1"
            )
            template = template.replace("__SOURCELENS_CONSOLE_PORT__", "11445")
            compose = root / "docker-compose.yml"
            compose.write_text(template)
            process_env = dict(os.environ)
            process_env.pop("LENSNODE_MAX_CONCURRENT_RUNS", None)
            for setting, expected in (("", "8"), *CONCURRENCY_SETTINGS):
                env = root / ".env"
                env.write_text(ADAPTER["apply_runtime_env"](setting))
                with self.subTest(setting=setting):
                    result = subprocess.run(
                        [
                            "docker",
                            "compose",
                            "--env-file",
                            str(env),
                            "-f",
                            str(compose),
                            "config",
                            "--format",
                            "json",
                        ],
                        env=process_env,
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                    service = json.loads(result.stdout)["services"]["local-lensnode"]
                    self.assertEqual(
                        service["environment"]["LENSNODE_MAX_CONCURRENT_RUNS"],
                        expected,
                    )

    def test_private_sidecar_defaults_overrides_and_preserves_existing_limits(self):
        installer = ROOT / "deploy/bootstrap/gateway-install-lensnode-sidecar.sh"
        function = re.search(
            r"^install_docker_sidecar\(\) \{\n.*?^\}",
            installer.read_text(),
            re.M | re.S,
        ).group()
        program = r"""
set -euo pipefail
COMPOSE_DIR="$1"
COMPOSE_PROJECT=hyperfilelens-gateway
SENTRY_PRIVACY_FILE="${COMPOSE_DIR}/privacy.py"
HFL_WORKSPACE_ROOT="${COMPOSE_DIR}/workspace"
HFL_SOURCELENS_MOUNTPOINT="${COMPOSE_DIR}/state"
HFL_SOURCELENS_STATE_ROOT="${COMPOSE_DIR}/state"
HFL_GATEWAY_TRASH_ROOT="${COMPOSE_DIR}/trash"
LENSNODE_TOKEN=fixture-token LENSNODE_NAME=fixture-gateway
LENS_CONTAINER_URL=https://console.example/sourcelens HFL_INSECURE_TLS=1
hfl_step() { :; }
hfl_ok() { :; }
hfl_warn() { :; }
hfl_fail() { printf '%s\n' "$1" >&2; exit "${2:-1}"; }
lens_url_needs_extra_hosts() { return 1; }
fixture_compose() {
    if [[ "$*" == "-p hyperfilelens-gateway ps -q lensnode" ]]; then
        echo fixture-container
    fi
}
resolve_compose() { COMPOSE=(fixture_compose); }
docker() {
    case "$*" in
        "image inspect --format {{.Id}} fixture:test"|"inspect --format {{.Image}} fixture-container")
            echo fixture ;;
        "inspect --format {{.State.Running}} fixture-container") echo true ;;
    esac
}
remove_owned_legacy_gateway_containers() { :; }
"""
        for previous, override, expected in (
            (None, None, "8"),
            (None, "2", "2"),
            ('"1"', None, "1"),
            ("4", None, "4"),
            ("'2' # local limit", None, "2"),
            ('"4"', None, "4"),
            ('"1"', "8", "8"),
            ('"4"', "2", "2"),
            ('"0"', None, None),
            ('"${CUSTOM_LIMIT}"', None, None),
            ('"1"', "0", None),
            ('"4"', "-1", None),
            ('"4"', "2\nBAD=1", None),
        ):
            with (
                self.subTest(previous=previous, override=override),
                TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                compose = root / "docker-compose.yml"
                if previous is not None:
                    compose.write_text(
                        f"services:\n  lensnode:\n    environment:\n"
                        f"      LENSNODE_MAX_CONCURRENT_RUNS: {previous}\n"
                    )
                process_env = dict(os.environ)
                process_env.pop("LENSNODE_MAX_CONCURRENT_RUNS", None)
                if override is not None:
                    process_env["LENSNODE_MAX_CONCURRENT_RUNS"] = override
                result = subprocess.run(
                    [
                        "bash",
                        "-c",
                        program + function + "\ninstall_docker_sidecar fixture:test",
                        "sidecar-test",
                        directory,
                    ],
                    env=process_env,
                    capture_output=True,
                    text=True,
                )
                if expected is None:
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertIn("must be a positive integer", result.stderr)
                    self.assertIn(
                        f"LENSNODE_MAX_CONCURRENT_RUNS: {previous}", compose.read_text()
                    )
                    self.assertFalse(list(root.glob(".docker-compose.yml.*")))
                    continue
                self.assertEqual(result.returncode, 0, result.stderr)
                setting = f'LENSNODE_MAX_CONCURRENT_RUNS: "{expected}"'
                self.assertIn(setting, compose.read_text())
                # Reinstallation must also retain the rendered setting after
                # enrollment drops unmanaged entries from lensnode.env.
                process_env.pop("LENSNODE_MAX_CONCURRENT_RUNS", None)
                subprocess.run(
                    [
                        "bash",
                        "-c",
                        program + function + "\ninstall_docker_sidecar fixture:test",
                        "sidecar-test",
                        directory,
                    ],
                    env=process_env,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                self.assertIn(setting, compose.read_text())


class QueueEnvironmentTests(unittest.TestCase):
    def normalize(self, text, **kwargs):
        return ADAPTER["remove_default_queue_override"](text, **kwargs)

    def test_owned_templates_inherit_any_future_image_default(self):
        for default in ("sourcelens", "backend", "future-default"):
            with self.subTest(default=default):
                text = f"SECRET_KEY=keep-me\nCELERY_TASK_DEFAULT_QUEUE={default}\n"
                self.assertEqual(
                    self.normalize(text, template=True), "SECRET_KEY=keep-me\n"
                )

    def test_template_cli_and_runtime_cli_distinguish_custom_configuration(self):
        import sys
        from unittest.mock import patch

        with TemporaryDirectory() as directory:
            path = Path(directory) / ".env.example"
            path.write_text("CELERY_TASK_DEFAULT_QUEUE=future-custom\n")
            with patch.object(sys, "argv", ["adapter", str(path)]):
                ADAPTER["main"]()
            self.assertIn("CELERY_TASK_DEFAULT_QUEUE=future-custom", path.read_text())
            with patch.object(sys, "argv", ["adapter", str(path), "--template"]):
                ADAPTER["main"]()
            self.assertNotIn("CELERY_TASK_DEFAULT_QUEUE", path.read_text())

    def test_known_legacy_conflict_is_removed_without_changing_other_settings(self):
        for suffix in (
            "",
            "CELERY_WORKER_QUEUES=backend,lens\n",
            "CELERY_WORKER_QUEUES=\n",
            'CELERY_TASK_QUEUES="backend,lens"\nCELERY_REQUIRED_QUEUES=lens,backend\n',
        ):
            text = "CELERY_TASK_DEFAULT_QUEUE=sourcelens\nREDIS_URL=keep-me\n" + suffix
            self.assertEqual(self.normalize(text), "REDIS_URL=keep-me\n" + suffix)

    def test_quoted_exported_and_duplicate_known_values_are_removed(self):
        text = (
            " export CELERY_TASK_DEFAULT_QUEUE = 'sourcelens' # old sample\n"
            'CELERY_TASK_DEFAULT_QUEUE="sourcelens"\n'
            "OTHER=unchanged\n"
        )
        self.assertEqual(self.normalize(text), "OTHER=unchanged\n")

    def test_quotes_in_comments_do_not_prevent_legacy_migration(self):
        for value in (
            "sourcelens # don't keep the legacy default",
            'sourcelens # remove "old setting',
            "'sourcelens' # don't retain \"this value",
            '"sourcelens"\t# old value\'s comment',
        ):
            with self.subTest(value=value):
                text = f"CELERY_TASK_DEFAULT_QUEUE={value}\nOTHER=keep\n"
                self.assertEqual(self.normalize(text), "OTHER=keep\n")

    def test_hashes_escapes_and_real_quotes_within_values_remain_custom(self):
        for value in (
            "sourcelens#custom",
            "'sourcelens #custom'",
            '"sourcelens #custom"',
            r'"sourcelens \" #custom"',
            r"sourcelens\#custom",
            "'sourcelens #custom' # don't change",
            '"sourcelens #custom" # unmatched \' here',
        ):
            with self.subTest(value=value):
                text = f"CELERY_TASK_DEFAULT_QUEUE={value}\n"
                self.assertEqual(self.normalize(text), text)

    def test_custom_or_ambiguous_existing_configuration_is_preserved(self):
        for text in (
            "CELERY_TASK_DEFAULT_QUEUE=custom\n",
            "CELERY_TASK_DEFAULT_QUEUE=backend\n",
            "CELERY_TASK_DEFAULT_QUEUE=\n",
            'CELERY_TASK_DEFAULT_QUEUE="${CUSTOM_QUEUE}"\n',
            'CELERY_TASK_DEFAULT_QUEUE="unterminated\n',
            "CELERY_TASK_DEFAULT_QUEUE=sourcelens#custom\n",
            'CELERY_TASK_DEFAULT_QUEUE="sourcelens #custom"\n',
            "CELERY_TASK_DEFAULT_QUEUE=sourcelens\nCELERY_TASK_DEFAULT_QUEUE=custom\n",
            "CELERY_TASK_DEFAULT_QUEUE=sourcelens\nCELERY_WORKER_QUEUES=backend,lens,sourcelens\n",
            "CELERY_TASK_DEFAULT_QUEUE=sourcelens\nCELERY_TASK_QUEUES=custom,lens\n",
            "CELERY_TASK_DEFAULT_QUEUE=sourcelens\nCELERY_REQUIRED_QUEUES=\n",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.normalize(text), text)

    def test_absence_is_preserved_and_full_adapter_is_idempotent(self):
        text = ADAPTER["apply_runtime_env"]("CELERY_TASK_DEFAULT_QUEUE=sourcelens\n")
        self.assertNotIn("CELERY_TASK_DEFAULT_QUEUE", text)
        self.assertEqual(ADAPTER["apply_runtime_env"](text), text)
        for key in (
            "CELERY_TASK_QUEUES",
            "CELERY_WORKER_QUEUES",
            "CELERY_REQUIRED_QUEUES",
        ):
            self.assertNotIn(key, text)

    def test_dev_and_installer_merge_never_reinsert_removed_default(self):
        for relative, marker in (
            ("tools/sourcelens/common.sh", "existing = set(re.findall"),
            ("deploy/installer/sourcelens/install.sh", "existing = set(re.findall"),
        ):
            source = (ROOT / relative).read_text()
            position = source.index(marker)
            start = source.rfind("<<'PY'\n", 0, position) + len("<<'PY'\n")
            end = source.index("\nPY", position)
            body = source[start:end]
            with self.subTest(path=relative), TemporaryDirectory() as directory:
                env = Path(directory) / ".env"
                example = Path(directory) / ".env.example"
                env.write_text("SECRET_KEY=keep-me\n")
                example.write_text(
                    "CELERY_TASK_DEFAULT_QUEUE=sourcelens\nNEW_SETTING=added\n"
                )
                import sys
                from unittest.mock import patch

                for _ in range(2):
                    with patch.object(sys, "argv", ["merge", str(env), str(example)]):
                        exec(compile(body, relative, "exec"), {})
                self.assertEqual(
                    env.read_text(), "SECRET_KEY=keep-me\nNEW_SETTING=added\n"
                )

    def test_online_and_saas_staging_share_the_normalized_template(self):
        online = runpy.run_path(str(ROOT / "deploy/online/prepare.py"))
        with TemporaryDirectory() as directory:
            target = Path(directory)
            online["stage_sourcelens"](
                ROOT,
                target,
                {
                    name: f"fixture/{name}:1"
                    for name in (
                        "backend",
                        "frontend",
                        "lensnode",
                        "nginx",
                        "postgres",
                        "redis",
                    )
                },
            )
            sample = (target / "sourcelens/.env.example").read_text()
            self.assertNotIn("CELERY_TASK_DEFAULT_QUEUE", sample)
            self.assertIn("LENSNODE_MAX_CONCURRENT_RUNS=8\n", sample)
            self.assertTrue((target / "sourcelens/patch-env-runtime.py").is_file())
        candidate = (ROOT / "release/ci/assemble-saas-candidate.sh").read_text()
        self.assertIn('online["stage_sourcelens"]', candidate)
        release = (ROOT / "release/build-sourcelens.sh").read_text()
        self.assertIn(
            'sourcelens_patch_env_runtime_defaults "${sl_root}/.env.example" --template',
            release,
        )

    def test_existing_custom_value_survives_full_adapter(self):
        text = ADAPTER["apply_runtime_env"](
            "CELERY_TASK_DEFAULT_QUEUE=custom\nCELERY_WORKER_QUEUES=custom,lens\n"
        )
        self.assertIn("CELERY_TASK_DEFAULT_QUEUE=custom\n", text)
        self.assertIn("CELERY_WORKER_QUEUES=custom,lens\n", text)

    def test_queue_changes_do_not_touch_tasks_or_monitoring_configuration(self):
        source = (ROOT / "deploy/installer/sourcelens/patch-env-runtime.py").read_text()
        self.assertNotRegex(source, r"\b(purge|lpop|rpop|lmove|rpush|lpush)\b")
        self.assertNotIn("HFL_SL_RUNTIME_QUEUES", source)
        template = (ROOT / "deploy/online/sourcelens/env.example").read_text()
        self.assertIsNone(re.search(r"^CELERY_TASK_DEFAULT_QUEUE=", template, re.M))

    def test_all_start_and_automation_gates_use_shared_read_only_verifier(self):
        dev = (ROOT / "dev/stack.sh").read_text()
        installer = (ROOT / "deploy/installer/install.sh").read_text()
        for start, end in (
            ("cmd_up() {", "cmd_down() {"),
            ("cmd_restart() {", "cmd_status() {"),
        ):
            block = dev.split(start, 1)[1].split(end, 1)[0]
            self.assertLess(
                block.index("prepare_sourcelens_dev"),
                block.index("verify_sl_queue_config_dev"),
            )
        for start, end in (
            ("cmd_install() {", "cmd_start() {"),
            ("cmd_start() {", "cmd_stop() {"),
            ("cmd_upgrade() {", "\nmain() {"),
        ):
            self.assertIn(
                "verify_sl_queue_config", installer.split(start, 1)[1].split(end, 1)[0]
            )
        action = (ROOT / ".github/actions/deploy-saas/action.yml").read_text()
        self.assertIn("--verify-queues --timeout 60", action)
        workflow = (ROOT / ".github/workflows/enterprise_saas_upgrade.yml").read_text()
        self.assertIn("test_sl_queue_env.py", workflow)
        self.assertIn("test_sl_queue_setup.py", workflow)

    def test_retained_sl_does_not_block_hfl_only_upgrade_with_new_queue_gate(self):
        source = (ROOT / "deploy/installer/install.sh").read_text()
        lifecycle = source.index("\t# SourceLens is a separate lifecycle.")
        start = source.index(
            '\tif [[ "$(configured_sourcelens_mode)" == "bundled" ]]', lifecycle
        )
        end = source.index('\n\tif [[ "${SOURCELENS_MAINTENANCE_ARMED}"', start)
        block = source[start:end]
        stubs = """
set -e
ROOT=/fixture
configured_sourcelens_mode() { echo bundled; }
sourcelens_installed() { return 0; }
configure_lens_bridge_env() { echo existing-health-configuration; }
wait_for_sourcelens_health() { return 0; }
verify_sl_queue_config() { echo hard-queue-gate; return "${gate_status}"; }
record_sourcelens_installed_bundle() { echo bundle-recorded; }
log() { echo "$*"; }
die() { echo "$*"; exit 1; }
"""
        for upgraded, removed, gate_status, expected_status in (
            (0, 0, 1, 0),  # --hfl-only / unchanged SL must retain old behavior.
            (1, 0, 1, 1),  # An actual SL upgrade must still reject bad queues.
            (1, 0, 0, 0),  # Verified upgrade records its bundle identity.
            (0, 1, 1, 0),  # SL removal must not invoke queue verification.
        ):
            with self.subTest(upgraded=upgraded, removed=removed, gate=gate_status):
                script = (
                    stubs
                    + (
                        f"\nupgrade_sourcelens={upgraded}\nremove_sourcelens={removed}"
                        f"\ngate_status={gate_status}\n"
                    )
                    + block
                    + '\necho "upgrade-finished"\n'
                )
                result = subprocess.run(
                    ["bash", "-c", script], capture_output=True, text=True
                )
                self.assertEqual(result.returncode, expected_status, result.stdout)
                self.assertEqual(
                    "hard-queue-gate" in result.stdout, bool(upgraded and not removed)
                )
                self.assertEqual(
                    "bundle-recorded" in result.stdout,
                    bool(upgraded and not removed and not gate_status),
                )


if __name__ == "__main__":
    unittest.main()
