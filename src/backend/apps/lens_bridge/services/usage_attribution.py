"""Keep known consumption attribution before a user FK becomes NULL."""

from apps.lens_bridge.models import LensUsageLedger


def preserve_user_usage_before_deletion(sender, instance, using, **kwargs):
    # Also covers historical/old-worker/bulk-created rows without a snapshot.
    # Truly unattributed history is not guessed or silently discarded.
    LensUsageLedger.objects.using(using).filter(
        hfl_user_id=instance.pk, billing_user_id__isnull=True,
    ).update(billing_user_id=instance.pk)
