#!/usr/bin/env bash
#
# Install nettail, either as a systemd service or as a Docker container.
#
# It asks which, then asks the handful of things it cannot work out: the port
# to listen for flows on, whether to serve the browser view and on which port,
# and how hard to try at turning addresses into names. Every answer can be
# given as a flag instead. --non-interactive never prompts: a setting with a
# documented default takes it, and a choice with no safe default fails
# rather than being guessed at.
#
# Safe to run more than once. Re-running picks up a new version and keeps the
# web token, so a bookmark that works carries on working.
#
set -euo pipefail

SERVICE_USER=nettail
INSTALL_DIR=/opt/nettail
CONFIG_DIR=/etc/nettail
ENV_FILE="$CONFIG_DIR/nettail.env"
UNIT_NAME=nettail.service
UNIT_FILE="/etc/systemd/system/$UNIT_NAME"
COMPOSE_FILE="$CONFIG_DIR/docker-compose.yml"
IMAGE=ghcr.io/mjaksn/nettail:latest

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
    cat <<'USAGE'
Usage: sudo scripts/install.sh [options]

Installs nettail as a systemd service, or writes a Docker Compose file and
brings it up. With no options it asks which, and asks for the ports.

Options:
  --systemd            install as a systemd service, do not ask
  --docker             install as a Docker container, do not ask
  --flow-port PORT     UDP port to listen for flows on (default 2055)
  --web                serve the browser view
  --no-web             do not serve the browser view
  --web-port PORT      TCP port for the browser view (default 2056)
  --resolve MODE       off, dns, or all (default dns)
  --external-only      show only flows that touch the internet
  --non-interactive    never prompt. A setting with a documented default takes
                       it; a choice with no safe default fails instead
  --no-start           install but do not start it
  --help               show this message

Run it again after pulling a new version. Your settings and web token survive.
USAGE
}

say() { printf '  %s\n' "$*"; }
die() { echo "install.sh: $*" >&2; exit 1; }

# A two-argument flag with nothing after it would otherwise `shift 2` off the
# end, which returns non-zero and, under `set -e`, ends the script without a
# word. Checked before shifting so the caller is told which flag it was.
need() {
    [ $# -ge 2 ] || die "$1 needs a value"
    printf '%s' "$2"
}

MODE=""
FLOW_PORT=""
WEB=""
WEB_PORT=""
RESOLVE=""
EXTERNAL_ONLY=""
INTERACTIVE=1
START_IT=1

while [ $# -gt 0 ]; do
    case "$1" in
        --systemd) MODE=systemd; shift ;;
        --docker) MODE=docker; shift ;;
        --flow-port) FLOW_PORT="$(need "$@")"; shift 2 ;;
        --flow-port=*) FLOW_PORT="${1#*=}"; shift ;;
        --web) WEB=1; shift ;;
        --no-web) WEB=0; shift ;;
        --web-port) WEB_PORT="$(need "$@")"; shift 2 ;;
        --web-port=*) WEB_PORT="${1#*=}"; shift ;;
        --resolve) RESOLVE="$(need "$@")"; shift 2 ;;
        --resolve=*) RESOLVE="${1#*=}"; shift ;;
        --external-only) EXTERNAL_ONLY=1; shift ;;
        --non-interactive) INTERACTIVE=0; shift ;;
        --no-start) START_IT=0; shift ;;
        --help|-h) usage; exit 0 ;;
        *) echo "install.sh: unrecognised option '$1'" >&2; usage >&2; exit 2 ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    echo "install.sh: this needs root. Try: sudo scripts/install.sh" >&2
    exit 1
fi


# A prompt that takes a default, and that refuses to block when nobody is there
# to answer. --non-interactive is the explicit form; a run from cron with no
# terminal is the accidental one, and both take the default rather than wait
# for input that is never coming.
#
# Only ever used where a default is documented and safe. A choice that is
# neither, such as whether to serve the browser view, is guarded above and
# fails instead.
ask() {
    local prompt="$1" default="$2" answer=""
    if [ "$INTERACTIVE" -eq 0 ] || [ ! -t 0 ]; then
        printf '%s' "$default"
        return
    fi
    read -r -p "  $prompt [$default]: " answer </dev/tty || answer=""
    printf '%s' "${answer:-$default}"
}

ask_yes_no() {
    local prompt="$1" default="$2" answer=""
    answer="$(ask "$prompt (y/n)" "$default")"
    case "$answer" in
        [Yy]*) printf '1' ;;
        [Nn]*) printf '0' ;;
        *) if [ "$default" = "y" ]; then printf '1'; else printf '0'; fi ;;
    esac
}

# Ports are checked here rather than left for the program to reject after the
# unit has been written and enabled, which is a slower and more confusing way
# to learn the same thing.
check_port() {
    local value="$1" what="$2"
    case "$value" in
        ''|*[!0-9]*) die "$what must be a number, not '$value'" ;;
    esac
    if [ "$value" -lt 1 ] || [ "$value" -gt 65535 ]; then
        die "$what must be between 1 and 65535, not $value"
    fi
    # Below 1024 needs privilege the service user will not have. nettail's own
    # default is above it for exactly this reason, and the README says so.
    if [ "$value" -lt 1024 ]; then
        say "warning: $what $value is privileged, and this runs unprivileged"
    fi
}

echo
echo "Installing nettail from $SOURCE_DIR"
echo

# == what kind of install ====================================================

if [ -z "$MODE" ]; then
    if [ "$INTERACTIVE" -eq 0 ]; then
        die "--systemd or --docker is required with --non-interactive"
    fi
    echo "  How should nettail run?"
    echo
    echo "    1) systemd, in a virtual environment on this machine"
    echo "    2) Docker, from the published image"
    echo
    case "$(ask "1 or 2" "1")" in
        2) MODE=docker ;;
        *) MODE=systemd ;;
    esac
    echo
fi

# == the answers =============================================================

if [ -z "$FLOW_PORT" ]; then
    FLOW_PORT="$(ask "UDP port to listen for flows on" "2055")"
fi
check_port "$FLOW_PORT" "the flow port"

# The browser view opens a second port, so which way this goes is not
# something to fall back to a default on when nobody is there to be asked.
# A port has a documented default and is merely unstated; this is a choice.
if [ -z "$WEB" ] && [ "$INTERACTIVE" -eq 0 ]; then
    die "--web or --no-web is required with --non-interactive"
fi

if [ -z "$WEB" ]; then
    WEB="$(ask_yes_no "Serve the display to a browser" "y")"
fi

if [ "$WEB" -eq 1 ]; then
    if [ -z "$WEB_PORT" ]; then
        WEB_PORT="$(ask "TCP port for the browser view" "2056")"
    fi
    check_port "$WEB_PORT" "the web port"
    if [ "$WEB_PORT" = "$FLOW_PORT" ]; then
        die "the web port and the flow port cannot both be $WEB_PORT"
    fi
else
    WEB_PORT="${WEB_PORT:-2056}"
fi

if [ -z "$RESOLVE" ]; then
    RESOLVE="$(ask "Hostname resolution: off, dns or all" "dns")"
fi
case "$RESOLVE" in
    off|dns|all) ;;
    *) die "unknown --resolve '$RESOLVE'; it is off, dns or all" ;;
esac

echo

# == a token that survives a reinstall =======================================

mkdir -p "$CONFIG_DIR"
chmod 0750 "$CONFIG_DIR"

# Kept in a file of its own rather than regenerated every time, so that running
# this again to pick up a new version does not invalidate a bookmark that is
# working. Same reasoning as readerboard's API key: a value somebody has
# already saved is not ours to churn.
if [ -f "$ENV_FILE" ] && grep -q '^NETTAIL_WEB_TOKEN=' "$ENV_FILE"; then
    WEB_TOKEN="$(sed -n 's/^NETTAIL_WEB_TOKEN=//p' "$ENV_FILE" | head -n 1)"
    say "keeping the existing web token"
else
    if command -v python3 >/dev/null 2>&1; then
        WEB_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
    else
        WEB_TOKEN="$(head -c 18 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=')"
    fi
    say "generated a web token"
fi

# The token goes in a file rather than on the command line, so that it does not
# show up in ps output to every user on the machine.
umask 077
cat > "$ENV_FILE" <<ENV
# Written by scripts/install.sh.
#
# The token is the whole of the web interface's authentication, so this file is
# 0640 and owned by the service. Re-running the installer keeps it, which is
# what makes a bookmarked URL survive an upgrade.
NETTAIL_WEB_TOKEN=$WEB_TOKEN
ENV
chmod 0640 "$ENV_FILE"

ARGS=(--port "$FLOW_PORT" --resolve "$RESOLVE")
if [ "$WEB" -eq 1 ]; then
    ARGS+=(--web --web-port "$WEB_PORT")
fi
if [ -n "$EXTERNAL_ONLY" ]; then
    ARGS+=(--external-only)
fi

if [ "$MODE" = "systemd" ]; then

    # == systemd =============================================================

    command -v systemctl >/dev/null 2>&1 \
        || die "systemctl not found. Choose the Docker install, or use a systemd machine."
    command -v python3 >/dev/null 2>&1 \
        || die "python3 not found. Install it and run this again."

    if id "$SERVICE_USER" >/dev/null 2>&1; then
        say "user $SERVICE_USER already exists"
    else
        # --user-group is not decoration. The unit below says Group=nettail,
        # and whether a bare useradd creates a matching group depends on
        # USERGROUPS_ENAB in login.defs, which differs between distributions.
        # Asking for it explicitly means the unit cannot reference a group that
        # was never made.
        useradd --system --user-group --no-create-home \
            --shell /usr/sbin/nologin "$SERVICE_USER"
        say "created system user and group $SERVICE_USER"
    fi
    chown root:"$SERVICE_USER" "$ENV_FILE"

    if [ ! -x "$INSTALL_DIR/venv/bin/python" ]; then
        mkdir -p "$INSTALL_DIR"
        python3 -m venv "$INSTALL_DIR/venv"
        say "created a virtual environment in $INSTALL_DIR/venv"
    else
        say "reusing the virtual environment in $INSTALL_DIR/venv"
    fi

    "$INSTALL_DIR/venv/bin/python" -m pip install --quiet --upgrade pip

    # The lock file pins what this machine runs, by version and by the hash of
    # every file the index publishes, and --require-hashes makes pip check
    # them. A version pin says what to install; the hashes say what the bytes
    # must be.
    if [ -f "$SOURCE_DIR/requirements.lock" ]; then
        "$INSTALL_DIR/venv/bin/python" -m pip install --quiet --require-hashes \
            -r "$SOURCE_DIR/requirements.lock"
        say "installed pinned dependencies, hashes verified"
    fi

    # The one package needed to turn the source tree into a wheel, pinned the
    # same way, so the build below can skip pip's build isolation instead of
    # fetching an unverified setuptools of its own.
    build_isolation=""
    if [ -f "$SOURCE_DIR/requirements-build.lock" ]; then
        "$INSTALL_DIR/venv/bin/python" -m pip install --quiet --require-hashes \
            -r "$SOURCE_DIR/requirements-build.lock"
        build_isolation="--no-build-isolation"
    fi

    # shellcheck disable=SC2086  # deliberately unquoted: empty means "pass nothing"
    "$INSTALL_DIR/venv/bin/python" -m pip install --quiet --no-deps $build_isolation \
        "$SOURCE_DIR"
    say "installed the nettail package"

    chown -R root:root "$INSTALL_DIR"

    # --web-bind is left at its loopback default. Putting the view on the
    # network is a decision to make deliberately by editing the unit, not
    # something an installer should do quietly on somebody's behalf.
    exec_args=""
    for arg in "${ARGS[@]}"; do
        exec_args="$exec_args $arg"
    done
    if [ "$WEB" -eq 1 ]; then
        exec_args="$exec_args --web-token \${NETTAIL_WEB_TOKEN}"
    fi

    cat > "$UNIT_FILE" <<UNIT
[Unit]
Description=nettail, a NetFlow and IPFIX collector
Documentation=https://github.com/mjaksn/nettail
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
EnvironmentFile=$ENV_FILE
ExecStart=$INSTALL_DIR/venv/bin/nettail$exec_args
Restart=always
RestartSec=5

# SIGTERM is handled the same as Ctrl-C, so the exit summary is printed on the
# way out rather than the process being cut off mid-line.
KillSignal=SIGTERM

StandardOutput=journal
StandardError=journal
SyslogIdentifier=nettail

# Hardening. It binds two ports, reads one file, and writes nothing.
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictNamespaces=true
RestrictRealtime=true
RestrictSUIDSGID=true
LockPersonality=true
SystemCallArchitectures=native
SystemCallFilter=@system-service
SystemCallErrorNumber=EPERM
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX

[Install]
WantedBy=multi-user.target
UNIT

    chmod 0644 "$UNIT_FILE"
    systemctl daemon-reload
    systemctl enable "$UNIT_NAME" >/dev/null 2>&1
    say "installed and enabled $UNIT_NAME"

    if [ "$START_IT" -eq 1 ]; then
        systemctl restart "$UNIT_NAME"
        say "started $UNIT_NAME"
    fi

else

    # == docker ==============================================================

    command -v docker >/dev/null 2>&1 \
        || die "docker not found. Choose the systemd install, or install Docker."
    docker compose version >/dev/null 2>&1 \
        || die "'docker compose' is not available. Install the Compose plugin."

    # Host networking, and not as a preference. Behind the bridge the address a
    # datagram came from is rewritten to the gateway, so every exporter shows
    # up as the same address and the EXPORTER column stops distinguishing
    # anything. A bridged run can still serve the browser view, through a
    # publish and a routable --web-bind, but the exporter column is the whole
    # point of a flow collector. The README says all of this at more length.
    if [ "$(uname -s)" != "Linux" ]; then
        say "warning: host networking is a Linux arrangement, and this is not Linux"
        say "         the collector will run, but the browser view will not be reachable"
    fi

    web_lines=""
    if [ "$WEB" -eq 1 ]; then
        web_lines="      - --web
      - --web-port
      - \"$WEB_PORT\"
      - --web-bind
      - 127.0.0.1
      - --web-token
      - \${NETTAIL_WEB_TOKEN}"
    fi

    extra=""
    if [ -n "$EXTERNAL_ONLY" ]; then
        extra="
      - --external-only"
    fi

    cat > "$COMPOSE_FILE" <<COMPOSE
# Written by scripts/install.sh. Running the installer again rewrites this
# file, so keep any edits of your own somewhere else.

name: nettail

services:
  nettail:
    image: $IMAGE
    container_name: nettail
    restart: unless-stopped

    # See the README. This is what keeps the exporter address real, and what
    # makes the browser view reachable at all.
    network_mode: host

    env_file:
      - $ENV_FILE

    command:
      - --port
      - "$FLOW_PORT"
      - --resolve
      - $RESOLVE$extra
$web_lines
COMPOSE

    chmod 0640 "$COMPOSE_FILE"
    say "wrote $COMPOSE_FILE"

    if docker pull --quiet "$IMAGE" >/dev/null 2>&1; then
        say "pulled $IMAGE"
    else
        say "warning: could not pull $IMAGE; compose will try again on start"
    fi

    if [ "$START_IT" -eq 1 ]; then
        docker compose --file "$COMPOSE_FILE" up --detach >/dev/null
        say "started the nettail container"
    fi
fi

# == what to do next =========================================================

echo
echo "Done."
echo

cat <<NEXT
  Listening for flows on UDP $FLOW_PORT. Point your exporter at this machine on
  that port, and open it in the firewall if there is one:

      ufw allow $FLOW_PORT/udp

NEXT

if [ "$WEB" -eq 1 ]; then
    cat <<WEBNEXT
  The browser view is at:

      http://127.0.0.1:$WEB_PORT/t/$WEB_TOKEN/

  It is bound to loopback, so only this machine can reach it. The token is kept
  in $ENV_FILE and survives a reinstall, so that URL keeps working.

WEBNEXT
fi

if [ "$MODE" = "systemd" ]; then
    cat <<SYSNEXT
  Check it came up:

      systemctl status $UNIT_NAME
      journalctl -u $UNIT_NAME -n 50 --no-pager

SYSNEXT
else
    cat <<DOCKNEXT
  Check it came up:

      sudo docker compose --file $COMPOSE_FILE ps
      sudo docker compose --file $COMPOSE_FILE logs --tail 50

DOCKNEXT
fi
