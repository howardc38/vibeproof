## 4. Kernel
<!-- pinned: kernel/ledger.py::SCHEMA -->

Long enough to count as a mechanism section. It describes how the ledger is laid
out and what the append-only triggers do, and it pins the schema so renaming it
fails this check instead of quietly making the paragraph wrong. Padding follows
so the four-hundred-character rule actually fires on this fixture rather than
skipping it as too short to be a mechanism. Read `kernel/ledger.py`, run
`v4 teleport`, and note that the `probe` checker below is named here so the
other-direction rule is satisfied: probe.
