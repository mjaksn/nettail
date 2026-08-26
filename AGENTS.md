# AGENTS.md

Guidance for AI coding agents working in this repository. Claude Code reaches
it through `CLAUDE.md`, which is a pointer at this file and nothing else.

## What this is, and what it is not

nettail is the console half of a NetFlow collector: it decides what a flow
looks like on a terminal. Two things it used to do were lifted out and are now
separate packages of their own.

- Decoding v5, v9 and IPFIX, templates, export gaps, advertised sampling
  rates: [netflume](https://github.com/mjaksn/netflume).
- Turning an address into a hostname over reverse DNS, mDNS and NetBIOS:
  [lanname](https://github.com/mjaksn/lanname).

A change about parsing a datagram or resolving a name almost certainly belongs
in one of those repositories rather than this one. What belongs here is
layout, colour, keys, the status bar and the exit summary.

## Commands

```bash
nettail                          # once installed
python -m nettail                # from a checkout

python tests/run.py              # every suite
python tests/run.py tally keys   # only suites whose name contains either
python tests/run.py -v           # print every check, not only failures
python tests/test_tally.py       # one suite directly

ruff check .                     # CI gates on this
python -m build                  # wheel and sdist
```

## The test suite is not pytest

Each suite is a plain script that runs top to bottom, prints a line per check
and exits non-zero if any failed. `tests/run.py` gives each its own process,
which is load-bearing: several of them replace `socket.socket`,
`shutil.get_terminal_size` or the keyboard for their duration.

`tests/harness.py` holds `check`, `finish`, a colour stripper and a stream
that claims to be a terminal. It also puts the repository root on `PYTHONPATH`
so that suites which start `python -m nettail` subprocesses can import the
package. Two consequences worth knowing:

- `from harness import ...` must come before `import nettail` in a suite.
  Importing harness is what puts the root on `sys.path`.
- A test that needs a file on disk uses `tempfile`, not a committed fixture.

There are no test dependencies and there is not meant to be one.

## Things with a single source of truth

Breaking one of these does not fail loudly on its own, so each is pinned by a
test:

- **`KEYS` in `keys.py`** is every keyboard key and what it does. The dispatch
  (`Controls.actions`) and the `?` listing both come from it, and
  `test_key_help` holds them to each other in both directions: a key that
  works and is listed nowhere fails, and so does one listed and wired to
  nothing. `HELP_KEY` is `?` itself, named once and used by the table, the
  dispatch and the startup reminder line.
- **`EPHEMERAL_FLOOR` in `services.py`** repeats a number netflume writes
  inline and exports no constant for. `test_services` finds where netflume
  actually stops naming ports and pins ours to it.
- **The version** appears in `pyproject.toml`, `nettail/__init__.py` and
  `CHANGELOG.md`. `release.yml` refuses to publish unless the tag agrees with
  the first two, and the release notes it posts are the changelog's section
  for that tag, so a tag the changelog says nothing about fails the release
  rather than putting up an empty page. `cli.py` imports `__version__` from
  the package, which works because `__init__.py` assigns it above its
  submodule imports; keep that order.

## Service names

`services.py` asks netflume, which reads the system services database, and
consults the shipped `supplemental-services` file only for a port the system
had no name for. That precedence never reverses: a machine that already names
a port keeps its own answer.

The data file has to stay listed in `[tool.setuptools.package-data]`. Left
out, the wheel ships without it and every supplemental name silently becomes a
bare port number.

## The terminal is shared state

`sticky.py` pins the column header to the top row and `statusbar.py` holds the
bottom two, and both want DECSTBM margins. DECSTBM is one pair of margins, not
two settings, so exactly one of them writes the region and the other asks.
`scroll_region()` is the only place that arithmetic lives. Changing either
feature means reading both, and their suites.

## Constraints that bite

- **Python 3.9 is supported.** No PEP 701 f-strings, so no newline inside an
  f-string expression; no `X | None`; no builtin generics. CI runs 3.9 on
  ubuntu, and it is easy to break this on a newer interpreter without
  noticing.
- **ruff line length is 88.** CI fails on 89.
- **Flow rows go to stdout; everything else goes to stderr** — the banner, the
  `?` listing, the host list, the summary, and every warning. That is what
  keeps `--json` and shell redirection usable.
- **Every `Resolver(...)` passes an explicit `mode`.** lanname 0.2.0 changed a
  bare `Resolver()` from looking nothing up to querying reverse DNS, with
  nothing raised and nothing warned. Explicit modes are why that release was a
  non-event here. Keep it true.
- **Dependency direction runs one way**, from `cli` down towards `colour` and
  `values`. There are no import cycles and there should not be.

## Prose

Comments and docstrings here carry the reasoning, not a restatement of the
code, and they are written as prose. Match the surrounding voice, and note
that it is deliberately free of em dashes and of double hyphens used as
punctuation. When a rewritten line changes a paragraph's shape, rewrap the
paragraph to the width the file already uses.
