# nettail in a container.
#
# What this image is for is the web view. nettail is a console program first:
# its display is a live terminal with keyboard control, and that wants
# `docker run -it` and a real terminal to be worth anything. `--web` is the
# mode that works properly in a detached container, so that is what the
# entrypoint sets up and what the compose example runs.
#
# Two stages. The first builds a virtual environment, the second copies the
# finished environment across, so pip and its cache are in nothing anybody
# pulls. There is no compiler in either and nothing needs one: the collector
# and both packages it is built on are pure Python, so every platform below
# takes the same wheels.

# The base image is pinned by digest, the way the workflows pin their actions by
# SHA, so that a rebuild of a released tag produces what it produced the first
# time. The digest names a manifest list covering amd64, arm64 and arm/v7, and
# the daemon picks the right one out of it.
#
# A patch tag rather than the rolling 3.14-slim, and that is not fussiness.
# The rolling tags are rebuilt every few days, so whatever digest they point
# at is always a few days old, and nothing that young may be used here. A
# patch tag stops moving once the next one ships, so it can be both specific
# and old enough. Check the age before bumping it: this one was 23 days when
# it was pinned.
FROM python:3.14.6-slim@sha256:7bec7ddcddeff7975d6ba9b4be7dd6f6b2f55e7491539145e2978f7f97ce9144 AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN python -m venv /opt/nettail/venv
ENV PATH="/opt/nettail/venv/bin:$PATH"

# The dependencies first and on their own, so that editing the source does not
# invalidate the layer that installed them.
#
# --require-hashes is the point of the lock files carrying hashes: a version pin
# says what to install, and the hashes say what the bytes must be. pip refuses
# the whole install if any file it selects is not one of the ones listed.
COPY requirements.lock requirements-build.lock ./
RUN pip install --require-hashes --requirement requirements.lock \
    && pip install --require-hashes --requirement requirements-build.lock

# Then the collector itself.
#
# --no-deps so that the ranges in pyproject.toml cannot quietly pull a version
# the lock file did not choose. --no-build-isolation so that pip builds the
# wheel with the setuptools installed above rather than fetching an unpinned one
# from the index, which would be the only unverified thing in the whole install.
#
# setuptools then comes back out. It was needed to build the wheel and is needed
# by nothing at run time, and this environment is copied wholesale into the
# image below.
COPY pyproject.toml README.md LICENSE ./
COPY nettail ./nettail
RUN pip install --no-deps --no-build-isolation . \
    && pip uninstall --yes setuptools


FROM python:3.14.6-slim@sha256:7bec7ddcddeff7975d6ba9b4be7dd6f6b2f55e7491539145e2978f7f97ce9144

LABEL org.opencontainers.image.title="nettail" \
      org.opencontainers.image.description="A NetFlow v5, NetFlow v9 and IPFIX collector that prints flows to a console, with hostnames, colour, a live status bar and an optional browser view" \
      org.opencontainers.image.source="https://github.com/mjaksn/nettail" \
      org.opencontainers.image.documentation="https://github.com/mjaksn/nettail/blob/main/README.md" \
      org.opencontainers.image.licenses="MIT"

# A fixed UID and GID rather than an arbitrary one, so that a bind-mounted
# hosts file can be given an owner that matches. 10001 is above the range
# Debian hands out to system accounts, so it will not collide with a user the
# base image created.
#
# The collector needs no privilege. Its default port, 2055, is above 1024, so
# binding it does not want root and the README already says so.
RUN groupadd --gid 10001 nettail \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin nettail

COPY --from=builder /opt/nettail/venv /opt/nettail/venv

ENV PATH="/opt/nettail/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

# The collector listens here, and the browser view is served here. Both UDP and
# TCP are named explicitly because the two are easy to confuse and the flow port
# is the one people get wrong.
EXPOSE 2055/udp
EXPOSE 2056/tcp

USER nettail

# --web-bind 0.0.0.0 rather than the program's own loopback default, because
# loopback inside a container's namespace is unreachable from a published port
# and the browser view would simply never answer.
#
# That is not the security regression it looks like. The exposure moves to the
# publish, where it can be seen and set: `-p 127.0.0.1:2056:2056` keeps the view
# on the host exactly as the loopback default does, and the collector says so at
# startup instead of the warning it prints for a routable bind on a host.
#
# CMD rather than baking it into ENTRYPOINT, so that `docker run ... nettail
# --json` or an interactive `-it` run with no arguments can replace the lot.
ENTRYPOINT ["nettail"]
CMD ["--web", "--web-bind", "0.0.0.0"]
