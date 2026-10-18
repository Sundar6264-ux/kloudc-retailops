#!/bin/zsh
# Suite A - ordering & stock pages - Playwright regression on a throwaway server.
#
#   ./prepare.sh                 # once: read-only prod copy -> migrate -> temp logins -> build website
#   ./run_suite_a.sh             # every spec, each on a fresh copy (ctl.sh reset between them)
#   ./run_suite_a.sh cart reorder
#   REG_WIDTHS=1280,375 ./run_suite_a.sh sweep
#   ./ctl.sh clean               # stop the server and delete REG_WORK (holds a copy of store data)
#
# Never touches the live DB (prepare.sh opens it mode=ro for one .backup), never sends
# email / WhatsApp / push (credentials blanked, SMTP faked, Toast mocked). Specs:
#   sweep       every page x role (admin, staff, hide_sales, demo, viewer, lead) x 1280/1024/375
#   cart        Purchase Order Cart: panels, Add items, projection context, Send PO + CCs, removals, Order placed
#   projection  Supplier Projection: suggested qty + basis, add, in-cart Update, weekly avg on hand
#   reorder     Reorder Candidates: add to cart (1 / selected / all), critical actions + Undo, Hidden from Today
#   calendar    Vendor Calendar: order-due menu + Undo, Suggested schedules + Add all, vendors without one
#   fixissues   Fix Issues lists, PO-section groups, filters, close flows; Category & vendor leads
#   pages       Inventory from Toast, Delivery Review, Invoices, Items Sold, Open Checks, PO sections, Placed Orders
# Output: REG_WORK/run-<spec>.log, REG_WORK/result-<spec>.json, screenshots in REG_WORK/shots/<spec>/.
HERE=${0:A:h}
source $HERE/env.sh
SPECS=(sweep cart projection reorder calendar fixissues pages)
(( $# )) && SPECS=($@)
NODE=${NODE:-node}
rc=0
for spec in $SPECS; do
  echo "=== $spec (fresh copy)"
  $HERE/ctl.sh reset || exit 1
  rm -rf $REG_WORK/shots/$spec $REG_WORK/result-$spec.json
  (cd $HERE && REG_WORK=$REG_WORK REG_PORT=$REG_PORT nice -n 15 $NODE $spec.mjs > $REG_WORK/run-$spec.log 2>&1)
  r=$?
  tail -n 15 $REG_WORK/run-$spec.log | grep -E 'ALL|FAILED|^- |Error' 
  [[ $r -ne 0 ]] && rc=1
done
$HERE/ctl.sh stop
echo
for spec in $SPECS; do
  f=$REG_WORK/result-$spec.json
  [[ -f $f ]] && /usr/bin/python3 -c "import json,sys; d=json.load(open('$f')); print(f\"{d['spec']:<11} {d['passes']:>4} ok  {len(d['failures']):>3} failed\")" || echo "$spec: no result (crashed - see run-$spec.log)"
done
exit $rc
