"""Objectionable-content filter for Team text (App Review guideline 1.2,
docs/APP_REVIEW_BUILD7_CONTRACT.md §2).

Applied server-side when chat messages, task titles/descriptions/comments/
notes and announcements are created or edited. A matching word is MASKED
(first letter kept: "f***"), never rejected - so nothing a staff member types
is lost, and nothing objectionable is shown to anyone else.

Matching is on whole words only, case-insensitively, so "class", "assess",
"Scunthorpe", "cocktail" or "Spice" are never touched. Simple leetspeak is
undone first (a <- @/4, e <- 3, i <- 1/!, o <- 0, s <- $/5), but a "word"
needs at least one real letter, so prices and numbers ("455", "$5") never
match.

The word list is kept here in the repo on purpose: changing it is a code
review, not a setting someone can blank by accident.
"""

import re
from typing import FrozenSet, Optional, Tuple

# Lower-case, whole words. Inflections are listed explicitly rather than
# matched by prefix/suffix rules, which is what causes Scunthorpe-style false
# positives. Words with an everyday meaning in a grocery/kitchen ("cock"
# soup, "negro" beans, "rape"seed, a garden "hoe", the name "Randi") are left
# out on purpose.
WORDS: FrozenSet[str] = frozenset(
    """
    fuck fucks fucked fucker fuckers fuckin fucking fuckhead fuckface fuckoff motherfucker motherfuckers
    motherfucking mofo fck fcking fuk fuking fukin phuck
    shit shits shitty shitting shithead shithole bullshit horseshit dipshit
    bitch bitches bitchy bitching sonofabitch
    ass asses asshole assholes arse arsehole jackass dumbass smartass badass
    bastard bastards
    cunt cunts
    dick dicks dickhead dickheads
    cocksucker cocksuckers
    pussy pussies
    slut sluts slutty whore whores
    wanker wankers twat twats prick pricks
    piss pissing
    douche douchebag
    tits titties boobs
    nigger niggers nigga niggas
    faggot faggots fag fags dyke dykes tranny trannies
    retard retards retarded
    chink chinks spic spics kike kikes wetback wetbacks paki pakis gook gooks
    chutiya chutiye madarchod madarchood behenchod bhenchod bhosdike bhosdi gandu harami
    lavda lauda loda
    """.split()
)

# Leetspeak -> letter. "!" and "@" are only read as letters inside a word;
# leading/trailing ones are punctuation ("Hi!", "@ravi").
_LEET = str.maketrans({"@": "a", "4": "a", "3": "e", "1": "i", "!": "i", "0": "o", "$": "s", "5": "s"})

# A "word": letters/digits plus the leet symbols, no underscore.
_TOKEN = re.compile(r"(?:[^\W_]|[@$!])+")


def _normalize(token: str) -> str:
    return token.casefold().translate(_LEET)


def _is_bad(token: str) -> bool:
    if not any(ch.isalpha() for ch in token):
        return False  # numbers / prices / "!!!" are never words
    return _normalize(token) in WORDS


def _match(token: str) -> Optional[Tuple[int, int]]:
    """(start, end) within `token` of the offending word, or None. Tries the
    whole token, then without leading "@"/trailing "!" punctuation."""
    start, end = 0, len(token)
    while True:
        part = token[start:end]
        if part and _is_bad(part):
            return start, end
        if end > start and token[end - 1] == "!":
            end -= 1
        elif start < end and token[start] == "@":
            start += 1
        else:
            return None


def filter_text(text: Optional[str]) -> Tuple[Optional[str], bool]:
    """(masked text, whether anything was masked). None/"" pass through."""
    if not text:
        return text, False
    changed = False
    out = []
    last = 0
    for m in _TOKEN.finditer(text):
        hit = _match(m.group(0))
        if hit is None:
            continue
        a, b = m.start() + hit[0], m.start() + hit[1]
        out.append(text[last:a])
        out.append(text[a] + "*" * (b - a - 1))
        last = b
        changed = True
    if not changed:
        return text, False
    out.append(text[last:])
    return "".join(out), True


def clean(text: Optional[str]) -> Optional[str]:
    """filter_text without the flag."""
    return filter_text(text)[0]
