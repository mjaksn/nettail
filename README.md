# nettail

[![CI](https://github.com/mjaksn/nettail/actions/workflows/ci.yml/badge.svg)](https://github.com/mjaksn/nettail/actions/workflows/ci.yml)
[![Release](https://github.com/mjaksn/nettail/actions/workflows/release.yml/badge.svg)](https://github.com/mjaksn/nettail/actions/workflows/release.yml)
[![PyPI](https://img.shields.io/pypi/v/nettail)](https://pypi.org/project/nettail/)
[![GHCR](https://img.shields.io/badge/ghcr.io-nettail-blue)](https://github.com/mjaksn/nettail/pkgs/container/nettail)
[![Docker Hub](https://img.shields.io/docker/v/mjaksn/nettail?label=docker%20hub&sort=semver)](https://hub.docker.com/r/mjaksn/nettail)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/mjaksn/nettail/blob/main/LICENSE)

A NetFlow and IPFIX collector that prints flow records to the console in a
readable table, annotating addresses with hostnames where it can find them.
`tail -f` for your network.

Built for pointing a UniFi Dream Machine Pro at a workstation and actually seeing what
the network is doing. Works with any exporter that speaks NetFlow v5, NetFlow v9, or
IPFIX (v10).

Two dependencies, both of them halves of this program that grew up and left:
[netflume](https://pypi.org/project/netflume/) reads the wire, and
[lanname](https://pypi.org/project/lanname/) turns an address into a hostname.
Neither has dependencies of its own, so `pip install nettail` brings in three
pure Python packages and nothing else.

```
TIME         EXPORTER        PROTO  SOURCE                                     DESTINATION                                 PKTS    BYTES     DUR  FLAGS
13:40:03.000 10.0.0.1        TCP    192.168.1.42:51234 (macbook-pro)         ↑ 140.82.114.4:443/https (github)               23     4.1K   4.90s  ...AP.SF
13:40:03.000 10.0.0.1        UDP    192.168.1.77:5353/mdns (hue)             ⇄ 224.0.0.251:5353/mdns                          2     180B   4.90s
13:39:56.103 10.0.0.1        TCP    10.0.1.5:44321 (nas)                     ↑ 104.244.42.1:443/https (twitter-edge)        412    57.5K  12.50s  ...AP...
13:40:08.453 10.0.0.1        TCP    1.1.1.1:853/domain-s                     ↓ 10.0.1.5:39012 (nas)                           4     320B   0.15s  ......S.
```

---

## Contents

- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Configuring the UDM Pro](#configuring-the-udm-pro)
- [Command line options](#command-line-options)
- [The status bar](#the-status-bar)
- [Keyboard controls](#keyboard-controls)
- [The web interface](#the-web-interface)
- [The traffic summary](#the-traffic-summary)
- [Output format](#output-format)
- [Hostname resolution](#hostname-resolution)
- [JSON output](#json-output)
- [Installing it](#installing-it)
- [Running as a service](#running-as-a-service)
- [Running in Docker](#running-in-docker)
- [How it works](#how-it-works)
- [Troubleshooting](#troubleshooting)
- [Tests](#tests)
- [Limitations](#limitations)
- [Extending it](#extending-it)

---

## Requirements

- Python 3.9 or newer
- [netflume](https://pypi.org/project/netflume/), the wire decoder
- [lanname](https://pypi.org/project/lanname/), the hostname lookups

Neither has dependencies of its own, so this brings in three pure Python
packages and nothing else.

```bash
pip install nettail
```

Or, from a checkout, either of:

```bash
pip install -e .        # the nettail command, pointed at the working tree
pip install lanname netflume && python -m nettail
```

Binding to the default port 2055 does not require root on Linux, since it is above
1024. You only need elevated privileges if you choose a port below 1024.

---

## Quick start

```bash
# Listen on the default port
nettail

# Pick a port and only show flows that touch the internet
nettail --port 2055 --external-only

# Passive name resolution plus your own static mappings
nettail --resolve dns --hosts ./lan-hosts

# Dump every decoded field under each flow
nettail --verbose

# Machine readable, one object per line
nettail --json > flows.jsonl
```

Press `Ctrl-C` to stop. A summary prints on exit with datagram counts, template
statistics, name resolution hit rates, and the top ten external addresses by
volume.

---

## Configuring the UDM Pro

In the UniFi Network application:

**Settings > CyberSecure > Traffic Logging**

On some firmware versions this lives under **Settings > System > Integrations**
instead. Look for a section named NetFlow or NetFlow (IPFIX).

| Setting | Value |
| --- | --- |
| NetFlow (IPFIX) | Enabled |
| Collector address | IP of the machine running this script |
| Collector port | 2055 |
| Sampling | **Off** |
| Networks / VLANs | Select every network you want visibility into |

### Sampling must be off

This matters more than anything else on the page. Sampled export throws away most
flows before they leave the router. A malware beacon is a handful of small flows per
hour, and at 1:1024 sampling you will never see it. Sampling is fine for bandwidth
graphs and useless for security monitoring.

If your firmware does not let you disable sampling, the flow data is not trustworthy
for detection work and you should look at a mirror port with Zeek instead.

You do not have to take the router's word for it. The collector reads the sampling
rate an exporter advertises and says so on stderr the first time it sees one:

```
10.0.0.1 reports 1-in-1000 sampling. The byte and packet counts shown are a sample,
so real traffic is roughly 1000x higher. Turn sampling off at the exporter for true
counts.
```

The rate is repeated in a **Sampling** section of the exit summary. It is read from
whichever form the exporter uses: `samplingInterval` or `samplerRandomInterval` on
v9, `samplingPacketInterval` with `samplingPacketSpace` or `samplingSize` out of
`samplingPopulation` on IPFIX, and the sampling field of the header on v5. Nothing
is printed when an exporter reports no sampling, or a rate of one in one.

If an exporter later reports that sampling is off, meaning an interval of one or
a selection that skips nothing, the remembered rate is dropped and a line says
the counts are complete again. An option record that says nothing about sampling, such
as one carrying interface names, never changes what is remembered.

Detection depends on the exporter actually sending options data. Silence is not
proof that sampling is off, so check the UniFi UI as well. On v5 there are no options
records at all: sampling is read from the header, and an exporter that stops
sampling simply clears the field, which is reported as nothing rather than as a
change.

### Local traffic may never arrive

Selecting every network in the table above reads like a request for visibility into
all of them. On a UDM Pro it is not what you get. Traffic to and from the internet
arrives. Traffic between two local hosts may not, and the two reasons for that are
worth telling apart, because only one of them is surprising.

**Two hosts on the same VLAN never produce a flow record at all.** They talk through
a switch and the gateway routes nothing between them, so there is nothing for it to
account. This is not a fault and no setting changes it. A flow exporter running on
the router cannot see traffic that never reaches the router, and every tool reading
NetFlow from a gateway has this property, this one included.

**Two hosts on different VLANs are routed by the gateway and may still be missing.**
This is the surprising one. Traffic between separate subnets has to pass through the
UDM Pro, and a `tracert` or `traceroute` between the hosts shows it as a hop, so the
router demonstrably handles the packets. They can still be absent from the export,
with every network selected.

Two mechanisms would account for that and they cannot be told apart from outside the
box. The routing may be offloaded to hardware along a path the flow accounting does
not sit on, or the accounting may be scoped to the internet-facing path by design.
The consequence is the same either way, which is why the distinction matters less
than it looks: neither this collector nor anything in the export configuration
changes it.

This is behaviour observed on a UDM Pro rather than anything Ubiquiti documents, so
establish it on your own hardware before concluding. Three checks, in order of
effort:

1. `tracert` or `traceroute` between the two hosts. The UDM's address as a hop means
   it routes them, and the rest of this section applies. A switch address instead
   means a layer 3 switch is doing the routing and the gateway has nothing to report,
   which is a different problem with a different answer.
2. **Insights > Flows** in the UniFi Network application. Sessions listed there and
   absent from your export mean the data exists on the box and only the export scope
   is holding it back. That is the version worth raising with Ubiquiti, being a
   demonstrable gap rather than a suspicion.
3. This collector's own exit summary. Compare `flows decoded` under **Summary** with
   `flows` under **External traffic**. Two equal figures mean every flow that arrived
   had a public endpoint, so nothing internal is being exported at all.

If it is east-west visibility you need, a gateway is the wrong vantage point for it.
Mirror the VLANs you care about to a capture host at the switch and read packets
rather than flow records, which is the same advice as for sampled export and for a
different reason.

### Other notes

- Make sure a host firewall on the collector is not dropping inbound UDP on your
  chosen port. `sudo ufw allow 2055/udp` or the nftables equivalent.
- If flows appear with the UDM's own WAN address as the source, you are seeing
  post-NAT records and have lost internal host attribution. Check which networks are
  selected in the export configuration.

---

## Command line options

```
usage: nettail [-h] [--version] [--bind BIND] [--port PORT] [--external-only]
               [--verbose] [--json] [--colour WHEN] [--no-color]
               [--header-every HEADER_EVERY] [--sticky-header] [--hide-status]
               [--no-supplemental-services] [--web] [--web-port PORT]
               [--web-bind ADDR] [--web-host NAME] [--web-token TOKEN]
               [--web-colour WHEN] [--web-readonly] [--size-scale-max BYTES |
               --size-scale-dynamic] [--size-scale-window FLOWS]
               [--resolve {off,dns,all}] [--hosts FILE] [--resolve-public]
               [--fqdn] [--resolve-workers RESOLVE_WORKERS]
               [--resolve-timeout RESOLVE_TIMEOUT]
```

### General

| Option | Default | Description |
| --- | --- | --- |
| `--version` | | Print the version and exit |
| `--bind ADDR` | `0.0.0.0` | Address to bind the UDP socket to |
| `--port PORT` | `2055` | UDP port to listen on |
| `--external-only` | off | Only display flows where the source or destination is a public IP. Everything is still counted in the summary |
| `--verbose` | off | Print every decoded field on an indented line under each flow. Also surfaces parse errors |
| `--json` | off | Emit one JSON object per flow on stdout instead of the table |
| `--colour WHEN` | `auto` | When to use ANSI colour **on this terminal**: `auto`, `always` or `never`. Under `auto` a terminal gets colour and a redirected stream does not, and `NO_COLOR` in the environment turns it off. The browser view has its own switch, `--web-colour`, and is not decided by this one. `--color` is accepted too |
| `--no-color` | off | The same as `--colour never`, and like it, about this terminal |
| `--header-every N` | `40` | Reprint the column header every N lines. `0` disables repeats |
| `--sticky-header` | off | Pin the column header to the top row of the window. See below |
| `--hide-status` | off | Turn off the two-line status bar at the foot of the window, which is shown by default whenever output is going to a terminal. The `b` key toggles it while the collector runs. See [The status bar](#the-status-bar) |
| `--no-supplemental-services` | off | Name ports from the system services database alone. Without it, a short list shipped with this program fills in the ports the system does not know. See [Service names](#service-names) |

### Web interface

All off unless `--web` is given. See [The web interface](#the-web-interface).

| Option | Default | Description |
| --- | --- | --- |
| `--web` | off | Also serve the display to a browser |
| `--web-port PORT` | `2056` | Port for the web interface |
| `--web-bind ADDR` | `127.0.0.1` | Address for the web interface. Anything other than loopback exposes this network's traffic over cleartext HTTP, and is warned about at startup |
| `--web-host NAME` | none | A name the view answers to. Under the loopback default it is added beside `localhost`; under another `--web-bind`, which otherwise answers to any name, it restricts the view to the names given. May be repeated |
| `--web-colour WHEN` | `on` | Colour in the browser view: `on` or `off`. A browser is a colour-capable reader whatever stdout is, so a redirected run does not take the colour out of it. `--web-color` is accepted too |
| `--web-token TOKEN` | random | Use this token in the URL instead of a fresh random one, so a bookmark survives a restart |
| `--web-readonly` | off | Serve the display but accept no keys from the browser |

### Sticky header

`--sticky-header` keeps the column header on the top row while flows scroll
underneath it, so you never lose track of which column is which. It works by
setting a VT100 scroll region (DECSTBM) covering every row but the first, then
letting output scroll inside that region as usual.

Two things to know before turning it on:

- **You lose scrollback.** Most terminals discard lines that scroll out of a
  margin region instead of pushing them into the scrollback buffer, so you can
  only see what is currently on screen. If you want to scroll back through past
  flows, leave the flag off and stay with `--header-every`, or use `--json` and
  write to a file.
- **It needs a real terminal.** On Windows the script enables virtual terminal
  processing automatically, which covers Windows Terminal and modern conhost.
  If stdout is redirected to a file or a pipe, or the window is too short, the
  flag prints a notice and falls back to `--header-every`. The header alone
  needs six rows; with the status bar up, which is the default, it needs
  eight.

The header is redrawn when the window is resized (the size is re-measured every
16 flow lines), and the scroll region is released on Ctrl-C so the summary and
your shell prompt are not trapped inside it. `--header-every` is ignored while
the header is pinned, since the repeats would be redundant.

### Flow size colour

| Option | Default | Description |
| --- | --- | --- |
| `--size-scale-max BYTES` | `100K` | Top of the BYTES colour scale. Accepts a plain byte count or a `K`, `M`, `G`, `T` suffix (powers of 1024, matching the column) |
| `--size-scale-dynamic` | off | Re-range the scale to the largest flow seen so far instead of a fixed top. Mutually exclusive with `--size-scale-max` |
| `--size-scale-window FLOWS` | off | Scope the dynamic scale to the last N flows instead of the whole run. Implies `--size-scale-dynamic`, so it cannot be combined with `--size-scale-max` |

See [Size colour scale](#size-colour-scale) for what the colours mean.

### Hostname resolution

| Option | Default | Description |
| --- | --- | --- |
| `--resolve MODE` | `all` | `off`, `dns`, or `all`. See below |
| `--hosts FILE` | none | Static mappings in `/etc/hosts` format. Repeatable |
| `--resolve-public` | off | Also reverse-resolve public addresses via PTR |
| `--fqdn` | off | Show `nas.lan` instead of `nas` |
| `--resolve-workers N` | `4` | Background lookup threads |
| `--resolve-timeout SEC` | `1.0` | Per-probe timeout for mDNS and NetBIOS queries |

---

## The status bar

Two lines along the foot of the window, holding the figures you would otherwise
have to stop and ask for. It is on whenever the collector is printing to a
terminal. There is no flag to turn it on, only `--hide-status` to turn it off,
and the `b` key to take it away and bring it back while the collector runs.

```
up 04:12    pkts 3.1k 12/s   flows 12.4k 1.2k/s   rx 48.0M 9.4 Mbps   ext 61% in 28.1M out 12.9M   peak 142.0 Mbps         top 93.184.216.34 (edge) 5.4M
live        all flows        v9 tmpl 6            scale dyn 4.0M      ok                           TCP 78% 443/https 41%   names all/short 412 found 88 missed
```

The first row is the wire: how long the collector has been up, what has arrived
and how fast it is arriving now, how much of it crossed the internet and which
way, the fastest the link is known to have run, and the external address
currently holding the most of it. Rates are averaged over the last five seconds
rather than the whole run, so the figures say what the network is doing rather
than what it has done. `peak` is the same floor the summary reports as the
minimum link speed: a rate the link certainly reached, not an estimate of what
was asked of it.

The second row is the run: whether anything is paused or filtered, which
versions the exporters are speaking and how many templates have been learned,
where the size colour scale is topped out, whether anything is going wrong, the
leading protocol and service, and how names are being looked up and how that is
going.

The middle field of that row is the one to watch. It reads `ok` while nothing
is wrong and turns into the count of whatever is: export gaps, data sets that
arrived before their template, malformed datagrams, name lookups dropped from a
full queue, sampling in force. They share the one field rather than each taking
their own, which is what keeps the two rows the same length. Four things going
wrong at once would otherwise push the run row four fields past the wire row.

Within a field the figures are drawn plainly and everything wrapped around
them is dimmed: `48.0` carries where the `M` after it recedes, `78` where the
`TCP` before it does. It is the same hue either way, so the palette still means
what it meant, red for loss and green for running, and it is only the weight
that separates the number being read from the unit naming it. A field
with no figures in it at all, `live` or `ok`, is left alone; there is nothing
there to tell apart. Terminals that ignore the dim attribute simply show the
field as it was before.

### Reading down as well as across

Field *n* of the wire row and field *n* of the run row begin in the same column,
so the bar is a grid rather than two independent lines and the eye can drop
straight down it. Seven fields on each row, which is why the run row is ordered
as it is: the wire row runs from a short runtime to a long address, so the run
row is ordered narrowest first to match, pairing wide with wide. Ordered any
other way, `names all/short 412 found 88 missed` would sit under `up 04:12` and
leave twenty-odd blank columns after it.

A column is as wide as the wider of the two fields sharing it, and that is what
the alignment costs. The rows no longer each spend the window on their own
contents, so between them they run out of room sooner than either would alone.
The full set of fourteen fields needs about 150 columns. Below that the bar
shortens, and the last column belongs to whichever row holds the wider field in
it, so the other row stops short of the right margin.

Neither row ever wraps. When the window is too narrow to hold everything, whole
fields are dropped rather than a line being cut mid-figure, and what goes first
is whatever matters least: the peak rate, then the leading service, then the
exporter versions. Anything reporting trouble outranks everything merely
describing the run, so a narrow window will show you `gaps 2` long after it has
given up on `TCP 78%`. The rows are dropped from independently, so below about
150 columns they will not always hold the same number of fields; the columns
they do have still line up.

The bar redraws twice a second at most, however busy the network is, and it is
wiped and the margins released before the summary is printed, so the report
lands on a screen that nothing is still holding on to.

Starting it scrolls the window clear rather than erasing it. Something has to
make room: setting a scroll region puts the cursor back on the top row, and
printing flows over whatever was already there would be worse than either
alternative. So the bar pushes a windowful of blank lines up first, and what
you had on screen goes into the scrollback with everything else instead of
being wiped, the same courtesy the bottom margin was chosen to preserve.

### What it costs, and what it does not

The bar reserves its two rows with a scroll region, the same VT100 mechanism
behind `--sticky-header`, but it sets only the bottom margin and leaves the top
of the window alone. That difference is the whole point. A terminal decides a
line is worth keeping when the line leaves the top of the screen, and with a
bottom margin lines still leave the top of the screen exactly as they always
did. Most terminals go on feeding scrollback as usual:

| Scrollback kept | Scrollback discarded |
| --- | --- |
| xterm, GNOME Terminal, Konsole, rxvt, Apple Terminal | kitty, iTerm2 |

This is the same technique `apt` uses to keep a progress bar below its output,
and the terminals in the right-hand column are where people notice apt's output
missing from their history ([kitty#3113][kitty]).

**Windows Terminal is untested.** It is not in either column above because
nobody has checked, and guessing would be worse than saying so. Ten seconds
settles it, in a plain terminal window rather than through a pager or a tool
that captures output:

```powershell
"$([char]27)[1;10r"; 1..30 | % { "line $_" }; "$([char]27)[r"
```

Scroll up. If `line 1` through `line 20` are in the history, your terminal keeps
scrollback under a bottom margin and the bar costs you nothing. If they are
gone, it belongs in the right-hand column, and `--hide-status` is how you get
your history back.

[kitty]: https://github.com/kovidgoyal/kitty/issues/3113

### With the header pinned as well

`--sticky-header` and the status bar compose, and turning both on gives you a
window with a header nailed to the top row, flows scrolling in the middle, and
the bar along the bottom. A scroll region is a single pair of margins rather
than two independent settings, so the two features do not each claim one: the
header writes one region covering both reservations, and the bar draws inside
what it was given.

Scrollback is lost in that combination, because the top margin is set. That is
already the price of `--sticky-header`, and the bar does not add to it.
On a window with fewer than eight rows the header stands down and says so,
leaving the bar and `--header-every` to carry on.

### When it does not appear

Under `--json`, redirected to a file or a pipe, on a terminal that will not
take a scroll region, or in a window shorter than six rows, the bar never
starts and nothing is said about it. Unlike `--sticky-header`, which prints a
notice when it cannot do what you asked for, the bar was never asked for, so
its absence is not news.

---

## Keyboard controls

While the collector is running it also takes single keypresses. Nothing has to
be enabled: if stdout is a terminal and `--json` is off, the keys are live and
one line under the startup banner says so:

```
keys: the collector takes single keypresses; press ? to list them
```

That is the whole of it. The line used to name all sixteen keys, which ran to
two hundred characters and wrapped on any ordinary terminal, and it scrolled
away with the banner regardless, so the reader who wanted it an hour later was
no better off for its having been thorough. `?` answers that reader instead,
whenever they ask. What the line still has to say for itself is that there are
keys at all: someone who does not already know the program answers the
keyboard has no reason to press anything, `?` included.

| Key | What it does |
| --- | --- |
| `esc` | Close the program. The exit summary prints as it would on `Ctrl-C` |
| `space` | Pause and resume printing. Flows still arrive and are still counted while paused; they queue up and print when you resume |
| `x` | Clear the screen. While paused it also throws away the queue |
| `s` | Print the traffic summary now, without stopping |
| `l` | List the local addresses seen this session and the names they answered to |
| `c` | Clear the collected statistics and restart the runtime clock |
| `b` | Hide the status bar, or bring it back. See [The status bar](#the-status-bar) |
| `d` | Toggle re-ranging of the size colour scale |
| `m` | Ask for a new fixed top for the size colour scale, and switch to it |
| `h` | Cycle host name resolution: off, dns, all |
| `n` | Show a host by its name in place of its address, where a name is known |
| `p` | Show hardware (MAC) addresses on a line under the flow |
| `f` | Toggle full domain names |
| `e` | Toggle showing only flows with a public endpoint |
| `q` | Print a QR code for the `--web` URL, with the URL under it. See [The web interface](#the-web-interface) |
| `?` | List every key and what it does, without stopping |

Every key prints one line saying what changed, so there is no guessing about
which mode you are now in. The exceptions are `s`, `l` and `?`, whose output is
its own confirmation and needs nothing said on top of it. What `s` prints is
exactly the report the program prints on the way out, as a snapshot of the
moment it was asked for, and the collector keeps running. Press it as often as
you like; press `c` first if you want the next one to cover only what happens
from now on.

`b` gives the two bottom rows back to the flows and takes them again on the next
press. Turning it off releases the scroll region; turning it back on scrolls up
only the two rows it is about to cover, so nothing already on screen is painted
over. In a window with no room for both the bar and a pinned `--sticky-header`,
the header keeps it and `b` says so rather than displacing what you asked for.

`n` swaps the address for the name, rather than printing the name after it:
`192.168.1.42:51234 (macbook-pro)` becomes `macbook-pro:51234`. A host that has
answered to nothing keeps its address, so a column under `n` is a mixture of
the two, and deliberately so: hiding the machines nothing is known about
behind a blank would hide exactly the ones worth noticing. It only affects rows
printed from then on, the same bargain `h` and `f` strike, and it says so if
nothing is being looked up at all.

`p` puts the two hardware addresses on a second line, sitting directly under
the addresses they belong to. Only exporters that send the MAC elements have
anything to show: NetFlow v5 has no field for them, and plenty of v9 exporters
leave them out. Where a flow carries none the line is not printed at all rather
than printed empty, so turning `p` on costs nothing on an exporter that cannot
answer. Where only one end is known the other reads `-`.

`l` lists every local address that has answered to a name at any point in the
session, not what happens to be cached, since names expire and the cache
evicts, and the useful question hours later is still "what did you see". Where
an address has answered to more than one name they share a row, most recent
first, with the superseded ones dimmed:

```
Local hosts seen
  192.168.1.5        tv
  192.168.1.20       nas  nas-old
```

A reader without colour is given a trailing `*` on a superseded name instead,
and a footer saying what the star means. That is decided per reader rather
than per run: a terminal with `--no-color` gets the stars while a browser
watching the same collector is still shown the dimmed form. The list holds
5000 addresses and five names each; past that the least recently seen are
dropped.

`?` prints the whole list, a key and a sentence a line, and changes nothing:

```
Keyboard controls
  space  pause and resume printing, holding flows meanwhile
      x  clear the screen, and the held flows with it while paused
      s  print the traffic summary now, without stopping
      l  list the local addresses seen, and their names
      c  clear the statistics and restart the runtime clock
      b  hide the status bar at the foot of the window, or bring it back
      d  re-range the size colour scale as flows arrive, or pin it
      m  ask for a new fixed top for the size colour scale
      h  cycle host name resolution: off, dns, all
      n  show a host by its name in place of its address
      p  show hardware addresses on a line under each flow
      f  show full domain names instead of the first label
      e  show only flows with a public endpoint, or show all
      q  print a QR code for the web interface URL, and the URL under it
      ?  this list
    esc  close the program, printing the exit summary
```

The listing and the dispatch that runs the keys are built from one table in
`keys.py`, and the suite holds the two to each other. A key that works and is
listed nowhere is as much a defect as one that is listed and does nothing, and
neither is the sort of thing anyone notices until they go looking for a key
that is not there. The key `?` itself is a constant that the table, the
dispatch and the reminder line all take it from, so the line cannot come to
point at a key that is not the one that answers.

Like `s` and `l` it goes to stderr, so a run with stdout redirected into a file
answers on the terminal where the question was asked rather than dropping a
listing into the middle of the flows.

### What the keys do not do

`space` holds at most 2000 flows. Past that the oldest are dropped and the count
is reported when you resume. Pausing is for reading what is on screen, not for
recording. Decoding never stops, so the summary and the top talkers table are
unaffected by how long you pause.

`c` clears the datagram, flow, template and export-gap counters, the top talkers
table, the name resolution counters, and the runtime clock. It deliberately
leaves the learned templates alone, along with the sequence positions each
exporter has reached, and the sampling rates, which are facts about the
exporter rather than statistics. Resetting the sequence positions would make
every exporter look as though it had restarted.

`d` honours whatever `--size-scale-window` was set to, so re-ranging is over the
last N flows if a window was given and over the whole run otherwise. Turning it
off returns the scale to its previous fixed top rather than to the default. `m`
accepts the same `K`, `M`, `G` suffixes as `--size-scale-max`; escape cancels,
and anything unparseable leaves the scale as it was.

`h` and `f` both affect what gets looked up from that moment on. Switching to
`dns` or `all` from `off` starts the lookup threads, which a collector started
with `--resolve off` does not have. Toggling `f` empties the name cache, because
every name in it was already shortened or not on the way in.

Names appear as new flows arrive rather than all at once: rows already on screen
are not revisited, and an address that failed to resolve during an earlier `dns`
period stays uncached-as-missing for its 300 second negative TTL, so it may take
that long to try again. Switching back to `off` stops new lookups but leaves the
worker threads idling; they are daemon threads and end with the program.

### When the keys are off

Keys need a terminal on stdin. Under `--json`, redirected into a file, or run
from systemd, key handling never starts and the collector behaves exactly as it
did before, with no reminder line and no terminal mode changes. The terminal is put
into cbreak mode, not raw, so `Ctrl-C` still interrupts, and it is restored on
exit alongside the scroll region the status bar and the sticky header share.

One consequence worth knowing: with keys live the socket wait drops from one
second to a quarter, so that a keypress on a silent network is answered
promptly rather than up to a second later.

---

## The web interface

`--web` mirrors the display into a browser. It is off unless you ask for it,
it binds loopback, and the URL it prints carries a token that nothing else
knows.

```bash
nettail --web
```

```
Listening for NetFlow/IPFIX on 0.0.0.0:2055
Hostname resolution: reverse DNS, mDNS, NetBIOS (sends probes to the LAN)
v9 and IPFIX exporters resend templates periodically. Data records before the
first template are counted as deferred.
Web interface: http://127.0.0.1:2056/t/QoYm2ZP4rD8xN1sVbTgKcW7eL9uHjX3f/
press q for a QR code of that URL
keys: the collector takes single keypresses; press ? to list them
```

Open that URL and you get the same flows, in the same colours, with the same
keys. The terminal keeps working throughout; this is a second view of one
collector rather than a second collector.

### Getting there from a phone

The URL carries a token, so it is not the sort of thing anyone wants to copy
off a screen by hand. Press `q` and the collector draws it as a QR code, with
the URL printed underneath in case the code is no use to you:

```
Web interface
█████████████████████████████████████████
█████████████████████████████████████████
████ ▄▄▄▄▄ █▄▀▀▄▄██▄ ▀  ██ ███ ▄▄▄▄▄ ████
████ █   █ ███▄█ ██▄ ▀██  ▀▀▄█ █   █ ████
████ █▄▄▄█ ██▄▀▄▀█▄  ▄ ▄█   ██ █▄▄▄█ ████
████▄▄▄▄▄▄▄█ █ ▀▄▀▄▀ █▄▀▄█▄▀▄█▄▄▄▄▄▄▄████
████  ▀  ▀▄▀▀ ▄▄▀ ██ ██▄▄ █ ▄▀▄█▀██▀▄████
████▄  █▄█▄▄▄▀  ▀ █ ▄▀█▀▄█▀ ▀▀▄ ▄ ▀█▄████
████ █▄▄ ▄▄ ▀▄█▄▄ ▀▀▄█▄▄▄▄▀▄█▄▄▄█ █▄▄████
████▄▄▀▀ ▀▄█▀██ ▄▄█ ▄▄█ █▄ ▀█▄▄▄ ▄▀█▀████
████ █▄█▄▀▄▀▄  ▀▀▀  █▀▀█▀███ ▀ █ ▄█  ████
████▄▄▄▀ ▄▄▄███▄▀█▀ █▄█▄▀▄█▄▄ ▄ ▀█▀▀ ████
████ ▀▄ ▀█▄ ▀█▄█▄▄  ▀▀▀ ▄██▀█ █▄▀▄▀▀█████
████ █    ▄▀▄ ██▄█ ▄▀ ▀███▀▄██▄▄▀▀ ▄▄████
████▄▄▄███▄▄ ▀ █▀▄▄█▀▀▄▄▀▄██ ▄▄▄ █▀ █████
████ ▄▄▄▄▄ ██  █▀▀   █▄ █▄▀▄ █▄█ █▀▀▀████
████ █   █ █ ▀█ ▄██▀█▀█▄ ▀█ ▄▄▄ ▄█▀▀▀████
████ █▄▄▄█ █▀▄ ▀▄ ▄▄▄▄█  ▄▄▀▄█▀▄ █▀  ████
████▄▄▄▄▄▄▄█▄▄▄▄█▄██▄███▄███▄████████████
█████████████████████████████████████████
█████████████████████████████████████████
http://127.0.0.1:2056/t/QoYm2ZP4rD8xN1sVbTgKcW7eL9uHjX3f/
```

The symbol is drawn out of half block characters, two rows of it to each row
of text, because a QR code is square and a terminal cell is not. Dark modules
come out as the window's background, which is right on a dark terminal and
inverted on a light one; scanners have coped with an inverted symbol for
years, and there is no way for a program to ask what colour the window is.

The symbol is 41 columns wide and 21 rows tall. With the heading over it and
the URL under it that is 24 rows in a window wide enough to print the URL on
one line, and more in a narrower one, where the URL wraps and is counted at
the rows it really takes. What it is measured against is the space between the
pinned header and the status bar rather than the whole window, and the window
it measures is the one this block is going to, which is stderr and need not be
the one the flows are going to. Anything smaller, or a stderr that is not a
window at all, gets the URL by itself: a code that has wrapped or scrolled is
not a degraded code, it is an unreadable one, and the URL underneath was the
point of it anyway.

The key does not cross to the browser. Its answer is drawn for a terminal and
written only there, and what it encodes is the address of the page a browser
is already looking at.

The encoder is in this repository rather than being a dependency. That is a
trade about what a dependency costs here: this program installs two pure
Python packages and nothing else, its suite has no dependencies at all, and
the container image pins every byte it installs by hash. A QR code is a
standard that was fixed in 2015 and does not move, so the code that makes one
is written once and then left alone. It handles versions 1 to 5 at error
correction level L, which is a URL of up to 106 bytes, and anything longer
gets the plain URL.

### What it shows

Everything the terminal shows, arriving as it arrives:

- **Flow rows**, in a table with the column header pinned to the top. The
  endpoint columns are wider than a terminal's, so a long hostname that would
  have been trimmed to an ellipsis is shown whole.
- **The banner**, including for a browser that connects an hour in. It travels
  in the greeting each stream opens with rather than being printed once and
  missed.
- **Decoder notices**, the running commentary about sampling and lost exports.
- **The traffic summary and the host list** when the `s` and `l` keys print
  them, and the exit summary when the collector stops.
- **A status footer** carrying what the terminal's status bar carries.

### Keys and buttons

The keys live in a drawer, shut by default so the flows have the window. Open
it with **Keys** in the top bar and every control is there as a labelled
button, in a grid, with the key it mirrors beside it. Press the key itself and
it does the same thing. The drawer remembers whether you left it open.

A button that mirrors a setting shows whether that setting is on, and it reads
that off the collector rather than tracking it locally, so a key pressed at the
terminal or in another browser moves this one too.

The buttons are built from the collector's own key table, so a key added to the
program appears in the browser without the page being touched. Three keys are
treated differently.

`esc` closes the program, which would end it for everybody, this terminal
included. That is not something that should arrive as a side effect of
mirroring a keyboard, so it does not cross at all: stop the collector where you
started it.

`q` does not cross either, for a duller reason: there would be nothing for it
to do. It draws the URL of this page as a QR code, for a terminal, on the
terminal, and a browser showing that page has the address in its own bar
already.

`?` has no button, for the same reason: it prints the list of keys, and the
drawer is that list, labelled and already in front of you. The key itself still
works. Press it and the listing appears among the flows, as it does at the
terminal, showing the keys a browser can press.

The `m` key asks for a value, which at a terminal means typing a line. In a
browser it prompts and sends the answer along with the key.

### While the tab is in the background

A tab you switch away from gives up its connection after about fifteen seconds,
and takes it back when you return. This is not tidiness. A hidden tab is
throttled and may be frozen outright, and while it is, the browser goes on
reading the connection and buffering what arrives with nothing running to
consume it. On a busy link that buffer grows until the tab is killed for
memory, which happens while you are not looking at it.

The delay before disconnecting means switching away and straight back costs
nothing. While the tab is parked the collector gets its watcher slot back.

Switching tabs is the easy case to notice and the mildest one. Minimising the
whole window is worse, for two reasons. A window that is not on screen can be
starved of time without ever being marked hidden, and the buffer is itself what
puts the tab under memory pressure, so the browser freezes it, and a frozen tab
no longer runs the timer that would have closed the connection. The buffering
that caused the freeze then outlives it.

So the page watches three things rather than one. It gives the connection up
when the tab is marked hidden, when the browser says it is about to freeze it,
and when its own clock shows it has not been run for ten seconds. Any of the
three is enough on its own, and whichever noticed, coming back works the same
way and reports the same count.

On return the page says how many flows went past:

```
12,431 flows arrived while this tab was in the background. The collector has
them in its totals; they were not kept for the page.
```

That figure is the collector's own count of flows that passed the display
filter, asked for on reconnection rather than counted by the page, which by
definition saw none of them. The totals in the status bar and the traffic
summary are unaffected: nothing was missed by the collector, only by the view.

### Following the tail

**Follow**, beside the connection indicator, decides whether new flows pull the
view down with them. It is on to start with. Scroll up and it clears itself, so
you can read something without wrestling the page for it; scroll back to the
bottom and it fills again.

If it was on when you switched away from the tab, returning puts you back at
the bottom straight away rather than leaving you where you were until the next
flow arrives to carry you there.

`--web-readonly` serves the display and accepts nothing back, which is the
setting for a session left up on a machine other people use.

### What it is safe to do with

What this serves is a live map of which machines on your network talked to
which, with hostnames attached, and the control route can switch hostname
resolution to active mDNS and NetBIOS probing. Treat the URL as a password.

- It binds `127.0.0.1`. Only this machine can reach it.
- The URL carries a random token. Without it every request is a 404.
- The `Host` header is checked on every request, which is what stops a web page
  you happen to have open from reaching it by rebinding a name to `127.0.0.1`.
  Under this bind the view answers to its address, to `localhost`, and to no
  other name unless `--web-host` gave it.
- The page loads nothing from anywhere and is served under a content security
  policy that names the hashes of its own script and style.
- Hostnames off the wire are shown as text and never as markup, which matters
  because a hostname is whatever some machine on your network answered.

`--web-bind` will put it on another address, and the collector says plainly
what that means when you do:

```
the web interface is bound to 0.0.0.0, not to loopback. Anyone who can reach
that address and guess nothing worse than the token can read which machines on
this network talked to which, and the hostnames behind them. This is plain
HTTP, so the token in the URL travels in the clear and so does everything it
fetches.
```

Put it on another address and it answers to any name, so opening it from the
next machine by this one's name works as you would expect. The `Host` check
keeps checking the port and stops comparing names, because rebinding is an
attack on what only loopback can reach: on a LAN address the view is reachable
directly and the token is what guards it. Jupyter, Syncthing and Ollama each
settled on the same rule. `--web-host` then narrows it, to the names given and
the address a connection arrived on:

```
nettail --web --web-bind 0.0.0.0 --web-host z2m
```

Under the loopback default the same flag adds a name beside `localhost`. It
may be repeated, and the URL printed at startup uses the first one. Under a
wildcard bind with no name the printed URL says `127.0.0.1`, and the banner
says to put this machine's address or name in its place from elsewhere.

There is no TLS and no login. If you want it reachable from elsewhere, put it
behind something that provides both, and bind it to loopback so that thing is
the only way in.

### With `--json`

The two work together, and it is a genuinely useful pairing:

```bash
nettail --json --web > flows.jsonl
```

stdout stays machine-readable while a browser gets the human view. Two things
follow from that.

`--json` turns the local keyboard off, so the browser becomes the only place
keys can be pressed. And `pause` holds the browser view only: stdout is the
part of the interface meant to be parsed, so it keeps flowing rather than
gaining holds and drops that a consumer would have to cope with.

Colour needs no flag there. `--colour` is about this terminal, and a
redirected stdout still turns it off, but the browser has its own switch and
it is on: a reader with a browser open is a colour-capable one whatever became
of stdout. `--web-colour off` is how a run says otherwise.

### Limits

- Four browsers at once. Each holds a connection open for as long as its tab
  is, and the cap is what stops that being unbounded.
- A browser that falls a long way behind has the oldest events dropped and is
  told how many, rather than being shown a gap that looks like continuity.
- The page keeps the last few thousand rows and trims the rest, saying so when
  it does. A tab left open on a busy link would otherwise become unusable.
- IPv4 only, matching the collector socket.

---

## The traffic summary

Runtime is reported as `hh:mm:ss`, and the hours are not wrapped at a day: a
collector left running for a week says `170:24:03` rather than starting again
at midnight. The status bar quotes the same clock in its `up` field, without
the leading hours until there are some.

Printed on the way out, and on demand with the `s` key. It describes every flow
that was decoded, whether or not it was displayed, so `--external-only` narrows
the screen without narrowing the report.

```
Summary
  runtime            01:00:00
  datagrams received 61
  bytes received     40.0K
  flows decoded      6
  templates learned  3
  option records     2

Protocols
                      bytes    flows    packets
  TCP                 26.1M        4      21.5k
  UDP                  9.6K        2         82

Services
                      bytes    flows
  445/microsoft-ds    20.0M        1
  443/https            5.2M        2
  22/ssh             878.9K        1
  53/domain            9.6K        2

Busiest 5 pairs by volume
  192.168.1.13 <-> 192.168.1.20                                  20.0M
  192.168.1.10 (laptop) <-> 93.184.216.34                         5.2M
  140.82.121.4 <-> 192.168.1.12 (buildbox)                      878.9K

Busiest 5 pairs by packets
  140.82.121.4 <-> 192.168.1.12 (buildbox)                       17.0k
  192.168.1.10 (laptop) <-> 93.184.216.34                         4.1k
  192.168.1.13 <-> 192.168.1.20                                    400

Longest 5 flows
    1h00m  TCP    192.168.1.12:51004 (buildbox) -> 140.82.121.4:22            878.9K
     8.0s  TCP    192.168.1.13:51005 -> 192.168.1.20:445                       20.0M
     3.0s  TCP    192.168.1.10:51000 (laptop) -> 93.184.216.34:443              5.1M

External traffic
  total              6.1M
  inbound            0B
  outbound           6.1M
  flows              5
  minimum link speed 14.4 Mbps
  concurrent demand  14.6 Mbps  if every flow sent evenly

Name resolution
  names found        4 (dns 1, mdns 2, netbios 1)
  unresolved         2

Top external addresses by bytes
  93.184.216.34                                          5.2M
  140.82.121.4                                         878.9K
  9.9.9.9                                                4.9K
```

**Protocols and services.** A flow is filed under whichever of its two ports has
a name, destination first, since that is the one being connected to in the
ordinary case. The number is always shown even when the name is known, as in
`443/https` or `53/domain`, because a name is a convention and the number is the
fact, and the number is what you reach for when writing a firewall rule or
searching a capture. Two ephemeral ports have no name between them, so those
read `51002/tcp`, and a flow with no ports at all is filed under its protocol.

**Pairs.** Every table that names an address shows its hostname beside it where
one is known, so `192.168.1.10 (laptop) <-> 93.184.216.34`. A row too long for
its column is trimmed with `...` rather than allowed to wrap.

Direction is collapsed: a conversation is one row whichever end
opened it. Both tables are over every flow, internal ones included, which is
usually what you want when the question is "what is talking to what".

Only the busiest handful is ever reported, so the tables do not hold every
conversation a long run has ever seen: past 50,000 distinct pairs, addresses or
services the rarest are dropped and the report says how many. What survives is
whatever ranks highest by either measure, so the rows shown are unaffected.

**Longest flows** are ranked by the duration the exporter reported. A flow with
no usable start and end is not in the running, which mostly means v5 exporters
with a wrapped uptime counter.

**External traffic** means every flow with at least one public endpoint, the
same definition `--external-only` uses, down to the same code: the display, the
filter and the summary all ask one helper where a flow's two ends are, so an
exporter that reports only post-NAT addresses is treated the same way by all
three. Inbound is what arrived from a public address and outbound is what left
for one; a flow between two public addresses is counted in both directions
rather than assigned to one.

### Minimum link speed, and concurrent demand

The last two lines are the interesting ones, and the easiest to misread. They
answer different questions and only one of them is a bound.

**Minimum link speed** is a floor in the strict sense: the link cannot have been
slower. Two things are true without assuming anything about how a flow spread
its bytes. First, a flow delivered all of its bytes inside its own lifetime, so
the link reached at least that flow's average at some instant within it. Second,
flows that began *and* ended inside the same second delivered all of their bytes
during that second, so those add up. The larger of the two is reported.

**Concurrent demand** is an estimate, not a bound. Every external flow is
spread evenly across its own lifetime and the rates are laid on a timeline; the
tallest point is the answer. It describes the shape of the traffic and is
usually the more interesting number, but it cannot be claimed as a minimum:
two flows whose lifetimes overlap need not have been sending at the same
instant. A flow of 1000 bytes across `[0s, 10s]` and another across `[9s, 19s]`
look like 1.6 kbps of overlap, yet each could have been sent in its own nine
second half, needing only 889 bps and never overlapping at all.

Neither number is a speed test. Real traffic is burstier than an even spread, so
the true peak is higher than both, often much higher for short flows. A quiet
network produces small numbers because nothing demanded more, not because the
link cannot do more.

Flows with no duration contribute to neither, since they say nothing about how
long anything took; if none of the flows were timed, the line says so rather
than claiming zero. On a very busy run the concurrent estimate is built from the
first 50,000 timed external flows and says so. The floor is unaffected by that
cap.

### Colour

Nothing in the report is left in the terminal's plain text, because a wall of
white is the hardest thing on the page to read. Every element says what it is:

| Element | Colour |
| --- | --- |
| Section headings | bold blue |
| Labels, column headers, notes, punctuation | grey |
| Counts: flows, packets, datagrams, durations | cyan |
| Byte figures | the size ramp, see below |
| A public address | cyan |
| An address on this network | blue |
| The arrow between two addresses | magenta |
| A hostname | green |
| A port number | cyan, its service name green |
| Protocols | the colours they have in the flow display |
| Warnings and losses | yellow and red, as elsewhere |

The addresses follow the distinction the flow display already draws, so a row
tells you at a glance which end of a conversation is out on the internet. The
arrow between them is punctuation rather than data, and gets a colour of its
own so it does not read as part of either address.

Byte figures are painted along the same cool-to-hot ramp the BYTES column uses,
but ranged differently. The flow display needs a scale that means the same thing
from one line to the next, so it uses a fixed or slowly moving top. A report is
read all at once, and the useful question is which of these figures is large
compared with the others in front of you, so the ramp is stretched over exactly
the values being printed, smallest to largest, and a figure's colour places it
among its neighbours rather than against any absolute idea of large. Only the
rows actually shown are ranged over, so the smallest figure on screen is the one
at the cold end even where rarer ones were counted but not printed.

Nothing is always the cold end: a zero sits at the bottom of the ramp, and a
report whose every figure is zero is painted entirely cold, which is what a run
that carried nothing should look like. Equal figures share the middle of the
ramp only when there is something there to be equal about. Counts of flows and
packets are left alone; the ramp is about volume.

On a terminal with `--no-color`, with `NO_COLOR` set, or with stdout
redirected, the report arrives with no escapes at all. A browser watching the
same run still gets the coloured one, because the colour is taken out on the
way to the reader that refused it rather than never painted.

---

## Output format

```
TIME         EXPORTER        PROTO  SOURCE                                     DESTINATION                                 PKTS    BYTES     DUR  FLAGS
13:40:03.000 10.0.0.1        TCP    192.168.1.42:51234 (macbook-pro)         ↑ 140.82.114.4:443/https (github)               23     4.1K   4.90s  ...AP.SF
13:40:03.000 10.0.0.1        UDP    192.168.1.77:5353/mdns (hue)             ⇄ 224.0.0.251:5353/mdns                          2     180B   4.90s
13:39:56.103 10.0.0.1        TCP    10.0.1.5:44321 (nas)                     ↑ 104.244.42.1:443/https (twitter-edge)        412    57.5K  12.50s  ...AP...
13:40:08.453 10.0.0.1        TCP    1.1.1.1:853/domain-s                     ↓ 10.0.1.5:39012 (nas)                           4     320B   0.15s  ......S.
```

| Column | Meaning |
| --- | --- |
| `TIME` | Flow **start** time in local time, to the millisecond. Not the time the record was received |
| `EXPORTER` | Source address of the device that sent the flow record |
| `PROTO` | IP protocol name, or the raw number for anything uncommon |
| `SOURCE` / `DESTINATION` | `ip:port/service (hostname)`, e.g. `104.18.32.7:443/https (cloudflare)`. The well known service name follows the port it describes; parentheses always hold the resolved hostname. Either annotation is omitted when unknown, and the service is dropped before the hostname when the column is tight. The `n` key shows the name in place of the address instead |
| (unnamed) | Which way the flow crossed the boundary: `↓` in from the internet, `↑` out to it, `⇄` between two addresses on this network. See below |
| `PKTS` | Packet count, abbreviated above 1000 |
| `BYTES` | Byte count, abbreviated with binary units |
| `DUR` | Flow duration in seconds |
| `FLAGS` | Union of TCP flags seen during the flow. Blank for non-TCP |

### Which way it went

The unnamed column between the two endpoints says which side of the router each
end sat on, which is usually the first thing worth knowing about a row.

| Arrow | Meaning |
| --- | --- |
| `↓` | A public address to a local one: something arriving from the internet |
| `↑` | A local address to a public one: something leaving for the internet |
| `⇄` | Both ends local: a conversation that never left the network |
| blank | Neither, or not enough to say: two public addresses, or an end that could not be read |

This is the way round your router's own dashboard draws it, where a download
points down, and the way round a network diagram is drawn, with the internet
above and everything here below it. Read the arrow as pointing at where the
flow went: `↑` is something heading out, `↓` is something coming down off the
internet to a machine here.

Multicast and link-local addresses count as local. A flow to `224.0.0.251` never
went near the internet whatever else is true of the address, and the arrow is
only ever about which side of the boundary each end was on. Anything that fits
none of the three gets a blank rather than a guess: an arrow pointing the wrong
way would be worse than no arrow.

Both internet arrows are drawn in the same colour as a public address elsewhere
in the display, and the local one is dimmed, so a screen of `⇄` stays quiet and
an `↑` or `↓` stands out of it.

The local mark is a stacked pair rather than a single `↔` for a practical
reason. One left-right arrow has to fit two heads and a shaft across a single
cell, and a console that gives it no more width than a letter renders it as a
smudge. Stacking two arrows spreads the same detail down the cell instead,
where there is room, which is why `↑` and `↓` read cleanly at any width, and
why `⇄` does too.

### Service names

The name after a port, `443/https` and `5353/mdns`, comes from the system
services database: `/etc/services` on Linux and macOS, `services` under
`System32\drivers\etc` on Windows. Those files are not the same from one
machine to the next. A Linux box names port 5353 and a Windows box does not, so
the same capture reads `5353/mdns` on one and a bare `5353` on the other, which
is a poor thing for a column whose whole job is to say what a port is.

So a short list ships beside the program, in `nettail/supplemental-services`,
and is consulted when the system database has nothing to say and only then. A
machine that already names a port keeps its own answer, and the list can only
ever fill a gap. It holds mDNS, LLMNR, SSDP, DNS over TLS, CoAP, MQTT and the
NetBIOS trio, in the format `/etc/services` uses:

```
mdns            5353/udp     # multicast DNS, what Apple and Linux answer to
llmnr           5355/udp     # link-local name resolution, Windows' own
```

Edit it to add your own. A line that cannot be read is skipped rather than
treated as an error, so a typo costs one name and not the whole file. Ports at
or above 49152 are never looked up in it, for the same reason the system
database is not asked about them: that range is ephemeral, and a name there
would describe whichever port the kernel handed a client rather than a service.

`--no-supplemental-services` ignores the list entirely and leaves the system
database as the only source. If the file has been deleted or cannot be read,
the collector says so once at startup and carries on with the system database,
which costs a handful of ports their names and nothing else.

A file that opens but yields no entries at all is reported the same way, and is
the case the message really exists for: one unreadable line goes quietly, but a
file where every line fails that way has lost the whole list while looking from
the outside exactly like one that worked. Saving it as UTF-16 rather than UTF-8
does that to every line at once, so save it as UTF-8.

### TCP flag string

Eight fixed positions, high bit to low bit. A dot means the flag was not set.

```
C E U A P R S F
│ │ │ │ │ │ │ └── FIN
│ │ │ │ │ │ └──── SYN
│ │ │ │ │ └────── RST
│ │ │ │ └──────── PSH
│ │ │ └────────── ACK
│ │ └──────────── URG
│ └────────────── ECE
└──────────────── CWR
```

`...AP.SF` is a complete, normally closed connection. `......S.` on its own with a
low packet count is a connection attempt that never completed, which is what a port
scan or a dead C2 server looks like.

### Colour

| Element | Colour |
| --- | --- |
| Destination is a public IP | cyan |
| Destination is private | dim |
| Destination is multicast | grey |
| TCP / UDP / ICMP | green / yellow / magenta |
| Flow size (BYTES column) | muted blue through green to dusty red, see below |

### Size colour scale

The BYTES column is tinted along a cool-to-hot ramp so flow size reads at a
glance: slate blue for the smallest flows, through teal and green, into khaki,
sand and a dusty red at the top of the scale.

Every step is picked from the muted part of the 256-colour cube, none at full
saturation, none with a channel turned off or pushed to the top. The rest of
the output is drawn in the terminal's own sixteen colours, which take their
tone from whatever theme is in use, and a ramp of primaries beside them looks
like something that wandered in from another program. These carry the same
weight: enough separation to rank a column at a glance, without any one figure
shouting. The same ramp does both jobs, so the live display and the report
agree.

Byte counts span several orders of magnitude, so position on the ramp is
**logarithmic**: each step is a constant multiple of bytes, not a constant
number of them. The bottom of the ramp is fixed at 64 bytes, below the size of
a bare ACK, so the interesting range is not squashed into the top few colours.
Anything at or above the top of the scale is red; nothing overflows.

With the default 100K top:

| Flow size | Colour |
| --- | --- |
| up to 64B | blue |
| 256B | sky blue |
| 1K | teal |
| 1.5K | green |
| 4K | lime |
| 10K | yellow |
| 32K | orange |
| 64K | orange-red |
| 100K and up | red |

Two ways to set the top of the scale, and they cannot be combined:

- `--size-scale-max BYTES` moves the fixed top. Use this when you want colours
  to mean the same thing across runs, or when you know roughly what a large
  flow looks like on your network. `--size-scale-max 1M` spreads the ramp over
  a wider range and leaves everyday flows cooler.
- `--size-scale-dynamic` re-ranges the scale as it runs: the largest flow
  seen so far always lands on red, and everything else is coloured
  relative to it. Every decoded flow counts, including ones hidden by
  `--external-only`. Colours therefore shift over the life of the run, and the
  same size may be a different colour before and after a big flow arrives. The
  scale never shrinks, and it starts at 4K so a capture of nothing but tiny
  flows does not run hot immediately.

#### Scoping the dynamic scale to recent flows

A run-long dynamic scale has one weakness: a single large transfer pins the top
of the ramp for the rest of the session, and everything after it stays cool
even when the traffic picks up again. `--size-scale-window N` fixes that by
ranging the scale over only the last N flows:

```
nettail --size-scale-window 500
```

The top of the scale is then the largest flow among the most recent 500, and it
falls again once that flow ages out, so the colours track what the traffic is
doing now rather than what it did an hour ago. Every decoded flow counts toward
the window, including ones `--external-only` hides. The 4K minimum still
applies, so a quiet stretch does not paint tiny flows red.

Pick N for the timescale you care about: a few hundred flows on a busy link is
a short recent-history window, while several thousand behaves closer to
run-long. `--size-scale-window` implies `--size-scale-dynamic`, so passing both
is allowed but redundant; passing it with `--size-scale-max` is an error, since
a fixed scale has nothing to re-range.

The window maximum is maintained in a monotonic deque, so the cost per flow is
constant regardless of how large N is.

Colour is dropped from this terminal under `--no-color`, when `NO_COLOR` is
set, or when stdout is not a TTY, and from the browser under
`--web-colour off`. The ramp uses 256-colour escapes, which every current
terminal supports, Windows Terminal included.

### Export gaps

Every export message carries a sequence counter, and a jump in it means messages
went missing on the way here. Without watching it the loss is silent: a flow that
never arrives looks exactly like a flow that never happened. Once an exporter's
counter has been read for long enough to be trusted (see below for when that
is), the exit summary reports either way:

```
  export gaps        none
```

or, when exports have gone astray:

```
Export gaps
  10.0.0.1           240 flow records never arrived
```

Neither line appears for an exporter whose counter could not be read
unambiguously, because nothing is being claimed about it in either direction.

An exporter can run several observation domains, and each is an independent
sequence space, so they are counted separately rather than added together. The
domain and version are named when an exporter has lost exports on more than one:

```
Export gaps
  10.0.0.1 v9 domain 7 40 data records never arrived
  10.0.0.1 v9 domain 0 2 export messages never arrived
```

The first gap for an exporter is also reported while running, since the useful time
to know is while it is still happening. Usual causes are a saturated link between
the router and the collector, or this collector not keeping up: the receive loop
is single threaded, and a socket buffer that fills drops datagrams silently.

What the counter counts differs by version: v5 counts flow records, IPFIX counts
data records, and v9 is *specified* to count export packets but is widely built to
count records instead. Rather than trust the version, each exporter is watched until
one reading lands exactly on the next message, and that becomes the rule for that
exporter. Until one does, nothing is reported. A wrong rule would invent a loss
on every message, and a collector that cries wolf about dropped flows is worse
than one that stays quiet. An exporter sending a single record per message is
ambiguous and is watched without ever being judged.

Repeated or reordered datagrams are counted separately and are not losses. So is a
counter restart, which is what an exporter reboot looks like.

### Timestamps

Flow start time is derived in this order of preference:

1. `flowStartMilliseconds` and friends (IPFIX elements 150 to 157), which are
   absolute epoch times
2. `first_switched` (v5 and v9), which is milliseconds since the exporter booted.
   The absolute time is reconstructed from the header's `sysUptime` and `unix_secs`

The reconstruction is rejected and the export time substituted if the result lands
more than a day from now, which guards against the uptime counter wrapping at 49.7
days.

---

## Hostname resolution

All lookups run in background worker threads behind a TTL cache. The packet receive
loop only ever reads the cache and never blocks. UDP has no backpressure, so anything
that stalls that loop silently drops flows.

**Practical consequence:** the first flow involving a new address prints a bare IP.
Later flows print the name once a worker has found it. Static mappings from `--hosts`
are the exception, since they need no network round trip and appear immediately.

### Sources, first hit wins

| Order | Source | Catches | Sends packets? |
| --- | --- | --- | --- |
| 1 | `--hosts` static file | Whatever you put in it | No |
| 2 | Reverse DNS (PTR) | Devices with DHCP hostnames registered by the UDM | Only to your resolver |
| 3 | mDNS reverse query to `224.0.0.251:5353` | Apple and Linux devices | Yes, to the LAN |
| 4 | NetBIOS node status, udp/137 | Windows machines, network printers | Yes, to the host |

The mDNS query sets the QU (unicast response) bit so the collector does not have to
join the multicast group. The NetBIOS parser picks the unique workstation name
(suffix `0x00` with the group bit clear) rather than the workgroup name.

### Choosing a mode

```bash
--resolve all     # PTR, then mDNS, then NetBIOS. Best hit rate. Default.
--resolve dns     # PTR only. Fully passive from the LAN's point of view.
--resolve off     # Nothing looked up. --hosts entries still apply.
```

`all` is the default because on most home networks PTR alone resolves almost nothing,
and a resolution feature that silently finds no names is worse than not having one.

That said, this is a security monitoring tool, and a collector that generates its own
traffic shows up in its own data. If that matters to you, use `--resolve dns` and put
the devices you care about in a `--hosts` file. That combination probes nothing and
is deterministic. The startup banner always states which mode is active.

### Static hosts file

Standard `/etc/hosts` syntax. First name on the line wins, aliases are ignored,
`#` comments are stripped.

```
# lan-hosts
192.168.1.1     udm-pro
192.168.1.42    macbook-pro.lan     mbp
192.168.1.77    living-room-appletv
192.168.10.20   ring-doorbell
10.0.1.5        nas.lan
```

```bash
nettail --hosts ./lan-hosts --hosts /etc/hosts
```

### Caching

| Result | TTL |
| --- | --- |
| Name found | 3600 s |
| No name found | 300 s |

The cache holds 50,000 addresses. When it is full the least recently used entry is
dropped, one at a time, and the total is reported as `cache evictions` in the exit
summary. Dropping the oldest matters: flushing the whole cache would send every
active host back through resolution at the same moment, which under `--resolve all`
means a burst of mDNS and NetBIOS probes onto the LAN.

The lookup queue holds 4096 pending addresses; overflow is counted as
`lookups dropped` in the exit summary rather than blocking.

---

## JSON output

`--json` writes one object per line to stdout, flushed immediately, suitable for
piping into another process.

```bash
nettail --json | ./ioc_match.py
nettail --json | jq -c 'select(.dst_port == 443)'
```

Field names follow the normalised names in the `IE` table, so v5, v9, and IPFIX all
produce the same keys where the underlying data is equivalent. Three metadata keys
are added with a leading underscore:

| Key | Meaning |
| --- | --- |
| `_exporter` | Source IP of the exporting device |
| `_version` | `5`, `9`, or `10` |
| `_timestamp` | Flow start as a unix float |

`src_host` and `dst_host` are present only when a hostname is known, so their absence
is normal and not a schema change.

Example:

```json
{"src_addr":"10.0.1.5","dst_addr":"104.244.42.1","src_port":44321,"dst_port":443,
 "proto":6,"tcp_flags":24,"packets":412,"octets":58900,
 "flow_start_ms":1787230327192,"flow_end_ms":1787230339692,"in_if":1,"out_if":2,
 "_exporter":"10.0.0.1","_version":10,"_timestamp":1787230327.192,"src_host":"nas"}
```

Unrecognised information elements are preserved rather than dropped. Standard
elements with no entry in the `IE` table appear as `ie<id>`, and enterprise-specific
elements appear as `e<enterprise>.<id>`.

---

## Installing it

`scripts/install.sh` sets nettail up either as a systemd service or as a Docker
container. It asks which, then asks for the ports and the resolver mode:

```
sudo scripts/install.sh
```

Every answer can be given as a flag instead, and `--non-interactive` refuses to
guess rather than hanging on a prompt nobody is there to answer:

```
sudo scripts/install.sh --systemd --non-interactive \
    --flow-port 2055 --web --web-port 2056 --resolve dns
```

Either way it generates a web token and keeps it in `/etc/nettail/nettail.env`,
mode 0640. That file is the whole of the web interface's authentication, and it
is why the installer is safe to run again: an upgrade keeps the token, so a URL
you bookmarked carries on working. The token is passed through the environment
rather than on the command line, so it does not appear in `ps` output.

The systemd install builds a virtual environment in `/opt/nettail` from
`requirements.lock`, verifying the hash of every file, creates a `nettail`
system user and group, and writes a hardened unit. The Docker install writes
`/etc/nettail/docker-compose.yml` using host networking, for the reasons in
[Running in Docker](#running-in-docker), and brings it up.

Neither puts the browser view on the network. `--web-bind` stays at its
loopback default in both, because that is a decision worth making deliberately
rather than one an installer should quietly make for you.

## Running as a service

`SIGTERM` is handled the same as `Ctrl-C`, so the exit summary still prints when
systemd stops the unit.

```ini
# /etc/systemd/system/nettail.service
[Unit]
Description=NetFlow collector
After=network-online.target

[Service]
Type=simple
User=netflow
ExecStart=/opt/netflow/venv/bin/nettail \
    --port 2055 \
    --resolve dns \
    --hosts /opt/netflow/lan-hosts \
    --json
StandardOutput=append:/var/log/netflow/flows.jsonl
StandardError=journal
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### With the web interface

A unit is the case `--web` was most worth building for: the flows go to a file
and there is no terminal to watch, so the browser becomes the only human view.

```ini
ExecStart=/opt/netflow/venv/bin/nettail \
    --port 2055 \
    --resolve dns \
    --hosts /opt/netflow/lan-hosts \
    --json \
    --web \
    --web-token ${NETTAIL_TOKEN}
```

Three things are worth knowing about that.

**Keep it on loopback.** The default bind is `127.0.0.1`, and on a server that
means reaching it through an SSH tunnel:

```bash
ssh -N -L 2056:127.0.0.1:2056 netflow-host
```

Then open the URL the unit logged, on your own machine. That gives you TLS and
authentication from SSH, which is more than `--web-bind` on its own will ever
give you.

**Pin the token.** A fresh random token every restart means a fresh URL every
restart. `--web-token` read from an environment file keeps one bookmark
working:

```ini
EnvironmentFile=/etc/netflow/nettail.env    # NETTAIL_TOKEN=...
```

Make that file readable only by the service user. The token is the whole of the
access control.

**Colour needs nothing.** Output is redirected here, so this unit's own
output has no colour in it, and the browser still does: the two are separate
switches and the browser's is on. `--web-colour off` turns it off if a plain
view is wanted.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now nettail
journalctl -u nettail -f
```

Rotate the output with logrotate using `copytruncate`, since the script holds the
file descriptor open.

---

## Running in Docker

An image is published on every release, to both
[GHCR](https://github.com/mjaksn/nettail/pkgs/container/nettail) and
[Docker Hub](https://hub.docker.com/r/mjaksn/nettail), for `linux/amd64`,
`linux/arm64` and `linux/arm/v7`.

The image is for the web interface. nettail is a console program first, and its
display wants a real terminal, so a detached container has nothing to show. The
browser view is the mode that works properly without one, and it is what the
image runs by default.

```
docker run -d --name nettail --restart unless-stopped \
    --network host ghcr.io/mjaksn/nettail:latest --web --web-bind 0.0.0.0
docker logs nettail        # the URL, with its token, is printed at startup
```

Under host networking the container's `0.0.0.0` is the host's, so that puts the
view on every address the host has, and from another machine it opens by the
host's address or by its name. `--web-host` narrows it to the names given, and
the printed URL then carries the first:

```
docker run -d --name nettail --restart unless-stopped \
    --network host ghcr.io/mjaksn/nettail:latest \
    --web --web-bind 0.0.0.0 --web-host z2m
```

`docker-compose.yml` in this repository is the same thing as a Compose file,
with the options worth knowing about written out beside it.

### Use host networking

This is the setting that matters, and it is not a performance nicety.

A flow collector cares who sent the datagram. Behind Docker's bridge that
address is rewritten to the gateway, so **every exporter on your network shows
up as `172.17.0.1`** and they cannot be told apart. The EXPORTER column becomes
a constant. Host networking keeps the real address.

It also keeps Docker's userland proxy out of the datagram path. The collector
asks the kernel for a 4 MB receive buffer because flow bursts are lossy, and a
hop in front of that socket undoes part of what the buffer is for.

Host networking is a Linux arrangement, and on Linux it is the only one in
which this image is fully usable. Docker Desktop on Windows and macOS does not
give a container the host's interfaces: `--network host` there puts the
container in the namespace of the Docker Desktop virtual machine, so a view
bound inside it is not on your loopback at all, and nothing answers. What works
on Docker Desktop is the bridge, with both ports published:

```
docker run -d --name nettail --restart unless-stopped \
    -p 2055:2055/udp -p 127.0.0.1:2056:2056 ghcr.io/mjaksn/nettail:latest
```

The view is reachable that way, because `0.0.0.0` inside the container is a
routable bind and the `Host` check does not compare names under one; the
publish to `127.0.0.1` is what keeps it private. Publish the web port with a
different number on the host side and the page is a 404, for the reason and
with the fix in [Moving the web port](#moving-the-web-port). The cost that
remains is the exporter address: every exporter shows as the gateway, as
above. For anything beyond a look, run the collector locally or in a Linux VM
with host networking.

### Why the image binds 0.0.0.0

`--web-bind` defaults to `127.0.0.1`, and that default is right on a host.
Inside a container it is unreachable: loopback there belongs to the container's
own network namespace, not to yours, so a published port would answer nothing.
The image therefore passes `--web-bind 0.0.0.0`.

That moves the loopback guarantee rather than dropping it. **Publish the web
port to `127.0.0.1` and it is exactly as private as the default was:**

```
-p 127.0.0.1:2056:2056        only this machine can reach the view
-p 2056:2056                  every interface the host has, over plain HTTP
```

The collector notices it is in a container and says this at startup, in place
of the warning it prints for a routable bind on a host. That warning still
appears everywhere else, unchanged; what would be useless is printing it on
every single container start, when the image asks for `0.0.0.0` every time and
the thing it should be warning about cannot be seen from inside.

With host networking none of this applies. The namespace is the host's, so
`--web-bind 127.0.0.1` works exactly as it does outside a container, the `Host`
check matches, and the startup line is the ordinary one. That is the arrangement
to prefer, and the compose file uses it:

```
docker run -d --name nettail --network host \
    ghcr.io/mjaksn/nettail:latest --web --web-bind 127.0.0.1
```

### Moving the web port

Both halves of the publish have to name the same port. `-p 127.0.0.1:9000:2056`
looks reasonable and answers 404 to everything, the token page included,
because the port is part of what the `Host` header is checked against: the
browser writes `9000` in that header, the collector inside the container is
listening on `2056` and knows nothing of the mapping, and the two cannot
agree. The refusal is the same 404 a wrong token gets, deliberately, so that
somebody probing cannot tell which of the two they got right.

Since 0.5.1 the collector says so on stderr the first time it happens, naming
both ports, which is where `docker logs` will show it. It says it once a run:
it is a fact about how the collector was started rather than about the
request, and repeating it per request would let anyone who can reach the port
scribble over the display.

To move the port, move both sides and tell the collector:

```
docker run -d --name nettail --restart unless-stopped \
    -p 2055:2055/udp -p 127.0.0.1:9000:9000 ghcr.io/mjaksn/nettail:latest \
    --web --web-bind 0.0.0.0 --web-port 9000
```

Publishing `9000:2056` and leaving the collector on its default does not work
and cannot be made to, short of a proxy that rewrites the header. The same
applies outside Docker: anything in front of this that changes the port, a
tunnel or a reverse proxy included, needs `--web-port` set to the port the
browser will actually name.

### What it does and does not carry

The collector keeps no state. It holds what it is showing in memory and writes
nothing, so there is no volume to mount and nothing to lose. A restart starts
counting again, which is the same thing `Ctrl-C` and a fresh run do.

The one thing worth mounting is your own static name mappings:

```
-v ./lan-hosts:/etc/nettail/lan-hosts:ro
```

and then `--hosts /etc/nettail/lan-hosts`.

It runs as an unprivileged user, UID and GID 10001. The default port is 2055,
which is above 1024 and so needs no privilege to bind.

### The console display, if you want it

```
docker run -it --rm --network host ghcr.io/mjaksn/nettail:latest
```

`-it` is not optional. Without a terminal the keyboard controls turn themselves
off and the sticky header has nothing to stick to. For anything longer than a
look, install it locally instead: this is a console program, and a container is
a poor terminal.

For a machine readable stream, `--json` needs no terminal and pipes as usual:

```
docker run --rm --network host ghcr.io/mjaksn/nettail:latest --json > flows.jsonl
```

## How it works

### Protocol support

| Version | Header | Templates | Notes |
| --- | --- | --- | --- |
| NetFlow v5 | 24 bytes | none, fixed 48-byte records | Fully decoded on the first packet |
| NetFlow v9 | 20 bytes | FlowSet 0 (data), 1 (options) | Templates cached per exporter and source ID |
| IPFIX / v10 | 16 bytes | Set 2 (data), 3 (options) | Enterprise fields and variable-length encoding supported |

Templates are keyed by `(exporter address, observation domain, template ID)`.
Different exporters can reuse the same template IDs for different layouts, so the key
has to include all three.

IPFIX variable-length fields are handled: a declared length of `0xFFFF` means the
value is prefixed by a one-byte length, or by `0xFF` followed by a two-byte length
for values 255 bytes or longer.

Options templates are parsed and stored even though their contents are not currently
interpreted. This is necessary so that options data sets can be walked without
desyncing the parser mid-message.

### Layout

Reading the wire is not part of this repository any more, and neither is
finding out what a machine is called. Decoding v5, v9 and IPFIX, the template
store behind them, sequence gaps and advertised sampling rates live in
netflume; hostname discovery and its cache live in lanname. Both were lifted
out of this program and released on their own. What is left here is the
display: the part that decides what a flow should look like. Mostly that
means a terminal, and since 0.2.0 the same display can be mirrored into a
browser, which draws what the terminal draws rather than deciding anything
of its own.

| It comes from | What lives there |
| --- | --- |
| `netflume` | decoding v5, v9 and IPFIX, and the templates behind them |
| `netflume` | information element definitions, shared by all three versions |
| `netflume` | what a field is: address kind, protocol, flags, and the system's own service names |
| `netflume` | the sampling rate an exporter advertises, and gaps in its sequence numbers |
| `lanname` | hostname discovery over reverse DNS, mDNS and NetBIOS, and its cache |

The `nettail` command installed by the package is a console script pointed at
`cli.main`; `python -m nettail` reaches the same function through
`__main__.py`, which is how a checkout runs it and how the test suite starts
subprocesses. Everything else is the package:

| Module | What lives there |
| --- | --- |
| `__main__.py` | what `python -m nettail` runs |
| `colour.py` | ANSI codes, the switch that disables them, and the stream that takes them out for one reader and not the other |
| `values.py` | sizes, rates and durations, written for a column |
| `sizescale.py` | the colour ramp behind the BYTES column |
| `services.py` | port names, the system database first and a shipped list after |
| `display.py` | laying one flow out as a line of text |
| `sticky.py` | pinning the column header to the top of the window |
| `statusbar.py` | the two-line bar along the foot of the window |
| `feed.py` | the events a browser watches, and the bounded queues they wait in |
| `web.py` | serving those events over HTTP, and taking keys back from a browser |
| `web.html` | the page itself, shipped as package data |
| `cli.py` | argument parsing, the receive loop, and the exit summary |
| `tally.py` | the running totals behind the traffic summary |
| `keys.py` | reading keypresses, and what each one does |

Dependencies run one way, from `cli` down towards `colour` and `values`, with
`web` above `feed` and both below `cli`, so there are no import cycles.
`nettail/__init__.py` re-exports the public names, which is what makes
`from nettail import SizeScale` work wherever the class actually lives. Nothing
from either dependency is re-exported: a program that wants the decoder should
import netflume, and one that wants the resolver should import lanname, and get
the version it pinned rather than whichever one this package happens to be
sitting on.

`cli.py` owns the socket and hands each datagram to a `netflume.Decoder`, which
returns a message and says nothing. netflume prints nothing at all, by design,
so the running warnings about sampling and lost exports are raised there as
objects and turned into text here, in `report_events`. lanname is the same way
about an unreadable hosts file, which is why that warning is worded here too.

### Robustness

A malformed or truncated datagram increments `parse_errors` and is discarded. The
listener keeps running. Set lengths, record lengths, and DNS compression pointer
loops are all bounds-checked.

---

## Troubleshooting

### Nothing appears at all

Confirm packets are arriving before suspecting the parser:

```bash
sudo tcpdump -ni any udp port 2055
```

If tcpdump is silent, the problem is the exporter configuration, routing, or a
firewall, not this script.

### Nothing appears for the first few minutes

Expected with v9 and IPFIX. Exporters resend templates on their own schedule,
typically every 60 to 600 seconds, and data records that arrive before their template
cannot be decoded. Those are counted as `data sets with no template` in the exit
summary. Wait.

If that counter keeps climbing after ten minutes, the exporter is sending data for a
template it is not retransmitting, or template datagrams are being dropped in transit.

### Only some flows show up

Three possibilities. The exit summary names the first two outright, and the third is
what is left when neither of them appears.

**Sampling**, if a `Sampling` section appears or a warning was printed while running.
See [Configuring the UDM Pro](#configuring-the-udm-pro).

**Export loss**, if an `Export gaps` section appears. Exports left the router and
never reached the decoder. See [Export gaps](#export-gaps).

**Never exported**, if neither section appears and what is missing is local traffic.
Nothing was lost, because nothing was sent: the router may not export flows between
local hosts at all, whether or not it routes them. See
[Local traffic may never arrive](#local-traffic-may-never-arrive).

### Hostnames never resolve

Try each source in isolation:

```bash
# Is PTR working at all?
dig -x 192.168.1.42

# Does the device answer mDNS?
avahi-resolve -a 192.168.1.42

# Does it answer NetBIOS?
nmblookup -A 192.168.1.42
```

If all three come back empty, the device genuinely does not advertise a name and a
static `--hosts` entry is the only option. Check the `Name resolution` block in the
exit summary to see which sources are producing hits.

### The browser view is a 404 from another machine

The token is right and the page is still a 404. A wrong token and a refused
`Host` header get the same 404 on purpose, so nothing at your end says which
it was. The `Host` check refuses a request in three cases: the collector is on
its loopback default, which no other machine reaches anyway; `--web-host` was
given and the name in the address bar is not on it; or the port in the address
bar is not `--web-port`, which happens behind a port forward or a Docker
publish that maps one port to another. Under a routable `--web-bind` with no
`--web-host`, any name works. See [The web interface](#the-web-interface).

### `unsupported_version` climbing

Something is sending non-NetFlow traffic to the port, or the exporter is configured
for sFlow. sFlow is a different protocol and is not supported.

### High CPU

Reduce `--resolve-workers`, or switch to `--resolve dns` or `--resolve off`. On a busy
link, `--json` piped to a separate process is cheaper than rendering the coloured
table.

---

## Tests

```bash
python tests/run.py               # every suite
python tests/run.py tally keys    # only suites whose name contains either
python tests/run.py -v            # print every check, not only failures
```

1192 checks across 32 suites, in well under a minute. No test dependencies and
no test runner to learn: the suites need only netflume and lanname, the same as
the collector.

They cover this program and not its decoder. How a gap is spotted, how a
template is stored and how a sampling rate is read are netflume's questions and
netflume's suites answer them, as name resolution and its cache are lanname's;
what is checked here is everything downstream of that, including the two places
a gap or a sampling rate is put into words.

Each suite is a plain script that runs top to bottom, prints a line per check
and exits non-zero if any failed:

```
PASS  a skipped message is reported
PASS  and attributed to the stream
FAIL  counted in data records  got 'export messages'
```

That shape suits what these tests mostly do, which is build state up over a
sequence of steps and assert along the way. `tests/harness.py` holds the little
they share: `check`, `finish`, a colour stripper, and a stream that claims to
be a terminal.

`tests/run.py` gives each suite its own process. That is not fussiness: several
of them replace `socket.socket`, `shutil.get_terminal_size` or the keyboard for
their duration, and sharing an interpreter would let one suite's fakes decide
another suite's result.

### What is covered

| Suite | What it holds |
| --- | --- |
| `test_tally` | breakdowns, busiest pairs, longest flows, and the link speed floor against a brute-force sweep |
| `test_sequence_gaps` | learning how each exporter counts, and telling loss from reordering and restarts |
| `test_options_records` | options data kept out of the flow display, sampling rates read from every form exporters use |
| `test_keys`, `test_keys_end_to_end` | every keyboard control, and scripted keys driven through the real receive loop |
| `test_key_help` | the `?` listing, the table and the dispatch agreeing, and the reminder line pointing at a key that answers |
| `test_size_scale`, `test_size_window`, `test_size_observe` | the BYTES colour ramp, its sliding window, and what it ranges against |
| `test_sticky_header`, `test_sticky_resize`, `test_sticky_shutdown` | the scroll region, what a resize does to it, and giving the terminal back |
| `test_status_lines`, `test_status_bar`, `test_status_shutdown` | what the bar says at every width, the rows it claims, and the two features sharing one scroll region |
| `test_flow_display` | the direction arrow, names in place of addresses, and the mac line sitting under the columns it belongs to |
| `test_colour` | colour decided per reader, painted once and taken out again for whichever of the two refused it |
| `test_help_colour` | the help staying plain on 3.14, where argparse settles colour before this program can |
| `test_hosts_and_gradient` | the local host list, and the report ramp spanning the rows it prints |
| `test_summary_key` | the traffic summary printed on demand, and the clock it is dated by |
| `test_sticky_with_gradient` | the pinned header and the size ramp sharing one screen |
| `test_services` | the supplemental port names, the parser behind them, the system database keeping precedence, and the ephemeral floor pinned to where netflume actually puts it |
| `test_readme_samples` | the transcripts this README quotes, against what the program prints today |
| `test_endpoints`, `test_top_talkers` | one definition of a flow's ends, both directions counted |
| `test_web_feed` | the event bus: what it publishes, what it drops when a browser falls behind, and the greeting a late arrival gets |
| `test_web_server` | the stream against a real server: the greeting, the events, the watcher cap, and the exit summary reaching a browser that is still open |
| `test_web_security` | everything the web interface refuses: forged tokens, forged `Host` and `Origin` headers, paths that try to reach the filesystem, and keys the collector does not answer |
| `test_web_keys` | browser keys driven through the real receive loop, including the two `--json` interactions nobody would notice going wrong |
| `test_container_warning` | which of the two web bind warnings is printed, and that guessing at a container only ever chooses prose |
| `test_size_end_to_end` | a real collector on loopback, fed real v5 datagrams, with the scale fixed and re-ranging |
| `test_version` | `--version`, and the package, pyproject and changelog agreeing about the number |

Nothing reaches the network except `test_size_end_to_end`, which starts a real
collector on the loopback interface, waits to be told the socket is bound, and
sends it NetFlow v5 datagrams, and the two web suites, which bind a server on a
port the operating system picks and talk to it over loopback. Nothing leaves the
machine. Everything else drives the code in process with synthetic packets,
which is also the honest limitation: the exporters are imaginary, and how the
colours actually look still needs a human and a terminal. The page is checked
for what it must not contain rather than rendered, so the browser half wants a
human too.

---

## Limitations

- **No sFlow.** Different protocol, different wire format.
- **No IPv6 transport.** The collector socket is `AF_INET`, and so is the web
  interface. IPv6 addresses *inside* flow records are decoded fine, but the
  exporter has to reach the collector over IPv4.
- **Single-threaded receive.** One thread reads the socket, decodes, renders and
  answers keys. Fine for a home or small office link; under heavy load the right
  change is to move the socket read into its own thread feeding a bounded queue.
  `--web` adds threads, but none of them go near the socket or change any
  collector state: they read a queue and serve it, which is what keeps this
  claim true of the part that matters.
- **No persistence.** Everything is in memory and lost on exit. Use `--json` and
  redirect if you want history. The web interface is a live view and keeps no
  history of its own: a browser opened late is shown the banner and the current
  figures, not the flows it missed.
- **The web interface has no TLS and no login.** A token in the URL over plain
  HTTP is enough for something bound to loopback and nowhere near enough for
  anything else. See [The web interface](#the-web-interface).
- **No IOC matching.** By design. See below.
- **Options data is read for sampling only.** Other exporter metadata carried in
  options records, such as interface names and application ID mappings, is
  decoded but not displayed.
- **Flow data is 5-tuple only.** No SNI, no JA3, no DNS names, no payload. If you need
  those, NetFlow is the wrong data source and a mirror port with Zeek is the right one.

---

## Extending it

The `--json` mode exists so that matching, enrichment, and alerting can live in a
separate process. Keeping them out of the receive loop is deliberate, not laziness:
a blocking feed lookup in the hot path drops flows silently.

A reasonable next stage:

```bash
nettail --json | python3 ioc_match.py
```

where `ioc_match.py` holds threat intel indicators in a radix trie (feeds ship CIDRs,
so a hash set is the wrong structure), deduplicates on `(src_addr, dst_addr,
dst_port)` with a TTL cache, and tiers its sources by confidence rather than treating
them all equally.

Useful starting points for feeds, roughly in descending order of signal quality:

- abuse.ch Feodo Tracker, active botnet C2
- abuse.ch ThreatFox, filtered to confidence 75 and above
- Spamhaus DROP, hijacked and criminal-controlled netblocks
- Emerging Threats `compromised-ips.txt`

Log-only rather than alert-worthy: FireHOL levels 2 and 3, and any scanner or
brute-force list. Those are inbound-attacker lists, and an outbound match usually
means you hit shared hosting.

Behavioural signals available from flow data are generally better value than IP
reputation lookups, since most modern malware C2 rides infrastructure that will never
appear on a blocklist. Worth building, in rough order of yield:

1. **Beaconing.** Connection interval periodicity with jitter tolerance
2. **Never-before-seen destination ASN** for a given internal host
3. **Long-lived low-volume connections**, the classic tunnel signature
4. **Upload/download ratio inversion** on a host that normally only downloads

---

## License

Do what you want with it.
