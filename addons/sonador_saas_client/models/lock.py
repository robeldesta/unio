"""Read-only mode: when the cloud service locks this system (trial ended, subscription lapsed), nobody can create,
change or delete records; reading, reports and exports keep working, and nothing is deleted. Background jobs run
as the superuser and are not stopped, so the books stay consistent."""
from odoo import _, api, models
from odoo.exceptions import UserError

# what logging in and looking around must still be able to write
ALLOWED = {'res.users.log', 'res.users.settings', 'res.device', 'res.device.log', 'bus.presence', 'mail.presence', 'ir.attachment',
           'res.users.apikeys.description', 'web_tour.tour', 'ir.ui.view', 'ir.qweb', 'ir.http', 'res.users.settings.volumes'}


class Base(models.AbstractModel):
    _inherit = 'base'

    def _saas_guard(self):
        if self.env.su or self._name in ALLOWED or self._transient:
            return
        if self.env['ir.config_parameter'].sudo().get_param('saas.locked') == 'True':
            raise UserError(_('This system is read-only: the trial has ended or the subscription is not paid. '
                              'You can still read everything and print reports. Subscribe to continue working.'))

    @api.model_create_multi
    def create(self, vals_list):
        self._saas_guard()
        return super().create(vals_list)

    def write(self, vals):
        self._saas_guard()
        return super().write(vals)

    def unlink(self):
        self._saas_guard()
        return super().unlink()
