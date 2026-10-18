# Spice Town category merge plan (Sep 30) - the TARGET taxonomy

Owner asked: merge the duplicate categories, then do a second full pass. All changes are app-side corrections
(Toast is read-only). Format below: group / category / subcategory ("-" = no subcategory).

## Merges (every item in the left combo moves; "by type" = decide per item)

1. RETAIL / Snacks & Savories / Biscuits & Cookies  ->  RETAIL / Snacks & Savories / Biscuits, Cookies & Rusk
   (except chocolates/candy -> rule 2).
2. NEW RETAIL / Snacks & Savories / Chocolates & Candy: all chocolate bars/boxes (Cadbury, Kit Kat, Kinder,
   Ferrero, Snickers, Mars, Dubai chocolate...), candy, toffees, gummies, jellies/jelly cups, marshmallows,
   lollipops, licorice, chewing gum, fruit bites/chews - taken out of Sweets & Mithai, Biscuits, Snacks.
   Sweets & Mithai keeps ONLY Indian/South Asian sweets (burfi, laddu, katli, soan papdi, gulab jamun tins,
   rasgulla tins, halwa, chikki, gajak, rewari, petha, mysore pak, badusha...). Amla candy stays in Sweets & Mithai.
3. RETAIL / Snacks & Savories / Aha Snacks, GPR Snacks, Telugu Snacks  ->  by type: savory Indian namkeen/murukku/
   mixture/chakli -> Namkeen; sweets -> Sweets & Mithai; chips -> Chips; other -> Snacks.
4. RETAIL / Produce / Fruits | Vegetables | Leafy Vegetables | Roots  ->  PRODUCE / same category / -
   RETAIL / Produce / Fresh Herbs  ->  PRODUCE / Fresh Herbs / -   (new PRODUCE category)
   PRODUCE / Popular / -  ->  by type (PRODUCE / Fruits | Vegetables | Roots | Leafy Vegetables).
   Keep PRODUCE / Fruits / Mangoes as is.
5. PRODUCE / Plants / -  ->  RETAIL / Plants & Seeds / -   (plants and seed packets aren't produce).
6. Pooja: RETAIL / Powders & Rituals / Chandan | Ritual Powders | Sindoor | Turmeric  ->
   RETAIL / Pooja Essentials / Powders, Chandan & Sindoor.
   RETAIL / Festival & Cultural / Prayer Kits  ->  RETAIL / Pooja Essentials / Prayer Kits.
   RETAIL / Pooja Essentials / Pooja Essentials (catch-all) -> by type: incense/dhoop/camphor/diyas/candles ->
   Candles, Incense, Camphor, Diyas; wicks -> Cotton Wicks; kumkum/chandan/sindoor/vibhuti -> Powders, Chandan &
   Sindoor; kits -> Prayer Kits; other pooja items (kalash, thread, janoi, bells, plates, backdrops, flowers,
   oils, ghee for lamps) stay Pooja Essentials. Festival & Cultural keeps Idols and Rakhi.
7. Health & Wellness: Pain Relief Balms & Ointments -> Pain Relief. Health Supplements -> Vitamins & Supplements.
   Health & Wellness / Health & Wellness (catch-all) -> by type (Ayurvedic & Herbal Remedies, Pain Relief,
   Vitamins & Supplements, First Aid & Hygiene / Bandages & Antiseptics, or NEW Health & Wellness / Digestive
   Health for non-herbal digestive aids like Eno, antacids, gripe water, ORS; herbal digestives stay in
   Ayurvedic & Herbal Remedies).
8. RETAIL / Pantry Staples / Cooking Essences & Flavors  ->  RETAIL / Baking & Cooking Essentials / Food Colors & Essences.
9. RETAIL / Hair Care / Hair Care and RETAIL / Dairy & Dairy Alternatives / Dairy (tiny catch-alls) -> by type.
10. " Men's Grooming" (leading space) -> "Men's Grooming" (all its items).

## New subcategories (use them where they fit)
- RETAIL / Snacks & Savories / Chocolates & Candy (rule 2)
- PRODUCE / Fresh Herbs / - (rule 4)
- RETAIL / Pooja Essentials / Powders, Chandan & Sindoor ; RETAIL / Pooja Essentials / Prayer Kits (rule 6)
- RETAIL / Household & Cleaning / Laundry (detergents, laundry bars/powders, fabric whiteners like Ujala)
- RETAIL / Ready-to-Eat & Instant Foods / Soups (Knorr/Ching's soup packets, instant soups)
- RETAIL / Tea, Coffee & Hot Beverages / Filter & Ground Coffee (filter coffee powders, ground coffee, chicory
  blends - Narasu's, Cothas, Coorg, Leo, Bru filter...). Instant coffee stays Instant Coffee.
- RETAIL / Tea, Coffee & Hot Beverages / Health & Malt Drinks (Horlicks, Bournvita, Boost, Complan, Milo,
  Ovaltine, Pediasure-type powders)
- RETAIL / Health & Wellness / Digestive Health (Eno, antacids, gripe water, ORS, isabgol/psyllium if medicinal)

## Catch-alls to empty where a specific home exists (second pass)
Frozen Foods / Frozen Foods, Snacks & Savories / Snacks, Pantry Staples / Pantry Staples,
Beverages (Non-Alcoholic) / Beverages, Condiments & Sauces / Condiments & Sauces,
Cookware & Kitchenware / Cookware & Kitchenware. Only move an item when a specific subcategory clearly fits;
leave genuinely miscellaneous items in the catch-all.

## Rules
- Never touch STREET KITCHEN, CATERING MENU, Event Menu, or RETAIL / Uninvoiced/Kitchen.
- Every target must be either an existing combo in taxonomy.txt or one of the targets named in this plan,
  spelled EXACTLY as here.
- Items owned by vendor "Shaz International" stay in Biscuits, Cookies & Rusk (a vendor rule).
- Frozen vs shelf: follow how the SAME brand's sibling products are filed; if unsure, leave it.
- Be conservative: only move an item when the new home is clearly better.

## Added after the first chunks (lead decision)
- NEW RETAIL / Dairy & Dairy Alternatives / Eggs (egg trays/cartons).
- NEW RETAIL / Frozen Foods / Frozen Sweets & Mithai: frozen Indian sweets and desserts (RES, Haldiram's,
  Vadilal, Mithai Kitchen... frozen mithai, rasmalai, khoa, frozen halwa, frozen gulab jamun). Shelf-stable
  tins stay in Snacks & Savories / Sweets & Mithai; ice creams/kulfi stay in Ice Creams.
