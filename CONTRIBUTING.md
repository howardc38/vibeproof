# Contributing

Open an issue or a pull request at [vibeproof](https://github.com/howardc38/vibeproof).
Include a concrete example, expected behavior, actual behavior and a test when
it demonstrates a meaningful regression. Do not include private code or credentials.

The public repository is a distribution. Maintainers develop code, tests,
documentation and release tooling in one canonical source repository, then
publish a verified snapshot. You do not need access to that private repository
to propose a patch: use a public fork. Accepted changes are imported into the
canonical source before the release is generated, with the public PR recorded
for attribution.

Run the public demo with Git and Python 3.12+:

```sh
python3 examples/first-proof/run.py
```

Run the framework tests and documentation checks:

```sh
python3 tests/run_without_silent_skips.py
./bin/v4 --repo . accept --here --docs
```

The provenance check describes an exact released tree. A contributor's edits
can intentionally differ from its manifest; maintainers regenerate that manifest
through the canonical release process, rather than asking contributors to invent
source hashes. Product tests still apply to the proposed patch.

Maintainer release instructions: [docs/SYNC.md](docs/SYNC.md).
