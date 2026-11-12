#!/bin/bash
# "Time Off" is called "Leave Requests" everywhere people see it (Robel, 9 Nov 2026). Text only, in the files that have
# it; safe to repeat. Usage: rename.sh <addons dir>
set -euo pipefail
A="$1"
n=0
for f in sonador_timeoff/views/menus.xml sonador_timeoff/__manifest__.py sonador_timeoff/static/src/leave/leave.xml \
         sonador_timeoff/static/src/leave/leave.js sonador_timeoff/models/service.py \
         sonador_me/static/src/me.xml sonador_me/static/src/me.js sonador_attendance/static/src/att/att.xml \
         sonador_payroll/static/src/pay/pay.xml sonador_payroll/static/src/pay/pay.js sonador_payroll/models/payroll.py \
         sonador_planning/models/plan.py sonador_planning/static/src/plan/plan.xml sonador_report_designer/models/blocks.py \
         sonador_insights/models/analytics.py; do
  p="$A/$f"
  [ -f "$p" ] || continue
  if grep -qi "time off" "$p"; then
    sed -i -e 's/Time Off/Leave Requests/g' -e 's/Time off is not installed/Leave Requests is not installed/g' \
           -e 's/> Time off</> Leave requests</g' -e 's/my time off/my leave requests/g' -e 's/^"""Time off:/"""Leave requests:/' \
           -e "s/_description = 'Time off'/_description = 'Leave requests'/" "$p"
    n=$((n+1)); echo "   $f"
  fi
done
echo "   $n files"
