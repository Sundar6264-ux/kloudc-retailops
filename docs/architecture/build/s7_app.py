from doc_core import Raw, esc

LABELS_EPS = [
    "GET /api/lookup/{upc}", "GET /api/search", "POST /api/print", "GET /api/print/{job_id}", "POST /api/products/custom",
    "GET /api/preview/{upc}.png", "GET /api/print-history",
]


def render(d, ctx):
    cm = ctx["clientmap"]
    d.h(1, "7", "Mobile app")
    d.h(2, "7.1", "Stack, start-up and the update gate")
    d.ul(
        [
            "Flutter (Dart 3), <b>Riverpod 2</b> (state), <b>go_router 16</b> (routes), <b>Dio 5</b> (HTTP). Version <code>2.0.0+8</code> in pubspec (the build number is bumped only at release). iOS 15.5+, bundle <code>com.spicetown.app</code>; Android package the same.",
            "<code>main.dart</code> &rarr; <code>bootstrap()</code> (ProviderScope + FreshnessObserver for resume) &rarr; <code>SpicetownApp</code> &rarr; <code>UpdateGate</code> wrapping the router.",
            "<b>UpdateGate</b>: <code>GET /api/app/config</code> (no login) gives min_supported_build_ios / _android and store URLs; a build below the minimum gets a blocking 'Update required' screen. Checked at launch and on every return to the foreground; a failed check never blocks. Both minimums are 0 (never block) today.",
            "<b>Router redirect</b> enforces the session life cycle: bootstrapping &rarr; <code>/splash</code>, unauthenticated &rarr; <code>/login</code>, authenticated &rarr; <code>/home</code>.",
            "<b>API base</b>: <code>--dart-define API_BASE_URL</code> (default <code>https://ops.spicetown.shop</code>) and LABELS_BASE_URL (default <code>https://labels.spicetown.shop</code>).",
        ]
    )

    d.h(2, "7.2", "Tabs and screens")
    d.p(
        "A <code>StatefulShellRoute.indexedStack</code> keeps five branch navigators (each tab keeps its own stack). "
        "Most detail screens are pushed with <code>MaterialPageRoute</code> from tiles; every tile and search result "
        "comes from one <code>featureIndex</code> (title, access rule, destination), so Store, More &rsaquo; Setup and "
        "search can never disagree about who may open what. Old paths redirect (<code>/orders</code>, <code>/reports</code>, "
        "<code>/ordering</code> &rarr; <code>/store</code>; <code>/more/insights</code> &rarr; <code>/store/insights</code>; "
        "<code>/more/flyers</code> &rarr; Team &rsaquo; Flyers)."
    )
    d.fig(
        r"""
flowchart LR
  SH["App shell<br/>bottom tabs"] --> H["Home /home"]
  SH --> S["Store /store"]
  SH --> SC["Scan /scan"]
  H --> H1["Greeting, search, Refresh all, cart badge"]
  H --> H2["Announcement ack banner"]
  H --> H3["Today card: My tasks, Orders due,<br/>Critical, Open checks, Invoices, Checklists"]
  H --> H4["Quick actions (unchanged):<br/>Print labels, Upload invoice,<br/>Fix issues, New order"]
  H --> H5["Daily checklists card"]
  H --> H6["Recent invoices, Recent orders"]
  H3 --> H3a["Critical sheet: Close, Skip this week,<br/>Remind me later, Order already placed"]
  H3 --> H3b["Orders due sheet: Placed, Skip, Other, Undo"]
  S --> S1["Ordering: Reorder Candidates, New order,<br/>Vendor Calendar, Purchase Order Cart,<br/>Placed Orders"]
  S --> S2["Stock: Fix Issues, Delivery Review, Invoices,<br/>Inventory Reports, Reconciliation,<br/>Upload Inventory, Ask Inventory Bot"]
  S --> S3["Sales: Items Sold, Open Checks,<br/>Insights /store/insights"]
  SC --> C1["Camera scan / manual entry"]
  SC --> C2["Product result: price, stock, print label"]
  SC --> C3["History icon: Print Label History"]
  SC --> C4["Create label"]
""",
        "App tabs, part 1: Home, Store (3 groups, 15 tools for an admin) and Scan.",
    )
    d.fig(
        r"""
flowchart LR
  SH["App shell<br/>bottom tabs"] --> T["Team /team?segment="]
  SH --> M["More /more"]
  T --> TR["Team rules gate (EULA, once)"]
  T --> T1["Tasks: Mine / Team / All, list or board,<br/>search, filters, My team today,<br/>bulk (admins), Can't do section"]
  T1 --> T1a["Task detail: action button,<br/>progress, steps, history, comments"]
  T1 --> T1b["Edit sheet: steps, reassign,<br/>expected_updated_at"]
  T1 --> T1c["Schedules (calendar button):<br/>list, pause, edit, Create now"]
  T1 --> T1d["Stats"]
  T --> T2["Chat: channels, DMs, conversation<br/>(voice, photos, PDFs), pins, search, settings"]
  T --> T3["News (announcements): read, acknowledge,<br/>compose (admins)"]
  T --> T4["Flyers (not for the demo login)"]
  M --> M1["Account: Face ID, Notifications,<br/>Change Password, Delete my account"]
  M1 --> M1a["Notifications: grouped switches,<br/>Reset, Store defaults (admins)"]
  M --> M2["Help (tabs Help / Glossary), About"]
  M --> M3["Setup (admins): PO sections, Vendor contacts,<br/>Checklist builder, Public checklist links,<br/>Inventory from Toast, Category and vendor leads,<br/>Users, Reported messages"]
  M --> M4["Sign Out"]
""",
        "App tabs, part 2: Team and More.",
    )

    d.h(2, "7.3", "Layers inside the app")
    d.fig(
        r"""
flowchart TB
  SCR["Screen / sheet<br/>ConsumerWidget"] -->|"ref.watch"| PRV["Riverpod providers<br/>FutureProvider, AsyncNotifier,<br/>StateNotifier, keepAlive + cacheFor"]
  PRV --> REP["Repository<br/>one per area, e.g. TasksRepository"]
  REP --> CL["ApiClient (Dio)<br/>base URL, timeouts,<br/>X-Client-Tab-Names: 1"]
  CL --> AI["AuthInterceptor<br/>Bearer token; 401 -> sign out"]
  CL --> CI["ConnectivityInterceptor<br/>offline banner, /api/health ping 8 s"]
  AI --> NET["https://ops.spicetown.shop"]
  REP --> LCL["LabelsApiClient (Dio)<br/>cookie session_token"]
  LCL --> LNET["https://labels.spicetown.shop"]
  PRV --> LOC["SharedPreferences /<br/>secure storage"]
  PRV --> PATCH["task_patches: optimistic overlays<br/>until a fresher list arrives"]
""",
        "Screen -> provider -> repository -> ApiClient -> server.",
    )
    d.table(
        ["Setting", "Value"],
        [
            ["Timeouts", "connect 15 s, receive 30 s, send 45 s; Ask bot receive 120 s; open-checks rescan 5 min"],
            ["Headers", "Authorization: Bearer <token>; X-Client-Tab-Names: 1 (server sends tab names for open checks)"],
            ["Errors", "ApiException with the server's detail; 401 (except credential paths) signs out; no automatic retry"],
            ["Offline", "ConnectivityInterceptor flips an offline banner on network errors and pings /api/health every 8 s until back. No offline data store: lists keep their last in-memory value; the glossary is bundled."],
        ],
        widths=["20%", "80%"],
        cls="compact",
    )

    d.h(2, "7.4", "Screen to repository to endpoint map")
    d.p(
        "Generated by following each screen file's imports (its own feature, plus the providers / data / repository files it imports) "
        "to the repositories it reaches, then reading each repository's Dio calls. A screen that shows nothing from the "
        "server (hubs, About, Help) has no repository."
    )
    rows = []
    for k, v in sorted(cm["app"].items()):
        repos = [r.split("/")[-1].replace("_repository.dart", "") for r in v["repos"]]
        if not repos:
            continue
        provs = [p.split("/")[-1].replace(".dart", "") for p in v.get("providers", []) if not p.endswith("auth_providers.dart")]
        rows.append([Raw(f"<code>{esc(k.replace('features/', ''))}</code>"), ", ".join(provs) or "-", ", ".join(repos)])
    d.table(["Screen / sheet (lib/features/...)", "Provider files", "Repositories reached"], rows, widths=["38%", "34%", "28%"], cls="compact small")
    rows = []
    for k, eps in sorted(cm["repo_eps"].items()):
        name = k.split("/")[-1]
        if name == "labels_repository.dart":
            eps = [f"(labels app) {e}" for e in LABELS_EPS]
        eps = [e.replace("{}/draft", "/api/checklists/admin/definitions/{}/draft").replace("{}/versions", "/api/checklists/admin/definitions/{}/versions").replace("POST {}/", "POST /api/checklists/admin/definitions/{}/") for e in eps]
        rows.append([Raw(f"<code>{esc(k)}</code>"), Raw("<br/>".join(f"<code>{esc(e)}</code>" for e in eps) or "-")])
    d.table(["Repository", "Endpoints it calls"], rows, widths=["38%", "62%"], cls="compact small endpoints")

    d.h(2, "7.5", "Push notifications and deep links")
    d.fig(
        r"""
flowchart TB
  TRG["Trigger<br/>scheduler job or a write"] --> DSP["dispatch_alert<br/>dedupe, recipients, prefs"]
  DSP --> FCM["Firebase FCM<br/>send_each"]
  FCM --> APNS["APNs (iOS) /<br/>Android"]
  APNS --> OS["Phone notification<br/>thread-id groups it"]
  OS -->|"tap"| PM["PushMessaging<br/>onMessageOpenedApp /<br/>getInitialMessage"]
  APNS -->|"app in front"| FG["onMessage<br/>(Oct 15) -> Team poll now"]
  PM --> PT["PushTarget.fromData<br/>route, type, ids"]
  PT --> NR["NotificationRouter"]
  NR --> SCR["Screen: task, chat, announcement,<br/>checklists, invoice, Supplier Projection,<br/>Fix Issues, cart, flyer, Loss watch, Insights,<br/>Inventory from Toast, open checks, moderation, Home"]
""",
        "Push delivery and tap routing.",
    )
    d.ul(
        [
            "<b>Registration</b>: after sign-in the app asks permission once (answer remembered), gets the FCM token and calls <code>POST /api/notifications/devices {token, platform}</code>; the last registered token is remembered so it is sent again only when it changes. Sign-out calls <code>DELETE /api/notifications/devices</code>; if that fails, the token is kept as 'pending unregister' and sent to the open <code>POST /api/notifications/unregister</code> later.",
            "<b>Payload</b>: data values are strings: <code>type</code> (the alert key), <code>route</code>, and optional <code>vendor</code>, <code>invoice_id</code>, <code>horizon_days</code>, <code>task_id</code>, <code>channel_id</code>, <code>message_id</code>, <code>announcement_id</code>, <code>report_id</code>, <code>flyer_id</code>, insights page. An unknown route (older build, new route) opens Home.",
            "<b>Routes understood</b>: supplier_projection (needs vendor), delivery_review, checklists, reorder, invoice, open_orders, task (needs task_id), chat (needs channel_id), announcement (needs announcement_id), moderation, fix_issues / issues, cart / purchase_cart, flyers / flyer, inventory_api(_ready), loss_watch / odd_checks, insights*, home; and by type when the route is unknown: fix_issues_reopened, cart_removal_request, flyer_sent, inventory_api_ready, loss_watch.",
            "<b>Foreground</b>: notifications are shown while the app is open; a push arriving in the foreground triggers an immediate Team poll (Oct 15).",
            "Task <code>action.route</code> uses the same route names, so a task's main button opens the real work screen in every build.",
        ]
    )

    d.h(2, "7.6", "Team sync controller")
    d.fig(
        r"""
sequenceDiagram
  autonumber
  participant UI as Team screens / Home My tasks
  participant SC as TeamSyncController
  participant API as FastAPI
  participant P as task_patches
  SC->>API: GET /api/team/bootstrap (flags, teams with member_count, me)
  loop while the app is in the foreground
    SC->>API: GET /api/team/updates?since=cursor&wait=20&include=tasks
    alt something changed (or 20 s passed)
      API-->>SC: cursor, changes, badges, tasks[]
      SC->>P: lay tasks[] over lists and detail
      SC->>UI: invalidate providers for changed entity types
    else server without tasks field (older)
      SC->>SC: fall back to timers: 30 s Team tab, 5 s chat, 120 s badges
    end
  end
  Note over SC: background = cancel the long-poll<br/>resume or foreground push = poll at once<br/>errors back off 5 s doubling<br/>resync_required = reload everything
""",
        "How the app keeps Team data live.",
    )

    d.h(2, "7.7", "Local storage on the phone")
    d.table(
        ["Key / store", "What", "Where"],
        [
            ["session_token", "The session token", "flutter_secure_storage (Keychain / Keystore)"],
            ["biometric_enabled, biometric_asked", "Face ID on/off and whether we asked", "SharedPreferences"],
            ["push_last_registered_token, push_pending_unregister", "Push token bookkeeping", "SharedPreferences"],
            ["notifications_prompt_answer", "The answer to the 'turn on notifications' prompt", "SharedPreferences"],
            ["recent_vendors_v1", "Recently used vendors (search)", "SharedPreferences"],
            ["invoice_learned_vendors_v1", "Vendor names learned from OCR corrections", "SharedPreferences"],
            ["cart_projection_lookback_days, cart_projection_horizon_days, cart_open_vendors", "Cart settings and expanded vendors", "SharedPreferences"],
            ["store_section", "Last Store category", "SharedPreferences"],
            ["Team rules acceptance", "Also stored on the server (team_rules_acceptances)", "SharedPreferences + server"],
            ["Invoice scans", "Last 3 scans kept for review; PDFs built on the phone", "App documents / temp directory"],
        ],
        widths=["38%", "37%", "25%"],
        cls="compact",
    )

    d.h(2, "7.8", "Native plugins and permissions")
    d.table(
        ["Plugin", "Used for"],
        [
            ["mobile_scanner", "Barcode camera on Scan"],
            ["cunning_document_scanner", "VisionKit / ML Kit document scanning for invoices"],
            ["google_mlkit_text_recognition", "On-device OCR of vendor name and total"],
            ["crop_your_image, image, pdf", "Review (rotate, crop, B&W) and on-device PDF of a scan"],
            ["file_picker, image_picker", "Attach a PDF / photo (invoices, task proof, chat, flyers)"],
            ["printing, share_plus, url_launcher, path_provider", "Print / share PO PDFs and reports, open links, files"],
            ["local_auth", "Face ID / biometrics unlock"],
            ["flutter_secure_storage, shared_preferences", "Token and preferences"],
            ["record, just_audio", "Voice messages in chat"],
            ["firebase_core, firebase_messaging", "Push"],
            ["package_info_plus", "Build number for the update gate and About"],
        ],
        widths=["38%", "62%"],
        cls="compact",
    )
    d.p(
        "iOS permission strings: camera (barcodes, invoice scans, task proof, chat photos, flyers), microphone (voice "
        "messages only), photo library read and add, Face ID; <code>UIBackgroundModes = remote-notification</code>. "
        "Android: CAMERA, RECORD_AUDIO."
    )

    d.h(2, "7.9", "iPad layout")
    d.ul(
        [
            "Content is centred at a maximum width of 720 px (<code>ContentWidthLimit</code>); the login form at 480 px.",
            "Device-matrix widget tests cover 10 shapes (iPhone SE at 1x and 2x text, standard phones, iPad portrait and Split View 507 px) for overflow and reachability.",
            "The Oct 15 regression fixed one iPad issue: Store defaults rows were each centred to their own width.",
        ]
    )

    d.h(2, "7.10", "Demo mode in the app")
    d.ul(
        [
            "<code>/api/auth/me</code> returns <code>demo: true</code>; a <code>DemoAccountNote</code> explains that sends are off.",
            "Hidden for the demo: More &rsaquo; Setup, Flyers, Reset to recommended, Store defaults, bulk task actions, 'My team today', step editing, Critical actions, Vendor contacts.",
            "Everything the App Review notes ask the reviewer to try still works: Home &rsaquo; Upload Invoice (saved, never sent), Home &rsaquo; Daily Checklists (saved, never sent to WhatsApp), Team (tasks and chat inside its Review team), More &rsaquo; Delete my account (refused with a clear message for the demo), More &rsaquo; About.",
        ]
    )
