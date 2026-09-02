import signal
import subprocess
from subprocess import CompletedProcess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, call, patch

from django.test import SimpleTestCase

from apps.storage.repositories.models import Repository
from apps.storage.services.internal.kopia_cli import (
    KopiaCliCancelled,
    KopiaCliError,
    KopiaControlDecision,
    KopiaProcessTerminatedError,
    KopiaRepositoryAlreadyExistsError,
    _connection_fingerprint_file,
    _invalidate_changed_s3_connection,
    _run_repository_command_unlocked,
    _s3_flags,
    _terminate_process_group,
    create_s3_repository,
    delete_s3_snapshots,
    run_maintenance,
)


class KopiaS3URLStyleCommandTests(SimpleTestCase):
    @patch("apps.storage.services.internal.kopia_cli._kopia_path", return_value="/usr/local/bin/kopia")
    @patch("apps.storage.services.internal.kopia_cli._kopia_supports_s3_url_style", return_value=True)
    def test_huaweicloud_virtual_hosted_style_uses_patched_flag(self, _supports, _path):
        repository = Repository(
            repo_type=Repository.Type.S3,
            s3_platform=Repository.S3Platform.HUAWEICLOUD,
            s3_bucket="bucket",
            config={"endpoint": "obs.cn-north-5.myhuaweicloud.com"},
        )

        self.assertIn("--url-style=virtual-hosted", _s3_flags(repository))

    @patch("apps.storage.services.internal.kopia_cli._kopia_path", return_value="/usr/local/bin/kopia")
    @patch("apps.storage.services.internal.kopia_cli._kopia_supports_s3_url_style", return_value=True)
    def test_controller_kopia_uses_snapshotted_external_endpoint(
        self, _supports, _path
    ):
        repository = Repository(
            repo_type=Repository.Type.S3,
            s3_platform=Repository.S3Platform.ALIYUN,
            s3_bucket="bucket",
            config={
                "endpoint": "oss-cn-hangzhou.aliyuncs.com",
                "external_endpoint": "oss-cn-hangzhou.aliyuncs.com",
                "internal_endpoint": "oss-cn-hangzhou-internal.aliyuncs.com",
                "s3_url_style": "virtual_hosted",
            },
        )

        flags = _s3_flags(repository)

        self.assertIn("--endpoint=oss-cn-hangzhou.aliyuncs.com", flags)
        self.assertNotIn("--endpoint=oss-cn-hangzhou-internal.aliyuncs.com", flags)

    @patch("apps.storage.services.internal.kopia_cli._kopia_path", return_value="/usr/bin/kopia")
    @patch("apps.storage.services.internal.kopia_cli._kopia_supports_s3_url_style", return_value=False)
    def test_official_binary_rejects_virtual_hosted_requirement(self, _supports, _path):
        repository = Repository(
            repo_type=Repository.Type.S3,
            s3_platform=Repository.S3Platform.HUAWEICLOUD,
            s3_bucket="bucket",
            config={"s3_url_style": "virtual_hosted"},
        )

        with self.assertRaisesMessage(KopiaCliError, "does not support --url-style"):
            _s3_flags(repository)

    @patch(
        "apps.storage.services.internal.kopia_cli._s3_connection_fingerprint",
        return_value="new-fingerprint",
    )
    def test_changed_connection_fingerprint_invalidates_local_config(self, _fingerprint):
        repository = Repository(repo_type=Repository.Type.S3)
        with TemporaryDirectory() as temporary:
            config_file = Path(temporary) / "repository.config"
            config_file.write_text("configured", encoding="utf-8")
            _connection_fingerprint_file(config_file).write_text(
                "old-fingerprint\n", encoding="utf-8"
            )

            _invalidate_changed_s3_connection(repository, config_file)

            self.assertFalse(config_file.exists())


class KopiaRepositoryCreateCommandTests(SimpleTestCase):
    @patch("apps.storage.services.internal.kopia_cli._run_repository_command")
    def test_existing_repository_is_rejected_without_connect(self, run_command):
        run_command.return_value = CompletedProcess(
            [],
            1,
            stdout="",
            stderr="repository already exists in the provided storage",
        )
        repository = Repository(
            id=52,
            name="S3 repository",
            repo_type=Repository.Type.S3,
            s3_bucket="bucket",
            config={"endpoint": "s3.example.test", "prefix": "repo/"},
        )

        with self.assertRaises(KopiaRepositoryAlreadyExistsError):
            create_s3_repository(repository)

        self.assertEqual(run_command.call_count, 1)
        self.assertEqual(
            run_command.call_args.args[1][:3],
            ["repository", "create", "s3"],
        )

    @patch("apps.storage.services.internal.kopia_cli._run_repository_command")
    def test_prefix_with_ownership_marker_is_rejected_as_existing(self, run_command):
        run_command.return_value = CompletedProcess(
            [],
            1,
            stdout="",
            stderr="unable to get repository storage: found existing data in storage location",
        )
        repository = Repository(
            id=53,
            name="S3 repository",
            repo_type=Repository.Type.S3,
            s3_bucket="bucket",
            config={"endpoint": "s3.example.test", "prefix": "hfl/"},
        )

        with self.assertRaises(KopiaRepositoryAlreadyExistsError):
            create_s3_repository(repository)

        self.assertEqual(run_command.call_count, 1)


class KopiaSnapshotDeleteCommandTests(SimpleTestCase):
    @patch(
        "apps.storage.services.internal.repository_ownership.verify_s3_repository_ownership"
    )
    @patch("apps.storage.services.internal.kopia_cli._connect_maintenance_repository")
    @patch("apps.storage.services.internal.kopia_cli._run_repository_command")
    def test_controller_fallback_deletes_only_requested_s3_snapshots(
        self,
        run_command,
        connect_repository,
        verify_ownership,
    ):
        repository = Repository(
            id=7,
            repo_type=Repository.Type.S3,
            s3_bucket="bucket",
            config={"secret_access_key": "secret-value"},
        )
        run_command.side_effect = [
            CompletedProcess([], 0, stdout="deleted secret-value", stderr=""),
            CompletedProcess(
                [],
                1,
                stdout="",
                stderr="no snapshots matched secret-value",
            ),
        ]

        with TemporaryDirectory() as temporary, patch.dict(
            "os.environ", {"HFL_KOPIA_CONFIG_DIR": temporary}
        ):
            result = delete_s3_snapshots(
                repository,
                snapshot_ids=["snapshot-a", "snapshot-b"],
                timeout_seconds=45,
            )

        connect_repository.assert_called_once()
        verify_ownership.assert_called_once_with(repository, adopt_legacy=False)
        self.assertEqual(result["deleted_count"], 1)
        self.assertEqual(result["failed_count"], 1)
        self.assertEqual(result["execution_mode"], "controller_fallback")
        delete_result = result["results"][0]["delete"]
        self.assertNotIn("stdout", delete_result)
        self.assertNotIn("stderr", delete_result)
        self.assertEqual(delete_result["stdout_tail"], "deleted ******")
        self.assertEqual(
            result["results"][1]["delete"]["stderr_tail"],
            "no snapshots matched ******",
        )
        self.assertNotIn("secret-value", str(result))
        self.assertEqual(
            [call.args[1] for call in run_command.call_args_list],
            [
                ["snapshot", "delete", "snapshot-a", "--delete"],
                ["snapshot", "delete", "snapshot-b", "--delete"],
            ],
        )

    def test_controller_fallback_rejects_non_s3_repository(self):
        repository = Repository(
            id=8,
            repo_type=Repository.Type.NAS,
            config={},
        )

        with self.assertRaisesMessage(
            KopiaCliError,
            "Control-plane snapshot delete only supports S3 repositories",
        ):
            delete_s3_snapshots(repository, snapshot_ids=["snapshot-a"])


class KopiaMaintenanceCommandTests(SimpleTestCase):
    @patch(
        "apps.storage.services.internal.repository_ownership.verify_s3_repository_ownership"
    )
    @patch("apps.storage.services.internal.kopia_cli._run_repository_command")
    def test_uses_dedicated_config_and_set_client_identity(
        self, run_command, verify_ownership
    ):
        run_command.return_value = CompletedProcess([], 0, stdout="", stderr="")
        repository = Repository(
            id=52,
            name="S3 repository",
            repo_type=Repository.Type.S3,
            s3_bucket="bucket",
            config={"endpoint": "s3.example.test", "prefix": "repo/"},
        )

        run_maintenance(
            repository,
            full=False,
            owner_identity="hfl-maintenance@controller",
            timeout_seconds=300,
        )

        verify_ownership.assert_called_once_with(repository, adopt_legacy=False)

        commands = [call.args[1] for call in run_command.call_args_list]
        self.assertEqual(commands[0][:3], ["repository", "connect", "s3"])
        self.assertEqual(
            commands[1],
            [
                "repository",
                "set-client",
                "--username=hfl-maintenance",
                "--hostname=controller",
            ],
        )
        self.assertEqual(
            commands[2],
            [
                "maintenance",
                "set",
                "--owner=hfl-maintenance@controller",
                "--enable-quick=false",
                "--enable-full=false",
            ],
        )
        self.assertEqual(commands[3], ["maintenance", "run"])
        self.assertEqual(commands[4], ["maintenance", "info", "--json"])
        self.assertIn("--region=us-east-1", commands[0])
        self.assertFalse(any("override-username" in arg for command in commands for arg in command))
        config_files = [call.kwargs["config_file"] for call in run_command.call_args_list]
        self.assertEqual(len(set(config_files)), 1)
        self.assertEqual(config_files[0].name, "maintenance.repository.config")

    @patch(
        "apps.storage.services.internal.repository_ownership.verify_s3_repository_ownership"
    )
    @patch("apps.storage.services.internal.kopia_cli.time.sleep")
    @patch("apps.storage.services.internal.kopia_cli._run_repository_command")
    def test_retries_failed_repository_connection(
        self, run_command, sleep, verify_ownership
    ):
        failed = CompletedProcess([], 1, stdout="", stderr="Connection closed by foreign host. Retry again.")
        succeeded = CompletedProcess([], 0, stdout="", stderr="")
        run_command.side_effect = [
            failed,
            failed,
            succeeded,
            succeeded,
            succeeded,
            succeeded,
            succeeded,
        ]
        repository = Repository(
            id=52,
            name="S3 repository",
            repo_type=Repository.Type.S3,
            s3_bucket="bucket",
            config={"endpoint": "minio.example.test", "prefix": "repo/"},
        )

        run_maintenance(
            repository,
            full=False,
            owner_identity="hfl-maintenance@controller",
            timeout_seconds=300,
        )

        verify_ownership.assert_called_once_with(repository, adopt_legacy=False)

        commands = [call.args[1] for call in run_command.call_args_list]
        self.assertEqual(commands[0][:3], ["repository", "connect", "s3"])
        self.assertEqual(commands[1], ["repository", "status"])
        self.assertEqual(commands[2][:3], ["repository", "connect", "s3"])
        sleep.assert_called_once_with(1)

    @patch("apps.storage.services.internal.kopia_cli._environment", return_value={})
    @patch("apps.storage.services.internal.kopia_cli._invalidate_changed_s3_connection")
    @patch("apps.storage.services.internal.kopia_cli._kopia_path", return_value="/usr/bin/kopia")
    @patch("apps.storage.services.internal.kopia_cli.os.killpg")
    @patch("apps.storage.services.internal.kopia_cli.subprocess.Popen")
    def test_cancellation_terminates_the_kopia_process_group(
        self,
        popen,
        killpg,
        _kopia_path,
        _invalidate,
        _environment,
    ):
        process = popen.return_value
        process.pid = 4321
        process.poll.side_effect = [None, -signal.SIGTERM]
        process.communicate.side_effect = subprocess.TimeoutExpired("kopia", 0.5)
        process.wait.return_value = 0
        decisions = iter(
            [KopiaControlDecision.CONTINUE, KopiaControlDecision.CANCEL]
        )

        with self.assertRaises(KopiaCliCancelled):
            _run_repository_command_unlocked(
                Repository(id=52, repo_type=Repository.Type.S3),
                ["maintenance", "run"],
                timeout_seconds=300,
                config_file=Path("/tmp/kopia-cancel-test.config"),
                control=lambda: next(decisions),
            )

        popen.assert_called_once()
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        killpg.assert_called_once_with(4321, signal.SIGTERM)

    @patch("apps.storage.services.internal.kopia_cli._environment", return_value={})
    @patch("apps.storage.services.internal.kopia_cli._invalidate_changed_s3_connection")
    @patch(
        "apps.storage.services.internal.kopia_cli._kopia_path",
        return_value="/usr/bin/kopia",
    )
    @patch("apps.storage.services.internal.kopia_cli.subprocess.Popen")
    def test_signal_terminated_process_has_a_distinct_error(
        self,
        popen,
        _kopia_path,
        _invalidate,
        _environment,
    ):
        process = popen.return_value
        process.returncode = -signal.SIGKILL
        process.communicate.return_value = ("", "")
        process.poll.return_value = -signal.SIGKILL

        with self.assertRaises(KopiaProcessTerminatedError) as raised:
            _run_repository_command_unlocked(
                Repository(id=52, repo_type=Repository.Type.S3),
                ["repository", "status"],
                timeout_seconds=300,
                config_file=Path("/tmp/kopia-signal-test.config"),
            )

        self.assertEqual(raised.exception.signal_number, signal.SIGKILL)

    @patch("apps.storage.services.internal.kopia_cli.os.killpg")
    def test_process_group_escalates_to_sigkill(self, killpg):
        process = MagicMock()
        process.pid = 9876
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("kopia", 5), 0]

        _terminate_process_group(process)

        self.assertEqual(
            killpg.call_args_list,
            [
                call(9876, signal.SIGTERM),
                call(9876, signal.SIGKILL),
            ],
        )
