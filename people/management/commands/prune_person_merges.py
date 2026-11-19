"""Retention of merges: the contact values a merge keeps for its undo are emptied when the undo ends.

A ``PersonMerge`` keeps, for ``people.merge.UNDO_DAYS`` days, the e-mail, phone or contact method
the surviving person had before (only the ones the merge changed). After that the undo is not
possible and nothing needs them: this command empties them and leaves the ids. The record of the
merge stays. Without ``--apply`` nothing is written: the command only counts. Running it again finds
nothing left to do. It prints a count and never a name or a value. The scheduler does the same on
every pass (the job ``uniones_personas`` of ``run_scheduled_jobs``), for every church whatever its
module flags; this command is the same thing for an operator (and for a server whose community jobs are off).
"""

from django.core.management.base import BaseCommand

from people import merge


class Command(BaseCommand):
    help = (
        f"Borra los datos de contacto anteriores que guarda una unión de registros de persona después de {merge.UNDO_DAYS} días, "
        "cuando ya no se puede deshacer. Sin --apply solo cuenta."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Escribe los cambios. Sin esta opción no se borra nada.")

    def handle(self, *args, **opts):
        total = merge.clear_expired(apply=opts["apply"])
        if opts["apply"]:
            self.stdout.write(f"Se hizo: uniones a las que se les borraron los datos de contacto anteriores: {total}")
        else:
            self.stdout.write(
                f"Se haría (ensayo: no se escribió nada; usa --apply para aplicarlo): uniones con datos de contacto anteriores por borrar: {total}"
            )
