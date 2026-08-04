# Security and Privacy

Emmaus handles sensitive spiritual and personal data. Churches should treat participant questions, reflection answers, follow-up requests, and pastoral notes as confidential church records.

## Consent and Pastoral Records

- Get consent before storing personal or pastoral notes.
- Avoid storing unnecessary sensitive details.
- Keep notes factual, limited, and pastorally necessary.
- Participants never see pastoral notes in the application.
- Mentors only see notes for assigned participants when note visibility allows it.

## AI Boundaries

AI-assisted drafts are optional and disabled by default.

- AI must not replace pastoral care.
- AI output is never sent directly to participants.
- Leaders must review, edit, approve, or reject drafts.
- Do not put unnecessary sensitive details into AI prompts.
- Sensitive issues should be escalated to pastors or qualified professionals.

## Emergencies

Emmaus is not emergency support, therapy, medical advice, legal advice, or a replacement for pastoral care. Emergency situations require local emergency services immediately.

## Production Requirements

Before using Emmaus with real participants:

- Use HTTPS.
- Set `DEBUG=false`.
- Use a strong `SECRET_KEY`.
- Restrict Django admin access.
- Use secure SMTP credentials.
- Protect database credentials.
- Configure regular PostgreSQL backups.
- Limit shell and server access to trusted operators.
- Keep dependencies updated.
- Review access controls after any role or workflow changes.

## Secrets

Do not commit `.env`, production credentials, API keys, SMTP passwords, database dumps, or participant exports.
