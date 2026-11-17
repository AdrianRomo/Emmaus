# 0009 - Who reads group attendance, and for how long it is kept

- **Status:** accepted (owner, 2026-11-17, answering issue #66). One part is behind a release gate, see below.
- **Date:** 2026-11-17
- **Deciders:** owner. Church leadership approves named pastoral access before it exists.

## Context

Guides record who came to each meeting (`groups.GroupMeeting`, `GroupAttendance`). Attendance at a
church group reveals religious participation, which Mexican privacy law treats as sensitive personal
data. Emmaus should help people take part in church life without making them feel monitored, and
still give pastors enough to care for people.

## Decision

| Who | Sees |
|---|---|
| A member | Their own attendance in their group, privately. No scores, streaks, rankings or comparison. |
| Who guides a group | The individual records of that group. |
| Church administration | Scoped operational access, as today. |
| A pastor | Totals for every group of their church ("5 de 8 personas asistieron"), growth and trends. Not names. |

1. The pastor's page ("Panorama", app `overview`) shows aggregates only. Overseeing every group does
   not grant every member's attendance history.
2. **Named pastoral access** (a pastor reading one person's attendance) is not built. When it is, it
   is a permission given to a named person, audited on every read. **Release gate:** church
   leadership approves it first.
3. Retention, as product defaults and not as legal deadlines: 12 months identifiable, then only the
   meeting's totals; 24 months for the totals. `manage.py prune_attendance` applies it (dry run by
   default). Each church validates the periods against its privacy notice before the command is scheduled.

## Consequences

- Positive: the pastoral dashboard is unblocked without widening access to individuals.
- Negative: a pastor who needs to know about one person asks the guide; that is intended for now.

## Reconsider if

- Church leadership approves named access (then: permission model, audit on read, notice to members).
- A privacy adviser asks for different periods.
