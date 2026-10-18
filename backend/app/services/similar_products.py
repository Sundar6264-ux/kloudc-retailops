"""Similar products for Supplier Projection ("Deep Grated Coconut" ->
"Daily Delight Grated Coconut", "Laxmi Grated Coconut 1.2kg", ...).

Fully offline: no LLM or web call at runtime. Everything the matcher knows
lives in versioned data files under `app/data/similar_products/`, learned
from the store's real catalog and Indian / Pakistani / South Indian grocery
glossaries when this was built:

- `brands.txt`   brand names and their spellings/abbreviations (LX. = Laxmi,
                 DD = Daily Delight, JF = Jain Foods, KX = Kitchen Xpress...).
- `synonyms.txt` spelling / transliteration variants mapped to one canonical
                 token (toor/tuvar/arhar, jeera/cumin, rava/sooji/semolina,
                 grated/shredded coconut...).
- `rules.json`   stopwords, weak marketing words, product-type words that
                 conflict (powder vs whole, milk vs grated...), scoring knobs.

How a match is scored:
1. Each name is normalised: pack sizes ("7oz", "2 lb", "150gx3", "20pcs")
   are cut out (kept separately as the item's size), the brand is detected
   and removed, synonyms are mapped to canonical tokens and plurals folded.
   What's left is the item's "product type" tokens.
2. Tokens are weighted by rarity (IDF) across the catalog. Weak words
   ("premium", "fresh", "organic"...) and words only one brand ever uses
   (a product line name) count much less.
3. Score = cosine similarity of the two weighted token sets, then adjusted:
   a small boost for the same category/subcategory, a penalty for a
   different department (Retail vs Produce vs Halal Meat), for frozen vs
   shelf, and for conflicting product-type words (coconut MILK vs GRATED
   coconut, turmeric POWDER vs WHOLE).
4. The top matches above `min_score` are returned with a short reason.

The index is built once per inventory upload (~0.3s for ~5,000 items) and
cached, so a lookup is a few milliseconds.
"""

import datetime as dt
import json
import logging
import math
import re
import threading
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import InventorySnapshot, Order
from app.services.item_vendors import aligned_item_codes, split_field

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "similar_products"


# ---------------------------------------------------------------------------
# Vocabulary (data files)
# ---------------------------------------------------------------------------


def _normalize_text(text: str) -> str:
    """Lowercase, split glued CamelCase words ("ShredCoconut"), drop
    apostrophes (haldiram's -> haldirams) and turn other punctuation into
    spaces. Digits and letters are kept together for size parsing later."""
    text = re.sub(r"(?<=[a-z]{2})(?=[A-Z][a-z]{2})", " ", text)
    text = text.lower().replace("’", "'").replace("'", "")
    return text


def _words(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", _normalize_text(text))


@dataclass
class Vocab:
    version: str
    brands: Dict[Tuple[str, ...], Tuple[str, bool]]  # alias tokens -> (canonical, leading_only)
    max_brand_len: int
    synonyms: Dict[Tuple[str, ...], Tuple[str, ...]]  # variant tokens -> canonical tokens
    max_synonym_len: int
    stopwords: Set[str]
    weak_words: Set[str]
    type_tokens: Set[str]
    frozen_words: Set[str]
    trailing_modifiers: Set[str]
    vendor_prefixes: Set[str]
    non_brand_leading: Set[str]
    plural_exceptions: Set[str]
    excluded_groups: Set[str]
    scoring: dict
    auto_brand: dict
    known_tokens: Set[str] = field(default_factory=set)
    brand_products: Dict[str, Tuple[str, ...]] = field(default_factory=dict)  # brand (lower) -> implied tokens
    type_groups: Dict[str, str] = field(default_factory=dict)  # type token -> equivalence key
    typo_protected: Set[str] = field(default_factory=set)


def load_vocab(data_dir: Optional[Path] = None) -> Vocab:
    return _load_vocab_cached(str(data_dir or DATA_DIR))


@lru_cache(maxsize=4)
def _load_vocab_cached(data_dir: str) -> Vocab:
    base = Path(data_dir)
    rules = json.loads((base / "rules.json").read_text(encoding="utf-8"))

    brands: Dict[Tuple[str, ...], Tuple[str, bool]] = {}
    for line in (base / "brands.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        leading_only = line.startswith("^")
        parts = [p.strip() for p in line.lstrip("^").split("|")]
        canonical = parts[0]
        # The canonical name is only an alias when no aliases are listed
        # ("Lotus | lotus biscoff" must not claim "Lotus Roots").
        for alias in parts[1:] or parts:
            key = tuple(_words(alias))
            if key and key not in brands:
                brands[key] = (canonical, leading_only)

    synonyms: Dict[Tuple[str, ...], Tuple[str, ...]] = {}
    for line in (base / "synonyms.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        canonical_text, variants_text = line.split("=", 1)
        canonical = tuple(_words(canonical_text))
        for variant in variants_text.split(","):
            key = tuple(_words(variant))
            if key and key != canonical and key not in synonyms:
                synonyms[key] = canonical

    brand_products: Dict[str, Tuple[str, ...]] = {}
    path = base / "brand_products.txt"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            brand_text, product_text = line.split("=", 1)
            brand_products[brand_text.strip().lower()] = tuple(_words(product_text))

    type_groups: Dict[str, str] = {}
    for group in rules.get("type_equivalents", []):
        for t in group:
            type_groups[t] = group[0]

    known = set()
    for canonical in synonyms.values():
        known.update(canonical)
    for key in synonyms:
        if len(key) == 1:
            known.add(key[0])

    return Vocab(
        version=str(rules.get("version")),
        brands=brands,
        max_brand_len=max((len(k) for k in brands), default=1),
        synonyms=synonyms,
        max_synonym_len=max((len(k) for k in synonyms), default=1),
        stopwords=set(rules["stopwords"]),
        weak_words=set(rules["weak_words"]),
        type_tokens=set(rules["type_tokens"]),
        frozen_words=set(rules["frozen_words"]),
        trailing_modifiers=set(rules.get("trailing_modifiers", [])),
        vendor_prefixes=set(rules["vendor_prefixes"]),
        non_brand_leading=set(rules["non_brand_leading_words"]) | known,
        plural_exceptions=set(rules["plural_exceptions"]),
        excluded_groups={g.upper() for g in rules["excluded_category_groups"]},
        scoring=rules["scoring"],
        auto_brand=rules["brand_auto_detect"],
        known_tokens=known,
        brand_products=brand_products,
        type_groups=type_groups,
        typo_protected=set(rules.get("typo_protected", [])),
    )


# ---------------------------------------------------------------------------
# Name parsing
# ---------------------------------------------------------------------------

_UNIT_TO_BASE = {
    # weight -> grams
    "kg": ("g", 1000.0), "kgs": ("g", 1000.0), "kilo": ("g", 1000.0), "kilos": ("g", 1000.0),
    "g": ("g", 1.0), "gm": ("g", 1.0), "gms": ("g", 1.0), "gr": ("g", 1.0), "grm": ("g", 1.0),
    "grms": ("g", 1.0), "gram": ("g", 1.0), "grams": ("g", 1.0), "gramm": ("g", 1.0),
    "lb": ("g", 453.592), "lbs": ("g", 453.592), "pound": ("g", 453.592), "pounds": ("g", 453.592),
    "oz": ("g", 28.3495), "ounce": ("g", 28.3495), "ounces": ("g", 28.3495),
    # volume -> ml
    "floz": ("ml", 29.5735), "fl oz": ("ml", 29.5735), "ml": ("ml", 1.0), "mls": ("ml", 1.0),
    "l": ("ml", 1000.0), "ltr": ("ml", 1000.0), "ltrs": ("ml", 1000.0), "lt": ("ml", 1000.0),
    "litre": ("ml", 1000.0), "liter": ("ml", 1000.0), "litres": ("ml", 1000.0), "liters": ("ml", 1000.0),
    "gal": ("ml", 3785.41), "gallon": ("ml", 3785.41),
    # counts
    "pc": ("pc", 1.0), "pcs": ("pc", 1.0), "piece": ("pc", 1.0), "pieces": ("pc", 1.0),
    "ct": ("pc", 1.0), "count": ("pc", 1.0), "nos": ("pc", 1.0), "sheets": ("pc", 1.0),
    "bags": ("pc", 1.0), "pk": ("pc", 1.0), "pack": ("pc", 1.0), "packs": ("pc", 1.0),
}
_UNIT_ALT = "|".join(sorted((re.escape(u).replace("\\ ", r"\.?\s*") for u in _UNIT_TO_BASE), key=len, reverse=True))
# "150gx3" (3 packs of 150g) still reads as 150g.
_SIZE_RE = re.compile(r"(?<![0-9.])(\d+(?:\.\d+)?)\s*(" + _UNIT_ALT + r")(?=x\s*\d|[^a-z]|$)")
_MULTI_RE = re.compile(r"(?<![a-z0-9])(?:x\s*\d+|\d+\s*x)(?![a-z0-9])")
_WEIGHED_RE = re.compile(r"\b(?:in|per)\s+(?:lbs?|kgs?)\b|\bby\s+weight\b")


@dataclass
class Parsed:
    brand: Optional[str]
    size: Optional[str]
    size_base: Optional[Tuple[str, float]]  # ("g"|"ml"|"pc", amount)
    tokens: List[str]  # canonical product-type tokens, in order, de-duplicated
    frozen_word: bool
    brand_tokens: Tuple[str, ...] = ()  # brand words kept as product words ("Parle-G 376g")


def _parse_size(text: str) -> Tuple[str, Optional[str], Optional[Tuple[str, float]]]:
    """Cut pack sizes out of a normalised name. Returns (rest, display size,
    (base unit, amount)) - the first weight/volume found wins for the base."""
    found = []

    def _take(match):
        found.append(match)
        return " "

    text = re.sub(r"\bb\d+g\d+\b", " ", text)  # "B4G1" offers, not a 4g pack
    rest = _SIZE_RE.sub(_take, text)
    rest = _MULTI_RE.sub(" ", rest)
    weighed = bool(_WEIGHED_RE.search(rest))
    if weighed:
        rest = _WEIGHED_RE.sub(" ", rest)
    display = None
    base = None
    for match in found:
        amount = float(match.group(1))
        unit = re.sub(r"[.\s]", "", match.group(2))
        unit_key = "floz" if unit == "floz" else unit
        base_unit, factor = _UNIT_TO_BASE.get(unit_key, _UNIT_TO_BASE.get(match.group(2), ("", 0.0)))
        if not base_unit:
            continue
        if base is None or (base[0] == "pc" and base_unit != "pc"):
            base = (base_unit, round(amount * factor, 1))
            number = match.group(1).rstrip("0").rstrip(".") if "." in match.group(1) else match.group(1)
            display = f"{number}{unit_key}"
    if display is None and weighed:
        display = "per lb"
        base = ("lb", 1.0)
    return rest, display, base


def _stem(token: str, vocab: Vocab, word_set: Optional[Set[str]] = None) -> str:
    """Fold a plural to its singular - only when the singular is itself a
    word the catalog (or the synonym table) uses, so "madras" or "khas"
    aren't chopped into non-words."""
    if len(token) <= 3 or token in vocab.plural_exceptions or token in vocab.known_tokens:
        return token
    stem = _plural_stem(token)
    if stem != token and word_set is not None and stem not in word_set and stem not in vocab.known_tokens:
        return token
    return stem


def _plural_stem(token: str) -> str:
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith("ves") and len(token) > 4:
        return token[:-3] + "f"
    if token.endswith(("oes", "shes", "ches", "xes")):
        return token[:-2]
    if token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def _spelling_variant(a: str, b: str) -> bool:
    """True when two words differ the way transliterated spellings do: one
    extra vowel or "h" (masla/masala, khurma/kurma), a doubled letter
    (idlli/idli, muton/mutton) or two neighbouring letters swapped
    (curshed/crushed). Never at the first or last letter - "makhan" (butter)
    and "makhana" (fox nuts), "doodh" and "doodhi" are different things."""
    if a[0] != b[0] or a == b:
        return False
    if len(a) == len(b):
        diffs = [i for i in range(len(a)) if a[i] != b[i]]
        return (
            len(diffs) == 2
            and diffs[1] == diffs[0] + 1
            and diffs[1] < len(a) - 1
            and a[diffs[0]] == b[diffs[1]]
            and a[diffs[1]] == b[diffs[0]]
        )
    long_word, short_word = (a, b) if len(a) > len(b) else (b, a)
    if len(long_word) - len(short_word) != 1:
        return False
    for i in range(len(long_word)):
        if long_word[:i] + long_word[i + 1 :] == short_word:
            if i == 0 or i == len(long_word) - 1:
                continue
            extra = long_word[i]
            if extra in "aeiouh" or extra == long_word[i - 1] or extra == long_word[i + 1]:
                return True
    return False


def _match_phrase(tokens: List[str], start: int, table: dict, max_len: int):
    for length in range(min(max_len, len(tokens) - start), 0, -1):
        key = tuple(tokens[start : start + length])
        if key in table:
            return length, table[key]
    return 0, None


def parse_name(
    name: str,
    vocab: Vocab,
    auto_brands: Optional[Dict[str, str]] = None,
    typo_map: Optional[Dict[str, str]] = None,
    word_set: Optional[Set[str]] = None,
) -> Parsed:
    text = _normalize_text(name or "")
    text, size, size_base = _parse_size(text)
    raw = re.findall(r"[a-z0-9]+", text)

    if len(raw) > 1 and raw[0] in vocab.vendor_prefixes:
        raw = raw[1:]

    # Brand: a leading brand phrase (any brand), or a distinctive brand
    # anywhere in the name. Every brand phrase found is removed so it never
    # counts as a product word.
    brand = None
    kept: List[str] = []
    i = 0
    while i < len(raw):
        length, hit = _match_phrase(raw, i, vocab.brands, vocab.max_brand_len)
        if hit is not None and (i == 0 or not hit[1] or (brand is not None and not kept)):
            if brand is None:
                brand = hit[0]
            i += length
            continue
        if i == 0 and auto_brands and raw[0] in auto_brands:
            brand = auto_brands[raw[0]]
            i += 1
            continue
        kept.append(raw[i])
        i += 1

    words = [w for w in kept if not any(c.isdigit() for c in w) and w not in vocab.stopwords]
    if typo_map:
        words = [typo_map.get(w, w) for w in words]

    tokens: List[str] = []
    i = 0
    while i < len(words):
        length, hit = _match_phrase(words, i, vocab.synonyms, vocab.max_synonym_len)
        if hit is not None:
            tokens.extend(hit)
            i += length
            continue
        stemmed = _stem(words[i], vocab, word_set)
        single = vocab.synonyms.get((stemmed,))
        tokens.extend(single if single else (stemmed,))
        i += 1

    seen: Set[str] = set()
    ordered = []
    for t in tokens:
        if t and t not in seen and t not in vocab.stopwords:
            seen.add(t)
            ordered.append(t)
    brand_tokens: Tuple[str, ...] = ()
    if brand is not None and all(t in vocab.weak_words for t in ordered):
        # The brand IS the product ("Parle-G 376g", "Cycle 3 in 1"): keep its
        # words, or there'd be nothing to tell it apart from any biscuit.
        brand_tokens = tuple(w for w in _words(brand) if w not in vocab.stopwords and w not in seen)
        ordered = list(brand_tokens) + ordered
        seen.update(brand_tokens)
    if brand is not None and not any(t in vocab.type_tokens for t in ordered):
        # "Nescafe Gold" -> coffee, "Shahi Kulfi Mango" -> kulfi.
        for t in vocab.brand_products.get(brand.lower(), ()):
            if t not in seen:
                seen.add(t)
                ordered.append(t)
    frozen_word = any(t in vocab.frozen_words for t in ordered)
    return Parsed(
        brand=brand, size=size, size_base=size_base, tokens=ordered, frozen_word=frozen_word, brand_tokens=brand_tokens
    )


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------


@dataclass
class CatalogItem:
    item_id: str
    name: str
    category_group: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    supplier: Optional[str] = None
    supplier_item_id: Optional[str] = None
    price: Optional[float] = None
    cost: Optional[float] = None
    qty: Optional[float] = None

    @property
    def is_container_row(self) -> bool:
        return self.price is None and not (self.supplier or "").strip()


@dataclass
class Match:
    item: CatalogItem
    parsed: Parsed
    score: float
    reason: str


def split_multi(raw: Optional[str]) -> List[str]:
    return split_field(raw)  # services/item_vendors.py (M1 MT5)


# A bad Toast export (seen in the Sep 20 2026 upload) put item codes and a
# placeholder into the Supplier column: "ACT_SUP_UPDT", "30128", "5RR2",
# "8908016489500". Real vendor names have letters and, when they have a
# digit, a space ("Singh & Singh", "SpiceTown - Street Kitchen").
_JUNK_VENDOR_RE = re.compile(r"^(?=\S*\d)\S+$|^[A-Z0-9]+(?:_[A-Z0-9]+)+$")


def is_junk_vendor(name: Optional[str]) -> bool:
    return not (name or "").strip() or bool(_JUNK_VENDOR_RE.match(name.strip()))


def split_aligned(raw: Optional[str]) -> List[str]:
    """Split a ";"-separated Toast field KEEPING empty slots, so position i
    still lines up with the other field ("Raja Foods;RBest;Restaurant Depot"
    with codes ";2524;856977005124" - Raja Foods has no code)."""
    return split_field(raw, keep_empty=True)


def aligned_codes(supplier: Optional[str], supplier_item_id: Optional[str]) -> Optional[List[Optional[str]]]:
    """The item code for each slot of `supplier` (None where empty), or None
    when the two fields don't line up."""
    return aligned_item_codes(supplier, supplier_item_id)  # services/item_vendors.py (M1 MT5)


def vendor_refs(supplier: Optional[str], supplier_item_id: Optional[str]) -> List[dict]:
    """Every vendor on an item with its own item code. `supplier_item_id` is
    semicolon-separated in the same order as `supplier` (empty slots
    included); when the two lists don't line up, a single code is kept only
    for a single vendor. Junk vendor values (see is_junk_vendor) are left
    out."""
    vendors = split_aligned(supplier)
    codes = aligned_codes(supplier, supplier_item_id)
    named = [v for v in vendors if v]
    real_codes = split_multi(supplier_item_id)
    out: List[dict] = []
    by_key: Dict[str, dict] = {}
    for i, name in enumerate(vendors):
        if is_junk_vendor(name):
            continue
        code = None
        if codes is not None:
            code = codes[i]
        elif len(named) == 1 and len(real_codes) == 1:
            code = real_codes[0]
        seen = by_key.get(name.lower())
        if seen is not None:  # "House of Spices;House of Spices" - listed twice in Toast
            seen["supplier_item_id"] = seen["supplier_item_id"] or code
            continue
        by_key[name.lower()] = ref = {"name": name, "supplier_item_id": code}
        out.append(ref)
    return out


def other_vendor_refs(supplier: Optional[str], supplier_item_id: Optional[str], selected: str) -> List[dict]:
    """Vendors on an item other than `selected` (case-insensitive), in the
    order Toast lists them."""
    sel = (selected or "").strip().lower()
    seen = set()
    out = []
    for ref in vendor_refs(supplier, supplier_item_id):
        key = ref["name"].lower()
        if key == sel or key in seen:
            continue
        seen.add(key)
        out.append(ref)
    return out


_FRESH_DEPARTMENTS = {"PRODUCE", "HALAL MEAT", "FISH MARKET", "FRESH FLOWERS"}


def _department(item: CatalogItem) -> str:
    group = (item.category_group or "").strip().upper()
    category = (item.category or "").strip().upper()
    if category in ("PLANTS", "PLANTS & SEEDS"):
        return "PLANTS"  # saplings to grow ("Okra (USA) Sampling"), not food
    if group == "RETAIL" and category == "PRODUCE":
        # Toast files some fresh fruit and veg under RETAIL / Produce: it's
        # still the produce department, not a packaged good.
        return "PRODUCE"
    return group


def _is_fresh(item: CatalogItem) -> bool:
    return _department(item) in _FRESH_DEPARTMENTS


def _is_frozen_category(item: CatalogItem) -> bool:
    text = f"{item.category or ''} {item.subcategory or ''}".lower()
    return "frozen" in text or "ice cream" in text


class SimilarIndex:
    """Parsed, weighted catalog. Build once; query many times."""

    def __init__(self, items: Iterable[CatalogItem], vocab: Optional[Vocab] = None):
        self.vocab = vocab or load_vocab()
        all_items = [i for i in items if (i.name or "").strip()]
        self.by_id: Dict[str, CatalogItem] = {i.item_id: i for i in all_items}

        # Container duplicates: a bare row (no price, no supplier) sharing
        # its name with a priced row - its stock belongs to that product.
        bare: Dict[str, List[CatalogItem]] = defaultdict(list)
        for it in all_items:
            if it.is_container_row and it.qty is not None:
                bare[it.name].append(it)
        self.container_by_name: Dict[str, Tuple[str, float]] = {
            name: (rows[0].item_id, rows[0].qty) for name, rows in bare.items() if len(rows) == 1
        }

        self.items: List[CatalogItem] = [
            it
            for it in all_items
            if not it.is_container_row and _department(it) not in self.vocab.excluded_groups
        ]
        self.auto_brands = self._detect_auto_brands(self.items)
        self.typo_map = self._build_typo_map(self.items)
        word_set = {w for it in self.items for w in _words(it.name)}
        self.parsed: Dict[str, Parsed] = {
            it.item_id: parse_name(it.name, self.vocab, self.auto_brands, self.typo_map, word_set) for it in all_items
        }

        n = max(1, len(self.items))
        df: Counter = Counter()
        brands_per_token: Dict[str, Set[str]] = defaultdict(set)
        self.postings: Dict[str, List[str]] = defaultdict(list)
        self.by_brand: Dict[Optional[str], List[str]] = defaultdict(list)
        brand_words: Set[str] = set()
        for it in self.items:
            p = self.parsed[it.item_id]
            self.by_brand[p.brand].append(it.item_id)
            brand_words.update(p.brand_tokens)
            for t in p.tokens:
                df[t] += 1
                brands_per_token[t].add(p.brand or f"?{it.item_id}")
                self.postings[t].append(it.item_id)
        sc = self.vocab.scoring
        self.weights: Dict[str, float] = {}
        for t, count in df.items():
            w = math.log(1.0 + n / count)
            if t in self.vocab.weak_words:
                w *= sc["weak_word_factor"]
            elif count >= 2 and len(brands_per_token[t]) == 1 and not next(iter(brands_per_token[t])).startswith("?") \
                    and t not in self.vocab.known_tokens and t not in self.vocab.type_tokens and t not in brand_words:
                # Only one brand ever uses this word: a product-line name,
                # not a kind of product.
                w *= sc["single_brand_token_factor"]
            elif count == 1 and t not in self.vocab.known_tokens and t not in self.vocab.type_tokens \
                    and t not in brand_words:
                # A word no other item uses ("Wet Tamarind", "Red Gel
                # Maxfresh"): a product-line name or a one-off descriptor.
                # At full rarity weight it drowns out the product word the
                # two names share, so nothing is ever similar.
                w *= sc.get("unique_token_factor", 1.0)
            self.weights[t] = w
        self._default_weight = math.log(1.0 + n)
        self.norms: Dict[str, float] = {
            item_id: self._norm(p.tokens) for item_id, p in self.parsed.items()
        }
        self._memo: Dict[Tuple[str, int], List[Match]] = {}
        self._lock = threading.Lock()

    # -- construction helpers ------------------------------------------------

    def _detect_auto_brands(self, items: List[CatalogItem]) -> Dict[str, str]:
        """Leading words used like brands (first word of 3+ items, rarely
        anywhere else) that aren't in brands.txt yet - so a new brand in a
        future upload is still treated as a brand."""
        cfg = self.vocab.auto_brand
        leading: Counter = Counter()
        anywhere: Counter = Counter()
        for it in items:
            words = _words(it.name)
            if len(words) > 1 and words[0] in self.vocab.vendor_prefixes:
                words = words[1:]
            if not words:
                continue
            length, hit = _match_phrase(words, 0, self.vocab.brands, self.vocab.max_brand_len)
            if hit is not None:
                continue
            leading[words[0]] += 1
            for w in set(words):
                anywhere[w] += 1
        out = {}
        for w, count in leading.items():
            if count < cfg["min_leading_count"] or w in self.vocab.non_brand_leading or w in self.vocab.stopwords:
                continue
            if any(c.isdigit() for c in w) or len(w) < 2:
                continue
            nonleading = anywhere[w] - count
            if nonleading <= cfg["max_nonleading_share"] * count:
                out[w] = w.capitalize()
        return out

    def _build_typo_map(self, items: List[CatalogItem]) -> Dict[str, str]:
        """Rare misspellings -> the common spelling one edit away ("idlli" ->
        "idli", "curshed" -> "crushed", "shreded" -> "shredded"). Only for
        unknown words of 5+ letters used on at most 2 items, mapped to a word
        used on 3+ items (or one in the synonym table)."""
        counts: Counter = Counter()
        for it in items:
            text, _, _ = _parse_size(_normalize_text(it.name))
            for w in set(re.findall(r"[a-z]+", text)):
                counts[w] += 1
        brand_words = {w for key in self.vocab.brands for w in key}
        targets = {w for w, c in counts.items() if c >= 3 and len(w) >= 4} | {
            k[0] for k in self.vocab.synonyms if len(k) == 1 and len(k[0]) >= 4
        } | {t for t in self.vocab.known_tokens if len(t) >= 4}

        def deletes(word: str) -> Set[str]:
            return {word[:i] + word[i + 1 :] for i in range(len(word))}

        by_delete: Dict[str, Set[str]] = defaultdict(set)
        for t in targets:
            for d in deletes(t):
                by_delete[d].add(t)
        protected = self.vocab.typo_protected
        out: Dict[str, str] = {}
        for w, c in counts.items():
            if c > 2 or len(w) < 5 or w in targets or w in brand_words or w in self.vocab.stopwords \
                    or w in self.vocab.weak_words or w in protected:
                continue
            found: Set[str] = set(by_delete.get(w, ()))  # w is a target plus one letter
            for d in deletes(w):
                if d in targets:
                    found.add(d)  # w is a target minus one letter
                found.update(by_delete.get(d, ()))  # one letter changed
            found = {t for t in found if t != w and _spelling_variant(w, t)}
            if not found:
                continue
            best = sorted(found, key=lambda t: (-counts.get(t, 0), t))
            if len(best) == 1 or counts.get(best[0], 0) > counts.get(best[1], 0):
                out[w] = best[0]
        return out

    def _weight(self, token: str) -> float:
        return self.weights.get(token, self._default_weight)

    def _norm(self, tokens: Sequence[str]) -> float:
        return math.sqrt(sum(self._weight(t) ** 2 for t in tokens)) or 1.0

    # -- queries -----------------------------------------------------------------

    def resolve(self, item_id: str) -> Optional[CatalogItem]:
        """The item to search for. A Container row resolves to its priced
        twin (same name) so both give the same answer."""
        item = self.by_id.get(item_id)
        if item is None:
            return None
        if item.is_container_row:
            for other in self.items:
                if other.name == item.name:
                    return other
        return item

    def similar(self, item_id: str, limit: Optional[int] = None, min_score: Optional[float] = None) -> List[Match]:
        sc = self.vocab.scoring
        limit = sc["default_limit"] if limit is None else limit
        min_score = sc["min_score"] if min_score is None else min_score
        key = (item_id, limit)
        if min_score == sc["min_score"]:
            with self._lock:
                hit = self._memo.get(key)
            if hit is not None:
                return hit
        result = self._similar(item_id, limit, min_score)
        if min_score == sc["min_score"]:
            with self._lock:
                self._memo[key] = result
        return result

    def count(self, item_id: str) -> int:
        return len(self.similar(item_id))

    def _similar(self, item_id: str, limit: int, min_score: float) -> List[Match]:
        query = self.resolve(item_id)
        if query is None:
            return []
        qp = self.parsed[query.item_id]
        if not qp.tokens:
            return []
        q_tokens = set(qp.tokens)
        q_norm = self.norms[query.item_id]
        q_types = q_tokens & self.vocab.type_tokens

        candidates: Set[str] = set()
        for t in q_tokens:
            if self._weight(t) > 0.5:
                candidates.update(self.postings.get(t, ()))
        if qp.brand is not None:
            candidates.update(i for i in self.by_brand.get(qp.brand, ()) if qp.brand_tokens or self.parsed[i].brand_tokens)
        candidates.discard(query.item_id)

        scored = []
        for cid in candidates:
            cand = self.by_id[cid]
            cp = self.parsed[cid]
            shared = q_tokens.intersection(cp.tokens)
            c_norm = self.norms[cid]
            q_norm_c = q_norm
            if qp.brand == cp.brand and (qp.brand_tokens or cp.brand_tokens):
                # "Parle-G 376g" vs "Parle-G Small - 1 pk": brand words one
                # side kept as product words are in the other side's brand.
                missing_c = [t for t in qp.brand_tokens if t not in shared]
                missing_q = [t for t in cp.brand_tokens if t not in q_tokens]
                shared = shared.union(missing_c, missing_q)
                c_norm = math.sqrt(c_norm ** 2 + sum(self._weight(t) ** 2 for t in missing_c))
                q_norm_c = math.sqrt(q_norm ** 2 + sum(self._weight(t) ** 2 for t in missing_q))
            if not shared:
                continue
            score = sum(self._weight(t) ** 2 for t in shared) / (q_norm_c * c_norm)
            score *= self._context_factor(query, qp, q_types, cand, cp)
            if score >= min_score:
                scored.append((score, cid))
        # Ties: items someone can actually order (has a vendor) first.
        scored.sort(key=lambda x: (-round(x[0], 3), not (self.by_id[x[1]].supplier or "").strip(), self.by_id[x[1]].name))
        out = []
        for score, cid in scored[:limit]:
            cand = self.by_id[cid]
            cp = self.parsed[cid]
            out.append(Match(item=cand, parsed=cp, score=round(min(score, 1.0), 3), reason=self._reason(qp, cp, score)))
        return out

    def _context_factor(self, query: CatalogItem, qp: Parsed, q_types: Set[str], cand: CatalogItem, cp: Parsed) -> float:
        sc = self.vocab.scoring
        factor = 1.0
        if _department(query) != _department(cand):
            factor *= sc["other_category_group_factor"]
        elif query.category and cand.category:
            if query.category == cand.category:
                factor *= sc["same_category_boost"]
                if query.subcategory and query.subcategory == cand.subcategory:
                    factor *= sc["same_subcategory_boost"] / sc["same_category_boost"]
            else:
                factor *= sc["other_category_factor"]
        # Fresh (produce, meat, fish, flowers) vs a packaged good: fresh
        # "Tomato" is not "Balaji Tomato" chips or a tomato plant.
        if _is_fresh(query) != _is_fresh(cand) and not (_is_frozen_category(query) and _is_frozen_category(cand)):
            factor *= sc.get("fresh_mismatch_factor", 1.0)
        # Frozen vs shelf: an explicit "frozen" word on one side, and the
        # other side neither says frozen nor sits in a frozen category.
        q_frozen = qp.frozen_word or _is_frozen_category(query)
        c_frozen = cp.frozen_word or _is_frozen_category(cand)
        if (qp.frozen_word and not c_frozen) or (cp.frozen_word and not q_frozen):
            factor *= sc["frozen_mismatch_factor"]
        c_tokens = set(cp.tokens)
        groups = self.vocab.type_groups
        qg = {groups.get(t, t) for t in q_types}
        cg = {groups.get(t, t) for t in c_tokens & self.vocab.type_tokens}
        if qg and cg and not (qg & cg):
            factor *= sc["type_conflict_factor"]
        elif bool(qg) != bool(cg):
            # "Coconut MILK" vs plain "Coconut ..." - one side says what kind
            # of product it is and the other doesn't.
            factor *= sc["missing_type_factor"]
        elif qg and cg:
            # Type words only one side has ("pani puri" vs "pani puri MASALA").
            extra = len(qg ^ cg)
            if extra:
                factor *= sc["extra_type_factor"] ** min(extra, 2)
        # The query's head noun (its last real product word: "coconut MILK",
        # "butter CHICKEN", "toor DAL oily") must be in the candidate, or it's
        # a different thing that merely shares a word ("milk bikis").
        q_head = self._head(qp)
        if q_head and q_head not in c_tokens and not (
            q_head in groups and any(groups.get(t) == groups[q_head] for t in c_tokens)
        ):
            factor *= sc["head_mismatch_factor"]
        return factor

    def _head(self, p: Parsed) -> Optional[str]:
        """The product word a name is about: its last product-type word
        ("khakhra methi" -> khakhra, "toor dal oily" -> dal), else its last
        word that isn't a weak word or a trailing modifier."""
        for t in reversed(p.tokens):
            if t in self.vocab.type_tokens:
                return t
        fallback = None
        for t in reversed(p.tokens):
            if t in self.vocab.weak_words:
                continue
            if t in self.vocab.trailing_modifiers:
                fallback = fallback or t
                continue
            return t
        return fallback

    def _core(self, p: Parsed) -> Set[str]:
        return {t for t in p.tokens if t not in self.vocab.weak_words}

    def _reason(self, qp: Parsed, cp: Parsed, score: float) -> str:
        known_brand = qp.brand is not None and cp.brand is not None
        same_product = self._core(qp) == self._core(cp) or score >= self.vocab.scoring["same_product_min_score"]
        sizes_known = qp.size_base is not None and cp.size_base is not None
        if same_product:
            if known_brand and qp.brand != cp.brand:
                return "Same product, other brand"
            if sizes_known and not _same_size(qp.size_base, cp.size_base):
                return "Same product, different size"
            if known_brand and sizes_known:
                return "Same product and size - possible duplicate listing"
            return "Same product"
        if known_brand and qp.brand != cp.brand:
            return "Similar type, other brand"
        if known_brand:
            return "Similar type, same brand"
        return "Similar type"


def _same_size(a: Optional[Tuple[str, float]], b: Optional[Tuple[str, float]]) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if a[0] != b[0]:
        return False
    return abs(a[1] - b[1]) <= 0.03 * max(a[1], b[1], 1.0)


# ---------------------------------------------------------------------------
# DB wiring + cache (one index per inventory upload)
# ---------------------------------------------------------------------------

_cache: dict = {}
_cache_lock = threading.Lock()


def clear_similar_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _snap_to_item(snap: InventorySnapshot) -> CatalogItem:
    return CatalogItem(
        item_id=snap.item_id,
        name=snap.name or "",
        category_group=snap.category_group,
        category=snap.category,
        subcategory=snap.subcategory,
        supplier=snap.supplier,
        supplier_item_id=getattr(snap, "supplier_item_id", None),
        price=snap.price,
        cost=snap.cost,
        qty=snap.inventory_quantity,
    )


def get_index(db: Session, latest: Optional[dict] = None) -> Optional[SimilarIndex]:
    """The cached index for the latest inventory upload (rebuilt when a new
    upload lands). `latest` = latest_inventory_by_item(db) if the caller
    already has it."""
    latest_date = db.execute(select(func.max(InventorySnapshot.snapshot_date))).scalar()
    if latest_date is None:
        return None
    # A new upload adds rows (max id); a same-day re-upload updates the rows
    # in place, so a cheap signature of what the index reads is part of the
    # key too (~5 ms for ~5,000 rows).
    signature = db.execute(
        select(
            func.count(),
            func.sum(func.length(InventorySnapshot.name)),
            func.sum(func.length(InventorySnapshot.supplier)),
            func.sum(func.length(InventorySnapshot.category)),
            func.total(InventorySnapshot.price),
            func.total(InventorySnapshot.cost),
            func.total(InventorySnapshot.inventory_quantity),
        ).where(InventorySnapshot.snapshot_date == latest_date)
    ).one()
    key = (
        str(db.get_bind().url),
        latest_date,
        db.execute(select(func.max(InventorySnapshot.id))).scalar(),
        tuple(signature),
    )
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None:
            return hit
        if latest is None:
            from app.services.category_overrides import invalidate
            from app.services.reorder import latest_inventory_by_item  # same rows (and overrides) as every report

            # Category rules are cached per session. A long request (Supplier
            # Projection) loads them at its start; if a rule changes and
            # clears this cache meanwhile, building from the session's old
            # rules would cache a stale index until the next upload. Re-read
            # them here, inside the lock, so a later clear always wins.
            invalidate(db)
            latest = latest_inventory_by_item(db)
        index = SimilarIndex(_snap_to_item(s) for s in latest.values())
        _cache.clear()
        _cache[key] = index
        return index


def units_sold_by_item(db: Session, item_ids: Sequence[str], days: int = 30, as_of: Optional[dt.date] = None) -> Dict[str, float]:
    """Units sold per item over the last `days` full days (from our own
    synced orders - Toast's last_N_day_sales columns are dollars)."""
    if not item_ids:
        return {}
    as_of = as_of or dt.date.today()
    start = as_of - dt.timedelta(days=days)
    stmt = (
        select(Order.item_guid, func.sum(Order.quantity))
        .where(
            Order.item_guid.in_(list(item_ids)),
            Order.voided.is_(False),
            Order.top_level(),
            Order.business_date >= start,
            Order.business_date < as_of,
        )
        .group_by(Order.item_guid)
    )
    return {guid: float(qty or 0) for guid, qty in db.execute(stmt)}


def _on_hand(index: SimilarIndex, item: CatalogItem) -> Tuple[Optional[float], Optional[float]]:
    container = index.container_by_name.get(item.name)
    container_qty = container[1] if container else None
    on_hand = item.qty
    if container_qty is not None:
        on_hand = (on_hand or 0.0) + container_qty
    return on_hand, container_qty


def similar_items_payload(db: Session, item_id: str, limit: Optional[int] = None) -> Optional[dict]:
    """The GET /api/ordering/similar-items response, or None when the item
    isn't in the latest upload."""
    index = get_index(db)
    if index is None:
        return None
    query = index.resolve(item_id)
    if query is None:
        return None
    qp = index.parsed[query.item_id]
    matches = index.similar(query.item_id, limit=limit)
    units = units_sold_by_item(db, [m.item.item_id for m in matches])
    items = []
    for m in matches:
        on_hand, container_qty = _on_hand(index, m.item)
        items.append(
            {
                "item_id": m.item.item_id,
                "name": m.item.name,
                "brand": m.parsed.brand,
                "size": m.parsed.size,
                "category": m.item.category,
                "subcategory": m.item.subcategory,
                "on_hand_qty": on_hand,
                "container_qty": container_qty,
                "price": m.item.price,
                "cost": m.item.cost,
                "vendors": vendor_refs(m.item.supplier, m.item.supplier_item_id),
                "units_30d": round(units.get(m.item.item_id, 0.0), 2),
                "score": m.score,
                "reason": m.reason,
            }
        )
    snapshot_date = db.execute(select(func.max(InventorySnapshot.snapshot_date))).scalar()
    return {
        "item_id": item_id,
        "name": query.name,
        "brand": qp.brand,
        "size": qp.size,
        "product_type": " ".join(qp.tokens),
        "snapshot_date": snapshot_date.isoformat() if snapshot_date else None,
        "matcher_version": index.vocab.version,
        "items": items,
    }


def similar_counts(db: Session, item_ids: Iterable[str], latest: Optional[dict] = None) -> Dict[str, int]:
    """item_id -> number of similar products (for Supplier Projection's
    per-row button). Empty on any failure - the UI then fetches on demand."""
    try:
        index = get_index(db, latest)
        if index is None:
            return {}
        return {i: index.count(i) for i in item_ids}
    except Exception:  # noqa: BLE001 - a counts failure must never break the projection
        logger.exception("Similar-product counts failed")
        return {}
