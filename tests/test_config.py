"""Settings from a file: what can be set, who wins, and where it is looked for.

The claim this feature makes is a strong one, that anything settable on the
command line is settable in a file, and the check that matters is the one that
holds it to that literally. For every option the parser has, the suite writes
it into a config file and types it on a command line, and asserts the two runs
come out with the same arguments. A sample value for each is the only thing
written down here, and a new option with no sample fails rather than quietly
turning out to be unsettable.

That is also why almost nothing here compares against an expected value. What
a config file means is defined as what the command line means, so the command
line is what it is compared with, and a test that wrote its own idea of the
answer would be a third opinion for the two to drift from.

The rest is the arrangement around it:

- **The command line wins**, because a file's settings are installed as the
  parser's defaults before parsing and a default is what an argument
  overrides. Merged the other way round it could only have been the other way
  round, and this is pinned in both directions.
- **The search order**, asked of platforms this machine is not. The list a
  Windows install would try is exactly what a Linux runner can never see for
  itself, so `search_paths` takes the platform and the environment as
  arguments and both are checked here.
- **A file is a thing people edit**, so a missing section header, an unknown
  key and an unusable value are each read as far as they can be and complained
  about rather than raised over. A line the format cannot read at all costs
  the file rather than the line, which is pinned here too, because the
  likeliest way to write one is to repeat a key that should have been a list.
- **What is saved can be read back**. `--save-config` writes what a run would
  have used, and the file it writes is put back through the reader and has to
  produce the same run.
"""
import io
import os
import subprocess
import sys
import tempfile

from harness import SCRIPT, check, finish

from nettail import config
from nettail.cli import build_parser

# Spelled rather than escaped, because this file is written and rewritten by
# tooling that has been known to eat a backslash.
NEWLINE = chr(10)

# One value per option, written as a person would write it in a file. The only
# table in this suite, and it is checked against the parser below: an option
# added to the program with no sample here fails rather than going untested.
#
# A switch takes no value, since the flag takes none either; "true" is what
# the file says and the check below knows to type the flag on its own.
SAMPLES = {
    "bind": "10.0.0.5",
    "port": "9995",
    "external_only": "true",
    "named_hosts": "true",
    "show_macs": "true",
    "verbose": "true",
    "json": "true",
    "colour": "never",
    "no_color": "true",
    "header_every": "12",
    "sticky_header": "true",
    "hide_status": "true",
    "no_supplemental_services": "true",
    "web": "true",
    "web_port": "9996",
    "web_bind": "0.0.0.0",
    "web_host": "collector.lan",
    "web_token": "a-token-worth-keeping",
    "web_colour": "off",
    "web_readonly": "true",
    "web_detail_refresh": "2.5",
    "size_scale_max": "2M",
    "size_scale_dynamic": "true",
    "size_scale_window": "500",
    "country": "true",
    "country_db": "/etc/nettail/country.mmdb",
    "country_style": "code",
    "resolve": "dns",
    "hosts": "/etc/hosts.lan",
    "resolve_public": "true",
    "fqdn": "true",
    "resolve_workers": "8",
    "resolve_timeout": "2.5",
}

parser = build_parser()
options = config.settable(parser)

check("the parser has options to set", len(options) > 20, str(len(options)))
check("every settable option has a sample here",
      sorted(action.dest for action in options) == sorted(SAMPLES),
      "missing %s, extra %s"
      % (sorted(set(a.dest for a in options) - set(SAMPLES)),
         sorted(set(SAMPLES) - set(a.dest for a in options))))

# The two config options are not among them, and neither is help or version.
for dest in ("config", "save_config", "help", "version"):
    check("%s is not settable in a file" % dest,
          dest not in [action.dest for action in options])


def from_file(text, argv=()):
    """What a run comes out with, given this file and this command line."""
    ap = build_parser()
    values, complaints = config.parse(ap, text, source="test.conf")
    ap.set_defaults(**values)
    return ap.parse_args(list(argv)), complaints


def typed(argv):
    """What a run comes out with from the command line alone."""
    return build_parser().parse_args(list(argv))


# --- the claim: a file can say anything the command line can ----------------

for action in options:
    name = config.option_name(action)
    text = SAMPLES[action.dest]
    # A switch is typed as the flag on its own; everything else takes a value.
    argv = ([action.option_strings[0]] if action.nargs == 0
            else [action.option_strings[0], text])
    written, complaints = from_file("[nettail]\n%s = %s\n" % (name, text))
    check("%s set in a file is %s typed" % (name, action.option_strings[0]),
          written == typed(argv),
          "file %r\n         line %r" % (written, typed(argv)))
    check("and reading it complains about nothing", complaints == [],
          str(complaints))

# The name may also be written the way Python spells it, since half the world
# will copy the dest out of a message rather than the flag off the help.
written, _ = from_file("[nettail]\nexternal_only = true\n")
check("an underscored key reaches the same option", written.external_only)
written, _ = from_file("[nettail]\nEXTERNAL-ONLY = true\n")
check("and case is not a way to miss it", written.external_only)

# --- an option answers to every name it has ---------------------------------

# `--colour` has `--color` beside it and `--web-colour` has `--web-color`. A
# file that could not say one of those would make the claim this whole feature
# rests on false, and the README says it in as many words.
written, complaints = from_file("[nettail]" + NEWLINE + "color = never" + NEWLINE)
check("an option's alias is a name a file can use",
      written.colour == "never" and complaints == [], str(complaints))
written, complaints = from_file("[nettail]" + NEWLINE + "web-color = off"
                                + NEWLINE)
check("and so is the other one", written.web_colour == "off", str(complaints))

# Two spellings of one option are two keys to configparser and one option
# here, so they walk past its own refusal of a repeated key. Said out loud
# rather than letting the second quietly replace the first.
written, complaints = from_file("[nettail]" + NEWLINE + "web-port = 1"
                                + NEWLINE + "web_port = 2" + NEWLINE)
check("a key written two ways is reported", len(complaints) == 1,
      str(complaints))
check("and names both spellings",
      "web-port" in complaints[0] and "web_port" in complaints[0],
      complaints[0])
check("and the first is the one used", written.web_port == 1,
      str(written.web_port))

# --- two options the command line would refuse together ---------------------

ap = build_parser()
values, _ = config.parse(ap, "[nettail]" + NEWLINE + "size-scale-max = 1M"
                         + NEWLINE + "size-scale-dynamic = true" + NEWLINE)
said = config.conflicts(ap, values)
check("a file setting both sides of an alternative is caught",
      len(said) == 1, str(said))
check("and the message names both",
      "size-scale-max" in said[0] and "size-scale-dynamic" in said[0], said[0])
check("one of them alone is not a conflict",
      config.conflicts(ap, {"size_scale_max": 1}) == [])
check("and neither is a pair from different groups",
      config.conflicts(ap, {"size_scale_max": 1, "web": True}) == [])

# --- who wins ---------------------------------------------------------------

written, _ = from_file("[nettail]\nport = 9995\n", ["--port", "1234"])
check("the command line beats the file", written.port == 1234, str(written.port))
written, _ = from_file("[nettail]\nport = 9995\n")
check("and the file beats the built-in default", written.port == 9995)
written, _ = from_file("[nettail]\n")
check("and the default stands when neither says",
      written.port == typed([]).port)

# A repeated option adds to what the file listed, which is what repeatable
# means everywhere else in this program and is written down as the one place
# "the command line wins" reads differently.
written, _ = from_file("[nettail]\nhosts = /etc/hosts.lan\n",
                       ["--hosts", "/etc/hosts.extra"])
check("a repeatable option adds to the file's list",
      written.hosts == ["/etc/hosts.lan", "/etc/hosts.extra"],
      str(written.hosts))

# --- an alternative with one side from each place ---------------------------
#
# The one case the ordering in `main` does not settle by itself. A mutually
# exclusive group is enforced against what was typed, so a file's side arrives
# as a default and argparse refuses nothing: both come through set, and the
# file has beaten the command line at the one thing that ordering exists to
# prevent. `overruled` is what puts the file's side back.


def merged(text, argv=()):
    """What a run comes out with once `main` has settled the alternatives too.

    `from_file` stops at the parse, which is exactly where both sides of an
    alternative are still set. This carries on the way `main` does.
    """
    ap = build_parser()
    base = config.defaults(ap)
    values, complaints = config.parse(ap, text, source="test.conf")
    ap.set_defaults(**values)
    args = ap.parse_args(list(argv))
    for dest, value in config.overruled(ap, args, values, base).items():
        setattr(args, dest, value)
    return args, complaints


written, _ = merged("[nettail]" + NEWLINE + "size-scale-max = 1M" + NEWLINE,
                    ["--size-scale-dynamic"])
check("a typed alternative puts the file's side back",
      written.size_scale_max is None, str(written.size_scale_max))
check("and the typed one stands", written.size_scale_dynamic)
check("which is the run the command line alone would have given",
      written == typed(["--size-scale-dynamic"]),
      "%r\n         %r" % (written, typed(["--size-scale-dynamic"])))

written, _ = merged("[nettail]" + NEWLINE + "size-scale-dynamic = true"
                    + NEWLINE, ["--size-scale-max", "1M"])
check("and it reads the same way round",
      written == typed(["--size-scale-max", "1M"]),
      "%r\n         %r" % (written, typed(["--size-scale-max", "1M"])))

# The pair that cannot be a group, because --size-scale-window rules out
# --size-scale-max and not --size-scale-dynamic, which it implies.
written, _ = merged("[nettail]" + NEWLINE + "size-scale-max = 1M" + NEWLINE,
                    ["--size-scale-window", "500"])
check("the hand-written pair is settled the same way",
      written == typed(["--size-scale-window", "500"]),
      "%r\n         %r" % (written, typed(["--size-scale-window", "500"])))

# Nothing is decided about a pair that came from one place. Two out of one
# file is what `conflicts` reports, two typed argparse has already refused,
# and both leave this with nothing to say.
ap = build_parser()
base = config.defaults(ap)
values, _ = config.parse(ap, "[nettail]" + NEWLINE + "size-scale-max = 1M"
                         + NEWLINE + "size-scale-dynamic = true" + NEWLINE)
ap.set_defaults(**values)
check("two out of one file are left to the check that reports them",
      config.overruled(ap, ap.parse_args([]), values, base) == {},
      str(config.overruled(ap, ap.parse_args([]), values, base)))

ap = build_parser()
base = config.defaults(ap)
check("and two typed are left to argparse, which has refused them already",
      config.overruled(ap, typed(["--size-scale-dynamic"]), {}, base) == {})

# And a file nothing argues with is left exactly as it was.
written, _ = merged("[nettail]" + NEWLINE + "size-scale-max = 1M" + NEWLINE)
check("a file's side stands when the command line said nothing about it",
      written.size_scale_max == typed(["--size-scale-max", "1M"]).size_scale_max,
      str(written.size_scale_max))

# --- --config with nothing in it --------------------------------------------
#
# "Was a file named" and "has the name anything in it" are two questions, and
# `main` asks the first: a file that was named and will not read is an error
# rather than a complaint. Asked as truthiness this returned to searching, so
# a script written as --config "$CONF" with the variable unset would quietly
# take its settings from whatever the working directory held.

ap = build_parser()
values, path, complaints = config.settings(ap, ["--config", ""])
check("an empty --config reads no file at all",
      values == {} and path is None, str((values, path)))
check("and says what was wrong with it",
      len(complaints) == 1 and "empty filename" in complaints[0],
      str(complaints))
check("a --config with a name in it still reads that file",
      config.settings(ap, ["--config", "no-such-file.conf"])[2] != complaints)


# --- the shapes a value can take -------------------------------------------

for text, expected in (("true", True), ("yes", True), ("on", True), ("1", True),
                       ("false", False), ("no", False), ("off", False),
                       ("0", False)):
    written, _ = from_file("[nettail]\nexternal-only = %s\n" % text)
    check("a switch reads %r as %s" % (text, expected),
          written.external_only is expected)

written, _ = from_file("[nettail]\nhosts =\n    /etc/one\n    /etc/two\n")
check("a list is one value a line", written.hosts == ["/etc/one", "/etc/two"],
      str(written.hosts))

written, _ = from_file("port = 9995\nexternal-only = true\n")
check("a file with no section header is read as though it had one",
      written.port == 9995 and written.external_only)
written, _ = from_file("# a comment first\n; and another\nport = 9995\n")
check("even behind comments", written.port == 9995)

# A percent sign is not an interpolation, because a path may contain one and
# configparser would otherwise refuse the file over it.
written, _ = from_file("[nettail]\nhosts = /etc/hosts%s\n" % "%20odd")
check("a percent in a value is a percent",
      written.hosts == ["/etc/hosts%20odd"], str(written.hosts))

# --- a file is a thing people edit ------------------------------------------

written, complaints = from_file("[nettail]\nnonsense = 1\nport = 9995\n")
check("an unknown key is a complaint", len(complaints) == 1, str(complaints))
check("and names the key", "nonsense" in complaints[0], complaints[0])
check("and the rest of the file is still read", written.port == 9995)

written, complaints = from_file("[nettail]\nport = ninety\nbind = 10.0.0.5\n")
check("an unusable value is a complaint", len(complaints) == 1, str(complaints))
check("and the rest of the file is still read", written.bind == "10.0.0.5")
check("and the option keeps its default", written.port == typed([]).port)

written, complaints = from_file("[nettail]\nresolve = sideways\n")
check("a value outside an option's choices is refused",
      len(complaints) == 1 and "sideways" in complaints[0], str(complaints))

written, complaints = from_file("[nettail]\nexternal-only = perhaps\n")
check("and so is a switch that is neither true nor false",
      len(complaints) == 1 and "true or false" in complaints[0],
      str(complaints))

written, complaints = from_file("[elsewhere]\nport = 9995\n")
check("a section this program does not read is a complaint",
      len(complaints) == 1 and "[elsewhere]" in complaints[0], str(complaints))
check("and nothing in it is used", written.port == typed([]).port)

written, complaints = from_file("[nettail]\nport 9995\n")
check("a line that is not a setting at all does not raise",
      complaints and "could not be read" in complaints[0], str(complaints))
check("and costs the file, since an INI file is read whole or not at all",
      written.port == typed([]).port)

# The likeliest way to lose a file, which is why it is pinned: a repeated
# option written the way it is typed rather than the way INI spells a list.
written, complaints = from_file(
    "[nettail]\nhosts = /etc/one\nhosts = /etc/two\nport = 9995\n")
check("a key written twice is refused rather than quietly halved",
      complaints and "could not be read" in complaints[0], str(complaints))
check("and the file goes with it, loudly", written.port == typed([]).port)

# The custom types are the parser's own, so a value they refuse is refused
# here in the same words rather than reaching the program.
written, complaints = from_file("[nettail]\nweb-token = has/a/slash\n")
check("a token the parser would refuse is refused here",
      len(complaints) == 1 and "web-token" in complaints[0], str(complaints))
check("and the option is left alone", written.web_token is None)

# --- where a file is looked for ---------------------------------------------

posix = config.search_paths(platform="linux", env={}, home="/home/x",
                            cwd="/srv/flows")
check("the working directory is looked at first",
      posix[0] == "/srv/flows/nettail.conf", str(posix))
check("then the user's own directory",
      posix[1] == "/home/x/.nettail/nettail.conf", str(posix))
check("then the home directory itself",
      posix[2:4] == ("/home/x/.nettail.conf", "/home/x/nettail.conf"),
      str(posix))
check("then the usual place for a user's configuration",
      "/home/x/.config/nettail/nettail.conf" in posix, str(posix))
check("and the machine's, last",
      posix[-2:] == ("/etc/nettail/nettail.conf", "/etc/nettail.conf"),
      str(posix))
check("with the machine's after the user's",
      posix.index("/home/x/.config/nettail/nettail.conf")
      < posix.index("/etc/nettail/nettail.conf"))

xdg = config.search_paths(platform="linux", env={"XDG_CONFIG_HOME": "/x/conf"},
                          home="/home/x", cwd="/srv")
check("XDG_CONFIG_HOME is honoured where it is set",
      "/x/conf/nettail/nettail.conf" in xdg, str(xdg))
check("and replaces the default rather than joining it",
      not any("/.config/" in path for path in xdg), str(xdg))

mac = config.search_paths(platform="darwin", env={}, home="/Users/x", cwd="/srv")
check("macOS looks in Application Support",
      "/Users/x/Library/Application Support/nettail/nettail.conf" in mac,
      str(mac))
check("and in /usr/local/etc, which is where a mac keeps such things",
      "/usr/local/etc/nettail/nettail.conf" in mac, str(mac))

windows = config.search_paths(
    platform="win32", home=r"C:\Users\x", cwd=r"D:\work",
    env={"APPDATA": r"C:\Users\x\AppData\Roaming",
         "LOCALAPPDATA": r"C:\Users\x\AppData\Local",
         "PROGRAMDATA": r"C:\ProgramData"})
check("Windows looks in the working directory first too",
      windows[0] == r"D:\work\nettail.conf", str(windows))
check("then in AppData",
      r"C:\Users\x\AppData\Roaming\nettail\nettail.conf" in windows,
      str(windows))
check("and in ProgramData for the machine",
      windows[-1] == r"C:\ProgramData\nettail\nettail.conf", str(windows))
check("and at none of the Unix places, which cannot exist there",
      not any(path.startswith("/") for path in windows), str(windows))
check("a Windows machine with none of those variables still has the first four",
      len(config.search_paths(platform="win32", env={}, home="C:\\Users\\x",
                              cwd="D:\\work")) == 4)

# --- the first file found is the one that is read ---------------------------

held = tempfile.mkdtemp()
first = os.path.join(held, "first.conf")
second = os.path.join(held, "second.conf")
for path, port in ((first, "1111"), (second, "2222")):
    with io.open(path, "w", encoding="utf-8") as handle:
        handle.write("port = %s\n" % port)

check("the first that exists is chosen",
      config.find([os.path.join(held, "nothing.conf"), first, second]) == first)
check("and none of them is not an error",
      config.find([os.path.join(held, "nothing.conf")]) is None)

ap = build_parser()
values, complaints, opened = config.read(ap, first)
check("a real file reads", values.get("port") == 1111, str(values))
check("with nothing to complain about", complaints == [], str(complaints))
check("and says it was read", opened is True)

values, complaints, opened = config.read(ap, os.path.join(held,
                                                          "not-there.conf"))
check("a file that is not there is a complaint and not an exception",
      len(complaints) == 1 and "not-there.conf" in complaints[0],
      str(complaints))
check("and says it was not read, so nothing claims it was", opened is False)
values, complaints, opened = config.read(ap, held)
check("and so is a directory where a file should be",
      len(complaints) == 1 and opened is False, str(complaints))

# What a Windows text editor writes. PowerShell's Out-File is UTF-16 by
# default and Notepad puts a byte order mark on its UTF-8, and the two used to
# fail in opposite ways: a traceback out of main before the socket was bound,
# and a file whose every setting was silently ignored because the mark had
# become part of the first key.
utf16 = os.path.join(held, "utf16.conf")
with io.open(utf16, "w", encoding="utf-16") as handle:
    handle.write("[nettail]" + NEWLINE + "port = 3333" + NEWLINE)
values, complaints, opened = config.read(ap, utf16)
check("a file saved as UTF-16 is a complaint rather than a traceback",
      values == {} and len(complaints) == 1 and opened is False,
      str(complaints))
check("and the complaint says what it looks like",
      "UTF-16" in complaints[0], complaints[0])

marked = os.path.join(held, "bom.conf")
with io.open(marked, "w", encoding="utf-8-sig") as handle:
    handle.write("[nettail]" + NEWLINE + "port = 3333" + NEWLINE)
values, complaints, opened = config.read(ap, marked)
check("a byte order mark does not become part of the first key",
      values.get("port") == 3333 and complaints == [], str(complaints))

headless = os.path.join(held, "bom-headless.conf")
with io.open(headless, "w", encoding="utf-8-sig") as handle:
    handle.write("port = 3333" + NEWLINE)
values, complaints, opened = config.read(ap, headless)
check("even with no section header in front of it",
      values.get("port") == 3333 and complaints == [], str(complaints))

# --- what is saved can be read back -----------------------------------------

ap = build_parser()
baseline = config.defaults(ap)
args = ap.parse_args(["--port", "9995", "--external-only", "--resolve", "dns",
                      "--hosts", "/etc/hosts.lan", "--web", "--web-port",
                      "9996", "--size-scale-max", "2M", "--web-token",
                      "a-token-worth-keeping"])
text = config.render(ap, args, baseline)

again, complaints = from_file(text)
check("saving and reading back gives the same run, but for the token",
      {k: v for k, v in vars(again).items() if k != "web_token"}
      == {k: v for k, v in vars(args).items() if k != "web_token"},
      "saved %r\n         read %r" % (vars(args), vars(again)))
check("and complains about nothing on the way", complaints == [],
      str(complaints))

check("the token is not written out", "a-token-worth-keeping" not in text)
check("and the file says why", "secret" in text)
check("the token can still be read from one",
      from_file("[nettail]\nweb-token = abc\n")[0].web_token == "abc")

def written_out(name, body):
    """Whether a saved file mentions an option, set or commented.

    A list is set with nothing after the equals, since its values are on the
    lines below it, so a check that looked for a space after the equals would
    miss every one of them.
    """
    return "\n%s =" % name in body or "\n#%s =" % name in body


check("every option appears in a saved file, set or not",
      all(written_out(config.option_name(action), text) for action in options),
      "missing %s" % [config.option_name(a) for a in options
                      if not written_out(config.option_name(a), text)])
check("what this run chose is live", "\nport = 9995\n" in text)
check("what it did not is commented, with the default beside it",
      "\n#bind = 0.0.0.0\n" in text, text[:200])
check("a list is written the way it is read",
      "\nhosts =\n    /etc/hosts.lan\n" in text)
check("no line is left with a space hanging off it",
      not any(line.endswith(" ") for line in text.splitlines()))
check("the header says which places are searched",
      "./nettail.conf" in text and "Read from the first" in text)
check("and does not write this machine's working directory into it",
      os.getcwd() not in text)

# A file saved from a run that loaded a file keeps what the file set. Without
# a baseline taken before the settings were installed, those values are the
# parser's defaults by then and every one of them would be commented out.
ap = build_parser()
baseline = config.defaults(ap)
values, _ = config.parse(ap, "[nettail]\nport = 9995\n")
ap.set_defaults(**values)
args = ap.parse_args([])
check("saving after loading keeps what was loaded",
      "\nport = 9995\n" in config.render(ap, args, baseline))
check("and without the baseline it would not, which is why there is one",
      "\nport = 9995\n" not in config.render(ap, args))

# A token is written back only into the file it came from. A bare
# --save-config writes ~/.nettail/nettail.conf, which is also the second place
# the search looks, so the file being written is very often the file just
# read; dropping the token there would mint a fresh one at the next restart
# and quietly break every bookmark.

check("two ways of spelling one path are one file",
      config.same_file("a.conf", os.path.join(os.getcwd(), "a.conf")))
check("and two different files are not",
      not config.same_file("a.conf", "b.conf"))
check("and nothing is not a file", not config.same_file(None, "a.conf"))

ap = build_parser()
baseline = config.defaults(ap)
values, _ = config.parse(ap, "[nettail]" + NEWLINE
                         + "web-token = a-token-worth-keeping" + NEWLINE)
ap.set_defaults(**values)
args = ap.parse_args([])
kept = config.render(ap, args, baseline, keep=("web_token",))
check("a token is written back where it already was",
      NEWLINE + "web-token = a-token-worth-keeping" + NEWLINE in kept)
check("and the file says why it is there", "already in this file" in kept)
check("but not into a file that did not have one",
      "a-token-worth-keeping" not in config.render(ap, args, baseline))

# --- the two options, read before the parse ---------------------------------

check("--config is found before parsing", config.chosen(["--config", "a.conf"])
      == ("a.conf", None))
check("and with an equals as argparse allows",
      config.chosen(["--config=a.conf"]) == ("a.conf", None))
check("--save-config with a path is found",
      config.chosen(["--save-config", "out.conf"]) == (None, "out.conf"))
check("--save-config with none falls back to the home directory",
      config.chosen(["--save-config"]) == (None, config.default_save_path()))
check("and it is under a directory of this program's own",
      config.default_save_path().endswith(
          os.path.join(".nettail", "nettail.conf")),
      config.default_save_path())
check("neither is neither", config.chosen(["--port", "9995"]) == (None, None))
check("and options this pre-parse knows nothing about do not upset it",
      config.chosen(["--web", "--hosts", "x", "--config", "a.conf"])
      == ("a.conf", None))
check("nor does something it cannot make sense of, which the real parse "
      "reports properly", config.chosen(["--config"]) == (None, None))

# The two are opposite directions through the same door and cannot both be
# asked for.
ap = build_parser()
try:
    ap.parse_args(["--config", "a.conf", "--save-config", "b.conf"])
    check("--config and --save-config are mutually exclusive", False)
except SystemExit:
    check("--config and --save-config are mutually exclusive", True)

# --- and the whole of it, through the real program --------------------------


def run(argv, cwd, expect=0):
    """The collector, started for real, in a directory of the test's choosing."""
    proc = subprocess.run([sys.executable, *SCRIPT, *argv], cwd=cwd,
                          capture_output=True, text=True, timeout=60)
    check("nettail %s exits cleanly" % " ".join(argv),
          proc.returncode == expect, "%d: %s" % (proc.returncode, proc.stderr))
    return proc.stdout, proc.stderr


work = tempfile.mkdtemp()
with io.open(os.path.join(work, config.CONFIG_NAME), "w",
             encoding="utf-8") as handle:
    handle.write("# no section header, as a person would write it\n"
                 "port = 9995\nexternal-only = true\nresolve = off\n")

# --save-config writes what the run would have used, which is how a test with
# no terminal and no socket can see what a run would have done.
_out, err = run(["--save-config", "saved.conf"], work)
check("the run says which file it read",
      os.path.join(work, config.CONFIG_NAME) in err, err)
check("and where it wrote one", "saved.conf" in err, err)
check("and did not go on to collect anything, which is why it returned",
      "Listening" not in err, err)

with io.open(os.path.join(work, "saved.conf"), encoding="utf-8") as handle:
    saved = handle.read()
check("what the file set is live in what was saved",
      "\nport = 9995\n" in saved and "\nexternal-only = true\n" in saved, saved)
check("and what it did not set is not", "\n#bind =" in saved, saved)

_out, err = run(["--port", "1234", "--save-config", "typed.conf"], work)
with io.open(os.path.join(work, "typed.conf"), encoding="utf-8") as handle:
    saved = handle.read()
check("a typed argument beats the file, through the real program",
      "\nport = 1234\n" in saved, saved)
check("and what the file said otherwise is still there",
      "\nexternal-only = true\n" in saved, saved)

# --config names a file instead of looking for one, and the one in the working
# directory is then not read.
other = os.path.join(work, "other.conf")
with io.open(other, "w", encoding="utf-8") as handle:
    handle.write("[nettail]\nport = 4321\n")
_out, err = run(["--config", other, "--save-config", "named.conf"], work,
                expect=2)
check("--config and --save-config together are refused by the program too",
      "not allowed with" in err or "mutually exclusive" in err, err)

# What --config does has to be watched from a real run, because the two things
# that end a run early both end it too early to see: argparse answers
# --version and exits inside the parse, before there is any question of
# printing which file was read, and --save-config cannot be asked for beside
# --config. So the collector is started for real and stopped once it has said
# what it is doing. The banner names the port, which is what says a setting
# reached the socket rather than only the parser.


def started(argv, cwd):
    """Start the collector, read what it says at startup, and stop it."""
    proc = subprocess.Popen([sys.executable, "-u", *SCRIPT, *argv], cwd=cwd,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True)
    lines = []
    while True:
        line = proc.stderr.readline()
        if not line:
            break                  # it died; the checks will say so
        lines.append(line)
        if "Listening for NetFlow" in line:
            break
    proc.terminate()
    _out, rest = proc.communicate(timeout=15)
    return "".join(lines) + rest


err = started(["--config", other, "--bind", "127.0.0.1"], work)
check("a named file is the one reported", other in err, err)
check("and the one in the working directory is not",
      os.path.join(work, config.CONFIG_NAME) not in err, err)
check("and what it says reaches the socket", "127.0.0.1:4321" in err, err)

# A file with a bad line still starts the collector, and says what it could
# not use.
bad = os.path.join(work, "bad.conf")
with io.open(bad, "w", encoding="utf-8") as handle:
    handle.write("[nettail]\nheader-every = ninety\nnonsense = 1\n"
                 "bind = 127.0.0.1\nport = 19995\n")
err = started(["--config", bad], work)
check("a bad value is reported rather than fatal", "ninety" in err, err)
check("and so is an unknown key", "nonsense" in err, err)
check("and the rest of the file is used, which is what starting on its port "
      "says", "127.0.0.1:19995" in err, err)

# A file this run named and could not read is an error, where one found by
# searching is a complaint. Somebody typed this one, and a unit file with a
# typo in the path would otherwise run on stock defaults for ever while
# printing a line that says its settings came from somewhere.
def refused(argv, cwd):
    """Run a command line that should stop the program, and say how it went.

    A timeout is the interesting failure: it means the program did not stop
    over what it was given and went on to listen instead, which is exactly
    what these two checks exist to catch. --version cannot be used to hurry it
    along, because argparse answers that inside the parse and exits before
    there is anything to refuse.
    """
    try:
        done = subprocess.run([sys.executable, *SCRIPT, *argv], cwd=cwd,
                              capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired as expired:
        return None, expired.stderr or ""
    return done.returncode, done.stderr


missing = os.path.join(work, "not-here.conf")
code, err = refused(["--config", missing], work)
check("a named file that is not there stops the run", code == 2, str(code))
check("and says which file", "not-here.conf" in err, err)
check("and nothing claims to have read it", "settings from" not in err, err)

# And a --config with nothing after it, which is what a script written as
# --config "$CONF" produces when the variable is unset. The name is empty
# rather than absent, so this is a file that was named and cannot be read and
# not an invitation to go looking: the working directory here holds a
# nettail.conf, and quietly taking its settings would be the surprise.
code, err = refused(["--config", ""], work)
check("an empty --config stops the run", code == 2, str(code))
check("and says that is what was wrong", "empty filename" in err, err)
check("and nothing was read from the working directory",
      "settings from" not in err, err)

# And a file that sets two options the command line refuses together. argparse
# enforces a mutually exclusive group against what was typed, so two of them
# arriving as defaults would walk straight through it.
both = os.path.join(work, "both.conf")
with io.open(both, "w", encoding="utf-8") as handle:
    handle.write("[nettail]" + NEWLINE + "size-scale-max = 1M" + NEWLINE
                 + "size-scale-dynamic = true" + NEWLINE)
code, err = refused(["--config", both], work)
check("a file setting both sides of an alternative stops the run",
      code == 2 and "cannot be set together" in err, "%s: %s" % (code, err))
check("and the message names them",
      "size-scale-max" in err and "size-scale-dynamic" in err, err)

# An alternative with one side from each place, through the real program. The
# file names a fixed top and the command line asks for a dynamic scale, which
# argparse cannot refuse because only one of them was typed. What is saved is
# what the run would have used, so the file's side being commented out there
# is the whole of the fix visible from outside.
alternative = tempfile.mkdtemp()
with io.open(os.path.join(alternative, config.CONFIG_NAME), "w",
             encoding="utf-8") as handle:
    handle.write("size-scale-max = 1M" + NEWLINE + "port = 9995" + NEWLINE)

_out, err = run(["--size-scale-dynamic", "--save-config", "chose.conf"],
                alternative)
with io.open(os.path.join(alternative, "chose.conf"),
             encoding="utf-8") as handle:
    saved = handle.read()
check("a typed alternative beats the file through the real program",
      "size-scale-dynamic = true" in saved, saved)
check("and the file's side is put back, so it saves as unset",
      "#size-scale-max =" in saved, saved)
check("and the rest of the file is untouched by any of it",
      "port = 9995" in saved, saved)

# The pair that is not a group gets the same answer, and this one used to stop
# the run: the check was written against the merged arguments, so a file's
# --size-scale-max met a typed --size-scale-window and was refused, which is a
# file beating a command line by making it fail.
_out, err = run(["--size-scale-window", "500", "--save-config", "window.conf"],
                alternative)
with io.open(os.path.join(alternative, "window.conf"),
             encoding="utf-8") as handle:
    saved = handle.read()
check("a typed window is no longer refused by a file's fixed top",
      "size-scale-window = 500" in saved, saved)
check("and that fixed top is what gave way", "#size-scale-max =" in saved,
      saved)

# Both from one file is still refused, since nothing typed has said which of
# them was meant.
pair = os.path.join(work, "window-and-max.conf")
with io.open(pair, "w", encoding="utf-8") as handle:
    handle.write("[nettail]" + NEWLINE + "size-scale-max = 1M" + NEWLINE
                 + "size-scale-window = 500" + NEWLINE)
code, err = refused(["--config", pair], work)
check("two of them out of one file still stop the run", code == 2, str(code))
check("and say which two", "size-scale-window" in err and "size-scale-max"
      in err, err)


# Saving over the file the settings came from keeps the token that was in it.
# A bare --save-config writes the second place the search looks, so this is
# the ordinary case rather than a corner of one.
tokened = tempfile.mkdtemp()
with io.open(os.path.join(tokened, config.CONFIG_NAME), "w",
             encoding="utf-8") as handle:
    handle.write("web-token = a-token-worth-keeping" + NEWLINE
                 + "port = 9995" + NEWLINE)

_out, err = run(["--save-config", config.CONFIG_NAME], tokened)
with io.open(os.path.join(tokened, config.CONFIG_NAME),
             encoding="utf-8") as handle:
    saved = handle.read()
check("saving over the file a token came from keeps it",
      "web-token = a-token-worth-keeping" in saved, saved[-600:])
check("and the rest of the file with it", "port = 9995" in saved)

_out, err = run(["--save-config", "elsewhere.conf"], tokened)
with io.open(os.path.join(tokened, "elsewhere.conf"),
             encoding="utf-8") as handle:
    elsewhere = handle.read()
check("saving somewhere else does not carry the token there",
      "a-token-worth-keeping" not in elsewhere)
check("though everything that is not a secret goes",
      "\nport = 9995\n" in elsewhere, elsewhere[-400:])

# Nothing found is nothing said, on a machine that happens to have no config
# file anywhere the search reaches.
empty = tempfile.mkdtemp()
if config.find(config.search_paths(cwd=empty)) is None:
    err = started(["--bind", "127.0.0.1"], empty)
    check("a run with no config file says nothing about one",
          "settings from" not in err, err)

finish("config")
