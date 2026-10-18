# Shaz International - category fix list for Toast

**Why:** every Shaz International item belongs in **RETAIL > Snacks & Savories > Biscuits, Cookies & Rusk**
(owner, Sep 29 2026), but Toast has them spread over Biscuits & Cookies, Roti/Naan/Wraps, Baking
Essentials and Sweets & Mithai. The dashboard and app can't change Toast (our Toast access is
read-only), so they already show the right category through an app-side override (the
`category_overrides` table, migration 0034). This list is for fixing Toast itself, so Toast, its
exports and its own reports agree too. Once Toast is fixed, the next inventory upload brings the
right category and the override simply has nothing left to change.

## Items to change (production database, upload of 2026-09-29)

Same list as a spreadsheet: `SHAZ_CATEGORY_FIX.csv` (next to this file). Target for every row:
Category group **RETAIL**, Category **Snacks & Savories**, Subcategory **Biscuits, Cookies & Rusk**.

| Item | Toast has now (group / category / subcategory) |
|---|---|
| KCB Cake Rusk Almond 20oz | RETAIL / Snacks & Savories / Biscuits & Cookies |
| KCB Coconut Cake Rusk 20oz | RETAIL / Snacks & Savories / Biscuits & Cookies |
| KCB Crown Cake Rusk 20oz | RETAIL / Snacks & Savories / Biscuits & Cookies |
| KCB Fruit Cake Rusk 20oz | RETAIL / Snacks & Savories / Biscuits & Cookies |
| KCB Milky Toast 7oz | RETAIL / Pantry Staples / Roti, Naan, Wraps |
| KCB Morning Toast 7oz | RETAIL / Pantry Staples / Roti, Naan, Wraps |
| KCB No Egg Cake Rusk 25oz | RETAIL / Snacks & Savories / Biscuits & Cookies |
| KCB No Sugar Toast 7oz | RETAIL / Pantry Staples / Roti, Naan, Wraps |
| KCB Plain Tea Rusk 7oz | RETAIL / Snacks & Savories / Biscuits & Cookies |
| KCB Sonfi Cake Rusk 20oz | RETAIL / Baking & Cooking Essentials / Baking Essentials |
| KCB Sooji Tea Toast 7oz | RETAIL / Pantry Staples / Roti, Naan, Wraps |
| KCB Sweet Bakar Khani 283g (vendors: Radhey Foods;Shaz International) | RETAIL / Snacks & Savories / Sweets & Mithai |
| KCB Veg Cake Rusk 10oz | RETAIL / Baking & Cooking Essentials / Baking Essentials |
| KCB W/Wheat Toast 7oz | RETAIL / Pantry Staples / Roti, Naan, Wraps |

14 items. KCB Sweet Bakar Khani is listed under two vendors; the rule covers any item that has
Shaz International among its vendors. If it should stay in Sweets & Mithai, tell us and we add a
one-item exception (an item rule always wins over the vendor rule).

This list was generated from a copy of the production database (upload of 2026-09-29); it matched
the dev database's list item for item. If items are added or removed before you fix Toast,
regenerate it from the live database - it only reads, never writes:

```
cd backend && python scripts/category_fix_list.py /path/to/spicetown.db > ../docs/SHAZ_CATEGORY_FIX.csv
```

(`--all` also lists matched items that Toast already has right.)

## Steps in Toast

1. In Toast Web, open the retail item categories and, under category **Snacks & Savories** (group
   RETAIL), add a new subcategory named exactly **Biscuits, Cookies & Rusk**.
2. For each item above, open the item and set Category group RETAIL, Category Snacks & Savories,
   Subcategory Biscuits, Cookies & Rusk. Use bulk edit if your Toast screen offers it (filter by
   the Shaz International vendor).
3. Optional: move the other items in the old "Biscuits & Cookies" subcategory too if you want one
   biscuits subcategory. The purchase order PDF already prints both under one "Biscuits, Cookies &
   Rusk" section, so this is not needed for picking.
4. Upload the next inventory export as usual. Then run the script above again - it should print
   no rows ("0 item(s) to change in Toast").

New Shaz items added later don't need anything: the app puts them in Biscuits, Cookies & Rusk
automatically. It's still best to file them there in Toast when you create them.
