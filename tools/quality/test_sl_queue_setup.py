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

    def configure(self, remove=False, *, dev=False):
        with patch.object(setup, "docker", side_effect=self.docker):
            setup.configure(self.root, remove, dev=dev)

    def use_dev_layout(self):
        runtime = self.root / "build/sourcelens/dev"
        runtime.mkdir(parents=True)
        (self.root / "sourcelens/docker-compose.yml").rename(
            runtime / "docker-compose.yml"
        )
        (self.root / "sourcelens").rmdir()
        for item in self.items.values():
            item["Config"]["Labels"]["com.docker.compose.project.working_dir"] = str(
                runtime
            )
        return runtime

    def test_dev_start_and_repeated_recovery_preserve_private_networks(self):
        runtime = self.use_dev_layout()
        nginx = self.item("c" * 64, "nginx")
        nginx["Config"]["Labels"]["com.docker.compose.project.working_dir"] = str(
            runtime
        )
        nginx["NetworkSettings"]["Networks"][setup.BRIDGE] = {
            "Aliases": ["nginx", "sourcelens-nginx"],
        }
        self.items["c" * 64] = nginx
        self.members["c" * 64] = {}
        nginx_networks = json.dumps(
            nginx["NetworkSettings"]["Networks"], sort_keys=True
        )
        api_networks = json.dumps(
            self.items["b" * 64]["NetworkSettings"]["Networks"], sort_keys=True
        )
        self.configure(dev=True)
        self.configure(dev=True)
        self.assertEqual(
            [call for call in self.calls if call[:2] == ("network", "connect")],
            [("network", "connect", "--alias", setup.ALIAS, setup.BRIDGE, "a" * 64)],
        )
        self.assertEqual(
            json.loads(setup.runtime_config_path(self.root).read_text())["url"],
            "redis://hfl-sourcelens-redis:6379/0",
        )
        self.assertEqual(
            json.dumps(nginx["NetworkSettings"]["Networks"], sort_keys=True),
            nginx_networks,
        )
        self.assertEqual(
            json.dumps(
                self.items["b" * 64]["NetworkSettings"]["Networks"], sort_keys=True
            ),
            api_networks,
        )
        self.assertIn("private-sl", self.items["a" * 64]["NetworkSettings"]["Networks"])
        self.assertFalse(
            any(call[0] in ("restart", "stop", "exec") for call in self.calls)
        )

    def test_installed_layout_does_not_silently_trust_dev_labels(self):
        self.use_dev_layout()
        redis = self.items["a" * 64]
        self.assertFalse(setup.owned(redis, self.root, "redis"))
        self.assertTrue(setup.owned(redis, self.root, "redis", dev=True))
        (self.root / "sourcelens").mkdir()
        (self.root / "sourcelens/docker-compose.yml").write_text("services: {}")
        with self.assertRaises(RuntimeError):
            self.configure()
        self.assertFalse(self.members)
        self.assertFalse(setup.runtime_config_path(self.root).exists())

    def test_dev_service_discovery_does_not_trust_another_repository(self):
        self.use_dev_layout()
        self.items["a" * 64]["Config"]["Labels"][
            "com.docker.compose.project.working_dir"
        ] = str(self.root / "another-repository/build/sourcelens/dev")
        with self.assertRaises(RuntimeError):
            self.configure(dev=True)
        self.assertFalse(self.members)

    def test_dev_recovery_reattaches_recreated_redis(self):
        runtime = self.use_dev_layout()
        self.configure(dev=True)
        self.members.clear()
        del self.items["a" * 64]
        replacement = self.item("c" * 64, "redis")
        replacement["Config"]["Labels"]["com.docker.compose.project.working_dir"] = str(
            runtime
        )
        self.items["c" * 64] = replacement
        self.configure(dev=True)
        self.assertIn("c" * 64, self.members)
        marker = json.loads((self.root / "deploy/sl-queue-monitor.json").read_text())
        self.assertEqual(marker["container"], "c" * 64)

    def test_dev_external_mode_cleans_up_only_owned_attachment(self):
        self.use_dev_layout()
        self.configure(dev=True)
        env = self.root / ".env"
        env.write_text(
            env.read_text().replace(
                "SOURCELENS_MODE=bundled", "SOURCELENS_MODE=external"
            )
        )
        self.configure(dev=True)
        self.assertFalse(self.members)
        self.assertEqual(
            json.loads(setup.runtime_config_path(self.root).read_text())["url"], ""
        )
        self.assertIn("private-sl", self.items["a" * 64]["NetworkSettings"]["Networks"])

    def test_dev_explicit_endpoint_is_preserved(self):
        self.use_dev_layout()
        with (self.root / ".env").open("a") as stream:
            stream.write("HFL_SL_RUNTIME_REDIS_URL=redis://custom:6379/1\n")
        self.configure(dev=True)
        self.assertEqual(
            setup.env_value(self.root / ".env", "HFL_SL_RUNTIME_REDIS_URL"),
            "redis://custom:6379/1",
        )
        self.assertFalse(self.calls)

    def test_dev_security_rejection_keeps_original_network(self):
        self.use_dev_layout()
        self.items["a" * 64]["NetworkSettings"]["Networks"][setup.BRIDGE] = {
            "Aliases": [setup.ALIAS, "redis"],
        }
        self.members["a" * 64] = {}
        with self.assertRaises(setup.UnsafeSetup):
            self.configure(dev=True)
        self.assertIn("private-sl", self.items["a" * 64]["NetworkSettings"]["Networks"])

    def test_dev_cli_selects_layout_without_printing_credentials(self):
        import io
        from contextlib import redirect_stdout

        self.use_dev_layout()
        output = io.StringIO()
        with (
            patch("sys.argv", ["helper", "--root", str(self.root), "--dev"]),
            patch.object(setup, "docker", side_effect=self.docker),
            redirect_stdout(output),
        ):
            setup.main()
        self.assertIn("reconciled", output.getvalue())
        self.assertNotIn("redis://", output.getvalue())
        self.assertIn("a" * 64, self.members)

    def test_verification_accepts_future_defaults_not_just_backend(self):
        producer = {
            "default_queue": "future-default",
            "declared_queues": ["future-default", "future-lens"],
            "required_queues": ["future-lens"],
        }
        self.assertTrue(
            setup.queue_config_matches([producer], ["future-default", "future-lens"])
        )
        self.assertFalse(setup.queue_config_matches([producer], ["future-lens"]))
        self.assertFalse(setup.queue_config_matches([producer], []))
        self.assertFalse(setup.queue_config_matches([producer], [None]))
        scheduler = dict(
            producer,
            default_queue="other-default",
            declared_queues=["other-default", "future-lens"],
        )
        self.assertFalse(
            setup.queue_config_matches(
                [producer, scheduler], ["future-default", "future-lens"]
            )
        )
        self.assertTrue(
            setup.queue_config_matches(
                [producer, scheduler],
                ["future-default", "other-default", "future-lens"],
            )
        )

    def test_verification_rejects_an_unconsumed_default_without_changing_it(self):
        producer = {
            "default_queue": "sourcelens",
            "declared_queues": ["sourcelens", "backend", "lens"],
            "required_queues": ["backend", "lens"],
        }
        with (
            patch.object(setup, "find_service", return_value={"Id": "a" * 64}),
            patch.object(
                setup,
                "read_queue_config",
                side_effect=[
                    producer,
                    dict(producer, worker_name="celery@fixture"),
                    producer,
                    {"worker_queues": ["backend", "lens"]},
                ],
            ),
            patch.object(setup, "configure") as configure,
            patch.object(setup, "disable_automatic_monitor") as disable,
        ):
            with self.assertRaises(RuntimeError):
                setup.verify_queue_config(self.root)
        configure.assert_not_called()
        disable.assert_not_called()

    def test_verification_waits_for_starting_worker_with_finite_deadline(self):
        producer = {
            "default_queue": "future",
            "declared_queues": ["future", "lens"],
            "required_queues": ["lens"],
        }
        with (
            patch.object(setup, "find_service", return_value={"Id": "a" * 64}),
            patch.object(
                setup,
                "read_queue_config",
                side_effect=[
                    producer,
                    dict(producer, worker_name="celery@fixture"),
                    producer,
                    {"worker_queues": []},
                    {"worker_queues": ["future", "lens"]},
                ],
            ),
            patch.object(setup.time, "sleep") as sleep,
        ):
            setup.verify_queue_config(self.root)
        sleep.assert_called_once()

    def test_verification_does_not_implicitly_accept_invalid_mode(self):
        (self.root / ".env").write_text("SOURCELENS_MODE=invalid\n")
        with patch.object(setup, "find_service") as find:
            with self.assertRaises(RuntimeError):
                setup.verify_queue_config(self.root)
        find.assert_not_called()

    def test_unresponsive_verification_stops_at_deadline(self):
        producer = {
            "default_queue": "future",
            "declared_queues": ["future"],
            "required_queues": [],
        }
        with (
            patch.object(setup, "find_service", return_value={"Id": "a" * 64}),
            patch.object(
                setup,
                "read_queue_config",
                side_effect=[
                    producer,
                    dict(producer, worker_name="celery@fixture"),
                    producer,
                    {"worker_queues": []},
                ],
            ) as read,
            patch.object(setup.time, "monotonic", side_effect=[0, 0.1, 0.2, 2]),
            patch.object(setup.time, "sleep"),
        ):
            with self.assertRaises(RuntimeError):
                setup.verify_queue_config(self.root, timeout=1)
        self.assertEqual(read.call_count, 4)

    def test_read_only_verification_does_not_change_monitoring_or_consumers(self):
        producers = [
            {
                "default_queue": "backend",
                "declared_queues": ["backend", "lens"],
                "required_queues": ["lens"],
            },
            {
                "default_queue": "backend",
                "declared_queues": ["backend", "lens"],
                "required_queues": ["lens"],
                "worker_name": "celery@fixture",
            },
            {
                "default_queue": "backend",
                "declared_queues": ["backend", "lens"],
                "required_queues": ["lens"],
            },
            {"worker_queues": ["backend", "lens"]},
        ]
        with (
            patch.object(setup, "find_service", return_value={"Id": "a" * 64}),
            patch.object(setup, "read_queue_config", side_effect=producers),
            patch.object(setup, "configure") as configure,
            patch.object(setup, "disable_automatic_monitor") as disable,
        ):
            setup.verify_queue_config(self.root)
        configure.assert_not_called()
        disable.assert_not_called()
        self.assertFalse(self.calls)
        self.assertFalse(setup.runtime_config_path(self.root).exists())

    def test_failed_verification_is_nonzero_and_never_disables_monitoring(self):
        import io
        from contextlib import redirect_stdout

        output = io.StringIO()
        with (
            patch("sys.argv", ["helper", "--root", str(self.root), "--verify-queues"]),
            patch.object(
                setup, "verify_queue_config", side_effect=RuntimeError("secret")
            ),
            patch.object(setup, "disable_automatic_monitor") as disable,
            redirect_stdout(output),
        ):
            with self.assertRaises(SystemExit) as error:
                setup.main()
        self.assertEqual(error.exception.code, 1)
        self.assertNotIn("secret", output.getvalue())
        disable.assert_not_called()

    def test_external_verification_does_not_inspect_or_change_services(self):
        env = self.root / ".env"
        env.write_text(
            env.read_text().replace(
                "SOURCELENS_MODE=bundled", "SOURCELENS_MODE=external"
            )
        )
        with patch.object(setup, "find_service") as find:
            setup.verify_queue_config(self.root)
        find.assert_not_called()

    def test_verification_discovery_respects_deadline(self):
        with (
            patch.object(setup.time, "monotonic", return_value=10),
            patch.object(setup, "docker") as docker,
        ):
            with self.assertRaises(RuntimeError):
                setup.find_service(self.root, "api", deadline=9)
        docker.assert_not_called()

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
        runtime_path = setup.runtime_config_path(self.root)
        self.assertEqual(
            json.loads(runtime_path.read_text())["url"],
            "redis://hfl-sourcelens-redis:6379/0",
        )
        self.assertEqual(runtime_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(runtime_path.parent.stat().st_mode & 0o777, 0o700)

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

    def test_transient_discovery_failure_preserves_previous_setup(self):
        import io
        from contextlib import redirect_stdout

        self.configure()
        env_before = (self.root / ".env").read_bytes()
        runtime_before = setup.runtime_config_path(self.root).read_bytes()
        marker = self.root / "deploy/sl-queue-monitor.json"
        marker_before = marker.read_bytes()
        real = self.docker

        def unavailable(*args):
            if args[0] == "ps" and "label=com.docker.compose.service=api" in args:
                raise RuntimeError("temporary daemon error with secret")
            return real(*args)

        output = io.StringIO()
        with (
            patch("sys.argv", ["helper", "--root", str(self.root)]),
            patch.object(
                setup,
                "docker",
                side_effect=unavailable,
            ),
            redirect_stdout(output),
        ):
            setup.main()
        self.assertEqual((self.root / ".env").read_bytes(), env_before)
        self.assertEqual(
            setup.runtime_config_path(self.root).read_bytes(), runtime_before
        )
        self.assertEqual(marker.read_bytes(), marker_before)
        self.assertIn("a" * 64, self.members)
        self.assertIn("existing setup retained", output.getvalue())
        self.assertNotIn("secret", output.getvalue())

    def test_hot_publication_failure_keeps_existing_env_and_runtime_unchanged(self):
        self.configure()
        env_before = (self.root / ".env").read_bytes()
        runtime_before = setup.runtime_config_path(self.root).read_bytes()
        marker_before = (self.root / "deploy/sl-queue-monitor.json").read_bytes()
        self.items["b" * 64]["Config"]["Env"] = [
            "CELERY_BROKER_URL=redis://redis:6379/2",
        ]
        with (
            patch.object(setup, "docker", side_effect=self.docker),
            patch.object(
                setup,
                "write_runtime_url",
                side_effect=OSError("temporary publication failure"),
            ),
        ):
            with self.assertRaises(OSError):
                setup.configure(self.root)
        self.assertEqual((self.root / ".env").read_bytes(), env_before)
        self.assertEqual(
            setup.runtime_config_path(self.root).read_bytes(), runtime_before
        )
        self.assertEqual(
            (self.root / "deploy/sl-queue-monitor.json").read_bytes(), marker_before
        )
        self.assertIn("a" * 64, self.members)

    def test_first_hot_publication_failure_rolls_back_new_connection_without_env_change(
        self,
    ):
        env_before = (self.root / ".env").read_bytes()
        with (
            patch.object(setup, "docker", side_effect=self.docker),
            patch.object(
                setup,
                "write_runtime_url",
                side_effect=OSError("temporary publication failure"),
            ),
        ):
            with self.assertRaises(OSError):
                setup.configure(self.root)
        self.assertEqual((self.root / ".env").read_bytes(), env_before)
        self.assertFalse(setup.runtime_config_path(self.root).exists())
        self.assertFalse(self.members)
        self.assertIn("private-sl", self.items["a" * 64]["NetworkSettings"]["Networks"])
        self.assertFalse((self.root / "deploy/sl-queue-monitor.json").exists())

    def test_compatibility_env_failure_keeps_successfully_published_hot_endpoint(self):
        with (
            patch.object(setup, "docker", side_effect=self.docker),
            patch.object(
                setup,
                "write_auto_url",
                side_effect=OSError("temporary env-file failure"),
            ),
        ):
            with self.assertRaises(OSError):
                setup.configure(self.root)
        self.assertEqual(
            json.loads(setup.runtime_config_path(self.root).read_text())["url"],
            "redis://hfl-sourcelens-redis:6379/0",
        )
        self.assertIn("a" * 64, self.members)
        self.assertTrue((self.root / "deploy/sl-queue-monitor.json").exists())
        self.assertEqual(setup.env_value(self.root / ".env", setup.AUTO_KEY), "")

    def test_failed_new_attachment_rolls_back_without_tearing_down_private_network(
        self,
    ):
        import io
        from contextlib import redirect_stdout

        real = self.docker

        def timeout_after_connect(*args):
            result = real(*args)
            if args[:2] == ("network", "connect"):
                raise RuntimeError("daemon timeout after attaching")
            return result

        with (
            patch("sys.argv", ["helper", "--root", str(self.root)]),
            patch.object(
                setup,
                "docker",
                side_effect=timeout_after_connect,
            ),
            redirect_stdout(io.StringIO()),
        ):
            setup.main()
        self.assertFalse(self.members)
        self.assertIn("private-sl", self.items["a" * 64]["NetworkSettings"]["Networks"])
        self.assertFalse((self.root / "deploy/sl-queue-monitor.json").exists())

    def test_definitive_security_rejection_disables_owned_setup(self):
        import io
        from contextlib import redirect_stdout

        self.configure()
        self.items["d" * 64] = self.item("d" * 64, "lensnode")
        self.members["d" * 64] = {}
        output = io.StringIO()
        with (
            patch("sys.argv", ["helper", "--root", str(self.root)]),
            patch.object(
                setup,
                "docker",
                side_effect=self.docker,
            ),
            redirect_stdout(output),
        ):
            setup.main()
        self.assertNotIn("a" * 64, self.members)
        self.assertIn("d" * 64, self.members)
        self.assertEqual(setup.env_value(self.root / ".env", setup.AUTO_KEY), "")
        self.assertEqual(
            json.loads(setup.runtime_config_path(self.root).read_text())["url"], ""
        )
        self.assertIn("unsafe automatic setup disabled", output.getvalue())

    def test_safety_cleanup_still_detaches_if_runtime_publication_fails(self):
        self.configure()
        with (
            patch.object(setup, "docker", side_effect=self.docker),
            patch.object(
                setup,
                "write_runtime_url",
                side_effect=OSError("temporary write failure"),
            ),
        ):
            with self.assertRaises(OSError):
                setup.disable_automatic_monitor(self.root)
        self.assertNotIn("a" * 64, self.members)
        self.assertEqual(setup.env_value(self.root / ".env", setup.AUTO_KEY), "")

    def test_broker_upgrade_publishes_hot_configuration_without_restart(self):
        self.configure()
        path = setup.runtime_config_path(self.root)
        self.assertTrue(json.loads(path.read_text())["url"].endswith("/0"))
        self.items["b" * 64]["Config"]["Env"] = [
            "REDIS_URL=redis://user:new-secret@redis:6379/2"
        ]
        self.configure()
        self.assertEqual(
            json.loads(path.read_text())["url"],
            "redis://user:new-secret@hfl-sourcelens-redis:6379/2",
        )
        self.assertFalse(any(c[0] in ("restart", "stop", "exec") for c in self.calls))
        self.assertNotIn(
            "new-secret", (self.root / "deploy/sl-queue-monitor.json").read_text()
        )
        self.configure(remove=True)
        self.assertEqual(json.loads(path.read_text())["url"], "")

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
        for path in ("deploy/docker-compose.yml", "docker-compose.yml"):
            compose = (ROOT / path).read_text()
            self.assertIn("./data/runtime:/opt/hyperfilelens/runtime:ro", compose)
        dev = (ROOT / "dev/stack.sh").read_text()
        for start, end in (
            ("cmd_up() {", "cmd_down() {"),
            ("cmd_restart() {", "cmd_status() {"),
        ):
            block = dev.split(start, 1)[1].split(end, 1)[0]
            self.assertLess(
                block.index("prepare_sourcelens_dev"),
                block.index("configure_sl_queue_monitor_dev"),
            )
            self.assertLess(
                block.index("configure_sl_queue_monitor_dev"),
                block.index("prepare_dev"),
            )


if __name__ == "__main__":
    unittest.main()
