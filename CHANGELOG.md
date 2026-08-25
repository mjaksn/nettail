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
- A `?` key that lists every keyboard control and what it does, so the
  reminder line under the startup banner can be a pointer rather than a
  two-hundred-character list that wrapped and then scrolled away.

[0.1.0]: https://github.com/mjaksn/nettail/releases/tag/v0.1.0
