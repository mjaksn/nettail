"""A QR code for the web interface URL, drawn in the terminal.

Why there is a QR encoder in this repository at all, when a good one is a pip
install away: the answer is what a dependency costs here rather than what it is
worth anywhere else. This program installs two pure Python packages and nothing
else, its suite has no dependencies and is not meant to grow one, and the
container image pins every byte it installs by hash. The encoder below is
write-once code against a standard that was fixed in 2015 and is not going to
move. Paying for it once in lines is cheaper here than paying for it forever in
supply chain.

What makes it small enough to be worth doing is the payload. This encodes one
thing, a URL of this program's own making, so the general case can go:

- **Error correction level L, and versions 1 through 5 only.** Those five are
  one Reed-Solomon block each. Version 6 splits into two, and with two comes
  the interleaving that most of a general encoder's block handling exists for.
- **No version information block**, which only versions 7 and up carry.
- **One alignment pattern**, at `4V+10`, which is where versions 2 to 5 put
  their only one. No table of centres, no combinations to rule out.
- **An eight bit character count**, which is what byte mode uses below version
  10, so there is no width to switch on.

What that leaves is 106 bytes of URL. The longest this program can build is a
routable name from `--web-host` with a token on the end, and it does not come
close. Anything that somehow did gets its URL printed on its own, which is the
answer the reader wanted anyway.

Byte mode throughout, and not alphanumeric, which would be denser: the token
in the URL is case-sensitive and alphanumeric mode is uppercase only.

The matrix this builds was checked module for module against segno over a few
thousand payloads while it was being written. That is a check made once, by
hand, and not a dependency: what the suite holds it to is the result, written
down as vectors in `tests/test_qr.py`.
"""

import os
import sys

from .colour import C

# Data and total codewords per version at error correction level L.
#
# The list stops at five because five is where one Reed-Solomon block stops
# being enough. Every version here is a single block, which is the whole
# reason the interleaving that a general encoder needs is absent below.
CAPACITY = {1: (19, 26), 2: (34, 44), 3: (55, 70), 4: (80, 100), 5: (108, 134)}

# The largest payload any of the above can hold, less the two codewords the
# mode indicator and the character count take between them.
MAX_BYTES = max(data for data, _total in CAPACITY.values()) - 2

# Modules of light margin around the symbol. The standard asks for four and
# a reader that has to hunt for the edge is a reader that gives up, so this
# is not somewhere to save two columns.
QUIET_ZONE = 4

# The half block characters the symbol is drawn with, keyed by the pair of
# modules they stand for, top then bottom.
#
# Two rows of modules to a row of text, because a QR code is square and a
# terminal cell is not: drawn a module to a cell it would be twice as tall as
# it is wide and scan as nothing at all.
#
# A dark module is drawn as a space and a light one as a block, which reads
# correctly on a terminal with a dark background and inverted on a light one.
# That is the same choice every terminal QR renderer makes, and it is made for
# the same reason: there is no way to ask what colour the window is, and the
# escape codes that would force the issue are the first thing a run without
# colour takes back out. Scanners have coped with an inverted symbol for years.
BLOCKS = {(1, 1): " ", (0, 1): "▀", (1, 0): "▄", (0, 0): "█"}

# GF(256) for the Reed-Solomon arithmetic below, built once at import from the
# primitive polynomial the standard names. The exponent table is kept at twice
# its length so that a sum of two logarithms can index it without wrapping by
# hand at every use.
_EXP = [0] * 512
_LOG = [0] * 256


def _build_tables():
    x = 1
    for i in range(255):
        _EXP[i] = x
        _LOG[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        _EXP[i] = _EXP[i - 255]


_build_tables()


def _generator(degree):
    """The Reed-Solomon generator polynomial for `degree` check codewords."""
    poly = [1]
    for i in range(degree):
        nxt = [0] * (len(poly) + 1)
        for j, coefficient in enumerate(poly):
            nxt[j] ^= coefficient
            if coefficient:
                nxt[j + 1] ^= _EXP[(_LOG[coefficient] + i) % 255]
        poly = nxt
    return poly


def _remainder(data, degree):
    """The check codewords for `data`: polynomial division, keeping the rest."""
    gen = _generator(degree)
    rest = list(data) + [0] * degree
    for i in range(len(data)):
        coefficient = rest[i]
        if coefficient:
            for j, g in enumerate(gen):
                rest[i + j] ^= _EXP[(_LOG[coefficient] + _LOG[g]) % 255]
    return rest[len(data):]


def _codewords(data, version):
    """The full message for `data`: header, payload, padding and check bytes.

    The order here is the standard's and each step is load-bearing. The four
    bit mode indicator and the eight bit count come first, so every byte of
    the payload after them straddles a codeword boundary. The terminator is up
    to four zero bits, fewer only when the symbol is nearly full. Then zeros to
    the next byte, then the two pad codewords alternating, beginning with
    0xEC and never the other way round.

    Getting that last detail backwards is invisible: a reader takes the length
    from the count indicator and never looks at the padding, so the symbol
    scans perfectly either way. It is written down here because a thing that
    cannot be caught by trying it is exactly the thing to be careful about.
    """
    ncap, total = CAPACITY[version]
    bits = [0, 1, 0, 0]
    bits += [(len(data) >> b) & 1 for b in range(7, -1, -1)]
    for byte in data:
        bits += [(byte >> b) & 1 for b in range(7, -1, -1)]
    bits += [0] * min(4, ncap * 8 - len(bits))
    bits += [0] * (-len(bits) % 8)
    words = [int("".join(str(b) for b in bits[i:i + 8]), 2)
             for i in range(0, len(bits), 8)]
    pad = (0xEC, 0x11)
    for n in range(ncap - len(words)):
        words.append(pad[n % 2])
    return words + _remainder(words, total - ncap)


def _skeleton(version):
    """The symbol with every function pattern on it, and a map of which is which.

    The map matters as much as the matrix. Everything after this asks it two
    questions: where a data bit may be written, and which modules a mask may
    flip. Both are the same question, which is why one map answers them.
    """
    size = version * 4 + 17
    matrix = [[0] * size for _ in range(size)]
    function = [[False] * size for _ in range(size)]

    def put(row, col, value):
        if 0 <= row < size and 0 <= col < size:
            matrix[row][col] = value
            function[row][col] = True

    # The three finder patterns, each with the light separator around it. The
    # loop runs one module wide of the pattern on every side, which is what
    # draws the separator without a second pass; the writes that fall outside
    # the symbol are dropped by `put`.
    for base_row, base_col in ((0, 0), (0, size - 7), (size - 7, 0)):
        for row in range(-1, 8):
            for col in range(-1, 8):
                dark = (0 <= row < 7 and 0 <= col < 7
                        and (row in (0, 6) or col in (0, 6)
                             or (2 <= row <= 4 and 2 <= col <= 4)))
                put(base_row + row, base_col + col, 1 if dark else 0)

    # The timing patterns, which run the width and height of the symbol and
    # give a reader the module pitch. They yield to the finders already there.
    for i in range(size):
        if not function[6][i]:
            put(6, i, 1 - i % 2)
        if not function[i][6]:
            put(i, 6, 1 - i % 2)

    # The one alignment pattern that versions 2 to 5 carry. Version 1 has none.
    if version >= 2:
        centre = version * 4 + 10
        for row in range(-2, 3):
            for col in range(-2, 3):
                put(centre + row, centre + col,
                    1 if max(abs(row), abs(col)) != 1 else 0)

    # The module that is always dark, reserved here but left light until a mask
    # has been chosen. The standard scores a masked symbol before the format
    # information goes on, and this module is scored with it: setting it now
    # changes which mask wins about one time in twenty. `_apply_format` turns
    # it on, which is the only place a symbol is finished.
    put(size - 8, 8, 0)

    # The format information areas, reserved so that no data lands in them.
    for i in range(9):
        if not function[8][i]:
            put(8, i, 0)
        if not function[i][8]:
            put(i, 8, 0)
    for i in range(8):
        put(8, size - 1 - i, 0)
    for i in range(7):
        put(size - 1 - i, 8, 0)
    return matrix, function, size


def _place(matrix, function, size, words):
    """Write the message into the symbol, in the order the standard walks it.

    Two columns at a time, right to left, alternating up the pair and then
    down the next, taking the right hand module of each pair first and
    stepping over any that a function pattern already owns. Column six is
    skipped whole: the vertical timing pattern lives there and the pairing
    resumes on its far side rather than straddling it.

    A symbol has a few more module positions than the message has bits, seven of
    them in versions 2 to 5 and none at all in version 1, and those are left as
    they were found, which is light.
    """
    bits = iter([(word >> b) & 1 for word in words for b in range(7, -1, -1)])
    upward = True
    col = size - 1
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for row in rows:
            for c in (col, col - 1):
                if not function[row][c]:
                    matrix[row][c] = next(bits, 0)
        upward = not upward
        col -= 2


def _mask_fn(number):
    """The condition each of the eight data masks flips a module on."""
    return (lambda i, j: (i + j) % 2 == 0,
            lambda i, j: i % 2 == 0,
            lambda i, j: j % 3 == 0,
            lambda i, j: (i + j) % 3 == 0,
            lambda i, j: (i // 2 + j // 3) % 2 == 0,
            lambda i, j: (i * j) % 2 + (i * j) % 3 == 0,
            lambda i, j: ((i * j) % 2 + (i * j) % 3) % 2 == 0,
            lambda i, j: ((i + j) % 2 + (i * j) % 3) % 2 == 0)[number]


# The seven module run that a scanner reads as a finder pattern: dark, light,
# three dark, light, dark, in the 1:1:3:1:1 ratio the standard describes.
_FINDER_RUN = bytes((1, 0, 1, 1, 1, 0, 1))


def _finder_like(line, size):
    """Rule three: finder-like runs with light space beside them, at 40 each.

    The standard asks for the run to be preceded or followed by four light
    modules and says nothing about one sitting flush against the edge of the
    symbol, where there is no room for four of anything. Counting that as
    qualifying is the reading the implementations worth copying have settled
    on, and it is the reading here.

    A run that does not qualify is stepped over by four rather than seven,
    because the last three dark modules of one run can be the first of the
    next and skipping the whole of it would miss that.
    """
    score = 0
    idx = line.find(_FINDER_RUN)
    while idx != -1:
        after = idx + 7
        if (idx in (0, size - 7)
                or not any(line[max(idx - 4, 0):idx])
                or not any(line[after:after + 4])):
            score += 40
        else:
            after = idx + 4
        idx = line.find(_FINDER_RUN, after)
    return score


def _penalty(matrix, size):
    """How poorly a masked symbol reads, by the four rules in the standard.

    Lower is better, and the lowest of the eight masks wins. The rules are
    there to keep a symbol from growing long runs or wide blocks of one
    colour, from carrying anything a scanner could take for a finder pattern,
    and from drifting far off an even split of dark and light.

    Scored with the format modules still blank and the dark module still
    light, which is what the standard asks for and what `_skeleton` leaves
    behind for it.
    """
    score = 0
    lines = [bytes(row) for row in matrix] + [bytes(col) for col in zip(*matrix)]
    for line in lines:
        run, previous = 1, line[0]
        for value in line[1:]:
            if value == previous:
                run += 1
            else:
                if run >= 5:
                    score += run - 2
                run, previous = 1, value
        if run >= 5:
            score += run - 2
        score += _finder_like(line, size)
    for row in range(size - 1):
        for col in range(size - 1):
            if (matrix[row][col] == matrix[row][col + 1]
                    == matrix[row + 1][col] == matrix[row + 1][col + 1]):
                score += 3
    dark = sum(sum(row) for row in matrix)
    score += abs(20 * dark - 10 * size * size) // (size * size) * 10
    return score


def _format_bits(mask):
    """The fifteen format modules: level, mask, BCH check bits, and the xor.

    The xor at the end is the standard's, and it is there so that a symbol
    whose level and mask both happen to be zero does not put fifteen light
    modules in a row beside the finder patterns.
    """
    value = (1 << 3) | mask          # 01 is error correction level L
    rest = value << 10
    for i in range(4, -1, -1):
        if rest & (1 << (i + 10)):
            rest ^= 0x537 << i
    return ((value << 10) | rest) ^ 0x5412


def _apply_format(matrix, size, mask):
    """Write the format information, twice, and turn the dark module on.

    The two copies run in opposite directions around the symbol, which is why
    the halves below look nothing like each other, and a reader that finds one
    of them damaged takes the other. Both are written least significant bit
    first. The copy around the top left finder turns a corner and steps over
    the two modules the timing patterns own; the second runs along row eight
    from the right edge and then down column eight from the bottom, eight
    modules and then seven, which is the split the reservation in `_skeleton`
    is cut to.
    """
    matrix[size - 8][8] = 1
    bits = _format_bits(mask)
    for i in range(15):
        bit = (bits >> i) & 1
        if i < 6:
            matrix[i][8] = bit
        elif i == 6:
            matrix[7][8] = bit
        elif i == 7:
            matrix[8][8] = bit
        elif i == 8:
            matrix[8][7] = bit
        else:
            matrix[8][14 - i] = bit
        if i < 8:
            matrix[8][size - 1 - i] = bit
        else:
            matrix[size - 15 + i][8] = bit


def encode(text):
    """The symbol for `text`, as rows of 0 and 1, or None if it will not fit.

    None rather than an exception because the caller has something useful to
    do with it: print the URL on its own, which is what the reader was after.
    """
    data = text.encode("utf-8")
    version = next((v for v in sorted(CAPACITY)
                    if len(data) + 2 <= CAPACITY[v][0]), None)
    if version is None:
        return None
    words = _codewords(data, version)
    best = None
    for mask in range(8):
        matrix, function, size = _skeleton(version)
        _place(matrix, function, size, words)
        flip = _mask_fn(mask)
        for row in range(size):
            for col in range(size):
                if not function[row][col] and flip(row, col):
                    matrix[row][col] ^= 1
        score = _penalty(matrix, size)
        if best is None or score < best[0]:
            best = (score, mask, matrix)
    _score, mask, matrix = best
    _apply_format(matrix, len(matrix), mask)
    return matrix


def render(matrix, border=QUIET_ZONE):
    """The symbol as lines of text, two rows of modules to each.

    An odd number of rows once the margin is on gets one more light row at the
    bottom, which the margin swallows without anybody noticing.
    """
    size = len(matrix)
    width = size + 2 * border
    rows = ([[0] * width for _ in range(border)]
            + [[0] * border + row + [0] * border for row in matrix]
            + [[0] * width for _ in range(border)])
    if len(rows) % 2:
        rows.append([0] * width)
    return ["".join(BLOCKS[pair] for pair in zip(rows[i], rows[i + 1]))
            for i in range(0, len(rows), 2)]


def window(stream=None):
    """The size of the window `stream` writes to, columns by lines.

    `shutil.get_terminal_size` would be the obvious thing and is the wrong
    one, because it measures stdout whatever it is handed and this block goes
    to stderr. The two are not the same window and need not both be one:
    `nettail --web > flows.txt` leaves a perfectly good terminal on stderr
    with the keyboard live on it, and measuring the file instead would answer
    zero and refuse to draw a symbol there was room for. The mirror image is
    worse in a quieter way, sizing a block bound for a file against whatever
    terminal stdout happens to be.

    The two environment variables come first, as they do in shutil, because
    that is how an operator overrides the answer and how the suite sets one.
    Zeros when there is no window to measure, which is what a pipe is, and
    what `fits` refuses on.
    """
    stream = stream if stream is not None else sys.stderr
    try:
        columns = int(os.environ["COLUMNS"])
    except (KeyError, ValueError):
        columns = 0
    try:
        lines = int(os.environ["LINES"])
    except (KeyError, ValueError):
        lines = 0
    if columns <= 0 or lines <= 0:
        try:
            measured = os.get_terminal_size(stream.fileno())
        except Exception:
            # A stream with no descriptor at all, one that is not a terminal,
            # and one whose fileno() raises are all the same answer here, and
            # the set of exceptions differs by platform and by what the stream
            # happens to be. There is nothing to do with any of them but go
            # without a symbol.
            measured = (0, 0)
        columns = columns if columns > 0 else measured[0]
        lines = lines if lines > 0 else measured[1]
    return columns, lines


def fits(matrix, size, url, border=QUIET_ZONE):
    """Whether the whole block fits a window of `size`, columns by lines.

    A QR code that wrapped is not a degraded QR code, it is an unreadable one,
    and one whose top has scrolled away is no better. Both are worth refusing
    over, because the fallback costs the reader nothing: the URL underneath is
    what they came for and it is still there.

    The URL is measured along with the symbol, and not only because it takes a
    row. It can be wider than the symbol above it, which a name from
    `--web-host` makes easy, and then it wraps and takes several. Counting one
    row for it regardless is how a window that this said yes to could still
    scroll the top of the symbol away: the check has to be about the block
    that gets printed rather than about the symbol in it.

    A window that reports nothing, which is what a redirected stream does,
    fails this. So does one measured against a scroll region rather than the
    whole terminal, which is the number the caller passes when a sticky header
    and a status bar have taken rows off the top and bottom.
    """
    columns, lines = size
    if columns < 1:
        return False
    drawn = render(matrix, border)
    # The blank line the block opens with and the heading under it, then the
    # symbol, then however many rows the URL wraps into.
    #
    # A block exactly as tall as the window loses its top row, because the
    # last newline scrolls the region once more to leave the cursor somewhere,
    # and that row is the blank line, which carries nothing. So this asks for
    # the block to fit rather than for the block and a spare row, and what it
    # is really promising is that the heading, the symbol and the URL all
    # survive. Written down because it reads like an off-by-one and is not.
    needed = 2 + len(drawn) + max(1, -(-len(url) // columns))
    return columns >= len(drawn[0]) and lines >= needed


def write_qr(url, out=None, size=None, border=QUIET_ZONE):
    """Print a QR code for `url`, with the URL itself underneath it.

    The URL is printed whatever happens, and the symbol only when there is
    somewhere it can be drawn whole. A reader looking at a window too narrow
    for it, or at a stream that is not a window at all, is told where the
    interface is in the one form that always works.

    Written to stderr like the summary, the host list and the key listing, so
    that a run with stdout redirected into a file gets its answer on the
    terminal where the question was asked.
    """
    out = out if out is not None else sys.stderr
    if size is None:
        size = window(out)
    matrix = encode(url)
    print(f"\n{C.BOLD}{C.BLUE}Web interface{C.RESET}", file=out)
    if matrix is not None and fits(matrix, size, url, border):
        for line in render(matrix, border):
            print(line, file=out)
    print(f"{C.CYAN}{url}{C.RESET}", file=out)
