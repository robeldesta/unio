"""An app built in Studio as a module of its own: models, fields, screens, menus, access rights and automations as
data files, installable on any Odoo 19 database, with or without Studio."""
import io
import json
import re
import zipfile

from markupsafe import escape

from odoo import api, models

from .builder import icon_svg
from .studio import require_studio


def xid(*parts):
    return re.sub(r'[^a-z0-9_]+', '_', '_'.join(p for p in parts if p).lower()).strip('_')[:120]


class SbStudioExport(models.AbstractModel):
    _name = 'sb.studio.export'
    _description = 'Studio export'

    def _field_xml(self, f, model_ref):
        out = []
        fid = xid('field', f.model, f.name)
        attrs = ['<field name="model_id" ref="%s"/>' % model_ref, '<field name="name">%s</field>' % f.name,
                 '<field name="field_description">%s</field>' % escape(f.field_description), '<field name="ttype">%s</field>' % f.ttype,
                 '<field name="state">manual</field>']
        for k in ('required', 'readonly', 'store', 'index', 'copied', 'group_expand'):
            if k in f._fields:
                attrs.append('<field name="%s" eval="%s"/>' % (k, bool(f[k])))
        for k in ('relation', 'relation_field', 'relation_table', 'column1', 'column2', 'on_delete', 'currency_field', 'related', 'help'):
            if k in f._fields and f[k]:
                attrs.append('<field name="%s">%s</field>' % (k, escape(f[k])))
        if f.compute:
            attrs.append('<field name="compute">%s</field>' % escape(f.compute))
            attrs.append('<field name="depends">%s</field>' % escape(f.depends or ''))
        if 'tracking' in f._fields and f.tracking:
            attrs.append('<field name="tracking" eval="%d"/>' % f.tracking)
        out.append('<record id="%s" model="ir.model.fields">%s</record>' % (fid, ''.join(attrs)))
        for s in f.selection_ids:
            out.append('<record id="%s" model="ir.model.fields.selection"><field name="field_id" ref="%s"/><field name="value">%s</field>'
                       '<field name="name">%s</field><field name="sequence" eval="%d"/></record>'
                       % (xid('sel', f.model, f.name, s.value), fid, escape(s.value), escape(s.name), s.sequence))
        return out

    @api.model
    def export_app(self, app_id):
        """(file name, zip bytes) of a Studio app."""
        require_studio(self.env)
        app = self.env['sb.studio.app'].browse(app_id)
        tech = 'unio_' + re.sub(r'[^a-z0-9]+', '_', app.name.lower()).strip('_')[:40]
        sms = app.model_ids
        own = sms.filtered('own')
        IrModel = self.env['ir.model'].sudo()
        Fields = self.env['ir.model.fields'].sudo()
        rec = []
        # models first, then plain fields, then fields linking to the models of the app (so their targets exist)
        own_models = set(own.mapped('model'))
        for sm in own:
            m = IrModel._get(sm.model)
            rec.append('<record id="%s" model="ir.model"><field name="model">%s</field><field name="name">%s</field><field name="state">manual</field>%s</record>'
                       % (xid('model', sm.model), sm.model, escape(m.name),
                          '<field name="is_mail_thread" eval="True"/><field name="is_mail_activity" eval="True"/>' if getattr(m, 'is_mail_thread', False) else ''))
        flds = Fields.search([('model', 'in', sms.mapped('model')), ('state', '=', 'manual')])
        flds = flds.filtered(lambda f: f.model in own_models or f.name.startswith('x_studio_') or f.name == 'x_currency_id')
        plain = flds.filtered(lambda f: f.ttype not in ('many2one', 'one2many', 'many2many', 'monetary') and not f.compute and not f.related)
        links = flds.filtered(lambda f: f.ttype in ('many2one', 'many2many') and not f.related)
        money = flds.filtered(lambda f: f.ttype == 'monetary' and not f.compute)  # after the currency they are in
        late = flds - plain - links - money
        # tables before the formulas that add them up; related fields (the line's currency) before amounts on lines
        late = late.sorted(lambda f: (not f.related, f.ttype != 'one2many'))
        for group in (plain, links, late.filtered('related'), money, late.filtered(lambda f: not f.related)):
            for f in group:
                mref = xid('model', f.model) if f.model in own_models else (self.env['ir.model']._get(f.model).get_external_id().get(
                    self.env['ir.model']._get(f.model).id) or '')
                rec += self._field_xml(f, mref)
        for sm in own:
            rec.append('<record id="%s" model="ir.model.access"><field name="name">%s everyone</field><field name="model_id" ref="%s"/>'
                       '<field name="group_id" ref="base.group_user"/><field name="perm_read" eval="True"/><field name="perm_write" eval="True"/>'
                       '<field name="perm_create" eval="True"/><field name="perm_unlink" eval="True"/></record>' % (xid('access', sm.model), sm.model, xid('model', sm.model)))
        for sm in sms:
            for v in sm.view_ids:
                inherit = ''
                if v.inherit_id:
                    ext = v.inherit_id.get_external_id().get(v.inherit_id.id)
                    if not ext:
                        continue
                    inherit = '<field name="inherit_id" ref="%s"/>' % ext
                rec.append('<record id="%s" model="ir.ui.view"><field name="name">%s</field><field name="model">%s</field>%s'
                           '<field name="priority" eval="%d"/><field name="arch" type="xml">%s</field></record>'
                           % (xid('view', v.model, v.type), escape(v.name), v.model, inherit, v.priority, v.arch_db))
            if sm.action_id:
                a = sm.action_id
                rec.append('<record id="%s" model="ir.actions.act_window"><field name="name">%s</field><field name="res_model">%s</field>'
                           '<field name="view_mode">%s</field>%s</record>' % (
                               xid('action', sm.model), escape(a.name), a.res_model, a.view_mode,
                               '<field name="search_view_id" ref="%s"/>' % xid('view', sm.model, 'search') if a.search_view_id else ''))
        tops = own.filtered(lambda m: not m.parent_id and m.action_id).sorted('sequence')
        if tops:
            rec.append('<menuitem id="menu_root" name="%s" web_icon="%s,static/description/icon.svg" action="%s" sequence="%d"/>'
                       % (escape(app.name), tech, xid('action', tops[0].model), app.sequence))
            for seq, sm in enumerate(tops):
                rec.append('<menuitem id="%s" name="%s" parent="menu_root" action="%s" sequence="%d"/>'
                           % (xid('menu', sm.model), escape(sm.get_spec().get('plural') or sm.label), xid('action', sm.model), seq))
        autos = self.env['base.automation'].sudo().search([('model_id.model', 'in', list(own_models))])
        for a in autos:
            rec += self.env['sb.studio.automation']._export_xml(a, xid)
        data = '<?xml version="1.0" encoding="utf-8"?>\n<odoo>\n%s\n</odoo>\n' % '\n'.join(rec)
        deps = sorted({'mail', 'base_automation'} | {mod for sm in sms.filtered(lambda s: not s.own) for mod in self._modules_of(sm.model)})
        manifest = {'name': app.name, 'version': '19.0.1.0.0', 'summary': app.description or 'Built with Unio Studio',
                    'category': 'Productivity', 'license': 'LGPL-3', 'depends': deps, 'data': ['data/app.xml'],
                    'post_init_hook': 'post_init_hook', 'installable': True, 'application': True}
        defaults = {sm.model: bool(IrModel._get(sm.model).field_id.filtered(lambda f: f.name == 'x_currency_id' and not f.related)) for sm in own}
        init = ('def post_init_hook(env):\n'
                '    """Records are active by default; amounts are in the company currency."""\n'
                '    for model, money in %r.items():\n'
                "        if 'x_active' in env[model]._fields:\n"
                "            env['ir.default'].set(model, 'x_active', True)\n"
                '        if money:\n'
                "            env['ir.default'].set(model, 'x_currency_id', env.company.currency_id.id)\n" % defaults)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('%s/__manifest__.py' % tech, repr(manifest).replace(', \'', ',\n    \'').replace('{', '{\n    ', 1) + '\n')
            z.writestr('%s/__init__.py' % tech, init)
            z.writestr('%s/data/app.xml' % tech, data)
            z.writestr('%s/static/description/icon.svg' % tech, icon_svg(app.icon, app.color))
            z.writestr('%s/studio.json' % tech, json.dumps({'app': {'name': app.name, 'icon': app.icon, 'color': app.color},
                                                            'models': [sm.get_spec() for sm in sms if not sm.parent_id]}, indent=1))
        return '%s.zip' % tech, buf.getvalue()

    def _modules_of(self, model):
        """The modules an existing model comes from (dependencies of an export that adds to it)."""
        m = self.env['ir.model']._get(model)
        return [x for x in (m.modules or '').replace(' ', '').split(',') if x][:1] or ['base']
