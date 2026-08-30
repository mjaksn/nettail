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

Since 0.2.0 there is a second place the same display can appear: `--web`
serves it to a browser. That is a mirror rather than a second program. It
decides nothing about what a flow looks like; it is handed the cells the
terminal row was built from and lays them out in a table.

## Commands

```bash
nettail                          # once installed
python -m nettail                # from a checkout
nettail --web                    # and mirror it to a browser on 127.0.0.1

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
  dispatch and the startup reminder line. `QR_KEY` is `q` in the same way,
  and has one thing more to agree with: it is kept back from the browser, so
  it is named in `WEB_EXCLUDED` as well.
- **`EPHEMERAL_FLOOR` in `services.py`** repeats a number netflume writes
  inline and exports no constant for. `test_services` finds where netflume
  actually stops naming ports and pins ours to it.
- **`COLUMNS` in `display.py`** is every column, its width, its alignment and
  the gap in front of it. `HEADER_LINE` is built from it, so is
  `ENDPOINT_INDENT`, and so is the table head the browser draws, which arrives
  over the wire in the `hello` event rather than being written down again in
  `web.html`. The same goes for the buttons, which come from `KEYS` by the same
  route. **The page hardcodes nothing the terminal already names**, and that is
  the rule to hold: a second list of columns or keys living in the HTML is a
  second thing to go stale, and nothing in the page would fail loudly when it
  did.
- **`row_cells` in `display.py`** builds one flow's cells once, plain and
  painted, and both views use them. A browser must never work a cell out for
  itself. It could not do it correctly in any case, since a service name is
  whatever this machine's services database calls that port, and reimplementing
  the protocol names, the size ramp or the arrow in JavaScript would be two
  implementations to keep in step.
- **The version** appears in `pyproject.toml`, `nettail/__init__.py` and
  `CHANGELOG.md`. `release.yml` refuses to publish unless the tag agrees with
  the first two, and the release notes it posts are the changelog's section
  for that tag, so a tag the changelog says nothing about fails the release
  rather than putting up an empty page. `cli.py` imports `__version__` from
  the package, which works because `__init__.py` assigns it above its
  submodule imports; keep that order. Tag the merge commit on `main` once the
  pull request has landed, and not the branch it came from: a squash merge
  replaces a branch's commits with one of its own, so a tag left behind on the
  branch names a commit that never reaches `main`, and `git describe` there
  then answers with the release before it.

## Service names

`services.py` asks netflume, which reads the system services database, and
consults the shipped `supplemental-services` file only for a port the system
had no name for. That precedence never reverses: a machine that already names
a port keeps its own answer.

The data file has to stay listed in `[tool.setuptools.package-data]`. Left
out, the wheel ships without it and every supplemental name silently becomes a
bare port number.

## The web interface

`feed.py` is the bus and knows nothing about HTTP; `web.py` is the server and
touches no collector state. Between them sits one rule that everything else is
arranged around: **a request thread may read a feed queue and put a key on a
queue, and that is the whole of its authority.** Everything that changes what
the collector is doing happens on the receive thread, which drains the key
queue between datagrams and hands each one to the same `Controls.handle` the
terminal uses.

Three things about it are easy to break and quiet when broken.

- **Nothing on an HTTP thread may print.** `sticky.py` and `statusbar.py` are
  managing a scroll region, and a line written from another thread lands inside
  it and corrupts both. This is why `log_message` is silenced outright rather
  than quietened: the problem is the writing, not the volume. `handle_error` on
  the server is silenced for the same reason and is the more important of the
  two, because it covers what escapes a handler rather than what a handler
  chooses to say. A traceback is several pages, and one arrived from an
  unauthenticated request until a review found it. Fix the raise; keep the
  guard for the next one.
- **A connection must be able to stall only for so long.** `MAX_CLIENTS`
  bounds the stream and nothing else, and `ThreadingHTTPServer` starts a
  thread per connection with no limit of its own, so `REQUEST_TIMEOUT` on
  the handler is what stops a client that promises a body and sends none
  from parking threads. The stream lifts it again once it starts writing,
  because a watcher on a quiet network legitimately says nothing for
  minutes and being cut off for it would be worse than no timeout at all.
- **Nothing that a request can influence may reach `hmac.compare_digest` as a
  `str` without an `isascii()` check first.** That includes `--web-token`,
  which is why `web_token_arg` refuses one that is non-ascii or holds a
  slash: both make the interface answer nothing at all, silently.
  `compare_digest` refuses a non-ascii `str` by raising, not by returning
  False, and `http.server` decodes the request line as latin-1, so any byte at
  all arrives. That is the raise the guard above was added for.
- **The `Host` check compares names under a loopback bind, and under a
  routable bind only when `--web-host` gave some.** `hosts_restricted` is
  where that is decided, once per bind in `bind()` from the address that was
  bound, and never per connection: a wildcard bind answers the same way on
  every interface. Jupyter, Syncthing and Ollama each arrived at the same
  rule, which is why it was chosen over the allow-list-everywhere shape Vite
  and Transmission use. In the open case `origin_allowed` is handed the
  request's own `Host` and accepts only an origin naming it, after an
  `isascii()` check on that header, which is text off the wire and would
  otherwise reach `compare_digest`. The names are held lowercased, ascii,
  without brackets or a port, which `web_host_arg` enforces for the reason
  `web_token_arg` does: a name stored with a port matches nothing for ever,
  and a non-ascii one raises on every key press. `*` is refused too: a
  routable bind already answers to any name, and the flag is an allow-list,
  not a pattern. The names are kept in the order given rather than in a set,
  because the first one is what the printed URL uses.
- **A subscription and the `finally` that gives it back belong in the same
  `try`.** `wfile` is unbuffered, so writing the response headers can raise if
  the browser has already gone, and a subscription taken before that `try`
  leaks a client the feed keeps publishing to and nothing drains.
- **Publishing must cost nothing when nobody is watching.** Every publish site
  asks `bus.active` first, and a record or a snapshot is built only then. The
  display path builds neither today, so a publish that assembled one
  speculatively would be real work per flow on a busy link.
- **Two keys do not cross.** `WEB_EXCLUDED` in `keys.py` keeps them back.
  `esc` because ending the process for everybody, this terminal included, is
  not something that should arrive as a side effect of mirroring a keyboard.
  `q` because there would be nothing for it to do: it draws the URL of the
  page as a QR code, for a terminal, on the terminal, and a browser showing
  that page has the address in its own bar. Allowing it would give a browser
  a key that appears to work and visibly does nothing.
- **The banner is rendered twice, and only for that.** The line pointing at
  the `q` key is the one thing the two readers are not shown alike, for the
  reason the `?` listing is: offering a browser a key the control route then
  refuses advertises something that is not there. `write_banner` takes a flag
  and `cli.main` calls it a second time only when the flag would change the
  answer. `test_web_keys` pins it in both directions, because the failure that
  costs anything is the quiet one where both copies come out the same.

The `--json` interactions are worth knowing because each of them is
unreachable from a terminal, so none of them existed as a question before this
did. Anything that draws on stdout has to ask whether there is a terminal to
draw on: the `x` key writes its clear escapes only when there is one, and so
does the `b` key, which otherwise puts a scroll region and two rows of status
bar into the middle of somebody's data and then repaints them twice a second.
Pause is the other way round, holding the browser view while stdout keeps
flowing, because `--json` is the part of the interface documented as parseable.
`test_web_keys` pins all three.

Whether a key may be pressed and whether it deserves a button are two
questions, so `keys.py` keeps two tables. `WEB_EXCLUDED` is what a browser may
not press at all, and holds `esc` and `q`. `WEB_UNLISTED` is what it may press
but gets no button for, and holds `?`: the drawer is already the list that key
would print, so a button reading "this list" beside the list would be absurd,
but somebody who knows the program will still reach for the key. `web_keys` is
the first set and `web_buttons` the second, derived from it, so a key can never
gain a button it is not allowed to press. The page asks `hello.pressable`, not
its own buttons, when deciding whether to answer a keystroke.

The `?` listing is the one place the two views are shown different text rather
than the same characters: the browser's copy leaves out `esc`, which it cannot
press. `controls.listing` writes to stderr directly rather than through
`controls.out`, which is a tee and would publish the listing a line at a time
dressed as replies to keys nobody pressed.

## There is a QR encoder in here

`qr.py` encodes the `--web` URL as a QR code and draws it out of half block
characters, which the `q` key prints. It is the one thing in this repository
that could obviously have been a dependency and deliberately is not, so the
reasoning is worth keeping.

What it would have cost is not what a dependency costs elsewhere. This program
installs two pure Python packages and nothing else, the suite has no
dependencies and is not meant to grow one, and the image pins every byte by
hash. The obvious library also carries `importlib-metadata`, and `zipp` behind
it, on the 3.9 that CI gates on, which would have made three statements about
what this installs false at once.

What it costs instead is about 250 lines against a standard fixed in 2015,
which is write-once code. It is small enough to be worth it only because the
payload is one URL of this program's own making, which lets the general case
go: **error correction level L and versions 1 to 5 only**, and those five are
one Reed-Solomon block each, so there is no interleaving; no version
information block, which starts at 7; one alignment pattern at `4V+10`, which
is where 2 to 5 put their only one; and an eight bit character count, which is
what byte mode uses below version 10. That leaves 106 bytes of URL, and the
longest this program builds is nowhere near it. Anything longer gets its URL
printed alone, which was the point of the block anyway.

Four things about it are easy to break and quiet when broken.

- **Three mistakes kill the symbol and three do not, and knowing which is
  which is the whole of testing this.** Reversing the format information bits,
  putting either copy on the wrong axis, or leaving the dark module unset
  produces a symbol no reader will take, and every one of those happened while
  this was being written. Starting the pad codewords with `0x11` rather than
  `0xEC`, or choosing a different one of the eight masks, produces a symbol
  that scans perfectly and is simply not the one intended: a reader takes the
  length from the character count indicator and never looks at the padding,
  and the format information says which mask was used. So a change that looks
  harmless because it still scans may still be wrong, and `test_qr` is what
  says so.
- **The masks are scored before the format information goes on, and with the
  dark module still light.** The standard asks for the first and `_skeleton`
  arranges the second by reserving that module without setting it;
  `_apply_format` turns it on, which is the only place a symbol is finished.
  Score with either in place and a different mask wins about one time in
  twenty. Both symbols scan, so nothing fails except the vectors.
- **The renderer uses no escape codes, and that is why it survives.**
  `PlainStream` takes SGR out for a reader without colour, so a symbol drawn
  with reverse video would come out as blank lines in a redirected run while
  looking right on a terminal. Half blocks are characters and pass through
  untouched. Dark modules are drawn as the window's background, which is right
  on a dark terminal and inverted on a light one, and there is no way to ask
  which it is.
- **A symbol that will not fit is not drawn at all.** `fits` asks about
  columns and about rows, and `cli.main` measures the scroll region rather
  than the window, because a sticky header and a status bar have taken rows
  off either end. A wrapped code and a code whose top has scrolled away are
  both unreadable rather than merely worse, and the URL underneath costs the
  reader nothing.

The vectors in `test_qr.py` were taken from segno, once, with a correction
applied to it: its `write_padding_bits` extends the stream by a whole codeword
when the data already ends on a codeword boundary, which in byte mode is
always. The difference is harmless for the reason above, but anybody
regenerating those vectors has to apply the same correction or every symbol in
the file moves by one pad codeword and its check bytes with it.

There is no scanner in the suite and there cannot be one, so the manual check
is the real acceptance step: press `q` and scan what comes up, once on a dark
terminal and once on a light one.


## One frame, one append

Nothing the stream carries reaches the table as it arrives. A row is built when
its event comes in, held in a `DocumentFragment`, and put on the page once per
animation frame, with a single `toTail` at the end of it.

What that removes is layout, not building. `toTail` reads `scrollHeight`, which
makes the browser lay the table out there and then, and a table lays out whole:
under `table-layout: auto` every column is as wide as the widest cell in it, so
laying out after one append is a pass over every row the page is holding. A
reconnect hands over a backlog of up to `CLIENT_BACKLOG` events inside a single
task, and letting go of pause does the same. A task that spends itself on
thousands of full-table layouts is, from the outside, a tab that has frozen. A
long session seizing up looked at first like the memory the history takes, and
the code says it is this.

Four things about the arrangement are easy to break.

- **Everything that changes the table goes through the queue.** Flows, prose,
  the notes the page writes itself and the clear, so that the order they
  arrived in is the order they appear in. A queue for some of them and a direct
  append for the rest behaves perfectly well on a quiet link and reorders the
  moment two land in one frame. `test_web_server` greps the page for
  `rows.appendChild(` outside `paint`, blunt in the way the `innerHTML` check
  is blunt and for the same reason: an append put back somewhere else fails
  nothing until the link is busy.
- **A clear takes the queue with it.** Rows already waiting were on their way
  to a table the reader has just emptied, so `clearTable` drops the fragment
  and starts another. A clear followed by flows inside one frame has to leave
  the flows and nothing else.
- **The gap row goes on inside `paint` rather than through the queue.** It is
  about a trim that has just happened, and a queued note waits for a frame that
  only arrives if something else does, which on a link that has just gone quiet
  can be minutes.
- **The page and the queue behind it are one history and are bounded as one.**
  Animation frames do not run in a tab that is hidden or merely starved, and
  such a tab goes on taking events until `park` closes the stream, so rows
  waiting to be shown cost what rows on the page cost and are trimmed too.
  `keep` counts the two together and takes the oldest first wherever the oldest
  is, which is the page before the queue. Bounding them separately looks
  reasonable and is not: it drops the newest thousand rows while older ones sit
  on the page untouched, and leaves the gap in the middle of the history rather
  than at the top of it, where the note that follows says it is. `untold`
  carries the fact to that note whichever of the two the rows went from, and
  the count restarts in `clearTable` rather than in `wipe`, a frame later, so
  that a trim between the two is still reported rather than forgotten with
  everything the clear threw away.

There is no browser in the suite, so none of this can be pinned by a test that
runs it. The manual check is to pause with `space`, let a few thousand flows be
held, let go, and watch the tab stay responsive.

## A hidden tab gives up its stream

`web.html` closes its `EventSource` when the tab goes to the background and
opens a new one when it comes back. This is the fix for a real crash and not a
nicety, so it should not be quietly dropped.

A hidden tab is throttled and, after a few minutes, may be frozen. No script of
ours runs then, but the browser keeps reading the socket and buffering the
response body, and on a busy link that grows until the tab is killed for
memory. Everything the page could do about it from the inside is too late: by
the time a handler runs, the memory has been spent. Not having the connection
open is the only thing that works, and it works whether the tab was throttled
or frozen outright.

Three separate things ask for it, and none of them covers the others. Keep all
three. The visibility flag with its fifteen second grace is the ordinary case,
and it misses a window that is starved without ever being marked hidden, which
is what minimising one reliably does. The `freeze` event is the load-bearing
one, because the buffer is what puts the tab under memory pressure in the first
place: the browser freezes the tab for it, a frozen tab runs no timers, and the
grace timer that would have closed the connection therefore never fires. The
clock watcher covers the rest, parking when a one second interval comes back
ten seconds late, on the grounds that a page being run that seldom is not
draining anything whatever the flags say. It cannot cover the freeze itself,
since it is a timer too.

Four things about that arrangement are easy to get wrong.

- **`park` cancels the grace timer, and has to.** With one caller it did not
  matter. With three, a pending timeout left to run fires after the tab is back
  and the stream is live again, and parks a tab somebody is looking at.
- **`stream === null` is not the same question as parked.** A refused
  connection and one abandoned after five failures both leave it null, and the
  clock watcher must not reopen either of those. That is what `parked` is for,
  and why it is cleared in `connect` rather than at each call site.
- **Every cause has to note the Follow box, and one return has to restore it.**
  The visibility path notes it on the way out, before the grace. The other two
  have no way out to note it on, so `park` takes it when nothing has, and
  `takeBack` puts it back. Restoring only in the visibility handler leaves the
  starved-window case, which is the one all this was written for, coming back
  to a page that has quietly stopped following.
- **A dead `EventSource` must not stay assigned.** Both branches that stop
  trying tell the reader to reload, so both close the stream, null it and set
  `gaveUp`. Left assigned, a refused connection reads as a live one, and the
  clock watcher parks it, takes the refusal off the indicator and reconnects
  into the same full cap to be refused again.

The count shown on return comes from `flows_shown` in the status payload,
counted in the receive loop where `should_show` passes. It has to be counted
there rather than in `feed.py`, because the feed stops publishing when nobody
is subscribed, which is exactly the stretch the count is about. It is flows
shown and not `snap["flows"]`, which counts every flow decoded: under
`--external-only` those differ by a lot, and the number is meant to say what
was missed rather than what happened.

The same fact about publishing decides where the page reads it. `feed.hello`
splices in the last status published, and nothing was published during the
gap, so a greeting hands a returning tab the figure it noted on its way out:
subtracting the two is zero every time, and the report says nothing at all.
The page therefore waits for a `status` frame, which follows within a repaint
interval. `test_web_keys` pins the greeting as the stale one, deliberately, so
that reading it there fails rather than going quiet.

## Text off the wire is text

Hostnames come from reverse DNS, mDNS and NetBIOS, every one of which is a
string a machine on this network chose. They reach the page inside flow cells
and inside captured prose. `web.html` therefore builds every character through
`textContent`, and its ANSI converter creates spans and fills them rather than
assembling markup. `test_web_server` greps the shipped page for `innerHTML =`
and its relatives, which is a blunt check that has the merit of failing the
moment somebody reaches for the easy thing.

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
- **Flow rows go to stdout; everything else goes to stderr**: the banner, the
  `?` listing, the host list, the summary, and every warning. That is what
  keeps `--json` and shell redirection usable. There is now a third
  destination, the feed, and it takes a copy of both rather than replacing
  either. `cli.tee` is how prose reaches it, and it works by pointing the
  existing `out=` parameter at a buffer, which is why none of those functions
  had to change.
- **Colour is decided per reader, and painted once.** There are two consumers
  and they need not agree: `--colour` is the terminal's switch and `NO_COLOR`
  is scoped to it, `--web-colour` is the browser's and defaults to on, because
  a browser is colour-capable whatever stdout is. `colour_choice` in `cli.py`
  answers both. When they agree the codes are blanked at the source with
  `C.disable()`, exactly as before; when they differ the codes are painted and
  taken out at the boundary by `PlainStream`, wrapped around stdout and stderr
  in `main()`. That wrapping must come after the `reconfigure` loop, which
  wants the real streams, and after the `isatty` that decides the question.
  **The stripper takes SGR and nothing else.** `sticky.py` and `statusbar.py`
  write scroll margins, cursor moves and erases to the same stream, and a
  general ANSI strip would leave the display drawing over itself while looking
  right in a file. `tee` renders twice when the two disagree, which is the one
  concession: the host list marks a superseded name with a star when there is
  no colour to dim it with, so a reader without colour is shown different
  words and not the same words undressed. `colour_on(stream)` is what that
  site asks, never `C.enabled()`. One switch for both is what this replaced,
  and the case it got wrong was the one the image exists for: a detached
  container has no terminal, so the browser view came out white.
- **Every `Resolver(...)` passes an explicit `mode`.** lanname 0.2.0 changed a
  bare `Resolver()` from looking nothing up to querying reverse DNS, with
  nothing raised and nothing warned. Explicit modes are why that release was a
  non-event here. Keep it true.
- **Dependency direction runs one way**, from `cli` down towards `colour` and
  `values`. There are no import cycles and there should not be.

## The installer

`scripts/install.sh` covers both deployments, systemd and Docker, and asks
which. Three things in it are deliberate:

- **The web token lives in `/etc/nettail/nettail.env` and is never
  regenerated.** Re-running the installer to pick up a new version must not
  invalidate a URL somebody has bookmarked. It is passed through the
  environment rather than on the command line so that it stays out of `ps`.
- **`--web-bind` is left at its loopback default in both paths.** Putting the
  view on the network is a decision to make by editing the unit or the compose
  file, not one an installer makes quietly on somebody's behalf.
- **`useradd --user-group`, explicitly.** The unit says `Group=nettail`, and
  whether a bare `useradd` creates a matching group depends on `USERGROUPS_ENAB`
  and so on the distribution.

It is safe to run twice, and that is worth keeping: an existing user, virtual
environment and token are all reused rather than replaced.

## The container image

The image is built for `--web`. The console display wants a real terminal and a
detached container has none, so the browser view is the only mode that works
properly there. That is why `CMD` is `--web --web-bind 0.0.0.0` and not a bare
collector.

Three things about it are easy to get wrong later:

- **`--web-bind 0.0.0.0` is not a lowered guard.** Loopback inside a container
  belongs to the container's namespace, so the program's own default would
  answer nothing through a published port. The guarantee moves to the publish,
  where `-p 127.0.0.1:2056:2056` is exactly as private as the default was.
- **`in_container()` in `web.py` may only choose prose.** It guesses, from
  markers no runtime promises, and a guess is acceptable precisely because a
  wrong answer changes a sentence rather than an address. If it ever decides
  what gets bound, that trade stops holding.
- **Host networking is not a nicety.** Behind the bridge, the address a
  datagram came from is rewritten to the gateway, so every exporter shows up as
  `172.17.0.1` and the EXPORTER column stops distinguishing anything. That is
  measured, not feared. `docker-compose.yml` uses `network_mode: host` for it,
  and the README says why.

Everything the image installs is pinned by version and by hash in
`requirements.lock`, with `requirements-build.lock` doing the same for the one
package needed to build the wheel. `scripts/lock_hashes.py` refreshes both from
the index's own digests; `--check` fails if either has drifted.

CI builds the image for amd64 and starts it, for the reason the rest of the
suite exists: a thing exercised only at tag time rots silently, and the first
anybody hears of it is a failed release. It also asserts that a container start
prints the container line and not the host warning.

The two registries are separate jobs. GHCR authenticates with the workflow's
own token, so it cannot fail for want of a secret; Docker Hub needs a stored
one, and in a job of its own a rotated token or an outage there costs Docker
Hub and nothing else. The GitHub release waits for GHCR, so a release page
cannot announce an image that was never pushed.

The publishing itself lives in `mjaksn/workflows` and is called from here, pinned
by commit like any other third-party step. It was the same hundred and forty lines
in three repositories before that, and they had begun to drift. Two calls rather
than one, and that is load-bearing: a called workflow succeeds only when every job
in it succeeds, so a single call covering both registries would put Docker Hub back
in front of the release page. The Dockerfile stays here, with the thing it packages.

Because the shared file is now one point of failure for three releases, and a
release is the hardest thing here to rehearse, CI calls the GHCR half with
`push: false` on one platform. That is what the `rehearsal` job is, and it is why
the publishing path is exercised on a pull request rather than first at tag time.

## Prose

Comments and docstrings here carry the reasoning, not a restatement of the
code, and they are written as prose. Match the surrounding voice, and note
that it is deliberately free of em dashes and of double hyphens used as
punctuation. When a rewritten line changes a paragraph's shape, rewrap the
paragraph to the width the file already uses.
