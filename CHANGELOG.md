# Changelog

All notable changes to this project are documented here.

## 1.0.0

- rebuilt the package for Python 3.10 and Selenium 4
- added automatic Chrome, Edge, and Firefox driver management
- replaced brittle line-based extraction with isolated Beautiful Soup parsers
- updated selectors for current ranking, team, match, search, and news pages
- added typed dataclass result models
- retained the original `Teams`, `Matches`, and `News` method names
- implemented the previously unfinished `FutureMatches`
- replaced process exits with package-specific exceptions
- added persistent browser profiles and request pacing for Cloudflare challenges
- added unit tests, linting configuration, build checks, and CI
- removed committed build output, bytecode, logs, and package metadata
