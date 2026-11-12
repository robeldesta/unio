"""Calls of the Projects screens: portfolio, a job (overview, plan, budget, BOQ, billing, costs, subcontracts, team),
timesheets and approvals, Excel for budgets and bills of quantities, and setup.

Actual costs and revenue come from posted books: voucher lines and store issues tagged with the job. Stock items bought
on a bill are counted when issued to the job (store issue), not when bought. Timesheet hours are valued at the person's
cost per hour (pay of the job record ÷ hours a month, plus employer pension) as a management allocation."""
import base64
import io
from collections import defaultdict
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.sonador_books.models.ethiopian_calendar import format_ethiopian

from .job import COST_KINDS, JOB_KINDS, JOB_STATES


def _s(d):
    return fields.Date.to_string(d) if d else False


def _ec(d):
    return format_ethiopian(d) if d else ''


EXPENSE_TYPES = ('expense', 'expense_direct_cost', 'expense_depreciation', 'expense_other')
INCOME_TYPES = ('income', 'income_other')


class SbProjects(models.AbstractModel):
    _name = 'sb.projects'
    _description = 'Projects'

    # ------------------------------------------------------------------ access
    def _is_manager(self):
        return self.env.su or self.env.user.has_group('sonador_project.group_manager')

    def _require(self, manager=False):
        if manager and not self._is_manager():
            raise AccessError(self.env._('Only a project manager can do this.'))
        if not (self.env.su or self.env.user.has_group('sonador_project.group_user')):
            raise AccessError(self.env._('Projects is for the project team.'))

    def _me(self):
        return self.env['sb.people']._me()

    def _J(self):
        return self.env['sb.job'].sudo()

    def _my_jobs(self):
        me = self._me()
        dom = [('company_id', '=', self.env.company.id)]
        if not self._is_manager():
            dom += ['|', ('manager_id', '=', me.id), ('member_ids', 'in', me.ids)]
        return self._J().search(dom)

    def _can_edit(self, job):
        me = self._me()
        return self._is_manager() and (self.env.su or self.env.user.has_group('sonador_project.group_manager'))

    # ------------------------------------------------------------------ context
    @api.model
    def ctx(self):
        self._require()
        me = self._me()
        c = self.env.company
        return {'is_manager': self._is_manager(), 'me': me.id or False, 'company': c.name, 'today': _s(fields.Date.context_today(self)),
                'today_ec': _ec(fields.Date.context_today(self)),
                'kinds': [{'id': k, 'name': v} for k, v in JOB_KINDS], 'states': [{'id': k, 'name': v} for k, v in JOB_STATES],
                'cost_kinds': [{'id': k, 'name': v} for k, v in COST_KINDS],
                'cost_codes': [{'id': x.id, 'code': x.code, 'name': x.name, 'kind': x.kind}
                               for x in self.env['sb.cost.code'].sudo().search(['|', ('company_id', '=', c.id), ('company_id', '=', False)])],
                'people': [{'id': e.id, 'name': e.name} for e in self.env['hr.employee'].sudo().search(
                    [('company_id', '=', c.id), ('active', '=', True)], order='name')],
                'vat_on': c.sb_job_vat_on}

    # ------------------------------------------------------------------ money from the books
    def _code_of(self, job, line, default_kind):
        if line.cost_code_id:
            return line.cost_code_id
        Code = self.env['sb.cost.code'].sudo()
        return Code.search([('kind', '=', default_kind), '|', ('company_id', '=', job.company_id.id), ('company_id', '=', False)], limit=1)

    def _actuals(self, jobs):
        """{job_id: {'cost': {cost_code_id: amount}, 'revenue': x, 'lines': [...], 'labour_hours': h}}"""
        out = {j.id: {'cost': defaultdict(float), 'revenue': 0.0, 'lines': [], 'hours': 0.0} for j in jobs}
        subs = defaultdict(set)
        for s in self.env['sb.job.sub'].sudo().search([('job_id', 'in', jobs.ids)]):
            subs[s.job_id.id].add(s.partner_id.commercial_partner_id.id)
        VL = self.env['sb.voucher.line'].sudo()
        for l in VL.search([('job_id', 'in', jobs.ids), ('voucher_id.state', '=', 'posted')]):
            j, v = l.job_id, l.voucher_id
            vt = v.voucher_type
            acc_type = l.account_id.account_type or ''
            amt_cost = amt_rev = 0.0
            if vt in ('sale', 'cash_sale'):
                amt_rev = l.price_subtotal
            elif vt == 'sale_return':
                amt_rev = -l.price_subtotal
            elif vt in ('bill', 'cash_purchase', 'purchase_return'):
                if l.product_id and l.product_id.is_storable:
                    continue  # counted when issued from the store to the job
                amt_cost = -l.price_subtotal if vt == 'purchase_return' else l.price_subtotal
            elif vt == 'payment':
                if acc_type in EXPENSE_TYPES:
                    amt_cost = l.amount
            elif vt == 'receipt':
                if acc_type in INCOME_TYPES:
                    amt_rev = l.amount
            elif vt in ('journal', 'opening'):
                if acc_type in EXPENSE_TYPES:
                    amt_cost = l.debit - l.credit
                elif acc_type in INCOME_TYPES:
                    amt_rev = l.credit - l.debit
            if not (amt_cost or amt_rev):
                continue
            o = out[j.id]
            if amt_cost:
                kind = 'subcontract' if (v.partner_id.commercial_partner_id.id in subs[j.id]) else 'other'
                code = self._code_of(j, l, kind)
                o['cost'][code.id] += amt_cost
            o['revenue'] += amt_rev
            o['lines'].append({'date': _s(v.date), 'ec': _ec(v.date), 'ref': v.name, 'type': dict(v._fields['voucher_type']._description_selection(self.env)).get(vt),
                               'who': v.partner_id.name or '', 'what': l.name or l.product_id.display_name or l.account_id.display_name or '',
                               'cost': round(amt_cost, 2), 'revenue': round(amt_rev, 2),
                               'code': (l.cost_code_id or (self._code_of(j, l, 'other') if amt_cost else l.cost_code_id)).display_name or '',
                               'phase': l.job_phase_id.name or '', 'model': 'sb.voucher', 'res_id': v.id})
        SL = self.env['sb.stock.voucher.line'].sudo()
        for l in SL.search([('job_id', 'in', jobs.ids), ('voucher_id.state', '=', 'posted')]):
            v = l.voucher_id
            sign = -1 if v.voucher_type in ('grn', 'opening') else 1
            amt = sign * (l.value or 0.0)
            if not amt:
                continue
            code = self._code_of(l.job_id, l, 'material')
            out[l.job_id.id]['cost'][code.id] += amt
            out[l.job_id.id]['lines'].append({'date': _s(v.date), 'ec': _ec(v.date), 'ref': v.name, 'type': 'Store issue', 'who': '',
                                              'what': '%s × %s' % (l.quantity, l.product_id.display_name), 'cost': round(amt, 2), 'revenue': 0.0,
                                              'code': code.display_name or '', 'phase': l.job_phase_id.name or '', 'model': 'sb.stock.voucher', 'res_id': v.id})
        T = self.env['sb.timesheet'].sudo()
        for t in T.search([('job_id', 'in', jobs.ids), ('state', '=', 'approved')]):
            code = self.env['sb.cost.code'].sudo().search([('kind', '=', 'labour')], limit=1)
            out[t.job_id.id]['cost'][code.id] += t.hours * t.cost_rate
            out[t.job_id.id]['hours'] += t.hours
        for j in jobs:
            labour = sum(T.search([('job_id', '=', j.id), ('state', '=', 'approved')]).mapped(lambda t: t.hours * t.cost_rate))
            if labour:
                out[j.id]['lines'].append({'date': '', 'ec': '', 'ref': 'Timesheets', 'type': 'Approved hours', 'who': '',
                                           'what': '%s h' % round(out[j.id]['hours'], 1), 'cost': round(labour, 2), 'revenue': 0.0,
                                           'code': 'Labour', 'phase': '', 'model': False, 'res_id': False})
        return out

    def _fin(self, j, act=None):
        act = act or self._actuals(j)[j.id]
        budget = defaultdict(float)
        for b in j.budget_ids:
            budget[b.cost_code_id.id] += b.amount
        cost = sum(act['cost'].values())
        bud = sum(budget.values())
        codes = set(budget) | set(act['cost'])
        eac = sum(act['cost'].get(c, 0) + max(budget.get(c, 0) - act['cost'].get(c, 0), 0) for c in codes)
        bills = j.bill_ids.filtered(lambda b: b.state in ('approved', 'invoiced'))
        certified = sum(bills.filtered(lambda b: b.kind != 'retention').mapped('gross'))
        retention_held = sum(bills.filtered(lambda b: b.kind != 'retention').mapped('retention')) - \
            sum(bills.filtered(lambda b: b.kind == 'retention').mapped('gross'))
        recovered = sum(bills.mapped('recovery'))
        contract = j.contract_value or (sum(j.boq_ids.mapped('amount')) if j.kind == 'construction' else 0.0)
        if j.kind == 'construction' and contract:
            pct = min(100.0, 100.0 * certified / contract)
        elif j.kind == 'tm':
            pct = None
        elif bud:
            pct = min(100.0, 100.0 * cost / bud)
        else:
            tasks = j.task_ids
            pct = 100.0 * len(tasks.filtered(lambda t: t.state == 'done')) / len(tasks) if tasks else 0.0
        earned = contract * (pct or 0) / 100.0 if pct is not None else certified
        today = fields.Date.context_today(self)
        cost_rag = 'green'
        if bud and pct is not None:
            planned = bud * max(pct, 1) / 100.0
            cost_rag = 'red' if cost > planned * 1.10 and cost > bud * 0.05 else 'amber' if cost > planned * 1.0 and cost > bud * 0.05 else 'green'
        if bud and eac > bud * 1.05:
            cost_rag = 'red'
        sched_rag = 'green'
        if j.date_end and j.state in ('active', 'hold'):
            if today > j.date_end:
                sched_rag = 'red'
            elif j.date_start and pct is not None:
                span = max((j.date_end - j.date_start).days, 1)
                elapsed = 100.0 * (today - j.date_start).days / span
                if elapsed - pct > 20:
                    sched_rag = 'amber'
        return {'contract': round(contract, 2), 'budget': round(bud, 2), 'cost': round(cost, 2), 'revenue': round(act['revenue'], 2),
                'eac': round(eac, 2), 'margin_now': round(act['revenue'] - cost, 2),
                'margin_eac': round(contract - eac, 2) if contract else None,
                'margin_pct': round(100.0 * (contract - eac) / contract, 1) if contract else None,
                'pct': round(pct, 1) if pct is not None else None, 'certified': round(certified, 2),
                'over_under': round(certified - earned, 2) if pct is not None else None,
                'retention': round(retention_held, 2), 'recovered': round(recovered, 2),
                'advance_left': round(max(j.advance_amount - recovered, 0), 2), 'hours': round(act['hours'], 1),
                'cost_rag': cost_rag, 'sched_rag': sched_rag,
                'by_code': [{'id': c, 'name': self.env['sb.cost.code'].sudo().browse(c).display_name,
                             'kind': self.env['sb.cost.code'].sudo().browse(c).kind,
                             'budget': round(budget.get(c, 0), 2), 'actual': round(act['cost'].get(c, 0), 2),
                             'eac': round(act['cost'].get(c, 0) + max(budget.get(c, 0) - act['cost'].get(c, 0), 0), 2)}
                            for c in sorted(codes, key=lambda c: self.env['sb.cost.code'].sudo().browse(c).code or '')]}

    def _row(self, j, fin):
        return {'id': j.id, 'code': j.code, 'name': j.name, 'customer': j.partner_id.name or '', 'kind': j.kind, 'state': j.state,
                'manager': j.manager_id.name or '', 'start': _ec(j.date_start), 'end': _ec(j.date_end), 'site': j.site or '', **fin}

    # ------------------------------------------------------------------ portfolio
    @api.model
    def portfolio(self, f=None):
        self._require()
        f = f or {}
        jobs = self._my_jobs()
        if f.get('state') == 'open':
            jobs = jobs.filtered(lambda j: j.state in ('draft', 'active', 'hold'))
        elif f.get('state'):
            jobs = jobs.filtered(lambda j: j.state == f['state'])
        if f.get('kind'):
            jobs = jobs.filtered(lambda j: j.kind == f['kind'])
        if f.get('search'):
            s = f['search'].lower()
            jobs = jobs.filtered(lambda j: s in (j.name or '').lower() or s in (j.code or '').lower() or s in (j.partner_id.name or '').lower())
        acts = self._actuals(jobs)
        rows = []
        for j in jobs.sorted(lambda x: (x.state not in ('active', 'hold'), x.code), reverse=False):
            fin = self._fin(j, acts[j.id])
            fin.pop('by_code')
            rows.append(self._row(j, fin))
        tot = lambda k: round(sum(r[k] or 0 for r in rows), 2)  # noqa: E731
        return {'rows': rows, 'totals': {k: tot(k) for k in ('contract', 'budget', 'cost', 'revenue', 'eac', 'certified', 'retention')},
                'red': len([r for r in rows if 'red' in (r['cost_rag'], r['sched_rag'])])}

    # ------------------------------------------------------------------ one job
    @api.model
    def job(self, job_id):
        self._require()
        j = self._J().browse(int(job_id))
        if j not in self._my_jobs():
            raise AccessError(self.env._('Not one of your jobs.'))
        act = self._actuals(j)[j.id]
        fin = self._fin(j, act)
        Sub = self.env['sb.voucher.line'].sudo()
        subs = []
        for s in j.sub_ids:
            lines = Sub.search([('job_id', '=', j.id), ('voucher_id.state', '=', 'posted'), ('voucher_id.voucher_type', 'in', ('bill', 'cash_purchase')),
                                ('voucher_id.partner_id', 'child_of', s.partner_id.commercial_partner_id.id)])
            billed = sum(lines.mapped('price_subtotal'))
            subs.append({'id': s.id, 'partner_id': s.partner_id.id, 'partner': s.partner_id.name, 'name': s.name, 'value': s.value,
                         'retention_pct': s.retention_pct, 'billed': round(billed, 2), 'left': round(s.value - billed, 2),
                         'phase_id': s.phase_id.id or '', 'note': s.note or ''})
        hours = defaultdict(float)
        for t in j.timesheet_ids.filtered(lambda t: t.state == 'approved'):
            hours[t.employee_id.name] += t.hours
        return dict(self._row(j, fin), partner_id=j.partner_id.id or False, manager_id=j.manager_id.id or False,
                    member_ids=j.member_ids.ids, date_start=_s(j.date_start), date_end=_s(j.date_end), contract_value=j.contract_value,
                    vat=j.vat, retention_pct=j.retention_pct, advance_amount=j.advance_amount, advance_recovery_pct=j.advance_recovery_pct,
                    bill_rate=j.bill_rate, description=j.description or '', can_edit=self._is_manager(),
                    phases=[{'id': p.id, 'name': p.name, 'start': _s(p.date_start), 'end': _s(p.date_end), 'start_ec': _ec(p.date_start),
                             'end_ec': _ec(p.date_end), 'progress': p.progress, 'sequence': p.sequence} for p in j.phase_ids],
                    tasks=[{'id': t.id, 'name': t.name, 'phase_id': t.phase_id.id or '', 'phase': t.phase_id.name or '', 'employee_id': t.employee_id.id or '',
                            'employee': t.employee_id.name or '', 'start': _s(t.date_start), 'due': _s(t.date_end), 'due_ec': _ec(t.date_end),
                            'state': t.state, 'hours': t.planned_hours, 'priority': t.priority, 'milestone': t.milestone,
                            'description': t.description or '',
                            'late': bool(t.date_end and t.state != 'done' and t.date_end < fields.Date.context_today(self))} for t in j.task_ids],
                    budget=[{'id': b.id, 'phase_id': b.phase_id.id or '', 'cost_code_id': b.cost_code_id.id, 'name': b.name or '', 'qty': b.qty,
                             'unit': b.unit or '', 'rate': b.rate, 'amount': b.amount} for b in j.budget_ids],
                    boq=[{'id': b.id, 'section': b.section or '', 'item_no': b.item_no or '', 'name': b.name, 'unit': b.unit or '', 'qty': b.qty,
                          'rate': b.rate, 'amount': b.amount, 'done': b.qty_done, 'done_amount': round(b.qty_done * b.rate, 2),
                          'pct': round(100.0 * b.qty_done / b.qty, 1) if b.qty else 0} for b in j.boq_ids],
                    bills=[self._bill_row(b) for b in j.bill_ids],
                    subs=subs, ledger=sorted(act['lines'], key=lambda x: x['date'] or '9999', reverse=True)[:300],
                    hours=[{'name': k, 'hours': round(v, 1)} for k, v in sorted(hours.items(), key=lambda x: -x[1])],
                    fin=fin)

    @api.model
    def save_job(self, job_id, vals):
        self._require(manager=True)
        w = {}
        for k in ('name', 'site', 'description', 'kind', 'state'):
            if k in vals:
                w[k] = vals[k] or False
        for k in ('partner_id', 'manager_id'):
            if k in vals:
                w[k] = int(vals[k]) if vals[k] else False
        for k in ('contract_value', 'retention_pct', 'advance_amount', 'advance_recovery_pct', 'bill_rate'):
            if k in vals:
                w[k] = float(vals[k] or 0)
        for k in ('date_start', 'date_end'):
            if k in vals:
                w[k] = vals[k] or False
        if 'vat' in vals:
            w['vat'] = bool(vals['vat'])
        if 'member_ids' in vals:
            w['member_ids'] = [(6, 0, [int(x) for x in vals['member_ids'] if x])]
        if w.get('state') == 'done':
            w['date_done'] = fields.Date.context_today(self)
        if job_id:
            j = self._J().browse(int(job_id))
            j.write(w)
        else:
            if not w.get('name'):
                raise ValidationError(self.env._('Name the job.'))
            j = self._J().create(dict(w, company_id=self.env.company.id))
            if j.kind in ('fixed', 'construction') and not j.phase_ids:
                default = ['Mobilisation', 'Execution', 'Handover'] if j.kind == 'construction' else ['Planning', 'Delivery', 'Close-out']
                j.phase_ids = [(0, 0, {'name': n, 'sequence': i}) for i, n in enumerate(default)]
        return self.job(j.id)

    @api.model
    def customers(self, search):
        P = self.env['res.partner'].sudo()
        return [{'id': p.id, 'name': p.name} for p in P.search([('name', 'ilike', search or ''), ('is_company', '=', True)], limit=15)] + \
            [{'id': p.id, 'name': p.name} for p in P.search([('name', 'ilike', search or ''), ('is_company', '=', False), ('customer_rank', '>', 0)], limit=5)]

    # phases and tasks
    @api.model
    def save_phase(self, job_id, vals):
        self._require(manager=True)
        P = self.env['sb.job.phase'].sudo()
        w = {'name': (vals.get('name') or '').strip(), 'date_start': vals.get('start') or False, 'date_end': vals.get('end') or False,
             'progress': float(vals.get('progress') or 0)}
        if not w['name']:
            raise ValidationError(self.env._('Name the phase.'))
        if vals.get('id'):
            P.browse(int(vals['id'])).write(w)
        else:
            P.create(dict(w, job_id=int(job_id), sequence=len(self._J().browse(int(job_id)).phase_ids) + 1))
        return self.job(job_id)

    @api.model
    def remove_phase(self, phase_id):
        self._require(manager=True)
        p = self.env['sb.job.phase'].sudo().browse(int(phase_id))
        job = p.job_id
        p.unlink()
        return self.job(job.id)

    @api.model
    def save_task(self, job_id, vals):
        self._require()
        T = self.env['sb.job.task'].sudo()
        me = self._me()
        if vals.get('id'):
            t = T.browse(int(vals['id']))
            if not self._is_manager() and t.employee_id != me:
                if set(vals) - {'id', 'state'}:
                    raise AccessError(self.env._('Only the project manager can change this task.'))
        w = {}
        for k in ('name', 'description'):
            if k in vals:
                w[k] = vals[k] or False
        for k, f in (('phase_id', 'phase_id'), ('employee_id', 'employee_id')):
            if k in vals:
                w[f] = int(vals[k]) if vals[k] else False
        for k, f in (('start', 'date_start'), ('due', 'date_end')):
            if k in vals:
                w[f] = vals[k] or False
        for k in ('state',):
            if k in vals:
                w[k] = vals[k]
        if 'hours' in vals:
            w['planned_hours'] = float(vals['hours'] or 0)
        for k in ('priority', 'milestone'):
            if k in vals:
                w[k] = bool(vals[k])
        if vals.get('id'):
            T.browse(int(vals['id'])).write(w)
        else:
            self._require(manager=True)
            if not w.get('name'):
                raise ValidationError(self.env._('Name the task.'))
            T.create(dict(w, job_id=int(job_id)))
        return self.job(job_id)

    @api.model
    def remove_task(self, task_id):
        self._require(manager=True)
        t = self.env['sb.job.task'].sudo().browse(int(task_id))
        job = t.job_id
        t.unlink()
        return self.job(job.id)

    # budget and BOQ
    @api.model
    def save_budget(self, job_id, rows):
        self._require(manager=True)
        j = self._J().browse(int(job_id))
        B = self.env['sb.job.budget'].sudo()
        keep = []
        for r in rows:
            if not r.get('cost_code_id'):
                continue
            w = {'phase_id': int(r['phase_id']) if r.get('phase_id') else False, 'cost_code_id': int(r['cost_code_id']),
                 'name': (r.get('name') or '').strip() or False, 'qty': float(r.get('qty') or 0), 'unit': (r.get('unit') or '').strip() or False,
                 'rate': float(r.get('rate') or 0)}
            if r.get('id'):
                b = B.browse(int(r['id']))
                b.write(w)
            else:
                b = B.create(dict(w, job_id=j.id))
            keep.append(b.id)
        j.budget_ids.filtered(lambda b: b.id not in keep).unlink()
        return self.job(j.id)

    @api.model
    def save_boq(self, job_id, rows):
        self._require(manager=True)
        j = self._J().browse(int(job_id))
        B = self.env['sb.job.boq'].sudo()
        keep = []
        for i, r in enumerate(rows):
            if not (r.get('name') or '').strip():
                continue
            w = {'sequence': i, 'section': (r.get('section') or '').strip() or False, 'item_no': (str(r.get('item_no') or '')).strip() or False,
                 'name': r['name'].strip(), 'unit': (r.get('unit') or '').strip() or False, 'qty': float(r.get('qty') or 0),
                 'rate': float(r.get('rate') or 0)}
            if r.get('id'):
                b = B.browse(int(r['id']))
                if b.qty_done and w['qty'] < b.qty_done:
                    raise ValidationError(self.env._('Item %(i)s: the contract quantity is below what is already certified (%(d)s).', i=b.item_no or b.name, d=b.qty_done))
                b.write(w)
            else:
                b = B.create(dict(w, job_id=j.id))
            keep.append(b.id)
        gone = j.boq_ids.filtered(lambda b: b.id not in keep)
        if gone.filtered('qty_done'):
            raise ValidationError(self.env._('Certified items cannot be removed.'))
        gone.unlink()
        if j.kind == 'construction' and not j.contract_value:
            j.contract_value = sum(j.boq_ids.mapped('amount'))
        return self.job(j.id)

    @api.model
    def read_excel(self, data, kind):
        """Rows from an Excel bill of quantities or budget (headings: Item, Description, Unit, Qty, Rate, Section)."""
        self._require(manager=True)
        import openpyxl
        try:
            wb = openpyxl.load_workbook(io.BytesIO(base64.b64decode(data)), data_only=True)
        except Exception:
            raise UserError(self.env._('This file is not an Excel workbook (.xlsx).'))
        ws = wb.worksheets[0]
        keys = {'item_no': ('item', 'no', 'item no', 's/n', 'ref'), 'name': ('description', 'work', 'activity', 'description of work'),
                'unit': ('unit', 'uom'), 'qty': ('qty', 'quantity'), 'rate': ('rate', 'unit rate', 'unit price', 'price'),
                'section': ('section', 'bill', 'part'), 'code': ('cost code', 'code')}
        head, cols = None, {}
        for r in range(1, 25):
            vals = [str(c.value or '').strip().lower() for c in ws[r]]
            hits = {}
            for i, v in enumerate(vals):
                for k, words in keys.items():
                    if k not in hits and v and (v in words or any(v.startswith(w) for w in words if len(w) > 3)):
                        hits[k] = i
            if 'name' in hits and ('qty' in hits or 'rate' in hits):
                head, cols = r, hits
                break
        if not head:
            raise UserError(self.env._('Could not find the heading row (Description, Unit, Qty, Rate).'))
        rows, section = [], ''
        for row in ws.iter_rows(min_row=head + 1, values_only=True):
            get = lambda k: row[cols[k]] if k in cols and cols[k] < len(row) else None  # noqa: E731
            name = str(get('name') or '').strip()
            qty, rate = get('qty'), get('rate')
            num = lambda x: float(x) if isinstance(x, (int, float)) else float(str(x).replace(',', '')) if x not in (None, '') and str(x).replace(',', '').replace('.', '', 1).isdigit() else 0.0  # noqa: E731
            if name and not num(qty) and not num(rate):
                section = name  # a heading row inside the bill
                continue
            if not name:
                continue
            rows.append({'item_no': str(get('item_no') or '').strip(), 'name': name, 'unit': str(get('unit') or '').strip(), 'qty': num(qty),
                         'rate': num(rate), 'section': str(get('section') or section or '').strip()})
        return rows

    @api.model
    def excel_template(self, kind):
        import openpyxl
        from openpyxl.styles import Font, PatternFill
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'BOQ' if kind == 'boq' else 'Budget'
        head = ['Item', 'Description', 'Unit', 'Qty', 'Rate', 'Amount'] if kind == 'boq' else ['Cost code', 'Description', 'Unit', 'Qty', 'Rate', 'Amount']
        for i, h in enumerate(head, 1):
            c = ws.cell(row=1, column=i, value=h)
            c.font = Font(bold=True, color='FFFFFF')
            c.fill = PatternFill('solid', fgColor='243742')
        sample = [('', 'SUBSTRUCTURE', '', None, None), ('1.1', 'Excavation in ordinary soil to 1.5 m depth', 'm3', 120, 450),
                  ('1.2', 'C-25 concrete in footings', 'm3', 38, 9800), ('', 'SUPERSTRUCTURE', '', None, None),
                  ('2.1', 'HCB 20 cm wall', 'm2', 640, 1150)] if kind == 'boq' else \
            [('LAB', 'Site crew', 'days', 120, 1800), ('MAT', 'Cement', 'bags', 900, 950), ('SUB', 'Electrical works', 'lot', 1, 450000)]
        for r, s in enumerate(sample, 2):
            for c, v in enumerate(s, 1):
                ws.cell(row=r, column=c, value=v)
            ws.cell(row=r, column=6, value='=IF(AND(ISNUMBER(D%d),ISNUMBER(E%d)),D%d*E%d,"")' % (r, r, r, r))
        for col, w in zip('ABCDEF', (10, 50, 8, 10, 12, 14)):
            ws.column_dimensions[col].width = w
        buf = io.BytesIO()
        wb.save(buf)
        return {'name': '%s template.xlsx' % ('BOQ' if kind == 'boq' else 'Budget'), 'data': base64.b64encode(buf.getvalue()).decode()}

    # subcontracts
    @api.model
    def save_sub(self, job_id, vals):
        self._require(manager=True)
        S = self.env['sb.job.sub'].sudo()
        w = {'partner_id': int(vals['partner_id']) if vals.get('partner_id') else False, 'name': (vals.get('name') or '').strip(),
             'value': float(vals.get('value') or 0), 'retention_pct': float(vals.get('retention_pct') or 0),
             'phase_id': int(vals['phase_id']) if vals.get('phase_id') else False, 'note': vals.get('note') or False}
        if not w['partner_id'] or not w['name']:
            raise ValidationError(self.env._('Pick the subcontractor and the scope.'))
        if vals.get('id'):
            S.browse(int(vals['id'])).write(w)
        else:
            S.create(dict(w, job_id=int(job_id)))
        return self.job(job_id)

    # ------------------------------------------------------------------ billing
    def _bill_row(self, b):
        if b.voucher_id and b.voucher_id.state == 'posted' and b.state == 'approved':
            b.state = 'invoiced'
        return {'id': b.id, 'kind': b.kind, 'number': b.number or '', 'date': _s(b.date), 'ec': _ec(b.date), 'name': b.name or '',
                'state': b.state, 'gross_to_date': b.gross_to_date, 'prev_gross': b.prev_gross, 'gross': b.gross, 'retention': b.retention,
                'recovery': b.recovery, 'net': b.net, 'vat': b.vat, 'total': b.total, 'note': b.note or '',
                'voucher': b.voucher_id and {'id': b.voucher_id.id, 'name': b.voucher_id.name or '', 'state': b.voucher_id.state} or False,
                'lines': [{'id': l.id, 'boq_id': l.boq_id.id, 'item_no': l.boq_id.item_no or '', 'name': l.name, 'unit': l.unit or '', 'rate': l.rate,
                           'contract_qty': l.boq_id.qty, 'prev': l.qty_prev, 'to_date': l.qty_to_date, 'this': l.qty_this,
                           'amount_this': l.amount_this, 'amount_to_date': l.amount_to_date} for l in b.line_ids],
                'hours': round(sum(b.timesheet_ids.mapped('hours')), 2)}

    def _recompute(self, b):
        j = b.job_id
        prev_bills = j.bill_ids.filtered(lambda x: x.state in ('approved', 'invoiced') and x.kind != 'retention' and x.id != b.id)
        if b.kind == 'ipc':
            for l in b.line_ids:
                l.qty_this = l.qty_to_date - l.qty_prev
                l.amount_to_date = round(l.qty_to_date * l.rate, 2)
                l.amount_this = round(l.qty_this * l.rate, 2)
            b.gross_to_date = sum(b.line_ids.mapped('amount_to_date'))
            b.prev_gross = sum(prev_bills.filtered(lambda x: x.kind == 'ipc').mapped('gross'))
            b.gross = round(b.gross_to_date - b.prev_gross, 2)
        elif b.kind == 'tm':
            b.gross = round(sum(t.hours * (t.bill_rate or j.bill_rate) for t in b.timesheet_ids), 2)
            b.gross_to_date = b.gross + sum(prev_bills.mapped('gross'))
        elif b.kind == 'milestone':
            b.gross_to_date = b.gross + sum(prev_bills.mapped('gross'))
        if b.kind == 'retention':
            b.retention = b.recovery = 0
        else:
            b.retention = round(b.gross * (j.retention_pct or 0) / 100.0, 2) if b.kind == 'ipc' or j.kind == 'construction' else 0.0
            left = max(j.advance_amount - sum(prev_bills.mapped('recovery')), 0)
            b.recovery = round(min(b.gross * (j.advance_recovery_pct or 0) / 100.0, left), 2)
        b.net = round(b.gross - b.retention - b.recovery, 2)
        base = b.net if j.company_id.sb_job_vat_on == 'net' else b.gross
        b.vat = round(base * 0.15, 2) if j.vat else 0.0
        b.total = round(b.net + b.vat, 2)

    @api.model
    def new_bill(self, job_id, kind):
        self._require(manager=True)
        j = self._J().browse(int(job_id))
        B = self.env['sb.job.bill'].sudo()
        if j.bill_ids.filtered(lambda x: x.state == 'draft'):
            raise UserError(self.env._('Finish or delete the draft bill first.'))
        n = len(j.bill_ids.filtered(lambda x: x.kind == kind)) + 1
        prefix = {'ipc': 'IPC', 'milestone': 'INV', 'tm': 'TM', 'retention': 'RET'}[kind]
        b = B.create({'job_id': j.id, 'kind': kind, 'number': '%s-%02d' % (prefix, n), 'period_to': fields.Date.context_today(self)})
        if kind == 'ipc':
            if not j.boq_ids:
                raise UserError(self.env._('Enter the bill of quantities first.'))
            b.line_ids = [(0, 0, {'boq_id': x.id, 'sequence': i, 'name': ('%s %s' % (x.item_no or '', x.name)).strip(), 'unit': x.unit,
                                  'rate': x.rate, 'qty_prev': x.qty_done, 'qty_to_date': x.qty_done}) for i, x in enumerate(j.boq_ids)]
            b.name = self.env._('Interim payment certificate No. %s', n)
        elif kind == 'tm':
            ts = self.env['sb.timesheet'].sudo().search([('job_id', '=', j.id), ('state', '=', 'approved'), ('billable', '=', True), ('billed_id', '=', False)])
            if not ts:
                raise UserError(self.env._('No approved billable hours to bill.'))
            ts.billed_id = b.id
            b.name = self.env._('Time and materials to %s', _ec(max(ts.mapped('date'))))
        elif kind == 'retention':
            held = self._fin(j)['retention']
            if held <= 0:
                raise UserError(self.env._('No retention is held.'))
            b.gross = held
            b.name = self.env._('Release of retention')
        else:
            b.name = self.env._('Progress payment %s', n)
        self._recompute(b)
        return self._bill_row(b)

    @api.model
    def save_bill(self, bill_id, vals):
        self._require(manager=True)
        b = self.env['sb.job.bill'].sudo().browse(int(bill_id))
        if b.state != 'draft':
            raise UserError(self.env._('Only a draft bill can be changed.'))
        for k in ('name', 'note'):
            if k in vals:
                b[k] = vals[k] or False
        if 'date' in vals:
            b.date = vals['date']
        if b.kind in ('milestone', 'retention') and 'gross' in vals:
            b.gross = float(vals['gross'] or 0)
        for r in vals.get('lines') or []:
            l = b.line_ids.filtered(lambda x: x.id == int(r['id']))
            q = float(r.get('to_date') or 0)
            if q < l.qty_prev - 1e-9:
                raise ValidationError(self.env._('%(i)s: measured to date (%(q)s) is less than already certified (%(p)s).', i=l.name, q=q, p=l.qty_prev))
            if l.boq_id.qty and q > l.boq_id.qty * 1.25:
                raise ValidationError(self.env._('%(i)s: %(q)s is far above the contract quantity (%(c)s). Record a variation first.', i=l.name, q=q, c=l.boq_id.qty))
            l.qty_to_date = q
        self._recompute(b)
        return self._bill_row(b)

    def _tax(self, company):
        return self.env['account.tax'].sudo().search([('type_tax_use', '=', 'sale'), ('amount', '=', 15), ('company_id', '=', company.id)], limit=1)

    @api.model
    def approve_bill(self, bill_id):
        """Approve and make the draft sales invoice in Accounting (the accountant numbers and posts it)."""
        self._require(manager=True)
        b = self.env['sb.job.bill'].sudo().browse(int(bill_id))
        j, c = b.job_id, b.job_id.company_id
        if b.state != 'draft':
            raise UserError(self.env._('Already approved.'))
        if b.gross <= 0:
            raise ValidationError(self.env._('Nothing to bill: the amount this time is zero.'))
        if not j.partner_id:
            raise ValidationError(self.env._('Set the customer of the job.'))
        income = c.sb_job_income_account_id
        if not income:
            raise UserError(self.env._('Set the job revenue account in Projects settings.'))
        tax = self._tax(c) if j.vat else self.env['account.tax']
        if j.vat and not tax:
            raise UserError(self.env._('No 15% sales VAT tax found in Accounting.'))
        taxes = [(6, 0, tax.ids)]
        # VAT on the amount due: the deduction lines carry VAT too, so the invoice VAT is 15% of the net
        ded_taxes = taxes if c.sb_job_vat_on == 'net' else []
        if b.kind == 'retention':
            lines = [(0, 0, {'name': self.env._('%(n)s – release of retention, %(j)s', n=b.number, j=j.name), 'quantity': 1, 'price_unit': b.gross,
                             'account_id': c.sb_job_retention_account_id.id, 'tax_ids': [], 'job_id': j.id})]
        else:
            lines = [(0, 0, {'name': '%s – %s' % (b.number, b.name or j.name), 'quantity': 1, 'price_unit': b.gross, 'account_id': income.id,
                             'tax_ids': taxes, 'job_id': j.id})]
            if b.retention:
                lines.append((0, 0, {'name': self.env._('Less retention %s%%', j.retention_pct), 'quantity': 1, 'price_unit': -b.retention,
                                     'account_id': c.sb_job_retention_account_id.id, 'tax_ids': ded_taxes}))
            if b.recovery:
                lines.append((0, 0, {'name': self.env._('Less advance recovery'), 'quantity': 1, 'price_unit': -b.recovery,
                                     'account_id': c.sb_job_advance_account_id.id, 'tax_ids': ded_taxes}))
        v = self.env['sb.voucher'].sudo().create({'voucher_type': 'sale', 'company_id': c.id, 'partner_id': j.partner_id.id, 'date': b.date,
                                                  'reference': '%s %s' % (j.code, b.number), 'memo': b.name, 'line_ids': lines})
        b.write({'state': 'approved', 'voucher_id': v.id})
        if b.kind == 'ipc':
            for l in b.line_ids:
                l.boq_id.qty_done = l.qty_to_date
        j.message_post(body=self.env._('%(n)s approved: %(t)s ETB due (draft sales invoice made for the accountant).', n=b.number,
                                       t='{:,.2f}'.format(b.total)))
        return self.job(j.id)

    @api.model
    def delete_bill(self, bill_id):
        self._require(manager=True)
        b = self.env['sb.job.bill'].sudo().browse(int(bill_id))
        j = b.job_id
        if b.voucher_id and b.voucher_id.state == 'posted':
            raise UserError(self.env._('The invoice is posted: void it in Accounting first.'))
        later = j.bill_ids.filtered(lambda x: x.kind == 'ipc' and x.id > b.id and x.state != 'draft')
        if b.kind == 'ipc' and b.state != 'draft' and later:
            raise UserError(self.env._('Later certificates exist: undo the latest first.'))
        if b.kind == 'ipc' and b.state != 'draft':
            for l in b.line_ids:
                l.boq_id.qty_done = l.qty_prev
        if b.voucher_id:
            b.voucher_id.unlink()
        b.timesheet_ids.write({'billed_id': False})
        b.unlink()
        return self.job(j.id)

    # ------------------------------------------------------------------ timesheets
    def _cost_rate(self, e):
        v = e.current_version_id
        wage = (v.wage or 0.0) + (getattr(v, 'sb_taxable_allowance', 0.0) or 0.0) + (getattr(v, 'sb_house_allowance', 0.0) or 0.0) + \
            (getattr(v, 'sb_position_allowance', 0.0) or 0.0)
        hours = getattr(e.company_id, 'sb_pay_hours', 0) or 208.0
        pension = (v.wage or 0.0) * 0.11 if getattr(e, 'sb_pension', True) else 0.0
        return round((wage + pension) / hours, 2)

    @api.model
    def week(self, day=False, employee_id=False):
        self._require()
        me = self._me()
        e = self.env['hr.employee'].sudo().browse(int(employee_id)) if employee_id else me
        if not e:
            return {'no_person': True}
        if e != me and not self._is_manager():
            raise AccessError(self.env._('Not your timesheet.'))
        d = fields.Date.to_date(day) if day else fields.Date.context_today(self)
        ws = d - timedelta(days=d.weekday())
        days = [ws + timedelta(days=i) for i in range(7)]
        T = self.env['sb.timesheet'].sudo()
        lines = T.search([('employee_id', '=', e.id), ('date', '>=', ws), ('date', '<=', days[-1])])
        rows = {}
        for t in lines:
            k = (t.job_id.id, t.task_id.id or 0)
            r = rows.setdefault(k, {'job_id': t.job_id.id, 'job': t.job_id.display_name, 'task_id': t.task_id.id or '', 'task': t.task_id.name or '',
                                    'hours': {}, 'states': {}, 'notes': {}})
            r['hours'][_s(t.date)] = t.hours
            r['states'][_s(t.date)] = t.state
            r['notes'][_s(t.date)] = t.note or ''
        jobs = self._J().search([('company_id', '=', e.company_id.id), ('state', 'in', ('active', 'hold')), '|', '|',
                                 ('member_ids', 'in', e.ids), ('manager_id', '=', e.id), ('kind', '=', 'internal')])
        return {'employee': {'id': e.id, 'name': e.name}, 'week': _s(ws), 'label': '%s – %s' % (_ec(ws), _ec(days[-1])),
                'prev': _s(ws - timedelta(days=7)), 'next': _s(ws + timedelta(days=7)),
                'days': [{'date': _s(x), 'name': x.strftime('%a'), 'ec': _ec(x).rsplit(',', 1)[0]} for x in days],
                'rows': list(rows.values()),
                'jobs': [{'id': j.id, 'name': j.display_name, 'tasks': [{'id': t.id, 'name': t.name} for t in j.task_ids.filtered(lambda t: t.state != 'done')]}
                         for j in jobs],
                'locked': all(t.state in ('submitted', 'approved') for t in lines) and bool(lines),
                'total': round(sum(lines.mapped('hours')), 2)}

    @api.model
    def save_week(self, week, rows, submit=False, employee_id=False):
        self._require()
        me = self._me()
        e = self.env['hr.employee'].sudo().browse(int(employee_id)) if employee_id else me
        if e != me and not self._is_manager():
            raise AccessError(self.env._('Not your timesheet.'))
        ws = fields.Date.to_date(week)
        T = self.env['sb.timesheet'].sudo()
        existing = {(t.job_id.id, t.task_id.id or 0, t.date): t for t in T.search([('employee_id', '=', e.id), ('date', '>=', ws),
                                                                                    ('date', '<=', ws + timedelta(days=6))])}
        seen = set()
        per_day = defaultdict(float)
        for r in rows:
            if not r.get('job_id'):
                continue
            for day, h in (r.get('hours') or {}).items():
                h = float(h or 0)
                d = fields.Date.to_date(day)
                k = (int(r['job_id']), int(r.get('task_id') or 0), d)
                seen.add(k)
                t = existing.get(k)
                per_day[d] += h
                if t and t.state in ('submitted', 'approved'):
                    continue
                if h <= 0:
                    if t:
                        t.unlink()
                    continue
                vals = {'hours': h, 'note': (r.get('notes') or {}).get(day) or False}
                if t:
                    t.write(dict(vals, state='draft', reject_note=False))
                else:
                    task = self.env['sb.job.task'].sudo().browse(int(r['task_id'])) if r.get('task_id') else False
                    T.create(dict(vals, employee_id=e.id, job_id=int(r['job_id']), task_id=task.id if task else False,
                                  phase_id=task.phase_id.id if task else False, date=d))
        for d, h in per_day.items():
            if h > 16:
                raise ValidationError(self.env._('%(h)s hours on %(d)s: more than a person can work in a day.', h=h, d=_ec(d)))
        for k, t in existing.items():
            if k not in seen and t.state in ('draft', 'rejected'):
                t.unlink()
        if submit:
            T.search([('employee_id', '=', e.id), ('date', '>=', ws), ('date', '<=', ws + timedelta(days=6)),
                      ('state', 'in', ('draft', 'rejected'))]).write({'state': 'submitted'})
        return self.week(week, employee_id)

    @api.model
    def approvals(self):
        self._require()
        me = self._me()
        T = self.env['sb.timesheet'].sudo()
        dom = [('state', '=', 'submitted'), ('company_id', '=', self.env.company.id)]
        if not self._is_manager():
            dom.append(('job_id.manager_id', '=', me.id))
        groups = defaultdict(lambda: {'hours': 0.0, 'ids': [], 'lines': []})
        for t in T.search(dom, order='employee_id, date'):
            g = groups[(t.employee_id.id, t.job_id.id)]
            g.update({'employee': t.employee_id.name, 'employee_id': t.employee_id.id, 'job': t.job_id.display_name, 'job_id': t.job_id.id})
            g['hours'] += t.hours
            g['ids'].append(t.id)
            g['lines'].append({'date': _ec(t.date), 'task': t.task_id.name or '', 'hours': t.hours, 'note': t.note or ''})
        return list(groups.values())

    @api.model
    def decide_hours(self, ids, approve, note=''):
        self._require()
        me = self._me()
        T = self.env['sb.timesheet'].sudo().browse([int(i) for i in ids])
        for t in T:
            if not self._is_manager() and t.job_id.manager_id != me:
                raise AccessError(self.env._('Only the project manager approves these hours.'))
            if t.employee_id == me and not self.env.su:
                raise AccessError(self.env._('Your own hours are approved by someone else.'))
        if approve:
            for t in T:
                t.write({'state': 'approved', 'approved_by': me.id or False, 'cost_rate': self._cost_rate(t.employee_id),
                         'bill_rate': t.bill_rate or t.job_id.bill_rate})
        else:
            if not (note or '').strip():
                raise ValidationError(self.env._('Say why the hours go back.'))
            T.write({'state': 'rejected', 'reject_note': note})
        return self.approvals()

    # ------------------------------------------------------------------ setup
    @api.model
    def settings(self):
        self._require(manager=True)
        c = self.env.company
        accs = self.env['account.account'].sudo().search([('company_ids', 'in', c.id)], order='code')
        row = lambda a: {'id': a.id, 'name': '%s %s' % (a.code, a.name)}  # noqa: E731
        of = lambda types: [row(a) for a in accs if a.account_type in types]  # noqa: E731
        return {'income': c.sb_job_income_account_id.id or '', 'retention': c.sb_job_retention_account_id.id or '',
                'advance': c.sb_job_advance_account_id.id or '', 'vat_on': c.sb_job_vat_on,
                'income_accounts': of(('income', 'income_other')),
                'retention_accounts': of(('asset_receivable', 'asset_current', 'asset_non_current')),
                'advance_accounts': of(('liability_current', 'liability_non_current', 'liability_payable')),
                'codes': [{'id': x.id, 'code': x.code, 'name': x.name, 'kind': x.kind} for x in self.env['sb.cost.code'].sudo().search([])]}

    @api.model
    def save_settings(self, vals):
        self._require(manager=True)
        self.env.company.sudo().write({'sb_job_income_account_id': int(vals['income']) if vals.get('income') else False,
                                       'sb_job_retention_account_id': int(vals['retention']) if vals.get('retention') else False,
                                       'sb_job_advance_account_id': int(vals['advance']) if vals.get('advance') else False,
                                       'sb_job_vat_on': vals.get('vat_on') or 'net'})
        return self.settings()

    @api.model
    def save_code(self, vals):
        self._require(manager=True)
        C = self.env['sb.cost.code'].sudo()
        w = {'code': (vals.get('code') or '').strip(), 'name': (vals.get('name') or '').strip(), 'kind': vals.get('kind') or 'other'}
        if not w['code'] or not w['name']:
            raise ValidationError(self.env._('Give the code and the name.'))
        if vals.get('id'):
            C.browse(int(vals['id'])).write(w)
        else:
            C.create(dict(w, company_id=self.env.company.id))
        return self.settings()
