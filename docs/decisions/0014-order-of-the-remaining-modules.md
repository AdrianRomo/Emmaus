# 0014 - Order of the modules that are not built, and who approves what

- **Status:** accepted (owner, 2026-11-17, answering issue #71)
- **Date:** 2026-11-17

## Principle

Emmaus helps people take part in church life; it does not make them feel monitored. The aim is a
place people return to between events: joining a group, finding activities, serving, hearing from
the church and receiving care. A feature that only collects more data does not qualify.

## Order

1. Pastor's dashboard · 2. Event FAQ and programme · 3. Visitor follow-up · 4. Post-event surveys ·
5. Volunteer scheduling (built) · 6. Sermons and media links (built) · 7. Devotionals and reading
plans (a hand-written daily devotional is built; plans need content ownership and licensing) ·
8. Guardian accounts and households · 9. Moderated photos (start with moments curated by staff) ·
10. Children's check-in (needs guardian verification and pick-up controls) · 11. Giving (last:
financial and legal complexity).

## Who approves

| Area | Approves |
|---|---|
| Member's view of their attendance | Product owner, with privacy review |
| Pastor's access to identifiable attendance | Church leadership |
| Minors' consent and guardian verification | Church leadership, safeguarding and privacy review |
| Waiting lists and walk-ups | Product owner |
| Who publishes urgent notices | Church leadership |
| Devotional authorship | Pastoral and content leadership |
| Volunteer scheduling | Ministry leaders |
| Photos of minors | Safeguarding and privacy approval |
| Children's check-in and pick-up | Church leadership and safeguarding team |
| Giving and payments | Church administration, legal and accounting review |

Where it fits, an approval becomes a setting of each church, not a condition written into the code
for the first church.

## Release gates

Two things do not reach production before their approval: minors' registration (0010) and named
access to attendance (0009). Everything else proceeds in parallel.
