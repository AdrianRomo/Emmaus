from django.core.management.base import BaseCommand, CommandError

from core.models import Church, DiscipleshipPath, Lesson, ReflectionQuestion


LESSONS = [
    {
        "title": "Who is Jesus?",
        "references": "John 1:1-14; Mark 8:27-30; Colossians 1:15-20",
        "body": (
            "Christian faith begins with Jesus himself. He is not merely a moral example or a distant religious "
            "teacher; he reveals God's heart and invites people to follow him with trust, repentance, and love. "
            "As you begin this path, pay attention to what Jesus says, what he does, and how people respond to him."
        ),
        "step": "Read one Gospel story this week and write down one thing Jesus reveals about God.",
        "questions": [
            "What have you believed about Jesus up to this point?",
            "What question about Jesus would you like to ask a church leader?",
        ],
    },
    {
        "title": "What is grace?",
        "references": "Ephesians 2:1-10; Luke 15:11-32; Romans 5:6-11",
        "body": (
            "Grace means God comes toward us with mercy before we can repair ourselves. It is not permission to "
            "stay unchanged, and it is not a reward for impressive religious effort. Grace receives us honestly and "
            "then begins to form us into people who live from gratitude."
        ),
        "step": "Name one place where you need to receive God's mercy instead of hiding or performing.",
        "questions": [
            "Where is it hard for you to believe grace is really a gift?",
            "How might receiving grace change the way you treat another person this week?",
        ],
    },
    {
        "title": "How do I pray?",
        "references": "Matthew 6:5-13; Psalm 13; Philippians 4:4-7",
        "body": (
            "Prayer is honest conversation with God. It can include worship, confession, gratitude, silence, grief, "
            "requests, and listening. You do not need polished words to begin. Jesus teaches his followers to come "
            "to God as Father and to bring ordinary needs into God's presence."
        ),
        "step": "Set aside five minutes each day to pray simply and honestly.",
        "questions": [
            "What feels natural or difficult when you try to pray?",
            "What would you like to bring to God this week?",
        ],
    },
    {
        "title": "How do I read the Bible?",
        "references": "Psalm 119:9-16; Luke 24:13-35; 2 Timothy 3:14-17",
        "body": (
            "The Bible is a library of writings that tells the story of God, creation, covenant, Jesus, and the life "
            "of God's people. Read slowly, with humility, and with help from the church. The goal is not to master a "
            "text for information only, but to meet God and be formed in wisdom."
        ),
        "step": "Read a short passage from a Gospel and ask: What does this show about God, people, and faithful response?",
        "questions": [
            "What has your experience with the Bible been like?",
            "What support would help you read Scripture with more confidence?",
        ],
    },
    {
        "title": "Why church matters",
        "references": "Acts 2:42-47; 1 Corinthians 12:12-27; Hebrews 10:19-25",
        "body": (
            "Following Jesus is personal, but it is not meant to be private or isolated. The church is a people who "
            "worship, learn, serve, confess, forgive, and bear burdens together. A healthy church helps us remember "
            "what is true when life is joyful, confusing, or painful."
        ),
        "step": "Attend a gathering or small group and introduce yourself to one leader or participant.",
        "questions": [
            "What hopes or hesitations do you have about belonging to a church?",
            "Where might you need encouragement from other Christians right now?",
        ],
    },
    {
        "title": "What is repentance?",
        "references": "Mark 1:14-15; Acts 3:19-21; 2 Corinthians 7:8-11",
        "body": (
            "Repentance is a grace-filled turning toward God. It includes telling the truth about sin, receiving "
            "mercy, and choosing a new direction with help from the Spirit and the church. Repentance is not "
            "self-hatred; it is returning home to the God who heals and leads."
        ),
        "step": "Ask God to show you one concrete step of turning toward him this week.",
        "questions": [
            "What is one area where you sense God inviting change?",
            "Who could support you wisely and safely as you take that step?",
        ],
    },
    {
        "title": "Walking with Jesus daily",
        "references": "John 15:1-11; Galatians 5:16-26; Micah 6:8",
        "body": (
            "Discipleship is a daily life of abiding in Jesus. It grows through ordinary practices: prayer, Scripture, "
            "worship, confession, service, rest, generosity, and love for neighbors. The path is not instant maturity; "
            "it is learning to return to Jesus again and again."
        ),
        "step": "Choose one simple weekly rhythm that helps you keep company with Jesus.",
        "questions": [
            "What daily or weekly rhythm would help you keep following Jesus?",
            "What would you like your mentor or pastor to pray about for you?",
        ],
    },
]


class Command(BaseCommand):
    help = "Create or update the editable starter path First Steps With Jesus."

    def add_arguments(self, parser):
        parser.add_argument("--church-slug", help="Church slug. Required if more than one church exists.")

    def handle(self, *args, **options):
        churches = Church.objects.all()
        if options.get("church_slug"):
            church = churches.filter(slug=options["church_slug"]).first()
            if not church:
                raise CommandError("No church found with that slug.")
        else:
            if churches.count() == 0:
                raise CommandError("Create a church first through the initial setup flow.")
            if churches.count() > 1:
                raise CommandError("Use --church-slug when more than one church exists.")
            church = churches.first()

        path, _ = DiscipleshipPath.objects.update_or_create(
            church=church,
            title="First Steps With Jesus",
            defaults={
                "description": "A seven-lesson introduction to following Jesus with the support of a local church.",
                "target_audience": "Visitors, seekers, new believers, and returning Christians",
                "is_active": True,
                "is_default": True,
            },
        )
        for index, lesson_data in enumerate(LESSONS, start=1):
            lesson, _ = Lesson.objects.update_or_create(
                path=path,
                order=index,
                defaults={
                    "title": lesson_data["title"],
                    "scripture_references": lesson_data["references"],
                    "body": lesson_data["body"],
                    "practical_step": lesson_data["step"],
                    "is_active": True,
                },
            )
            for question_index, question_text in enumerate(lesson_data["questions"], start=1):
                ReflectionQuestion.objects.update_or_create(
                    lesson=lesson,
                    order=question_index,
                    defaults={"question_text": question_text},
                )
        self.stdout.write(self.style.SUCCESS(f"Starter path ready for {church.name}."))
