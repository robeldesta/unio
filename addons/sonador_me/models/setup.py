"""First-day setup checklist on Me, for the person who runs the system.

A new company opens on an empty Me page. This card says what to do first, for the kind of business chosen at sign-up
(saas.industry), and each step opens the screen where it is done. A step ticks itself when the records exist, so it
never needs to be marked by hand. The card goes away when every step is done, or when someone hides it.
Steps whose app is not installed are left out."""
from odoo import api, models

INDUSTRY_TITLE = {
    'manufacturing': 'Set up your factory', 'trading': 'Set up your trading business', 'retail': 'Set up your shop',
    'services': 'Set up your service business', 'construction': 'Set up your construction company',
    'farm': 'Set up your farm', 'school': 'Set up your school',
}
# which steps each kind of business sees, in order
INDUSTRY_STEPS = {
    'manufacturing': ['company', 'sell', 'materials', 'recipe', 'machines', 'stock', 'team'],
    'trading': ['company', 'sell', 'suppliers', 'stock', 'team'],
    'retail': ['company', 'sell', 'suppliers', 'stock', 'team'],
    'farm': ['company', 'sell', 'suppliers', 'stock', 'team'],
    'services': ['company', 'services', 'customers', 'team'],
    'construction': ['company', 'project', 'suppliers', 'team'],
    'school': ['company', 'team'],
}
DEFAULT_STEPS = ['company', 'sell', 'customers', 'team']
HIDDEN = 'unio.setup_hidden'


class SbMeSetup(models.AbstractModel):
    _inherit = 'sb.me'

    # ------------------------------------------------------------------ the steps
    def _setup_steps(self):
        """key: (title, why, done() -> bool, action() -> dict or False, needs model)"""
        env, company = self.env, self.env.company

        def act(xmlid, **extra):
            def make():
                if not env.ref(xmlid, raise_if_not_found=False):
                    return False
                a = dict(env['ir.actions.actions']._for_xml_id(xmlid), **extra)  # copy: the original is cached
                return a
            return make

        def count(model, domain):
            return env[model].sudo().search_count(domain, limit=1) > 0

        return {
            'company': ('Add your logo and TIN', 'They print on every document you send.',
                        lambda: bool(company.vat),
                        lambda: {'type': 'ir.actions.act_window', 'res_model': 'res.company', 'res_id': company.id,
                                 'views': [[False, 'form']], 'target': 'current'}, 'res.company'),
            'sell': ('Add what you sell', 'Your products, with their selling price.',
                     lambda: count('product.template', [('sale_ok', '=', True)]),
                     act('sonador_books.action_sb_products'), 'product.template'),
            'services': ('Add the services you sell', 'With their price per hour, day or job.',
                         lambda: count('product.template', [('sale_ok', '=', True), ('type', '=', 'service')]),
                         act('sonador_books.action_sb_products'), 'product.template'),
            'materials': ('Add the materials you buy', 'Raw materials and packing, with what they cost you.',
                          lambda: count('product.template', [('purchase_ok', '=', True), ('sale_ok', '=', False)])
                          or ('mrp.bom.line' in env and count('mrp.bom.line', [])),
                          act('sonador_books.action_sb_products'), 'product.template'),
            'suppliers': ('Add your suppliers', 'So purchase orders and bills know who to pay.',
                          lambda: count('res.partner', [('supplier_rank', '>', 0)]),
                          act('account.res_partner_action_supplier'), 'res.partner'),
            'customers': ('Add your customers', 'With their phone and TIN, for quotations and invoices.',
                          lambda: count('res.partner', [('customer_rank', '>', 0)]),
                          act('account.res_partner_action_customer'), 'res.partner'),
            'recipe': ('Write a recipe', 'What goes into one product: materials, labour and overhead.',
                       lambda: count('mrp.bom', []), act('sonador_mrp.action_sb_recipes'), 'mrp.bom'),
            'machines': ('Add your machines or lines', 'The production board plans work on them.',
                         lambda: count('sb.mfg.line', []), act('sonador_factory.action_board', params={'page': 'setup'}),
                         'sb.mfg.line'),
            'stock': ('Enter the stock you have today', 'Your opening quantities and their cost.',
                      lambda: count('stock.quant', [('quantity', '>', 0), ('location_id.usage', '=', 'internal')]),
                      act('sonador_inventory.action_sb_opening_stock'), 'stock.quant'),
            'project': ('Open your first project', 'A site or job, with its budget.',
                        lambda: count('project.project', []), act('project.open_view_project_all'), 'project.project'),
            'team': ('Invite your team', 'Each person signs in with their own email.',
                     lambda: env['res.users'].sudo().search_count([('share', '=', False), ('id', '!=', 1)]) > 1,
                     act('base.action_res_users'), 'res.users'),
        }

    def _setup_admin(self):
        return self.env.user.has_group('base.group_system')

    def _setup(self):
        if not self._setup_admin():
            return False
        P = self.env['ir.config_parameter'].sudo()
        if P.get_param(HIDDEN):
            return False
        industry = P.get_param('saas.industry') or ''
        defs = self._setup_steps()
        steps = []
        for key in INDUSTRY_STEPS.get(industry, DEFAULT_STEPS):
            title, why, done, action, model = defs[key]
            if model not in self.env or not action():
                continue
            steps.append({'key': key, 'title': title, 'why': why, 'done': bool(self._safe(done))})
        if not steps or all(s['done'] for s in steps):
            return False
        return {'title': INDUSTRY_TITLE.get(industry, 'Set up your company'), 'steps': steps,
                'next': next(s['key'] for s in steps if not s['done']),
                'done': sum(s['done'] for s in steps), 'total': len(steps)}

    # ------------------------------------------------------------------ actions from the card
    @api.model
    def setup_open(self, key):
        if not self._setup_admin():
            return False
        return self._setup_steps()[key][3]()

    @api.model
    def setup_hide(self):
        if self._setup_admin():
            self.env['ir.config_parameter'].sudo().set_param(HIDDEN, '1')
        return self.data()

    @api.model
    def setup_add_me(self):
        """The owner of a new company is its first employee: one click instead of "ask HR"."""
        user = self.env.user
        if not self._setup_admin() or self._me():
            return self.data()
        E = self.env['hr.employee'].sudo().with_context(active_test=False)
        vals = {'name': user.name, 'work_email': user.email, 'active': True}
        # a new company keeps the administrator's employee archived (it was "Administrator"): bring it back as this person
        archived = E.search([('user_id', '=', user.id), ('company_id', '=', self.env.company.id), ('active', '=', False)], limit=1)
        if archived:
            archived.write(vals)
        else:
            E.create(dict(vals, user_id=user.id, company_id=self.env.company.id))
        self.env.invalidate_all()  # user.employee_ids was read before the change; Me must see the new link
        return self.data()
