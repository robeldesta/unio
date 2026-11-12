"""Calls of the Fixed Assets screen, the monthly depreciation run and disposals.

Depreciation run (a month): one journal voucher dated the last day of the month, a debit per expense account and a
credit per accumulated-depreciation account, one line per asset on the register. Running a month twice is refused;
months must follow each other per asset (a month skipped is caught up in the next run).
Disposal: the asset's cost leaves the cost account, its accumulated depreciation is cleared, what it was sold for goes
to the account chosen (bank or receivable) and the difference is the gain or loss on disposal.
Automatic runs (daily job): a company posting every month gets the period just ended posted; one posting once a year
gets the whole year posted at year end. Control: each class's cost and accumulated-depreciation accounts in the ledger
against the register, and every ledger line on those accounts that no asset accounts for."""
import logging
from collections import defaultdict
from datetime import timedelta

from odoo import Command, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.sonador_books.models.ethiopian_calendar import format_ethiopian

from .asset import FREQUENCIES, METHODS, next_period, period_end, period_of, year_end
from odoo.addons.sonador_books.models.ethiopian_calendar import fiscal_periods, fiscal_year_of as fiscal_year_of_d
from .rules import INCOME_TAX, apply_rules

_logger = logging.getLogger(__name__)
SYSTEM_REFS = ('DEP-', 'DISP-', 'EXP-', 'CAP-')  # the entries this app posts itself


def _s(d):
    return fields.Date.to_string(d) if d else False


def _month_end(d):
    """Last day of the accounting period containing d."""
    return period_end(d)


class SbAssets(models.AbstractModel):
    _name = 'sb.assets'
    _description = 'Fixed assets'

    def _require(self):
        if not (self.env.su or self.env.user.has_group('sonador_books.group_sb_accountant')):
            raise AccessError(self.env._('Fixed assets are kept by the accountants.'))

    def _acc(self, code):
        return self.env['account.account']._sb_find(code)[:1]

    # ------------------------------------------------------------------ from bills and capital projects
    @api.model
    def _from_vouchers(self, vouchers):
        """Lines of posted bills / cash purchases / journals on a category's cost account become assets to confirm.
        Lines tagged with a job (capital project) build one asset per job and account."""
        A = self.env['sb.asset'].sudo()
        all_cats = self.env['sb.asset.category'].sudo().search([])
        cats = {c.asset_account_id.id: c for c in all_cats}
        expensed = {}
        for c in all_cats.filtered('expensed_account_id'):
            expensed.setdefault(c.expensed_account_id.id, c)
        if not cats:
            return
        for v in vouchers.filtered(lambda x: x.voucher_type in ('bill', 'cash_purchase', 'journal', 'payment')
                                   and not (x.reference or '').startswith(SYSTEM_REFS)):
            for l in v.line_ids.filtered(lambda x: (x.account_id.id in cats or x.account_id.id in expensed) and not x.sb_asset_ids):
                amount = getattr(l, 'price_subtotal', 0.0) or getattr(l, 'amount', 0.0) or (l.debit - l.credit if 'debit' in l._fields else 0.0)
                if amount <= 0:
                    continue
                if l.account_id.id in expensed:
                    # bought under the threshold and expensed: on the register all the same
                    A.create({'name': (l.name or l.product_id.display_name or 'Item')[:120], 'category_id': expensed[l.account_id.id].id,
                              'company_id': v.company_id.id, 'date_acquired': v.date, 'date_start': v.date, 'cost': amount,
                              'state': 'expensed', 'voucher_line_ids': [Command.link(l.id)]})
                    continue
                cat = cats[l.account_id.id]
                job = l.job_id if 'job_id' in l._fields else False
                if job:
                    a = A.search([('job_ref', '=', job.id), ('category_id', '=', cat.id), ('state', '=', 'draft')], limit=1)
                    if a:
                        a.cost += amount
                        a.voucher_line_ids = [Command.link(l.id)]
                        continue
                    A.create({'name': job.name, 'category_id': cat.id, 'company_id': v.company_id.id, 'date_acquired': v.date,
                              'date_start': v.date, 'cost': amount, 'job_ref': job.id, 'voucher_line_ids': [Command.link(l.id)],
                              'note': self.env._('Capital project %s', job.display_name)})
                else:
                    A.create({'name': (l.name or l.product_id.display_name or cat.name)[:120], 'category_id': cat.id, 'company_id': v.company_id.id,
                              'date_acquired': v.date, 'date_start': v.date, 'cost': amount, 'voucher_line_ids': [Command.link(l.id)]})

    # ------------------------------------------------------------------ screen
    def _row(self, a):
        return {'id': a.id, 'code': a.code, 'name': a.name, 'category': a.category_id.name, 'category_id': a.category_id.id, 'state': a.state,
                'acquired': _s(a.date_acquired), 'acquired_ec': format_ethiopian(a.date_acquired), 'start': _s(a.date_start), 'cost': a.cost,
                'salvage': a.salvage, 'depreciated': a.depreciated, 'book_value': a.book_value, 'method': a.method,
                'life_months': a.life_months, 'rate': a.rate, 'serial': a.serial or '', 'location': a.location or '',
                'custodian': a.custodian_id.name or '', 'custodian_id': a.custodian_id.id or '', 'opening_depreciation': a.opening_depreciation,
                'monthly': a._amount_for(_month_end(fields.Date.context_today(self))) if a.state == 'running' else 0.0,
                'source': ', '.join(sorted(set(a.voucher_line_ids.voucher_id.mapped('name')))), 'note': a.note or '',
                'disposed': _s(a.date_disposed), 'disposal_amount': a.disposal_amount, 'below_threshold': a.below_threshold,
                'law': a.category_id.law or '', 'tax_method': a.tax_method or a.method, 'tax_rate': a.tax_rate,
                'tax_life_months': a.tax_life_months, 'tax_opening': a.tax_opening,
                'tax_wdv': a._tax_wdv(fields.Date.context_today(self)) if a.state not in ('draft', 'expensed') else 0.0}

    @api.model
    def ctx(self):
        self._require()
        c = self.env.company
        cash = self.env['account.account'].search([('company_ids', 'in', c.id), ('account_type', 'in', ('asset_cash', 'asset_receivable'))], order='code')
        today = fields.Date.context_today(self)
        _n, start, _e = period_of(today)
        return {'company': c.name, 'today': _s(today), 'methods': [{'id': k, 'name': v} for k, v in METHODS],
                'frequencies': [{'id': k, 'name': v} for k, v in FREQUENCIES], 'frequency': c.sb_asset_frequency,
                'threshold': c.sb_asset_threshold, 'last_period_end': _s(start - timedelta(days=1)),
                'last_period': period_of(start - timedelta(days=1))[0],
                'categories': [{'id': x.id, 'name': x.name, 'method': x.method, 'life_months': x.life_months, 'rate': x.rate,
                                'salvage_pct': x.salvage_pct, 'asset': x.asset_account_id.display_name, 'dep': x.depreciation_account_id.display_name,
                                'exp': x.expense_account_id.display_name, 'asset_id': x.asset_account_id.id,
                                'dep_id': x.depreciation_account_id.id, 'exp_id': x.expense_account_id.id, 'law': x.law or '',
                                'expensed': x.expensed_account_id.display_name or '', 'expensed_id': x.expensed_account_id.id or '',
                                'tax_method': x.tax_method, 'tax_rate': x.tax_rate, 'tax_life_months': x.tax_life_months}
                               for x in self.env['sb.asset.category'].search([('company_id', '=', c.id)])],
                'accounts': [{'id': a.id, 'name': '%s %s' % (a.code, a.name), 'type': a.account_type}
                             for a in self.env['account.account'].search([('company_ids', 'in', c.id)], order='code')],
                'cash': [{'id': a.id, 'name': '%s %s' % (a.code, a.name)} for a in cash],
                'people': [{'id': e.id, 'name': e.name} for e in self.env['hr.employee'].search([('company_id', '=', c.id)], order='name')],
                'last_run': _s(max(self.env['sb.asset.dep'].search([('company_id', '=', c.id), ('voucher_id', '!=', False)]).mapped('date') or [False]) or False)}

    @api.model
    def overview(self, f=None):
        self._require()
        f = f or {}
        dom = [('company_id', '=', self.env.company.id)]
        if f.get('state'):
            dom.append(('state', '=', f['state']))
        if f.get('category_id'):
            dom.append(('category_id', '=', int(f['category_id'])))
        A = self.env['sb.asset'].search(dom)
        if f.get('search'):
            s = f['search'].lower()
            A = A.filtered(lambda a: s in (a.name or '').lower() or s in (a.code or '').lower() or s in (a.serial or '').lower())
        live = A.filtered(lambda a: a.state in ('running', 'done', 'draft'))
        return {'rows': [self._row(a) for a in A],
                'totals': {'cost': sum(live.mapped('cost')), 'depreciated': sum(live.mapped('depreciated')), 'book_value': sum(live.mapped('book_value')),
                           'monthly': sum(self._row(a)['monthly'] for a in live), 'to_confirm': len(A.filtered(lambda a: a.state == 'draft')),
                           'expensed': sum(A.filtered(lambda a: a.state == 'expensed').mapped('cost'))}}

    @api.model
    def asset(self, asset_id):
        self._require()
        a = self.env['sb.asset'].browse(int(asset_id))
        sched, acc, d = [], a.opening_depreciation, a._first_month() if a.date_start else False
        posted = {l.date: l for l in a.line_ids}
        # the schedule: posted months, then the projection to the end of the useful life
        bv = a.cost - a.opening_depreciation
        n = 0
        while d and a.state != 'expensed' and bv - a.salvage > 0.5 and n < 600:
            me = _month_end(d)
            if me in posted:
                amt = sum(x.amount for x in a.line_ids if x.date == me)
                state = 'posted' if posted[me].voucher_id.state == 'posted' else 'draft'
            else:
                left = bv - a.salvage
                amt = min(bv * a.rate / 100 / 12 if a.method == 'declining' and a.rate else (a.cost - a.salvage) / max(a.life_months or 1, 1), left)
                state = 'future'
            acc += amt
            bv -= amt
            sched.append({'date': _s(me), 'label': period_of(me)[0], 'amount': round(amt, 2), 'accumulated': round(acc, 2), 'book_value': round(bv, 2), 'state': state})
            d = next_period(d)
            n += 1
            if a.state == 'disposed' and a.date_disposed and me >= a.date_disposed:
                break
        return dict(self._row(a), schedule=sched[:240])

    @api.model
    def save(self, asset_id, vals):
        self._require()
        A = self.env['sb.asset']
        a = A.browse(int(asset_id)) if asset_id else A
        # depreciation already posted: the change is taken and the depreciation worked out again (models/redo.py)
        redo = a._sb_snapshot() if a and a.state in ('running', 'done') and set(vals) & {
            'cost', 'category_id', 'date_start', 'method', 'life_months', 'rate', 'salvage', 'opening_depreciation'} \
            and any(l.voucher_id for l in a.line_ids) else None
        w = {}
        for k in ('name', 'serial', 'location', 'note', 'method'):
            if k in vals:
                w[k] = vals[k] or False
        for k in ('tax_method',):
            if vals.get(k):
                w[k] = vals[k]
        for k in ('cost', 'salvage', 'rate', 'opening_depreciation', 'tax_rate', 'tax_opening'):
            if k in vals:
                w[k] = float(vals[k] or 0)
        for k in ('life_months', 'tax_life_months'):
            if k in vals:
                w[k] = int(float(vals[k] or 0))
        for k in ('date_acquired', 'date_start'):
            if vals.get(k):
                w[k] = vals[k]
        for k, f in (('category_id', 'category_id'), ('custodian_id', 'custodian_id')):
            if k in vals:
                w[f] = int(vals[k]) if vals[k] else False
        if not a:
            if not w.get('name') or not w.get('category_id') or not w.get('cost'):
                raise ValidationError(self.env._('Name, category and cost are needed.'))
            a = A.create(w)
        else:
            a.write(w)
            if redo:
                a._sb_redo_depreciation(redo)
        return self.asset(a.id)

    @api.model
    def confirm(self, asset_ids):
        self._require()
        for a in self.env['sb.asset'].browse([int(i) for i in asset_ids]).filtered(lambda x: x.state == 'draft'):
            if a.method == 'straight' and not a.life_months:
                raise ValidationError(self.env._('%s: enter the useful life.', a.name))
            if a.method == 'declining' and not a.rate:
                raise ValidationError(self.env._('%s: enter the yearly rate.', a.name))
            a.state = 'running'
        return True

    # ------------------------------------------------------------------ monthly run
    @api.model
    def run(self, month_end):
        """Post the depreciation of every running asset for the month ending month_end (catching up skipped months)."""
        self._require()
        me = _month_end(fields.Date.to_date(month_end))
        c = self.env.company
        lock = c.fiscalyear_lock_date
        if lock and me <= lock:
            raise UserError(self.env._('The books are closed through %s.', lock))
        assets = self.env['sb.asset'].search([('company_id', '=', c.id), ('state', '=', 'running')])
        Dep = self.env['sb.asset.dep']
        lines = defaultdict(float)
        made = Dep
        for a in assets:
            done = set(a.line_ids.mapped('date'))
            total = a.opening_depreciation + sum(a.line_ids.mapped('amount'))  # posted and still to post
            d = a._first_month()
            while _month_end(d) <= me:
                m = _month_end(d)
                if m not in done:
                    amt = a._amount_for(m, total)
                    if amt:
                        made |= Dep.create({'asset_id': a.id, 'date': m, 'amount': amt})
                        lines[(a.category_id.expense_account_id.id, 'dr')] += amt
                        lines[(a.category_id.depreciation_account_id.id, 'cr')] += amt
                        total += amt
                d = next_period(d)
        if not made:
            raise UserError(self.env._('Nothing to depreciate for %s: already run, or no running assets.', period_of(me)[0]))
        v = self.env['sb.voucher'].create({
            'voucher_type': 'journal', 'company_id': c.id, 'date': me, 'reference': 'DEP-%s' % me.strftime('%Y%m'),
            'memo': self.env._('Depreciation to %s', period_of(me)[0]),
            'line_ids': [Command.create({'account_id': acc, 'name': self.env._('Depreciation to %s', period_of(me)[0]),
                                         'debit': round(amt, 2) if side == 'dr' else 0.0, 'credit': round(amt, 2) if side == 'cr' else 0.0})
                         for (acc, side), amt in sorted(lines.items())]})
        v.action_post()
        made.write({'voucher_id': v.id})
        for a in made.asset_id:
            a.invalidate_recordset(['depreciated', 'book_value'])
            if a.cost - a.salvage - a.depreciated <= 0.5:
                a.state = 'done'
        return {'voucher': v.name, 'amount': round(sum(made.mapped('amount')), 2), 'assets': len(made.asset_id)}

    # ------------------------------------------------------------------ disposal
    @api.model
    def dispose(self, asset_id, day, amount, account_id=False, partner_id=False):
        self._require()
        a = self.env['sb.asset'].browse(int(asset_id))
        day = fields.Date.to_date(day)
        amount = float(amount or 0)
        if a.state not in ('running', 'done', 'expensed'):
            raise UserError(self.env._('Only an asset in use can be disposed of.'))
        if a.state == 'expensed':
            # written off when bought: nothing in the books to take out, the register just records where it went
            a.write({'state': 'disposed', 'date_disposed': day, 'disposal_amount': amount})
            return self.asset(a.id)
        if amount and not account_id:
            raise ValidationError(self.env._('Where did the money go? Choose the bank or the customer account.'))
        c = a.company_id
        gain_acc = self._acc('120300') or self.env['account.account'].search([('company_ids', 'in', c.id), ('account_type', '=', 'income_other')], limit=1)
        loss_acc = self._acc('590300') or self.env['account.account'].sudo().create({
            'code': self.env['account.account']._sb_new_code('590300', c), 'sb_legacy_code': '590300', 'name': self.env._('Loss on disposal of fixed assets'), 'account_type': 'expense', 'company_ids': [Command.set(c.ids)]})
        dep = a.depreciated
        result = amount - (a.cost - dep)
        lines = [Command.create({'account_id': a.category_id.asset_account_id.id, 'name': self.env._('Disposal %s: cost', a.code), 'debit': 0.0, 'credit': a.cost})]
        if dep:
            lines.append(Command.create({'account_id': a.category_id.depreciation_account_id.id, 'name': self.env._('Disposal %s: depreciation', a.code),
                                         'debit': dep, 'credit': 0.0}))
        if amount:
            acc = self.env['account.account'].browse(int(account_id))
            lines.append(Command.create({'account_id': acc.id, 'name': self.env._('Disposal %s: proceeds', a.code), 'debit': amount, 'credit': 0.0,
                                         'partner_id': int(partner_id) if partner_id else False}))
        if abs(result) > 0.005:
            lines.append(Command.create({'account_id': (gain_acc if result > 0 else loss_acc).id,
                                         'name': self.env._('Disposal %s: %s', a.code, 'gain' if result > 0 else 'loss'),
                                         'debit': -result if result < 0 else 0.0, 'credit': result if result > 0 else 0.0}))
        v = self.env['sb.voucher'].create({'voucher_type': 'journal', 'company_id': c.id, 'date': day, 'reference': 'DISP-%s' % a.code,
                                           'memo': self.env._('Disposal of %s %s', a.code, a.name), 'line_ids': lines})
        v.action_post()
        a.write({'state': 'disposed', 'date_disposed': day, 'disposal_amount': amount, 'disposal_voucher_id': v.id})
        return self.asset(a.id)

    # ------------------------------------------------------------------ categories
    @api.model
    def save_category(self, vals):
        self._require()
        C = self.env['sb.asset.category']
        w = {'name': vals['name'], 'asset_account_id': int(vals['asset_id']), 'depreciation_account_id': int(vals['dep_id']),
             'expense_account_id': int(vals['exp_id']), 'method': vals.get('method') or 'straight',
             'life_months': int(float(vals.get('life_months') or 0)), 'rate': float(vals.get('rate') or 0),
             'salvage_pct': float(vals.get('salvage_pct') or 0), 'tax_method': vals.get('tax_method') or vals.get('method') or 'straight',
             'tax_rate': float(vals.get('tax_rate') or 0), 'tax_life_months': int(float(vals.get('tax_life_months') or 0)),
             'expensed_account_id': int(vals['expensed_id']) if vals.get('expensed_id') else False}
        if vals.get('id'):
            C.browse(int(vals['id'])).write(w)
        else:
            C.create(w)
        return self.ctx()

    # ------------------------------------------------------------------ settings, country rules
    @api.model
    def save_settings(self, vals):
        self._require()
        w = {}
        if vals.get('frequency') in dict(FREQUENCIES):
            w['sb_asset_frequency'] = vals['frequency']
        if 'threshold' in vals:
            w['sb_asset_threshold'] = float(vals['threshold'] or 0)
        self.env.company.sudo().write(w)
        return self.ctx()

    @api.model
    def apply_rules_all(self):
        """On install and upgrade: every company gets its country's asset classes."""
        for company in self.env['res.company'].sudo().search([]):
            apply_rules(self.env, company)
        return True

    # ------------------------------------------------------------------ expensing an item under the threshold
    @api.model
    def expense_it(self, asset_id):
        """An item on a fixed-asset account that costs less than the threshold: its cost moves to the expensed-items
        account and it stays on the register."""
        self._require()
        a = self.env['sb.asset'].browse(int(asset_id))
        if a.state != 'draft':
            raise UserError(self.env._('Only an asset still to confirm can be expensed.'))
        target = a.category_id.expensed_account_id
        if not target:
            raise UserError(self.env._('Set the expensed items account of %s first.', a.category_id.name))
        v = self.env['sb.voucher'].create({
            'voucher_type': 'journal', 'company_id': a.company_id.id, 'date': a.date_acquired, 'reference': 'EXP-%s' % a.code,
            'memo': self.env._('%s %s expensed (under the capitalisation threshold)', a.code, a.name),
            'line_ids': [Command.create({'account_id': target.id, 'name': a.name, 'debit': a.cost, 'credit': 0.0}),
                         Command.create({'account_id': a.category_id.asset_account_id.id, 'name': a.name, 'debit': 0.0, 'credit': a.cost})]})
        v.action_post()
        a.write({'state': 'expensed', 'expense_voucher_id': v.id})
        return self.asset(a.id)

    # ------------------------------------------------------------------ control: ledger against register
    def _ledger(self, account, dt):
        rows = self.env['account.move.line'].sudo()._read_group(
            [('account_id', '=', account.id), ('parent_state', '=', 'posted'), ('date', '<=', dt), ('company_id', '=', self.env.company.id)],
            aggregates=['balance:sum'])
        return rows[0][0] or 0.0 if rows else 0.0

    @api.model
    def control(self, date_to=None):
        """Per class: the cost and accumulated-depreciation accounts in the ledger against the register, as at date_to,
        and the ledger lines on those accounts that no asset accounts for."""
        self._require()
        c = self.env.company
        dt = fields.Date.to_date(date_to) if date_to else fields.Date.context_today(self)
        cats = self.env['sb.asset.category'].search([('company_id', '=', c.id)])
        A = self.env['sb.asset'].search([('company_id', '=', c.id), ('date_acquired', '<=', dt)])
        rep = self.env['sb.report']
        rows, seen = [], set()

        def in_books(a):
            return a.state not in ('expensed',) and not (a.state == 'disposed' and a.date_disposed and a.date_disposed <= dt)

        def add(account, kind, register, label):
            if account.id in seen:  # an account shared by several classes: compared once, against all of them
                return
            seen.add(account.id)
            sign = -1 if kind == 'dep' else 1
            ledger = round(sign * self._ledger(account, dt), 2)
            rows.append({'account': '%s %s' % (account.code, account.name), 'account_id': account.id, 'kind': kind, 'label': label,
                         'ledger': ledger, 'register': round(register, 2), 'diff': round(ledger - register, 2)})

        for cat in cats:
            same_cost = A.filtered(lambda a: a.category_id.asset_account_id == cat.asset_account_id and in_books(a))
            add(cat.asset_account_id, 'cost', sum(same_cost.mapped('cost')), cat.name)
        for cat in cats:
            same_dep = A.filtered(lambda a: a.category_id.depreciation_account_id == cat.depreciation_account_id and in_books(a))
            add(cat.depreciation_account_id, 'dep', sum(rep._dep_to(a, dt) for a in same_dep), cat.name)
        # items expensed when bought: what the expensed-items account shows this year against the register
        for acc in cats.mapped('expensed_account_id'):
            items = A.filtered(lambda a: a.category_id.expensed_account_id == acc and (a.state == 'expensed' or (a.state == 'disposed' and a.expense_voucher_id)))
            if acc.id not in seen:
                seen.add(acc.id)
                ledger = round(self._ledger(acc, dt), 2)
                rows.append({'account': '%s %s' % (acc.code, acc.name), 'account_id': acc.id, 'kind': 'expensed', 'label': 'Expensed when bought',
                             'ledger': ledger, 'register': round(sum(items.mapped('cost')), 2), 'diff': round(ledger - sum(items.mapped('cost')), 2)})
        # ledger lines on cost / expensed accounts that no asset accounts for
        accounts = cats.mapped('asset_account_id') | cats.mapped('expensed_account_id')
        VL = self.env['sb.voucher.line'].sudo()
        lines = VL.search([('voucher_id.state', '=', 'posted'), ('voucher_id.company_id', '=', c.id), ('voucher_id.date', '<=', dt),
                           ('account_id', 'in', accounts.ids), ('sb_asset_ids', '=', False)], order='id')
        unlinked = []
        for l in lines:
            if (l.voucher_id.reference or '').startswith(SYSTEM_REFS):
                continue
            amount = l.price_subtotal or l.amount or (l.debit - l.credit)
            if not amount:
                continue
            unlinked.append({'id': l.id, 'date': _s(l.voucher_id.date), 'voucher': l.voucher_id.name, 'voucher_id': l.voucher_id.id,
                             'name': l.name or l.product_id.display_name or '', 'account': '%s %s' % (l.account_id.code, l.account_id.name),
                             'amount': amount, 'expensed': l.account_id in cats.mapped('expensed_account_id')})
        # ledger lines that came from somewhere else than a voucher (imports, Odoo journal entries)
        moves = self.env['account.move.line'].sudo().search([('account_id', 'in', (cats.mapped('asset_account_id') | cats.mapped('depreciation_account_id')).ids),
                                                             ('parent_state', '=', 'posted'), ('date', '<=', dt), ('company_id', '=', c.id)])
        vmoves = set(self.env['sb.voucher'].sudo().search([('move_id', 'in', moves.move_id.ids)]).move_id.ids)
        outside = [{'date': _s(m.date), 'move': m.move_id.name, 'name': m.name or '', 'account': '%s %s' % (m.account_id.code, m.account_id.name),
                    'amount': m.balance} for m in moves if m.move_id.id not in vmoves]
        return {'date': _s(dt), 'label': format_ethiopian(dt), 'rows': rows, 'unlinked': unlinked, 'outside': outside,
                'ok': all(abs(r['diff']) < 1 for r in rows if r['kind'] != 'expensed') and not unlinked and not outside,
                'assets': [{'id': a.id, 'name': '%s %s' % (a.code, a.name), 'category_id': a.category_id.id}
                           for a in A.filtered(lambda a: not a.voucher_line_ids and a.state != 'disposed')]}

    @api.model
    def from_line(self, line_id):
        """A ledger line no asset accounts for becomes an asset to confirm (or an expensed item)."""
        self._require()
        l = self.env['sb.voucher.line'].browse(int(line_id))
        self._from_vouchers(l.voucher_id)
        return self.control()

    @api.model
    def link_line(self, line_id, asset_ids):
        """Assets entered by hand (opening balance, capital work) that make up a ledger line: linked to it."""
        self._require()
        l = self.env['sb.voucher.line'].browse(int(line_id))
        l.sb_asset_ids = [Command.link(int(i)) for i in asset_ids]
        return self.control()

    # ------------------------------------------------------------------ automatic runs
    @api.model
    def _cron_run(self):
        """Daily: post what is due for every company that does not run depreciation by hand."""
        today = fields.Date.context_today(self)
        for c in self.env['res.company'].sudo().search([('sb_asset_frequency', '!=', 'manual')]):
            env = self.with_company(c).with_context(allowed_company_ids=[c.id]).sudo()
            last = period_of(today)[1] - timedelta(days=1)  # end of the period just finished
            target = last if c.sb_asset_frequency == 'monthly' else (year_end(last) if year_end(last) == last else False)
            if not target:
                continue
            due = env.env['sb.asset'].search([('company_id', '=', c.id), ('state', '=', 'running')]).filtered(
                lambda a: a._first_month() <= target and target not in a.line_ids.mapped('date'))
            lock = c.fiscalyear_lock_date
            if not due or (lock and target <= lock):
                continue
            try:
                with self.env.cr.savepoint():
                    r = env.run(_s(target))
                _logger.info('Depreciation posted for %s to %s: %s', c.name, target, r)
            except UserError as e:
                _logger.info('Depreciation for %s to %s not posted: %s', c.name, target, e)

    # ------------------------------------------------------------------ books against tax (IAS 12)
    @api.model
    def book_vs_tax(self, date_to=None):
        """Per asset: carrying amount in the books, tax written-down value, the difference and the deferred tax on it at
        the company income tax rate; against the deferred tax already in the ledger."""
        self._require()
        c = self.env.company
        dt = fields.Date.to_date(date_to) if date_to else fields.Date.context_today(self)
        rate = INCOME_TAX.get(c.country_id.code or 'ET', 30.0)
        rep = self.env['sb.report']
        rows = []
        for a in self.env['sb.asset'].search([('company_id', '=', c.id), ('state', 'in', ('running', 'done', 'disposed')),
                                              ('date_acquired', '<=', dt)], order='category_id, code'):
            if a.state == 'disposed' and a.date_disposed and a.date_disposed <= dt:
                continue
            book = round(a.cost - rep._dep_to(a, dt), 2)
            tax = a._tax_wdv(dt)
            rows.append({'id': a.id, 'code': a.code, 'name': a.name, 'category': a.category_id.name, 'book': book, 'tax': tax,
                         'diff': round(book - tax, 2), 'deferred': round((book - tax) * rate / 100.0, 2),
                         'tax_basis': '%s%%' % a.tax_rate if (a.tax_method or a.method) == 'declining' else '%s months' % (a.tax_life_months or a.life_months)})
        need = round(sum(r['deferred'] for r in rows), 2)  # + liability, - asset
        liab, asset = self._acc('316200'), self._acc('257100')
        booked = round(-(self._ledger(liab, dt) if liab else 0.0) - (self._ledger(asset, dt) if asset else 0.0), 2)
        return {'date': _s(dt), 'label': format_ethiopian(dt), 'rate': rate, 'rows': rows,
                'totals': {'book': round(sum(r['book'] for r in rows), 2), 'tax': round(sum(r['tax'] for r in rows), 2),
                           'diff': round(sum(r['diff'] for r in rows), 2), 'deferred': need},
                'booked': booked, 'adjust': round(need - booked, 2)}

    @api.model
    def post_deferred_tax(self, date_to):
        """Bring the deferred tax in the ledger to what the assets need: liability when the books are ahead of tax (and
        the other way round), the change through deferred income tax expense."""
        self._require()
        data = self.book_vs_tax(date_to)
        adj = data['adjust']
        if abs(adj) < 1:
            raise UserError(self.env._('The deferred tax in the ledger already matches the assets.'))
        c = self.env.company
        exp = self._acc('641200')
        side = self._acc('316200') if data['totals']['deferred'] >= 0 else self._acc('257100')
        if not exp or not side:
            raise UserError(self.env._('The deferred tax accounts (641200, 316200, 257100) are missing from the chart.'))
        label = self.env._('Deferred tax on fixed assets to %s', data['label'])
        v = self.env['sb.voucher'].create({
            'voucher_type': 'journal', 'company_id': c.id, 'date': data['date'], 'reference': 'DEP-TAX-%s' % data['date'], 'memo': label,
            'line_ids': [Command.create({'account_id': exp.id, 'name': label, 'debit': adj if adj > 0 else 0.0, 'credit': -adj if adj < 0 else 0.0}),
                         Command.create({'account_id': side.id, 'name': label, 'debit': -adj if adj < 0 else 0.0, 'credit': adj if adj > 0 else 0.0})]})
        v.action_post()
        return dict(self.book_vs_tax(date_to), posted=v.name)

    # ------------------------------------------------------------------ capitalising what was expensed in the past
    @api.model
    def capitalise_candidates(self, f=None):
        """Posted expense lines that may be assets: from a date, above an amount (the threshold by default), matching a text."""
        self._require()
        f = f or {}
        c = self.env.company
        cats = self.env['sb.asset.category'].search([('company_id', '=', c.id)])
        skip = cats.mapped('expense_account_id') | cats.mapped('expensed_account_id') | (self._acc('641200') or self.env['account.account'])
        # things bought from suppliers (bills, cash purchases, payments), not payroll, tax or finance costs
        skip |= self.env['account.account'].search([('company_ids', 'in', c.id), '|', '|', '|', '|', ('name', 'ilike', 'salar'), ('name', 'ilike', 'pension'),
                                                     ('name', 'ilike', 'tax'), ('name', 'ilike', 'interest'), ('name', 'ilike', 'penalt')])
        dom = [('voucher_id.state', '=', 'posted'), ('voucher_id.company_id', '=', c.id), ('sb_asset_ids', '=', False),
               ('voucher_id.voucher_type', 'in', ('bill', 'cash_purchase', 'payment')),
               ('account_id.account_type', 'in', ('expense', 'expense_direct_cost')), ('account_id', 'not in', skip.ids)]
        if f.get('date_from'):
            dom.append(('voucher_id.date', '>=', f['date_from']))
        if f.get('date_to'):
            dom.append(('voucher_id.date', '<=', f['date_to']))
        if f.get('search'):
            dom += ['|', '|', ('name', 'ilike', f['search']), ('product_id.name', 'ilike', f['search']), ('voucher_id.memo', 'ilike', f['search'])]
        least = float(f.get('min_amount') or c.sb_asset_threshold or 0)
        out = []
        for l in self.env['sb.voucher.line'].search(dom, order='id desc', limit=400):
            amount = l.price_subtotal or l.amount or (l.debit - l.credit)
            if amount <= 0 or amount < least or (l.voucher_id.reference or '').startswith(SYSTEM_REFS):
                continue
            out.append({'id': l.id, 'date': _s(l.voucher_id.date), 'voucher': l.voucher_id.name, 'voucher_id': l.voucher_id.id,
                        'partner': l.voucher_id.partner_id.name or '', 'name': l.name or l.product_id.display_name or l.voucher_id.memo or '',
                        'account': '%s %s' % (l.account_id.code, l.account_id.name), 'amount': amount})
            if len(out) >= 200:
                break
        return {'rows': out, 'min_amount': least}

    @api.model
    def capitalise(self, line_ids, category_id, date_start=False):
        """Expense lines that were assets: the cost moves to the class's cost account (from the expense account in an
        open year; from retained earnings when the expense is in a year already closed or ended - IAS 8), the asset goes
        on the register in use from its purchase date, and the depreciation it would have had is caught up the same way."""
        self._require()
        c = self.env.company
        cat = self.env['sb.asset.category'].browse(int(category_id))
        lines = self.env['sb.voucher.line'].browse([int(i) for i in line_ids])
        today = fields.Date.context_today(self)
        fy_start = fiscal_periods(fiscal_year_of_d(today))[0][2]
        lock = c.fiscalyear_lock_date
        retained = self._acc('403000')
        made = self.env['sb.asset']
        for l in lines:
            amount = l.price_subtotal or l.amount or (l.debit - l.credit)
            day = l.voucher_id.date
            past = day < fy_start or (lock and day <= lock)
            if past and not retained:
                raise UserError(self.env._('Retained earnings (403000) is missing from the chart.'))
            a = self.env['sb.asset'].create({
                'name': (l.name or l.product_id.display_name or cat.name)[:120], 'category_id': cat.id, 'company_id': c.id,
                'date_acquired': day, 'date_start': date_start or day, 'cost': amount, 'voucher_line_ids': [Command.link(l.id)],
                'note': self.env._('Capitalised from %s (expensed on %s)', l.voucher_id.name, l.account_id.display_name)})
            # depreciation it would have had: years before this one to retained earnings, this year to the expense of the year
            before = a._book_through(fy_start - timedelta(days=1)) if past else 0.0
            this_year = round(a._book_through(period_of(today)[1] - timedelta(days=1)) - before, 2)
            catchup = round(before + this_year, 2)
            label = self.env._('%s %s capitalised', a.code, a.name)
            vl = [Command.create({'account_id': cat.asset_account_id.id, 'name': label, 'debit': amount, 'credit': 0.0}),
                  Command.create({'account_id': (retained if past else l.account_id).id, 'name': label, 'debit': 0.0, 'credit': amount})]
            if before:
                vl += [Command.create({'account_id': retained.id, 'name': self.env._('%s: depreciation of past years', a.code), 'debit': before, 'credit': 0.0})]
            if this_year:
                vl += [Command.create({'account_id': cat.expense_account_id.id, 'name': self.env._('%s: depreciation this year', a.code), 'debit': this_year, 'credit': 0.0})]
            if catchup:
                vl += [Command.create({'account_id': cat.depreciation_account_id.id, 'name': self.env._('%s: depreciation caught up', a.code), 'debit': 0.0, 'credit': catchup})]
            v = self.env['sb.voucher'].create({'voucher_type': 'journal', 'company_id': c.id, 'date': today,
                                               'reference': 'CAP-%s' % a.code, 'memo': label, 'line_ids': vl})
            v.action_post()
            # tax: an expense of a past year was deducted on that year's return, so nothing is left to claim; this year's is
            # claimed from now as an asset
            a.write({'opening_depreciation': catchup, 'tax_opening': amount if past else 0.0, 'dep_from': period_of(today)[1], 'capitalise_voucher_id': v.id, 'state': 'running'})
            made |= a
        return {'assets': [self._row(a) for a in made]}
