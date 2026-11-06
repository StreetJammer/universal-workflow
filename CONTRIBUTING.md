# Contributing

Use a feature branch and open a focused pull request. Explain the concrete user-visible problem and behavior after the change. Add regression tests for consequential failures. Keep runtime dependencies in Python's standard library and do not commit generated memory stores or real conversations.

## Local checks

```bash
python3 scripts/validate_package.py
python3 -m unittest discover -s skills/universal-workflow/scripts -p 'test_*.py' -v
```

Keep native CLI integration checks separate from fixture transport tests. Reproduce bugs in an isolated temporary project. Never run untrusted test fixtures against production accounts.

## Review process

A maintainer can request Codex review by commenting `@codex review` on a pull request after enabling code review for the connected repository. Review the actual finding, reproduce it, fix it, and add a regression check. Run the relevant suite again and request another review if the change warrants it. Do not merge solely because a bot reports no issues.

Source guidance: https://learn.chatgpt.com/docs/third-party/github
