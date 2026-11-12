from markupsafe import Markup

from odoo import fields, models


def _rebrand(value):
    if isinstance(value, str):
        return value.replace('OdooBot', 'AutoBot').replace('Enjoy exploring Odoo!', 'Enjoy exploring!').replace("Odoo's chat", 'The chat')
    return value


class MailBot(models.AbstractModel):
    _inherit = 'mail.bot'

    def _get_answer(self, *args, **kwargs):
        return _rebrand(super()._get_answer(*args, **kwargs))


class ResUsers(models.Model):
    _inherit = 'res.users'

    odoobot_state = fields.Selection(string='AutoBot status')

    def _init_odoobot(self):
        """The first message AutoBot sends a new user (without naming the platform underneath)."""
        self.ensure_one()
        bot = self.env['ir.model.data']._xmlid_to_res_id('base.partner_root')
        channel = self.env['discuss.channel']._get_or_create_chat([bot, self.partner_id.id])
        message = Markup('%s<br/>%s<br/><b>%s</b> <span class="o_odoobot_command">:)</span>') % (
            self.env._('Hello,'),
            self.env._("The chat helps your team work together. I'm AutoBot, here to help you discover it."),
            self.env._('Try to send me an emoji'))
        channel.sudo().message_post(author_id=bot, body=message, message_type='comment', silent=True, subtype_xmlid='mail.mt_comment')
        self.sudo().odoobot_state = 'onboarding_emoji'
        return channel
