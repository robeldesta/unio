"""Me: everything about the person logged in, and one inbox of what waits for their decision.

Each app keeps its own rules (who may approve what); this only gathers. A provider is used only when its app is
installed, so the screen fits whatever the company runs."""
from odoo import api, fields, models
from odoo.exceptions import UserError

from .confirm import ConfirmationRequired


def _s(d):
    return fields.Date.to_string(d) if d else False


def _dt(d):
    if not d:
        return False
    return d if isinstance(d, str) else fields.Datetime.to_string(d)


def _day(d):
    """The day as everyone reads it on screen: 14 Jul 2026."""
    if not d:
        return ''
    return fields.Datetime.to_datetime(d).strftime('%d %b %Y').lstrip('0')


def _age(d):
    """Whole days something has been waiting."""
    if not d:
        return 0
    return max(0, (fields.Datetime.now() - fields.Datetime.to_datetime(d)).days)


class SbMe(models.AbstractModel):
    _name = 'sb.me'
    _description = 'Me (self service)'

    def _has(self, model):
        return model in self.env

    def _me(self):
        return self.env['sb.people']._me()

    # ------------------------------------------------------------------ the page
    @api.model
    def data(self):
        P = self.env['sb.people']
        me = self._me()
        out = {'user': {'name': self.env.user.name, 'avatar': '/web/image/res.users/%d/avatar_128' % self.env.user.id},
               'sign': bool(self.env.company.sb_confirm_on),
               'employee': P._card(me) if me else False, 'today': _s(fields.Date.context_today(self)),
               'apps': {k: self._has(m) for k, m in (('timeoff', 'sb.leave.service'), ('attendance', 'sb.att.service'),
                                                      ('payroll', 'sb.payroll.service'), ('performance', 'sb.perf'),
                                                      ('planning', 'sb.planning'), ('onboarding', 'sb.onboarding'))}}
        if me:
            out['profile'] = self._profile(me)
        out['approvals'] = self.approvals()
        out['todo'] = self._todo()
        if me and out['apps']['timeoff']:
            out['timeoff'] = self._safe(lambda: self._timeoff())
        if me and out['apps']['attendance']:
            out['clock'] = self._safe(lambda: self.env['sb.att.service'].my_clock())
        if me and out['apps']['payroll']:
            out['payslips'] = self._safe(lambda: self._payslips(me))
        if me and out['apps']['performance']:
            out['goals'] = self._safe(lambda: [g for g in self.env['sb.perf'].goals(scope='mine')['goals']][:6])
        if me and out['apps']['planning']:
            out['week'] = self._safe(lambda: self._week())
        out['sections'] = self.sections()
        out['setup'] = self._safe(lambda: self._setup()) or False
        out['can_add_me'] = not me and self._setup_admin()
        out['notices'] = self._safe(lambda: self.env['sb.me.notice']._mine()) or []
        return out

    # ------------------------------------------------------------------ sections other apps add to Me
    # A module adds a card to Me in two steps:
    #   Python: inherit sb.me, override _me_sections() and append
    #           {'key': 'usage', 'title': 'My usage', 'icon': 'fa-tachometer', 'sequence': 40, 'wide': False, 'data': {...}}
    #   JS:     registry.category("sonador_me.sections").add('usage', MyCardComponent)   (props: data, reload)
    # A section whose data fails to load is left out; the rest of Me still opens.
    def _me_sections(self):
        return []

    @api.model
    def sections(self):
        out = []
        for s in self._safe(lambda: self._me_sections()) or []:
            if callable(s.get('data')):
                s = dict(s, data=self._safe(s['data']))
                if s['data'] is False:
                    continue
            out.append(s)
        return sorted(out, key=lambda s: s.get('sequence', 50))

    def _safe(self, fn):
        try:
            with self.env.cr.savepoint():
                return fn()
        except Exception:
            return False

    def _profile(self, e):
        e = e.sudo()
        v = e.current_version_id if 'current_version_id' in e._fields else e
        return {'start': _s(getattr(v, 'date_start', False) or getattr(e, 'first_contract_date', False)),
                'work_email': e.work_email or '', 'phone': e.mobile_phone or e.work_phone or '',
                'private_phone': e.private_phone or '' if 'private_phone' in e._fields else '',
                'birthday': _s(e.birthday) if 'birthday' in e._fields else False,
                'contacts': len(self.env['sb.people.contact'].sudo().search([('employee_id', '=', e.id)])) if self._has('sb.people.contact') else 0,
                'documents': len(self.env['sb.people.document'].sudo().search([('employee_id', '=', e.id)])) if self._has('sb.people.document') else 0}

    def _timeoff(self):
        S = self.env['sb.leave.service']
        ov = S.overview()
        ctx = S.ctx()
        return {'annual': ov.get('annual'), 'sick': ov.get('sick'), 'leaves': (ov.get('leaves') or [])[:6],
                'types': [t for t in ctx['types'] if t['mine']]}

    def _payslips(self, me):
        L = self.env['sb.payroll.line'].sudo()
        lines = L.search([('hr_employee_id', '=', me.id), ('run_id.state', 'in', ('posted', 'paid'))], order='date desc', limit=12)
        year = fields.Date.context_today(self).year
        ytd = lines.filtered(lambda l: l.date and l.date.year == year)
        return {'lines': [{'id': l.id, 'run': (l.run_id.name or '').replace(' (Odoo 18)', '').strip(), 'date': _s(l.date), 'gross': l.gross, 'net': l.net, 'tax': l.income_tax,
                           'url': '/sonador_payroll/payslip/%d' % l.id} for l in lines],
                'ytd': {'gross': sum(ytd.mapped('gross')), 'net': sum(ytd.mapped('net')), 'tax': sum(ytd.mapped('income_tax'))},
                'currency': self.env.company.currency_id.symbol or self.env.company.currency_id.name}

    def _week(self):
        w = self.env['sb.planning'].week()
        return {'label': w['label'], 'strip': w['strip'][:8], 'score': w.get('score'), 'plan_missing': w.get('plan_missing'),
                'selected_ec': w.get('selected_ec')}

    # ------------------------------------------------------------------ to do (things for me to do, not decide)
    def _todo(self):
        rows = []
        if self._has('sb.onboarding'):
            for t in self._safe(lambda: self.env['sb.onboarding'].my_tasks()) or []:
                rows.append({'key': 'journey:%d' % t['id'], 'icon': 'fa-check-square-o', 'title': t['name'],
                             'sub': '%s · %s' % (t['employee']['name'], t['role_label'] or ''), 'due': t['due_ec'], 'overdue': t['overdue'],
                             'done': 'journey'})
            pol = self._safe(lambda: self.env['sb.onboarding'].policies()) or {}
            for p in pol.get('policies', []):
                if pol.get('me') and not p['acked']:
                    rows.append({'key': 'policy:%d' % p['id'], 'icon': 'fa-file-text-o', 'title': 'Read and accept: %s' % p['name'],
                                 'sub': 'Version %s' % p['version'], 'due': '', 'overdue': False, 'done': 'policy', 'body': p['body']})
        if self._has('sb.perf.review') and self._me():
            for r in self.env['sb.perf.review'].sudo().search([('reviewer_id', '=', self._me().id), ('state', '=', 'draft'),
                                                               ('cycle_id.state', '=', 'open')], limit=30):
                rows.append({'key': 'review:%d' % r.id, 'icon': 'fa-star-half-o',
                             'title': ('Your self-review' if r.role == 'self' else 'Review %s' % r.employee_id.name),
                             'sub': r.cycle_id.name, 'due': '', 'overdue': False, 'open': 'sonador_performance.app'})
        acts = self.env['mail.activity'].search([('user_id', '=', self.env.uid), ('date_deadline', '<=', fields.Date.add(fields.Date.context_today(self), days=7))],
                                                order='date_deadline', limit=30)
        for a in acts:
            rows.append({'key': 'activity:%d' % a.id, 'icon': 'fa-clock-o', 'title': a.summary or a.activity_type_id.name or 'To do',
                         'sub': '%s · %s' % (a.res_name or '', self.env['ir.model']._get(a.res_model).name if a.res_model else ''),
                         'due': _s(a.date_deadline), 'overdue': a.date_deadline < fields.Date.context_today(self), 'done': 'activity',
                         'record': {'model': a.res_model, 'id': a.res_id}})
        return rows

    @api.model
    def todo_done(self, key):
        kind, rid = key.split(':')
        rid = int(rid)
        if kind == 'journey':
            self.env['sb.onboarding'].task_done(rid, True)
        elif kind == 'policy':
            self.env['sb.onboarding'].acknowledge(rid)
        elif kind == 'activity':
            self.env['mail.activity'].browse(rid).action_feedback()
        return self.data()

    # ------------------------------------------------------------------ approvals: one inbox
    # Each source of approvals is a "kind" in a registry other modules extend (override _approval_kinds and call super).
    # A kind implements, on sb.me:
    #   _ap_<kind>_pending()                  -> [item]  what waits for my decision (the app decides who may decide)
    #   _ap_<kind>_decide(rid, approve, note) -> None    approve or send back (note = the reason)
    #   _ap_<kind>_mine()                     -> [item]  optional: what I submitted that still waits, and for whom
    #   _ap_<kind>_open(rid)                  -> action  optional: the document (default: its form, or the app)
    #   _ap_<kind>_next(rid)                  -> str     optional: after an approval, who it waits for now (False: done)
    # An item: key "<kind>:<id>", app, icon, title, details [str], amount, currency, avatar, since (datetime), model,
    # res_id, submitter_uid; in "mine" also waiting_for (str).
    def _approval_kinds(self):
        kinds = []
        for kind, model in (('leave', 'sb.leave.service'), ('overtime', 'sb.att.service'), ('plan', 'sb.planning'),
                            ('po', 'purchase.order'), ('fuel', 'sb.fleet'), ('perdiem', 'sb.fleet'), ('hours', 'sb.projects'),
                            ('gate', 'sb.gate.pass'), ('store', 'sb.stock.voucher'), ('autobill', 'sb.voucher'),
                            ('loan', 'sb.payroll.loan')):
            if self._has(model):
                kinds.append(kind)
        return kinds

    def _ap(self, kind, what):
        return getattr(self, '_ap_%s_%s' % (kind, what), None)

    @api.model
    def approvals(self):
        items = []
        for kind in self._approval_kinds():
            fn = self._ap(kind, 'pending')
            for it in (self._safe(fn) if fn else None) or []:
                it.setdefault('kind', kind)
                it.setdefault('note_on_refuse', True)
                it.setdefault('refuse_label', 'Send back')
                it['since'] = _dt(it.get('since'))
                it['days'] = _age(it.get('since'))
                it['since_day'] = _day(it.get('since'))
                items.append(it)
        items.sort(key=lambda i: i.get('since') or '', reverse=True)  # newest first
        return items

    @api.model
    def submitted(self):
        items = []
        for kind in self._approval_kinds():
            fn = self._ap(kind, 'mine')
            for it in (self._safe(fn) if fn else None) or []:
                it.setdefault('kind', kind)
                it['since'] = _dt(it.get('since'))
                it['days'] = _age(it.get('since'))
                it['since_day'] = _day(it.get('since'))
                items.append(it)
        items.sort(key=lambda i: i.get('since') or '', reverse=True)  # newest first
        return items

    @api.model
    def history(self, limit=60):
        Log = self.env['sb.approval.log'].sudo()
        row = lambda l: {'id': l.id, 'key': l.key, 'app': l.app, 'title': l.title, 'decision': l.decision, 'note': l.note or '',
                         'date': _dt(l.date), 'by': l.user_id.name, 'channel': l.channel, 'model': l.model, 'res_id': l.res_id}
        return {'decided': [row(l) for l in Log.search([('user_id', '=', self.env.uid)], limit=limit)],
                'mine': [row(l) for l in Log.search([('submitter_id', '=', self.env.uid), ('user_id', '!=', self.env.uid)], limit=30)]}

    @api.model
    def approval_center(self):
        return {'approvals': self.approvals(), 'submitted': self.submitted(), 'history': self.history()}

    def _find(self, key):
        kind = key.split(':')[0]
        if kind not in self._approval_kinds():
            raise UserError(self.env._('This kind of approval is not installed.'))
        fn = self._ap(kind, 'pending')
        for it in (fn() if fn else []):
            if it['key'] == key:
                it.setdefault('kind', kind)
                return it
        raise UserError(self.env._('This item no longer waits for your decision: someone may have decided it already.'))

    @api.model
    def decide(self, key, approve, note='', channel='me'):
        """Approve or send back one item. Only what is in my own list can be decided here; the app checks its rules again."""
        note = (note or '').strip()
        if not approve and not note:
            raise UserError(self.env._('Say why it goes back: the person who sent it will see your reason.'))
        it = self._find(key)
        rid = key.split(':', 1)[1]
        self._ap(it['kind'], 'decide')(rid, bool(approve), note)
        nxt = self._ap(it['kind'], 'next')
        it['next_step'] = (nxt(rid) or False) if (nxt and approve) else False
        self._log_decision(it, approve, note, channel)
        steps = 1
        while approve and steps < 5:
            # the next step waits for me as well (one person holding both roles): decided by the same click, unless it
            # asks its own signed confirmation, which keeps it in my list for that
            try:
                again = self._find(key)
            except UserError:
                break
            try:
                with self.env.cr.savepoint():
                    self._ap(again['kind'], 'decide')(rid, True, '')
            except ConfirmationRequired:
                break
            again['next_step'] = (nxt(rid) or False) if nxt else False
            self._log_decision(again, True, '', channel)
            steps += 1
        return {'approvals': self.approvals(), 'steps': steps}

    def _log_decision(self, it, approve, note, channel):
        log = self.env['sb.approval.log'].sudo().create({
            'user_id': self.env.uid, 'key': it['key'], 'kind': it['kind'], 'app': it.get('app'), 'title': it.get('title'),
            'model': it.get('model') or False, 'res_id': it.get('res_id') or 0, 'decision': 'approved' if approve else 'sent_back',
            'note': note or False, 'channel': channel, 'submitter_id': it.get('submitter_uid') or False,
            'amount': it.get('amount') or 0.0, 'next_step': it.get('next_step') or False})
        self.env['sb.notify.rule'].sudo()._event_decided(log)
        return log

    @api.model
    def open_doc(self, key):
        kind, rid = key.split(':', 1)
        fn = self._ap(kind, 'open')
        if fn:
            return fn(rid)
        return False

    def _form(self, model, rid, fallback=None):
        """The document's own form when it has one, else the app it lives in."""
        if model in self.env and self.env['ir.ui.view'].sudo().search_count([('model', '=', model), ('type', '=', 'form')]):
            return {'type': 'ir.actions.act_window', 'res_model': model, 'res_id': int(rid), 'views': [[False, 'form']], 'target': 'current'}
        if fallback:
            act = self.env.ref(fallback, raise_if_not_found=False)
            if act:
                return act.sudo()._get_action_dict() if hasattr(act, '_get_action_dict') else {'type': 'ir.actions.client', 'tag': act.tag}
        return False

    @api.model
    def count(self):
        try:
            return len(self.approvals())
        except Exception:
            return 0

    # -------------------------------------------------- the kinds Unio ships
    def _uid_of(self, employee):
        return employee.user_id.id if employee and employee.user_id else False

    # staff loans and salary advances: HR sets the terms, then the general manager approves
    def _ap_loan_item(self, x):
        cur = self.env.company.currency_id
        r = x._sb_rules()
        kind = 'Loan' if x.kind == 'loan' else 'Salary advance'
        details = ['%s %s a month for %s months%s' % (cur.symbol or '', '{:,.2f}'.format(x.installment), x.months or '-',
                                                     (', from %s' % x.start) if x.start else '') if x.installment else 'Terms not set yet',
                   'One third of the monthly wage: %s (other loans take %s)' % ('{:,.2f}'.format(r['third']), '{:,.2f}'.format(r['others']))]
        if x.note:
            details.insert(0, x.note)
        return {'key': 'loan:%d' % x.id, 'app': 'Loans', 'icon': 'fa-handshake-o', 'title': '%s · %s' % (kind, x.employee_id.name),
                'details': details, 'amount': x.amount, 'currency': cur.symbol or cur.name, 'since': x.hr_at or x.create_date,
                'model': 'sb.payroll.loan', 'res_id': x.id, 'submitter_uid': x.requested_by.id,
                'approve_label': 'Approve terms' if x.state == 'hr' else 'Approve'}

    def _ap_loan_pending(self):
        Lo = self.env['sb.payroll.loan']
        dom = []
        if Lo._sb_is_hr():
            dom.append([('state', '=', 'hr')])
        if Lo._sb_is_gm():
            dom.append([('state', '=', 'gm')])
        items = []
        for d in dom:
            items += [self._ap_loan_item(x) for x in Lo.sudo().search(d + [('company_id', '=', self.env.company.id)], limit=80)]
        return items

    def _ap_loan_decide(self, rid, approve, note):
        self.env['sb.payroll.service'].loan_decide(int(rid), approve, note)

    def _ap_loan_next(self, rid):
        x = self.env['sb.payroll.loan'].sudo().browse(int(rid))
        return ', '.join(x._sb_gm_users(x.company_id).mapped('name')) or 'the general manager' if x.state == 'gm' else False

    def _ap_loan_mine(self):
        Lo = self.env['sb.payroll.loan'].sudo()
        rows = []
        for x in Lo.search([('requested_by', '=', self.env.uid), ('state', 'in', ('hr', 'gm'))], limit=30):
            it = self._ap_loan_item(x)
            it['waiting_for'] = 'HR' if x.state == 'hr' else (', '.join(x._sb_gm_users(x.company_id).mapped('name')) or 'the general manager')
            rows.append(it)
        return rows

    def _ap_loan_open(self, rid):
        act = self.env.ref('sonador_payroll.action_loans', raise_if_not_found=False)
        return act.sudo()._get_action_dict() if act else False

    # leave
    def _ap_leave_pending(self):
        items = []
        Leave = self.env['sb.leave'].sudo()
        for l in self.env['sb.leave.service'].approvals() or []:
            if not l.get('can_decide'):
                continue
            rec = Leave.browse(l['id'])
            if not self._leave_is_mine_to_decide(rec):
                continue
            det = ['%s → %s (%s days)' % (l['from_ec'], l['to_ec'], l['days'])]
            if l.get('balance') is not None:
                det.append('Annual leave left: %s days' % l['balance'])
            if l.get('reason'):
                det.append(l['reason'])
            items.append({'key': 'leave:%d' % l['id'], 'app': 'Leave', 'icon': 'fa-plane', 'title': '%s · %s' % (l['employee']['name'], l['type']),
                          'avatar': l['employee']['avatar'], 'details': det, 'since': rec.write_date, 'model': 'sb.leave', 'res_id': rec.id,
                          'submitter_uid': self._uid_of(rec.employee_id), 'doc': '%s %s' % (l['type'], l['from_ec'])})
        return items

    def _leave_is_mine_to_decide(self, leave):
        """One approver per leave in Me and its messages: the person's manager when they have one with a login, else HR.
        Others with the right can still decide it in Leave; it just does not wait in their inbox."""
        mgr = leave.employee_id.parent_id
        if mgr and mgr != leave.employee_id and mgr.user_id and mgr.user_id.active:
            return mgr.user_id == self.env.user
        return self.env['sb.leave.service']._is_hr()

    def _ap_leave_decide(self, rid, approve, note):
        self.env['sb.leave.service'].decide(int(rid), approve, note)

    def _ap_leave_mine(self):
        me = self._me()
        if not me:
            return []
        return [{'key': 'leave:%d' % l.id, 'app': 'Leave', 'icon': 'fa-plane', 'title': '%s, %s days' % (l.type_id.name, l.days),
                 'waiting_for': 'HR' if l.manager_ok_id else (l.employee_id.parent_id.name or 'HR'), 'since': l.write_date, 'model': 'sb.leave', 'res_id': l.id}
                for l in self.env['sb.leave'].sudo().search([('employee_id', '=', me.id), ('state', '=', 'submitted')])]

    def _ap_leave_open(self, rid):
        return self._form('sb.leave', rid, 'sonador_timeoff.action_timeoff')

    # overtime
    def _ap_overtime_pending(self):
        me = self._me()
        items = []
        appr = getattr(self.env.company, 'sb_att_ot_approver_id', False)
        if appr and self.env.user != appr:
            return []  # the company named who approves overtime
        for r in self.env['sb.att.service'].overtime('pending') or []:
            if me and r['employee_id'] == me.id:
                continue
            day = self.env['sb.att.day'].sudo().browse(r['id'])
            items.append({'key': 'overtime:%d' % r['id'], 'app': 'Overtime', 'icon': 'fa-clock-o',
                          'title': '%s · %s h overtime' % (r['name'], r['total']),
                          'avatar': '/web/image/hr.employee.public/%d/avatar_128' % r['employee_id'],
                          'details': ['%s %s · in %s, out %s' % (r['weekday'], r['ec'], r['first'] or '-', r['last'] or '-')] + ([r['limit']] if r['limit'] else []),
                          'since': day.write_date, 'model': 'sb.att.day', 'res_id': r['id'], 'submitter_uid': self._uid_of(day.employee_id)})
        return items

    def _ap_overtime_decide(self, rid, approve, note):
        self.env['sb.att.service'].decide_overtime([int(rid)], 'approve' if approve else 'refuse', note or '')

    def _ap_overtime_open(self, rid):
        return self._form('sb.att.day', rid, 'sonador_attendance.action_attendance')

    # monthly plans
    def _ap_plan_pending(self):
        me = self._me()
        if not me:
            return []
        team = self._safe(lambda: self.env['sb.planning']._team(me)) or self.env['hr.employee']
        return [{'key': 'plan:%d' % p.id, 'app': 'Planning', 'icon': 'fa-calendar-check-o',
                 'title': '%s · %s plan' % (p.employee_id.name, p.label), 'avatar': '/web/image/hr.employee.public/%d/avatar_128' % p.employee_id.id,
                 'details': ['%d tasks' % len(p.task_ids)] + ['· %s' % t.name for t in p.task_ids[:5]],
                 'since': p.submitted_at or p.write_date, 'model': 'sb.plan', 'res_id': p.id, 'submitter_uid': self._uid_of(p.employee_id)}
                for p in self.env['sb.plan'].sudo().search([('state', '=', 'submitted'), ('employee_id', 'in', team.ids or [-1])])]

    def _ap_plan_decide(self, rid, approve, note):
        plan = self.env['sb.plan'].sudo().browse(int(rid))
        decisions = {t.id: {'review': 'approved' if approve else 'returned', 'note': '' if approve else note} for t in plan.task_ids}
        self.env['sb.planning'].decide(plan.id, decisions, note or '')

    def _ap_plan_mine(self):
        me = self._me()
        if not me:
            return []
        return [{'key': 'plan:%d' % p.id, 'app': 'Planning', 'icon': 'fa-calendar-check-o', 'title': '%s plan' % (p.label or ''),
                 'waiting_for': p.approver_id.name or p.employee_id.parent_id.name or 'your manager', 'since': p.submitted_at or p.write_date,
                 'model': 'sb.plan', 'res_id': p.id}
                for p in self.env['sb.plan'].sudo().search([('employee_id', '=', me.id), ('state', '=', 'submitted')])]

    def _ap_plan_open(self, rid):
        return self._form('sb.plan', rid, 'sonador_planning.action_planning_team')

    # purchase orders
    def _ap_po_pending(self):
        items = []
        PO = self.env['purchase.order']
        if not PO.has_access('read'):
            # someone named to approve without the Purchase app (the general manager's own orders go to the company's named
            # person, who may only use Telegram): the approval rule alone decides
            if not hasattr(PO, '_sb_can_approve'):
                return []
            PO = PO.sudo()
        for o in PO.search([('state', '=', 'to approve'), ('company_id', 'in', self.env.companies.ids)], limit=80):
            can = o._sb_can_approve(self.env.user) if hasattr(o, '_sb_can_approve') else None
            if can is False or (can is None and not self.env.user.has_group('purchase.group_purchase_manager')):
                continue
            items.append({'key': 'po:%d' % o.id, 'app': 'Purchase', 'icon': 'fa-shopping-cart',
                          'title': '%s · %s' % (o.name, o.partner_id.name), 'doc': o.name,
                          'avatar': False, 'amount': o.amount_total, 'currency': o.currency_id.symbol or o.currency_id.name,
                          'details': ['%s × %s' % ('%g' % l.product_qty, l.product_id.display_name) for l in o.order_line[:5]]
                                     + (['Requested by %s' % o.user_id.name] if o.user_id else []),
                          'since': o.write_date, 'model': 'purchase.order', 'res_id': o.id, 'submitter_uid': (o.user_id or o.create_uid).id})
        return items

    def _ap_po_decide(self, rid, approve, note):
        o = self.env['purchase.order'].browse(int(rid))
        if not o.has_access('write'):
            can = o.sudo()._sb_can_approve(self.env.user) if hasattr(o, '_sb_can_approve') else False
            if can is not True:
                raise UserError(self.env._('This order does not wait for your approval.'))
            o = o.sudo()  # the approval rule names this person (see _ap_po_pending); approved in their name
        if approve:
            o.button_approve()
        else:
            o.button_cancel()
            o.button_draft()
            o.message_post(body=self.env._('Sent back by %(u)s: %(n)s', u=self.env.user.name, n=note))

    def _ap_po_mine(self):
        rows = []
        for o in self.env['purchase.order'].sudo().search(['|', ('user_id', '=', self.env.uid), ('create_uid', '=', self.env.uid),
                                                            ('state', '=', 'to approve')], limit=50):
            g = getattr(o, 'sb_approver_group_id', False)
            rows.append({'key': 'po:%d' % o.id, 'app': 'Purchase', 'icon': 'fa-shopping-cart', 'title': '%s · %s' % (o.name, o.partner_id.name),
                         'amount': o.amount_total, 'currency': o.currency_id.symbol or o.currency_id.name,
                         'waiting_for': (g.name if g else 'a purchase manager'), 'since': o.write_date, 'model': 'purchase.order', 'res_id': o.id})
        return rows

    def _ap_po_open(self, rid):
        return self._form('purchase.order', rid)

    # fuel requests
    def _ap_fuel_pending(self):
        if not self.env['sb.fleet']._is_manager():
            return []
        return [{'key': 'fuel:%d' % f.id, 'app': 'Fuel', 'icon': 'fa-tint', 'title': '%s · %s' % (f.vehicle_id.display_name, f.driver_id.name or ''),
                 'details': ['%g litres asked' % f.liters_req] + ([f.reason] if f.reason else []), 'since': f.requested_at or f.create_date,
                 'model': 'sb.fuel', 'res_id': f.id, 'submitter_uid': self._uid_of(f.driver_id) or f.create_uid.id, 'doc': f.name}
                for f in self.env['sb.fuel'].sudo().search([('state', '=', 'request'), ('company_id', '=', self.env.company.id)], limit=80)]

    def _ap_fuel_decide(self, rid, approve, note):
        self.env['sb.fleet'].decide_fuel([int(rid)], approve, note)

    def _ap_fuel_mine(self):
        me = self._me()
        return [{'key': 'fuel:%d' % f.id, 'app': 'Fuel', 'icon': 'fa-tint', 'title': '%s, %g litres' % (f.vehicle_id.display_name, f.liters_req),
                 'waiting_for': 'a fleet manager', 'since': f.requested_at or f.create_date, 'model': 'sb.fuel', 'res_id': f.id}
                for f in self.env['sb.fuel'].sudo().search([('state', '=', 'request'), '|', ('create_uid', '=', self.env.uid),
                                                            ('driver_id', '=', me.id if me else -1)], limit=30)]

    def _ap_fuel_open(self, rid):
        return self._form('sb.fuel', rid, 'sonador_fleet.action_fleet')

    # per diem of trips
    def _ap_perdiem_pending(self):
        if not self.env['sb.fleet']._is_manager():
            return []
        cur = self.env.company.currency_id
        return [{'key': 'perdiem:%d' % t.id, 'app': 'Per diem', 'icon': 'fa-road', 'title': '%s · %s' % (t.name, t.driver_id.name or ''),
                 'details': ['%s days to %s' % (t.days, t.destination or t.purpose or '-')], 'amount': t.perdiem, 'currency': cur.symbol or cur.name,
                 'since': t.write_date, 'model': 'sb.trip', 'res_id': t.id, 'submitter_uid': self._uid_of(t.driver_id), 'doc': t.name}
                for t in self.env['sb.trip'].sudo().search([('perdiem_state', '=', 'requested'), ('company_id', '=', self.env.company.id)], limit=80)]

    def _ap_perdiem_decide(self, rid, approve, note):
        t = self.env['sb.trip'].sudo().browse(int(rid))
        self.env['sb.fleet'].decide_perdiem(int(rid), approve)
        if not approve and note and hasattr(t, 'message_post'):
            t.message_post(body=self.env._('Per diem sent back: %s', note))

    def _ap_perdiem_mine(self):
        me = self._me()
        if not me:
            return []
        return [{'key': 'perdiem:%d' % t.id, 'app': 'Per diem', 'icon': 'fa-road', 'title': t.name, 'waiting_for': 'a fleet manager',
                 'since': t.write_date, 'model': 'sb.trip', 'res_id': t.id}
                for t in self.env['sb.trip'].sudo().search([('perdiem_state', '=', 'requested'), ('driver_id', '=', me.id)])]

    def _ap_perdiem_open(self, rid):
        return self._form('sb.trip', rid, 'sonador_fleet.action_fleet')

    # project hours (one item per person and job)
    def _ap_hours_pending(self):
        if not (self.env.su or self.env.user.has_group('sonador_project.group_user')):
            return []
        items = []
        T = self.env['sb.timesheet'].sudo()
        for g in self.env['sb.projects'].approvals() or []:
            recs = T.browse(g['ids'])
            items.append({'key': 'hours:%s' % '-'.join(str(i) for i in g['ids']), 'app': 'Hours', 'icon': 'fa-hourglass-half',
                          'title': '%s · %s h on %s' % (g['employee'], round(g['hours'], 2), g['job']),
                          'avatar': '/web/image/hr.employee.public/%d/avatar_128' % g['employee_id'],
                          'details': ['%s · %s h %s' % (l['date'], l['hours'], l['task']) for l in g['lines'][:5]],
                          'since': min(recs.mapped('write_date')) if recs else False, 'model': 'sb.job', 'res_id': g['job_id'],
                          'submitter_uid': self._uid_of(recs[:1].employee_id)})
        return items

    def _ap_hours_decide(self, rid, approve, note):
        self.env['sb.projects'].decide_hours([int(i) for i in rid.split('-')], approve, note)

    def _ap_hours_mine(self):
        me = self._me()
        if not me or not (self.env.su or self.env.user.has_group('sonador_project.group_user')):
            return []
        rows = {}
        for t in self.env['sb.timesheet'].sudo().search([('employee_id', '=', me.id), ('state', '=', 'submitted')]):
            r = rows.setdefault(t.job_id.id, {'key': 'hours:%d' % t.id, 'app': 'Hours', 'icon': 'fa-hourglass-half', 'title': t.job_id.display_name,
                                              'hours': 0.0, 'waiting_for': t.job_id.manager_id.name or 'the project manager',
                                              'since': t.write_date, 'model': 'sb.job', 'res_id': t.job_id.id})
            r['hours'] += t.hours
        for r in rows.values():
            r['title'] = '%s h on %s' % (round(r.pop('hours'), 2), r['title'])
        return list(rows.values())

    def _ap_hours_open(self, rid):
        t = self.env['sb.timesheet'].sudo().browse(int(rid.split('-')[0]))
        return self._form('sb.job', t.job_id.id, 'sonador_project.action_projects')

    # gate passes waiting at the gate: security lets them out
    def _gate_user(self):
        return self.env.su or self.env.user.has_group('sonador_me.group_gate')

    def _ap_gate_pending(self):
        if not self._gate_user():
            return []
        return [{'key': 'gate:%d' % g.id, 'app': 'Gate', 'icon': 'fa-ticket', 'title': '%s · %s' % (g.name, g.partner_id.name or g.destination or g.source or ''),
                 'details': ([('%s %s' % (g.vehicle or '', g.driver or '')).strip()] if (g.vehicle or g.driver) else [])
                            + ['%g × %s' % (l.quantity, l.name) for l in g.line_ids[:5]],
                 'since': g.write_date, 'model': 'sb.gate.pass', 'res_id': g.id, 'submitter_uid': g.issued_by.id, 'doc': g.name,
                 'approve_label': 'Let out'}
                for g in self.env['sb.gate.pass'].sudo().search([('state', '=', 'issued'), ('company_id', '=', self.env.company.id)], limit=80)]

    def _ap_gate_decide(self, rid, approve, note):
        g = self.env['sb.gate.pass'].sudo().browse(int(rid))
        if approve:
            g.with_user(self.env.user).sudo().action_out()
        else:
            g.action_cancel()
            g.message_post(body=self.env._('Stopped at the gate by %(u)s: %(n)s', u=self.env.user.name, n=note))

    def _ap_gate_mine(self):
        return [{'key': 'gate:%d' % g.id, 'app': 'Gate', 'icon': 'fa-ticket', 'title': g.name, 'waiting_for': 'security at the gate',
                 'since': g.write_date, 'model': 'sb.gate.pass', 'res_id': g.id}
                for g in self.env['sb.gate.pass'].sudo().search([('state', '=', 'issued'), ('issued_by', '=', self.env.uid)], limit=30)]

    def _ap_gate_open(self, rid):
        return self._form('sb.gate.pass', rid)

    # store documents sent to finance (Inventory: "Store documents are posted by finance")
    def _finance_user(self):
        return self.env.su or self.env.user.has_group('sonador_books.group_sb_clerk')

    def _store_item(self, v):
        fmt = lambda x: '{:,.2f}'.format(x)  # noqa: E731
        who = v.partner_id.name or v.department or (v.warehouse_dest_id and '→ %s' % v.warehouse_dest_id.name) or v.warehouse_id.name or ''
        det = ['%s × %s @ %s' % ('%g' % l.quantity, l.product_id.display_name, fmt(l.unit_cost)) for l in v.line_ids.filtered('product_id')[:6]]
        det += ['Check: %s' % w for w in v._sb_price_warnings()]
        if v.reference:
            det.append('Ref. %s' % v.reference)
        return {'key': 'store:%d' % v.id, 'app': 'Inventory', 'icon': 'fa-cubes', 'title': '%s · %s' % (v._type_label(), who),
                'doc': v._type_label(), 'amount': v.amount_total, 'currency': v.currency_id.symbol or v.currency_id.name,
                'details': det, 'since': v.sb_sent_finance_at, 'model': 'sb.stock.voucher', 'res_id': v.id,
                'submitter_uid': v.sb_sent_finance_by.id, 'approve_label': 'Post'}

    def _ap_store_pending(self):
        SV = self.env['sb.stock.voucher']
        if 'sb_sent_finance_at' not in SV._fields or not self._finance_user():
            return []
        return [self._store_item(v) for v in SV.search([('state', '=', 'draft'), ('sb_sent_finance_at', '!=', False),
                                                        ('company_id', 'in', self.env.companies.ids)], order='sb_sent_finance_at', limit=80)]

    def _ap_store_decide(self, rid, approve, note):
        v = self.env['sb.stock.voucher'].browse(int(rid)).with_context(sb_me_logged=True)  # Me logs the decision itself
        if approve:
            v.action_sb_finance_post()
        else:
            v.action_sb_send_back(note)

    def _ap_store_mine(self):
        SV = self.env['sb.stock.voucher']
        if 'sb_sent_finance_at' not in SV._fields:
            return []
        return [{'key': 'store:%d' % v.id, 'app': 'Inventory', 'icon': 'fa-cubes',
                 'title': '%s · %s' % (v._type_label(), v.partner_id.name or v.department or v.warehouse_id.name or ''),
                 'waiting_for': 'finance (unit price and VAT check)', 'since': v.sb_sent_finance_at, 'model': 'sb.stock.voucher', 'res_id': v.id}
                for v in SV.sudo().search([('state', '=', 'draft'), ('sb_sent_finance_by', '=', self.env.uid),
                                           ('sb_sent_finance_at', '!=', False)], limit=30)]

    def _ap_store_open(self, rid):
        return self._form('sb.stock.voucher', rid)

    # supplier bills Unio made by itself (a prepaid purchase order received) that could not be posted
    def _ap_autobill_pending(self):
        V = self.env['sb.voucher']
        if 'sb_auto_bill_error' not in V._fields or not self._finance_user():
            return []
        items = []
        for b in V.search([('state', '=', 'draft'), ('sb_auto_bill_error', '!=', False), ('company_id', 'in', self.env.companies.ids)],
                          limit=80):
            items.append({'key': 'autobill:%d' % b.id, 'app': 'Purchase', 'icon': 'fa-file-text-o',
                          'title': 'Supplier bill · %s' % (b.partner_id.name or ''), 'doc': 'Supplier bill',
                          'amount': b.amount_total, 'currency': b.currency_id.symbol or b.currency_id.name,
                          'details': ['Made from %s, not posted: %s' % (', '.join(b.sb_grn_ids.mapped('name')) or '-', b.sb_auto_bill_error)]
                                     + ['%s × %s' % ('%g' % l.quantity, l.product_id.display_name or l.name) for l in b.line_ids[:5]],
                          'since': b.create_date, 'model': 'sb.voucher', 'res_id': b.id, 'submitter_uid': False,
                          'approve_label': 'Post', 'refuse_label': 'Leave as draft'})
        return items

    def _ap_autobill_decide(self, rid, approve, note):
        b = self.env['sb.voucher'].browse(int(rid)).with_context(sb_me_logged=True)
        if approve:
            b.action_post()
        else:
            b.write({'sb_auto_bill_error': False})
            b.message_post(body=self.env._('Left as a draft by %(u)s: %(n)s', u=self.env.user.name, n=note))

    def _ap_autobill_open(self, rid):
        return self._form('sb.voucher', rid)

    # ------------------------------------------------------------------ actions from the page
    @api.model
    def leave_preview(self, vals):
        return self.env['sb.leave.service'].preview(vals)

    @api.model
    def leave_request(self, vals):
        self.env['sb.leave.service'].request(vals, submit=True)
        return self.data()

    @api.model
    def leave_cancel(self, leave_id):
        self.env['sb.leave.service'].cancel(leave_id)
        return self.data()

    @api.model
    def clock(self, lat=0.0, lon=0.0, acc=0):
        self.env['sb.att.service'].clock(lat, lon, acc)
        return self.data()
