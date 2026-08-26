# Changelog

Notable changes to nettail. Versions follow [semantic
versioning](https://semver.org/spec/v2.0.0.html): while the major version is 0
the interface may still change, and any such change is called out here under
**Changed** rather than assumed to be obvious from the version number.

What is versioned is the command: its options, its keys, and the shape of what
it prints. The `nettail` package is importable and its modules are documented,
but it is a program rather than a library, and the names inside it may move
without that being a breaking change. `--json` output is the part meant to be
parsed, and it is treated as public.

## [0.1.2] - 2026-08-26

### Documentation

- The README now carries the same badge set as the sibling projects: CI,
  Release, PyPI version, and licence. Released so that the badges appear on the
  PyPI project page, which is rendered from the README inside the uploaded
  distribution and cannot be edited in place.

No code changed in this release.

## [0.1.1] - 2026-08-25

### Fixed

- The help came out in colour on Python 3.14 whatever the reader had asked
  for, `nettail --no-color --help` included, and a redirected `--help` carried
  escape codes into the file. 3.14 gave argparse a colour scheme of its own
  and settles it while the arguments are being parsed, which is before
  `--no-color` has been read, so the switch that turns colour off could never
  reach it. argparse is now asked to keep out of the question, leaving what
  this program prints in one place as it was on every earlier interpreter.

## [0.1.0] - 2026-08-25

First release, under this name and as a package rather than a script in a
repository.

Before this it was a single program with the wire decoding and the hostname
lookups inside it. Both have been lifted out and released on their own, as
[netflume](https://pypi.org/project/netflume/) and
[lanname](https://pypi.org/project/lanname/), so what is left here is the
console: the part that decides what a flow should look like on a terminal.

### Added

- `pip install nettail` and a `nettail` command, in place of cloning a
  repository and running a script out of it. `python -m nettail` runs the same
  thing from a checkout.
- A supplemental service name list, so that a port the system services
  database happens not to know is still named. mDNS on 5353 is the one that
  prompted it: named on most Linux and macOS installs, unnamed on Windows, and
  the same capture should not read differently on two machines.
  `--no-supplemental-services` turns it off.
- `--version`, which prints the number the package reports and nothing else.
- A `?` key that lists every keyboard control and what it does, so the
  reminder line under the startup banner can be a pointer rather than a
  two-hundred-character list that wrapped and then scrolled away.

[0.1.2]: https://github.com/mjaksn/nettail/releases/tag/v0.1.2
[0.1.1]: https://github.com/mjaksn/nettail/releases/tag/v0.1.1
[0.1.0]: https://github.com/mjaksn/nettail/releases/tag/v0.1.0
