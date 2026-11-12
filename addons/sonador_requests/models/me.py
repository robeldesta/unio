"""Requests in the approvals inbox of Me (sb.me registry: _approval_kinds + _ap_<kind>_*)."""
from odoo import models

KINDS = {'payreq': ('sb.payment.request', 'Requests', 'fa-money'), 'purreq': ('sb.purchase.request', 'Requests', 'fa-shopping-basket')}
TYPE_LABEL = {'payment': 'Payment request', 'petty': 'Petty cash', 'perdiem': 'Per diem'}


class SbMe(models.AbstractModel):
    _inherit = 'sb.me'

    def _approval_kinds(self):
        return super()._approval_kinds() + [k for k, v in KINDS.items() if self._has(v[0])]

    def _rq_item(self, kind, r):
        model, app, icon = KINDS[kind]
        cur = r.currency_id
        if kind == 'payreq':
            title = '%s %s · %s' % (TYPE_LABEL.get(r.request_type, ''), r.name, r._sb_payee() or r.requester_id.name)
            details = [(r.description or '')[:160], 'Requested by %s' % r.requester_id.name]
            if r.category_id:
                details.append(r.category_id.display_name)
        else:
            title = 'Purchase request %s · %s' % (r.name, r.requester_id.name)
            details = ['%s × %s' % ('%g' % l.qty, l.product_id.display_name or l.name) for l in r.line_ids[:5]] + \
                      [(r.description or '')[:160]]
        step = dict(r._fields['state']._description_selection(self.env)).get(r.state)
        details.append('Step: %s' % step)
        if r.sent_back_reason and r.state == 'draft':
            details.append('Sent back before: %s' % r.sent_back_reason)
        return {'key': '%s:%d' % (kind, r.id), 'app': app, 'icon': icon, 'title': title, 'doc': r.name, 'details': details,
                'amount': r.amount, 'currency': cur.symbol or cur.name, 'since': r.submitted_at or r.write_date,
                'model': model, 'res_id': r.id, 'submitter_uid': r.requester_id.id, 'step': r.state,
                'approve_label': {'finance': 'Check', 'gm': 'Authorize'}.get(r.state, 'Approve')}

    def _rq_pending(self, kind):
        return [self._rq_item(kind, r) for r in self.env[KINDS[kind][0]].sb_waiting_for_user(self.env.user, limit=100)]

    def _rq_decide(self, kind, rid, approve, note):
        r = self.env[KINDS[kind][0]].browse(int(rid))
        if approve:
            r.action_approve()
        else:
            r.action_send_back(note)

    def _rq_mine(self, kind):
        recs = self.env[KINDS[kind][0]].sudo().search([('requester_id', '=', self.env.uid), ('state', 'in', ('dept', 'finance', 'gm'))], limit=50)
        rows = []
        for r in recs:
            it = self._rq_item(kind, r)
            it['waiting_for'] = r.sb_waiting_for
            rows.append(it)
        return rows

    def _rq_next(self, kind, rid):
        r = self.env[KINDS[kind][0]].sudo().browse(int(rid))
        w = r.with_user(self.env.user).sb_waiting_for if r.state in ('dept', 'finance', 'gm') else False
        return ('the ' + w[4:]) if w and w.startswith('The ') else w

    def _ap_payreq_next(self, rid):
        return self._rq_next('payreq', rid)

    def _ap_purreq_next(self, rid):
        return self._rq_next('purreq', rid)

    def _ap_payreq_pending(self):
        return self._rq_pending('payreq')

    def _ap_payreq_decide(self, rid, approve, note):
        return self._rq_decide('payreq', rid, approve, note)

    def _ap_payreq_mine(self):
        return self._rq_mine('payreq')

    def _ap_payreq_open(self, rid):
        return self._form('sb.payment.request', rid)

    def _ap_purreq_pending(self):
        return self._rq_pending('purreq')

    def _ap_purreq_decide(self, rid, approve, note):
        return self._rq_decide('purreq', rid, approve, note)

    def _ap_purreq_mine(self):
        return self._rq_mine('purreq')

    def _ap_purreq_open(self, rid):
        return self._form('sb.purchase.request', rid)
