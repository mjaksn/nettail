"""A file that says what the command line would have said.

Everything this program can be told on the command line it can be told in a
file instead, and the two are the same set because this module never writes
that set down. It reads the parser: `build_parser` is the one place an option
exists, and every option in it is settable here by the name it already has,
less its dashes. An option added there is settable here the day it is added,
and a test holds the two to each other so that an option which somehow cannot
be set fails rather than being quietly unavailable.

The command line wins. A file's settings are installed as the parser's
defaults before the arguments are parsed, so anything typed overrides the
file, and anything neither says falls back to the default the program shipped
with. That ordering is the whole of the design and is why the file is read
before parsing rather than merged after it: argparse cannot tell a value that
was typed from a default that happens to equal it, and merging afterwards
would let a file quietly override the command line.

The format is what configparser reads, which is to say an INI file, because it
is in the standard library and because a person editing a settings file has
seen one before. There is one section and it is `[nettail]`, and the header is
optional: a file with only `port = 2055` in it is read as though it had one,
since there is nowhere else for a setting to be.

    # nettail.conf
    port = 2055
    external-only = true
    web = true
    hosts =
        /etc/hosts.lan
        /etc/hosts.iot

A key may be written with dashes as the flag is or with underscores as Python
would; both reach the same option. A switch takes true or false and the words
configparser accepts for them. An option that may be repeated on the command
line takes one value per line, indented under the key, which is how INI spells
a list.

A repeatable option is the one place "the command line wins" reads differently,
and deliberately: typing `--hosts` adds to what the file listed rather than
replacing it, because that is what repeatable means everywhere else in this
program. A run that wants none of them wants a different file, or `--config`
pointed at one that lists none.

Where the file is looked for is `search_paths`, and the order is deliberate:
the working directory first, so that a directory can carry its own settings,
then the user, then the machine. The first file found is the only file read;
they are not merged. **Which file was read is always printed at startup**, and
that is not decoration: a settings file in the working directory is a file
somebody else may have put there, and a run that silently took its options
from one would be worse than no feature at all.
"""

import argparse
import configparser
import io
import os
import sys

CONFIG_NAME = "nettail.conf"

# The section a setting lives in, and the only one there is.
SECTION = "nettail"

# Options that exist to be typed and cannot be set in a file.
#
# The two config options themselves, because a file naming another file is a
# chain nobody asked for and a file that saves a file is nonsense. `--help`
# and `--version` because they are questions rather than settings: a file that
# turned either on would make the program answer it and exit, for ever, and
# the reader would have no way to see why.
UNSETTABLE = ("help", "version", "config", "save_config")

# The one setting that is read from a file and never written to one.
#
# `--save-config` writes the key commented, with a line saying where the token
# should live instead. Writing the value would take a secret that this program
# goes to some trouble to keep out of `ps` and put it in a file whose whole
# purpose is to be edited, copied between machines and pasted into an issue.
# Reading it is another matter and is allowed: somebody who put it there did
# so on purpose.
NEVER_WRITTEN = ("web_token",)


def default_save_path():
    """Where `--save-config` writes when it is given no path.

    Under the home directory rather than beside the program or in the working
    directory: it is this user's settings, and the directory is the second
    place the search below looks, so a file written here is found by the next
    run without anything else being said.
    """
    return os.path.join(os.path.expanduser("~"), ".nettail", CONFIG_NAME)


def _join(parts, windows):
    """A path built with the separator the named platform uses.

    Spelled rather than left to `os.path.join`, which uses the separator of
    the machine asking rather than of the machine asked about. The paths a
    Windows install would search are exactly the ones a Linux runner can never
    see for itself, so they have to be assembled the same way from anywhere.
    """
    sep = "\\" if windows else "/"
    return sep.join(part.rstrip("\\/") if index == 0 else part
                    for index, part in enumerate(parts) if part)


def search_paths(platform=None, env=None, home=None, cwd=None):
    """Every place a config file is looked for, in the order they are tried.

    Nearest first, which is the order that makes the answer predictable: the
    working directory, then this user, then this machine. The first file that
    exists is the one that is read, and nothing is merged. Merging is the
    other reasonable design and this is not it, because a setting that comes
    from two files at once is a setting nobody can find by looking at either.

    The user's places differ by platform and are the ones that platform's own
    programs use: `%APPDATA%` on Windows, `~/Library/Application Support` on
    macOS, `$XDG_CONFIG_HOME` or `~/.config` elsewhere. The machine's are
    `%PROGRAMDATA%` and `/etc`, and `/etc/nettail` is where the installer
    already keeps this program's other machine-wide file.

    The platform, the environment, the home directory and the working
    directory are all arguments with the real ones as their default, which is
    what lets the suite ask what a Windows install would look for without
    being one.
    """
    platform = sys.platform if platform is None else platform
    env = os.environ if env is None else env
    home = os.path.expanduser("~") if home is None else home
    cwd = os.getcwd() if cwd is None else cwd
    windows = platform.startswith("win") or platform == "cygwin"

    def path(*parts):
        return _join(parts, windows)

    places = [
        # A directory's own settings, which is what makes it possible to keep
        # one collector's options beside whatever else that directory is for.
        path(cwd, CONFIG_NAME),
        path(home, ".nettail", CONFIG_NAME),
        path(home, "." + CONFIG_NAME),
        path(home, CONFIG_NAME),
    ]
    if windows:
        for variable in ("APPDATA", "LOCALAPPDATA"):
            if env.get(variable):
                places.append(path(env[variable], "nettail", CONFIG_NAME))
        if env.get("PROGRAMDATA"):
            places.append(path(env["PROGRAMDATA"], "nettail", CONFIG_NAME))
        return tuple(dict.fromkeys(places))

    if platform == "darwin":
        places.append(path(home, "Library", "Application Support", "nettail",
                           CONFIG_NAME))
    places.append(path(env.get("XDG_CONFIG_HOME") or path(home, ".config"),
                       "nettail", CONFIG_NAME))
    if platform == "darwin":
        places.append(path("/usr/local/etc", "nettail", CONFIG_NAME))
    places.append(path("/etc", "nettail", CONFIG_NAME))
    places.append(path("/etc", CONFIG_NAME))
    return tuple(dict.fromkeys(places))


def find(places=None):
    """The first config file that exists, or None if there is no such file."""
    for candidate in (search_paths() if places is None else places):
        if os.path.isfile(candidate):
            return candidate
    return None


def settable(parser):
    """Every option that can be set in a file, in the order the help shows.

    Read off the parser rather than listed here, which is the point: there is
    no second place an option has to be added, and no way for the two lists to
    disagree, because there is one list.

    `_actions` is argparse's own and has no public spelling. There is no API
    for "what options does this parser have", and the alternative to reaching
    for the attribute is writing the options out again, which is the thing
    this module exists not to do. It has been there since argparse was
    accepted and every library that introspects a parser uses it.
    """
    return [action for action in parser._actions
            if action.option_strings and action.dest not in UNSETTABLE
            and action.dest != argparse.SUPPRESS]


def option_name(action):
    """What an option is called in a file: its own flag, without the dashes.

    The first long option, so `--colour` rather than its `--color` alias and
    `--web-port` rather than anything invented here. Reading accepts
    underscores too, since half the world will type the dest instead.
    """
    for flag in action.option_strings:
        if flag.startswith("--"):
            return flag[2:]
    return action.option_strings[0].lstrip("-")


def _key(name):
    """A key as it is compared: lowercase, and dashes and underscores alike."""
    return name.strip().lower().replace("_", "-")


def _boolean(text):
    """A switch's value, using the words configparser already accepts."""
    try:
        return configparser.ConfigParser.BOOLEAN_STATES[text.strip().lower()]
    except KeyError as exc:
        raise ValueError("expected true or false, not %r" % text) from exc


def _items(text):
    """A repeated option's values: one per line, or one on its own."""
    return [line.strip() for line in text.splitlines() if line.strip()]


def _one(action, text):
    """One value, converted and checked exactly as the command line does it.

    The action's own `type` is what a typed argument goes through, so a token
    with a slash in it or a size with a bad suffix is refused here in the same
    words it is refused there, and there is no second opinion about what a
    value means.
    """
    text = text.strip()
    value = action.type(text) if callable(action.type) else text
    if action.choices is not None and value not in action.choices:
        raise ValueError("%r is not one of %s"
                         % (text, ", ".join(str(choice)
                                            for choice in action.choices)))
    return value


def convert(action, text):
    """A file's text for one option, as the value that option holds.

    Three shapes, and the action says which without anything here having to
    know one option from another: a switch takes no argument and so is a
    boolean, an option that may be repeated becomes a list, and everything
    else is a single value.
    """
    if action.nargs == 0:
        # store_true, store_false and their like. `const` is what the flag
        # sets and `default` is what it leaves alone, so false means the
        # default and never a hardcoded False: `--no-color` and a flag that
        # turns something off both come out right.
        return action.const if _boolean(text) else action.default
    if isinstance(action, argparse._AppendAction):
        return [_one(action, item) for item in _items(text)]
    return _one(action, text)


def parse(parser, text, source="the config file"):
    """Read config text against a parser: the settings, and the complaints.

    Nothing here raises. A file is something a person edits, often on a
    machine they are not looking at, and one bad line should cost that line
    rather than the collector's ability to start. Every complaint names the
    file and the key, and the caller prints them; every key that did read is
    in the settings regardless.

    A missing section header is not a complaint. There is one section and a
    file with `port = 2055` in it means the obvious thing, so a header is
    supplied when there is none rather than demanded.
    """
    complaints = []
    if not _has_section(text):
        text = "[%s]\n%s" % (SECTION, text)

    reader = configparser.ConfigParser(interpolation=None)
    try:
        reader.read_string(text, source)
    except configparser.Error as exc:
        return {}, ["%s could not be read: %s" % (source, exc)]

    known = {}
    for action in settable(parser):
        known[_key(option_name(action))] = action
        known[_key(action.dest)] = action

    settings = {}
    for section in reader.sections():
        if _key(section) != SECTION:
            complaints.append("%s: [%s] is not a section this program reads; "
                              "everything belongs under [%s]"
                              % (source, section, SECTION))
            continue
        for name, raw in reader.items(section):
            action = known.get(_key(name))
            if action is None:
                complaints.append("%s: no option called %r" % (source, name))
                continue
            try:
                settings[action.dest] = convert(action, raw)
            except (ValueError, TypeError, argparse.ArgumentTypeError) as exc:
                complaints.append("%s: %s = %r is not usable: %s"
                                  % (source, name, raw.strip(), exc))
    return settings, complaints


def _has_section(text):
    """Whether the text already opens a section of its own."""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        return line.startswith("[")
    return True


def read(parser, path):
    """Read one config file: the settings, and the complaints.

    A file that cannot be opened is a complaint like any other and not an
    exception, because the caller has already decided this file exists or has
    been told to read it, and neither case is worth stopping a collector over.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        return {}, ["could not read %s: %s" % (path, exc.strerror or exc)]
    return parse(parser, text, source=path)


def defaults(parser):
    """What every settable option holds before anything has been read.

    Taken before a file is installed over the top of them, because that is
    what `--save-config` compares against: once a file's settings have become
    the parser's defaults, a value that came from the file looks exactly like
    a value nobody ever chose.
    """
    return {action.dest: action.default for action in settable(parser)}


def spell(value):
    """One value, as a file would write it.

    A list comes back beginning with a newline, since that is how INI carries
    one: the key, then a line each, indented. `assign` is what puts a key in
    front of any of these, and is where that shape stops needing to be
    remembered.
    """
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (list, tuple)):
        # One per line, indented, which is how INI spells a list and how this
        # reads one back.
        return "\n" + "".join("    %s\n" % item for item in value).rstrip("\n")
    return str(value)


def assign(name, value, commented=False):
    """A whole line: the key, the equals, and the value written out.

    The space after the equals goes when the value is a list, which starts on
    the next line: without that, every list in the file would carry a trailing
    space nobody can see and every editor would strip.
    """
    rendered = spell(value) if value not in (None, [], "") else ""
    joiner = " =" if rendered.startswith(chr(10)) or not rendered else " = "
    return "%s%s%s%s\n" % ("#" if commented else "", name, joiner, rendered)


def _wrapped(text, width=74):
    """A help string as comment lines, wrapped without taking a dependency."""
    words, lines, line = text.split(), [], ""
    for word in words:
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = "%s %s" % (line, word) if line else word
    if line:
        lines.append(line)
    return lines


def render(parser, args, baseline=None):
    """The whole config file for a run, as text.

    Every option appears, in the order the help lists them, under its own help
    text. An option this run is not at its default for is written live; the
    rest are written commented out with the default beside them. So the file
    is a complete list of what can be set and a short list of what is set, and
    it stays readable rather than becoming a hundred lines of settings nobody
    chose. It also means a default this program changes later reaches a reader
    who never touched that option, which writing every value would have
    frozen.

    `baseline` is what the options held before any file was read. Without it,
    a run that loaded a config and saved it again would write everything the
    file had set back out as a comment, since by then those values are the
    parser's defaults, and the saved file would be empty of the settings it
    was made from.
    """
    baseline = defaults(parser) if baseline is None else baseline
    out = io.StringIO()
    out.write("# nettail settings.\n#\n")
    for line in _wrapped(
            "Anything this program takes on the command line it takes here, "
            "under the same name without its dashes. What is typed on the "
            "command line wins over what is written here. Lines that are "
            "commented out are showing the default rather than setting it."):
        out.write("# %s\n" % line)
    out.write("#\n")
    # The working directory is named as such rather than as whatever it was
    # when this was saved: that path is a fact about one moment on one machine,
    # and read later it would look like a claim about where this file has to
    # live.
    places = ("./" + CONFIG_NAME,) + tuple(search_paths()[1:])
    for line in _wrapped(
            "Saved by --save-config. Read from the first of these that "
            "exists: " + ", ".join(places)):
        out.write("# %s\n" % line)
    out.write("\n[%s]\n" % SECTION)

    for action in settable(parser):
        name = option_name(action)
        value = getattr(args, action.dest, action.default)
        out.write("\n")
        for line in _wrapped(action.help or ""):
            out.write("# %s\n" % line)
        if action.choices is not None:
            out.write("# one of: %s\n"
                      % ", ".join(str(choice) for choice in action.choices))
        if action.dest in NEVER_WRITTEN:
            for line in _wrapped(
                    "Never written out, whatever this run was given. It is a "
                    "secret and this file is not, and the environment is where "
                    "it belongs. Reading one from here still works, for "
                    "somebody who put it here on purpose."):
                out.write("# %s\n" % line)
            out.write("#%s =\n" % name)
            continue
        if value == baseline.get(action.dest) or value is None:
            out.write(assign(name, baseline.get(action.dest), commented=True))
            continue
        out.write(assign(name, value))
    return out.getvalue()


def write(parser, args, path, baseline=None):
    """Write a config file for this run, making its directory if it is missing.

    Hands back the path it wrote, which is what the caller prints. The
    directory is created because the default path is `~/.nettail`, which is
    this program's own and will not exist on a machine that has never saved
    one.
    """
    path = os.path.abspath(os.path.expanduser(path))
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(render(parser, args, baseline))
    return path


def chosen(argv=None):
    """`--config` and `--save-config` as (load, save), before the real parse.

    The file has to be read before the arguments are parsed, and which file it
    is may itself be an argument, so this reads those two options and nothing
    else. A parser of its own rather than a scan of argv, so that the ways
    argparse accepts an option, `--config=x` and an unambiguous abbreviation
    among them, are the ways this accepts it too.

    Anything it cannot make sense of is left to the real parser, which has the
    program's name and every other option and will say so properly. Two error
    messages for one mistake, the first of them from a parser the reader has
    never heard of, is worse than one.
    """
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config")
    pre.add_argument("--save-config", nargs="?", const=default_save_path())
    stderr = sys.stderr
    try:
        sys.stderr = io.StringIO()
        known, _rest = pre.parse_known_args(argv)
    except SystemExit:
        return None, None
    finally:
        sys.stderr = stderr
    return known.config, known.save_config


def settings(parser, argv=None):
    """Everything a file has to say, in one call.

    Hands back the settings to install as the parser's defaults, the path they
    came from or None, and whatever there was to complain about. `main` does
    the printing, because when any of this can be said depends on the colour
    and country wrapping, and neither is settled at the point this is read.

    `--save-config` is not answered here even though `chosen` reads it: where
    to save is a decision for after the real parse, which is the parse that
    knows whether the rest of the command line was any good.
    """
    named, _save = chosen(argv)
    path = named if named else find()
    if path is None:
        return {}, None, []
    values, complaints = read(parser, path)
    return values, path, complaints
