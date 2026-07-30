# Contributing

Thanks for helping keep the client compatible with HLTV.

## Before opening an issue

- Confirm the failure with a current Chrome, Edge, or Firefox release.
- Distinguish a Cloudflare challenge from an HTML parser failure.
- Remove personal browser data, cookies, tokens, and full browsing profiles
  from logs and attachments.
- Include the method called, exception type, Python version, browser version,
  and a minimal reproducible example.

Do not post Cloudflare clearance cookies or ask maintainers to automate a
CAPTCHA.

## Development setup

```bash
python -m venv .venv
python -m pip install -e ".[dev]"
```

Run the required checks:

```bash
ruff check .
pytest
python -m build
```

## Parser changes

Keep browser navigation separate from parsing. Add the smallest HTML fixture
that demonstrates the upstream markup and a regression test for the expected
model. Fixtures must not contain user data, cookies, advertising payloads, or
large copied pages.

## Pull requests

- Keep each pull request focused.
- Explain which upstream selector or behavior changed.
- Preserve the compatibility properties unless a breaking release is planned.
- Update the README or type models when the public API changes.
- Confirm that the test suite does not contact HLTV.
