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

## [0.4.1] - 2026-08-29

### Fixed

- **The browser view no longer seizes up when a burst arrives.** Every event
  went on the page as it landed, and each row appended made the browser lay the
  whole table out, so the cost of showing one flow grew with the history behind
  it. A reconnect, which hands over a backlog of up to four thousand events in
  one go, and letting go of pause, which does the same, were each thousands of
  full-table layouts inside a single task: from the outside, a tab that had
  frozen. What arrives is now applied once per animation frame, as one append
  and one scroll to the tail however much it is holding.

## [0.4.0] - 2026-08-28

### Fixed

- **The browser view is coloured in a detached container**, which is the
  arrangement the image exists for and the one where it was white. Colour was
  one switch for the whole program, thrown by asking whether stdout was a
  terminal; a detached container has no terminal by definition, so the codes
  were blanked at the source and the browser was handed the colourless
  version because of the state of a stream nobody was watching.
  `--colour always` was the workaround and appeared in none of the container
  documentation. No flag is needed now.

### Added

- **`--web-colour on|off`**, the browser's own colour switch, on by default.
  A browser is a colour-capable reader whatever stdout is, so a redirected
  run no longer takes the colour out of it, and `off` is there for a run that
  wants the view plain.

### Changed

- **`--colour` is the terminal's switch and no longer the program's.** It
  means for this terminal exactly what it always meant, `auto` included, and
  `--no-color` and `NO_COLOR` go on meaning `never` for it. What changes is
  reach: none of the three blanks the browser's colour any more, which is the
  point of the fix above. A run with no `--web` behaves exactly as it did.
- Colour is now painted once and taken out on the way to whichever reader
  refused it, rather than never painted. Only colour is taken out: the scroll
  margins, cursor moves and erases the sticky header and the status bar write
  to the same stream pass through untouched. Where the two readers disagree,
  the host list still marks a superseded name with a star for the one without
  colour and dims it for the one with, so neither is shown the other's
  rendering.

## [0.3.0] - 2026-08-28

### Added

- **`--web-host NAME`**, a name the browser view answers to. Under the
  loopback default it is added beside `localhost`; under another `--web-bind`
  it narrows the view to the names given and the address a connection arrived
  on. It may be repeated, and the first name given is what the printed URL
  carries. A name that could not work, because it is not ascii, carries a
  port that belongs to `--web-port`, or is a pattern, is refused when the
  flag is read rather than left to match nothing.
- Under a wildcard `--web-bind` with no name given, the banner says to put
  this machine's address or name in place of the `127.0.0.1` the printed URL
  shows.

### Changed

- **A routable `--web-bind` answers to any name.** The `Host` check used to
  accept the address a connection arrived on and nothing else, whatever the
  bind, so `http://z2m:2056/t/.../` was a 404 with nothing to say why. It
  still checks the port, and under the loopback default it still refuses
  every name but `localhost` and those given, because that is the case DNS
  rebinding is about: a routable bind is reachable by the LAN directly and
  the token is its guard. Jupyter, Syncthing and Ollama each settled on the
  same rule. The `Origin` check on the control route follows the request's
  own `Host` in that case, so a page opened by any name can press keys and a
  page on any other origin cannot.
- **The image's view is reachable through a bridge publish**, which follows
  from the above: the container binds `0.0.0.0`, a connection through
  `-p 127.0.0.1:2056:2056` arrives on the container's own bridge address, and
  that no longer has to match the `Host` header. Docker Desktop, which offers
  nothing but the bridge, can therefore show the view, with the exporter
  address still lost to the gateway. The README and the compose file said the
  view was unreachable there, and now say this instead.

### Documentation

- The README now says where the `Host` check leaves somebody opening the
  view from another machine: in the web interface section, in the Docker
  section beside the host networking example, and as a troubleshooting entry.

## [0.2.2] - 2026-08-28

Release plumbing only. Nothing in the command, its options or its output
changes, and an existing install has no reason to move for it. It is a
release because most of what changed only happens when a tag does, and
because a release is the only way to find out whether it works.

### Changed

- **The image is built and pushed by `mjaksn/workflows` now**, called from
  `release.yml` and pinned by commit. It was the same hundred and forty
  lines here as in two other repositories, kept in step by hand and already
  drifting. Two calls rather than one, so that a Docker Hub outage still
  cannot hold up the GHCR image or the release page behind it, which is the
  arrangement 0.2.1 introduced and this preserves. The Dockerfile stays
  here. Nothing about the published image changes: same base digest, same
  three platforms, same tags.
- **CI rehearses the publishing path on every pull request**, building for
  one platform and pushing nothing. A release workflow cannot be tried
  without releasing, and a shared file is one point of failure for three of
  them, so the `rehearsal` job is what keeps it honest between tags.

### Fixed

- **A tag that is not on `main` is refused rather than published.** A squash
  merge writes a commit of its own, so a tag put on the release branch
  beforehand names a commit that never reaches `main`, and `git describe`
  there then answers with the release before it. `v0.1.1` was tagged that
  way and has since been moved onto `main`; the release workflow now refuses
  the mistake instead of recording it.

## [0.2.1] - 2026-08-28

### Added

- **A container image**, published to `ghcr.io/mjaksn/nettail` and
  `docker.io/mjaksn/nettail` on every release, for `linux/amd64`,
  `linux/arm64` and `linux/arm/v7`. It is built for `--web`: the console
  display wants a real terminal, which a detached container does not have, and
  the browser view is the mode that works properly without one.
  `docker-compose.yml` is a worked example.
- **`scripts/install.sh`**, which sets nettail up as either a systemd service
  or a Docker container, asking which and asking for the flow port, the web
  port and the resolver mode. Every answer has a flag, and `--non-interactive`
  fails rather than hanging when nobody is there to answer. It generates a web
  token, keeps it in `/etc/nettail/nettail.env` mode 0640, and reuses it on a
  reinstall so that a bookmarked URL survives an upgrade. Until now the README
  asked you to write the systemd unit by hand.
- `requirements.lock` and `requirements-build.lock`, pinning what the image
  installs by version and by the hash of every file the index publishes, with
  `scripts/lock_hashes.py` to refresh them from the index's own digests.

### Changed

- **The routable web bind warning now says something different in a
  container.** On a host it is unchanged, and it should be: a bind to anything
  but loopback puts a map of the network on an address others can reach, over
  plain HTTP. In a container it was misleading. Loopback there belongs to the
  container's namespace, so the image passes `--web-bind 0.0.0.0` on every
  start and the warning fired every time, which teaches a reader to skip the
  line that matters. What a container cannot see is how the port was published,
  and that is where the exposure is really settled, so it now says that
  instead, pointing at `-p 127.0.0.1:2056:2056` and at `-p 2056:2056`.

  Nothing about what is bound changed. The detection decides which sentence is
  printed and nothing else.

## [0.2.0] - 2026-08-26

### Added

- An opt-in web interface. `--web` mirrors the display into a browser: the same
  flows in the same colours, the decoder notices, the summary and the host list
  as they are printed, a live status footer, and every keyboard control as both
  a button and a key. It is a second view of one collector rather than a second
  collector, so a key pressed anywhere moves the terminal too.

  The keys live in a drawer, shut by default so the flows have the window, and
  laid out as a grid of labelled buttons when it is open. The flow table sizes
  its columns to what is in them, and the two endpoint columns wrap rather than
  widen, so a long hostname cannot push the table past the window.

  A tab in the background gives up its connection and takes it back on return,
  saying how many flows went past while it was away. A backgrounded tab is
  throttled or frozen by the browser, which goes on buffering the connection
  with nothing running to drain it, and on a busy link that grows until the tab
  is killed for memory. Three things ask for the connection to go: the tab
  being marked hidden, after about fifteen seconds so that switching away and
  straight back costs nothing; the browser saying it is about to freeze the
  tab, which it does under the memory pressure the buffer itself creates; and
  the page's own clock running ten seconds late, which is what a minimised
  window looks like from the inside. A **Follow** box
  beside the connection indicator decides whether new flows pull the view down;
  scrolling up clears it and scrolling back to the bottom fills it again, and a
  tab that was following when it went to the background is put back at the
  bottom the moment it returns rather than waiting for the next flow to carry
  it there. The scrollbars are drawn in the page's own colours.

  It is off unless asked for and binds `127.0.0.1`. The URL printed at startup
  carries a random token, without which every request is a 404, and the `Host`
  header is checked on every request so that a page open in another tab cannot
  reach it by pointing a name at the loopback address. `--web-bind` will put it
  elsewhere and says in as many words what that exposes, cleartext included.
  `--web-token` pins the token so a bookmark survives a restart, and
  `--web-readonly` serves the display while accepting nothing back. A token
  that could not work, because it is empty, not ascii, or holds a character
  a URL path would split on, is refused when the flag is read rather than
  leaving the collector printing a URL that answers nothing.

  Standard library only. There is still no dependency beyond netflume and
  lanname, and no test dependency at all.

- `--colour always|auto|never`, with `--color` accepted as a spelling and
  `--no-color` kept as `--colour never`. `auto` is what the program has always
  done. `always` exists because colour was decided by whether stdout is a
  terminal, which hands the web interface the colourless version in exactly the
  arrangement it is most useful in: a service unit writing `--json` to a file
  while a browser watches.

### Changed

- Two keys are treated differently in the browser. `esc` closes the program,
  which would end it for everybody including the terminal, and that should not
  arrive as a side effect of mirroring a keyboard, so it does not cross at all.
  `?` prints the list of keys, and the browser already has that list as
  labelled buttons, so it gets no button of its own; the key still works, and
  prints the keys a browser can press. The terminal keeps both.

- Under `--json`, the `x` and `b` keys no longer draw on stdout. Neither could
  be reached before, because `--json` turns the keyboard off, but a browser can
  press both: `x` would have put two escape sequences into the middle of a
  stream something else was parsing, and `b` would have put a scroll region and
  two rows of status bar there and repainted them twice a second. Drawing on a
  screen is a thing that happens to a terminal, so it now happens only where
  there is one.

- Under `--json`, the space key pauses the browser view and leaves stdout
  alone. `--json` is the part of the interface meant to be parsed, and holds
  and drops do not belong in it.

- `HEADER_LINE` and the continuation indent are now built from a `COLUMNS`
  table rather than written out beside it, and one flow's cells are built once
  by `row_cells` and used by both views. What a row looks like is unchanged;
  what changed is that it is now described in one place, which is what lets a
  browser draw the same table without a second copy of the column list.

### Fixed

- The `x` key wrote its clear escapes to stdout whenever `--json` was off, a
  redirected stdout included, and the header it reprints afterwards went with
  them. Clearing a screen is something that happens to a terminal, so the key
  now asks whether there is one. The keyboard has only ever needed a tty on
  stdin, so a redirected run could always do this; `--web` widens it to a
  collector with no terminal at either end, which is the arrangement the
  browser view is most worth having.

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

[0.4.1]: https://github.com/mjaksn/nettail/releases/tag/v0.4.1
[0.4.0]: https://github.com/mjaksn/nettail/releases/tag/v0.4.0
[0.3.0]: https://github.com/mjaksn/nettail/releases/tag/v0.3.0
[0.2.2]: https://github.com/mjaksn/nettail/releases/tag/v0.2.2
[0.2.1]: https://github.com/mjaksn/nettail/releases/tag/v0.2.1
[0.2.0]: https://github.com/mjaksn/nettail/releases/tag/v0.2.0
[0.1.2]: https://github.com/mjaksn/nettail/releases/tag/v0.1.2
[0.1.1]: https://github.com/mjaksn/nettail/releases/tag/v0.1.1
[0.1.0]: https://github.com/mjaksn/nettail/releases/tag/v0.1.0
