"""
Creates test backup failure data for the development environment.

Usage:
    python manage.py create_test_failure_data [--email admin@hyperfilelens.com]
"""

import uuid
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.contrib.auth import get_user_model

from apps.iam.models import Membership
from apps.node.models.node import Node
from apps.storage.repositories.models import Repository
from apps.protection.models.backup_config import BackupConfig
from apps.source.models.source_pipeline import SourceBackupPipelineEntry
from apps.source.models.source_resource import SourceResource
from apps.task.models.task import Task
from apps.task.models.task_event import TaskEvent
from apps.task.models.task_step import TaskStep
from apps.task.models.task_resource import TaskResource

User = get_user_model()


class Command(BaseCommand):
    help = 'Create a failed backup task with structured failure_details for testing #361'

    def add_arguments(self, parser):
        parser.add_argument(
            '--email',
            default='admin@hyperfilelens.com',
            help='User email to associate test data with',
        )
        parser.add_argument(
            '--scenario',
            choices=('backup', 'source-unregister-batch'),
            default='backup',
            help='Test data scenario to create',
        )

    def handle(self, *args, **options):
        email = options['email']
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            self.stderr.write(f'User with email {email} not found')
            return

        membership = Membership.objects.filter(user=user, is_active=True).first()
        if not membership:
            self.stderr.write(f'User {email} has no active organization membership')
            return

        org = membership.organization
        org_id = org.id
        self.stdout.write(f'Using organization: {org.name} (id={org_id})')

        if options['scenario'] == 'source-unregister-batch':
            self.create_source_unregister_batch(org)
            return

        now = timezone.now()

        # 1. Create a test Node (Agent)
        node = Node.objects.create(
            organization=org,
            name='[TEST] dev-server-01',
            role='agent',
            installation_mode='system',
            status='active',
        )
        self.stdout.write(f'Created Node: {node.name} (id={node.id})')

        # 2. Create a test Repository
        repo = Repository.objects.create(
            organization_id=org_id,
            name='[TEST] Mock S3 Repository',
            repo_type='s3',
            status='created',
            health='online',
        )
        self.stdout.write(f'Created Repository: {repo.name} (id={repo.id})')

        # 3. Create a BackupConfig linking node → repo
        backup_config = BackupConfig.objects.create(
            organization_id=org_id,
            name='[TEST] Mock Backup Config',
            source_type='agent',
            source_ref_id=node.id,
            repository_id=repo.id,
            status='active',
        )
        self.stdout.write(f'Created BackupConfig: {backup_config.name} (id={backup_config.id})')

        # 3b. Create pipeline entry so the source shows on step 3 (start-backup)
        from apps.source.constants import PipelineStep
        pipeline_entry, _ = SourceBackupPipelineEntry.objects.get_or_create(
            organization=org,
            source_kind='agent',
            ref_id=node.id,
            defaults={
                'step': PipelineStep.READY,
                'source_name': node.name,
                'source_status': 'active',
                'source_availability': 'online',
                'last_backup_status': 'failed',
            },
        )
        # Force update step/last_backup_status even on existing entries
        pipeline_entry.step = PipelineStep.READY
        pipeline_entry.source_status = 'active'
        pipeline_entry.source_availability = 'online'
        pipeline_entry.last_backup_status = 'failed'
        pipeline_entry.save()
        self.stdout.write(f'Created/updated pipeline entry: {pipeline_entry.source_kind}:{pipeline_entry.ref_id} (step={pipeline_entry.step})')

        # 4. Create the failed Task
        task_uuid = str(uuid.uuid4())
        task_name = '[TEST] Failed backup · home/admin/documents'

        task = Task.objects.create(
            organization_id=org_id,
            task_uuid=task_uuid,
            task_type='backup',
            display_name=task_name,
            status='failed',
            progress=78,
            error_code='BACKUP_SOURCE_READ_FAILED',
            error_message='5 files could not be read from the backup source.',
            trigger_type='manual',
            current_step='finalize_snapshot',
            retry_count=2,
            recovery_attempt=0,
            started_at=now - timedelta(minutes=15),
            finished_at=now - timedelta(minutes=5),
            request_payload={
                'source_path': '/home/admin/documents',
                'source_display_name': 'home/admin/documents',
                'source_type': 'agent',
                'source_ref_id': node.id,
                'backup_config_id': backup_config.id,
            },
            result_payload={
                'outcome': 'failed',
                'source_path': '/home/admin/documents',
                'failure_details': {
                    'category': 'source_read_failed',
                    'total_count': 5,
                    'reported_count': 3,
                    'truncated': True,
                    'items': [
                        {'path': '/home/admin/documents/report.pdf', 'error': 'Permission denied', 'cause': 'permission_denied', 'item_type': 'file'},
                        {'path': '/home/admin/documents/data.db', 'error': 'Locked by another process', 'cause': 'file_locked', 'item_type': 'file'},
                        {'path': '/home/admin/documents/logs/', 'error': 'Directory unreadable', 'cause': 'unreadable_directory', 'item_type': 'directory'},
                    ],
                    'causes': [
                        {'code': 'permission_denied', 'item_type': 'file', 'count': 2},
                        {'code': 'file_locked', 'item_type': 'file', 'count': 1},
                        {'code': 'unreadable_directory', 'item_type': 'directory', 'count': 2},
                    ],
                    'remediation': [
                        'check_source_access',
                        'enable_skip_unreadable_files',
                        'retry_backup',
                    ],
                },
                'skipped_details': {
                    'category': 'source_items_skipped',
                    'count': 3,
                    'file_count': 2,
                    'directory_count': 1,
                    'special_count': 0,
                    'items': [
                        {'path': '/home/admin/documents/tmp/cache.db', 'error': 'Locked by database process'},
                        {'path': '/home/admin/documents/.cache/thumbnails', 'error': 'Locked by cache daemon'},
                    ],
                },
            },
        )
        self.stdout.write(f'Created Task: {task_name} (uuid={task_uuid})')

        # 5. Create TaskResource linking task to the source node
        TaskResource.objects.get_or_create(
            task=task,
            resource_type='backup_source',
            resource_subtype='agent',
            resource_id=node.id,
            defaults={'is_primary': True},
        )

        # Create steps
        step_data = [
            ('prepare_source', 'success', 100, 1),
            ('start_snapshot', 'success', 100, 2),
            ('transfer_data', 'success', 78, 3),
            ('finalize_snapshot', 'failed', 78, 4),
        ]
        for step_name, status, progress_val, idx in step_data:
            TaskStep.objects.create(
                task=task,
                step_index=idx,
                step_name=step_name,
                status=status,
                progress=progress_val,
                created_at=now - timedelta(minutes=15 - idx * 3),
            )

        # Create a failure event on the finalize_snapshot step
        finalize_step = task.steps.get(step_name='finalize_snapshot')
        TaskEvent.objects.create(
            task=task,
            step=finalize_step,
            seq=1,
            level='ERROR',
            message='Task finished with status failed',
            metadata={
                'error_code': 'BACKUP_SOURCE_READ_FAILED',
                'error_message': '5 files could not be read from the backup source.',
                'source_path': '/home/admin/documents',
                'failure_details': {
                    'category': 'source_read_failed',
                    'total_count': 5,
                    'reported_count': 3,
                    'truncated': True,
                    'items': [
                        {'path': '/home/admin/documents/report.pdf', 'error': 'Permission denied', 'cause': 'permission_denied', 'item_type': 'file'},
                        {'path': '/home/admin/documents/data.db', 'error': 'Locked by another process', 'cause': 'file_locked', 'item_type': 'file'},
                        {'path': '/home/admin/documents/logs/', 'error': 'Directory unreadable', 'cause': 'unreadable_directory', 'item_type': 'directory'},
                    ],
                    'causes': [
                        {'code': 'permission_denied', 'item_type': 'file', 'count': 2},
                        {'code': 'file_locked', 'item_type': 'file', 'count': 1},
                        {'code': 'unreadable_directory', 'item_type': 'directory', 'count': 2},
                    ],
                    'remediation': [
                        'check_source_access',
                        'enable_skip_unreadable_files',
                        'retry_backup',
                    ],
                },
                'skipped_details': {
                    'category': 'source_items_skipped',
                    'count': 3,
                    'file_count': 2,
                    'directory_count': 1,
                    'special_count': 0,
                    'items': [
                        {'path': '/home/admin/documents/tmp/cache.db', 'error': 'Locked by database process'},
                        {'path': '/home/admin/documents/.cache/thumbnails', 'error': 'Locked by cache daemon'},
                    ],
                },
            },
        )
        self.stdout.write('Created failure task events with structured failure_details')

        # Update pipeline entry to reference the task
        pipeline_entry.last_backup_task_id = task.id
        pipeline_entry.last_backup_status = 'failed'
        pipeline_entry.save()

        self.stdout.write(f'\nDone. Task UUID: {task_uuid}')
        self.stdout.write(f'Backup Config ID: {backup_config.id}')
        self.stdout.write(f'Node ID: {node.id}')
        self.stdout.write(f'Repository ID: {repo.id}')
        self.stdout.write('\nVisit: protection/backups?step=start-backup')

    def create_source_unregister_batch(self, org):
        """Create a mixed batch for testing consolidated unregister details."""
        now = timezone.now()
        resources = []
        for index in range(1, 4):
            resource = SourceResource.objects.create(
                organization=org,
                name=f'[TEST] Unregister source {index}',
                resource_type='nas',
                config={'path': f'/tmp/hfl-test-unregister-{index}'},
                status='remove_failed' if index > 1 else 'removed',
                mount_status='error' if index == 2 else 'unmounted',
                availability='offline',
            )
            resources.append(resource)

        outcomes = [
            {
                'status': 'success',
                'error_code': None,
                'error_message': None,
                'result_payload': {
                    'outcome': 'success', 'cleanup_complete': True,
                    'sources': [{'source_id': str(resources[0].id), 'source_name': resources[0].name}],
                },
            },
            {
                'status': 'failed',
                'error_code': 'SOURCE_UNREGISTER_CLEANUP_FAILED',
                'error_message': 'Unmount failed and the source remains registered.',
                'result_payload': {
                    'outcome': 'partial_success', 'cleanup_complete': False,
                    'reasons': [{'code': 'unmount_failed', 'detail': 'The NAS mount could not be removed.'}],
                    'suggestions': [{'code': 'force_cleanup', 'detail': 'Retry cleanup after verifying the Agent connection.'}],
                    'cleanup_failures': [{'source_id': str(resources[1].id), 'source_name': resources[1].name, 'detail': 'Unmount failed'}],
                    'retained_resources': [f'mount:{resources[1].id}'],
                    'sources': [{'source_id': str(resources[1].id), 'source_name': resources[1].name}],
                },
            },
            {
                'status': 'success',
                'error_code': 'SOURCE_UNREGISTER_PARTIAL',
                'error_message': 'Source removed with skipped cleanup items.',
                'result_payload': {
                    'outcome': 'partial_success', 'cleanup_complete': False,
                    'skipped_details': {'count': 2, 'items': [{'path': '/tmp/stale-mount', 'error': 'Still busy'}]},
                    'sources': [{'source_id': str(resources[2].id), 'source_name': resources[2].name}],
                },
            },
        ]
        for index, outcome in enumerate(outcomes, start=1):
            task = Task.objects.create(
                organization_id=org.id,
                task_type=Task.Type.SOURCE_UNREGISTER,
                display_name=f'[TEST] Batch unregister {index}',
                trigger_type=Task.TriggerType.MANUAL,
                progress=100,
                current_step='cleanup',
                started_at=now - timedelta(minutes=10 - index),
                finished_at=now - timedelta(minutes=8 - index),
                **outcome,
            )
            TaskResource.objects.create(
                task=task,
                resource_type=TaskResource.Type.BACKUP_SOURCE,
                resource_subtype='nas',
                resource_id=resources[index - 1].id,
                is_primary=True,
            )
            TaskStep.objects.create(task=task, step_index=1, step_name='cleanup', status='failed' if outcome['status'] == 'failed' else 'success', progress=100)
            self.stdout.write(f'Created unregister task {index}: {task.task_uuid} (source={resources[index - 1].id})')
        self.stdout.write('\nBatch scenario ready. Open the Sources page and use the test source rows, or inspect these tasks from Task List.')
