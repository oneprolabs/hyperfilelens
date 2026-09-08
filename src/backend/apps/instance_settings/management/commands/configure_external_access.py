"""Configure the Community external-access URL."""

from django.core.management.base import BaseCommand, CommandError

from apps.instance_settings.services.external_access import set_external_access_url
from common.deploy.product import COMMUNITY_EDITION, product_edition


class Command(BaseCommand):
    help = "Set or clear the Community external access URL."

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--url", help="Canonical HTTP(S) tenant origin")
        group.add_argument(
            "--clear",
            action="store_true",
            help="Restore the deployment-provided URL",
        )

    def handle(self, *args, **options):
        if product_edition() != COMMUNITY_EDITION:
            raise CommandError(
                "External access is managed by Enterprise deployment configuration."
            )
        try:
            value = set_external_access_url("" if options["clear"] else options["url"])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        if value:
            self.stdout.write(self.style.SUCCESS(f"External access URL set to {value}"))
        else:
            self.stdout.write(self.style.SUCCESS("External access URL override cleared"))
