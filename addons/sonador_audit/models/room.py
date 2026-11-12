"""Audit Room screen service: everything the screen shows and every button it has, in plain words."""
from datetime import timedelta

from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from .audit import AREAS, FINDING_STATES, SEVERITY, VERDICTS

FIRST_VISIT_DAYS = 30


def _dt(v, value, fmt='%d %b %Y'):
    return fields.Datetime.context_timestamp(v, value).strftime(fmt) if value else ''


class AuditRoom(models.AbstractModel):
    _name = 'sb.audit.room'
    _description = 'Audit Room'

    # ------------------------------------------------------------------ who is who
    def _is_auditor(self):
        return self.env.user.has_group('sonador_audit.group_auditor')

    def _need_auditor(self):
        if not self._is_auditor():
            raise AccessError(self.env._('Only internal auditors can do this.'))

    def _is_manager(self):
        return self.env.user.has_group('sonador_books.group_sb_manager')

    def _open_visit(self):
        return self.env['sb.audit.visit'].search([('company_id', '=', self.env.company.id), ('state', '=', 'open'),
                                                  ('auditor_id', '=', self.env.uid)], limit=1)

    # ------------------------------------------------------------------ home
    @api.model
    def home(self):
        Visit, Finding = self.env['sb.audit.visit'], self.env['sb.audit.finding']
        company = self.env.company
        visit = self._open_visit() if self._is_auditor() else Visit
        last = Visit.search([('company_id', '=', company.id), ('state', '=', 'closed')], limit=1)
        open_f = Finding.search([('company_id', '=', company.id), ('state', 'in', ('open', 'answered', 'fixed'))])
        mine = open_f.filtered(lambda f: f.owner_id == self.env.user and f.state in ('open', 'answered'))
        return {
            'company': company.name,
            'currency': company.currency_id.symbol or company.currency_id.name,
            'user': self.env.user.name,
            'is_auditor': self._is_auditor(),
            'is_manager': self._is_manager(),
            'visit': self._visit(visit) if visit else False,
            'last': {'id': last.id, 'when': _dt(last, last.started_at), 'auditor': last.auditor_id.name,
                     'verdict': dict(VERDICTS).get(last.verdict, ''), 'verdict_key': last.verdict or '',
                     'days': (fields.Date.context_today(self) - fields.Datetime.context_timestamp(last, last.started_at).date()).days}
            if last else False,
            'counts': {'open': len(open_f.filtered(lambda f: f.state == 'open')),
                       'answered': len(open_f.filtered(lambda f: f.state == 'answered')),
                       'fixed': len(open_f.filtered(lambda f: f.state == 'fixed')),
                       'overdue': len(open_f.filtered('overdue')),
                       'high': len(open_f.filtered(lambda f: f.severity == 'high'))},
            'findings': [self._finding(f) for f in open_f],
            'mine': [self._finding(f) for f in mine],
            'visits': [self._visit_row(v) for v in Visit.search([('company_id', '=', company.id), ('state', '=', 'closed')], limit=12)],
            'auditors': [{'id': u.id, 'name': u.name, 'login': u.login}
                         for u in self.env.ref('sonador_audit.group_auditor').sudo().user_ids.filtered(
                             lambda u: company in u.company_ids and u.active and not u.share)],
            'areas': AREAS, 'severities': SEVERITY, 'verdicts': VERDICTS,
            'owners': self._owners(company),
        }

    def _owners(self, company):
        users = self.env.ref('sonador_books.group_sb_clerk').sudo().all_user_ids.filtered(
            lambda u: company in u.company_ids and u.active and not u.share and not u.has_group('sonador_audit.group_auditor'))
        names = [u.name for u in users]
        return [{'id': u.id, 'name': '%s (%s)' % (u.name, u.login) if names.count(u.name) > 1 else u.name}
                for u in users.sorted('name')]

    def _visit_row(self, v):
        return {'id': v.id, 'when': _dt(v, v.started_at), 'auditor': v.auditor_id.name, 'duration': v.duration,
                'verdict': dict(VERDICTS).get(v.verdict, ''), 'verdict_key': v.verdict or '',
                'raised': len(v.finding_ids), 'high': len(v.finding_ids.filtered(lambda f: f.severity == 'high')),
                'closed': len(v.recheck_ids.filtered(lambda r: r.result == 'closed'))}

    def _visit(self, v):
        rechecked = {r.finding_id.id: r for r in v.recheck_ids}
        to_recheck = self.env['sb.audit.finding'].search([('company_id', '=', v.company_id.id), ('visit_id', '!=', v.id),
                                                          ('create_date', '<', v.started_at),
                                                          '|', ('state', 'in', ('open', 'answered', 'fixed')),
                                                          ('id', 'in', list(rechecked))])
        checks = v.check_ids
        return {
            'id': v.id, 'state': v.state, 'auditor': v.auditor_id.name,
            'started': _dt(v, v.started_at, '%d %b %Y, %H:%M'), 'started_iso': fields.Datetime.to_string(v.started_at),
            'ended': _dt(v, v.ended_at, '%H:%M'), 'duration': v.duration,
            'since': _dt(v, v.since, '%d %b %Y, %H:%M'), 'first': not v.previous_id,
            'previous': {'when': _dt(v, v.previous_id.started_at), 'auditor': v.previous_id.auditor_id.name} if v.previous_id else False,
            'verdict': v.verdict or '', 'verdict_label': dict(VERDICTS).get(v.verdict, ''), 'summary': v.summary or '',
            'checks': [self._check(c) for c in checks],
            'looked': len(checks.filtered(lambda c: c.state != 'todo')), 'total': len(checks),
            'flagged': len(checks.filtered(lambda c: c.count or (c.counted_set and abs(c.counted - c.book_balance) > 0.005))),
            'findings': [self._finding(f) for f in v.finding_ids],
            'rechecks': [dict(self._finding(f), recheck=rechecked[f.id].result if f.id in rechecked else '',
                              recheck_note=rechecked[f.id].note or '' if f.id in rechecked else '') for f in to_recheck],
            'company': v.company_id.name,
        }

    def _check(self, c):
        diff = c.counted - c.book_balance if c.counted_set else 0.0
        return {'id': c.id, 'key': c.key, 'area': dict(AREAS).get(c.area, ''), 'title': c.title, 'why': c.why or '',
                'count': c.count, 'amount': c.amount, 'items': [x for x in (c.detail or '').split('\n') if x],
                'state': c.state, 'note': c.note or '', 'cash': c.key.startswith('cash:'),
                'book': c.book_balance, 'counted': c.counted if c.counted_set else None, 'diff': diff,
                'can_open': bool(c.res_model and c.ids_list())}

    def _finding(self, f):
        return {'id': f.id, 'number': f.name, 'title': f.title, 'area': dict(AREAS).get(f.area, ''), 'area_key': f.area,
                'severity': f.severity, 'severity_label': dict(SEVERITY).get(f.severity), 'found': f.found or '',
                'recommendation': f.recommendation or '', 'amount': f.amount, 'state': f.state,
                'state_label': dict(FINDING_STATES).get(f.state), 'owner': f.owner_id.name or '', 'owner_id': f.owner_id.id,
                'due': f.due_date.strftime('%d %b %Y') if f.due_date else '', 'due_iso': fields.Date.to_string(f.due_date) or '',
                'overdue': f.overdue, 'response': f.response or '', 'response_by': f.response_by.name or '',
                'response_at': _dt(f, f.response_at), 'raised': _dt(f, f.create_date), 'auditor': f.auditor_id.name or '',
                'vouchers': [{'id': v.id, 'name': v.name} for v in f.voucher_ids.sudo()],
                'closed_note': f.closed_note or '', 'mine': f.owner_id == self.env.user, 'check_key': f.check_key or ''}

    # ------------------------------------------------------------------ the visit
    @api.model
    def start_visit(self):
        self._need_auditor()
        if self._open_visit():
            return self.home()
        Visit = self.env['sb.audit.visit']
        prev = Visit.search([('company_id', '=', self.env.company.id), ('state', '=', 'closed')], limit=1)
        since = prev.started_at if prev else fields.Datetime.now() - timedelta(days=FIRST_VISIT_DAYS)
        v = Visit.create({'since': since, 'previous_id': prev.id})
        self._build_checks(v)
        return self.home()

    def _build_checks(self, v):
        Check = self.env['sb.audit.check']
        old = {c.key: c for c in v.check_ids}
        for seq, (key, vals) in enumerate(Check._sb_checks_for(v)):
            vals = dict(vals, sequence=seq)
            if key in old:
                keep = old.pop(key)
                keep.write(vals)
            else:
                Check.create(dict(vals, visit_id=v.id, key=key))
        for c in old.values():
            c.unlink()

    def _my_visit(self, visit_id):
        self._need_auditor()
        v = self.env['sb.audit.visit'].browse(int(visit_id)).exists()
        if not v or v.auditor_id != self.env.user or v.state != 'open':
            raise UserError(self.env._('This visit has ended or is not yours.'))
        return v

    @api.model
    def refresh(self, visit_id):
        self._build_checks(self._my_visit(visit_id))
        return self.home()

    @api.model
    def set_check(self, check_id, state=None, note=None, counted=None):
        c = self.env['sb.audit.check'].browse(int(check_id))
        self._my_visit(c.visit_id.id)
        vals = {}
        if state:
            vals['state'] = state
        if note is not None:
            vals['note'] = note
        if counted is not None and c.key.startswith('cash:'):
            vals.update(counted=float(counted), counted_set=True)
            if not state:
                vals['state'] = 'ok' if abs(float(counted) - c.book_balance) < 0.005 else 'issue'
        c.write(vals)
        return self._check(c)

    @api.model
    def open_check(self, check_id):
        c = self.env['sb.audit.check'].browse(int(check_id))
        ids = c.ids_list()
        if c.res_model == 'account.account':
            fy = c.company_id.compute_fiscalyear_dates(fields.Date.context_today(self))
            return {'type': 'ir.actions.client', 'tag': 'sonador_books.report', 'name': c.title,
                    'context': {'report': 'general_ledger',
                                'options': {'account_ids': ids, 'date_from': fields.Date.to_string(fy['date_from']),
                                            'date_to': fields.Date.to_string(fields.Date.context_today(self))}}}
        return {'type': 'ir.actions.act_window', 'name': c.title, 'res_model': c.res_model,
                'domain': [('id', 'in', ids)], 'views': [[False, 'list'], [False, 'form']], 'target': 'current',
                'context': {'create': False, 'edit': False, 'delete': False}}

    @api.model
    def save_finding(self, vals):
        """Raise (or change) a finding during the open visit."""
        v = self._my_visit(vals['visit_id'])
        numbers = [n.strip() for n in (vals.get('vouchers') or '').replace(';', ',').split(',') if n.strip()]
        vouchers = self.env['sb.voucher'].sudo().search([('company_id', '=', v.company_id.id), ('name', 'in', numbers)])
        missing = set(numbers) - set(vouchers.mapped('name'))
        if missing:
            raise UserError(self.env._('No voucher numbered %s.', ', '.join(sorted(missing))))
        if not (vals.get('title') or '').strip() or not (vals.get('found') or '').strip():
            raise UserError(self.env._('Give the finding a title and say what you found.'))
        data = {'title': vals['title'].strip(), 'area': vals.get('area') or 'other', 'severity': vals.get('severity') or 'medium',
                'found': vals['found'].strip(), 'recommendation': (vals.get('recommendation') or '').strip(),
                'amount': float(vals.get('amount') or 0.0), 'voucher_ids': [(6, 0, vouchers.ids)],
                'owner_id': int(vals['owner_id']) if vals.get('owner_id') else False,
                'due_date': vals.get('due') or False, 'check_key': vals.get('check_key') or False}
        F = self.env['sb.audit.finding']
        if vals.get('id'):
            f = F.browse(int(vals['id']))
            if f.visit_id != v:
                raise UserError(self.env._('Only findings of this visit can be changed here.'))
            old_owner = f.owner_id
            f.write(data)
            if f.owner_id != old_owner:
                self._ask_owner(f)
        else:
            f = F.create(dict(data, visit_id=v.id))
            self._ask_owner(f)
            if data['check_key']:
                v.check_ids.filtered(lambda c: c.key == data['check_key']).write({'state': 'issue'})
        return self.home()

    def _ask_owner(self, f):
        if not f.owner_id:
            return
        f.message_subscribe(partner_ids=(f.owner_id.partner_id | f.auditor_id.partner_id).ids)
        f.activity_schedule('mail.mail_activity_data_todo', date_deadline=f.due_date or fields.Date.context_today(f) + timedelta(days=14),
                            user_id=f.owner_id.id, summary='Audit finding %s: please answer' % f.name,
                            note=Markup('<p>%s raised <b>%s</b> (%s) and asks you to answer by %s.</p><p>%s</p>') % (
                                f.auditor_id.name, f.title, dict(SEVERITY)[f.severity].lower(),
                                (f.due_date or fields.Date.context_today(f) + timedelta(days=14)).strftime('%d %b'), f.found))

    @api.model
    def recheck(self, visit_id, finding_id, result, note=''):
        v = self._my_visit(visit_id)
        f = self.env['sb.audit.finding'].browse(int(finding_id))
        R = self.env['sb.audit.recheck']
        R.search([('visit_id', '=', v.id), ('finding_id', '=', f.id)]).unlink()
        if result:
            R.create({'visit_id': v.id, 'finding_id': f.id, 'result': result, 'note': note})
            if result in ('closed', 'accepted'):
                f.write({'state': result, 'closed_visit_id': v.id, 'closed_note': note})
                f.activity_ids.action_feedback(feedback='Closed by the auditor')
                f.message_post(body=Markup('%s %s this finding on the visit of %s.%s') % (
                    self.env.user.name, 'closed' if result == 'closed' else 'accepted the risk on', _dt(v, v.started_at),
                    Markup(' <i>%s</i>') % note if note else ''))
            else:
                if f.state in ('closed', 'accepted'):
                    f.write({'state': 'open', 'closed_visit_id': False, 'closed_note': False})
                f.message_post(body=Markup('Still open after the visit of %s.%s') % (
                    _dt(v, v.started_at), Markup(' <i>%s</i>') % note if note else ''))
        return self.home()

    @api.model
    def end_visit(self, visit_id, verdict, summary=''):
        v = self._my_visit(visit_id)
        if not verdict:
            raise UserError(self.env._('Choose how the books look overall.'))
        v.write({'state': 'closed', 'ended_at': fields.Datetime.now(), 'verdict': verdict, 'summary': summary})
        self._tell_management(v)
        return {'home': self.home(), 'report': self._visit(v)}

    def _tell_management(self, v):
        managers = self.env.ref('sonador_books.group_sb_manager').sudo().all_user_ids.filtered(
            lambda u: v.company_id in u.company_ids and u.active and not u.share and u.id not in (1,))
        owners = v.finding_ids.owner_id
        partners = (managers | owners).partner_id - self.env.user.partner_id
        high = v.finding_ids.filtered(lambda f: f.severity == 'high')
        closed = v.recheck_ids.filtered(lambda r: r.result == 'closed')
        still = v.recheck_ids.filtered(lambda r: r.result == 'open')
        rows = Markup('').join(Markup('<li><b>%s</b> %s <i>(%s, %s)</i></li>') % (
            f.name, f.title, dict(SEVERITY)[f.severity].lower(), 'for %s' % f.owner_id.name if f.owner_id else 'no owner yet')
            for f in v.finding_ids)
        url = '/odoo/action-sonador_audit.action_room'
        body = Markup('<p>Hello,</p><p><b>%s</b> finished the audit visit of %s (%s). Overall: <b>%s</b>.</p>') % (
            v.auditor_id.name, _dt(v, v.started_at, '%d %b'), v.duration, dict(VERDICTS)[v.verdict].lower())
        if v.summary:
            body += Markup('<p>%s</p>') % v.summary
        body += Markup('<p>%s new finding%s%s. %s earlier finding%s closed, %s still open.</p>') % (
            len(v.finding_ids), '' if len(v.finding_ids) == 1 else 's',
            Markup(', <b>%s high</b>') % len(high) if high else '', len(closed), '' if len(closed) == 1 else 's', len(still))
        if rows:
            body += Markup('<ul>%s</ul>') % rows
        body += Markup('<p><a href="%s" style="background:#243742;color:#fff;padding:8px 14px;border-radius:6px;'
                       'text-decoration:none">Open the Audit Room</a></p>') % url
        v.message_post(body=body, partner_ids=partners.ids, subject='Audit visit %s: %s' % (
            _dt(v, v.started_at, '%d %b'), dict(VERDICTS)[v.verdict].lower()),
                       message_type='comment', subtype_xmlid='mail.mt_comment')

    @api.model
    def visit_report(self, visit_id):
        v = self.env['sb.audit.visit'].browse(int(visit_id)).exists()
        if not v:
            raise UserError(self.env._('No such visit.'))
        return self._visit(v)

    # ------------------------------------------------------------------ management answers
    @api.model
    def answer(self, finding_id, text, fixed=False):
        f = self.env['sb.audit.finding'].browse(int(finding_id)).exists()
        if not f:
            raise UserError(self.env._('No such finding.'))
        if not (f.owner_id == self.env.user or self._is_manager()):
            raise AccessError(self.env._('Only %s or a manager can answer this finding.', f.owner_id.name or 'its owner'))
        if not (text or '').strip():
            raise UserError(self.env._('Write your answer first.'))
        f.sudo().write({'response': text.strip(), 'response_by': self.env.uid, 'response_at': fields.Datetime.now(),
                        'state': 'fixed' if fixed else 'answered'})
        f.sudo().message_post(body=Markup('<p><b>%s</b> answered%s:</p><p>%s</p>') % (
            self.env.user.name, ' and says it is fixed' if fixed else '', text.strip()),
                              partner_ids=f.auditor_id.partner_id.ids, message_type='comment', subtype_xmlid='mail.mt_comment')
        f.sudo().activity_ids.filtered(lambda a: a.user_id == self.env.user).action_feedback(feedback='Answered')
        return self.home()

    # ------------------------------------------------------------------ auditors
    @api.model
    def add_auditor(self, name, email):
        if not self._is_manager():
            raise AccessError(self.env._('Only a manager can add an auditor.'))
        name, email = (name or '').strip(), (email or '').strip().lower()
        if not name or '@' not in email:
            raise UserError(self.env._('Give the auditor\'s name and email.'))
        Users = self.env['res.users'].sudo().with_context(no_reset_password=True)
        group = self.env.ref('sonador_audit.group_auditor')
        user = Users.search([('login', '=', email)], limit=1)
        if user and not user.has_group('sonador_audit.group_auditor'):
            raise UserError(self.env._('%s already uses the system with other rights. Use a separate email for the auditor.', email))
        if not user:
            user = Users.create({'name': name, 'login': email, 'email': email,
                                 'company_id': self.env.company.id, 'company_ids': [(6, 0, [self.env.company.id])],
                                 'group_ids': [(6, 0, [self.env.ref('base.group_user').id, group.id])]})
        try:
            user.action_reset_password()
            sent = True
        except Exception:  # noqa: BLE001 - no outgoing mail: the manager sets a password instead
            sent = False
        res = self.home()
        res['added'] = {'name': user.name, 'login': user.login, 'sent': sent}
        return res

    @api.model
    def remove_auditor(self, user_id):
        if not self._is_manager():
            raise AccessError(self.env._('Only a manager can remove an auditor.'))
        user = self.env['res.users'].sudo().browse(int(user_id))
        if user.has_group('sonador_audit.group_auditor') and not user.has_group('sonador_books.group_sb_clerk'):
            user.active = False
        return self.home()
