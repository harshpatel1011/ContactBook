from django.core.management.base import BaseCommand
from django.db import connection
from django.conf import settings


class Command(BaseCommand):
    help = "Creates this project's Postgres schema if it doesn't already exist"

    def handle(self, *args, **options):
        schema_name = getattr(settings, "DB_SCHEMA", None)
        if not schema_name:
            self.stdout.write(self.style.WARNING("DB_SCHEMA not set, skipping."))
            return
        with connection.cursor() as cursor:
            cursor.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"')
        self.stdout.write(self.style.SUCCESS(f'Schema "{schema_name}" is ready.'))
