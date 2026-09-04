# RED. Operations runbook, second connection string

The same shape as `db_url.md` with one difference: the password carries the
RFC 3986 sub-delims a generated password actually contains. `TEMPLATE_CHARS`
held `$`, `(`, `)`, `*` and `'` while the sentence beside it said sub-delims
are left out because a real password may contain them -- so this line was
suppressed as `template-placeholder` and the credential in it was never
reported. `$` is the character a generated password is most likely to carry:
a bcrypt hash starts `$2b$`.

Synthetic: generated for this fixture, never issued, never valid.

    postgresql://svc_reports:aX9$qw(Lm)2Zp*7RtVnE4@db.prod.internal:5432/adopter_a
