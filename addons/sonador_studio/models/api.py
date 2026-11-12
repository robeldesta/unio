"""What the Studio screen calls."""
import json

from odoo import api, fields, models
from odoo.exceptions import UserError

from .builder import ICONS, TYPES, icon_svg, slug
from .studio import require_studio
from .templates import TEMPLATES

# models people link to, by everyday name
LINKABLE = [('res.partner', 'Contact (customer, supplier, person)'), ('res.users', 'User (colleague)'), ('product.product', 'Product'),
            ('hr.employee', 'Employee'), ('res.company', 'Company'), ('res.country', 'Country'), ('res.currency', 'Currency'),
            ('project.project', 'Project'), ('fleet.vehicle', 'Vehicle'), ('stock.warehouse', 'Warehouse'), ('account.account', 'Account'),
            ('sale.order', 'Sales order'), ('purchase.order', 'Purchase order')]
# models of the apps that can be extended, by everyday name
EXTENDABLE = [('res.partner', 'Contacts'), ('product.template', 'Products'), ('hr.employee', 'Employees'), ('sale.order', 'Sales orders'),
              ('purchase.order', 'Purchase orders'), ('crm.lead', 'Leads and opportunities'), ('project.task', 'Tasks'),
              ('project.project', 'Projects'), ('fleet.vehicle', 'Vehicles'), ('hr.applicant', 'Job applicants'),
              ('account.asset', 'Fixed assets'), ('stock.warehouse', 'Warehouses'), ('res.users', 'Users')]


class SbStudioApi(models.AbstractModel):
    _name = 'sb.studio.api'
    _description = 'Studio screen'

    # ------------------------------------------------------------------ start page
    @api.model
    def home(self):
        require_studio(self.env)
        apps = []
        for app in self.env['sb.studio.app'].search([]):
            apps.append({'id': app.id, 'name': app.name, 'icon': app.icon, 'color': app.color, 'svg': icon_svg(app.icon, app.color),
                         'description': app.description or '',
                         'models': [{'model': m.model, 'label': m.label} for m in app.model_ids if m.own and not m.parent_id]})
        changed = {m.model for m in self.env['sb.studio.model'].search([('own', '=', False)])}
        return {'apps': apps, 'icons': list(ICONS), 'icon_svgs': {k: icon_svg(k, '#2D6BFF') for k in ICONS},
                'templates': [{'key': k, 'name': t['name'], 'icon': t['icon'], 'color': t['color'], 'description': t['description']}
                              for k, t in TEMPLATES.items()],
                'extendable': [{'model': m, 'label': l, 'changed': m in changed} for m, l in EXTENDABLE if m in self.env],
                'types': [{'key': k, 'label': v[1]} for k, v in TYPES.items()],
                'ai': 'unio.ai' in self.env and self.env['unio.ai'].enabled()}

    # ------------------------------------------------------------------ apps
    def _unique_model(self, label):
        base = slug(label, 'x_', 30)
        name, n = base, 2
        while name in self.env or self.env['sb.studio.model'].search_count([('model', '=', name)]):
            name, n = '%s_%d' % (base, n), n + 1
        return name

    @api.model
    def app_create(self, name, icon='box', color='#2D6BFF', template=None, record_label=None, description=None, specs=None):
        """A new app with its first kind of record (from a template, a draft, or a blank one named record_label)."""
        require_studio(self.env)
        if not (name or '').strip():
            raise UserError(self.env._('Give the app a name.'))
        app = self.env['sb.studio.app'].create({'name': name.strip(), 'icon': icon if icon in ICONS else 'box', 'color': color,
                                                'description': description})
        if template:
            t = TEMPLATES[template]
            specs = json.loads(json.dumps(t['models']))
        specs = specs or [self.blank_spec(record_label or name)]
        renamed = {}
        for spec in specs:
            new = self._unique_model(spec.get('label') or 'record')
            renamed[spec['model']] = new
        for seq, spec in enumerate(specs):
            spec = self._rename(spec, renamed)
            sm = self.env['sb.studio.model'].create({'app_id': app.id, 'model': spec['model'], 'label': spec['label'], 'own': True, 'sequence': seq})
            self.env['sb.studio.builder'].apply(sm, spec, 'Created')
        for spec in specs:
            for a in spec.get('automations') or []:
                try:
                    with self.env.cr.savepoint():
                        self.env['sb.studio.automation'].save(renamed[spec['model']], self._rename(a, renamed))
                except UserError:
                    continue  # a drafted automation that does not fit is left out; the app is still made
        return {'app_id': app.id, 'model': renamed[specs[0]['model']], 'menu_id': app.menu_id.id}

    def _rename(self, spec, renamed):
        """Template and draft specs use placeholder model names; put the real ones in."""
        text = json.dumps(spec)
        for old, new in sorted(renamed.items(), key=lambda kv: -len(kv[0])):
            text = text.replace('"%s"' % old, '"%s"' % new)
        return json.loads(text)

    @api.model
    def blank_spec(self, label):
        return {'model': 'x_new', 'label': (label or 'Record').strip(), 'own': True, 'chatter': True,
                'fields': [{'name': 'x_name', 'label': 'Name', 'type': 'text', 'required': True}],
                'form': {'title': 'x_name', 'sections': [{'label': '', 'columns': [[], []]}], 'tabs': []}, 'list': ['x_name']}

    @api.model
    def app_update(self, app_id, vals):
        require_studio(self.env)
        app = self.env['sb.studio.app'].browse(app_id)
        app.write({k: v for k, v in vals.items() if k in ('name', 'icon', 'color', 'description', 'sequence')})
        self.env['sb.studio.builder']._sync_app_menu(app)
        return self.home()

    @api.model
    def model_add(self, app_id, label):
        require_studio(self.env)
        app = self.env['sb.studio.app'].browse(app_id)
        spec = self.blank_spec(label)
        spec['model'] = self._unique_model(label)
        sm = self.env['sb.studio.model'].create({'app_id': app.id, 'model': spec['model'], 'label': spec['label'], 'own': True,
                                                 'sequence': len(app.model_ids) + 1})
        self.env['sb.studio.builder'].apply(sm, spec, 'Created')
        return spec['model']

    @api.model
    def app_delete(self, app_id):
        """The app, its screens and menus, and every record kept in it."""
        require_studio(self.env)
        app = self.env['sb.studio.app'].browse(app_id)
        sms = app.model_ids
        self.env['base.automation'].sudo().search([('model_id.model', 'in', sms.mapped('model'))]).unlink()
        for sm in sms:
            (sm.menu_id | app.menu_id).sudo().unlink()
            if sm.action_id:
                sm.action_id.sudo().unlink()
            sm.view_ids.sudo().unlink()
        own = sms.filtered('own')
        Fields = self.env['ir.model.fields'].sudo()
        # formulas and tables first: other fields depend on them
        self._drop_formulas(Fields.search([('model', 'in', own.mapped('model')), ('state', '=', 'manual'), ('compute', '!=', False)]))
        Fields.search([('model', 'in', own.mapped('model')), ('state', '=', 'manual'), ('related', '!=', False)]).unlink()
        Fields.search([('model', 'in', own.mapped('model')), ('state', '=', 'manual'), ('ttype', '=', 'one2many')]).unlink()
        # lines first: they point at their records
        for sm in own.sorted(lambda m: not m.parent_id):
            self.env['ir.model.access'].sudo().search([('model_id.model', '=', sm.model)]).unlink()
            self.env['ir.model'].sudo().search([('model', '=', sm.model)]).unlink()
        sms.unlink()
        app.unlink()
        return self.home()

    def _drop_formulas(self, formulas):
        """Formulas one at a time, those nothing else depends on first (a total over a table's line amounts goes before them)."""
        left = formulas
        while left:
            done = left.browse()
            for f in left:
                if not any(f.name in (g.depends or '') and g.model != f.model or (g.model == f.model and f.name in (g.depends or '').split(','))
                           for g in left - f):
                    f.unlink()
                    done |= f
            if not done:
                left[:1].unlink()
                done = left[:1]
            left = (left - done).exists()

    # ------------------------------------------------------------------ one model
    @api.model
    def load(self, model):
        require_studio(self.env)
        sm = self.env['sb.studio.model'].search([('model', '=', model)], limit=1)
        if not sm:
            if model not in self.env:
                raise UserError(self.env._('There is no model %s.', model))
            # an app's model: start a (still empty) set of additions to it
            label = dict(EXTENDABLE).get(model) or self.env['ir.model']._get(model).name
            sm = sm.create({'model': model, 'label': label, 'own': False,
                            'spec': json.dumps({'model': model, 'label': label, 'own': False, 'fields': [], 'place': [], 'list': []})})
        spec = sm.get_spec()
        out = {'model': model, 'label': sm.label, 'own': sm.own, 'spec': spec, 'app_id': sm.app_id.id, 'app': sm.app_id.name or '',
               'action_id': sm.action_id.id, 'menu_id': (sm.app_id.menu_id or sm.menu_id).id,
               'types': [{'key': k, 'label': v[1]} for k, v in TYPES.items()],
               'linkable': [{'model': m, 'label': l} for m, l in LINKABLE if m in self.env] +
                           [{'model': m.model, 'label': '%s (%s)' % (m.label, m.app_id.name)} for m in
                            self.env['sb.studio.model'].search([('own', '=', True), ('parent_id', '=', False)])],
               'versions': [{'id': v.id, 'summary': v.summary, 'user': v.user_id.name, 'date': fields.Datetime.to_string(v.create_date)}
                            for v in sm.version_ids[:50]],
               'automations': self.env['sb.studio.automation'].list(model),
               'count': self.env[model].sudo().with_context(active_test=False).search_count([]) if model in self.env else 0}
        if not sm.own:
            out['outline'] = self.env['sb.studio.extend'].outline(model)
            M = self.env[model]
            out['existing'] = [{'name': n, 'label': f.string, 'type': f.type} for n, f in sorted(M._fields.items(), key=lambda x: x[1].string or '')
                               if not n.startswith('x_studio_') and f.store and n not in ('id', 'create_uid', 'write_uid', 'create_date', 'write_date')]
        return out

    @api.model
    def save(self, model, spec, summary='Changed'):
        require_studio(self.env)
        sm = self.env['sb.studio.model'].search([('model', '=', model)], limit=1)
        if not sm:
            raise UserError(self.env._('Open the model in Studio first.'))
        spec['model'] = model
        self.env['sb.studio.builder'].apply(sm, spec, summary)
        return self.load(model)

    @api.model
    def new_field_name(self, model, label, own=True):
        """A free technical name for a field labelled label."""
        sm = self.env['sb.studio.model'].search([('model', '=', model)], limit=1)
        taken = {f['name'] for f in sm.get_spec().get('fields', [])} | set(self.env[model]._fields if model in self.env else [])
        base = slug(label, 'x_' if own else 'x_studio_', 30)
        name, n = base, 2
        while name in taken:
            name, n = '%s_%d' % (base, n), n + 1
        return name

    @api.model
    def revert(self, version_id):
        require_studio(self.env)
        v = self.env['sb.studio.version'].browse(version_id)
        sm = v.studio_model_id
        self.env['sb.studio.builder'].apply(sm, json.loads(v.spec), 'Back to "%s" of %s' % (v.summary, fields.Datetime.to_string(v.create_date)[:16]))
        return self.load(sm.model)

    @api.model
    def save_automation(self, model, spec, automation_id=False):
        self.env['sb.studio.automation'].save(model, spec, automation_id)
        return self.env['sb.studio.automation'].list(model)

    @api.model
    def remove_automation(self, model, automation_id):
        self.env['sb.studio.automation'].remove(automation_id)
        return self.env['sb.studio.automation'].list(model)

    @api.model
    def draft(self, description):
        """An app drafted from a description in plain words (see ai.py)."""
        require_studio(self.env)
        return self.env['sb.studio.ai'].draft(description)
