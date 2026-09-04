# AGENTS.md

Guidance for AI coding agents working in this repository. Claude Code reaches
it through `CLAUDE.md`, which is a pointer at this file and nothing else.

## Work in a worktree, not in this checkout

**Cut a worktree and work in that, before making the first change.** The
failure this guards against is two agents editing one checkout at the same
time, and every part of it is quiet: each reads the other's half-finished
edits as its own, a suite fails for a reason nothing in the branch explains,
and a commit carries away work belonging to a change somebody else was in the
middle of. Nothing announces any of it, and the repository root is where a
person is most likely to be sitting.

So fetch, and cut a branch from `origin/main` into a worktree under
`.claude/worktrees/`, which is where this repository already keeps them and
which `.gitignore` already covers. `EnterWorktree` does it, an `Agent` given
`isolation: "worktree"` does it, and by hand it is
`git worktree add .claude/worktrees/<name> -b <name> origin/main`.

The suite runs in a worktree with no further setup, which is worth saying
because there is no virtual environment in one. `tests/harness.py` puts its
own root at the front of `sys.path` and of `PYTHONPATH`, and its own root is
the worktree, so an interpreter from anywhere imports the package under test
rather than whatever else is installed. It has to be an interpreter that has
`lanname` and `netflume`, though: one without them fails every suite at import
and says nothing about the code.

Three kinds of work stay in the main checkout, and all three are work a
worktree cannot see or cannot reach.

- **Anything about uncommitted work there.** Committing what is already in the
  tree, saying what has changed, or chasing a failure that only happens with
  those edits in place. A worktree has none of it.
- **Anything about the repository rather than the code**: pruning branches,
  tagging a release, listing or removing worktrees. These act on one shared
  git directory whichever checkout runs them, and a branch that is checked out
  in a worktree cannot be deleted at all.
- **Reading, when nothing is going to be written.** Answering a question or
  reviewing what is there costs a worktree nothing and gains nothing, and the
  checkout is likely to be the tree the question is about.

And being told to, which needs no reason.

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

It has one thing of its own now, which is the details dialog a click on a row
opens. That is not a mirror of anything, since there is nowhere on a console
to put it, but the rule above still holds inside it: every value in that
dialog is worked out and written out by the collector and the page names no
field, no flag and no protocol. See "Asking about a flow".

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
  dispatch, the startup reminder line and `WEB_UNLISTED`, which keeps its
  button off a browser while leaving the key pressable. `QR_KEY` is `q` in the
  same way, and the thing it has to agree with is `WEB_EXCLUDED`, since it is
  kept back from the browser altogether.

  A key that turns part of the display on also wants a flag beside it, and
  `test_key_help` holds that too: every key is either an act, which is
  something to do rather than something to set, or a setting whose dest a
  command line option moves as well. A key is for changing your mind and a
  flag is for saying so at the start, and the flag is what lets a settings
  file hold it at all, since a file says what the command line says and
  nothing more. `--names` and `--macs` exist because the n and p keys were
  the two that had no flag; the check is what stops the next one arriving the
  same way. A new key is either added to that suite's list of acts or given
  its flag, and until somebody says which, it fails.
- **`EPHEMERAL_FLOOR` in `services.py`** is netflume's, imported from
  `netflume.values`. It was a copy while netflume had the number inline and
  exported nothing, and `test_services` found where netflume actually stopped
  naming ports and held the copy to it; netflume 0.2.0 published the constant
  and both went. What is checked now is only that it is still the same object,
  since an import quietly becoming a local again would read the same.
- **`TCP_FLAG_NAMES` and `FIELD_LABELS` in `detail.py`** are the same kind of
  deal with the same upstream. The first is keyed by the letters
  `TCP_FLAG_BITS` writes, the second by the names in `netflume.IE`, and
  `test_detail` holds each to its table in both directions. An element added
  upstream would otherwise reach a reader as a bare key in a dialog whose
  whole claim is that it spells everything out, and nothing would fail.
- **`COLUMNS` in `display.py`** is every column, its width, its alignment and
  the gap in front of it. `HEADER_LINE` is built from it, so is
  `ENDPOINT_INDENT`, and so is the table head the browser draws, which arrives
  over the wire in the `hello` event rather than being written down again in
  `web.html`. The widths travel with it and are what the page's `colgroup` is
  built from, a character count being a width on the page because the font is
  monospace. `FLAGS` is the one column `COLUMNS` gives no width, since a
  terminal has nothing to pad it against, and `FLAGS_WIDTH` fills that in for
  the table by asking netflume how long the string it produces is rather than
  counting it off a screen. The same goes for the buttons, which come from
  `KEYS` by the same route. **The page hardcodes nothing the terminal already
  names**, and that is the rule to hold: a second list of columns or keys
  living in the HTML is a second thing to go stale, and nothing in the page
  would fail loudly when it did.
- **`row_cells` in `display.py`** builds one flow's cells once, plain and
  painted, and both views use them. A browser must never work a cell out for
  itself. It could not do it correctly in any case, since a service name is
  whatever this machine's services database calls that port, and reimplementing
  the protocol names, the size ramp or the arrow in JavaScript would be two
  implementations to keep in step.

  **`extra_lines` beside it is the same bargain for what goes under a row**,
  and it exists because that bargain was not being kept. The `p` key writes the
  hardware addresses and the `v` key writes the fields the row has no column
  for, and both were built inside `render`, which is the terminal's path and
  only ever the terminal's. A browser was handed the cells and nothing else, so
  either key moved the console and left the page exactly as it was, whichever
  view it was pressed from. Anything new that goes under a flow belongs here
  rather than in `render`, and the terminal's indent travels with it: the font
  is monospace at both ends, and a style threaded through so the two views
  could disagree about it is what this whole arrangement avoids.
- **`Controls.toggles` in `keys.py`** is which keys are showing as on, and the
  browser draws every active key from it. The page held its own list before,
  of four, so `b`, `n`, `p`, `g` and `h` never lit however they were pressed
  and nothing failed: a key missing from a table written in JavaScript is
  invisible rather than wrong. `test_key_help` holds it against `KEYS` in both
  directions, and `test_web_server` greps the page for a key named inside
  `setToggles`, bluntly, the way it greps for `innerHTML`. Two entries are not
  the plain yes or no the rest are and both are answered in Python: `h` cycles
  three ways and counts as on whenever names are being looked up at all, and
  pause is an act with no flag behind it but is plainly on or off while a run
  is going.
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
bare port number. Three other files are listed there for the same kind of
reason: `web.html`, `flags.woff2` and `flags-licence`. CI installs the wheel
and asserts all four are in it.

## Countries

`country.py` reads a MaxMind format database and answers "what country is this
address in". It is off unless `--country` asks for it and silent unless there
is a file to read, and no country data ships with this program, which is the
whole shape of the feature: what a flag says is whatever the reader's file
says. Since 0.11.0 it will fetch one, and only ever after somebody at a
terminal has said yes.

Reading the format rather than depending on `maxminddb` is the same trade
`qr.py` makes, and rests on the same three facts: this installs three pure
Python packages and nothing else, the suite has no dependencies, and the image
pins every byte by hash. The whole format is decoded rather than the part a
country database uses, because people will point this at a City database they
already have and being told to fetch a second file would be absurd.

Six things about it are easy to break and quiet when broken.

- **The flag is painted at the source and spelled out at the boundary.** A
  terminal that cannot draw one is behind `country.CodeStream`, which turns
  each regional indicator back into its letter, exactly as `PlainStream` takes
  colour out for a reader who is not having it. The reason it has to be a
  boundary and not a setting is `write_summary`: the report is rendered once
  and read by a terminal and a browser together, and `tee`'s `per_reader` is
  not available to it for the reasons written on `tee`. A style threaded
  through `row_cells`, the summary and the bar would also be a fourth thing
  for the two views to disagree about.
- **The browser is sent a flag, and is now also sent something to draw it
  with.** Nothing on the feed's route passes a terminal, so nothing spells a
  flag out there. What a browser then draws was for a while not this program's
  to decide: no monospace font has a glyph for a regional indicator pair, and
  Windows has no emoji font that draws a flag at all, so Chrome and Edge there
  drew the two letters in boxes. `flags.woff2` is the answer and is the one
  exception to the page being a single file; the reasoning sits on `FONT_FILE`
  in `web.py`. Three things about it are easy to undo: the page asks for it
  through `BASE`, because a relative url in CSS resolves against whichever of
  the page's two addresses the reader arrived by; it asks only when a status
  frame says `countries`, because `FontFace.load()` fetches when it is called
  and a face built at startup sent 78 KB to every browser watching a run that
  would never draw a flag; and the emoji families still named after the
  monospace ones are the fallback for a build with the font left out. The
  `unicodeRange` on the face is what keeps it off every other character once
  it is there. `flags-licence` has to stay in the wheel beside it, since the
  artwork is CC BY 4.0 and the credit travels with the material.
  `test_country` holds the licence's checksum to the font that is actually
  there, and `test_web_server` fetches the route.
- **Both forms are two characters wide**, on screen and to `len`, and every
  column in the program measures its contents with `len`. That is what lets
  `endpoint`, `with_names` and the status bar go on padding as they always
  did. A marker is three characters, the space included, in either form. A
  change that made one form wider than the other would leave every column
  that holds an address a character out, and only in one of the two views.
- **`_record` packs 28 bit records with the high nibble to the left.** Get
  that backwards and the tree still walks perfectly and answers with somebody
  else's country. `test_country` builds the same database at all three widths
  and asks it the same questions, which is the only thing that catches it,
  since 24 and 32 have no nibble to get wrong. It is the one width no real
  file has confirmed here: the free country databases are 24 bit, so what the
  packing rests on is the specification and the suite's own three way
  agreement.
- **An IPv4 address in an IPv6 database is at ::/96**, which is ninety six
  zero bits before the address itself. `_bits` arranges that by reading the
  same number as 128 bits wide rather than 32, which puts the zeros there
  already. A reader that skipped them answers from whatever sits at the top of
  the v6 tree, for every address.
- **Whether a database is loaded and whether the display is marking are two
  questions.** `--json` carries `src_country` and `dst_country` whenever one
  is loaded, the way it carries `src_host` whatever the n key is doing; the g
  key moves the second and not the first. Both live on the module rather than
  on `args`, where the other display switches live, because `display`, `cli`
  and `statusbar` all ask and share no arguments. It is what `services` does
  and the reasoning is written where the state is.

### Fetching one

A run that searches and finds nothing offers to fetch a database, and
`--update-country-db` asks for one outright. Eight things about that are worth
knowing before touching it.

- **A HEAD settles whether there is an offer to make, and it goes after both
  guards rather than before them.** `probe` asks db-ip.com for the headers of
  both months and hands back the URL that answered, the size it named and what
  went wrong. It exists so that nobody is put a question whose yes could not
  have been carried out: a reader with no route out would otherwise say yes,
  wait, and be told it failed, when the useful answer was always the two
  download pages, which is what `find_online` gives them. Two things about its
  position are load-bearing. It is the only request this program makes before
  anybody has agreed to anything, so it is announced on the line above itself,
  the way `config` prints which file it read: the line is the mitigation, not
  decoration. And it sits below the terminal check, so a run under systemd or
  cron or docker makes no request at all rather than merely asking no question;
  `test_country` pins that with a probe that counts its calls. `PROBE_TIMEOUT`
  is shorter than `DOWNLOAD_TIMEOUT` on purpose, because the machine this
  matters on is the one that drops packets rather than refusing them, and
  thirty seconds of that in front of a collector that runs perfectly well
  anyway looks like a program that has hung.

- **The licence decides which publisher can be offered, and there is only
  one.** DB-IP's lite build is a plain URL under Creative Commons Attribution
  4.0, and their terms of service put the free files outside their own terms
  and under that licence expressly. MaxMind's GeoLite2 wants an account and a
  licence key before a byte moves, and obliges a holder to delete a database
  within thirty days of a newer one. Neither is a thing a yes at a prompt can
  stand in for, so GeoLite2 is named in the declined message and nowhere else.
  A change that added a second publisher here is a licence question before it
  is a code question.
- **That licence asks for a credit, and this program pays it rather than
  leaving it to the reader.** `credit()` answers the words and the address for
  a DB-IP file, `describe()` puts them on the startup line, and `web_status`
  sends the pair so the page can make the link DB-IP's own wording asks a web
  page for. It is decided from `database_type` and not from whether this
  program did the fetching: a DB-IP file somebody installed by hand is under
  the same terms. The page hardcodes neither the words nor the address, for
  the reason it hardcodes no column and no key, and `test_country` greps it
  for both.
- **`missing()` is not `not loaded()`.** Only a search that found nothing is
  worth offering a fetch for. A `--country-db` naming a file that is not there
  is a typo, and a file that was found and would not read is a file to name
  rather than a reason to fetch another: the broken one is still first in the
  search order, so a copy fetched into some other directory would be found
  second by every later run and never read.
- **Both stdin and stderr have to be a terminal before anything is asked.**
  This program runs from systemd, from cron and inside a container far more
  often than it runs from a keyboard, and a question written where nobody is
  reading and answered by whatever a pipe held is a program that downloads a
  file because it was run from cron. The question goes to stderr and not
  through `input`, whose prompt goes to stdout, which is where the flow rows
  go. Nothing is asked either when `destination()` answers None, since a yes
  could only have been followed by a refusal.
- **The Unix search list ends with a per-user path, and that is what makes a
  yes mean anything.** Every path above it belongs to root. It is last rather
  than first so that a machine syncing a database into `/usr/share/GeoIP` goes
  on reading the copy something else keeps current. `destination()` is what
  both the fetch and the "put one at" hint name, so the two cannot disagree,
  and it creates nothing on its way past: a hint that made `/etc/nettail`
  would be doing something nobody asked for.
- **What is fetched is opened before it is moved into place.** The bytes came
  off the network, and a half written or plainly wrong file left under a name
  the search looks in would be found by every later run and refused by every
  one of them, which is worse than the state it started in. That check is also
  why `Database.__init__` closes its mapping when it raises: on Windows a
  mapped file cannot be deleted or replaced, so without it the very caller
  that opens a file to decide whether to throw it away cannot then throw it
  away.
- **A refresh writes only where the next run will read, and `update_target` is
  the whole of that rule.** `--update-country-db` is the offer's other half:
  the same file from the same publisher, fetched because somebody typed a flag
  rather than because a prompt caught them. Every guard on `offer_country_db`
  is about not mistaking an empty pipe for a yes, so none of them applies here
  and none is kept: no question, no terminal check, and no probe either, since
  the probe exists to avoid putting a question whose yes could not be carried
  out and there is no question. What is left to get wrong is where it writes.
  A named `--country-db` is the file, whatever the search would have said.
  Otherwise the search order decides, and a database above `destination()`
  refuses the fetch and is named, because a copy written below it would be
  found second and opened never, which is the same trap `missing()` avoids one
  door along. The two are compared by their index in `search_paths()` rather
  than as paths, which needs no rule about case, separators or symbolic links:
  whatever `destination()` answers came out of that list in the first place.
  The errand answers with an exit status rather than a note, which is why
  `__main__.py` raises `SystemExit(main())`, and it is in `config.UNSETTABLE`
  beside `--save-config`, because an errand a settings file could hold would
  reach out to db-ip.com and exit on every run for ever after. The line naming
  a file about to be replaced is built from `kind()` and `built()` rather than
  from `describe()`, and that is not an accident: `describe()` ends an old
  file's line by naming the flag that would fetch a newer one, and printing
  that to somebody who has just typed the flag answers a question they have
  already acted on.

`download` and `probe` both take their opener, so the suite exercises the
fetch, the probe, both months, every failure and the offer around them without
touching the network. There is no check that the real URL is still there and
there should not be one: a suite that fails when db-ip.com is down is a suite
that fails for reasons that are none of this program's business. What the fake
opener stands in for was checked by hand against the real server, and is worth
rechecking if any of it is changed: a month that is there answers a HEAD with
200 and a `Content-Length`, and a month that is not answers a plain 404 rather
than a 403 or a redirect, which is what makes the fall back to the previous
month work.

The size in the offer comes from that `Content-Length` rather than from a
figure in the source. That is deliberate and worth keeping: the file grows
every month, and a number written into the prose would be wrong within a year
and wrong in the two places the prose lives. The size in the README's sample
run is a snapshot of one run and reads as one, which is what a transcript is
for; it is not a second claim about the file. Where the server names no length
the whole clause goes, destination included, because "about twice that" with
no size before it is a sentence about nothing, and that is what `test_country`
pins rather than merely the absence of a figure.

Nothing in the suite draws on a terminal or opens a browser, so the manual
check is the real acceptance step, as it is for the QR code. Run with
`--country` at a real terminal and confirm the columns still line up with a
flag in them, and open `--web` beside a `--country-style code` terminal to see
the two views differ in the one way they are meant to. The offer has a manual
step of its own, and it is the only place a real fetch happens: move whatever
database this machine has out of the way, run `--country`, say yes, and check
that what comes down reads and that the credit is on the startup line and in
the browser's status bar.

`terminal_flags` is a guess and may only choose prose, in the sense
`in_container` may: there is no query for "can you draw a flag", so what it
knows is where one is certainly not drawn. It takes the environment and the
platform as arguments so that every branch can be asked about from a runner
that is none of them.

## Settings from a file

`config.py` reads `nettail.conf` and writes one. The feature's whole claim is
that anything settable on the command line is settable in a file, and it holds
because **there is no list of what can be set**: `settable(parser)` reads the
parser, so `build_parser` goes on being the one place an option exists.
`test_config` types every option and writes every option and asserts the two
runs come out with the same arguments, and a new option with no sample value
in that suite fails rather than turning out to be quietly unsettable.

Six things about it are easy to break.

- **The file is read before the arguments are parsed, and that is what makes
  the command line win.** Its settings are installed with `set_defaults`, and
  an argument overrides a default. Merged after the parse it could only have
  gone the other way round: by then argparse cannot tell a value that was
  typed from a default that happens to equal it, so a file would silently
  beat the command line. This is also why the two config options are read by
  a small parser of their own first; which file to read may itself be an
  argument.
- **`--save-config` compares against a baseline taken before any of that.**
  Once a file's settings are the parser's defaults, a value that came from the
  file is indistinguishable from one nobody ever chose, and a run that loaded
  a config and saved it again would write every one of them back out as a
  comment. `main` takes `config.defaults(ap)` before `set_defaults` and hands
  it to `write`. There is a check for it in both directions, because the
  failure is a file that looks fine and has lost half of itself.
- **Which file was read is printed at startup, every time.** The search starts
  in the working directory, which is what makes a per-directory config
  possible and is also a file somebody else may have put there. The line is
  the whole mitigation and is not decoration; a run that quietly took its
  options from a stranger's file would be worse than not having the feature.
  It is printed after the colour is settled rather than where the file is
  read, because nothing may print before that.
- **A repeatable option adds rather than replaces.** `--hosts` typed beside a
  file that lists two gives three, because that is what repeatable means
  everywhere else in this program. It is the one place "the command line
  wins" reads differently and it is written down in three places for that
  reason.
- **Two options that are alternatives are the one place the ordering does not
  settle it.** Everywhere else the file and the command line are arguing about
  one option, so installing the file as a default and letting an argument
  override it is the whole mechanism. A mutually exclusive group is two
  options that mean opposite things, and argparse refuses the second only when
  it was typed, so a file's `size-scale-max` and a typed
  `--size-scale-dynamic` both survive the parse and nothing downstream can
  tell a choice was ever meant. That is the file beating the command line at
  the one thing the ordering exists to prevent, and worse than the ordinary
  case, because the file's value does not lose an argument, it survives into a
  run that asked for its opposite. `config.overruled` puts the file's side
  back, quietly, which is what every other option does when the command line
  overrides it. It decides nothing about a pair that came from one place: two
  typed argparse has refused already, and two out of one file is what
  `conflicts` reports. `EXCLUSIVE_PAIRS` is there because
  `--size-scale-window` rules out `--size-scale-max` and not
  `--size-scale-dynamic`, which it implies, and an argparse group excludes in
  every direction at once, so that pair cannot be expressed as one.
- **A token goes back only where it already was.** `NEVER_WRITTEN` keeps
  `--web-token` out of a saved file, and `keep` is the exception `main` allows
  when the file about to be written is the file the settings came from. That
  is not a softening of the rule: a bare `--save-config` writes
  `~/.nettail/nettail.conf`, which is the second place the search looks, so
  the file being written is usually the file just read, and dropping the token
  there mints a fresh one at the next restart and breaks every bookmarked URL.
  The rule the code enforces is that a file which never had a secret never
  gets one.

Four smaller things, each of which was a real defect before it was a rule.
`read` opens with `utf-8-sig` and catches `UnicodeDecodeError`, because a
mark left by Notepad became part of the first key and a file saved as UTF-16
by PowerShell was a traceback out of `main` before the socket was bound.
`parse` registers every long flag an action has, not the first, or `color` and
`web-color` would be names the command line takes and a file does not.
`conflicts` exists because argparse enforces a mutually exclusive group
against what was typed, so two of its options arriving as defaults walk
straight through it. `settings` asks whether a file was named and not whether
the name has anything in it, because `main` asks it that way too when it
decides a named file that will not read is an error: asked as truthiness,
`--config ""` went back to searching, and a script written as `--config
"$CONF"` with the variable unset would have taken its settings from whatever
the working directory held, which is the one file the printed line exists to
warn about.

`settable` reaches for `parser._actions`, which is argparse's own and has no
public spelling. There is no API for "what options does this parser have", and
the alternative to the attribute is writing the options out again, which is
the thing the module exists not to do.

`--web-token` is read from a file and never written to one. A settings file is
the file people edit, copy between machines and paste into an issue, and the
token is the one thing here this program already goes to trouble to keep out
of `ps`. `NEVER_WRITTEN` is where that lives.

## The web interface

`feed.py` is the bus and knows nothing about HTTP; `web.py` is the server and
touches no collector state. Between them sits one rule that everything else is
arranged around: **a request thread may read a feed queue and put a key or a
question on a queue, and that is the whole of its authority.** Everything that
changes what the collector is doing, and everything that reads what it has
counted, happens on the receive thread, which drains both queues between
datagrams: a key goes to the same `Controls.handle` the terminal uses, and a
question about a flow goes to `detail.report`, which is written for being
called there. See "Asking about a flow" below.

Nine things about it are easy to break and quiet when broken.

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
- **One key goes the other way, and it is the page's own.** The down arrow
  works the Follow box, and `web.html` answers it without telling anybody.
  That is not the page hardcoding something the terminal names: following the
  tail is a fact about one tab, the collector holds no state for it and has no
  name for it, and two windows on one run scroll independently. So it is in
  neither `KEYS` nor the greeting, gets no button, and is in neither listing,
  and nothing about the parity `test_key_help` holds is touched by it. The
  terminal has no equivalent to offer: a console cannot hold its view still
  without holding the flows back, which is what `space` already does, and
  `Keyboard.poll` swallows the arrow sequences on both platforms so that a
  cursor key is never read as the escape that closes the program. Three things
  about where it sits in the keydown handler are easy to undo and quiet when
  undone, and `test_web_server` greps for all three: it is answered above the
  readonly guard, because nothing about it reaches the collector and the
  reader of a display-only session is the one most likely to want it; above
  the guard that leaves a keystroke to a focused input, because clicking the
  box leaves the focus on it and the arrow pressed straight afterwards is the
  natural way to change your mind; and with `preventDefault`, because the
  browser's own answer to the arrow is to scroll, the scroll handler reads the
  position back into the box, and at the tail that puts the tick straight back
  on. That is a guard rather than an observed scroll: `body` is
  `overflow: hidden` and the flows scroll inside `main`, so whether the arrow
  scrolls anything at all depends on what the browser has decided to focus.
  It works the box with `click()` rather than by assigning `checked`,
  which fires no `change` event, so the one handler that decides what turning
  Follow on does stays the one handler. It is answered below one guard rather
  than above it: nothing is forwarded at all while a flow dialog is open,
  because the dialog is what the reader is looking at and the arrow scrolls
  what is in it. `test_web_server` greps for that order too, since flipping it
  would toggle Follow behind a dialog and nothing would complain.
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

The `b` key is the one of those where the setting and the drawing had to come
apart, and answering them as one was a defect rather than a simplification. It
means the status bar, and there are two of those: the rows on the terminal and
the footer in the browser. `hide_status` is what both read, so the key moves it
whatever is watching, and a run with no terminal used to leave it exactly where
it started, which is why the key did nothing anywhere and a reader pressing it
in a browser watched their own footer stay put. What the guards still decide is
whether the bar on stdout draws, never what the setting says. The browser's
footer keeps the country credit when it hides the figures, because the flags
are still up in the rows above and CC BY 4.0 asks for the attribution to be
wherever the material is: a reader who wanted fewer figures did not waive
DB-IP's credit.

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
than the same characters: the browser's copy leaves out `esc` and `q`, neither
of which it can press. `controls.listing` writes to stderr directly rather than
through `controls.out`, which is a tee and would publish the listing a line at
a time dressed as replies to keys nobody pressed.

### Asking about a flow

Clicking a row in the browser opens a dialog holding everything known about
that flow, the datagram it arrived in, statistics for each of its two ends, and
statistics for the pair. `traffic.py` accumulates the last two, `detail.py`
writes the report, and `web.py` takes the question. The terminal has no
equivalent and that is accepted: there is nowhere on a console to put it.

The reasoning that is not in the code:

- **The report is built on the receive thread, and there was never a choice
  about that.** The tally is mutated there, so no HTTP handler may read it.
  The dialog therefore works the way the `?` key already works: the page POSTs
  an ask to a `detail` route, the handler validates it and puts it on a
  bounded queue, and the receive loop drains that queue beside the key queue,
  builds the report from state it owns and publishes a `detail` event. What
  the POST answers is `{"queued":true}` and never the report.
- **Every browser receives every answer, and the ask's own id is what sorts
  them out.** Publishing to the one client that asked is the alternative, and
  it would mean the feed learning which client an ask came from, which is a
  thread boundary `feed.py` deliberately does not cross. At `MAX_CLIENTS = 4`
  the cost of the broadcast is three pages parsing a frame and dropping it.
- **The route is allowed under `--web-readonly`, so the readonly refusal moved
  under the `key` branch.** Read-only is about not changing what the collector
  is doing, and asking what a flow was changes nothing. The origin check stays
  shared, because the reasoning behind it has nothing to do with what the
  request goes on to ask for.
- **Direction in `traffic.py` is relative to the endpoint**, where everywhere
  else in this program it is relative to the network edge. *In* means the
  endpoint was the destination and *out* that it was the source, so a public
  server's panel says it sent what it served while the summary's external
  table calls the same bytes inbound. That is two questions about one set of
  bytes rather than a disagreement, and it is the one semantic trap in the
  feature. It is written on the module, in the report, and in the README.
- **The ring and its bound.** `cli.main` keeps `DETAIL_RING` flows by serial,
  filled inside `web_flow`, so a run with nobody watching keeps none of it.
  The figure matches the page's own `MAX_ROWS`: keeping more would be keeping
  records for rows nothing can click, and keeping fewer would leave rows on
  the page this could no longer describe. A serial the ring has dropped is the
  ordinary case rather than an error, and `detail.report` answers it with the
  endpoint and pair panels, built from the addresses the ask carried. That is
  what the ends are on the ask for.
- **The serial is never reset, not even by the c key.** A page holding rows
  from before a clear must not have them answered by flows from after it.
- **A serial is checked against the ask's ends before it is believed, and
  that is not belt and braces.** Serials do start again at 1 on a restart, and
  `--web-token` exists so that a bookmark survives one, so a reconnecting tab
  arrives with a page full of the previous run's serials on its rows.
  Answering one of those out of the new run's ring shows a reader an entirely
  different flow under a title naming the one they clicked, which is worse
  than saying nothing. `report` calls it held only when
  `flow_endpoints(rec)` matches the ends the ask carried, and `test_detail`
  pins both directions.
- **Text off the wire may not reach a report unchecked.** `_detail` in
  `web.py` refuses anything that is not a whole number in range, a bool wearing
  one (`isinstance(True, int)` is True), a body carrying a field it does not
  know, or an end that is not an address. Each end is parsed and written back
  out through `ipaddress`, which gives the spelling netflume decodes into, so
  that what a browser sends back matches a key in the tally rather than merely
  looking like one.
- **Every value in the report is formatted in Python.** The page names no
  field, no flag and no protocol, for the reason it hardcodes no column and no
  key: a service name is whatever this machine's services database calls that
  port, and a second opinion written in JavaScript would be a second thing to
  keep in step with `values.py`. A section is a title and a list of (label,
  value) pairs, a table is a head and rows of finished strings, and the page
  has exactly two renderers. The dialog's own title and the sentence about a
  flow the ring has dropped come over the wire for the same reason.
- **And painted in Python, in the vocabulary the rest of the program
  already uses.** The colour rides to the browser as escape codes inside those
  finished strings, and `web.html` turns them back into spans with the `ansi`
  converter a flow row and the captured prose already go through, so the
  renderers put their text on the page with `appendChild(ansi(...))` rather
  than into `textContent`. A per-field class chosen in JavaScript would be the
  page deciding what a field is, which is the one thing this whole feature is
  arranged to stop; `test_web_server` greps for both calls, since escape codes
  assigned as text would appear on the page as characters and nothing at
  either end would fail. The rule the colours themselves follow is set out in
  the comment above `_paint` in `detail.py`: a figure is cyan and whatever
  restates or measures it is grey, an identity takes the colour its kind is
  given elsewhere, a direction takes the colour `display.way` chose for the
  arrow, and prose and raw record fields are left alone, because grey arrives
  in the page as the ink the label column is drawn in. `address_colour` moved
  from `cli.py` to `display.py` for that: the summary and the dialog both
  paint an address, and two mappings for one question would be two things to
  drift apart. A browser refusing colour has it taken out at the boundary by
  `cli.detail_for_web`, which is `for_web` for a structure rather than for a
  block of prose, and it stands on `colour.strip_payload`. Nothing threads a
  colour setting down into the report, for the reason nothing threads one into
  the summary.
- **The flags are spelled in the decoder's bit order**, so "ACK, SYN" rather
  than the "SYN, ACK" a handshake is usually described as. That is the order
  the letters run in the FLAGS column of the row the dialog was opened from,
  and a reader comparing the two should not have to reorder one in their head.

Two things about the page are easy to get wrong.

- **The keydown handler bails while the dialog is open.** Otherwise `x`, typed
  into a dialog opened from a row, clears the table underneath it. Escape is
  safe without any of that: it is in `WEB_EXCLUDED` and never forwarded, and
  it is what closes a dialog natively.
- **One `close` listener clears the timer and the outstanding ask.** Escape,
  the backdrop, the Close button and `park` all end up there, so there is one
  place the state is put back. The timer is stopped before a new one is
  started, so two open-and-close cycles cannot leave two timers asking, and
  the ask id is stepped on so a reply already in flight is not rendered into a
  dialog the reader has closed. `park` closes the dialog rather than clearing
  anything itself, since the answer comes back on the stream it has just given
  up.

Nothing in the suite runs the page, so the dialog's own behaviour is a manual
check, as the QR code and the flags are. Click a row, watch the figures move on
their own, press Refresh, close it three ways, and type `x` inside it.

## There is a QR encoder in here

`qr.py` encodes the `--web` URL as a QR code and draws it out of half block
characters, which the `q` key prints. It is the one thing in this repository
that could obviously have been a dependency and deliberately is not, so the
reasoning is worth keeping.

What it would have cost is not what a dependency costs elsewhere. This program
installs three pure Python packages and nothing else, the suite has no
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
- **A symbol that will not fit is not drawn at all**, and three separate
  things go into deciding that. `fits` counts the URL at the rows it really
  wraps into rather than at one, because a name from `--web-host` makes a URL
  wider than the symbol above it easy and the block then scrolls its own top
  away. `cli.main` measures the scroll region rather than the window, because
  a sticky header and a status bar have taken rows off either end. And the
  window measured is the one the block is going to: `qr.window` asks about the
  stream it is handed, where `shutil.get_terminal_size` asks about stdout
  whatever it is handed, so `nettail --web > flows.txt` would otherwise
  measure the file and refuse to draw on the terminal beside it. A wrapped
  code and a code whose top has gone are both unreadable rather than merely
  worse, and the URL underneath costs the reader nothing.

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
makes the browser lay the table out there and then, so the queue turns one such
measurement per event into one per frame. A reconnect hands over a backlog of up
to `CLIENT_BACKLOG` events inside a single task, and letting go of pause does
the same. A task that stops to measure the page thousands of times is, from the
outside, a tab that has frozen. A long session seizing up looked at first like
the memory the history takes, and the code says it is this.

What each measurement costs is the colgroup's business rather than the queue's,
and the two were written a release apart. Under `table-layout: auto` a column is
as wide as the widest cell in it, so laying the table out is a pass over every
row there is, and the cost of showing one flow grows with the history behind it.
`table-layout: fixed` with widths from `COLUMNS` settles every column before a
row is read and takes that growth out. Neither replaces the other: the fixed
layout makes a measurement cheap, the queue makes there be one measurement a
frame, and the queue is also what keeps rows in the order they arrived in, which
nothing about layout would.

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
  right in a file. There is a second boundary of the same shape now, the one
  that spells a country flag out as two letters, so `FilterStream` is the
  plumbing under both and `colour_on` asks the stream rather than testing it
  with `isinstance`: two wrappers can sit around one terminal in either order,
  and the outermost is not the one with the answer. `tee` renders twice when
  the two disagree, which is the one concession: the host list marks a
  superseded name with a star when there is no colour to dim it with, so a
  reader without colour is shown different words and not the same words
  undressed. `colour_on(stream)` is what that site asks, never `C.enabled()`.
  One switch for both is what this replaced, and the case it got wrong was the
  one the image exists for: a detached container has no terminal, so the
  browser view came out white.
- **Every `Resolver(...)` passes an explicit `mode`.** lanname 0.2.0 changed a
  bare `Resolver()` from looking nothing up to querying reverse DNS, with
  nothing raised and nothing warned. Explicit modes are why that release was a
  non-event here. Keep it true.
- **`WatchedTemplates` rests on `put` returning True, and that is the only
  thing it rests on.** The store's return value is where the fact lives, and
  `--verbose` stands a subclass in the decoder's way to hear about it rather
  than parsing the datagram again. `test_templates` pins the store's side of
  the deal separately from the block it feeds, so an upstream change shows up
  as the store failing rather than as a run that quietly says nothing.

  **The better home now exists and this has not moved onto it yet.** netflume
  0.3.0 raises a `TemplateLearned` for a template that is new or changed, with
  the layout it replaced, and 0.4.0 added the kind it replaced beside it. That
  is the event this note used to wish for. What holds the move up is the half
  the event deliberately does not cover: it fires for new and changed only, on
  the same reasoning that a `SamplingChange` fires only on a change, and this
  program prints a line for a resend as well, because how often a template
  comes round is visible nowhere else. So moving means the event for new and
  changed and something thinner kept for the resends, which is a rewrite of
  this block rather than a swap, and it has not been done.

  One thing did move on its own, though, and it is why the pin is 0.4.0 rather
  than 0.1.0. Data and options templates are allocated from one pool of IDs,
  so an exporter may reuse an ID for the other kind without touching a field
  specification. netflume before 0.4.0 compared layouts alone, took the new
  kind and returned False, so this block called a redefinition a resend and
  said "unchanged" while every record for that ID had moved from `flows` to
  `options`. Nothing here could have known: the fact never reached it.

  Every `put` is recorded and not only the ones that return True, because a
  template resent unchanged is worth a line too: how often one comes round is
  visible nowhere else in this program. The two are one list carrying a flag
  rather than two lists, so that a set holding one of each is reported in the
  order the exporter wrote it, and `report_templates` is the one place that
  decides which of the two lengths a template gets. Both are prose, so both go
  to stderr and both are teed to the browser, under a prose kind of their own:
  `template` is not `notice`, because a notice is something gone wrong.
- **Dependency direction runs one way**, from `cli` down towards `colour` and
  `values`. There are no import cycles and there should not be.

## The installer

`scripts/install.sh` covers both deployments, systemd and Docker, and asks
which. Four things in it are deliberate:

- **The web token lives in `/etc/nettail/nettail.env` and is never
  regenerated.** Re-running the installer to pick up a new version must not
  invalidate a URL somebody has bookmarked. It reaches the program through the
  environment and never through the command line, so that it stays out of
  `ps`: systemd puts it there with `EnvironmentFile`, compose with `env_file`,
  and `cli.main` reads `WEB_TOKEN_ENV` when `--web-token` was not given.

  That is now true and was not before 0.6.0. Both generated files used to
  fetch the value back onto the command line as `${NETTAIL_WEB_TOKEN}`, which
  went wrong differently in each. Under systemd it expands into the argv, so
  the token appeared in `ps` for every user on the machine, which is the one
  thing keeping it in a 0640 file was meant to prevent. Under compose it does
  not expand at all: `${...}` there is interpolated on the host, from the
  host's environment or a file named `.env` beside the compose file, and never
  from `env_file`, which is a different mechanism that runs later and inside
  the container. Nothing exported the variable, so it became the empty string
  and the container was started with `--web-token ""`, which nettail refuses.
  **The Docker install could not start at all**, from 0.2.1 until it was
  found. Nothing had ever run it.
- **`--web-bind` is left at its loopback default in both paths.** Putting the
  view on the network is a decision to make by editing the unit or the compose
  file, not one an installer makes quietly on somebody's behalf.
- **`useradd --user-group`, explicitly.** The unit says `Group=nettail`, and
  whether a bare `useradd` creates a matching group depends on `USERGROUPS_ENAB`
  and so on the distribution.
- **The paths it installs into come from the environment**, defaulting to
  exactly what they always were. That is what lets `test_installer` run the
  whole script into a temporary directory and read back what it wrote, rather
  than testing an intermediate. An install that sets none of them is byte for
  byte the install it always was.

It is safe to run twice, and that is worth keeping: an existing user, virtual
environment and token are all reused rather than replaced.

### What tests it, and what does not

`test_installer` runs the real script with fakes for `useradd`, `systemctl`,
`docker`, `chown`, `id` and `python3`, each logging its argv, and then reads
the unit and the compose file back. The check that matters is that the command
line each of them carries is one `build_parser` accepts, because the two bugs
above were both a command line the program refused.

That is why the parser is a function rather than something `main` builds
inline. Asking `nettail --help` instead does not work: argparse reports an
unrecognised argument only after parsing finishes and `--help` exits before
that, so `nettail --nonsense --help` succeeds while an invalid choice does not.

Faking rather than really installing is the sharper check as well as the
cheaper one. Asserting `--user-group` in useradd's argv tests the decision two
bullets up; running the real `useradd` would test whichever `USERGROUPS_ENAB`
the machine happened to have, which is the variable that flag exists to
escape. Starting the service, a real `useradd` and a container run are all out
of scope for the same reason: they test the runner's distribution rather than
this file.

The permission checks, the file mode and the ownership, are the only ones in
the whole suite that gate on `os.name`. They mean nothing under Git Bash on
Windows. Everything else runs wherever bash does, and the suite says so rather
than skipping quietly.

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
