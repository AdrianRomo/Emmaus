from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db.models import Q


class Command(BaseCommand):
    help = "Desactiva las cuentas de demostración (@example.org y @demo.invalid)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        users = User.objects.filter(Q(email__iendswith="@example.org") | Q(email__iendswith="@demo.invalid"), is_active=True)
        emails = list(users.values_list("email", flat=True))
        if options["dry_run"]:
            self.stdout.write("Se desactivarían: " + (", ".join(emails) or "ninguna"))
            return
        updated = users.update(is_active=False)
        self.stdout.write(self.style.SUCCESS(f"Cuentas desactivadas: {updated}"))
