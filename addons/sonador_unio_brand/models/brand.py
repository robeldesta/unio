"""Puts Unio where Odoo's own placeholders are, and leaves alone whatever a company chose itself."""
import base64
import hashlib

from odoo import api, models
from odoo.tools.misc import file_open

# Odoo's placeholders: replaced only while a company still has them
PLACEHOLDERS = {'base/static/img/res_company_logo.png', 'web/static/img/favicon.ico'}
ODOO_LINK = ('https://www.odoo.com?utm_source=db&amp;utm_medium=auth', 'https://www.odoo.com?utm_source=db&utm_medium=auth')


def _b64(path):
    with file_open(path, 'rb') as f:
        return base64.b64encode(f.read())


class UnioBrand(models.AbstractModel):
    _name = 'unio.brand'
    _description = 'Unio branding'

    @api.model
    def placeholder_hashes(self):
        return {hashlib.md5(_b64(p)).hexdigest() for p in PLACEHOLDERS}

    @api.model
    def is_placeholder(self, data):
        if not data:
            return True
        raw = data if isinstance(data, bytes) else data.encode()
        return hashlib.md5(raw).hexdigest() in self.placeholder_hashes()

    @api.model
    def apply(self):
        """Run at install and on every upgrade (data/data.xml)."""
        favicon = _b64('sonador_unio_brand/static/img/favicon.png')
        bar = _b64('sonador_unio_brand/static/img/unio_wordmark_white.png')
        for c in self.env['res.company'].sudo().search([]):
            vals = {}
            if 'favicon' in c._fields and self.is_placeholder(c.favicon):
                vals['favicon'] = favicon
            if 'appbar_image' in c._fields and self.is_placeholder(c.appbar_image):
                vals['appbar_image'] = bar
            if vals:
                c.write(vals)
        self.env['ir.config_parameter'].sudo().set_param('web.web_app_name', 'Unio')
        # a company's system: people are invited, nobody signs up on the login page ("Don't have an account?" gone)
        self.env['ir.config_parameter'].sudo().set_param('auth_signup.invitation_scope', 'b2b')
        # Discuss and Settings wear Unio icons
        for xmlid, icon in (('mail.menu_root_discuss', 'discuss'), ('base.menu_administration', 'settings')):
            menu = self.env.ref(xmlid, raise_if_not_found=False)
            path = 'sonador_unio_brand,static/img/apps/%s.png' % icon
            if menu and menu.web_icon != path:
                menu.sudo().web_icon = path
        # Odoo's weekly call home to odoo.com and its "periodic digest" emails: off
        for xmlid in ('mail.ir_cron_module_update_notification', 'digest.ir_cron_digest_scheduler_action'):
            cron = self.env.ref(xmlid, raise_if_not_found=False)
            if cron and cron.active:
                cron.sudo().active = False
        if 'digest.digest' in self.env:
            self.env['digest.digest'].sudo().search([('state', '=', 'activated')]).write({'state': 'deactivated'})
        # app icons are kept in the database when a menu is made: refresh them from the files (small icons load faster)
        for menu in self.env['ir.ui.menu'].sudo().with_context(active_test=False).search([('web_icon', '!=', False), ('parent_id', '=', False)]):
            data = menu._read_image(menu.web_icon) if hasattr(menu, '_read_image') else False
            if data and data != menu.web_icon_data:
                menu.web_icon_data = data
        # Odoo's own apps (Invoicing, Inventory, Manufacturing...), installed after Accounting hid them, are hidden again
        menus = self.env['ir.ui.menu']
        if hasattr(menus, '_sb_hide_legacy_apps'):
            menus._sb_hide_legacy_apps()
        # Odoo's module list (Odoo's icons and store) gives way to the Apps screen of the company's own apps
        if self.env.ref('sonador_saas_client.menu_apps', raise_if_not_found=False):
            odoo_apps = self.env.ref('base.menu_management', raise_if_not_found=False)
            if odoo_apps:
                odoo_apps.sudo().active = False
        # email subjects and texts that name Odoo ("... invites you to connect to Odoo"): Unio
        import re
        word = re.compile(r'\bOdoo\b')
        templates = self.env['mail.template'].sudo().with_context(active_test=False).search(['|', ('subject', 'ilike', 'odoo'), ('body_html', 'ilike', 'odoo')])
        # while modules load, the templates of apps loaded later are not reachable yet: they get it on the next upgrade
        for t in templates.filtered(lambda x: not x.model or x.model in self.env):
            subject = t.subject or ''
            if word.search(subject):
                t.subject = word.sub('Unio', subject)
            body = str(t.body_html or '')
            # only the word in the text: links (odoo.com, /odoo/...) are handled below or are routes
            new_body = re.sub(r'(?<![/.\w])Odoo(?![\w/]|\.\w)', 'Unio', body)
            # the invite's "Have a look at the Odoo Tour" (odoo.com): gone
            new_body = re.sub(r'Have a look at the <a href="https://www\.odoo\.com/page/tour[^"]*"[^>]*>[^<]*</a> to discover the tool\.\s*<br/><br/>\s*',
                              '', new_body)
            if new_body != body:
                t.body_html = new_body
        # sign-up and password emails (records, not views): their "Powered by Odoo" line
        for t in self.env['mail.template'].sudo().with_context(active_test=False).search([('body_html', 'ilike', 'odoo.com?utm_source=db')]).filtered(
                lambda x: not x.model or x.model in self.env):
            for lang_body in [t.body_html]:
                body = str(lang_body or '')
                new = body.replace('>Odoo</a>', '>Unio</a>')
                for link in ODOO_LINK:
                    new = new.replace(link, 'https://unio.et')
                new = new.replace('https://www.odoo.com?utm_source=db&amp;utm_medium={{ ctx.get(\'medium\', \'auth\') }}', 'https://unio.et')
                if new != body:
                    t.body_html = new
        return True
