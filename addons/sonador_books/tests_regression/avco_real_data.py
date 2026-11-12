# AVCO on the company's own data: every store document since SINCE treated as made here (as when this system is the main
# one), every item re-costed, then random changes to real GRNs and FPRRs that later issues, production and sales used:
# price up / down, quantity up / down (10%, 60%), both, a void or an issue while in correction. After each change: every
# issue equals an independent replay (same-day rule), stock = GL on each inventory account, no new stock below zero.
# Items whose stock dips below zero on some day (or with returns) are checked against the GL only. Rolled back at the end.
#   docker compose -p unio-local run --rm -T -e AVCO_SEED=1 -e AVCO_OPS=40 odoo odoo shell -d <copy> --no-http \
#       < ~/sonador-odoo/addons/sonador_books/tests_regression/avco_real_data.py
# Output: one "OP {...}" line per change, then "AVCO REAL PASS|FAIL ...".
import os
import json
import random
from collections import defaultdict
from datetime import date, datetime, time

from odoo.exceptions import UserError, ValidationError

SEED = int(os.environ.get('AVCO_SEED', '1'))
N_OPS = int(os.environ.get('AVCO_OPS', '40'))
R = random.Random(SEED)
cr = env.cr
company = env.company
Stock = env['sb.stock']
SV = env['sb.stock.voucher']
SINCE = date.fromisoformat(os.environ.get('AVCO_SINCE', '2026-07-08'))
out = {'seed': SEED, 'ops': [], 'problems': []}

# ---------------------------------------------------------------- native, as when unio is the main system
cr.execute("update sb_stock_voucher set sb_migrated = false where date >= %s and state = 'posted'", (SINCE,))
cr.execute("update stock_move set sb_fixed_value = false where date >= %s and state = 'done' and sb_reversal_of_id is null", (datetime.combine(SINCE, time.min),))
env.invalidate_all()
items = env['product.product'].browse([r[0] for r in (cr.execute(
    "select distinct product_id from stock_move where state = 'done' and date >= %s", (datetime.combine(SINCE, time.min),)) or cr.fetchall())])
avco = items.filtered(lambda p: Stock._uses_daily_average(p, company))
inv_accounts = Stock._stock_valuation_accounts(company)


def stock_value_all():
    """Stock value per inventory account (each item on its class's account)."""
    env.flush_all()
    cr.execute("""select m.product_id, coalesce(sum(case when dst.usage = 'internal' and src.usage <> 'internal' then abs(m.value)
                                          when src.usage = 'internal' and dst.usage <> 'internal' then -abs(m.value) else 0 end), 0)
                  from stock_move m join stock_location src on src.id = m.location_id join stock_location dst on dst.id = m.location_dest_id
                  where m.state = 'done' and m.company_id = %s group by 1""", (company.id,))
    per = defaultdict(float)
    vals = dict(cr.fetchall())
    cr.execute("select product_id, sum(value) from sb_stock_revaluation where company_id = %s group by 1", (company.id,))
    for pid, v in cr.fetchall():
        vals[pid] = float(vals.get(pid, 0)) + float(v)
    for pid, v in vals.items():
        per[Stock._valuation_account(env['product.product'].browse(pid), company).code] += float(v)
    return per


def gl_inv():
    env.flush_all()
    cr.execute("""select l.account_id, coalesce(sum(l.balance), 0) from account_move_line l
                  where l.parent_state = 'posted' and l.account_id in %s group by 1""", (tuple(inv_accounts.ids),))
    return {env['account.account'].browse(k).code: float(v) for k, v in cr.fetchall()}


def diffs():
    st, gl = stock_value_all(), gl_inv()
    return {k: round(st.get(k, 0) - gl.get(k, 0), 2) for k in st}


def rows(p):
    env.flush_all()
    cr.execute("""select m.id, m.date::date, case when dst.usage = 'internal' and src.usage <> 'internal' then 1
                         when src.usage = 'internal' and dst.usage <> 'internal' then -1 else 0 end, m.quantity, abs(coalesce(m.value, 0)),
                         coalesce(m.sb_cancelled, false), m.sb_return_of_id is not null or coalesce(m.sb_at_average, false)
                  from stock_move m join stock_location src on src.id = m.location_id join stock_location dst on dst.id = m.location_dest_id
                  where m.product_id = %s and m.company_id = %s and m.state = 'done' order by m.date, m.id""", (p.id, company.id))
    return cr.fetchall()


def replay(p):
    """Independent replay from 8 Jul: (mismatches, lowest quantity, checked?). Same-day rule: an issue on day D goes out at
    (value at the end of D-1 + receipts and cost adjustments of D) / (quantity likewise)."""
    rs = rows(p)
    qty = val = 0.0
    for _id, d, dr, q, v, canc, ret in rs:
        if d < SINCE and dr:
            qty += dr * q; val += dr * v
    cr.execute("select date, sum(value) from sb_stock_revaluation where product_id = %s and company_id = %s group by 1", (p.id, company.id))
    reval = dict(cr.fetchall())
    val += sum(float(x) for d, x in reval.items() if d < SINCE)
    days = defaultdict(list)
    for r in rs:
        if r[1] >= SINCE and r[2] and not r[5]:
            days[r[1]].append(r)
    for d in reval:
        if d >= SINCE:
            days.setdefault(d, [])
    bad, low, skip = [], qty, False
    for d in sorted(days):
        ins = [r for r in days[d] if r[2] > 0]
        outs = [r for r in days[d] if r[2] < 0]
        qty += sum(r[3] for r in ins); val += sum(r[4] for r in ins) + float(reval.get(d, 0))
        if outs:
            if qty <= 1e-9 or sum(r[3] for r in outs) > qty + 1e-6 or any(r[6] for r in ins + outs):
                skip = True  # negative stock true-ups / returns: covered by the ledger check only
                break
            avg = val / qty
            for r in outs:
                exp = company.currency_id.round(r[3] * avg)
                if abs(exp - r[4]) > 0.011:
                    bad.append({'move': r[0], 'day': str(d), 'engine': r[4], 'replay': exp})
        qty -= sum(r[3] for r in outs); val -= sum(r[4] for r in outs)
        low = min(low, qty)
    return bad, low, not skip


def check_all(tag, lows_before=None):
    d = diffs()
    res = {'tag': tag, 'diff': {k: round(v - BASE.get(k, v), 2) for k, v in d.items() if abs(v - BASE.get(k, v)) > 0.02}, 'abs': d, 'unchecked': [], 'mismatch': 0}
    lows = {}
    for p in avco:
        bad, low, ok = replay(p)
        lows[p.id] = low
        if not ok:
            res['unchecked'].append(p.default_code)
        if bad:
            res['mismatch'] += len(bad)
            out['problems'].append({'tag': tag, 'item': p.default_code, 'bad': bad[:3]})
        if lows_before is not None and low < min(lows_before.get(p.id, 0), 0) - 1e-6:
            out['problems'].append({'tag': tag, 'item': p.default_code, 'negative': [lows_before.get(p.id), low]})
    return res, lows


BASE = {}
before_diff = diffs()
for p in avco:  # baseline: every item re-costed by unio from 8 Jul
    Stock._recost(p, company, SINCE, reason='stress baseline')
BASE = diffs()
base, lows = check_all('baseline')
out['before_native_diff'] = before_diff
out['baseline'] = base

docs = SV.search([('state', '=', 'posted'), ('date', '>=', SINCE), ('voucher_type', 'in', ('grn', 'fprr'))])
KINDS = ['price up', 'price down', 'qty up', 'qty down 10%', 'qty down 60%', 'price and qty', 'giv while in correction', 'void while in correction']
for i in range(N_OPS):
    doc = R.choice(docs)
    kind = KINDS[i % len(KINDS)] if i < len(KINDS) else R.choice(KINDS)
    lines = doc.line_ids.filtered(lambda l: l.product_id and l.quantity > 0)
    if not lines:
        continue
    l = R.choice(lines)
    old_q, old_c = l.quantity, l.unit_cost or (l.value / l.quantity)
    q, c = old_q, old_c
    if kind == 'price up':
        c = round(old_c * R.uniform(1.05, 1.3), 4)
    elif kind == 'price down':
        c = round(old_c * R.uniform(0.7, 0.95), 4)
    elif kind == 'qty up':
        q = round(old_q * 1.1 + 1)
    elif kind == 'qty down 10%':
        q = max(round(old_q * 0.9), 1)
    elif kind == 'qty down 60%':
        q = max(round(old_q * 0.4), 1)
    elif kind == 'price and qty':
        q, c = round(old_q * 1.05 + 1), round(old_c * 1.12, 4)
    op = {'n': i + 1, 'doc': doc.name, 'type': doc.voucher_type, 'date': str(doc.date), 'item': l.product_id.default_code, 'kind': kind,
          'from': [old_q, round(old_c, 4)], 'to': [q, c]}
    try:
      with cr.savepoint():
          doc.action_correct()
          if kind == 'giv while in correction':
              giv = SV.create({'voucher_type': 'giv', 'date': doc.date, 'warehouse_id': doc.warehouse_id.id, 'department': 'Stress',
                               'account_id': env['account.account'].search([('code', '=', '1505-0000')], limit=1).id,
                               'line_ids': [(0, 0, {'product_id': l.product_id.id, 'quantity': max(old_q, 1)})]})
              try:
                  giv.action_post()
                  op['result'] = 'GIV posted while the receipt was in correction'
              except (UserError, ValidationError) as e:
                  op['result'] = 'GIV refused: ' + str(e).split('\n')[0][:160]
              doc.action_post()  # back as it was
          elif kind == 'void while in correction':
              try:
                  doc.with_context(sb_void_reason='stress').action_void()
                  op['result'] = 'voided'
              except (UserError, ValidationError) as e:
                  op['result'] = 'void refused: ' + str(e).split('\n')[0][:160]
                  doc.action_post()
          else:
              l.write({'quantity': q, 'unit_cost': c})
              if abs(l.value - q * c) > 0.01:
                  l.write({'value': company.currency_id.round(q * c)})
              try:
                  doc.action_post()
                  op['result'] = 'posted'
              except (UserError, ValidationError) as e:
                  op['result'] = 'refused: ' + str(e).split('\n')[0][:160]
                  l.write({'quantity': old_q, 'unit_cost': old_c, 'value': company.currency_id.round(old_q * old_c)})
                  doc.action_post()
                  op['result'] += ' (posted back as it was)'
          res, lows2 = check_all('op %d' % (i + 1), lows)
          op.update(diff=res['diff'], mismatch=res['mismatch'], state=doc.state)
          lows = lows2
    except UserError as e:
        # a stated rule, refused before anything changed (a document sharing its entry with another can't be corrected)
        op['result'] = ('refused: ' if 'has the same journal entry' in str(e) else 'ERROR UserError: ') + str(e)[:300]
        env.invalidate_all()
    except Exception as e:  # noqa: BLE001
        op['result'] = 'ERROR %s: %s' % (type(e).__name__, str(e)[:300])
        env.invalidate_all()
    out['ops'].append(op)
    print('OP', json.dumps(op), flush=True)

out['final'] = check_all('final')[0]
bad = [o for o in out['ops'] if o.get('mismatch') or o.get('diff') or str(o.get('result', '')).startswith('ERROR')]
print('STRESSJSON' + json.dumps(out))
print('AVCO REAL %s: seed %d, %d changes (%d posted), %d with a value off the replay or stock off the GL or a crash: %s'
      % ('PASS' if not bad and not out['problems'] else 'FAIL', SEED, len(out['ops']),
         sum(1 for o in out['ops'] if o.get('result') == 'posted'), len(bad), [(o['n'], o['doc'], o.get('result', '')[:80]) for o in bad][:5]))
cr.rollback()
