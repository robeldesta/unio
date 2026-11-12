"""Leaving and clearance screen: start a leaving, clear each line, HR and the general manager approve; items held."""
from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.sonador_payroll.models.settlement import REASONS

from .exit import AREAS, KINDS, STATES

OPEN = ('draft', 'clearance', 'hr', 'gm')


class SbExitService(models.AbstractModel):
    _name = 'sb.exit.service'
    _description = 'Leaving and clearance'

    def _P(self):
        return self.env['sb.people']

    def _hr(self):
        return self._P()._is_hr()

    def _me(self):
        return self._P()._me()

    def _head(self, company, *names):
        D = self.env['hr.department'].sudo()
        for n in names:
            d = D.search([('name', 'ilike', n), ('company_id', 'in', (company.id, False))], limit=1)
            if d.manager_id.user_id:
                return d.manager_id.user_id
        return self.env['res.users']

    def _approver(self, company, area):
        c = company.sudo()
        if area == 'vehicle':
            return c.sb_exit_vehicle_uid or self._head(c, 'logistic', 'procurement', 'fleet')
        if area == 'it':
            return c.sb_exit_it_uid or self._head(c, 'ICT', 'information', ' IT')
        if area == 'finance':
            return c.sb_exit_finance_uid or self._head(c, 'finance', 'account')
        return self.env['res.users']  # other items: HR clears them

    def _gm(self, company):
        return company.sudo().sb_exit_gm_uid or self._head(company, 'administration', 'management', 'general')

    def _hr_users(self, company):
        return self.env.ref('sonador_books.group_sb_hr').sudo().all_user_ids.filtered(
            lambda u: company in u.company_ids and u.active and not u.share)

    # ------------------------------------------------------------------ what the person holds and owes
    def _clearance_lines(self, x):
        e, c = x.employee_id.sudo(), x.company_id
        lines, seq = [], 0

        def add(area, name, detail='', ref=None):
            nonlocal seq
            seq += 10
            lines.append({'area': area, 'name': name, 'detail': detail, 'sequence': seq, 'approver_id': self._approver(c, area).id or False,
                          'ref_model': ref._name if ref else False, 'ref_id': ref.id if ref else 0})
        for i in e.sb_item_ids.filtered(lambda i: not i.returned_on):
            add(i.area, '%s: %s' % (dict(KINDS)[i.kind], i.name), ' · '.join(filter(None, [i.serial, i.note])), i)
        if 'sb.vehicle' in self.env:
            for v in self.env['sb.vehicle'].sudo().search([('driver_id', '=', e.id)]):
                if not any(l['ref_model'] == 'sb.vehicle' and l['ref_id'] == v.id for l in lines):
                    add('vehicle', 'Vehicle: %s' % v.name, 'Assigned driver: hand over the vehicle, keys and papers', v)
        if 'sb.asset' in self.env and 'custodian_id' in self.env['sb.asset']._fields:
            for a in self.env['sb.asset'].sudo().search([('custodian_id', '=', e.id), ('state', 'not in', ('disposed', 'expensed'))]):
                cat = (a.category_id.name or '').lower()
                area = 'vehicle' if any(w in cat for w in ('vehicle', 'motor', 'car')) else 'it' if any(
                    w in cat for w in ('computer', 'it ', 'phone', 'laptop', 'electronic')) else 'other'
                add(area, 'Fixed asset: %s %s' % (a.code or '', a.name), a.serial or '', a)
        for area in ('vehicle', 'it'):
            if not any(l['area'] == area for l in lines):
                add(area, 'Confirm %s holds no %s' % (e.name, 'company vehicle' if area == 'vehicle' else 'laptop, computer, phone or SIM'))
        owed, _due = self._owed(e, c)
        add('finance', 'Money owed to the company', '; '.join(owed) or 'Nothing found in the books: confirm (staff debtor, van sales, cash)')
        return lines

    def _owed(self, e, c):
        """What the person still owes the company: their staff debtor account, loans and advances not paid back, and any
        customer balance on their name. Finance clears only when all of it is nil."""
        owed, due = [], 0.0
        ML = self.env['account.move.line'].sudo()
        acc = e.sb_staff_account_id if 'sb_staff_account_id' in e._fields else False
        if acc:
            bal = sum(ML.search([('account_id', '=', acc.id), ('parent_state', '=', 'posted'), ('company_id', '=', c.id)]).mapped('balance'))
            if bal > 0.5:
                owed.append('staff debtor %s: %s' % (acc.display_name, '{:,.2f}'.format(bal)))
                due = max(due, bal)
        loans = self.env['sb.payroll.loan'].sudo().search([('employee_id', '=', e.id), ('state', 'in', ('open', 'hold'))])
        left = sum(loans.mapped('remaining'))
        if left > 0.5:
            owed.append('loans and advances not paid back: %s' % '{:,.2f}'.format(left))
            due = max(due, left)
        partner = e.work_contact_id.commercial_partner_id if e.work_contact_id else False
        if partner:
            dom = [('partner_id', 'child_of', partner.id), ('parent_state', '=', 'posted'), ('company_id', '=', c.id),
                   ('account_id.account_type', 'in', ('asset_receivable', 'asset_current')), ('reconciled', '=', False)]
            if acc:
                dom.append(('account_id', '!=', acc.id))
            bal = sum(ML.search(dom).mapped('amount_residual'))
            if bal > 0.5:
                owed.append('balance on their name: %s' % '{:,.2f}'.format(bal))
                due += bal
        return owed, due

    # ------------------------------------------------------------------ screen
    def _line(self, l):
        me = self.env.user
        return {'id': l.id, 'area': l.area, 'area_label': dict(AREAS)[l.area], 'name': l.name, 'detail': l.detail or '',
                'approver': l.approver_id.name or 'HR', 'state': l.state, 'note': l.note or '', 'amount': l.amount_due,
                'by': l.decided_by.name or '', 'can': l.exit_id.state == 'clearance' and (l.approver_id == me or self._hr())}

    def _dict(self, x, full=False):
        lines = x.line_ids
        d = {'id': x.id, 'name': x.employee_id.name, 'employee_id': x.employee_id.id, 'job': x.employee_id.job_title or '',
             'department': x.employee_id.department_id.name or '', 'reason': x.reason, 'reason_label': dict(REASONS).get(x.reason),
             'last_day': x.last_day.strftime('%d %b %Y'), 'state': x.state, 'state_label': dict(STATES)[x.state], 'note': x.note or '',
             'cleared': len(lines.filtered(lambda l: l.state == 'cleared')), 'total': len(lines),
             'issues': len(lines.filtered(lambda l: l.state == 'issue')), 'owed': sum(lines.mapped('amount_due')),
             'mine': x.employee_id == self._me(), 'waiting_me': bool(lines.filtered(
                 lambda l: l.state != 'cleared' and l.approver_id == self.env.user)) and x.state == 'clearance'}
        if full:
            me = self.env.user
            d.update(lines=[self._line(l) for l in lines], hr_by=x.hr_by.name or '', gm_by=x.gm_by.name or '',
                     pay_months=x.sudo().pay_months if self._hr() or me == self._gm(x.company_id) else 0.0,
                     can_send=x.state == 'draft' and (self._hr() or d['mine']),
                     can_hr=x.state == 'hr' and self._hr() and not d['mine'],
                     can_gm=x.state == 'gm' and (me == self._gm(x.company_id) or (not self._gm(x.company_id) and self._hr())),
                     can_cancel=x.state in OPEN and (self._hr() or (d['mine'] and x.state == 'draft')),
                     gm=self._gm(x.company_id).name or '')
        return d

    @api.model
    def home(self):
        X = self.env['sb.exit'].sudo()
        c = self.env.company
        me = self.env.user
        rows = X.search([('company_id', '=', c.id)], limit=200)
        if not self._hr():
            gm = me == self._gm(c)
            rows = rows.filtered(lambda x: x.employee_id.user_id == me or me in x.line_ids.approver_id
                                 or (gm and x.state in ('gm', 'done', 'left')))
        return {'hr': self._hr(), 'reasons': REASONS, 'kinds': KINDS, 'rows': [self._dict(x) for x in rows],
                'people': [{'id': e.id, 'name': e.name} for e in self.env['hr.employee'].sudo().search(
                    [('company_id', '=', c.id), ('active', '=', True)], order='name')] if self._hr() else [],
                'me': self._me().id or False}

    def _get(self, exit_id):
        x = self.env['sb.exit'].sudo().browse(int(exit_id)).exists()
        me = self.env.user
        if not x or not (self._hr() or x.employee_id.user_id == me or me in x.line_ids.approver_id or me == self._gm(x.company_id)):
            raise AccessError(self.env._('This leaving is not yours to see.'))
        return x

    @api.model
    def get(self, exit_id):
        return self._dict(self._get(exit_id), full=True)

    @api.model
    def start(self, vals):
        """HR for anyone, or the employee for themself (resignation)."""
        e = self.env['hr.employee'].sudo().browse(int(vals.get('employee_id') or self._me().id))
        if not e or (e != self._me() and not self._hr()):
            raise AccessError(self.env._('HR starts a leaving for someone else.'))
        if not vals.get('last_day'):
            raise UserError(self.env._('Enter the last working day.'))
        if self.env['sb.exit'].sudo().search_count([('employee_id', '=', e.id), ('state', 'in', OPEN)]):
            raise UserError(self.env._('%s already has a leaving under way.', e.name))
        x = self.env['sb.exit'].sudo().create({'employee_id': e.id, 'company_id': e.company_id.id, 'last_day': vals['last_day'],
                                              'reason': vals.get('reason') or 'resign', 'note': vals.get('note') or False})
        if vals.get('send'):
            return self.act(x.id, 'send')
        return self._dict(x, full=True)

    def _post(self, x, text, users):
        users = users - self.env.user
        x.message_post(body=Markup('<p>%s</p>') % text, partner_ids=users.partner_id.ids, message_type='comment',
                       subtype_xmlid='mail.mt_comment')

    @api.model
    def act(self, exit_id, action, note='', pay_months=None):
        x = self._get(exit_id)
        me = self.env.user
        if action == 'send':
            if x.state != 'draft':
                raise UserError(self.env._('Already sent.'))
            x.line_ids.unlink()
            x.write({'state': 'clearance', 'line_ids': [(0, 0, v) for v in self._clearance_lines(x)]})
            self._post(x, '%s is leaving on %s (%s). Please clear your part.' % (x.employee_id.name, x.last_day.strftime('%d %b %Y'),
                                                                                 dict(REASONS)[x.reason].lower()),
                       x.line_ids.approver_id | self._hr_users(x.company_id))
        elif action == 'hr':
            if x.state != 'hr' or not self._hr():
                raise AccessError(self.env._('HR approves after the clearance.'))
            months = max(float(pay_months or 0.0), 0.0)
            if months > 36:
                raise UserError(self.env._('The separation payment is in months of salary (e.g. 2).'))
            x.sudo().write({'state': 'gm', 'hr_by': me.id, 'hr_on': fields.Datetime.now(), 'pay_months': months})
            if months:
                self._post(x, 'HR authorised a separation payment of %s months of salary, paid with the payroll of the last day.'
                           % ('%g' % months), self._gm(x.company_id))
            self._post(x, 'HR approved the leaving of %s. It waits for the general manager.' % x.employee_id.name, self._gm(x.company_id))
        elif action == 'gm':
            if x.state != 'gm' or not (me == self._gm(x.company_id) or (not self._gm(x.company_id) and self._hr())):
                raise AccessError(self.env._('The general manager gives the last approval.'))
            x.write({'state': 'done', 'gm_by': me.id, 'gm_on': fields.Datetime.now()})
            x.employee_id.sudo().write({'sb_pay_leave_after': True, 'sb_exit_last_day': x.last_day})
            self._post(x, 'Approved: %s is paid up to %s and leaves when that payroll is posted.' % (
                x.employee_id.name, x.last_day.strftime('%d %b %Y')), self._hr_users(x.company_id) | x.employee_id.user_id)
        elif action == 'back':
            if x.state not in ('hr', 'gm') or not (self._hr() or me == self._gm(x.company_id)):
                raise AccessError(self.env._('Only HR or the general manager send it back.'))
            x.state = 'clearance'
            self._post(x, 'Sent back to clearance: %s' % (note or ''), x.line_ids.approver_id | self._hr_users(x.company_id))
        elif action == 'cancel':
            if x.state not in OPEN:
                raise UserError(self.env._('It can no longer be cancelled.'))
            x.state = 'cancelled'
            x.employee_id.sudo().write({'sb_pay_leave_after': False, 'sb_exit_last_day': False})
            self._post(x, 'Cancelled: %s stays.%s' % (x.employee_id.name, (' ' + note) if note else ''), x.line_ids.approver_id)
        else:
            raise UserError(self.env._('Unknown action.'))
        self.env['sb.exit']._sb_notify(x, action)
        return self._dict(x, full=True)

    @api.model
    def clear(self, line_id, ok, note='', amount=0.0):
        l = self.env['sb.exit.line'].sudo().browse(int(line_id)).exists()
        x = l.exit_id
        me = self.env.user
        if not l or x.state != 'clearance' or not (l.approver_id == me or self._hr()):
            raise AccessError(self.env._('Only %s or HR clears this.', l.approver_id.name or 'HR'))
        if not ok and not (note or '').strip():
            raise UserError(self.env._('Say what is missing or owed.'))
        if ok and l.area == 'finance':
            owed, due = self._owed(x.employee_id.sudo(), x.company_id)
            if due > 0.5:
                l.write({'detail': '; '.join(owed)})
                raise UserError(self.env._(
                    '%(e)s still owes the company: %(o)s. Finance clears once it is nil: paid in (a receipt, CRV, on the '
                    'staff debtor account) or deducted from the final pay.', e=x.employee_id.name, o='; '.join(owed)))
        l.write({'state': 'cleared' if ok else 'issue', 'note': (note or '').strip(), 'amount_due': float(amount or 0.0),
                 'decided_by': me.id, 'decided_on': fields.Datetime.now()})
        if all(r.state == 'cleared' for r in x.line_ids):
            x.state = 'hr'
            self._post(x, 'Everything is cleared for %s. HR, please approve.' % x.employee_id.name, self._hr_users(x.company_id))
            self.env['sb.exit']._sb_notify(x, 'cleared')
        return self._dict(x, full=True)

    # ------------------------------------------------------------------ items held
    @api.model
    def items(self, employee_id):
        if not self._hr() and not self.env.user.has_group('sonador_books.group_sb_accountant'):
            raise AccessError(self.env._('HR keeps the list of items people hold.'))
        e = self.env['hr.employee'].sudo().browse(int(employee_id))
        return {'employee': e.name, 'items': [{'id': i.id, 'kind': i.kind, 'kind_label': dict(KINDS)[i.kind], 'name': i.name,
                                               'serial': i.serial or '', 'issued': str(i.issued_on or ''), 'returned': str(i.returned_on or ''),
                                               'note': i.note or ''} for i in e.sb_item_ids]}

    @api.model
    def save_item(self, employee_id, vals):
        if not self._hr():
            raise AccessError(self.env._('HR keeps the list of items people hold.'))
        I = self.env['sb.people.item'].sudo()
        data = {k: vals.get(k) or False for k in ('kind', 'name', 'serial', 'issued_on', 'returned_on', 'note')}
        if not data['name']:
            raise UserError(self.env._('Name the item.'))
        if vals.get('id'):
            I.browse(int(vals['id'])).write(data)
        else:
            I.create(dict(data, employee_id=int(employee_id)))
        return self.items(employee_id)


class SbExit(models.Model):
    _inherit = 'sb.exit'

    @api.model
    def _sb_notify(self, x, event):
        """Hook for other channels (e.g. Telegram)."""
        return True
