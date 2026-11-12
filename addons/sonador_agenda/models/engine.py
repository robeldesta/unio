"""The agenda: what is due, for the person looking. Nothing is stored here: each source reads the dates its app already
holds, at the moment the agenda opens, so an item goes away by itself when its cause does (the bill is paid, the cheque
deposited, the leave decided). Each source shows only to the roles it concerns, and personal items (your to-dos, your
drafts, findings assigned to you, your own documents) only to you."""
from collections import defaultdict
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

from odoo.addons.sonador_books.models.ethiopian_calendar import fiscal_periods, fiscal_year_of, format_ethiopian, fy_label, gregorian

from .rule import AREAS

ACCOUNTANT = ('sonador_books.group_sb_accountant',)
CLERK = ('sonador_books.group_sb_clerk',)
HR = ('sonador_books.group_sb_hr',)
MANAGER = ('sonador_books.group_sb_manager',)
FLEET = ('sonador_fleet.group_user', 'sonador_fleet.group_manager')
RANK = {'high': 3, 'medium': 2, 'low': 1}


class AgendaEngine(models.AbstractModel):
    _name = 'agenda.engine'
    _description = 'Agenda'

    # ------------------------------------------------------------------ helpers
    def _can(self, groups):
        user = self.env.user
        return any(self.env.ref(g, raise_if_not_found=False) and user.has_group(g) for g in groups)

    def _importance(self, day, today, amount=0.0, urgent_days=3, base='low'):
        days = (day - today).days
        level = 'high' if days <= urgent_days else 'medium' if days <= 7 else base
        big = self.env.company.agenda_min_amount * 10
        if amount and big and abs(amount) >= big and level != 'high':
            level = 'medium' if level == 'low' else 'high'
        return level

    def _item(self, key, area, title, day, today, sub='', amount=0.0, model=None, res_id=None, ids=None, manual=False,
              importance=None, mine=False):
        return {'key': key, 'area': area, 'title': title, 'sub': sub, 'date': fields.Date.to_string(day),
                'ec': '' if gregorian() else format_ethiopian(day), 'amount': amount, 'model': model, 'res_id': res_id,
                'ids': ids or [], 'manual': manual, 'mine': mine, 'overdue': day < today,
                'importance': importance or self._importance(day, today, amount)}

    # ------------------------------------------------------------------ sources
    def _src_tax(self, today, until):
        can_tax, can_hr = self._can(ACCOUNTANT), self._can(HR)
        if not (can_tax or can_hr):
            return []
        out = []
        for rule, period, end, due in self.env['agenda.rule'].sudo()._deadlines(self.env.company, today - timedelta(days=60), until):
            if not (can_tax or rule.area == 'payroll'):
                continue
            if 'VAT' in (rule.name or '') and 'sb_vat_registered' in self.env.company._fields \
                    and not self.env.company.sb_vat_registered:
                continue  # not registered for VAT: no VAT return to file
            if due < rule.create_date.date():  # deadlines before the agenda existed are not chased
                continue
            out.append(self._item('tax:%d:%s' % (rule.id, end), rule.area, '%s · %s' % (rule.name, period), due, today,
                                  sub=rule.note or '', manual=True,
                                  importance='high' if (due - today).days <= 7 else 'medium' if (due - today).days <= 14 else 'low'))
        return out

    def _open_items(self, account_type, sign):
        """Per customer / supplier: the open items on their trade account (the partner's own receivable or payable account,
        or the company's default), never a staff debtor, an advance account or withholding receivable on the same type.
        Money paid out to a person (a CPV to a staff member) is not an invoice to collect."""
        company = self.env.company
        AML = self.env['account.move.line'].sudo()
        lines = AML.search([('company_id', '=', company.id), ('parent_state', '=', 'posted'), ('reconciled', '=', False),
                            ('account_id.account_type', '=', account_type), ('partner_id', '!=', False),
                            ('amount_residual', '!=', 0)])
        prop = 'property_account_receivable_id' if account_type == 'asset_receivable' else 'property_account_payable_id'
        default = self.env['res.partner'].sudo().with_company(company).new({})[prop]
        per = defaultdict(lambda: AML)
        for l in lines:
            partner = l.partner_id.commercial_partner_id
            trade = partner.with_company(company)[prop] | default
            if l.account_id not in trade:
                continue
            per[partner] |= l
        for partner, pl in per.items():
            net = sign * sum(pl.mapped('amount_residual'))
            due = pl.filtered(lambda l: sign * l.amount_residual > 0)
            if net >= company.agenda_min_amount and due:
                yield partner, due, net

    def _doc_kinds(self, lines, kind):
        """'2 invoices open', '1 invoice and 1 other item open': only sales invoices are called invoices (bills for
        suppliers); payments, advances and journal entries are other items."""
        V = self.env['sb.voucher'].sudo() if 'sb.voucher' in self.env else None
        doc_types = {'invoice': ('sale', 'cash_sale'), 'bill': ('bill', 'cash_purchase')}[kind]
        docs = others = 0
        for move in lines.move_id:
            v = V.search([('move_id', '=', move.id)], limit=1) if V is not None else None
            is_doc = (v and v.voucher_type in doc_types) or move.move_type in ('out_invoice', 'in_invoice') or \
                move.journal_id.type == ('sale' if kind == 'invoice' else 'purchase') or \
                (not v and (move.name or '').split('/')[0].split(' ')[0].upper() in ('VSO', 'INV', 'CSI', 'BILL', 'PINV'))
            if is_doc:
                docs += 1
            else:
                others += 1
        parts = []
        if docs:
            parts.append('%d %s%s' % (docs, kind, 's' if docs > 1 else ''))
        if others:
            parts.append('%d other item%s' % (others, 's' if others > 1 else ''))
        return ' and '.join(parts) + ' open'

    def _src_bills(self, today, until):
        if not self._can(ACCOUNTANT):
            return []
        out = []
        for partner, due, net in self._open_items('liability_payable', -1):
            first = min(due.mapped(lambda l: l.date_maturity or l.date))
            if first > until:
                continue
            sub = self._doc_kinds(due, 'bill')
            advances = self.env['account.move.line'].sudo().search_count([
                ('company_id', '=', self.env.company.id), ('parent_state', '=', 'posted'), ('reconciled', '=', False),
                ('account_id.account_type', '=', 'liability_payable'), ('partner_id.commercial_partner_id', '=', partner.id),
                ('amount_residual', '>', 0)])
            if advances:
                sub += ' · less %d advance%s paid' % (advances, 's' if advances > 1 else '')
            out.append(self._item('bill:%d' % partner.id, 'money', 'Pay %s' % partner.name, first, today,
                                  sub=sub, amount=net, model='account.move.line', ids=due.ids))
        return out

    def _src_collect(self, today, until):
        if not self._can(ACCOUNTANT + CLERK):  # a clerk receives the money too: same list, same layout
            return []
        out = []
        oldest = today - timedelta(days=60)  # older balances are the Controller's (overdue customers)
        for partner, due, net in self._open_items('asset_receivable', 1):
            first = min(due.mapped(lambda l: l.date_maturity or l.date))
            if first > until or first < oldest:
                continue
            out.append(self._item('collect:%d' % partner.id, 'money', 'Collect from %s' % partner.name, first, today,
                                  sub=self._doc_kinds(due, 'invoice'), amount=net,
                                  model='account.move.line', ids=due.ids))
        return out

    def _src_cheques(self, today, until):
        if not self._can(ACCOUNTANT + CLERK) or 'sb.voucher.cash' not in self.env:
            return []
        out = []
        rows = self.env['sb.voucher.cash'].sudo().search([('company_id', '=', self.env.company.id), ('cheque_state', '=', 'open'),
                                                           ('voucher_state', '=', 'posted'), ('cheque_date', '<=', until)])
        for r in rows:
            who = r.partner_id.name or ''
            if r.voucher_type in ('receipt', 'cash_sale'):
                title, sub = 'Deposit cheque %s' % (r.reference or ''), 'from %s · %s' % (who, r.cheque_bank or '')
            else:
                title, sub = 'Cheque %s can be cashed' % (r.reference or ''), 'to %s · keep the money in %s' % (who, r.account_id.name or 'the bank')
            out.append(self._item('cheque:%d' % r.id, 'money', title.strip(), r.cheque_date, today, sub=sub.strip(' ·'),
                                  amount=r.amount, model='sb.voucher', res_id=r.voucher_id.id))
        return out

    def _src_exemptions(self, today, until):
        if not self._can(ACCOUNTANT) or 'sb_wht_exempt_until' not in self.env['res.partner']._fields:
            return []
        partners = self.env['res.partner'].sudo().search([('sb_wht_exempt_until', '>=', today - timedelta(days=30)),
                                                          ('sb_wht_exempt_until', '<=', until + timedelta(days=30))])
        return [self._item('exempt:%d' % p.id, 'tax', 'Withholding exemption of %s ends' % p.name, p.sb_wht_exempt_until,
                           today, sub='Ask for the renewed certificate, or withhold from the next payment',
                           model='res.partner', res_id=p.id) for p in partners]

    def _src_payroll(self, today, until):
        if not (self._can(HR) or self._can(ACCOUNTANT)):
            return []
        Run = self.env['sb.payroll.run'].sudo()
        company = self.env.company
        if not Run.search_count([('company_id', '=', company.id)]):
            return []
        out = []
        period = next((p for p in fiscal_periods(fiscal_year_of(today)) if p[2] <= today <= p[3]), None)
        if period and today >= period[3] - timedelta(days=7) \
                and not Run.search_count([('company_id', '=', company.id), ('date_from', '=', period[2])]):
            out.append(self._item('payroll:%s' % period[2], 'payroll', 'Prepare the %s payroll' % period[1], period[3], today,
                                  sub='No payroll yet for this month'))
        for run in Run.search([('company_id', '=', company.id), ('state', '=', 'draft'), ('date', '<=', until)]):
            out.append(self._item('payroll-post:%d' % run.id, 'payroll', 'Post %s' % run.name, run.date, today,
                                  sub='Saved, not posted', amount=run.total_net, model='sb.payroll.run', res_id=run.id))
        for run in Run.search([('company_id', '=', company.id), ('state', '=', 'posted'), ('date', '>=', today - timedelta(days=60))]):
            day = run.pay_date or run.date
            if day <= until:
                out.append(self._item('payroll-pay:%d' % run.id, 'payroll', 'Pay salaries · %s' % run.name, day, today,
                                      sub='Posted, not paid', amount=run.total_net, model='sb.payroll.run', res_id=run.id))
        return out

    def _src_people(self, today, until):
        is_hr = self._can(HR)
        Emp = self.env['hr.employee'].sudo()
        company = self.env.company
        mine = Emp.search([('user_id', '=', self.env.uid), ('company_id', '=', company.id)])
        emps = Emp.search([('company_id', '=', company.id)]) if is_hr else mine
        out = []
        for e in emps:
            own = e in mine
            v = e.current_version_id
            if is_hr and v:
                if v.trial_date_end and today - timedelta(days=7) <= v.trial_date_end <= until:
                    out.append(self._item('probation:%d' % e.id, 'people', 'Probation ends · %s' % e.name, v.trial_date_end, today,
                                          sub='Confirm or end employment before this date', model='hr.employee', res_id=e.id))
                if v.contract_date_end and today - timedelta(days=7) <= v.contract_date_end <= until + timedelta(days=30):
                    out.append(self._item('contract:%d' % e.id, 'people', 'Contract ends · %s' % e.name, v.contract_date_end, today,
                                          sub='Renew it or prepare the settlement', model='hr.employee', res_id=e.id))
            for f, label in (('work_permit_expiration_date', 'Work permit'), ('visa_expire', 'Visa')):
                d = f in e._fields and e[f]
                if d and d <= until + timedelta(days=30):
                    out.append(self._item('%s:%d' % (f, e.id), 'people', '%s expires · %s' % (label, 'yours' if own and not is_hr else e.name),
                                          d, today, model='hr.employee', res_id=e.id, mine=own))
        if 'sb.people.document' in self.env:
            docs = self.env['sb.people.document'].sudo().search([('employee_id', 'in', emps.ids), ('expiry_date', '!=', False),
                                                                ('expiry_date', '<=', until + timedelta(days=30))])
            types = dict(docs._fields['doc_type'].selection)
            for d in docs:
                who = d.employee_id
                out.append(self._item('pdoc:%d' % d.id, 'people', '%s %s · %s' % (types.get(d.doc_type, 'Document'),
                                      'expired' if d.expiry_date < today else 'expires', who.name), d.expiry_date, today,
                                      sub=d.name or '', model='hr.employee', res_id=who.id, mine=who in mine))
        return out

    def _src_leave(self, today, until):
        if 'sb.leave' not in self.env:
            return []
        dom = [('state', '=', 'submitted'), ('company_id', '=', self.env.company.id)]
        if not self._can(HR):
            dom.append(('employee_id.parent_id.user_id', '=', self.env.uid))
        out = []
        for l in self.env['sb.leave'].sudo().search(dom):
            day = min(l.date_from, max(today, l.create_date.date() + timedelta(days=2)))
            out.append(self._item('leave:%d' % l.id, 'people', 'Approve leave · %s' % l.employee_id.name, day, today,
                                  sub='%s · %g day%s from %s' % (l.type_id.name, l.days, '' if l.days == 1 else 's',
                                                               format_ethiopian(l.date_from)),
                                  model='sb.leave', res_id=l.id))
        return out

    def _src_fleet(self, today, until):
        if 'sb.vehicle.doc' not in self.env:
            return []
        officer = self._can(FLEET)
        out = []
        company = self.env.company
        if officer:
            kinds = dict(self.env['sb.vehicle.doc']._fields['kind'].selection)
            Doc = self.env['sb.vehicle.doc'].sudo()
            for d in Doc.search([('company_id', '=', company.id), ('renewed', '=', False), ('date_end', '!=', False),
                                 ('date_end', '<=', until)]):
                if Doc.search_count([('vehicle_id', '=', d.vehicle_id.id), ('kind', '=', d.kind), ('date_end', '>', d.date_end)]):
                    continue  # a newer one of the same kind is on file
                out.append(self._item('vdoc:%d' % d.id, 'fleet', '%s %s · %s' % (kinds.get(d.kind, 'Document'),
                                      'expired' if d.date_end < today else 'expires', d.vehicle_id.display_name), d.date_end,
                                      today, sub=d.number or '', model='sb.vehicle', res_id=d.vehicle_id.id))
            for s in self.env['sb.vehicle.schedule'].sudo().search([('vehicle_id.company_id', '=', company.id),
                                                                    ('every_months', '>', 0), ('last_date', '!=', False)]):
                due = s.last_date + relativedelta(months=s.every_months)
                if due <= until:
                    out.append(self._item('vsched:%d:%s' % (s.id, s.last_date), 'fleet', '%s · %s' % (s.name, s.vehicle_id.display_name),
                                          due, today, sub='Last done %s' % format_ethiopian(s.last_date),
                                          model='sb.vehicle', res_id=s.vehicle_id.id))
        if 'sb_licence_expiry' in self.env['hr.employee']._fields:
            dom = [('company_id', '=', company.id), ('sb_licence_expiry', '!=', False), ('sb_licence_expiry', '<=', until + timedelta(days=30))]
            if not officer:
                dom.append(('user_id', '=', self.env.uid))
            for e in self.env['hr.employee'].sudo().search(dom):
                own = e.user_id.id == self.env.uid
                out.append(self._item('licence:%d' % e.id, 'fleet', 'Driving licence %s · %s' % (
                    'expired' if e.sb_licence_expiry < today else 'expires', 'yours' if own and not officer else e.name),
                    e.sb_licence_expiry, today, model='hr.employee', res_id=e.id, mine=own))
        return out

    def _src_control(self, today, until):
        if 'ctl.finding' not in self.env:
            return []
        Finding = self.env['ctl.finding'].sudo()
        dom = [('company_id', '=', self.env.company.id), ('state', 'in', ('open', 'review'))]
        out = [self._item('finding:%d' % f.id, 'control', f.name, today, today, sub='Assigned to you', amount=f.amount,
                          model='ctl.finding', res_id=f.id, importance=f.severity, mine=True)
               for f in Finding.search(dom + [('user_id', '=', self.env.uid)])]
        if self._can(MANAGER):  # the ones nobody has taken: one line, not one per finding
            free = Finding.search(dom + [('user_id', '=', False)])
            if free:
                high = len(free.filtered(lambda f: f.severity == 'high'))
                out.append(self._item('findings-free', 'control', '%d Controller finding%s nobody has taken' % (
                    len(free), '' if len(free) == 1 else 's'), today, today, sub='%d high · give each to someone' % high,
                    amount=sum(free.mapped('amount')), model='ctl.finding', ids=free.ids,
                    importance='high' if high else 'medium'))
        return out

    def _src_stock(self, today, until):
        """Items below zero in a warehouse: the storekeeper (and the accountant) see one line listing them, until the
        missing receipts or counts are entered."""
        if 'stock.quant' not in self.env:
            return []
        store = self.env.ref('sonador_inventory.group_sb_store_user', raise_if_not_found=False)
        if not (self._can(ACCOUNTANT) or (store and self.env.user.has_group('sonador_inventory.group_sb_store_user'))):
            return []
        quants = self.env['stock.quant'].sudo().search([('company_id', '=', self.env.company.id), ('quantity', '<', 0),
                                                        ('location_id.usage', '=', 'internal')])
        if not quants:
            return []
        worst = quants.sorted('quantity')[:3]
        sub = ', '.join('%s %s' % (q.product_id.display_name, '{:,.0f}'.format(q.quantity)) for q in worst)
        return [self._item('negstock', 'stock', '%d item%s below zero in stock' % (len(quants.product_id),
                           '' if len(quants.product_id) == 1 else 's'), today, today,
                           sub='%s. Enter the missing receipts or a stock count.' % sub, model='stock.quant', ids=quants.ids,
                           importance='high')]

    def _src_close(self, today, until):
        """Month end: the last month that ended and is not locked yet (reconcile, post, lock), and any earlier fiscal year
        still open. For accountants and managers."""
        if not self._can(ACCOUNTANT):
            return []
        company = self.env.company
        lock = company.fiscalyear_lock_date
        out = []
        fy = fiscal_year_of(today)
        ended = [p for p in fiscal_periods(fy - 1) + fiscal_periods(fy) if p[3] < today]
        # only a manager closes a month or a year: an accountant gets the month ready and asks for it
        manager = self.env.user.has_group('account.group_account_manager')
        if ended:
            last = ended[-1]
            if not lock or lock < last[3]:
                out.append(self._item('close:%s' % last[3], 'money', ('Close %s' if manager else 'Get %s ready to close') % last[1],
                                      last[3] + timedelta(days=10), today,
                                      sub='Reconcile the bank and cash accounts, post depreciation, accruals and the payroll, '
                                          'check the trial balance, then ' + (
                                              'close the month (Setup > Accounting Periods)' if manager else
                                              'ask a manager to close the month'),
                                      importance='high' if today > last[3] + timedelta(days=10) else 'medium'))
        has_entries = self.env['account.move'].sudo().search_count
        for year in range(fy - 3, fy):
            periods = fiscal_periods(year)
            start, end = periods[0][2], periods[-1][3]
            if (not lock or lock < end) and has_entries([('company_id', '=', company.id), ('state', '=', 'posted'),
                                                         ('date', '>=', start), ('date', '<=', end)], limit=1):
                out.append(self._item('closeyear:%d' % year, 'money', 'Close the year %s' % fy_label(year), end + timedelta(days=1),
                                      today, sub='The whole year is still open to changes. Finish the year-end entries '
                                                 'and ' + ('close it (Setup > Accounting Periods)' if manager else
                                                          'ask a manager to close it'), importance='high'))
                break
        return out

    def _src_approvals(self, today, until):
        """Purchase orders waiting for approval: to the people who can approve them, and to whoever raised them."""
        if 'purchase.order' not in self.env or 'sb_approver_group_id' not in self.env['purchase.order']._fields:
            return []
        out = []
        for o in self.env['purchase.order'].sudo().search([('company_id', '=', self.env.company.id), ('state', '=', 'to approve')],
                                                           limit=100):
            day = (o.date_order or o.create_date).date()
            mine = o.user_id.id == self.env.uid or o.create_uid.id == self.env.uid
            try:
                can = o._sb_can_approve(self.env.user)
            except Exception:
                can = False
            if can:
                out.append(self._item('approve:%d' % o.id, 'money', 'Approve purchase order %s' % o.name, day, today,
                                      sub='From %s · %s' % (o.create_uid.name, o.partner_id.name), amount=o.amount_total,
                                      model='purchase.order', res_id=o.id, importance='high'))
            elif mine:
                out.append(self._item('waiting:%d' % o.id, 'money', 'Waiting for approval: %s' % o.name, day, today,
                                      sub='%s · to be approved by %s' % (o.partner_id.name, o.sb_approver_group_id.name or ''),
                                      amount=o.amount_total, model='purchase.order', res_id=o.id, importance='low', mine=True))
        return out

    def _src_drafts(self, today, until):
        if 'sb.voucher' not in self.env:
            return []
        # a clerk sees every draft of theirs (their work list); others the ones left more than two days
        clerk_only = self._can(CLERK) and not self._can(ACCOUNTANT)
        old = fields.Datetime.now() + timedelta(minutes=1) if clerk_only else fields.Datetime.now() - timedelta(days=2)
        V = self.env['sb.voucher'].sudo()
        vs = V.search([('company_id', '=', self.env.company.id), ('state', '=', 'draft'),
                       ('create_uid', '=', self.env.uid), ('create_date', '<', old)], limit=50)
        types = dict(self.env['sb.voucher']._fields['voucher_type'].selection)
        out = [self._item('draft:%d' % v.id, 'money', 'Post or delete: %s %s' % (types.get(v.voucher_type, 'Voucher'),
                          v.partner_id.name or ''), v.create_date.date() + timedelta(days=2), today,
                          sub='Saved %s, not posted' % format_ethiopian(v.create_date.date()), amount=v.amount_total,
                          model='sb.voucher', res_id=v.id, mine=True) for v in vs if 'sb_editing' not in v or not v.sb_editing]
        others = V.search([('company_id', '=', self.env.company.id), ('state', '=', 'draft'), ('create_uid', '!=', self.env.uid),
                           ('create_date', '<', fields.Datetime.now() - timedelta(days=2))])
        if others and self._can(CLERK):
            out.append(self._item('drafts-team', 'money', '%d voucher%s saved by others, not posted' % (
                len(others), '' if len(others) == 1 else 's'), today, today,
                sub='Oldest saved %s; post them or ask who saved them' % format_ethiopian(min(others.mapped('create_date')).date()),
                amount=sum(others.mapped('amount_total')), model='sb.voucher', ids=others.ids, importance='low'))
        if 'sb_editing' in V._fields:
            # posted vouchers opened with Edit and left: still in the books as posted, the changes wait
            for v in V.search([('company_id', '=', self.env.company.id), ('sb_editing', '=', True),
                               ('write_uid', '=', self.env.uid)], limit=50):
                out.append(self._item('editing:%d' % v.id, 'money', 'Post or discard your changes: %s %s' % (
                    v.name, v.partner_id.name or ''), v.write_date.date(), today,
                    sub='Opened for editing %s; the posted version stays in the books' % format_ethiopian(v.write_date.date()),
                    amount=v.amount_total, model='sb.voucher', res_id=v.id, mine=True))
        if 'sb.stock.voucher' in self.env:
            Stock = self.env['sb.stock.voucher'].sudo()
            types = dict(Stock._fields['voucher_type'].selection)
            for v in Stock.search([('company_id', '=', self.env.company.id), ('state', '=', 'draft'),
                                   ('create_uid', '=', self.env.uid), ('create_date', '<', old),
                                   ('sb_correction_before', '=', False)], limit=50):
                out.append(self._item('sdraft:%d' % v.id, 'money', 'Post or delete: %s %s' % (
                    types.get(v.voucher_type, 'Voucher'), v.partner_id.name or ''), v.create_date.date() + timedelta(days=2),
                    today, sub='Saved %s, not posted' % format_ethiopian(v.create_date.date()), amount=v.amount_total,
                    model='sb.stock.voucher', res_id=v.id, mine=True))
        if 'sb.stock.voucher' in self.env and self._can(ACCOUNTANT):
            # inventory vouchers reset to draft for a correction: their stock is out of the books, later issues are costed
            # without them, until they are posted again (whoever started the correction)
            for v in self.env['sb.stock.voucher'].sudo().search([('company_id', '=', self.env.company.id), ('state', '=', 'draft'),
                                                                 ('sb_correction_before', '!=', False)], limit=50):
                out.append(self._item('correction:%d' % v.id, 'money', 'Post the correction of %s' % v.name, v.write_date.date(),
                                      today, sub='Its stock is out of the books until it is posted again; later issues are '
                                                 'costed without it', amount=v.amount_total, model='sb.stock.voucher',
                                      res_id=v.id, importance='high'))
        return out

    def _src_todos(self, today, until):
        Todo = self.env['agenda.todo']
        todos = Todo.search([('company_id', '=', self.env.company.id), ('done', '=', False), '|',
                             ('user_id', '=', self.env.uid), ('create_uid', '=', self.env.uid)])
        out = []
        for t in todos:
            mine = t.user_id.id == self.env.uid
            day = t.date or today
            it = self._item('todo:%d' % t.id, 'todo', t.name, day, today,
                            sub=(t.note or '') if mine else 'With %s' % t.user_id.name, model=t.res_model or None,
                            res_id=t.res_id or None, importance=t.priority if mine else 'low', mine=mine)
            it.update(todo_id=t.id, overdue=bool(t.date) and t.date < today)
            out.append(it)
        return out

    SOURCES = ('tax', 'bills', 'collect', 'cheques', 'exemptions', 'payroll', 'people', 'leave', 'fleet', 'control',
               'close', 'approvals', 'stock', 'drafts', 'todos')
    # who sees all of a source (and so can tell that an item made into a to-do is over); key prefix -> (source, roles)
    FAMILY = {'tax': ('tax', ACCOUNTANT), 'bill': ('bills', ACCOUNTANT), 'collect': ('collect', ACCOUNTANT + CLERK),
              'cheque': ('cheques', ACCOUNTANT + CLERK), 'exempt': ('exemptions', ACCOUNTANT), 'payroll': ('payroll', HR + ACCOUNTANT),
              'payroll-post': ('payroll', HR + ACCOUNTANT), 'payroll-pay': ('payroll', HR + ACCOUNTANT),
              'probation': ('people', HR), 'contract': ('people', HR), 'pdoc': ('people', HR),
              'work_permit_expiration_date': ('people', HR), 'visa_expire': ('people', HR), 'leave': ('leave', HR),
              'vdoc': ('fleet', FLEET), 'vsched': ('fleet', FLEET), 'licence': ('fleet', FLEET),
              'finding': ('control', MANAGER), 'draft': ('drafts', None), 'correction': ('drafts', ACCOUNTANT),
              'sdraft': ('drafts', None), 'drafts-team': ('drafts', CLERK), 'close': ('close', ACCOUNTANT), 'closeyear': ('close', ACCOUNTANT),
              'approve': ('approvals', None), 'waiting': ('approvals', None), 'negstock': ('stock', ACCOUNTANT)}

    # ------------------------------------------------------------------ the agenda
    @api.model
    def agenda(self):
        today = fields.Date.context_today(self)
        company = self.env.company
        until = today + timedelta(days=company.agenda_horizon or 30)
        done, later = self.env['agenda.mark']._active(company)
        linked = self.env['agenda.todo'].sudo().search([('company_id', '=', company.id), ('done', '=', False),
                                                        ('source_key', '!=', False)])
        raw = {}
        for src in self.SOURCES[:-1]:
            for it in getattr(self, '_src_%s' % src)(today, until):
                raw[it['key']] = it
        # a to-do made from an item is over when the item is (bill paid, leave decided, deadline marked filed),
        # judged by someone who sees the whole source
        for t in linked:
            fam = self.FAMILY.get(t.source_key.split(':')[0])
            if fam and t.source_key not in raw and (fam[1] is None and t.user_id.id == self.env.uid or fam[1] and self._can(fam[1])):
                t.write({'done': True, 'note': ((t.note or '') + '\nDone: it went away in its own app.').strip()})
        todo_keys = set(linked.filtered(lambda t: not t.done).mapped('source_key'))
        items = [it for k, it in raw.items() if k not in done and k not in later and k not in todo_keys]
        items += self._src_todos(today, until)
        items.sort(key=lambda i: (i['date'], -RANK[i['importance']], -abs(i['amount'] or 0)))
        names = dict(AREAS)
        areas = [{'code': a, 'name': names[a], 'count': sum(1 for i in items if i['area'] == a)}
                 for a in names if any(i['area'] == a for i in items)]
        users = self.env['res.users'].search([('share', '=', False), ('company_ids', 'in', company.id)])
        return {'today': fields.Date.to_string(today), 'today_ec': '' if gregorian() else format_ethiopian(today),
                'currency': company.currency_id.symbol or company.currency_id.name, 'items': items, 'areas': areas,
                'users': [{'id': u.id, 'name': u.name} for u in users], 'uid': self.env.uid}

    @api.model
    def mark_done(self, key):
        if not key.startswith('tax:'):
            raise UserError(self.env._('This goes away by itself once it is done in its own app.'))
        self.env['agenda.mark'].sudo().create({'key': key, 'kind': 'done', 'user_id': self.env.uid})
        return self.agenda()

    @api.model
    def later(self, key, days):
        until = fields.Date.context_today(self) + timedelta(days=max(int(days), 1))
        Mark = self.env['agenda.mark'].sudo()
        Mark.search([('key', '=', key), ('kind', '=', 'later'), ('user_id', '=', self.env.uid)]).unlink()
        Mark.create({'key': key, 'kind': 'later', 'until': until, 'user_id': self.env.uid})
        return self.agenda()

    @api.model
    def add_todo(self, vals):
        clean = {k: vals[k] for k in ('name', 'date', 'user_id', 'priority', 'note', 'source_key', 'res_model', 'res_id')
                 if vals.get(k)}
        if not (clean.get('name') or '').strip():
            raise UserError(self.env._('Write what has to be done.'))
        self.env['agenda.todo'].create(clean)
        return self.agenda()

    @api.model
    def todo_done(self, todo_id):
        todo = self.env['agenda.todo'].browse(int(todo_id))
        todo.write({'done': True})
        if (todo.source_key or '').startswith('tax:'):  # a deadline handed to someone: done for everyone
            self.env['agenda.mark'].sudo().create({'key': todo.source_key, 'kind': 'done', 'user_id': self.env.uid,
                                                   'company_id': todo.company_id.id})
        return self.agenda()
