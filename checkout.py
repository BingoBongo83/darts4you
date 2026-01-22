
# Simple checkout utility.
# Provides a mapping for common checkouts up to 170 and a fallback search for 1-3 dart finishes that end on a double.

COMMON_CHECKOUTS = {
    170: ['T20','T20','BULL'],
    167: ['T20','T19','BULL'],
    164: ['T20','T18','BULL'],
    161: ['T20','T17','BULL'],
    160: ['T20','T20','D20'],
    158: ['T20','T20','D19'],
    157: ['T20','T19','D20'],
    156: ['T20','T20','D18'],
    155: ['T20','T19','D19'],
    154: ['T20','T18','D20'],
    153: ['T20','T19','D18'],
    152: ['T20','T20','D16'],
    151: ['T20','T17','D20'],
    150: ['T20','T18','D18'],
    # ... include more if desired
}

# All possible dart scores (value, multiplier, name)
SINGLES = [(i,1,f"S{i}") for i in range(1,21)]
DOUBLES = [(i,2,f"D{i}") for i in range(1,21)]
TRIPLES = [(i,3,f"T{i}") for i in range(1,21)]
BULL = [(25,1,"SBULL"), (25,2,"BULL")]  # single bull 25, double bull 50 ("BULL")
ALL_THROWS = TRIPLES + DOUBLES + SINGLES + BULL

def format_throw(t):
    val, mult, name = t
    if name:
        return name
    return f"{name}"

def find_checkout(score):
    # check common table first
    if score in COMMON_CHECKOUTS:
        return COMMON_CHECKOUTS[score]
    # Try to find 1, 2 or 3 dart checkouts ending on a double
    # A finishing throw must be a double (or bull 50)
    solutions = []
    # 1 dart finish - must be double
    for d in DOUBLES + [(25,2,"BULL")]:
        if d[0] * d[1] == score:
            return [d[2]]
    # 2 dart finish: first any throw, last double
    for first in ALL_THROWS:
        for last in DOUBLES + [(25,2,"BULL")]:
            if first[0]*first[1] + last[0]*last[1] == score:
                return [first[2], last[2]]
    # 3 dart finish: try all combinations (may be heavy but limited set)
    for first in ALL_THROWS:
        for second in ALL_THROWS:
            subtotal = first[0]*first[1] + second[0]*second[1]
            required = score - subtotal
            for last in DOUBLES + [(25,2,"BULL")]:
                if last[0]*last[1] == required:
                    return [first[2], second[2], last[2]]
    return None