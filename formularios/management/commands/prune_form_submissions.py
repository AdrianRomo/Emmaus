"""Retention of the general forms: the answers of a form are deleted when the time it keeps them ended.

A form says how long its answers are kept after its last day (30, 90 or 365 days; the person who
answers reads it on the form). Past that day this command deletes the form's answers, and with them
their tasks. The form, its questions and its versions stay. Without ``--apply`` nothing is written:
the command only counts. Running it again finds nothing left to do. It prints counts and never a
title, a name or an answer. The scheduler does the same on every pass (the job
``respuestas_formularios`` of ``run_scheduled_jobs``), for every church whatever its module flags;
this command is the same thing for an operator (and for a server whose community jobs are off).
"""

from django.core.management.base import BaseCommand

from formularios import services


class Command(BaseCommand):
    help = "Borra las respuestas de los formularios cuyo tiempo de guardado terminó, con sus pendientes. Sin --apply solo cuenta."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Escribe los cambios. Sin esta opción no se borra nada.")

    def handle(self, *args, **opts):
        done = services.prune(apply=opts["apply"])
        if opts["apply"]:
            self.stdout.write(f"Se hizo: formularios con respuestas vencidas: {done['forms']}; respuestas borradas: {done['submissions']}")
        else:
            self.stdout.write(
                "Se haría (ensayo: no se escribió nada; usa --apply para aplicarlo): "
                f"formularios con respuestas vencidas: {done['forms']}; respuestas por borrar: {done['submissions']}"
            )
