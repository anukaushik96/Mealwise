"""Parse a spoken-style grocery order into structured requests.

Handles input like:
    "500 gms paneer, 2 kg atta and 3 packets of maggi"
    "milk 1 litre, 12 eggs"

Produces OrderRequest(name, size, count) where size is normalised so it can
be compared against Swiggy's quantityDescription strings ("400 g",
"500 ml x 4", "12 Pieces").
"""

import re

WEIGHT, VOLUME, COUNT = "weight", "volume", "count"

# unit token -> (base multiplier, dimension). Base units: gram, millilitre, piece.
UNITS = {
    "g": (1, WEIGHT), "gm": (1, WEIGHT), "gms": (1, WEIGHT), "gram": (1, WEIGHT),
    "grams": (1, WEIGHT), "kg": (1000, WEIGHT), "kgs": (1000, WEIGHT),
    "kilo": (1000, WEIGHT), "kilos": (1000, WEIGHT), "kilogram": (1000, WEIGHT),
    "ml": (1, VOLUME), "millilitre": (1, VOLUME), "milliliter": (1, VOLUME),
    "l": (1000, VOLUME), "ltr": (1000, VOLUME), "ltrs": (1000, VOLUME),
    "litre": (1000, VOLUME), "liter": (1000, VOLUME), "litres": (1000, VOLUME),
    "liters": (1000, VOLUME),
    "pc": (1, COUNT), "pcs": (1, COUNT), "piece": (1, COUNT), "pieces": (1, COUNT),
    "unit": (1, COUNT), "units": (1, COUNT), "egg": (1, COUNT), "eggs": (1, COUNT),
    "dozen": (12, COUNT), "dozens": (12, COUNT),
}

# Words that mean "how many of the pack", not "how big is the pack".
PACK_WORDS = {"pack", "packs", "packet", "packets", "bottle", "bottles",
              "box", "boxes", "tin", "tins", "can", "cans", "bag", "bags"}

_NOISE = {"of", "a", "an", "the", "some", "please", "get", "buy", "order",
          "add", "want", "need", "i", "me", "and"}

# "eggs" is both a unit ("12 eggs") and the product itself, unlike "kg" or
# "litre" which are only ever measurements.
_PRODUCT_UNITS = {"egg", "eggs"}
_MEASURE_ONLY = set(UNITS) - _PRODUCT_UNITS

WORD_NUMBERS = {
    "half": 0.5, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "couple": 2, "quarter": 0.25,
}

_SPLIT = re.compile(r"\s*(?:,|;|\band\b|\+|&|\n)\s*", re.I)
# "... x 2 units" / "... x 3" - an explicit multiplier at the end.
_TRAIL_X = re.compile(r"[x*]\s*(\d+)\s*(?:units?|nos?|pcs?|pieces?|packs?|packets?)?\s*$", re.I)
# "... 2 units" / "... 2 nos" - no x, but an explicit counting word. "pieces"
# is deliberately excluded so "12 eggs"/"6 pieces" stay pack SIZES, not counts.
_TRAIL_UNITS = re.compile(r"(\d+)\s*(?:units?|nos?)\s*$", re.I)

_QTY = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)(\s*)([a-z]+)?", re.I)

# A count written as a bare number before the product name ("2 maggi"). Capped
# low on purpose: the higher the number, the likelier it is part of a brand
# name ("sunfeast 50 50") than a quantity someone typed.
_MAX_BARE_COUNT = 20


class Size(object):
    """A normalised pack size, e.g. 500 g or 1000 ml or 12 pieces."""

    def __init__(self, magnitude, dimension, pack=1):
        self.magnitude = float(magnitude)   # per pack, in base units
        self.dimension = dimension
        self.pack = int(pack)               # packs bundled in one SKU

    @property
    def total(self):
        return self.magnitude * self.pack

    def __repr__(self):
        unit = {WEIGHT: "g", VOLUME: "ml", COUNT: "pc"}[self.dimension]
        base = "%g %s" % (self.magnitude, unit)
        return base if self.pack == 1 else "%s x %d" % (base, self.pack)

    def __eq__(self, other):
        return (isinstance(other, Size) and self.dimension == other.dimension
                and abs(self.total - other.total) < 1e-6)


class OrderRequest(object):
    def __init__(self, name, size=None, count=1, raw=""):
        self.name = name
        self.size = size
        self.count = count
        self.raw = raw

    def __repr__(self):
        bits = [self.name]
        if self.size:
            bits.append(str(self.size))
        if self.count != 1:
            bits.append("x%d" % self.count)
        return " ".join(bits)


def parse_size_text(text):
    """Parse a Swiggy quantityDescription like "500 ml x 4" or "12 Pieces"."""
    if not text:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)\s*([A-Za-z]+)", text)
    if not match:
        return None
    unit = match.group(2).lower()
    if unit not in UNITS:
        return None
    multiplier, dimension = UNITS[unit]
    pack = 1
    bundle = re.search(r"[xX*]\s*(\d+)", text[match.end():])
    if bundle:
        pack = int(bundle.group(1))
    return Size(float(match.group(1)) * multiplier, dimension, pack)


def _normalise(segment):
    """Turn spoken quantities into digits so one regex can find them all."""
    tokens = re.split(r"(\s+)", segment)
    out = []
    for i, token in enumerate(tokens):
        low = token.lower().strip()
        if low in WORD_NUMBERS:
            out.append("%g" % WORD_NUMBERS[low])
            continue
        if low in ("a", "an"):
            # "a dozen eggs" / "a kg rice" means one - but "a bread" does not.
            nxt = ""
            for follow in tokens[i + 1:]:
                if follow.strip():
                    nxt = follow.strip().lower()
                    break
            if nxt in UNITS or nxt in PACK_WORDS:
                out.append("1")
                continue
        out.append(token)
    return "".join(out)


def _parse_segment(segment):
    segment = _normalise(segment.strip())
    if not segment:
        return None

    # A leading multiplier: "2 x 500 ml milk". Named distinctly from the
    # per-unit multiplier below, which is a different thing entirely.
    lead_count = 1
    lead = re.match(r"\s*(\d+)\s*[xX*]\s+(?=\d)", segment)
    if lead:
        lead_count = int(lead.group(1))
        segment = segment[lead.end():]

    # A trailing count: "amul milk @ 1 L x 2 units", "atta 1 kg 3 nos".
    for pattern in (_TRAIL_X, _TRAIL_UNITS):
        trail = pattern.search(segment)
        if trail:
            lead_count = int(trail.group(1))
            segment = segment[:trail.start()]
            break

    size = None
    count = 1
    consumed = []

    for match in _QTY.finditer(segment):
        value = float(match.group(1))
        gap = match.group(2) or ""
        word = (match.group(3) or "").lower()

        if word in UNITS:
            multiplier, dimension = UNITS[word]
            # "12 eggs" is a count, but only if we have no better size yet.
            if size is None:
                size = Size(value * multiplier, dimension)
            # For "eggs" the word is also the product name, so eat only the
            # number and leave the noun behind.
            consumed.append(match.span(1) if word in _PRODUCT_UNITS else match.span())
        elif word in PACK_WORDS:
            count = int(value)
            consumed.append(match.span())
        elif not word:
            # A trailing bare number: "maggi 2".
            if value.is_integer() and 1 <= value <= 50:
                count = int(value)
                consumed.append(match.span())
        elif gap and value.is_integer() and 1 <= value <= _MAX_BARE_COUNT:
            # A number, a space, then a word that is not a measurement:
            # "2 maggi", "3 packets" having been handled above. Without the
            # space this would eat the "7" of "7up", so the space is the whole
            # test. Only the digits are consumed - the word is the product.
            #
            # Until this existed, "2 maggi" searched Swiggy for "2 maggi" and
            # ordered one of it: the digit leaked into the query and the
            # quantity vanished silently, which is the exact failure section
            # 1.10 of INSTAMART_NOTES.md was written about.
            count = int(value)
            consumed.append(match.span(1))

    # Strip the quantity spans out; whatever is left is the product name.
    text = segment
    for start, end in reversed(consumed):
        text = text[:start] + " " + text[end:]

    def keep(word, drop_units):
        low = word.lower()
        if not word or low in _NOISE or low in PACK_WORDS:
            return False
        if low in ("x", "@"):
            return False  # leftover multiplier/at sign, never part of a name
        return not (drop_units and low in UNITS)

    if lead_count > 1:
        count = lead_count

    # Keep only tokens containing an actual letter or digit, so stray
    # punctuation from "milk - 1 litre" does not end up in the name.
    tokens = [t for t in re.split(r"[^\w%&'-]+", text) if re.search(r"[\w]", t)]
    words = [w for w in tokens if keep(w, drop_units=True)]
    if not words:
        # Everything looked like a unit - retry keeping words that can name a
        # product ("12 eggs" -> "eggs"), but still drop pure measurements.
        words = [w for w in tokens
                 if keep(w, drop_units=False) and w.lower() not in _MEASURE_ONLY
                 and not w.replace(".", "").isdigit()]
    name = " ".join(words).strip()
    if not name:
        return None
    return OrderRequest(name, size, count, raw=segment)


def parse_order(sentence):
    """Split a sentence into one OrderRequest per item."""
    requests = []
    for segment in _SPLIT.split(sentence or ""):
        parsed = _parse_segment(segment)
        if parsed:
            requests.append(parsed)
    return requests


def rank_variants(request, rows):
    """Order candidate rows by how well they match the requested size, then price.

    Rows whose dimension is wrong entirely (grams asked, millilitres offered)
    are dropped, not ranked last - they are not the same product shape.
    Returns [(score, price, row, size)]; score 0.0 means an exact size match.
    """
    ranked = []
    for row in rows:
        size = parse_size_text(row["variant"])
        score = score_variant(request, size)
        if score is None:
            continue
        ranked.append((score, row["price"], row, size))
    ranked.sort(key=lambda r: (round(r[0], 3), r[1]))
    return ranked


def score_variant(request, variant_size):
    """Rank a variant against the requested size. Lower is better; None = wrong kind."""
    if request.size is None or variant_size is None:
        return 1.0  # no size asked for (or none parseable) - neutral
    if request.size.dimension != variant_size.dimension:
        return None
    wanted, got = request.size.total, variant_size.total
    if wanted <= 0 or got <= 0:
        return None
    ratio = got / wanted if got >= wanted else wanted / got
    return ratio - 1.0  # 0.0 == exact match
