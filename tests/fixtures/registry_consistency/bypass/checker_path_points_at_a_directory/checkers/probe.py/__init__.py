# The evasion: `checkers/probe.py` exists -- as a *directory*. A registry
# check that asks `exists()` rather than `is_file()` sees a checker that
# is there, and `file_sha` on a directory is a different error entirely.
