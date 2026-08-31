"""What `scripts/install.sh` writes, and whether nettail will take it.

The installer assembles a command line for a program it does not import, out
of choices it writes down a second time, into files nothing then reads back.
Every one of those is a place for the two to drift, and none of them fails
loudly. Two had drifted before this suite existed:

- `--resolve passive`, a mode nettail has never had, offered as the default.
  A plain install wrote a unit the collector refused to start, from 0.2.1 to
  0.5.0, and it was found by reading rather than by anything running.
- `--web-token ${NETTAIL_WEB_TOKEN}` in the compose file. A `${...}` there is
  interpolated by compose, on the host, from the host's environment or a file
  named `.env` beside it. It never reads `env_file`, which is a different
  mechanism that runs later and inside the container. Nothing exported the
  variable, so it resolved to the empty string and the container was started
  with `--web-token ""`, which nettail refuses.

Both are the same shape, and the shape is what this pins: what comes out the
far end has to be something `build_parser` accepts. That is why the parser was
lifted out of `main`. Asking `nettail --help` instead is not enough, because
argparse reports an unrecognised argument only after it has finished parsing
and `--help` exits before that, so `nettail --nonsense --help` succeeds while
the invalid choice that actually shipped does not.

## How the installer is run at all

It wants root, `useradd`, `systemctl`, `docker`, a virtual environment and a
network. It gets fakes for all of them, on a PATH of its own, each appending
its argv to a log and doing the least the script checks for: the fake `id -u`
answers 0, and the fake `python3 -m venv` leaves a `venv/bin/python` behind so
that a second run takes the reuse branch. The paths it installs into come from
the environment, defaulting to what they always were, so the whole script runs
into a temporary directory and the test reads what it actually wrote.

Faking rather than really installing is not only cheaper, it is a sharper
check of the thing worth checking. Asserting `--user-group` in useradd's argv
tests the decision the comment in the installer defends. Running the real
`useradd` would test whichever `USERGROUPS_ENAB` this machine happens to have,
which is the exact variable that flag exists to escape.

Deliberately not covered: starting the service, a real `useradd`, and an end
to end container run. Each tests the runner's distribution more than it tests
this file.

## The one platform gate in the suite

File modes and ownership mean nothing under Git Bash on Windows, so those two
checks ask `os.name` first. Everything else runs wherever bash does. No other
suite here gates on platform, so this says why rather than leaving it to be
guessed at.
"""
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

from harness import ROOT, check, finish
from lanname import Resolver

from nettail.cli import build_parser
from nettail.web import WEB_TOKEN_ENV

INSTALLER = os.path.join(ROOT, "scripts", "install.sh")
BASH = shutil.which("bash")

check("the installer is where the tests think it is", os.path.isfile(INSTALLER))

if BASH is None:
    # Not a failure. The file is bash and a machine without bash cannot say
    # anything about it, which is different from it being wrong.
    check("bash is available to run the installer with", True,
          "no bash on PATH; the installer checks are skipped")
    finish("installer")
    sys.exit(0)


# --- the fakes ---------------------------------------------------------------
#
# Each logs its argv to $FAKELOG and does the minimum the installer tests for.
# `id` is the interesting one: the script refuses to run unless `id -u` says 0,
# and answers about the service user differently once useradd has been called,
# which is what the reuse branch turns on.

FAKES = {
    "id": """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$FAKELOG/id.argv"
if [ "${1:-}" = "-u" ]; then echo 0; exit 0; fi
[ -f "$FAKELOG/user.created" ] && exit 0 || exit 1
""",
    "useradd": """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$FAKELOG/useradd.argv"
: > "$FAKELOG/user.created"
""",
    "python3": """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$FAKELOG/python3.argv"
case "${1:-}" in
  -m) if [ "${2:-}" = "venv" ]; then mkdir -p "$3/bin"; cp "$0" "$3/bin/python"; fi ;;
  -c) echo "installertoken-aaaaaaaaaaaaaaaa" ;;
esac
exit 0
""",
    "systemctl": None,
    "docker": None,
    "chown": None,
}
PLAIN_FAKE = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$FAKELOG/%s.argv"
"""


def make_fakes(where):
    binned = os.path.join(where, "bin")
    os.makedirs(binned)
    for name, body in FAKES.items():
        if body is None:
            body = PLAIN_FAKE.replace("%s.argv", name + ".argv")
        path = os.path.join(where, "bin", name)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(body)
        os.chmod(path, 0o755)
    return binned


def install(where, *options, **kw):
    """Run the installer into `where`, and hand back what it wrote.

    Returns (returncode, output, paths). `paths` names the four places the
    installer is pointed at, so a check can read the files back.
    """
    binned = kw.get("binned") or make_fakes(where)
    log = os.path.join(where, "log")
    paths = {
        "log": log,
        "config": os.path.join(where, "etc"),
        "install": os.path.join(where, "opt"),
        "units": os.path.join(where, "units"),
    }
    for key in ("log", "units"):
        os.makedirs(paths[key], exist_ok=True)
    # The systemd directory exists on every machine that has systemd, so the
    # installer does not create it and neither does this pretend otherwise.
    env = dict(os.environ)
    env.update({
        "PATH": binned + os.pathsep + env.get("PATH", ""),
        "FAKELOG": log,
        "NETTAIL_INSTALL_DIR": paths["install"],
        "NETTAIL_CONFIG_DIR": paths["config"],
        "NETTAIL_SYSTEMD_DIR": paths["units"],
    })
    # Whatever this run inherited must not decide what the installer writes.
    env.pop(WEB_TOKEN_ENV, None)
    done = subprocess.run(
        [BASH, INSTALLER, "--non-interactive", "--no-start"] + list(options),
        capture_output=True, text=True, env=env, cwd=ROOT)
    return done.returncode, done.stdout + done.stderr, paths


def argv_from_unit(text):
    """The ExecStart argv, less the interpreter. Empty if there is none."""
    found = re.search(r"^ExecStart=(.*)$", text, re.M)
    if not found:
        return []
    return shlex.split(found.group(1))[1:]


def argv_from_compose(text):
    """The argv under `command:`. Empty if there is none.

    Written to fail closed. An extractor that quietly returns nothing would
    hand `parse_args` an empty list, which every parser accepts, and the check
    would pass while testing nothing at all. Every caller asserts it found
    something before asking whether it parses.
    """
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip() == "command:":
            start = index + 1
            break
    if start is None:
        return []
    argv, indent = [], None
    for line in lines[start:]:
        if not line.strip():
            continue
        here = len(line) - len(line.lstrip())
        if indent is None:
            indent = here
        if here < indent or not line.strip().startswith("- "):
            break
        argv.append(line.strip()[2:].strip().strip('"'))
    return argv


def shown(value):
    """A detail for `check`, which concatenates and so wants a string."""
    if isinstance(value, (list, tuple)):
        return " ".join(str(item) for item in value)
    return str(value)


def flat(path):
    """A path with its separators flattened.

    The installer is bash and writes back whatever it was handed. Under Git
    Bash on Windows that is a mixture of both separators in one string, which
    compares equal to neither form of what was passed in.
    """
    return str(path).replace("\\", "/")


def token_in(path):
    for line in open(path, encoding="utf-8"):
        if line.startswith(WEB_TOKEN_ENV + "="):
            return line.split("=", 1)[1].strip()
    return None


PARSER = build_parser()


def parses(argv):
    """Whether nettail's own parser accepts this command line."""
    try:
        PARSER.parse_args(argv)
        return True
    except SystemExit:
        return False


# --- it is a shell script, and has to be one --------------------------------

done = subprocess.run([BASH, "-n", INSTALLER], capture_output=True, text=True)
check("the installer parses as bash", done.returncode == 0,
      done.stderr.strip()[:400])


# --- the systemd path -------------------------------------------------------

with tempfile.TemporaryDirectory() as where:
    code, output, paths = install(where, "--systemd", "--web")
    check("a systemd install runs to the end", code == 0, output[-500:])

    unit_path = os.path.join(paths["units"], "nettail.service")
    check("and writes a unit", os.path.isfile(unit_path), output[-300:])
    unit = open(unit_path, encoding="utf-8").read() if os.path.isfile(unit_path) else ""

    argv = argv_from_unit(unit)
    check("whose ExecStart carries a command line", len(argv) > 2, shown(argv))
    check("that nettail's own parser accepts", parses(argv), shown(argv))
    check("and which asks for the web interface", "--web" in argv, shown(argv))

    # The bug that shipped, pinned at the place it would come back.
    check("no unexpanded shell variable survives into ExecStart",
          "${" not in " ".join(argv), shown(argv))

    check("the unit runs as the service user", "User=nettail" in unit)
    check("and its group, which useradd was told to make",
          "Group=nettail" in unit)
    env_path = os.path.join(paths["config"], "nettail.env")
    check("the unit reads the environment file",
          "EnvironmentFile=" + flat(env_path) in flat(unit),
          shown([ln for ln in unit.splitlines()
                 if ln.startswith("EnvironmentFile")]))

    # AGENTS.md says the token is kept out of ps. That is only true while
    # nothing puts it on the command line, which is what this holds.
    secret = token_in(env_path)
    check("the env file holds a token", bool(secret), repr(secret))
    check("which appears nowhere in the unit", secret not in unit)
    check("and no --web-token is passed at all", "--web-token" not in argv,
          argv)

    # Putting the view on the network is the operator's decision, not the
    # installer's, so the loopback default has to survive untouched.
    check("--web-bind is left at its default", "--web-bind" not in argv, shown(argv))

    log = paths["log"]
    useradd = open(os.path.join(log, "useradd.argv"), encoding="utf-8").read()
    check("useradd is asked for a matching group explicitly",
          "--user-group" in useradd, useradd.strip())
    check("and for a system account with no home",
          "--system" in useradd and "--no-create-home" in useradd)
    pip = open(os.path.join(log, "python3.argv"), encoding="utf-8").read()
    check("dependencies are installed with their hashes checked",
          "--require-hashes" in pip,
          shown([ln for ln in pip.splitlines() if "pip" in ln][:2]))
    check("systemctl was asked to reload, not to start, under --no-start",
          "daemon-reload" in open(os.path.join(log, "systemctl.argv"),
                                  encoding="utf-8").read())

    if os.name != "nt":
        mode = os.stat(env_path).st_mode & 0o777
        check("the env file is 0640", mode == 0o640, oct(mode))
        chown = open(os.path.join(log, "chown.argv"), encoding="utf-8").read()
        check("and is given to the service group",
              "root:nettail" in chown, chown.strip())


# --- the docker path --------------------------------------------------------

with tempfile.TemporaryDirectory() as where:
    code, output, paths = install(where, "--docker", "--web")
    check("a docker install runs to the end", code == 0, output[-500:])

    compose_path = os.path.join(paths["config"], "docker-compose.yml")
    check("and writes a compose file", os.path.isfile(compose_path))
    compose = (open(compose_path, encoding="utf-8").read()
               if os.path.isfile(compose_path) else "")

    argv = argv_from_compose(compose)
    check("whose command is a real argument list", len(argv) > 2, shown(argv))
    check("that nettail's own parser accepts", parses(argv), shown(argv))

    # This is the check that fails on the bug that was live in 0.5.1. A
    # ${...} here is interpolated by compose on the host and resolved to
    # nothing, and the container started with an empty token and stopped.
    check("no unexpanded shell variable survives into the command",
          "${" not in " ".join(argv), shown(argv))
    check("and no --web-token is passed at all", "--web-token" not in argv,
          argv)

    env_path = os.path.join(paths["config"], "nettail.env")
    secret = token_in(env_path)
    check("the env file holds a token", bool(secret))
    check("which appears nowhere in the compose file", secret not in compose)
    check("the container is handed that file instead",
          "env_file:" in compose and flat(env_path) in flat(compose))

    # The image binds 0.0.0.0 because loopback in a container's namespace
    # answers nothing through a published port. What keeps it private is the
    # publish, and host networking is why the exporter column means anything.
    check("the compose file uses host networking",
          "network_mode: host" in compose)
    check("and the command still names loopback for the view",
          "--web-bind" in argv and "127.0.0.1" in argv, shown(argv))


# --- every resolver mode the program has, and nothing else ------------------
#
# Both directions, the way test_key_help holds keys and dispatch to each other.
# Forward: a mode nettail gained that the installer never learned about would
# fail here. Reverse: the ExecStart parse above is what catches a mode the
# installer offers and nettail does not, which is the bug that shipped.

for mode in Resolver.MODES:
    with tempfile.TemporaryDirectory() as where:
        code, output, paths = install(where, "--systemd", "--web",
                                      "--resolve", mode)
        check("the installer accepts --resolve %s" % mode, code == 0,
              output[-300:])
        unit_path = os.path.join(paths["units"], "nettail.service")
        argv = argv_from_unit(open(unit_path, encoding="utf-8").read()
                              if os.path.isfile(unit_path) else "")
        check("and nettail accepts what it wrote for %s" % mode,
              len(argv) > 2 and parses(argv), shown(argv))
        check("which names that mode", mode in argv, shown(argv))

with tempfile.TemporaryDirectory() as where:
    code, output, _paths = install(where, "--systemd", "--web",
                                   "--resolve", "passive")
    check("a mode nettail does not have is refused by the installer",
          code != 0, output[-200:])
    check("and refused by name", "passive" in output, output[-200:])


# --- running it twice -------------------------------------------------------
#
# The installer exists to be re-run for a new version. What must survive is
# the token, because a URL somebody bookmarked is not ours to churn, and the
# user and the virtual environment, because replacing either would be a
# surprise rather than an upgrade.

with tempfile.TemporaryDirectory() as where:
    binned = make_fakes(where)
    code, first_out, paths = install(where, "--systemd", "--web",
                                     binned=binned)
    check("the first run succeeds", code == 0, first_out[-300:])
    env_path = os.path.join(paths["config"], "nettail.env")
    first_token = token_in(env_path)
    first_unit = open(os.path.join(paths["units"], "nettail.service"),
                      encoding="utf-8").read()

    code, second_out, paths = install(where, "--systemd", "--web",
                                      binned=binned)
    check("and so does running it again", code == 0, second_out[-300:])
    check("the token is kept, so a bookmark still works",
          token_in(env_path) == first_token,
          "%r then %r" % (first_token, token_in(env_path)))
    check("the existing user is reused rather than made again",
          "already exists" in second_out, second_out[-300:])
    check("and so is the virtual environment",
          "reusing the virtual environment" in second_out, second_out[-300:])
    check("the unit comes out the same both times",
          open(os.path.join(paths["units"], "nettail.service"),
               encoding="utf-8").read() == first_unit)


# --- without the web interface ----------------------------------------------

with tempfile.TemporaryDirectory() as where:
    code, output, paths = install(where, "--systemd", "--no-web")
    check("an install without the browser view succeeds", code == 0,
          output[-300:])
    argv = argv_from_unit(open(os.path.join(paths["units"], "nettail.service"),
                               encoding="utf-8").read())
    check("and nettail accepts that too", len(argv) > 1 and parses(argv), shown(argv))
    check("with no web flags on it",
          not [a for a in argv if a.startswith("--web")], shown(argv))


# --- --external-only, the last flag it can pass -----------------------------

with tempfile.TemporaryDirectory() as where:
    code, output, paths = install(where, "--systemd", "--web",
                                  "--external-only")
    check("--external-only installs", code == 0, output[-300:])
    argv = argv_from_unit(open(os.path.join(paths["units"], "nettail.service"),
                               encoding="utf-8").read())
    check("and reaches the command line", "--external-only" in argv, shown(argv))
    check("which nettail accepts", parses(argv), shown(argv))

finish("installer")
