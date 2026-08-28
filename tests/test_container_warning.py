"""What the collector says about a routable web bind, and where it says it.

On a host, a bind to anything but loopback is warned about loudly, because it
puts a live map of this network on an address other machines can reach over
plain HTTP. Inside a container the same bind is unremarkable: loopback in the
container's own namespace is unreachable from a published port, so the image
asks for 0.0.0.0 every time it starts.

Printing the loud version on every container start would be crying wolf, and a
reader who learns to skip the line skips it on a host too, which is the case it
exists for. So the container gets its own line, pointing at the `docker run -p`
that actually settles the exposure and that nothing inside can see.

The risk in guessing at the environment is that the guess changes behaviour
rather than prose. These checks pin it to prose.
"""
import os
import queue

from harness import check, finish, plain

from nettail.cli import web_bind_warning
from nettail.feed import Feed
from nettail.web import WebInterface, in_container, is_loopback

# -- the detection ---------------------------------------------------------

# $container is what podman and systemd-nspawn set. Saved and restored rather
# than assumed absent, because this suite may itself be running in a container,
# which is the environment it is about.
_saved = os.environ.get("container")
on_disk = any(os.path.exists(p) for p in ("/.dockerenv", "/run/.containerenv"))
try:
    os.environ.pop("container", None)
    check("with no marker set, detection follows the filesystem",
          in_container() == on_disk)

    os.environ["container"] = "podman"
    check("$container alone is enough", in_container() is True)

    os.environ["container"] = ""
    check("an empty $container is not a marker", in_container() == on_disk)
finally:
    if _saved is None:
        os.environ.pop("container", None)
    else:
        os.environ["container"] = _saved

# -- the two warnings ------------------------------------------------------

host = plain(web_bind_warning("0.0.0.0", 2056, contained=False))
contained = plain(web_bind_warning("0.0.0.0", 2056, contained=True))

check("both name the address that was bound",
      "0.0.0.0" in host and "0.0.0.0" in contained)
check("the host warning says what an onlooker gets",
      "travels in the clear" in host, host)
check("and is coloured, because it is a warning",
      web_bind_warning("0.0.0.0", 2056, contained=False) != host)

check("the container line points at the publish instead",
      "-p 127.0.0.1:2056:2056" in contained, contained)
check("and names the exposed publish too, so the choice is visible",
      "-p 2056:2056" in contained, contained)
check("it carries no colour, because it is not a warning",
      web_bind_warning("0.0.0.0", 2056, contained=True) == contained)
check("and does not repeat the host warning's alarm",
      "travels in the clear" not in contained, contained)

check("the port is taken from the argument, not hardcoded",
      "-p 127.0.0.1:9999:9999" in plain(
          web_bind_warning("0.0.0.0", 9999, contained=True)))

# -- what the detection is not allowed to affect ---------------------------

# The bind is settled by --web-bind and nothing else. If a container ever
# started binding somewhere the flag did not ask for, this is where it shows.
os.environ["container"] = "podman"
try:
    site = WebInterface(Feed(), queue.Queue(), set(), bind="127.0.0.1", port=0)
    site.bind()
    try:
        check("a loopback bind stays loopback in a container",
              is_loopback(site.bound_addr), site.bound_addr)
    finally:
        site.stop(timeout=1.0)
finally:
    if _saved is None:
        os.environ.pop("container", None)
    else:
        os.environ["container"] = _saved

finish("container warning")
