"""Sizes, rates and durations, written for a column.

What a field *is* is netflume's question, and `addr_kind` and `tcp_flags_str`
are imported from there by the modules that need them. A service name is
netflume's answer too, with a shipped list behind it for the ports a system
database happens not to know, which is `services.py`. What is left here is the
part that only a console needs: turning a number into something narrow enough
to sit in a fixed width column and still be read at a glance.
"""


def human_bytes(n):
    if n is None:
        return "-"
    n = float(n)
    for unit in ("B", "K", "M", "G", "T"):
        if n < 1024 or unit == "T":
            if unit == "B":
                return f"{int(n)}{unit}"
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}T"


def human_count(n):
    if n is None:
        return "-"
    if n < 1000:
        return str(n)
    if n < 1000000:
        return f"{n / 1000:.1f}k"
    return f"{n / 1000000:.1f}M"


def human_bits(bps):
    """A bit rate, in the decimal units link speeds are always quoted in."""
    if not bps:
        return "0 bps"
    for unit in ("bps", "kbps", "Mbps", "Gbps"):
        if bps < 1000 or unit == "Gbps":
            return f"{bps:.1f} {unit}" if unit != "bps" else f"{bps:.0f} bps"
        bps /= 1000
    return f"{bps:.1f} Gbps"


def human_clock(seconds):
    """A runtime as hh:mm:ss.

    Kept apart from human_duration, which describes how long one flow lived
    and is written for a narrow column. This one is read as a clock: the hours
    are always there, padded, so two runtimes can be compared down the page
    without counting the colons first. Hours are not wrapped at a day, since a
    collector left running for a week should say so rather than say 4:00:00.
    """
    if seconds is None:
        return "-"
    seconds = int(max(seconds, 0))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def human_duration(seconds):
    """A flow lifetime, short enough to sit in a column."""
    if seconds is None:
        return "-"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, seconds = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m{seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"
