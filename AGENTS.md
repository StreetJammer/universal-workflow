# Project guidance

Use Python 3.10+ and the standard library for runtime code. Do not introduce a network dependency into memory operations or installation. Preserve original user requests, existing project instructions, and unrelated files. This repository itself is a Claude Code plugin; its reusable skill lives in `skills/universal-workflow`.

Run `python3 scripts/validate_package.py` and `python3 -m unittest discover -s skills/universal-workflow/scripts -p 'test_*.py' -v` for relevant changes. Native CLI/model checks are separate: mocks prove transport behavior only. Do not publish raw transcripts, private memory, generated project stores, credentials, or test fixture output.

## Code Review Rules

- Flag paths that can overwrite a concurrent memory update, accept stale proposals, or promote a workflow without a matching independently executed evaluation. Keep comparison and mutation in one transaction.
- Flag installers or hooks that overwrite unrelated user files, bypass project boundaries, expose private input, or introduce unbounded/reentrant background work. Treat owner policy and private native memory as outside the writable store.
- Verify that tests and documentation distinguish local fixture results, live model behavior, native host discovery, and actual scheduler activation. Never infer a passing live integration from a mocked CLI.
