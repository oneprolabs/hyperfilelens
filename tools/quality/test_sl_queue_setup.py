"""Offline Docker adapter tests: no live containers/networks are modified."""

import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "sl_queue_setup",
    ROOT / "deploy/installer/configure-sl-queue-monitor.py",
)
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class QueueSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "sourcelens").mkdir()
        (self.root / "sourcelens/docker-compose.yml").write_text("services: {}")
        (self.root / ".env").write_text(
            "SOURCELENS_MODE=bundled\nREDIS_URL=redis://redis:6379/0\n"
        )
        self.items = {
            "a" * 64: self.item("a" * 64, "redis"),
            "b" * 64: self.item("b" * 64, "api"),
        }
        self.items["b" * 64]["Config"]["Env"] = [
            "CELERY_BROKER_URL=redis://redis:6379/0"
        ]
        self.members = {}
        self.calls = []

    def item(self, cid, service):
        return {
            "Id": cid,
            "Config": {
                "Labels": {
                    "com.docker.compose.project": "hyperfilelens-sourcelens",
                    "com.docker.compose.service": service,
                    "com.docker.compose.project.working_dir": str(
                        self.root / "sourcelens"
                    ),
                },
                "Env": [],
            },
            "NetworkSettings": {"Networks": {"private-sl": {"Aliases": [service]}}},
        }

    def docker(self, *args):
        self.calls.append(args)
        if args[0] == "ps":
            identity = next((a[3:] for a in args if a.startswith("id=")), None)
            if identity is not None:
                return identity if identity in self.items else ""
            service = args[-1].split("=")[-1]
            project = next(
                a.split("=")[-1]
                for a in args
                if a.startswith("label=com.docker.compose.project=")
            )
            return "\n".join(
                cid
                for cid, item in self.items.items()
                if item["Config"]["Labels"]["com.docker.compose.service"] == service
                and item["Config"]["Labels"]["com.docker.compose.project"] == project
            )
        if args[0] == "inspect":
            if any(cid not in self.items for cid in args[1:]):
                raise RuntimeError("missing container")
            return json.dumps([self.items[cid] for cid in args[1:]])
        if args[:2] == ("network", "inspect"):
            return json.dumps([{"Containers": self.members}])
        if args[:2] == ("network", "connect"):
            cid = args[-1]
            self.members[cid] = {}
            self.items[cid]["NetworkSettings"]["Networks"][setup.BRIDGE] = {
                "Aliases": [setup.ALIAS]
            }
            return ""
        if args[:2] == ("network", "disconnect"):
            cid = args[-1]
            self.members.pop(cid, None)
            self.items[cid]["NetworkSettings"]["Networks"].pop(setup.BRIDGE, None)
            return ""
        raise AssertionError(args)

    def configure(self, remove=False):
        with patch.object(setup, "docker", side_effect=self.docker):
            setup.configure(self.root, remove)

    def test_fresh_and_repeated_setup_only_attach_owned_redis(self):
        self.configure()
        self.configure()
        connects = [c for c in self.calls if c[:2] == ("network", "connect")]
        self.assertEqual(
            connects,
            [
                ("network", "connect", "--alias", setup.ALIAS, setup.BRIDGE, "a" * 64),
            ],
        )
        self.assertIn("private-sl", self.items["a" * 64]["NetworkSettings"]["Networks"])
        self.assertEqual(
            setup.env_value(self.root / ".env", setup.AUTO_KEY),
            "redis://hfl-sourcelens-redis:6379/0",
        )
        self.assertIn(
            "REDIS_URL=redis://redis:6379/0", (self.root / ".env").read_text()
        )
        self.assertFalse(any(c[0] in ("restart", "stop", "exec") for c in self.calls))

    def test_recreated_redis_is_reattached(self):
        self.configure()
        self.members.clear()
        del self.items["a" * 64]
        self.items["c" * 64] = self.item("c" * 64, "redis")
        self.configure()
        self.assertIn("c" * 64, self.members)
        marker = json.loads((self.root / "deploy/sl-queue-monitor.json").read_text())
        self.assertEqual(marker["container"], "c" * 64)

    def test_remove_only_detaches_installer_owned_attachment(self):
        self.configure()
        self.configure(remove=True)
        self.assertNotIn(
            setup.BRIDGE, self.items["a" * 64]["NetworkSettings"]["Networks"]
        )
        self.assertIn("private-sl", self.items["a" * 64]["NetworkSettings"]["Networks"])
        self.assertEqual(setup.env_value(self.root / ".env", setup.AUTO_KEY), "")

    def test_preexisting_safe_connection_is_not_removed(self):
        self.items["a" * 64]["NetworkSettings"]["Networks"][setup.BRIDGE] = {
            "Aliases": [setup.ALIAS]
        }
        self.members["a" * 64] = {}
        self.configure()
        self.configure(remove=True)
        self.assertIn(setup.BRIDGE, self.items["a" * 64]["NetworkSettings"]["Networks"])

    def test_explicit_endpoint_is_never_overwritten(self):
        with (self.root / ".env").open("a") as stream:
            stream.write("HFL_SL_RUNTIME_REDIS_URL=redis://custom:6379/1\n")
        self.configure()
        self.assertEqual(
            setup.env_value(self.root / ".env", "HFL_SL_RUNTIME_REDIS_URL"),
            "redis://custom:6379/1",
        )
        self.assertFalse(self.calls)

    def test_explicit_url_using_managed_alias_keeps_automatic_connectivity(self):
        with (self.root / ".env").open("a") as stream:
            stream.write(
                "HFL_SL_RUNTIME_REDIS_URL=redis://hfl-sourcelens-redis:6379/1\n"
            )
        self.configure()
        self.assertIn("a" * 64, self.members)
        self.assertEqual(
            setup.env_value(self.root / ".env", "HFL_SL_RUNTIME_REDIS_URL"),
            "redis://hfl-sourcelens-redis:6379/1",
        )

    def test_foreign_service_and_untrusted_bridge_are_rejected(self):
        self.items["a" * 64]["Config"]["Labels"][
            "com.docker.compose.project.working_dir"
        ] = "/other"
        with self.assertRaises(RuntimeError):
            self.configure()
        self.assertFalse(self.members)
        self.items["a" * 64] = self.item("a" * 64, "redis")
        self.items["d" * 64] = self.item("d" * 64, "lensnode")
        self.members["d" * 64] = {}
        with self.assertRaises(RuntimeError):
            self.configure()
        self.assertNotIn(
            setup.BRIDGE, self.items["a" * 64]["NetworkSettings"]["Networks"]
        )

    def test_unsafe_generic_alias_is_rejected(self):
        self.items["a" * 64]["NetworkSettings"]["Networks"][setup.BRIDGE] = {
            "Aliases": [setup.ALIAS, "redis"]
        }
        with self.assertRaises(RuntimeError):
            self.configure()

    def test_broker_credentials_are_quoted_and_kept_out_of_marker(self):
        self.items["b" * 64]["Config"]["Env"] = [
            "REDIS_URL=redis://user:p%24ss@redis:6379/2"
        ]
        self.configure()
        self.assertIn(
            "user:p%24ss@hfl-sourcelens-redis:6379/2", (self.root / ".env").read_text()
        )
        self.assertNotIn(
            "user", (self.root / "deploy/sl-queue-monitor.json").read_text()
        )
        self.assertEqual((self.root / ".env").stat().st_mode & 0o777, 0o600)

    def test_generic_dns_name_is_also_rejected(self):
        self.items["a" * 64]["NetworkSettings"]["Networks"][setup.BRIDGE] = {
            "Aliases": [setup.ALIAS],
            "DNSNames": ["postgres"],
        }
        with self.assertRaises(RuntimeError):
            self.configure()

    def test_external_cleanup_preserves_explicit_configuration(self):
        self.configure()
        text = (
            (self.root / ".env")
            .read_text()
            .replace("SOURCELENS_MODE=bundled", "SOURCELENS_MODE=external")
        )
        (self.root / ".env").write_text(text)
        self.configure()
        self.assertFalse(self.members)

    def test_cleanup_preserves_intent_when_a_live_container_has_no_endpoint_yet(self):
        marker = self.root / "deploy/sl-queue-monitor.json"
        setup.atomic_write(marker, json.dumps({"container": "a" * 64, "created": True}))
        self.configure(remove=True)
        self.assertTrue(marker.exists())
        self.assertFalse(self.members)
        self.assertEqual(setup.env_value(self.root / ".env", setup.AUTO_KEY), "")

    def test_alias_verification_failure_rolls_back_only_new_connection(self):
        real = self.docker

        def unsafe_connect(*args):
            result = real(*args)
            if args[:2] == ("network", "connect"):
                self.items[args[-1]]["NetworkSettings"]["Networks"][setup.BRIDGE][
                    "Aliases"
                ].append("redis")
            return result

        with patch.object(setup, "docker", side_effect=unsafe_connect):
            with self.assertRaises(RuntimeError):
                setup.configure(self.root)
        self.assertFalse(self.members)
        self.assertIn("private-sl", self.items["a" * 64]["NetworkSettings"]["Networks"])
        self.assertNotIn(setup.AUTO_KEY, (self.root / ".env").read_text())

    def test_cli_failure_does_not_abort_installation_or_print_secrets(self):
        import io
        from contextlib import redirect_stdout

        output = io.StringIO()
        with (
            patch("sys.argv", ["helper", "--root", str(self.root)]),
            patch.object(
                setup,
                "configure",
                side_effect=RuntimeError("secret-password"),
            ),
            redirect_stdout(output),
        ):
            setup.main()
        self.assertIn("WARNING", output.getvalue())
        self.assertNotIn("secret-password", output.getvalue())
        self.assertEqual(setup.env_value(self.root / ".env", setup.AUTO_KEY), "")

    def test_shell_lifecycle_and_all_packagers_ship_the_helper(self):
        installer = (ROOT / "deploy/installer/install.sh").read_text()
        self.assertIn("configure_sl_queue_monitor --remove", installer)
        for start, end in (
            ("cmd_install() {", "cmd_start() {"),
            ("cmd_start() {", "cmd_stop() {"),
            ("cmd_upgrade() {", "\nmain() {"),
        ):
            block = installer.split(start, 1)[1].split(end, 1)[0]
            self.assertIn("configure_sl_queue_monitor", block)
        self.assertIn("recover_upgrade_services()", installer)
        for path in (
            "deploy/online/prepare.py",
            "release/build.sh",
            "release/ci/assemble-release.sh",
            "release/ci/assemble-saas-candidate.sh",
        ):
            self.assertIn("configure-sl-queue-monitor.py", (ROOT / path).read_text())


if __name__ == "__main__":
    unittest.main()
