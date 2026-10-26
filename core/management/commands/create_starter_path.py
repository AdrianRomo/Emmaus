from django.core.management.base import BaseCommand, CommandError

from core.models import Church, DiscipleshipPath, Lesson, ReflectionQuestion


STARTER_TITLE = "Primeros pasos con Jesús"
# Earlier releases created the starter path in English; update it in place instead of duplicating it.
LEGACY_STARTER_TITLES = ["First Steps With Jesus"]

LESSONS = [
    {
        "title": "¿Quién es Jesús?",
        "references": "Juan 1:1-14; Marcos 8:27-30; Colosenses 1:15-20",
        "body": (
            "La fe cristiana comienza con Jesús mismo. No es solo un ejemplo moral ni un maestro religioso lejano: "
            "él nos muestra el corazón de Dios e invita a las personas a seguirlo con confianza, arrepentimiento y "
            "amor. Al comenzar esta ruta, pon atención a lo que Jesús dice, a lo que hace y a cómo responde la gente "
            "ante él."
        ),
        "step": "Lee una historia de los Evangelios esta semana y anota una cosa que Jesús te muestra sobre Dios.",
        "questions": [
            "¿Qué has creído sobre Jesús hasta ahora?",
            "¿Qué pregunta sobre Jesús te gustaría hacerle a un líder de tu iglesia?",
        ],
    },
    {
        "title": "¿Qué es la gracia?",
        "references": "Efesios 2:1-10; Lucas 15:11-32; Romanos 5:6-11",
        "body": (
            "La gracia significa que Dios se acerca a nosotros con misericordia antes de que podamos arreglarnos por "
            "nuestra cuenta. No es un permiso para seguir igual, ni un premio por un gran esfuerzo religioso. La "
            "gracia nos recibe tal como somos y luego empieza a formarnos como personas que viven desde la gratitud."
        ),
        "step": "Identifica un área en la que necesitas recibir la misericordia de Dios en lugar de esconderte o aparentar.",
        "questions": [
            "¿En qué te cuesta creer que la gracia de verdad es un regalo?",
            "¿Cómo podría cambiar tu forma de tratar a otra persona esta semana si recibes la gracia?",
        ],
    },
    {
        "title": "¿Cómo oro?",
        "references": "Mateo 6:5-13; Salmo 13; Filipenses 4:4-7",
        "body": (
            "Orar es platicar con Dios con honestidad. La oración puede incluir adoración, confesión, gratitud, "
            "silencio, dolor, peticiones y escucha. No necesitas palabras perfectas para empezar. Jesús enseña a sus "
            "seguidores a acercarse a Dios como Padre y a llevar sus necesidades de todos los días a su presencia."
        ),
        "step": "Aparta cinco minutos cada día para orar de forma sencilla y honesta.",
        "questions": [
            "¿Qué se te hace natural o difícil cuando intentas orar?",
            "¿Qué te gustaría presentarle a Dios esta semana?",
        ],
    },
    {
        "title": "¿Cómo leo la Biblia?",
        "references": "Salmo 119:9-16; Lucas 24:13-35; 2 Timoteo 3:14-17",
        "body": (
            "La Biblia es una biblioteca de escritos que cuenta la historia de Dios, la creación, el pacto, Jesús y la "
            "vida del pueblo de Dios. Léela despacio, con humildad y con la ayuda de la iglesia. La meta no es dominar "
            "un texto solo para tener información, sino encontrarte con Dios y crecer en sabiduría."
        ),
        "step": (
            "Lee un pasaje corto de un Evangelio y pregúntate: ¿qué muestra sobre Dios, sobre las personas y sobre "
            "cómo responder con fidelidad?"
        ),
        "questions": [
            "¿Cómo ha sido tu experiencia con la Biblia?",
            "¿Qué apoyo te ayudaría a leer las Escrituras con más confianza?",
        ],
    },
    {
        "title": "Por qué importa la iglesia",
        "references": "Hechos 2:42-47; 1 Corintios 12:12-27; Hebreos 10:19-25",
        "body": (
            "Seguir a Jesús es algo personal, pero no está pensado para vivirse a solas ni en aislamiento. La iglesia "
            "es un pueblo que adora, aprende, sirve, confiesa, perdona y lleva las cargas en comunidad. Una iglesia "
            "sana nos ayuda a recordar lo que es verdad cuando la vida es alegre, confusa o dolorosa."
        ),
        "step": "Asiste a una reunión o a un grupo pequeño y preséntate con un líder o con otro participante.",
        "questions": [
            "¿Qué esperanzas o dudas tienes sobre pertenecer a una iglesia?",
            "¿En qué área podrías necesitar el ánimo de otros cristianos en este momento?",
        ],
    },
    {
        "title": "¿Qué es el arrepentimiento?",
        "references": "Marcos 1:14-15; Hechos 3:19-21; 2 Corintios 7:8-11",
        "body": (
            "El arrepentimiento es volverse hacia Dios sostenidos por su gracia. Incluye decir la verdad sobre el "
            "pecado, recibir misericordia y elegir una nueva dirección con la ayuda del Espíritu y de la iglesia. "
            "Arrepentirse no es odiarse a uno mismo; es volver a casa, al Dios que sana y guía."
        ),
        "step": "Pídele a Dios que te muestre un paso concreto para volverte hacia él esta semana.",
        "questions": [
            "¿En qué área sientes que Dios te invita a cambiar?",
            "¿Quién podría acompañarte con sabiduría y de forma segura al dar ese paso?",
        ],
    },
    {
        "title": "Caminar con Jesús cada día",
        "references": "Juan 15:1-11; Gálatas 5:16-26; Miqueas 6:8",
        "body": (
            "El discipulado es una vida diaria de permanecer en Jesús. Crece por medio de prácticas sencillas: "
            "oración, Escrituras, adoración, confesión, servicio, descanso, generosidad y amor al prójimo. Este camino "
            "no es madurez instantánea; es aprender a volver a Jesús una y otra vez."
        ),
        "step": "Elige un hábito semanal sencillo que te ayude a caminar de cerca con Jesús.",
        "questions": [
            "¿Qué hábito diario o semanal te ayudaría a seguir a Jesús?",
            "¿Por qué te gustaría que tu mentor o tu pastor orara por ti?",
        ],
    },
]


class Command(BaseCommand):
    help = f"Crea o actualiza la ruta inicial editable «{STARTER_TITLE}»."

    def add_arguments(self, parser):
        parser.add_argument(
            "--church-slug",
            help="Identificador de la iglesia. Es obligatorio si existe más de una iglesia.",
        )

    def handle(self, *args, **options):
        churches = Church.objects.all()
        if options.get("church_slug"):
            church = churches.filter(slug=options["church_slug"]).first()
            if not church:
                raise CommandError("No hay ninguna iglesia con ese identificador.")
        else:
            if churches.count() == 0:
                raise CommandError("Primero crea una iglesia desde la configuración inicial.")
            if churches.count() > 1:
                raise CommandError("Usa --church-slug cuando exista más de una iglesia.")
            church = churches.first()

        if not DiscipleshipPath.objects.filter(church=church, title=STARTER_TITLE).exists():
            legacy = (
                DiscipleshipPath.objects.filter(church=church, title__in=LEGACY_STARTER_TITLES)
                .order_by("created_at")
                .first()
            )
            if legacy:
                legacy.title = STARTER_TITLE
                legacy.save(update_fields=["title"])

        path, _ = DiscipleshipPath.objects.update_or_create(
            church=church,
            title=STARTER_TITLE,
            defaults={
                "description": "Siete lecciones para empezar a seguir a Jesús con el apoyo de una iglesia local.",
                "target_audience": "Visitantes, personas que buscan a Dios, nuevos creyentes y quienes regresan a la fe",
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
        self.stdout.write(self.style.SUCCESS(f"La ruta inicial está lista para {church.name}."))
