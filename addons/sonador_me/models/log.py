"""What was decided from the approval center (Me or a chat bot), and the short notices people get in Me."""
from odoo import api, fields, models


class SbApprovalLog(models.Model):
    _name = 'sb.approval.log'
    _description = 'Approval decision'
    _order = 'date desc, id desc'
    _rec_name = 'title'

    date = fields.Datetime(default=fields.Datetime.now, required=True, readonly=True, index=True)
    user_id = fields.Many2one('res.users', 'Decided by', required=True, readonly=True, index=True, ondelete='restrict')
    submitter_id = fields.Many2one('res.users', 'Submitted by', readonly=True, index=True, ondelete='set null')
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company, readonly=True)
    key = fields.Char(readonly=True, index=True)
    kind = fields.Char(readonly=True, index=True)
    app = fields.Char(readonly=True)
    title = fields.Char('Document', readonly=True)
    model = fields.Char(readonly=True)
    res_id = fields.Integer(readonly=True)
    amount = fields.Float(readonly=True)
    decision = fields.Selection([('approved', 'Approved'), ('sent_back', 'Sent back')], required=True, readonly=True, index=True)
    note = fields.Char('Reason', readonly=True)
    channel = fields.Selection([('me', 'Me'), ('telegram', 'Telegram'), ('web', 'On the document')], default='me', readonly=True)
    next_step = fields.Char('Now waiting for', readonly=True, help='Set when the approval was one step of several.')

    def action_open(self):
        self.ensure_one()
        return self.env['sb.me'].open_doc(self.key) or False


class SbMeNotice(models.Model):
    _name = 'sb.me.notice'
    _description = 'Notice in Me'
    _order = 'date desc, id desc'

    date = fields.Datetime(default=fields.Datetime.now, required=True, index=True)
    user_id = fields.Many2one('res.users', required=True, index=True, ondelete='cascade')
    title = fields.Char(required=True)
    body = fields.Text()
    key = fields.Char(help='Approval item the notice is about, when there is one.')
    model = fields.Char()
    res_id = fields.Integer()
    event = fields.Char()
    read = fields.Boolean(index=True)

    @api.model
    def _mine(self, limit=15):
        return [{'id': n.id, 'title': n.title, 'body': n.body or '', 'date': fields.Datetime.to_string(n.date), 'read': n.read,
                 'key': n.key or False, 'event': n.event or '', 'model': n.model or False, 'res_id': n.res_id}
                for n in self.sudo().search([('user_id', '=', self.env.uid)], limit=limit)]

    @api.model
    def mark_read(self, ids=None):
        dom = [('user_id', '=', self.env.uid), ('read', '=', False)]
        if ids:
            dom.append(('id', 'in', ids))
        self.sudo().search(dom).write({'read': True})
        return self._mine()
