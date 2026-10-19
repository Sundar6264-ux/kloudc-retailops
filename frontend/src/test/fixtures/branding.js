// The three fake stores' GET /api/app/branding bodies (docs/M2_CONTRACT.md
// section 7). Copies of backend/tests/fixtures/stores/<code>.branding.json
// (MT7 generates those; store/branding.test.mjs compares the field names).
// Made-up values only: 555 numbers, .invalid hosts.
export const BRANDING = {
  "RETAILOPS_TEST_TOAST": {
    "schema": 1,
    "store_code": "RETAILOPS_TEST_TOAST",
    "name": "Test Toast Market",
    "short_name": "Toast Test",
    "product": {
      "name": "KloudC RetailOps",
      "company": "KloudC LLC"
    },
    "logo_url": "https://retailops-test-toast.retailops.invalid/branding/logo.png",
    "icon_url": "https://retailops-test-toast.retailops.invalid/branding/icon.png",
    "primary_color": "#1D4ED8",
    "secondary_color": "#93C5FD",
    "timezone": "America/Chicago",
    "currency": "USD",
    "locale": "en-US",
    "address": "100 Example Ave, Springfield, IL 62701",
    "phone": "(555) 010-0100",
    "pos_name": "Toast",
    "capabilities": [
      "orders_by_business_date",
      "open_orders",
      "live_today_sales",
      "catalog",
      "catalog_change_stamp",
      "stock_levels",
      "receiving_records",
      "employees",
      "day_totals",
      "analytics_reports",
      "card_fingerprints",
      "csv_catalog_import",
      "modifier_price_in_parent"
    ],
    "store_features": [
      "ap_inbox",
      "labels",
      "whatsapp",
      "ai"
    ],
    "labels_url": "https://labels-retailops-test-toast.retailops.invalid",
    "public_host": "https://retailops-test-toast.retailops.invalid",
    "legal": {
      "company": "KloudC LLC",
      "privacy_url": "https://kloudc.com/kloudc-privacy-policy/",
      "terms_url": null,
      "support_url": "https://kloudc.com/kloudc-support/"
    },
    "min_build": {
      "ios": 0,
      "android": 0
    },
    "store_url": {
      "ios": null,
      "android": "https://play.google.com/store/apps/details?id=com.kloudc.retailops"
    },
    "demo": false
  },
  "RETAILOPS_TEST_CLOVER": {
    "schema": 1,
    "store_code": "RETAILOPS_TEST_CLOVER",
    "name": "Test Clover Grocery",
    "short_name": "Clover Test",
    "product": {
      "name": "KloudC RetailOps",
      "company": "KloudC LLC"
    },
    "logo_url": "https://retailops-test-clover.retailops.invalid/branding/logo.png",
    "icon_url": "https://retailops-test-clover.retailops.invalid/branding/icon.png",
    "primary_color": "#15803D",
    "secondary_color": "#86EFAC",
    "timezone": "America/Los_Angeles",
    "currency": "USD",
    "locale": "en-US",
    "address": "200 Sample St, Fresno, CA 93650",
    "phone": "(555) 010-0200",
    "pos_name": "Clover",
    "capabilities": [
      "orders_incremental",
      "open_orders",
      "live_today_sales",
      "catalog",
      "stock_levels",
      "stock_negative_counts",
      "item_cost",
      "employees"
    ],
    "store_features": [],
    "labels_url": null,
    "public_host": "https://retailops-test-clover.retailops.invalid",
    "legal": {
      "company": "KloudC LLC",
      "privacy_url": "https://kloudc.com/kloudc-privacy-policy/",
      "terms_url": null,
      "support_url": "https://kloudc.com/kloudc-support/"
    },
    "min_build": {
      "ios": 0,
      "android": 0
    },
    "store_url": {
      "ios": null,
      "android": "https://play.google.com/store/apps/details?id=com.kloudc.retailops"
    },
    "demo": false
  },
  "DEMO": {
    "schema": 1,
    "store_code": "DEMO",
    "name": "KloudC RetailOps Demo Market",
    "short_name": "Demo",
    "product": {
      "name": "KloudC RetailOps",
      "company": "KloudC LLC"
    },
    "logo_url": "https://demo.retailops.invalid/branding/logo.png",
    "icon_url": "https://demo.retailops.invalid/branding/icon.png",
    "primary_color": "#004BAD",
    "secondary_color": "#2EA3F2",
    "timezone": "America/New_York",
    "currency": "USD",
    "locale": "en-US",
    "address": "300 Demo Rd, Hartford, CT 06101",
    "phone": "(555) 010-0300",
    "pos_name": "CSV upload",
    "capabilities": [
      "csv_catalog_import"
    ],
    "store_features": [],
    "labels_url": null,
    "public_host": "https://demo.retailops.invalid",
    "legal": {
      "company": "KloudC LLC",
      "privacy_url": "https://kloudc.com/kloudc-privacy-policy/",
      "terms_url": null,
      "support_url": "https://kloudc.com/kloudc-support/"
    },
    "min_build": {
      "ios": 0,
      "android": 0
    },
    "store_url": {
      "ios": null,
      "android": "https://play.google.com/store/apps/details?id=com.kloudc.retailops"
    },
    "demo": true
  }
};
