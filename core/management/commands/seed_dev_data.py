from django.core.management import call_command
from django.core.management.base import BaseCommand

from core.models import Church, ParticipantProfile, Role
from core.services import assign_role, enroll_participant_in_path
from django.contrib.auth.models import User
from django.utils import timezone


class Command(BaseCommand):
    help = "Create local-only demo data. Do not use as the production setup path."

    def handle(self, *args, **options):
        church, _ = Church.objects.get_or_create(
            slug="emmaus-demo",
            defaults={
                "name": "Emmaus Demo Church",
                "contact_email": "hello@example.org",
                "public_welcome_text": "Welcome to the Emmaus demo workspace.",
            },
        )
        people = {
            "admin@example.org": ("Admin", "User", Role.CHURCH_ADMIN),
            "pastor@example.org": ("Pastor", "User", Role.PASTOR),
            "mentor@example.org": ("Mentor", "User", Role.MENTOR),
            "participant@example.org": ("Participant", "User", Role.PARTICIPANT),
        }
        users = {}
        for email, (first_name, last_name, role) in people.items():
            user, _ = User.objects.get_or_create(
                username=email,
                defaults={"email": email, "first_name": first_name, "last_name": last_name},
            )
            user.email = email
            user.first_name = first_name
            user.last_name = last_name
            user.set_password("password12345")
            user.is_staff = role in [Role.CHURCH_ADMIN, Role.PASTOR]
            user.save()
            assign_role(user, church, role)
            users[role] = user
        call_command("create_starter_path", church_slug=church.slug)
        profile, _ = ParticipantProfile.objects.get_or_create(
            user=users[Role.PARTICIPANT],
            defaults={
                "church": church,
                "assigned_mentor": users[Role.MENTOR],
                "preferred_contact_method": ParticipantProfile.ContactMethod.EMAIL,
                "consent_accepted_at": timezone.now(),
                "emergency_disclaimer_accepted_at": timezone.now(),
            },
        )
        profile.assigned_mentor = users[Role.MENTOR]
        profile.save()
        enroll_participant_in_path(users[Role.PARTICIPANT], church.paths.filter(is_default=True).first())
        self.stdout.write(self.style.SUCCESS("Demo users ready. Password for all demo accounts: password12345"))
