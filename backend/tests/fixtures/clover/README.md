Synthetic Clover REST v3 payloads for the Clover connector tests (website-ready
track D). Made up by hand in the shape of Clover's public API reference
(docs.clover.com: merchants, properties, items, item_stocks, orders with
expand=lineItems,lineItems.modifications,lineItems.discounts,payments,refunds,
orderType,employee, employees). No real merchant, order or person.

Times are epoch milliseconds. The merchant is in America/New_York; with the
store's 04:00 cutoff, order ORD1 (2025-10-14 03:30 EDT) belongs to business
date 2025-10-13 and ORD2 (2025-10-14 12:05 EDT) to 2025-10-14.
