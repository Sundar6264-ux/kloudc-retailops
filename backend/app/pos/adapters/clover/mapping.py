"""Clover JSON -> the POS-neutral models (track D2; plan 4.4 / 4.7, MT16-MT17).

Pure functions, no I/O. Rules (each unverified point is listed in
docs/CLOVER_OWNER_CHECKLIST.md for the sandbox run):

Money: Clover sends cents (integers); a model gets ``cents / 100`` as a
Decimal. Quantities: a weighed / per-unit line has ``unitQty`` in
thousandths (1500 = 1.5 lb); any other line is 1 (Clover sends a fixed-price
item sold 3 times as 3 lines, each its own row).

Lines: each ``lineItem`` is one top-level row; each ``modification`` is its
own row (``is_modifier``) under it - Clover's line price does NOT include
modifiers, so the connector does not claim MODIFIER_PRICE_IN_PARENT.
Net = price x quantity + discounts (amount, negative cents, or a
percentage), then the order-level discounts spread over the sold lines in
proportion to their net; tax = net x the sum of the line's ``taxRates``
(Clover rates are in 1/100000 of a percent: 1000000 = 10 %). A refunded or exchanged line
counts as voided (not sold).

Business date: the local date of the order's creation time in the merchant's
time zone, minus the store's cutoff hour (a 01:30 sale with a 04:00 cutoff
belongs to the day before) - the same rule Toast's business dates follow.

Channel: from ``orderType.label`` (dine in / in store -> in_store, pick up /
to go / takeout -> pickup, online -> online, delivery -> delivery_3p,
catering -> catering, anything else -> other); no order type -> in_store
(a sale rung up at the register).

Catalog: one Item per Clover item; barcode = UPC ``code``; PLU = ``sku``;
price = ``price`` for FIXED (None when 0) and PER_UNIT (per ``unitName``),
None for VARIABLE (open price); cost = ``cost`` cents; primary category =
the first category by (``sortOrder``, name) - every category is a location;
stock = ``itemStock.quantity`` (signed: Clover keeps negative counts).
"""

import datetime as dt
import zoneinfo
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.pos.adapters.clover.client import from_ms
from app.pos.models import (
    Category,
    Check,
    Employee,
    Item,
    Order,
    OrderLine,
    StockLevel,
    StockStatus,
)

CENTS = Decimal(100)
CENT = Decimal("0.01")
UNIT_QTY_SCALE = Decimal(1000)
TAX_RATE_SCALE = Decimal(10_000_000)  # 1000000 = 10 % -> 0.10


def money(cents: Any) -> Optional[Decimal]:
    if cents in (None, ""):
        return None
    try:
        return Decimal(int(cents)) / CENTS
    except (TypeError, ValueError):
        return None


def _elements(value: Any) -> List[dict]:
    if isinstance(value, dict):
        return [e for e in (value.get("elements") or []) if isinstance(e, dict)]
    if isinstance(value, list):
        return [e for e in value if isinstance(e, dict)]
    return []


def zone(name: Optional[str], fallback: str = "America/New_York") -> zoneinfo.ZoneInfo:
    for candidate in (name, fallback, "UTC"):
        if candidate:
            try:
                return zoneinfo.ZoneInfo(str(candidate))
            except (zoneinfo.ZoneInfoNotFoundError, ValueError):
                continue
    return zoneinfo.ZoneInfo("UTC")


def business_date_of(when: Optional[dt.datetime], tz: zoneinfo.ZoneInfo, cutoff_hour: int) -> Optional[dt.date]:
    if when is None:
        return None
    local = when.astimezone(tz) - dt.timedelta(hours=int(cutoff_hour or 0))
    return local.date()


def business_day_bounds(day: dt.date, tz: zoneinfo.ZoneInfo, cutoff_hour: int) -> Tuple[dt.datetime, dt.datetime]:
    """[start, end) in UTC of one business date (local midnight + cutoff)."""
    start_local = dt.datetime.combine(day, dt.time(int(cutoff_hour or 0)), tzinfo=tz)
    end_local = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(int(cutoff_hour or 0)), tzinfo=tz)
    return start_local.astimezone(dt.timezone.utc), end_local.astimezone(dt.timezone.utc)


CHANNEL_WORDS: Tuple[Tuple[str, str], ...] = (
    ("deliver", "delivery_3p"),
    ("cater", "catering"),
    ("online", "online"),
    ("pick", "pickup"),
    ("to go", "pickup"),
    ("togo", "pickup"),
    ("take", "pickup"),
    ("dine", "in_store"),
    ("store", "in_store"),
    ("walk", "in_store"),
    ("counter", "in_store"),
)


def channel_of(order_type: Optional[dict]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """(channel, raw label, order type id)."""
    if not order_type:
        return "in_store", None, None
    label = str(order_type.get("label") or order_type.get("labelKey") or "").strip()
    low = label.lower()
    for word, channel in CHANNEL_WORDS:
        if word in low:
            return channel, label or None, order_type.get("id")
    return "other", label or None, order_type.get("id")


def line_quantity(line: dict) -> Decimal:
    unit_qty = line.get("unitQty")
    if unit_qty not in (None, ""):
        try:
            return Decimal(int(unit_qty)) / UNIT_QTY_SCALE
        except (TypeError, ValueError):
            pass
    return Decimal(1)


def _discount_total(base: Decimal, discounts: Iterable[dict]) -> Decimal:
    total = Decimal(0)
    for d in discounts:
        if d.get("amount") not in (None, ""):
            amt = money(d.get("amount")) or Decimal(0)
            total += amt if amt <= 0 else -amt
        elif d.get("percentage") not in (None, ""):
            try:
                total -= (base * Decimal(str(d.get("percentage"))) / Decimal(100)).quantize(CENT, rounding=ROUND_HALF_UP)
            except (ArithmeticError, ValueError):
                continue
    return total


def _tax_rate(line: dict) -> Decimal:
    rate = Decimal(0)
    for t in _elements(line.get("taxRates")):
        try:
            rate += Decimal(int(t.get("rate") or 0)) / TAX_RATE_SCALE
        except (TypeError, ValueError):
            continue
    return rate


def _tax(net: Decimal, line: dict) -> Decimal:
    return (net * _tax_rate(line)).quantize(CENT, rounding=ROUND_HALF_UP)


def map_lines(order_id: str, raw_lines: List[dict]) -> List[OrderLine]:
    out: List[OrderLine] = []
    for line in raw_lines:
        lid = str(line.get("id") or "")
        if not lid:
            continue
        qty = line_quantity(line)
        price = money(line.get("price")) or Decimal(0)
        base = (price * qty).quantize(CENT, rounding=ROUND_HALF_UP)
        net = base + _discount_total(base, _elements(line.get("discounts")))
        voided = bool(line.get("refunded") or line.get("exchanged"))
        item = line.get("item") or {}
        out.append(
            OrderLine(
                external_id=lid,
                check_external_id=order_id,
                parent_external_id=None,
                is_modifier=False,
                item_external_id=item.get("id"),
                name=line.get("name"),
                quantity=qty,
                net_amount=net,
                tax_amount=_tax(net, line),
                voided=voided,
                check_index=0,
            )
        )
        for mod in _elements(line.get("modifications")):
            mid = str(mod.get("id") or "")
            if not mid:
                continue
            try:
                mqty = Decimal(int(mod.get("quantity") or 1))
            except (TypeError, ValueError):
                mqty = Decimal(1)
            amount = (money(mod.get("amount")) or Decimal(0)) * mqty * (qty if line.get("unitQty") in (None, "") else 1)
            modifier = mod.get("modifier") or {}
            out.append(
                OrderLine(
                    external_id=f"{lid}:{mid}",
                    check_external_id=order_id,
                    parent_external_id=lid,
                    is_modifier=True,
                    item_external_id=modifier.get("id"),
                    name=mod.get("name") or modifier.get("name"),
                    quantity=mqty,
                    net_amount=amount.quantize(CENT, rounding=ROUND_HALF_UP),
                    tax_amount=Decimal(0),
                    voided=voided,
                    check_index=0,
                )
            )
    return out


def spread_order_discounts(
    lines: List[OrderLine], discounts: List[dict], tax_rates: Optional[Dict[str, Decimal]] = None
) -> List[OrderLine]:
    """Order-level discounts (Clover ``order.discounts``: an amount in
    negative cents, or a percentage) spread over the order's sold top-level
    lines in proportion to their net, so the lines add up to what the
    customer paid. The last line takes the rounding remainder; tax is
    recomputed at the line's own rate."""
    if not discounts:
        return lines
    sold = [i for i, l in enumerate(lines) if not l.is_modifier and not l.voided and l.net_amount > 0]
    base = sum((lines[i].net_amount for i in sold), Decimal(0))
    if not sold or base <= 0:
        return lines
    total = -_discount_total(base, discounts)  # positive amount off
    total = min(total, base)
    if total <= 0:
        return lines
    out = list(lines)
    left = total
    for n, i in enumerate(sold):
        line = lines[i]
        share = left if n == len(sold) - 1 else (total * line.net_amount / base).quantize(CENT, rounding=ROUND_HALF_UP)
        share = min(share, left)
        left -= share
        new_net = line.net_amount - share
        rate = (tax_rates or {}).get(line.external_id)
        if rate is None:
            rate = (line.tax_amount / line.net_amount) if line.net_amount else Decimal(0)
        out[i] = OrderLine(**{**line.__dict__, "net_amount": new_net, "tax_amount": (new_net * rate).quantize(CENT, rounding=ROUND_HALF_UP)})
    return out


def map_order(raw: dict, tz: zoneinfo.ZoneInfo, cutoff_hour: int, business_date: Optional[dt.date] = None) -> Order:
    oid = str(raw.get("id") or "")
    created = from_ms(raw.get("createdTime")) or from_ms(raw.get("clientCreatedTime"))
    modified = from_ms(raw.get("modifiedTime"))
    payments = _elements(raw.get("payments"))
    paid_times = [t for t in (from_ms(p.get("createdTime")) for p in payments if str(p.get("result") or "SUCCESS").upper() == "SUCCESS") if t]
    paid_at = max(paid_times) if paid_times else None
    state = str(raw.get("state") or "").lower()
    payment_state = str(raw.get("paymentState") or "").upper()
    closed = state in ("locked", "paid") or payment_state in ("PAID", "REFUNDED", "PARTIALLY_REFUNDED")
    refunds = _elements(raw.get("refunds"))
    fully_refunded = payment_state == "REFUNDED" or (
        bool(refunds) and raw.get("total") not in (None, 0)
        and sum(int(r.get("amount") or 0) for r in refunds) >= int(raw.get("total") or 0)
    )
    channel, channel_raw, type_id = channel_of(raw.get("orderType"))
    raw_lines = _elements(raw.get("lineItems"))
    employee = raw.get("employee") or {}
    bdate = business_date or business_date_of(created, tz, cutoff_hour) or dt.date.today()
    check = Check(
        external_id=oid,
        paid_at=paid_at,
        total=money(raw.get("total")),
        tab_name=(raw.get("title") or None),
        voided=fully_refunded,
        deleted=False,
    )
    return Order(
        external_id=oid,
        business_date=bdate,
        opened_at=created,
        created_at=created,
        paid_at=paid_at,
        closed=closed,
        closed_at=(paid_at or modified) if closed else None,
        promised_at=None,
        channel=channel,
        channel_raw=channel_raw,
        dining_option_id=type_id,
        display_number=raw.get("title") or None,
        guests=raw.get("groupLineItems") if isinstance(raw.get("groupLineItems"), int) else None,
        server_external_id=employee.get("id"),
        approval_status=None,
        checks=(check,),
        lines=tuple(spread_order_discounts(
            map_lines(oid, raw_lines), _elements(raw.get("discounts")),
            {str(l.get("id")): _tax_rate(l) for l in raw_lines if l.get("id")},
        )),
        voided=fully_refunded,
        deleted=bool(raw.get("deleted")),
        updated_at=modified,
    )


def is_open(raw: dict) -> bool:
    return str(raw.get("state") or "").lower() == "open" and not raw.get("deleted")


# --- employees ---------------------------------------------------------------------------------


def map_employee(raw: dict) -> Employee:
    name = str(raw.get("name") or raw.get("nickname") or "").strip() or "Employee"
    return Employee(external_id=raw.get("id"), name=name, active=not raw.get("deletedTime") and not raw.get("deleted"))


# --- catalog -----------------------------------------------------------------------------------


def _categories(raw: dict) -> List[dict]:
    cats = _elements(raw.get("categories"))
    return sorted(cats, key=lambda c: (c.get("sortOrder") if isinstance(c.get("sortOrder"), int) else 10**9, str(c.get("name") or "").lower()))


def map_item(raw: dict) -> Item:
    price_type = str(raw.get("priceType") or "FIXED").upper()
    price = money(raw.get("price"))
    if price_type == "VARIABLE" or not price:
        price = None
    unit = (raw.get("unitName") or "").strip() or None
    cats = [Category(group=str(c.get("name")).strip()) for c in _categories(raw) if (c.get("name") or "").strip()]
    code = (raw.get("code") or "").strip()
    sku = (raw.get("sku") or "").strip()
    return Item(
        external_id=str(raw.get("id")),
        name=raw.get("name"),
        barcodes=(code,) if code else (),
        plu=sku or None,
        price=price,
        cost=money(raw.get("cost")) if raw.get("cost") not in (None, "", 0) else None,
        category=cats[0] if cats else None,
        locations=tuple(cats),
        other_names=tuple(n for n in (raw.get("name"), raw.get("alternateName")) if n),
        price_rule=f"{price_type}:{unit}" if price_type == "PER_UNIT" and unit else price_type,
        active=not raw.get("hidden") and raw.get("available", True) is not False and not raw.get("deleted"),
        updated_at=from_ms(raw.get("modifiedTime")),
    )


def stock_level(item_id: Optional[str], stock: Optional[dict], as_of: Optional[dt.datetime] = None) -> StockLevel:
    """Clover keeps a signed quantity; an item without an itemStock is not tracked."""
    if not stock or stock.get("quantity") in (None, ""):
        return StockLevel(item_external_id=item_id, status=StockStatus.UNTRACKED, quantity=None, as_of=as_of)
    try:
        qty = Decimal(str(stock.get("quantity")))
    except (ArithmeticError, ValueError):
        return StockLevel(item_external_id=item_id, status=StockStatus.UNTRACKED, quantity=None, as_of=as_of)
    return StockLevel(item_external_id=item_id, status=StockStatus.COUNT, quantity=qty, as_of=as_of)


def stock_from_items(items: Iterable[dict], as_of: Optional[dt.datetime] = None) -> List[StockLevel]:
    return [stock_level(str(i.get("id")), i.get("itemStock"), as_of) for i in items if i.get("id")]


def stock_from_item_stocks(rows: Iterable[dict], as_of: Optional[dt.datetime] = None) -> List[StockLevel]:
    out: List[StockLevel] = []
    for r in rows:
        item_id = (r.get("item") or {}).get("id")
        if item_id:
            out.append(stock_level(str(item_id), r, as_of))
    return out


def merchant_facts(merchant: Dict[str, Any], properties: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": merchant.get("name"),
        "timezone": properties.get("timezone") or (merchant.get("address") or {}).get("timezone"),
        "summary_hour": properties.get("summaryHour"),
    }
