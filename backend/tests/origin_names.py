"""Names of the store this product was first built for, kept out of the
product (KloudC RetailOps final pass).

The names themselves are not written anywhere in the repository: the list
below holds only salted SHA-256 hashes of the normalised words (lower case,
letters and digits only, neighbouring words joined). A line is a hit when one
word, or two or three neighbouring words joined, hashes to an EXACT entry, or
when a word starts or ends with a SUBSTRING entry.

Used by tests/test_no_origin_names.py (every tracked file) and by the tests
that check generated output (branding, demo data, a fresh store, glossary).
The same hashes are in the app (test/identity/origin_names.dart) and the
website (frontend/src/test/originNames.mjs).
"""

import hashlib
import re
from typing import Iterable, List, Tuple

SALT = "kloudc-retailops/origin-names/v1:"

# Whole words (or 2-3 neighbouring words joined): store, sister kitchen,
# town, street address, zip, phone, private host and staff first names.
EXACT = frozenset(
    {
        "3f6f46dfbfd966b817866305f891fe11c0d658ddb507fb1e866bef705d410e5d",
        "1f343dfc57546a57e777995989f38fa50427f097f0d8cef50b0dafe500dbcd4b",
        "92dcbb59736cd79706bbaed3511de6215a22e222cb5f84b2b731d485afa8587a",
        "ebc5d0501d9177f52525d20d5df266cbdf86d33bbbc2b9be5a9783e445e8ddb4",
        "8937660eb6a6d1cd8be7b366a3226890c0b7edd6e5dff119c24e2534c9dc3ae5",
        "3504ffcb509fbbc51e49be7e2f078c0d8406525d441382be7328083849b38ee9",
        "14e80ffc703b6988de1b634a2d785293baff8faef3ad407d8bc765db8b8090ac",
        "1e071899c73f0fbe69de24511ec5517b84a8f22205952799f5155b3029f2c58e",
        "18ca962659e08e13477118dbb1fc7b2a87476780399b4a06a61ffc560c1b8dd7",
        "0f4a0e05db73c7f56685f848d5e1dc34921cd1d8264c0e20fd40fd5d4705daf9",
        "df019f9df438a0974afaa27200ee4d7629989160e5c58b971eeaf4c4802e519b",
        "73b81869a58fd7b80d854b2a1009d59dafb7aa01727ad1e8388dd6f818ba6cfd",
        "cd3cdc8755045be5456b98bdc5f15f06c49eb9befc67eb9a8c7e4f112a5a672f",
        "50c34d8ea9c37965db00dc0b992be77c79d9fabc3d63db7ddaf176a6bf2d6429",
    }
)
# (length, hash): also a hit at the start or the end of a longer word (the
# store's name inside a host name or a user name).
SUBSTRING = frozenset({(9, "5012f887b1df1014f785c4dd27e2b386517cb85c657a80441b95c2b3db2a7ba4")})

_LENGTHS = frozenset(len_ for len_, _ in SUBSTRING)
_WORD = re.compile(r"[a-z0-9]+")
# A backslash escape in source text ("\\nName") is a word break, not a letter.
_ESCAPE = re.compile(r"\\[a-z]")
# Every hashed entry is 3 to 13 characters long once joined.
_MIN, _MAX = 3, 13


def _h(s: str) -> str:
    return hashlib.sha256((SALT + s).encode("utf-8")).hexdigest()


def _candidates(words: List[str]) -> Iterable[str]:
    """Each word, and 2-3 neighbouring words joined when every one of them is
    at least 3 characters long and the result at least 6 (so "x's ai" or
    "_s(s), ai_enabled" never join into a short name)."""
    n = len(words)
    for i in range(n):
        yield words[i]
        joined = words[i]
        for j in range(i + 1, min(i + 3, n)):
            if len(words[j]) < 3 or len(words[i]) < 3:
                break
            joined += words[j]
            if len(joined) >= 6:
                yield joined


def line_has_origin_name(line: str) -> bool:
    words = _WORD.findall(_ESCAPE.sub(" ", line).lower())
    for cand in _candidates(words):
        if _MIN <= len(cand) <= _MAX and _h(cand) in EXACT:
            return True
        for length in _LENGTHS:
            if len(cand) >= length:
                for part in {cand[:length], cand[-length:]}:
                    if (length, _h(part)) in SUBSTRING:
                        return True
    return False


def find_origin_names(text: str) -> List[Tuple[int, str]]:
    """(line number, line) for every line of ``text`` with a hit."""
    return [(n, line) for n, line in enumerate((text or "").splitlines(), 1) if line_has_origin_name(line)]


def has_origin_name(text: str) -> bool:
    return any(line_has_origin_name(line) for line in (text or "").splitlines())
