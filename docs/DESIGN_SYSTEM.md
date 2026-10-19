# KloudC RetailOps - Design system, navigation and help (spec)

Status: **spec for the final pass** (Oct 19, 2026). No feature code here. Four builders work from it in
parallel: **W** website, **H** help content + help components, **M** mobile app, **S** server + ops
(section 5). Where this file and older contracts disagree about look, navigation or help, this file
wins; behaviour, APIs and permissions stay as `docs/PLATFORM_CONTRACT.md` says.

Product: **KloudC RetailOps** by **KloudC LLC** (Connecticut, USA). Tagline: *"Make Your POS
Inventory Work Like Your Real Store."* Support: support@kloudc.com. Links: https://kloudc.com,
privacy https://kloudc.com/kloudc-privacy-policy/, support https://kloudc.com/kloudc-support/,
contact https://kloudc.com/contact/. No Terms page exists yet: show Privacy only; the server's
`TERMS_URL` setting stays blank and a Terms link appears only when it is set.

Owner's brief (Oct 19): *"Make it beautiful, professional and easily navigation. Use different color
combinations, add help section, add glossary, add info etc."* Standing preferences (memories
`minimal-navigation-ui`, `compact-list-headers`): few taps, group related tools on one screen as
tab-like tiles that remember the last choice, no drill-down chains, slim frozen list headers
(search + filter + settings + ⓘ), explanatory text behind ⓘ so lists get the room.

No store or customer name from the origin store appears anywhere in the product, its content files,
tests or screenshots (the existing `noStoreNames` tests extend to `content/`).

---

## 0. Where we are today (baseline, Oct 19)

Studied: `frontend/src` (Sidebar, featureRoutes, App, index.css 14.7k lines, platform.css, Help 1.9k
lines of JSX, glossary.jsx, InfoBlock) and `storeops-app/lib` (AppColors/AppTheme, app_tabs, Store
sections, CompactListHeader, InfoSheet, help_content.dart). Baseline screenshots were taken of the
local preview at 1280 and 375 px (outside git).

| Area | Today | Problem |
|---|---|---|
| Name | "StoreOps" on login, sidebar fallback, app | Product is now KloudC RetailOps |
| Colour | Teal `#0F766E` default brand, dark grey sidebar, ad-hoc `.tone-*` classes (10 hues) and one-off hex values across 14.7k lines of CSS | No KloudC palette, no area identity, no dark mode on the website |
| Website dark mode | none | Light only |
| App theme | forced dark (`ThemeMode.dark`), warm-grey neutrals, forced `Roboto` on iOS | No light theme in use, off-brand neutrals |
| Navigation (web) | 3 top links + 5 collapsible groups + 4 footer links; Sales & Insights alone holds 13 links; Invoices top-level on web but under Stock in the app; cart only as a floating button | Long menus, web and app disagree on where things live |
| Top bar (web) | none (no search, no user menu, no help ⓘ) | Sign-out and password at the bottom of a long sidebar |
| Help | Website: 25 hard-coded JSX sections. App: a hand "port" in `help_content.dart` | Two copies already drifting; no getting-started, FAQ or troubleshooting |
| Glossary | One source (`backend/app/glossary.py`, ~256 status/label terms, served at `/api/glossary`, bundled in the app) | Good - but covers statuses only, not concepts (Store ID, PO section, Container, Lead time...) |
| Page info | `InfoBlock` ("? More") on ~50 web components; app ⓘ sheets with per-screen hand-written text | Text lives in code, different on web and app |
| Master console | Website only, 4 tile tabs (Requests, Stores, Hosting, Audit), plain styling | Not KloudC branded |

---

## 1. Tokens

All colour, type, spacing, radius, shadow and motion values come from tokens. **No hex value in a
component** (website: CSS custom properties; app: `KcTokens` theme extension). A lint test fails on
new raw hex outside the token files (W: `frontend/src/styles/tokens.css`; M:
`lib/core/theme/kc_tokens.dart`).

### 1.1 Colour - KloudC brand

| Token | Light | Dark | Use |
|---|---|---|---|
| `--kc-blue` | `#004BAD` | `#6CB6F7` (as text) / `#1F6FD1` (as fill) | Product brand: master console, login, product default `--brand` |
| `--kc-sky` | `#2EA3F2` | `#2EA3F2` | Decorative only on light (2.75:1 on white - never text); gradients, charts, illustrations, dark-mode accents |
| `--kc-link` | `#0C71C3` | `#6CB6F7` | Links, focus ring (5.0:1 on white) |
| `--kc-ink` | `#2D3940` | - | KloudC dark text in marketing-style surfaces (login panel copy) |
| `--kc-grey` | `#9E9E9E` | - | Logo motion lines only (2.7:1 - decoration only) |
| `--kc-gradient` | `linear-gradient(135deg, #004BAD 0%, #0C71C3 55%, #2EA3F2 100%)` | same | Login brand panel, master top band, empty-state art |

The **product default store brand** changes from teal `#0F766E` to KloudC blue: `BRAND_COLOR=#004BAD`,
`BRAND_SECONDARY_COLOR=#2EA3F2` (website `PRODUCT_DEFAULTS`, app `TenantConfig.defaultPrimaryColor`,
server settings). A store that set its own colours keeps them.

### 1.2 Colour - neutrals

Cool slate neutrals tinted toward KloudC ink (replace the app's warm greys and the web's mixed greys).

| Token | Light | Dark | Notes |
|---|---|---|---|
| `--bg` | `#F5F7FA` | `#0E1620` | Page background |
| `--surface` | `#FFFFFF` | `#162130` | Cards, tables, sheets |
| `--surface-2` | `#EEF2F6` | `#1D2A3A` | Table header, segmented-control track, input fill on cards |
| `--surface-3` | `#E4E9EF` | `#243346` | Hover on surface-2, skeletons |
| `--border` | `#E1E6EC` | `#273545` | Hairlines |
| `--border-strong` | `#C9D1DA` | `#36475A` | Inputs, dividers that must show (3:1 vs surface for inputs: use `--input-border` `#8A97A5` light / `#5A6B7E` dark) |
| `--text` | `#1E2A32` | `#E8EEF4` | 13.7:1 / 15.6:1 |
| `--text-2` | `#475663` | `#B4C0CC` | Secondary text, table body numbers |
| `--text-muted` | `#5E6C78` | `#8C9BAB` | Hints, captions (≥4.8:1 on every surface above) |
| `--text-disabled` | `#9AA6B2` | `#5D6C7C` | Disabled only (exempt from AA) |
| `--overlay` | `rgba(14,22,32,.45)` | `rgba(0,0,0,.6)` | Modal/sheet scrim |
| Sidebar (store) | `--nav-bg #0B1F3A`, `--nav-text #A9B8CC` (8.2:1), `--nav-muted #7F93AD` (5.3:1), `--nav-active #FFFFFF`, `--nav-hover rgba(255,255,255,.06)`, `--nav-active-bg rgba(255,255,255,.10)` | same navy in both themes | Deep KloudC navy instead of today's `#111827` |

### 1.3 Colour - area accents ("different colour combinations")

Each area has one hue. It is an **identity** colour, never a status: it appears only on the area chip
in the page header, the sidebar group icon, the app Store category tile and Home quick-action icons,
the 3 px top edge of an Overview KPI card that links into the area, and chart series that belong to
the area. Never as a full-bleed background, never on body text.

| Area | `data-area` | Solid (text/icon) light | Soft bg light | Dark text/icon | Dark soft bg | Icon (Lucide / Material) |
|---|---|---|---|---|---|---|
| Home / Today | `home` | `#004BAD` | `#E8F1FD` | `#6CB6F7` | `rgba(108,182,247,.14)` | `layout-dashboard` / `space_dashboard_outlined` |
| Ordering | `ordering` | `#6D28D9` violet (7.1:1) | `#F3EEFF` | `#B79CFF` | `rgba(183,156,255,.14)` | `shopping-cart` / `shopping_cart_outlined` |
| Stock | `stock` | `#0F766E` teal (5.5:1) | `#E6F6F4` | `#4FD1C5` | `rgba(79,209,197,.14)` | `package` / `inventory_2_outlined` |
| Sales | `sales` | `#BE185D` raspberry (6.0:1) | `#FDEBF3` | `#F98BBE` | `rgba(249,139,190,.14)` | `chart-line` / `insights_outlined` |
| Team | `team` | `#C2410C` orange (5.2:1) | `#FFF1E6` | `#FDA769` | `rgba(253,167,105,.14)` | `users` / `groups_outlined` |
| Setup | `setup` | `#475569` slate (7.6:1) | `#EEF2F6` | `#A8B6C6` | `rgba(168,182,198,.14)` | `settings` / `tune_outlined` |
| Platform (master) | `platform` | `#004BAD` KloudC blue | `#E8F1FD` | `#6CB6F7` | `rgba(108,182,247,.14)` | `building-2` / `domain_outlined` |
| Help | `help` | `#0C71C3` | `#E6F2FD` | `#6CB6F7` | `rgba(108,182,247,.14)` | `life-buoy` / `help_outline` |

Tokens: `--area-ordering`, `--area-ordering-soft`, ... A page sets `data-area="ordering"` on its root
and components read `--area` / `--area-soft` (CSS: `[data-area="ordering"] { --area: var(--area-ordering);
--area-soft: var(--area-ordering-soft); }`). All solid/soft pairs measured ≥4.6:1 (light) and ≥7:1
(dark on `--surface`).

### 1.4 Colour - status

Status always pairs colour with an icon and a word (never colour alone).

| Status | Text light | Soft bg light | Dark text | Dark soft | Solid fill (white text) | Icon | Maps today's |
|---|---|---|---|---|---|---|---|
| success | `#157A3E` | `#E3F6EA` | `#4ADE80` | `rgba(74,222,128,.14)` | `#157A3E` (5.4:1) | check-circle | `--success-*`, `pill-success` |
| warning | `#9A5B00` | `#FEF4D6` | `#FBBF24` | `rgba(251,191,36,.14)` | `#9A5B00` | alert-triangle | `--running-*`, `pill-warn` |
| danger | `#B42318` | `#FDECEA` | `#FF7B72` | `rgba(255,123,114,.14)` | `#B42318` (6.6:1) | alert-octagon | `--failed-*`, `pill-failed` |
| info | `#0C63B0` | `#E6F2FD` | `#60A5FA` | `rgba(96,165,250,.14)` | `#0C63B0` | info | `--info-*`, `pill-info` |
| neutral | `#475663` | `#EEF2F6` | `#B4C0CC` | `rgba(180,192,204,.12)` | - | circle | `pill-neutral` |
| violet ("check") | `#6D28D9` | `#F3EEFF` | `#B79CFF` | `rgba(183,156,255,.14)` | - | eye | `pill-check` |

The glossary's `tone` field (`success | warning | danger | info | neutral | violet`) keeps mapping to
these. The ten legacy `.tone-*` classes are re-pointed to tokens (blue→info, red→danger,
amber→warning, green→success, purple→violet, teal→stock, rose→sales, orange→team, slate→setup,
indigo→platform) so old markup keeps working while W migrates it.

### 1.5 Store brand tint

Store pages may carry the store's own colours (`GET /api/app/branding` `primary_color`,
`secondary_color`). Rules (existing `branding.js brandCssVars` / app `BrandColors` keep the contrast
math and gain a dark variant):

- `--brand` = store primary darkened until white text reaches 4.5:1 (exists). `--brand-text` = the
  store primary adjusted to 4.5:1 **on the current theme's surface** (new; dark mode lightens).
- `--brand-soft` = 8 % brand over `--surface` (light) / 14 % over `--surface` (dark).
- Brand is used for: the primary button, the active nav marker (3 px left bar), focus-within on the
  store switcher, the 3 px top stripe of the sidebar (secondary colour), the logo fallback tile, the
  Home greeting band (`--brand-soft` gradient to transparent).
- Brand is **not** used for: area chips, status, links (links stay `--kc-link`), body text, charts.
- The master console and logged-out pages never use a store's brand: they are KloudC blue.

### 1.6 Typography

System font stack for speed and privacy (no Google Fonts request, no CSP exception):

```
--font-sans: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI Variable", "Segoe UI",
             Roboto, "Helvetica Neue", Arial, sans-serif;
--font-mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
```

Numbers in tables, KPIs and money use `font-variant-numeric: tabular-nums`. Store IDs, barcodes and
codes use `--font-mono`.

| Token | Size / line | Weight | Use | Flutter `TextTheme` |
|---|---|---|---|---|
| `display` | 30 / 36 | 700 | Login hero, empty-state hero (rare) | `displaySmall` |
| `h1` | 24 / 32 | 700 | Page title | `headlineSmall` |
| `h2` | 20 / 28 | 650 | Section title, dialog title | `titleLarge` |
| `h3` | 16 / 24 | 650 | Card title | `titleMedium` |
| `body` | 15 / 22 (web 14/20 in dense tables) | 400 | Default | `bodyLarge` (15) / `bodyMedium` (14) |
| `small` | 13 / 18 | 400-500 | Hints, captions, table meta | `bodySmall` |
| `label` | 12 / 16, letter-spacing .04em, uppercase | 600 | KPI labels, table headers, group labels | `labelSmall` |
| `kpi` | 26 / 32 tabular | 700 | KPI values | custom `kpi` in `KcTypography` |
| `button` | 14 / 20 | 600 | Buttons, tabs | `labelLarge` |

Max line length for prose (Help articles, ⓘ panels): 68ch.

### 1.7 Spacing, layout, radius, elevation, motion

- **Spacing** (4 px base): `--space-1 4` `--space-2 8` `--space-3 12` `--space-4 16` `--space-5 20`
  `--space-6 24` `--space-8 32` `--space-10 40` `--space-12 48` `--space-16 64`. App `AppSpacing`
  keeps xs 4 / sm 8 / md 12 / lg 16 / xl 24 / xxl 32 and adds `xxxl 48`.
- **Page gutter**: 16 px (<640), 24 px (640-1279), 32 px (≥1280). Content max width 1240 px for
  dashboards and forms (forms 640 px column); wide tables may use the full width.
- **Breakpoints**: `sm 640`, `md 768`, `lg 1024`, `xl 1280`, `2xl 1440`. Below 1024 the sidebar is a
  drawer; ≥1024 it is fixed (240 px, collapsible to a 64 px icon rail, remembered).
- **Radius**: `--radius-xs 4` (badges in tables), `--radius-sm 6` (inputs, buttons), `--radius-md 10`
  (cards, tiles), `--radius-lg 14` (dialogs, sheets), `--radius-pill 999`. The app keeps its slightly
  rounder touch scale (`AppRadius` sm 8 / md 12 / lg 16 / pill).
- **Shadows** (light): `--shadow-xs 0 1px 1px rgba(16,24,40,.04)`; `--shadow-sm 0 1px 2px
  rgba(16,24,40,.06), 0 1px 3px rgba(16,24,40,.08)`; `--shadow-md 0 4px 8px -2px rgba(16,24,40,.08),
  0 2px 4px -2px rgba(16,24,40,.05)`; `--shadow-lg 0 12px 24px -4px rgba(16,24,40,.12)`
  (popovers, dialogs). Dark: shadows drop to `rgba(0,0,0,.4)` and surfaces separate by border +
  surface step instead. Cards at rest use border + `--shadow-xs`; hover lifts to `--shadow-sm`.
- **Z-index**: sidebar 30, top bar 40, drawer 50, popover 60, dialog/sheet 70, toast 80.
- **Motion**: `--dur-fast 120ms` (hover, press), `--dur 180ms` (popover, tab indicator), `--dur-slow
  240ms` (drawer, sheet); easing `--ease-out cubic-bezier(.2,.8,.2,1)`, `--ease-in-out
  cubic-bezier(.4,0,.2,1)`. `prefers-reduced-motion: reduce` → durations 0, no translate (opacity only).
  App: `KcMotion.fast/normal/slow` same values; respect `MediaQuery.disableAnimations`.
- **Focus**: 2 px `--focus` ring (`#0C71C3` light / `#6CB6F7` dark) with 2 px offset on every
  interactive element via `:focus-visible`. Never `outline: none` without a replacement.
- **Touch**: every control ≥44×44 px hit area below `lg` (icon buttons draw 36 px, pad to 44).

### 1.8 Themes

- **Website**: `light`, `dark`, `system` (default). Implemented as `:root` (light) +
  `@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {...} }` +
  `:root[data-theme="dark"] {...}`. Choice in the user menu, remembered in `localStorage`
  (`kc.theme`), applied by an inline script in `index.html` before first paint (no flash).
- **App**: More › Account › **Appearance**: System / Light / Dark. **Default stays Dark** (the owner's
  earlier explicit decision for the staff tool) - the new option is additive and remembered in
  `shared_preferences` (`kc.theme`).
- Charts, images and store logos sit on `--surface` in both themes; logos get a 1 px `--border` ring so
  a dark logo stays visible in dark mode.

### 1.9 Flutter mapping

`lib/core/theme/kc_tokens.dart` (M) defines `KcColors extends ThemeExtension<KcColors>` (light/dark
instances with every token above: neutrals, `areas: Map<KcArea, KcAreaColors(solid, soft)>`, status
`KcStatusColors(text, soft, fill)`), `KcTypography`, `KcMotion`; `AppColors` stays as a thin
deprecated alias layer so old screens compile while M migrates them.

| ThemeData slot | Light | Dark |
|---|---|---|
| `colorScheme.primary` / `onPrimary` | `BrandColors.primary` (store or `#004BAD`) / white | fill variant (`#1F6FD1` for product default) / white |
| `colorScheme.secondary` | store secondary or `#2EA3F2` | same |
| `colorScheme.surface` / `onSurface` | `#FFFFFF` / `#1E2A32` | `#162130` / `#E8EEF4` |
| `scaffoldBackgroundColor` | `#F5F7FA` | `#0E1620` |
| `colorScheme.surfaceContainerLow` / `High` | `#EEF2F6` / `#E4E9EF` | `#1D2A3A` / `#243346` |
| `colorScheme.outline` / `outlineVariant` | `#8A97A5` / `#E1E6EC` | `#5A6B7E` / `#273545` |
| `colorScheme.error` | `#B42318` | `#FF7B72` |
| `textTheme` | section 1.6, colour `--text`; `bodySmall` uses `--text-muted` | same, dark values |
| `fontFamily` | **null** (system: SF Pro on iOS, Roboto on Android) - today's forced `'Roboto'` is removed | same |
| `tabBarTheme.labelColor` | `BrandColors.readableOn(bg)` (exists) | same |
| `navigationBarTheme` | surface bg, indicator `--brand-soft`, selected icon `--brand-text` | same |
| `cardTheme` | radius 12, `--border` side, elevation 0 | same |
| `inputDecorationTheme` | fill `--surface`, border `--input-border`, focus `--focus` 2 px | same |
| `snackBarTheme` | `--text` bg, `--surface` text (inverse), floating, radius 10 | inverse of dark |
| `bottomSheetTheme` | radius 16 top, `--surface`, drag handle `--border-strong` | same |
| `ThemeExtension<KcColors>` | areas + status + neutrals | same |

Usage: `context.kc.area(KcArea.ordering).solid`, `context.kc.status(KcStatus.danger).soft`.

---

## 2. Components

Website components live in `frontend/src/ui/` (new; W owns, except `ui/help/*` which H owns). App
widgets live in `lib/core/widgets/` (M owns, except `lib/core/help/*` which H owns). Icons: website
uses an inline SVG set `ui/icons.jsx` (~45 icons copied from Lucide, ISC licence, licence text in the
file; **no new npm package**); the app keeps Material Symbols outlined. Text glyph icons (☰ « » ✕ ▲)
are replaced by real icons.

### 2.1 App shell (website)

```
┌────────────┬───────────────────────────────────────────────────────────────┐
│ [logo]Store│ [☰]  Store name ▾ · ID  [ 🔍 Search pages, help, terms  ⌘K ] [🛒3] [?] [AB▾] │ top bar 56px, sticky
│ ───stripe──├───────────────────────────────────────────────────────────────┤
│ ⌂ Home     │  PageHeader                                                   │
│ ☑ Checklists│  content                                                     │
│ ORDERING ▸ │                                                               │
│ STOCK    ▸ │                                                               │
│ SALES    ▸ │                                                               │
│ TEAM     ▸ │                                                               │
│ SETUP    ▸ │                                                               │
│ ───────────│                                                               │
│ ? Help     │                                                               │
│ ⎙ Labels ↗ │                                                               │
│ «  collapse│                                                               │
└────────────┴───────────────────────────────────────────────────────────────┘
```

- **Sidebar** (240 px, navy `--nav-bg`, 3 px store-secondary stripe under the brand row). Brand row:
  store logo (32 px, radius 8) + short name; tap → Home. Groups have an area-coloured icon, an
  uppercase `label` heading and a chevron; expanded state per group is remembered
  (`kc.nav.open`, today it resets each visit); the group holding the current page always opens
  (existing `groupsToOpen`). Active link: `--nav-active-bg` + 3 px `--brand` left bar +
  `aria-current="page"`. Count badges (Chat unread, News unacknowledged) stay; collapsed group shows
  the sum. Collapsed rail (64 px): icons only with tooltip + flyout of the group's links on hover/focus.
  Footer: Help, Label Scanner ↗ (when the store has one), collapse toggle. "Signed in as" and Log out
  move to the user menu.
- **Top bar** (56 px, `--surface`, bottom border, sticky):
  - **Store switch / name**: store name + Store ID (mono, copy button). The account belongs to one
    store, so the menu offers *Copy Store ID*, *Open the app* (store's app links), and *Sign in to
    another store* (signs out, keeps the typed Store ID blank). Shown as plain text, no ▾, when it
    has nothing to offer.
  - **Search** (`⌘K` / `Ctrl K` / `/`): one box over (1) pages this user can open (from
    `buildSidebar`), (2) Help articles and FAQs, (3) Glossary terms. Results grouped, keyboard
    navigable, each shows where it lives ("Ordering › Vendor Calendar"). Phone: search icon opens a
    full-screen sheet.
  - **Cart** button (cart users only) with line count - replaces the floating cart button, which
    covered content on phones.
  - **Help ⓘ / ?**: opens a right-side *Help panel* (drawer, 400 px; full-screen sheet on phone) with
    this page's info (section 4.3), its glossary terms and "Open full guide". Same content as the
    page header ⓘ; the top-bar one is always in the same place.
  - **User menu** (avatar initials on `--brand-soft`): name, role (Owner/Admin/Staff), *Change
    password*, *Appearance* (System/Light/Dark), *Help center*, *Glossary*, *Contact support*,
    *Privacy policy* ↗, *Sign out*.
- Phone (<1024): top bar shows ☰, store logo, search icon, cart, avatar; the sidebar slides in as a
  drawer (focus trapped, Esc / backdrop closes). Unread dot on ☰ stays.

### 2.2 Master console shell (KloudC branded)

- Top band 56 px with `--kc-gradient`, KloudC RetailOps logo (white lockup), "Platform" pill, search,
  ⓘ, user menu (Two-step sign-in, Change password, Appearance, Help, Sign out). No sidebar.
- Under it, **tile tabs** (exists, remembered): *Requests (n)*, *Stores*, *Hosting*, *Audit*, plus a
  *Help* tab (master guide + FAQ). Each tile: icon, label, count badge.
- Slim frozen list header per tab (search, status filter, ⓘ) - exists; restyle only.
- Store rows: store logo/initial, name, Store ID mono, status pill (Active success / Pending warning /
  Suspended danger / Archived neutral / Provisioning info), POS badge, created, actions menu.
- Accent everywhere = KloudC blue; never a store brand.

### 2.3 Logged-out pages (login, sign-up, pending, owner set-up)

- ≥1024: two columns. Left 44 %: `--kc-gradient` panel, white logo lockup, tagline
  ("Make Your POS Inventory Work Like Your Real Store."), three one-line value points (Find inventory
  problems before they cost margin / Order the right amount from every vendor / Keep the team on the
  same page), plane artwork (`brand/kloudc-retailops/plane.svg`) at 12 % opacity. Right: the form card
  (400 px) on `--bg`.
- <1024: logo lockup on top, form card, value points collapsed under "What is KloudC RetailOps?".
- Login form keeps Store ID → Username → Password (→ 6-digit code). When the Store ID resolves, a chip
  shows the store logo + name ("Signing in to <store>"). ⓘ next to Store ID ("Your store's ID is in
  your welcome email, or ask your owner").
- Footer: *Privacy* · *Support* · *Contact* · © 2026 KloudC LLC · Connecticut, USA (and *Terms* only
  when `TERMS_URL` is set).

### 2.4 Page header

```
[area chip: ● Ordering]                                     
Vendor Calendar  ⓘ                         [secondary] [Primary action]
One-line subtitle (optional, muted)
[tabs / tile tabs, optional]
```

API (W): `<PageHeader pageId="vendor_calendar" title? subtitle? area? actions? tabs? />`. `title`,
`area` and the ⓘ come from the page registry (`content/help/pages.json`) by `pageId`, so a page
title can't differ between header, sidebar, search and Help. One primary action max. On phone the
actions collapse into a `⋯` menu except the primary. The header is not sticky on dashboards; on list
pages the **compact list header** below is the sticky part.

### 2.5 Compact list header (web + app)

One slim, frozen row (exists in the app as `CompactListHeader`; W builds the web twin
`<ListHeader>`): search field (40 px), then icon buttons: filter (badge = active filter count),
settings, ⓘ. Second row (optional): chips that scroll sideways and never wrap (active filters with
×, Open/Closed counts). Explanatory text ("On hand is {pos}'s live count as of 3:05 PM", "Updated
2:08 AM") lives in the ⓘ panel, not above the list. Height ≤ 104 px with chips.

### 2.6 Cards and tiles

- **Card**: `--surface`, `--border`, radius 10, padding 20 (16 on phone), `--shadow-xs`. Title row:
  h3 + optional ⓘ + right-aligned link ("Calendar →").
- **KPI tile**: label (caps), value (`kpi`), sub-line (muted), optional delta chip (▲ success / ▼
  danger, with the word "up"/"down" for screen readers), ⓘ. 3 px top edge in the area colour of the
  page it opens; whole tile is the link. Sales numbers hidden for "Hide sales numbers" users shows
  "Hidden" + eye-off icon (exists).
- **Hub tile** (Store tab, Insights hub, Setup hub, Help Center topics): 40 px icon square on
  `--area-soft` with `--area` icon, title, one-line subtitle, optional count badge, chevron. Grid: 1
  col (<640), 2 (640-1023), 3 (≥1024).
- **Tile tabs** (one-screen grouping): a row of hub tiles acting as tabs; the chosen tile gets a
  2 px `--area` border + `--area-soft` fill, `role="tab"`, `aria-selected`; the content renders right
  below; last choice remembered per page (`kc.tab.<pageId>` / app `shared_preferences`).

### 2.7 Tables

- Inside a card, full width; header row `--surface-2`, `label` style, sticky under the list header;
  rows 44 px (dense 36 px via the settings button, remembered); 1 px row dividers, row hover
  `--surface-2`; numbers right-aligned, tabular; sortable headers keep `SortableTh` with an arrow
  icon and `aria-sort`.
- Row actions: an icon `⋯` menu at the row end; destructive actions inside it, in danger text, with a
  confirm or an Undo toast.
- <768: tables with more than 4 columns switch to stacked row cards (key column as title, the rest as
  label/value pairs) - no sideways page scroll anywhere (Playwright check at 375).
- Empty, loading (skeleton rows) and error (inline danger callout with Retry) states for every table.

### 2.8 Empty states

Centered in the card: 48 px icon in an `--area-soft` circle, h3 title ("No open checks"), one
sentence of why/what next, optional primary action, and a *Learn more* help link
(`<HelpLink article=...>`). No stock illustrations.

### 2.9 Toasts and banners

- Toast: bottom-right (desktop) / bottom-center above the tab bar (phone), max 3 stacked, 4 s
  (8 s with an action such as **Undo**), icon + text + action, `role="status"` `aria-live="polite"`
  (danger: `role="alert"`). App: `showAppSnackBar` restyled to the same tones.
- Banner (page-level): full-width callout under the page header for state that persists (demo store
  "sends are off", sync failed, app update required). Tone colours, icon, dismiss only when safe.
- Connectivity banner (app, exists): warning tone.

### 2.10 Forms

Label above (14/600), control, hint below (13 muted), error below (danger, icon, replaces the hint).
Optional fields say "(optional)"; nothing marks required. Inputs 40 px (44 touch), radius 6,
`--input-border`, focus ring. Selects, date pickers and toggles share the height. Group long forms
into cards with h3 titles; the primary button sits bottom-right (bottom full-width on phone).
Password fields keep Show/Hide. Inline validation on blur, never on each keystroke.

### 2.11 Buttons

| Variant | Look | Use |
|---|---|---|
| Primary | `--brand` fill, white text | One per view: the main thing to do |
| Secondary | `--surface` fill, `--border-strong` border, `--text` | Other actions |
| Tertiary / ghost | transparent, `--text-2`, hover `--surface-2` | Toolbars, cancel |
| Danger | `danger` fill, white text (only in confirm dialogs); elsewhere danger text ghost | Delete, archive, suspend |
| Link | `--kc-link`, underline on hover | Inline navigation |
| Icon | 36 px square ghost, tooltip + `aria-label` | Row and header icons |

Sizes sm 32 / md 40 / lg 48. Loading: spinner replaces the leading icon, label stays, button disabled.
Disabled: 45 % opacity + `cursor:not-allowed`; every custom `:hover` carries `:not(:disabled)`
(existing rule).

### 2.12 Badges, pills, chips

- **Status pill**: soft bg + text colour + 12 px icon + label, radius pill, 12/600. Glossary-backed
  pills keep `<Term>` (tap → definition).
- **Count badge**: `--brand` fill, white, min 18 px, "99+".
- **Area chip** (page header): `--area-soft` bg, `--area` dot + label.
- **Filter chip**: secondary look; selected = `--brand-soft` + `--brand-text`; removable ×.
- **"New" badge**: info soft, used for 14 days after a feature ships (date in the page registry).

### 2.13 Tabs

- **Segmented** (2-4 short options, e.g. Help | Glossary, Open | Closed): `--surface-2` track,
  selected `--surface` + `--shadow-xs`.
- **Underline tabs** (sections of one page, e.g. Inventory Reports): 2 px `--area` indicator.
- **Tile tabs** (hubs - section 2.6).
All are `role="tablist"`, arrow-key navigable, remembered where the page says so.

### 2.14 Dialogs, sheets, popovers

Dialog: max 560 px, radius 14, `--shadow-lg`, title + close, body, footer actions right; Esc closes;
focus returns to the opener. Phone (<640): becomes a bottom sheet (radius 16 top, drag handle,
max 92 vh). Popover (term definitions, ⓘ on desktop): 320 px, arrow, closes on outside click/Esc.

### 2.15 Charts (Insights, Overview)

Series colours: the area colour of the page first, then `--kc-sky`, `--kc-blue`, `--text-muted`.
Comparison ("same day last week") is always the muted grey dashed line. Axis/grid `--border`.
Tooltips use the popover style. Every chart has a table/CSV fallback (exists).

---

## 3. Information architecture

Rule: **every page is reachable in ≤2 clicks/taps** from anywhere (sidebar/tab = 1, group or tile = 2)
and from search in one. Web and app use the **same groups, names and order**. Names come from the
page registry.

### 3.1 Website - store users (owner, admin, staff)

| Sidebar | Pages (route) | Change from today |
|---|---|---|
| **Home** | Overview `/` | Renamed "Home" (matches the app); greeting band + Today card + KPI tiles |
| **Daily Checklists** | `/daily-checklists` | stays top-level |
| **Ordering** (violet) | Reorder Candidates `/reorder` · New order `/supplier-projection` · Vendor Calendar `/vendor-calendar` · Purchase Order Cart `/purchase-order` · Placed Orders `/placed-orders` | Cart gets a menu link (was only the floating button); **Delivery Review moves to Stock** (as in the app) |
| **Stock** (teal) | Fix Issues `/fix-issues` · Delivery Review `/delivery-review` · Invoices `/invoices` · Inventory Reports `/inventory-reports` · Reconciliation `/reconciliation` | **Invoices moves here** from top-level (as in the app); Home keeps an *Upload invoice* quick action so staff stay at 1 click |
| **Sales** (raspberry) | Items Sold `/items-sold` · Open Checks `/open-orders` · Insights `/insights` | The 11 Insights links collapse into **one Insights hub**: tile tabs Daily compare, Prep forecast, Menu trends, Add-ons, Returning customers, Loss watch*, Odd checks* (*admins) and an "Also in {pos}" tile row (Sales channels, Check size & guests, Busy hours, Best & worst sellers). Each keeps its URL (`/insights/<page>`); last tile remembered. Group label "Sales" for everyone (was "Sales & Insights") |
| **Team** (orange) | Tasks `/team/tasks` · Chat `/team/chat` · News `/team/announcements` · Flyers `/flyers` | Recurring tasks becomes a tab inside Tasks (admins) and also lives in Setup |
| **Setup** (slate, admins) | **People**: Users `/users`, Teams `/teams` · **Connections**: Integrations `/settings/integrations`, Inventory from {pos} `/inventory-api` · **Ordering setup**: PO sections `/po-sections`, Vendor contacts `/vendor-contacts`, Category & vendor leads `/issue-leads` · **Team setup**: Checklist builder `/checklists/admin`, Recurring tasks `/team/templates`, Reported messages `/team/moderation` | Small sub-headings inside the group (no extra click) |
| Footer | Help `/help` · Label Scanner ↗ | Change password + Log out move to the user menu |
| User menu | Change password `/change-password` · Appearance · Help center · Glossary `/glossary` · Contact support · Privacy ↗ · Sign out | new |

Rules kept from `featureRoutes.js`: never show what the user can't open; a group with one visible
link renders as that link; empty groups vanish; landing page = first accessible page; old URLs
redirect (`/transfer-review`, `/price-changes`, `/barcode-reports`, `/insights/menu`, `/categories`,
`/notifications`). `NAV_GROUPS` stays the one place the grouping is defined; each route gains
`area` and `pageId`.

### 3.2 Website - master (Store ID `PLATFORM`)

One screen: KloudC top band + tile tabs **Requests · Stores · Hosting · Audit · Help** (section 2.2).
Store settings open in a side drawer from a Stores row (not a new page). Two-step sign-in set-up
lives in the user menu (QR flow exists). Every master page is 1 click.

### 3.3 App - store users

Bottom tabs stay **Home · Store · Scan · Team · More** (`app_tabs.dart`).

| Tab | Content | Change |
|---|---|---|
| **Home** | Greeting band (store brand tint), Today card (orders due, critical vendors, closing reminder, sales vs last week), quick-action tiles: Print labels, Upload invoice, Fix issues, New order, Daily Checklists | Tiles get area colours; search icon in the app bar |
| **Store** | Category tile tabs **Ordering · Stock · Sales** (remembered, exists) with the same tools and order as the website groups (section 3.1); Insights opens its own tile-tab hub | Area colours on the category tiles and tool icons |
| **Scan** | Camera lookup, manual entry, print label history | restyle only |
| **Team** | Segmented tabs **Tasks · Chat · News · Flyers** (remembered) | restyle only |
| **More** | **Account** (name/role card, Notifications, Appearance *(new)*, Face ID, Change password, Delete my account) · **Help & info** (Help Center, Glossary, Contact support *(new)*, About, Privacy) · **Setup** (admins: Users, PO sections, Vendor contacts, Checklist builder, Public checklist links, Inventory from {pos}, Category & vendor leads, Reported messages) · Switch store · Sign out | Help Center replaces the single Help screen |

Every tool's search entry (`feature_index.dart`) keeps `location` ("Store › Ordering") and gains a
`helpPageId`. `navigation_reachability_test.dart` extends to "every page in `pages.json` with
`app.location` is reachable in ≤2 taps from its tab".

### 3.4 App - master

The master console stays **website only** (decision). If someone signs in to the app with Store ID
`PLATFORM`, the login screen shows an info card: "The KloudC RetailOps platform console is on the
website" with a button that opens the website URL (no sign-in attempted).

### 3.5 Naming (one name per thing, web = app)

Home (not Overview) · Open Checks · New order (subtitle "Supplier Projection") · Purchase Order Cart ·
Placed Orders · Vendor Calendar · Fix Issues · Delivery Review · Invoices · Inventory Reports ·
Reconciliation · Items Sold · Insights · Tasks · Chat · News · Flyers · Help Center · Glossary.
"StoreOps" is replaced by "KloudC RetailOps" in every visible string (web title, login, footer, app
name, emails, push titles) - W, M, S each in their own files; H in content.

---

## 4. Help system

Three layers, **one content source** (`content/help/`, section 5.2) used by the website, the app and
the server:

1. **Help Center** - full guides, getting started, FAQ, troubleshooting, contact.
2. **Glossary** - every term the UI uses: definition + where it appears.
3. **Contextual ⓘ** - on every page header, list header, card/section and form field that needs it;
   content keyed by `pageId` (+ optional `section`).

Writing rules (existing glossary rules, now for all help): plain English for store staff, short
sentences, second person ("you"), no em/en dashes, real thresholds quoted from code via placeholders
where they exist, `{pos}` for the store's POS name, `{store}` for the store's name, never a real store,
person or vendor name. Info summaries ≤160 characters; points ≤5 of ≤120 characters each.

### 4.1 Help Center (web `/help`, app More › Help & info › Help Center)

Layout: search box on top; then tile tabs **Start here · Guides · FAQ · Troubleshooting · Glossary ·
Contact**; last tab remembered. Articles open at `/help/<articleId>` (web) / a pushed screen (app)
with a "On this page" list, "Related" links and "Was this helpful? Email us" (mailto, nothing sent
automatically). Audience filter chips (Owner / Staff / Master) default to the signed-in role.

**Start here** (three tracks, each a numbered checklist that links to the right page):

- *Owners and admins* (10 steps): request access → owner set-up link and first password → connect
  your POS (Integrations) → first inventory (upload or Inventory from {pos}) → vendors, delivery days
  and lead times (Vendor Calendar) → PO sections and vendor contacts → add users and choose what each
  can open → teams, checklists and News → install the phone app and choose alerts → your daily
  routine (Home → Today → Reorder/New order → send PO → Delivery Review).
- *Staff* (7 steps): sign in with Store ID, username, password → Home and the Today card → your
  daily checklist → upload an invoice → fix issues assigned to you → tasks, chat and News → scan a
  barcode and print a label.
- *KloudC master* (7 steps): sign in with Store ID `PLATFORM` and two-step code → review requests
  and approve with a starter pack → watch provisioning → store status (suspend, resume, archive) →
  hosting requests (shared vs dedicated) → the audit log → helping a store (what support can and
  can't see).

**Guides** (one article per feature, grouped by area; article ids):

| Area | Articles |
|---|---|
| Basics | `basics-signin` (Store ID, two-step, Face ID), `basics-navigation` (menu, search, ⓘ, Help), `basics-roles` (owner/admin/staff, feature access, Hide sales numbers), `basics-data-freshness` (what is live vs nightly vs on upload; replaces "What's live and what's on a schedule"), `basics-limits` (what RetailOps doesn't do yet), `basics-downloads` (CSV/PDF downloads and sharing), `basics-app` (the phone app, tabs, offline behaviour, updates) |
| Home | `home-today` (Today card, critical vendors, orders due, closing reminder), `home-kpis`, `home-upload-inventory`, `home-weekly-digest`, `home-job-status` |
| Ordering | `reorder`, `new-order` (Supplier Projection, suggested qty, similar products, vendor minimums), `vendor-calendar` (schedules, suggested schedules, placed/skip/other), `po-cart` (cart, PO PDF, Send PO, CC), `placed-orders` |
| Stock | `fix-issues` (every list, close reasons, snooze, came back, leads), `delivery-review` (suggested deliveries, confirm & log), `invoices` (upload, WhatsApp, {pos} email, trash, transfers), `inventory-reports` (price changes, Store vs Container, vendor price comparison), `reconciliation` (+ worked example), `ask-bot` |
| Sales | `items-sold`, `open-checks`, `insights` (hub) and one per insight: `insights-daily`, `insights-prep`, `insights-menu-trends`, `insights-addons`, `insights-returning`, `insights-loss-watch`, `insights-odd-checks`, `insights-also-in-pos` |
| Team | `daily-checklists` (+ public links), `team-tasks` (+ recurring, stats), `team-chat` (channels, DMs, voice, pins, reports), `team-news`, `flyers`, `team-rules` |
| Setup | `setup-users`, `setup-teams`, `setup-integrations` (POS, WhatsApp, email, AI), `setup-inventory-from-pos`, `setup-po-sections`, `setup-vendor-contacts`, `setup-leads`, `setup-checklist-builder`, `setup-moderation`, `setup-branding` (logo, colours) |
| Account | `account-password`, `account-alerts` (phone notifications), `account-appearance`, `account-delete`, `account-privacy` |
| Master | `master-requests`, `master-stores`, `master-store-settings`, `master-hosting`, `master-audit`, `master-security` (two-step, recovery), `master-support-playbook` |

Existing Help text (web `HelpPage.jsx`, app `help_content.dart`) is the starting material: H moves it
into these articles (deduplicated, rewritten to the rules), then both old copies are deleted.

**FAQ** (`faq.json`, ~35 entries, each tagged area + audience). Topics at least: What is a Store ID
and where do I find it? · Why can't I see a page my colleague sees? · Why are sales numbers hidden
for me? · How often do sales update? · Do I have to upload inventory every day? · What if I miss a
day? · What does Critical mean? (links glossary) · How is the suggested quantity worked out? · Why
does a vendor need a minimum order? · What is the Container? · Can I undo a closed issue? · What
happens when I snooze? · How do I send a PO by email, and who gets a copy? · What does "uncertain"
mean on a sent PO? · How do deliveries get logged? · Why does Reconciliation show a variance? · Who
sees Loss watch and Odd checks? · Which numbers repeat {pos}'s own reports? · How do I add a
user / reset a password? · How do I set up a checklist and its WhatsApp group? · Can staff submit a
checklist without logging in? · How do Tasks, Chat and News differ? · Who sees a reported message? ·
How do I choose phone alerts? · Does the app work offline? · How do I switch store on the phone? ·
How do I change light/dark? · How do I delete my account? · What data does KloudC keep, and where?
(privacy link) · How do I connect Toast or Clover? · What is the demo store? · How do I request a
dedicated server? · How do I contact support?

**Troubleshooting** (`troubleshooting.json`, symptom → likely causes → fixes, ~20): can't sign in
(wrong Store ID / username / password / locked / two-step code / store suspended) · "Couldn't load"
on sales tiles · sales sync failed or is late · inventory upload rejected (wrong file, edited
columns, re-saved in Excel) · numbers look old · a page is missing from my menu · Label Scanner link
missing · push alerts not arriving · WhatsApp message not sent · PO email failed / uncertain ·
delivery not suggested · invoice stuck in "In progress" · app says an update is required · camera
won't scan · Face ID stopped working · chat voice note won't record · demo store won't send ·
website looks wrong (cache/refresh, supported browsers) · how to send us a good support request.

**Contact** (`contact.json`): support@kloudc.com (mailto with subject "RetailOps support - <Store
ID>" and a body template: page, what you did, what you expected, screenshot; the app adds app version
and device; **never** a password), support page, contact page, privacy policy, company line
"KloudC LLC, Connecticut, USA". No phone number (none published).

### 4.2 Glossary (web `/glossary` = Help Center tab; app More › Help & info › Glossary)

Two kinds of term, one list, one API (`GET /api/glossary`, existing):

- **Status / label terms** - the existing ~256 in `backend/app/glossary.py` (rules computed from the
  code's constants). Unchanged source; gains `area` and `pages` (page ids) fields.
- **Concept terms** - new, written by H in `content/help/glossary.json`, merged into the same response
  by `glossary.py` (concept keys prefixed `concept_`). At least:

  Store ID · Store · Owner · Admin · Staff · Master (KloudC) · Feature access · Hide sales numbers ·
  POS · Toast · Clover · Sales sync · Inventory upload · Retail items export · On hand · Container ·
  Store vs Container · Item · SKU · Barcode (UPC/EAN) · Vendor · Vendor contact · Vendor minimum
  order · Lead time · Delivery days · Order deadline · Vendor schedule (suggested schedule) ·
  Lookback · Weekly sales · Days of stock · Reorder candidate · Supplier projection (New order) ·
  Suggested quantity · Similar product · Purchase order (PO) · PO section · Purchase Order Cart ·
  Send PO · CC · Placed order · Placed outside the app · Delivery · Suggested delivery · Delivery
  review · Purchase log · Invoice · Return · Transfer · Invoice trash · Reconciliation · Variance ·
  Fix Issues · Issue list · Close reason · Snooze · Came back · Category lead · Vendor lead ·
  Insights · Daily compare · Prep forecast · Menu trends · Add-on · Returning customer · Sales channel
  · Check · Open check · Check size · Guests · Busy hours · Loss watch · Odd check · Also in {pos} ·
  Today card · Critical vendor · Job status · Weekly digest · Ask Inventory Bot · Label Scanner ·
  Print label · Daily checklist · Closing checklist · Public checklist link · Checklist builder ·
  Task · Recurring task · Task stats · Chat channel · Direct message · Pinned message · News ·
  Acknowledge · Reported message · Team rules · Flyer · WhatsApp group · Integration · Two-step
  sign-in · Request access · Starter pack · Provisioning · Suspend · Resume · Archive · Shared
  hosting · Dedicated server · Audit log · Demo store · Store timezone · Brand colour · Appearance
  (light/dark).

Entry shape (both kinds after merge): `{key, label, category, area, definition, rule?, tone?,
aliases[], where (text), pages[] (page ids), related[] (keys)}`. The glossary screen: search box,
category tile tabs (remembered), A-Z within; each entry shows definition, rule (if any), "Where you
see it" as links to the pages, related terms. `<Term k>` (web) / `TermTap` (app) keep working.

### 4.3 Contextual ⓘ info

Every page header has ⓘ; every compact list header has ⓘ; cards/sections and form fields get ⓘ where
the registry lists a `section`. Tap/click opens:

- **Desktop**: popover (sections, fields) or the right-side Help panel (page-level ⓘ).
- **Phone (web <640) and app**: bottom sheet (`showInfoSheet`, exists).

Panel content, in order: title · **summary** · up to 5 **points** · "Where the numbers come from"
(**source** + **freshness**) · page-supplied live lines (e.g. "Updated 2:08 AM", "On hand as of
3:05 PM" - passed as `extra`, not in JSON) · **Terms on this page** (glossary chips) · **Who can see
this** (from `gating`, e.g. "Admins only", "Needs Sales insights access") · "Read the full guide →"
(article) · "Still stuck? Email support".

**Page registry and info topics** (every `pages.json` entry; `S` = section ids that also get ⓘ):

| pageId | Web route | App location | Area | Info topics (summary + points cover) |
|---|---|---|---|---|
| `login` | `/` (logged out) | Login | - | Store ID, which username, forgot password, two-step code, request access |
| `signup` | `/signup` | - | - | Who can request, what happens next, POS choice, timezone; S: `store_code`, `pos` |
| `signup_pending` | `/signup/pending` | - | - | Review time, email you'll get, contact |
| `owner_setup` | `/setup` | - | - | One-time link, password rules, then sign in |
| `public_checklist` | public link | - | team | Submitting without a login, photo rules, who gets it |
| `home` | `/` | Home | home | What Today shows, KPI tiles, hidden sales, where to go next; S: `today`, `critical_vendors`, `orders_due`, `kpis`, `upload_inventory`, `vendor_schedule`, `weekly_digest`, `job_status`, `sales_accuracy` |
| `daily_checklists` | `/daily-checklists` | Home › Daily Checklists | team | Which checklist when, photos, WhatsApp copy, history; S: `history` |
| `reorder` | `/reorder` | Store › Ordering | ordering | Urgency levels, lookback, lead time, by vendor, order already placed; S: `urgency`, `lookback`, `critical` |
| `new_order` | `/supplier-projection` | Store › Ordering | ordering | How the projection works, suggested qty, vendor minimums, Review flags, similar products, add to cart; S: `suggested_qty`, `similar`, `review` |
| `vendor_calendar` | `/vendor-calendar` | Store › Ordering | ordering | Deadlines vs deliveries, schedules, suggested schedules, Placed/Skip/Other/Undo; S: `schedules`, `suggestions`, `vendors_without_schedule` |
| `cart` | `/purchase-order` | Store › Ordering | ordering | Lines by vendor, quantities, PO PDF, Send PO, CC, PO sections on the PDF; S: `send_po`, `send_log` |
| `placed_orders` | `/placed-orders` | Store › Ordering | ordering | What counts as placed, PDF vs sent, reorder from history, search |
| `vendor_contacts` | `/vendor-contacts` | More › Setup | setup | Who gets each PO, default CC |
| `fix_issues` | `/fix-issues` | Store › Stock | stock | The lists, close with a reason, snooze, came back, grouped by PO section, leads; S: one per issue list (`missing_barcode` ... `slow_review`), `closed_report` |
| `delivery_review` | `/delivery-review` | Store › Stock | stock | Suggested deliveries, net change, confirm & log, dismiss/undo |
| `invoices` | `/invoices` | Store › Stock | stock | Upload, statuses, WhatsApp and {pos} email, transfers, trash; S: `upload`, `trash` |
| `inventory_reports` | `/inventory-reports` | Store › Stock | stock | The tabs; S: `price_changes`, `store_vs_container`, `vendor_price_comparison` |
| `reconciliation` | `/reconciliation` | Store › Stock | stock | Purchased vs sold vs counted, variance, invoice gaps, log a purchase; S: `report`, `invoice_gaps`, `log_purchase` |
| `upload_inventory` | (Home card) | Store › Stock | stock | Which file, don't edit it, missing days are fine, delivery checkbox |
| `ask_bot` | chat bubble | Store › Stock | stock | What it can answer, data it uses, it can be wrong |
| `items_sold` | `/items-sold` | Store › Sales | sales | Date range, revenue vs units, sync time, download |
| `open_checks` | `/open-orders` | Store › Sales | sales | What an open check is, "check" (open too long), tab names |
| `insights` | `/insights` | Store › Sales › Insights | sales | The hub, data from {pos} analytics, counts-only mode |
| `insights_daily` … `insights_best_sellers` (11) | `/insights/<page>` | Store › Sales › Insights | sales | Per page: what it compares, period, who can see it, caveats |
| `team_tasks` | `/team/tasks` | Team › Tasks | team | Mine vs team, due/overdue, board, from template; S: `stats`, `templates` |
| `team_chat` | `/team/chat` | Team › Chat | team | Channels vs DMs, voice/docs, pins, seen-by, reporting; S: `settings`, `search` |
| `team_news` | `/team/announcements` | Team › News | team | Acknowledge, who sees it |
| `flyers` | `/flyers` | Team › Flyers | team | Templates, sending to WhatsApp groups, never in demo |
| `team_rules` | - | Team | team | What everyone agrees to |
| `users` | `/users` | More › Setup | setup | Roles, feature access, Hide sales numbers, reset password, deactivate |
| `teams` | `/teams` | More › Setup | setup | What teams are for (tasks, chat, checklists) |
| `checklist_builder` | `/checklists/admin` | More › Setup | setup | Items, photos, WhatsApp group, schedule; S: `public_links` |
| `issue_leads` | `/issue-leads` | More › Setup | setup | Category and vendor leads, daily lead tasks |
| `po_sections` | `/po-sections` | More › Setup | setup | What a PO section is, how items are placed |
| `inventory_from_pos` | `/inventory-api` | More › Setup | setup | Switching to {pos} inventory, readiness checks |
| `integrations` | `/settings/integrations` | - (web) | setup | POS, WhatsApp, email, AI: status, connect, test; S: `pos`, `whatsapp`, `email`, `ai` |
| `team_moderation` | `/team/moderation` | More › Setup | setup | Reported items, resolve/keep/remove |
| `team_templates` | `/team/templates` | More › Setup | setup | Recurring tasks, steps, days |
| `help` / `glossary` | `/help`, `/glossary` | More › Help & info | help | How to use help, search, contact |
| `change_password` | `/change-password` | More › Account | - | Password rules, and what happens to your other signed-in devices (H checks the real behaviour) |
| `notifications` | - | More › Account | - | Alert types, quiet hours, per-phone |
| `appearance` | user menu | More › Account | - | System/Light/Dark |
| `face_id` · `delete_account` · `about` | - | More | - | as named |
| `scan` · `manual_lookup` · `print_labels` | - | Scan | stock | Lookup, price/stock shown, printing, history |
| `no_access` · `not_available` | `/no-access`, `/not-available` | - | - | Why, who to ask |
| `platform_requests` | `/platform` Requests | - | platform | Review, approve with pack, reject, retry provisioning |
| `platform_stores` | Stores | - | platform | Statuses, suspend/resume/archive, settings drawer; S: `store_settings` |
| `platform_hosting` | Hosting | - | platform | Shared vs dedicated, request lifecycle, rollback |
| `platform_audit` | Audit | - | platform | What's logged, filters, retention |
| `platform_security` | user menu | - | platform | Two-step sign-in set-up and recovery |

Web: a node test fails if any `FEATURE_ROUTES` route or `PLATFORM_TABS` tab has no registry entry.
App: a test fails if any `featureIndex` entry's `helpPageId` or any `InfoButton(pageId)` literal is
missing from the bundled `pages.json`.

---

## 5. Ownership split and shared contracts

### 5.1 Builders and files

| Builder | Owns (writes) | Must not edit |
|---|---|---|
| **W** website design + navigation | `frontend/src/styles/tokens.css` (new; tokens out of index.css), `index.css`, `platform/platform.css`, `ui/**` except `ui/help/**`, `components/Sidebar.jsx` + new `TopBar.jsx`, `App.jsx`, `featureRoutes.js` (+ `area`, `pageId` per route), page layout/markup in `pages/**` and `components/**`, `index.html` (theme script, title), `store/branding.js` (defaults + dark brand text), login/sign-up/master console look, web brand rename, Playwright screenshot set | `content/**`, `ui/help/**`, help/glossary text in JSX |
| **H** help, glossary, info content + components | `content/help/**` (all JSON + `schema.json`), `backend/app/help_content.py` + `GET /api/help` + public subset + tests, `backend/app/glossary.py` (merge concept terms, add `area`/`pages`), `frontend/src/ui/help/**` (HelpProvider, InfoButton, HelpPanel, HelpCenter, HelpArticle, Glossary page, search index), replacing `pages/HelpPage.jsx` + `GlossaryPage.jsx`, app `lib/core/help/**` + `lib/features/help/**` + `lib/core/glossary/**`, app `assets/help/**` + `tool/sync_help_content.sh` | page layouts (asks W/M to place components), tokens |
| **M** mobile app design + navigation + help screens' place | `lib/core/theme/**` (new `kc_tokens.dart`), `lib/core/widgets/**`, `lib/app/**`, every `lib/features/**` except help, `pubspec.yaml`, app icons/name (`com.kloudc.retailops`, "KloudC RetailOps"), Appearance setting, More/Store/Home/Team/login redesign, wiring `InfoButton(pageId)` into `CompactListHeader`/app bars, the PLATFORM info card, device-matrix screenshots | `lib/core/help/**`, `lib/features/help/**`, `assets/help/**` |
| **S** server hardening + ops + pilot | `backend/**` except H's files, `Dockerfile` (adds `COPY content/` to both stages - see 5.2), `docker-compose*.yml`, `deploy/**`, `docs/*RUNBOOK*`, onboarding/pilot scripts, settings (`PRODUCT_NAME`, `BRAND_COLOR=#004BAD`, `BRAND_SECONDARY_COLOR=#2EA3F2`, `TERMS_URL` blank, support/privacy URLs), CSP (no external fonts/scripts needed), server-side brand rename (emails, push titles) | frontend, app, content |

Shared touch points (agree by these contracts, then build in parallel):

- **H ships stubs first** (first commit, within its first hour): `ui/help/index.js` exporting
  `HelpProvider`, `InfoButton`, `HelpLink`, `useHelp` with the APIs below and a minimal `pages.json`
  containing every page id (title + area + one-line summary). The app gets the same stubs in
  `lib/core/help/help.dart`. W and M code against these; H fills them in.
- W's `PageHeader` renders `<InfoButton pageId={pageId} />`; M's `CompactListHeader` gains
  `infoPageId` and renders `InfoButton(pageId: ...)` (keeping today's `onInfo` + `extra` lines so
  dynamic lines still show).
- Titles: W and M read page titles from the registry (`useHelp().page(id).title` /
  `helpPage(id).title`) - H owns the words, W/M own placement.
- `featureRoutes.js` is W's; H adds nothing there. The route → pageId map is the `pageId` field W adds.
- `Dockerfile` is S's; S adds the `content/` copy lines on day one (they're harmless before H fills
  the folder).
- `PLATFORM_CONTRACT.md`: each builder appends its own "As built - <part>" section at the end; no
  one edits another's section.

### 5.2 Shared content: location and format

```
content/help/                      (repo root of storeops, next to backend/ and frontend/)
  manifest.json        {"version": 3, "updated": "2026-10-20", "locale": "en"}  - bump on every change
  schema.json          JSON Schema (2020-12) for every file below
  pages.json           [PageEntry]           - the page registry (section 4.3)
  articles/*.json      one Article per file   - Guides + Start-here tracks
  getting-started.json {owner: Track, staff: Track, master: Track}
  faq.json             [Faq]
  troubleshooting.json [Trouble]
  glossary.json        [ConceptTerm]          - merged into /api/glossary by glossary.py
  contact.json         {email, support_url, contact_url, privacy_url, terms_url: "", company, location}
```

Types (all strings plain text with two inline marks only: `**bold**` and links
`[text](page:<pageId>)`, `[text](article:<id>)`, `[text](term:<key>)`, `[text](https://...)`; a
~40-line renderer on each side, no markdown library):

```jsonc
// PageEntry
{ "id": "reorder", "title": "Reorder Candidates", "area": "ordering",
  "audience": ["owner", "admin", "staff"],            // who the text is written for
  "web": { "route": "/reorder" }, "app": { "location": "Store › Ordering" },   // null where absent
  "gating": { "feature": "reorder_candidates", "admin": false, "salesOnly": false },
  "public": false,                                    // true: served logged-out (login, signup...)
  "info": { "summary": "What to reorder now, by vendor and urgency.",
            "points": ["**Critical** means ...", "..."],
            "source": "{pos} sales (nightly) and your latest inventory upload.",
            "freshness": "Sales: last night. Stock: latest upload." },
  "sections": { "urgency": { "title": "Urgency", "summary": "...", "points": [] } },
  "terms": ["urgency_critical", "concept_lead_time"],
  "article": "reorder", "new_until": null }

// Article
{ "id": "reorder", "title": "Reorder Candidates", "area": "ordering", "audience": ["owner","admin","staff"],
  "summary": "...", "pages": ["reorder"], "related": ["new-order", "vendor-calendar"],
  "blocks": [ {"type": "h", "text": "..."}, {"type": "p", "text": "..."},
              {"type": "steps", "items": ["..."]}, {"type": "bullets", "items": ["..."]},
              {"type": "tip" | "note" | "warning", "text": "..."},
              {"type": "example", "id": "reconciliation_worked"} ] }   // a live widget the client knows

// Faq        { "id", "q", "a": [Block], "area", "audience": [...], "pages": [...] }
// Trouble    { "id", "symptom", "causes": ["..."], "fixes": [Block], "area", "audience", "pages" }
// ConceptTerm{ "key": "concept_lead_time", "label", "category", "area", "definition", "aliases", "pages", "related" }
// Track      { "title", "intro", "steps": [{ "title", "text", "page": "<pageId>" | null, "article": "<id>" | null }] }
```

Placeholders allowed: `{pos}`, `{store}`, `{support_email}`, and the glossary's existing threshold
placeholders. Anything else fails validation.

**Delivery - one source, three readers:**

- **Server** (`help_content.py`, H): loads `content/help/` once at start (path:
  `Path(__file__).resolve().parents[2] / "content" / "help"`, which is `/app/content/help` in the
  image), validates against `schema.json`, and serves `GET /api/help` (signed-in; this store's words
  filled in; `{version, pages, articles, getting_started, faq, troubleshooting, contact}`, gzip,
  `ETag` = manifest version + store words hash) and `GET /api/public/help` (only `public: true`
  pages + contact; for logged-out screens of older app builds). `/api/glossary` merges
  `glossary.json`.
- **Website** (H): imports the JSON at build time through a Vite alias (`@help` →
  `../content/help`; `vite.config.js` adds `resolve.alias` and `server.fs.allow: ['..']`), so help
  works logged-out with no request and no flash; placeholders are filled client-side from the
  branding store. Docker web stage: `COPY content/ /src/content/` (S).
- **App** (H): a committed copy in `storeops-app/assets/help/` produced by
  `tool/sync_help_content.sh` (copies from `../storeops/content/help/`, writes the manifest
  version), listed under `flutter: assets:`. At runtime `helpContentProvider` loads the bundle, then
  fetches `/api/help` and uses the server copy when its version is newer (cached in
  `shared_preferences`), so an app on the store's phone never shows older help than the server.

**Never-drift checks** (H): server pytest validates schema, unique ids, every cross-reference
(`page:`, `article:`, `term:`), every `terms[]` key exists in the merged glossary, length limits, no
em/en dashes, no unknown placeholder, no origin-store names; web node test (registry ↔ routes); app
test (bundle parses, manifest version = file hash recorded by the sync script, every `helpPageId` and
`InfoButton` literal resolves). `scripts/check_help_sync.sh` (H) compares the app copy's hash with
`content/help` when both repos sit side by side (run in Verify).

### 5.3 Component APIs

**Website (`frontend/src/ui/help/`, H)**

```jsx
<HelpProvider>{app}</HelpProvider>            // in main.jsx above the router; reads @help JSON + branding words
const help = useHelp();
help.page(id)            // -> PageEntry (filled) | null
help.section(id, sec)    // -> section entry | null
help.article(id); help.term(key); help.search(q, {audience}) // -> [{kind:'page'|'article'|'faq'|'term', id, title, where, score}]

<InfoButton pageId="reorder" section?="urgency" extra?={<LastUpdatedLine .../>}
            variant?="icon"|"inline" label?="About this page" placement?="bottom-end" />
  // icon 16px in a 32px button (44px hit area on touch), aria-label "About <title>",
  // aria-expanded, aria-controls; popover on >=640px for sections, Help panel for page-level, sheet below 640px.
<HelpLink article="reorder" anchor?="lookback">Learn more</HelpLink>   // -> /help/reorder#lookback (JS scroll; HashRouter)
<HelpPanel />                                 // the right-side drawer, opened by the top bar ? and page ⓘ
<HelpCenter tab?="start"|"guides"|"faq"|"troubleshooting"|"glossary"|"contact" />  // /help, /help/:tab, /help/a/:articleId
<GlossaryList category? />                    // /glossary
<Term k="urgency_critical">Critical</Term>    // unchanged API
<FieldInfo pageId="signup" section="store_code" />  // small ⓘ beside a form label (popover)
```

**App (`lib/core/help/`, H)**

```dart
final helpContentProvider = FutureProvider<HelpContent>(...);   // bundle -> server refresh
HelpPage? helpPage(WidgetRef ref, String pageId);
InfoButton(pageId: 'reorder', section: 'urgency', extra: [LastUpdatedLine(...)]);  // IconButton(Icons.info_outline) 44x44 -> showInfoSheet
HelpCenterScreen(initialTab: HelpTab.start);     // More › Help & info › Help Center (route /more/help)
HelpArticleScreen(articleId: 'reorder');         // route /more/help/a/:id
GlossaryScreen(initialCategory: ...);            // exists; restyled, concept terms included
ContactSupportTile();                            // mailto with Store ID + app version, never auto-sends
HelpText(String text);                           // renders **bold** + page:/article:/term: links (go_router)
```

`CompactListHeader` (M) gains `String? infoPageId` and `List<Widget> infoExtra`; when set, its ⓘ
renders `InfoButton(pageId: infoPageId, extra: infoExtra)`.

### 5.4 Acceptance (each builder, before its Verify stage)

- Screenshots (W, M): every main page at 375 / 768 / 1280 / 1440 in light and dark, master console,
  login, sign-up, Help Center, Glossary, an ⓘ panel open; no origin-store names visible.
- Contrast: a token check script asserts every documented text/background pair ≥4.5:1 (UI parts
  ≥3:1) in both themes, including a store brand of `#FFD400` (worst case yellow) and `#111111`.
- No sideways page scroll at 375; every interactive element has a visible focus ring and ≥44 px hit
  area on touch; `prefers-reduced-motion` honoured.
- Navigation: every registry page reachable in ≤2 clicks/taps (web Playwright + app
  `navigation_reachability_test.dart`); old URLs still redirect; search finds every page.
- Help: every page has ⓘ with registry content; Help Center tracks link to live pages; content tests
  green; app bundle hash matches.
- Existing suites stay green (backend pytest, web node tests + build, `flutter analyze` + tests with
  `--concurrency=2`, Playwright) - heavy runs via `m1heavy.sh` only.

---

## 6. Decisions made for the owner (reversible)

1. Product default brand colour becomes KloudC blue `#004BAD` / sky `#2EA3F2` (was teal). Stores
   with their own colours keep them.
2. System fonts, no Inter/Google Fonts: faster, no third-party request, no CSP change.
3. Area colours: Ordering violet, Stock teal, Sales raspberry, Team orange, Setup slate, Platform
   KloudC blue - identity only, never status.
4. Website gets light/dark with "System" as default; the app adds an Appearance setting but **keeps
   Dark as its default** (the earlier explicit choice).
5. App stops forcing Roboto on iOS (system font).
6. Website moves Invoices and Delivery Review under Stock (matches the app); Home keeps an Upload
   invoice quick action.
7. The 11 Insights links become one Insights hub with remembered tile tabs (URLs unchanged).
8. Change password and Log out move from the sidebar to a top-bar user menu; the floating cart
   becomes a top-bar cart button.
9. Master console stays website-only; the app explains this when Store ID `PLATFORM` is typed.
10. Help content moves to `content/help/*.json` (one source); the web JSX help and the app's Dart
    copy are deleted once ported. Concept terms join the existing glossary API.
11. Lucide icon paths copied inline (ISC licence) instead of adding an npm icon package.
12. No Terms link until `TERMS_URL` is set (no Terms page exists on kloudc.com yet).
