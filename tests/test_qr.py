"""The QR encoder, pinned to vectors, and the guards around drawing one.

There is no scanner in this suite and there cannot be one, so nothing here
proves that a phone reads what `nettail/qr.py` draws. What it proves is that
the encoder still produces exactly the symbols it produced on the day it was
checked against an independent implementation, module for module, which is the
next best thing and the thing that catches a change made by accident.

The vectors below were generated once, from segno, with one correction applied
to it: its `write_padding_bits` extends the stream by a whole codeword when the
data already ends on a codeword boundary, which in byte mode is always, where
the standard asks for nothing. The difference is harmless, because a reader
takes the length from the character count indicator and never looks at the
padding, but it is a difference, and a vector regenerated from an uncorrected
segno would quietly move every symbol here by one pad codeword and its check
bytes with it. Anyone refreshing these has to apply that correction again.

Five vectors, one for each version this encoder handles, chosen so that every
one of the five capacity steps is crossed by something. Between them they
exercise the version with no alignment pattern and the four with one, and all
four penalty rules by way of whichever mask each one settles on.
"""
import io
import os

from harness import check, finish, plain

from nettail import qr
from nettail.keys import KEYS, QR_KEY, WEB_EXCLUDED, Controls, web_keys

VECTORS = (
    (
        'n',
        1,
        (
            "111111100101101111111",
            "100000100111001000001",
            "101110101101101011101",
            "101110100101001011101",
            "101110100010101011101",
            "100000100000101000001",
            "111111101010101111111",
            "000000001101100000000",
            "111011111111011000100",
            "010000001110001000110",
            "110001100000100010001",
            "101000000000001000111",
            "010000110010101010110",
            "000000001111010101011",
            "111111101111011101111",
            "100000101001110111000",
            "101110101101011101101",
            "101110100110001000110",
            "101110101100100010001",
            "100000101010001000110",
            "111111101000101010111",
        ),
    ),
    (
        'http://127.0.0.1:2056/',
        2,
        (
            "1111111011110100101111111",
            "1000001000011010001000001",
            "1011101001010100101011101",
            "1011101000110111001011101",
            "1011101000111000001011101",
            "1000001001010000001000001",
            "1111111010101010101111111",
            "0000000011001110000000000",
            "1101101001001001001000001",
            "1001000100100001110011110",
            "1001111101001001001001001",
            "1110110110000011000001111",
            "1110011101100010111000001",
            "1010100000101101000110010",
            "1100001111100011110001111",
            "1000000101001010100010101",
            "1011111101011111111110110",
            "0000000011010101100010010",
            "1111111001101000101011001",
            "1000001001010100100010000",
            "1011101011110101111111011",
            "1011101010101110001101011",
            "1011101001010100100010111",
            "1000001010011100011110111",
            "1111111010111101111001001",
        ),
    ),
    (
        'http://127.0.0.1:2056/t/AbCd1234EfGh5678/',
        3,
        (
            "11111110111101001100001111111",
            "10000010100100100010101000001",
            "10111010010001011110001011101",
            "10111010000111010101001011101",
            "10111010100111000010101011101",
            "10000010111011101110001000001",
            "11111110101010101010101111111",
            "00000000110000011011100000000",
            "11100110110110111101011110011",
            "10010001000010101001001000011",
            "00101011111010011111000111101",
            "01111000001110111010101000000",
            "01110111011000111001011000001",
            "11101000011001111001011000111",
            "00000011100101110011111101001",
            "11011101101000001000000110000",
            "00001010010110111101011101001",
            "01100100101011001001001001111",
            "11010010100010011011100000001",
            "00111100001110101011001100000",
            "11010110011001110001111111010",
            "00000000111001010001100011101",
            "11111110000100010100101010001",
            "10000010111001010000100010010",
            "10111010000111010011111110010",
            "10111010011010010010100110001",
            "10111010110011111111000110111",
            "10000010101110110011100010000",
            "11111110110001110011101110001",
        ),
    ),
    (
        'http://127.0.0.1:2056/t/QoYm2ZP4rD8xN1sVbTgKcW7eL9uHjX3f/',
        4,
        (
            "111111101001100110110010001111111",
            "100000100110000011110010001000001",
            "101110100010100110001100101011101",
            "101110100000100011001111001011101",
            "101110100101001111110111001011101",
            "100000100010100110100111001000001",
            "111111101010101010101010101111111",
            "000000001011010110010001000000000",
            "110110100111010010011101101000001",
            "111111011100110010000101010010010",
            "111010111011010110001001001111001",
            "011000000111110101010011110101100",
            "101111110101110010111101011101011",
            "100010011000011100000010000001000",
            "110010100001110111010110011111000",
            "001111001001000100010011000010101",
            "101010101110001100000000101011011",
            "100001010111111101101000111010011",
            "111011110001000101010101111100001",
            "000110000000101100001000010110111",
            "101100110010111100011000010101000",
            "110110011000001111110001010010110",
            "101111101100101101000001001100111",
            "101111010100001011100010000011100",
            "111000111010011000110100111110010",
            "000000001110100011001000100010110",
            "111111100110001110110101101010000",
            "100000100110111110010010100010111",
            "101110101001100000011001111110000",
            "101110101101000101001101000100111",
            "101110100110111111011110100110011",
            "100000101011010000011001001010111",
            "111111101111010010001000100000000",
        ),
    ),
    (
        'http://nettail.monitoring.example.invalid:2056/t/QoYm2ZP4rD8xN1sVbTgKcW7eL9uHjX3f/',
        5,
        (
            "1111111001001100111001110011001111111",
            "1000001010011111000010011011101000001",
            "1011101011111100110110011010001011101",
            "1011101001110111001101111000001011101",
            "1011101010000110101001100011101011101",
            "1000001011010110010111001001001000001",
            "1111111010101010101010101010101111111",
            "0000000011001010011011001001100000000",
            "1101001100100001010011001110001110110",
            "1110010110110011011110000000111000111",
            "0011101110000000110110101110100101101",
            "0110010011100011001011101101110001001",
            "1111101100001000111011100101111000000",
            "0000010111011001011111001100001100001",
            "0110101010101001100001110110001111011",
            "1111010011101110111011001101010010010",
            "1001011000110111010100111000011100101",
            "1010100111000001000100110100101010111",
            "1101111001100100000001101100110110011",
            "0011100100010100011101101001101110110",
            "0010011011011011110010010111001010001",
            "1001110011111100000011100100101000001",
            "0000011001011110000100000110101101011",
            "1010000100111100100111001101101000000",
            "1111111110010010111001100100111001011",
            "0110100011010110001101000100011000101",
            "1000011101010110011000011010110000101",
            "0111110100110000011011010100100010010",
            "1110001100101100110011100001111111100",
            "0000000010001110001101110011100010001",
            "1111111011011010100011101011101010111",
            "1000001001001011111001100011100010111",
            "1011101000100001010110010110111110011",
            "1011101011110011100011000100100111000",
            "1011101001100000001110101101000011011",
            "1000001010100010000111010111111010000",
            "1111111010101001110101110111100000011",
        ),
    ),
)


# --- the encoder, module for module -----------------------------------------

for text, version, rows in VECTORS:
    matrix = qr.encode(text)
    label = "%d bytes" % len(text.encode())
    check("%s: encodes to a symbol at all" % label, matrix is not None)
    if matrix is None:
        continue
    size = version * 4 + 17
    check("%s: version %d, so %d modules square" % (label, version, size),
          len(matrix) == size and all(len(r) == size for r in matrix),
          "got %d x %d" % (len(matrix), len(matrix[0])))
    produced = tuple("".join(str(bit) for bit in row) for row in matrix)
    if produced == rows:
        check("%s: every module matches the pinned symbol" % label, True)
    else:
        wrong = [i for i, (a, b) in enumerate(zip(produced, rows)) if a != b]
        check("%s: every module matches the pinned symbol" % label, False,
              "rows %s differ; row %d is\n  %s\nand should be\n  %s"
              % (wrong[:6], wrong[0], produced[wrong[0]], rows[wrong[0]]))


# --- the function patterns are where the standard puts them -----------------
#
# Pinned apart from the vectors above, which would report only that something
# had moved. These say what moved.

URL = "http://127.0.0.1:2056/t/QoYm2ZP4rD8xN1sVbTgKcW7eL9uHjX3f/"
matrix = qr.encode(URL)
size = len(matrix)

FINDER = ["1111111", "1000001", "1011101", "1011101",
          "1011101", "1000001", "1111111"]
for name, top, left in (("top left", 0, 0), ("top right", 0, size - 7),
                        ("bottom left", size - 7, 0)):
    drawn = ["".join(str(matrix[top + r][left + c]) for c in range(7))
             for r in range(7)]
    check("the %s finder pattern is drawn" % name, drawn == FINDER, drawn)

check("the horizontal timing pattern alternates between the finders",
      "".join(str(matrix[6][c]) for c in range(8, size - 8))
      == "".join("1" if c % 2 == 0 else "0" for c in range(8, size - 8)))
check("the vertical timing pattern alternates between the finders",
      "".join(str(matrix[r][6]) for r in range(8, size - 8))
      == "".join("1" if r % 2 == 0 else "0" for r in range(8, size - 8)))

centre = 4 * 4 + 10
check("the one alignment pattern sits at 4V+10",
      [[matrix[centre + r][centre + c] for c in range(-2, 3)]
       for r in range(-2, 3)]
      == [[1, 1, 1, 1, 1], [1, 0, 0, 0, 1], [1, 0, 1, 0, 1],
          [1, 0, 0, 0, 1], [1, 1, 1, 1, 1]])

# The module that is always dark, whatever the payload and whichever mask won.
# It is left light while the masks are scored and turned on afterwards, which
# is the step easiest to drop and impossible to see.
for text in ("n", "http://127.0.0.1:2056/", "x" * 60, "x" * 106):
    drawn = qr.encode(text)
    check("the dark module is set for a %d byte payload" % len(text),
          drawn[len(drawn) - 8][8] == 1)


# --- the format information decodes back ------------------------------------
#
# Read the fifteen modules of each copy back, undo the value the standard xors
# them with, and check that the BCH remainder is zero. That is what a reader
# does, so a symbol failing this is one a reader would reject, and it catches
# either copy being written backwards or into the wrong axis.

def format_copies(drawn):
    edge = len(drawn)
    first = ([drawn[i][8] for i in range(6)]
             + [drawn[7][8], drawn[8][8], drawn[8][7]]
             + [drawn[8][14 - i] for i in range(9, 15)])
    second = ([drawn[8][edge - 1 - i] for i in range(8)]
              + [drawn[edge - 15 + i][8] for i in range(8, 15)])
    return first, second


for text in ("n", "http://127.0.0.1:2056/", "x" * 106):
    drawn = qr.encode(text)
    first, second = format_copies(drawn)
    check("%d bytes: the two format copies agree" % len(text), first == second,
          "%s\n%s" % (first, second))
    value = 0
    for i, bit in enumerate(first):
        value |= bit << i
    value ^= 0x5412
    rest = value
    for i in range(4, -1, -1):
        if rest & (1 << (i + 10)):
            rest ^= 0x537 << i
    check("%d bytes: the format information passes its BCH check" % len(text),
          rest == 0, "remainder %d" % rest)
    check("%d bytes: the format information says level L" % len(text),
          (value >> 13) & 0x3 == 0b01)
    # The mask it names has to be the mask actually applied, which is worth a
    # round trip rather than a range check: masking with 0x7 and then asserting
    # the answer is between 0 and 7 is true however wrong the symbol is. Rebuilt
    # from the payload with that mask, the whole thing has to come back.
    named = (value >> 10) & 0x7
    rebuilt, function, edge = qr._skeleton(
        next(v for v in sorted(qr.CAPACITY)
             if len(text.encode()) + 2 <= qr.CAPACITY[v][0]))
    qr._place(rebuilt, function, edge, qr._codewords(text.encode(), (edge - 17) // 4))
    flip = qr._mask_fn(named)
    for r in range(edge):
        for c in range(edge):
            if not function[r][c] and flip(r, c):
                rebuilt[r][c] ^= 1
    qr._apply_format(rebuilt, edge, named)
    check("%d bytes: the mask it names is the mask that was applied" % len(text),
          rebuilt == drawn, "format says mask %d" % named)


# --- what will not fit ------------------------------------------------------

check("the largest payload that fits is 106 bytes", qr.MAX_BYTES == 106)
check("106 bytes encodes", qr.encode("x" * 106) is not None)
check("107 bytes does not, and says so with None", qr.encode("x" * 107) is None)
check("the empty string still encodes rather than raising",
      qr.encode("") is not None)
check("a payload is measured in bytes and not in characters",
      qr.encode("é" * 53) is not None and qr.encode("é" * 54) is None)


# --- the renderer -----------------------------------------------------------

drawn = qr.render(matrix)
check("a version 4 symbol renders 21 lines by 41 columns",
      len(drawn) == 21 and set(len(line) for line in drawn) == {41},
      "%d lines, widths %s" % (len(drawn), sorted(set(len(x) for x in drawn))))
check("two rows of modules go to each row of text",
      len(drawn) * 2 >= len(matrix) + 2 * qr.QUIET_ZONE)
check("nothing but the four half block characters comes out",
      set("".join(drawn)) <= set(qr.BLOCKS.values()),
      sorted(set("".join(drawn)) - set(qr.BLOCKS.values())))
check("the quiet zone is a solid margin along the top and the bottom",
      all(line == "█" * 41 for line in drawn[:2] + drawn[-2:]))
check("no escape codes are used, so a run without colour keeps its symbol",
      "\x1b" not in "".join(drawn))

odd = qr.render(qr.encode("n"))
check("an odd number of module rows still pairs up",
      len(odd) == (21 + 2 * qr.QUIET_ZONE + 1) // 2, len(odd))


# --- the guards on drawing one ----------------------------------------------

check("a symbol fits a window with room to spare",
      qr.fits(matrix, (80, 40), URL))
check("41 columns is exactly enough for the symbol",
      qr.fits(matrix, (41, 30), URL))
check("40 columns is not, so it would wrap",
      not qr.fits(matrix, (40, 30), URL))
check("24 rows holds the symbol and its three lines of prose",
      qr.fits(matrix, (80, 24), URL))
check("23 rows does not, so its top would scroll away",
      not qr.fits(matrix, (80, 23), URL))
check("a window that reports nothing fails, which is what a pipe reports",
      not qr.fits(matrix, (0, 0), URL))

# The URL is counted along with the symbol, and it can be wider than the
# symbol is. A name from --web-host makes that easy, and a check that gave the
# URL one row regardless would say yes to a window the block then scrolled the
# top off. Fifty-seven characters in a forty-one column window is two rows,
# not one, so what fitted in twenty-four rows above wants twenty-five here.
check("a URL that wraps is counted at the rows it really takes",
      not qr.fits(matrix, (41, 24), URL))
check("and fits once those rows are there", qr.fits(matrix, (41, 25), URL))

LONG = ("http://nettail.monitoring.internal.example.invalid:2056"
        "/t/QoYm2ZP4rD8xN1sVbTgKcW7eL9uHjX3f/")
wide = qr.encode(LONG)
check("a long name still encodes", wide is not None)
check("a URL that wraps to two rows is counted at two",
      not qr.fits(wide, (60, 26), LONG) and qr.fits(wide, (60, 27), LONG))

out = io.StringIO()
qr.write_qr(LONG, out=out, size=(60, 26))
cramped = plain(out.getvalue())
check("so a window that could not hold the block gets no symbol",
      "█" not in cramped)
check("and the URL, still whole", LONG in cramped)


# --- the window is the one the block is going to ----------------------------
#
# shutil.get_terminal_size measures stdout whatever it is handed, and this
# block goes to stderr. The two need not be the same window or both be one:
# `nettail --web > flows.txt` leaves a terminal on stderr with the keyboard
# live on it, and measuring the file would answer zero and refuse to draw a
# symbol there was room for.

was = {name: os.environ.pop(name, None) for name in ("COLUMNS", "LINES")}
try:
    class NoDescriptor(io.StringIO):
        def fileno(self):
            raise io.UnsupportedOperation("no fileno")

    check("a stream with no window behind it measures nothing",
          qr.window(io.StringIO()) == (0, 0))
    check("and so does one whose fileno raises rather than returns",
          qr.window(NoDescriptor()) == (0, 0))
    os.environ["COLUMNS"], os.environ["LINES"] = "100", "50"
    check("the environment is honoured first, as shutil honours it",
          qr.window(io.StringIO()) == (100, 50))
    os.environ["COLUMNS"] = "nonsense"
    check("and nonsense in it falls back rather than raising",
          qr.window(io.StringIO()) == (0, 50))
finally:
    for name, value in was.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


# --- write_qr prints the URL whatever happens -------------------------------

out = io.StringIO()
qr.write_qr(URL, out=out, size=(80, 40))
wide = plain(out.getvalue())
check("a wide window gets the symbol", "█" in wide)
check("and the URL under it", URL in wide)
check("under a heading", "Web interface" in wide)

out = io.StringIO()
qr.write_qr(URL, out=out, size=(30, 40))
narrow = plain(out.getvalue())
check("a narrow window gets no symbol", "█" not in narrow)
check("but still gets the URL, which is what was wanted", URL in narrow)
check("and nothing it prints is wider than the URL itself",
      max(len(line) for line in narrow.splitlines()) <= len(URL))

out = io.StringIO()
qr.write_qr(URL, out=out, size=(80, 23))
short = plain(out.getvalue())
check("a short window gets no symbol either", "█" not in short)
check("and still gets the URL", URL in short)

TOO_LONG = "http://" + "x" * 200
out = io.StringIO()
qr.write_qr(TOO_LONG, out=out, size=(200, 200))
huge = plain(out.getvalue())
check("a URL too long to encode gets no symbol", "█" not in huge)
check("and is printed in full rather than trimmed", TOO_LONG in huge)


# --- the key it hangs off ---------------------------------------------------

check("q is in the key table", QR_KEY in [key for key, _doc in KEYS])
check("q is kept back from the browser", QR_KEY in WEB_EXCLUDED)
check("so it is not among the keys a browser may press",
      QR_KEY not in [key for key, _doc in web_keys()])

controls = Controls.__new__(Controls)
controls.out = io.StringIO()
controls.qr = None
check("a run with no web interface says so rather than going quiet",
      controls._qr() == "the web interface is not running")

pressed = []
controls.qr = lambda: pressed.append(True)
check("a run with one answers with the block and says nothing over it",
      controls._qr() is None)
check("and the block is what got called", pressed == [True])

finish("QR encoder")
