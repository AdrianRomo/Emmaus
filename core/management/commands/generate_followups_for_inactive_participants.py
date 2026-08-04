from django.core.management.base import BaseCommand

from core.models import ParticipantProfile
from core.services import create_inactivity_follow_up


class Command(BaseCommand):
    help = "Create follow-up requests for participants with no activity for 7+ days."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=7)

    def handle(self, *args, **options):
        created = 0
        for profile in ParticipantProfile.objects.select_related("user", "church", "assigned_mentor"):
            follow_up = create_inactivity_follow_up(profile, days=options["days"])
            if follow_up:
                created += 1
        self.stdout.write(self.style.SUCCESS(f"Created {created} inactivity follow-up request(s)."))
