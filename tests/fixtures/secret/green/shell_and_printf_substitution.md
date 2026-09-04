# GREEN. The same runbook, with the password left to the environment.

The control for `red/db_url_with_sub_delims.md`. Taking `$` and the brackets
out of `TEMPLATE_CHARS` must not stop a substitution being read as one, so a
template is matched as a *shape* -- `${VAR}`, `$(cmd)`, `%(name)s` -- rather
than by the characters that appear inside it. Rule: template-placeholder.

    postgresql://svc_reports:${REPORTS_DB_PASSWORD}@db.prod.internal:5432/adopter_a
    postgresql://svc_reports:$(pass show ops/reports_db)@db.prod.internal:5432/adopter_a
    postgresql://svc_reports:%(reports_db_password)s@db.prod.internal:5432/adopter_a
