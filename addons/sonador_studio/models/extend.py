"""Studio on a model that came with an app: new fields, put where they belong on its form, list and search; existing
fields hidden or renamed. All of it is one extension view per screen, so the app's own screens stay untouched."""
import json

from lxml import etree

from odoo import api, models
from odoo.exceptions import UserError

from .builder import q


class SbStudioExtend(models.AbstractModel):
    _name = 'sb.studio.extend'
    _description = 'Studio additions to an existing model'

    def _primary(self, model, vtype):
        View = self.env['ir.ui.view'].sudo()
        v = View.search([('model', '=', model), ('type', '=', vtype), ('mode', '=', 'primary'), ('key', 'not like', 'studio.%')],
                        order='priority, id', limit=1)
        return v

    def _apply(self, sm, spec):
        B = self.env['sb.studio.builder']
        spec['own'] = False
        model = spec['model']
        if model not in self.env:
            raise UserError(self.env._('There is no model %s.', model))
        rec = self.env['ir.model']._get(model)
        M = self.env[model]
        if any(f['type'] == 'money' for f in spec.get('fields', [])):
            if 'currency_id' in M._fields and M._fields['currency_id'].comodel_name == 'res.currency':
                spec['currency_field'] = 'currency_id'
            else:
                B._money_helper(rec)
                spec['currency_field'] = 'x_currency_id'
        B._check_spec(spec, own=False)
        B._sync_fields(rec, spec, 'x_studio_')
        sm.write({'spec': json.dumps(spec), 'own': False, 'label': sm.label or rec.name})
        self.env.flush_all()
        self.env.registry.clear_cache()
        self._sync_views(sm, spec)

    def _form_arch(self, spec, base_arch):
        B = self.env['sb.studio.builder']
        doc = etree.fromstring(base_arch)
        names = {n.get('name') for n in doc.iter('field')}
        parts = []
        placed = set()
        for p in spec.get('place') or []:
            if p.get('field') not in {f['name'] for f in spec['fields']}:
                continue
            anchor = p.get('after') or p.get('before')
            if anchor not in names:
                continue
            parts.append('<xpath expr="//field[@name=\'%s\']" position="%s">%s</xpath>' % (anchor, 'after' if p.get('after') else 'before',
                                                                                         B._form_field(spec, p['field'], in_group=False)))
            placed.add(p['field'])
        rest = [f['name'] for f in spec['fields'] if f['name'] not in placed]
        if rest:
            body = '<group string="More details">%s</group>' % ''.join(B._form_field(spec, n) for n in rest)
            where = '//sheet' if doc.find('.//sheet') is not None else '/form'
            parts.append('<xpath expr="%s" position="inside">%s</xpath>' % (where, body))
        for n in spec.get('hide') or []:
            if n in names:
                parts.append('<xpath expr="//field[@name=\'%s\']" position="attributes"><attribute name="invisible">1</attribute></xpath>' % n)
        for n, label in (spec.get('relabel') or {}).items():
            if n in names and label:
                parts.append('<xpath expr="//field[@name=\'%s\']" position="attributes"><attribute name="string">%s</attribute></xpath>' % (n, q(label)))
        if spec.get('currency_field') == 'x_currency_id':
            parts.append('<xpath expr="/form" position="inside"><field name="x_currency_id" invisible="1"/></xpath>')
        return '<data>%s</data>' % ''.join(parts) if parts else ''

    def _sync_views(self, sm, spec):
        B = self.env['sb.studio.builder']
        View = self.env['ir.ui.view'].sudo()
        model = spec['model']
        keep = View
        for vtype in ('form', 'list', 'search'):
            base = self._primary(model, vtype)
            if not base:
                continue
            arch = ''
            if vtype == 'form':
                full = self.env[model].sudo().get_view(base.id, 'form')['arch']
                arch = self._form_arch(spec, full)
            elif vtype == 'list':
                cols = ''.join(B._list_col(spec, n).replace('/>', ' optional="show"/>') for n in spec.get('list') or [])
                if cols:
                    root = etree.fromstring(base.arch_db).tag
                    arch = '<xpath expr="/%s" position="inside">%s%s</xpath>' % (
                        root, cols, '<field name="x_currency_id" column_invisible="1"/>' if spec.get('currency_field') == 'x_currency_id' else '')
            else:
                fl = ''.join('<field name="%s"/>' % f['name'] for f in spec['fields'] if f['type'] in ('text', 'link', 'tags'))
                if fl:
                    arch = '<xpath expr="/search" position="inside">%s</xpath>' % fl
            key = 'studio.%s.%s' % (model, vtype)
            v = View.search([('key', '=', key)], limit=1)
            if not arch:
                continue
            if v:
                v.write({'arch_db': arch, 'inherit_id': base.id})
            else:
                v = View.create({'name': 'Studio: %s %s' % (model, vtype), 'model': model, 'type': vtype, 'inherit_id': base.id,
                                 'mode': 'extension', 'arch_db': arch, 'key': key, 'priority': 99})
            keep |= v
        (sm.view_ids - keep).unlink()
        sm.view_ids = [(6, 0, keep.ids)]

    @api.model
    def outline(self, model):
        """The current form of an existing model as sections of fields, for the editor to show and drop new fields into."""
        base = self._primary(model, 'form')
        if not base:
            return []
        arch = self.env[model].sudo().get_view(base.id, 'form')['arch']
        doc = etree.fromstring(arch)
        M = self.env[model]
        out = []
        seen = set()
        sm = self.env['sb.studio.model'].search([('model', '=', model)], limit=1)
        hidden = set(sm.get_spec().get('hide') or []) if sm else set()

        def walk(node, section):
            for ch in node:
                if not isinstance(ch.tag, str):
                    continue
                if ch.tag == 'page':
                    walk(ch, {'label': ch.get('string') or 'Page', 'fields': []})
                    continue
                if ch.tag == 'group' and ch.get('string'):
                    walk(ch, {'label': ch.get('string'), 'fields': []})
                    continue
                cls = ch.get('class') or ''
                if ch.get('name') == 'button_box' or 'oe_button_box' in cls or 'alert' in cls or ch.tag in ('header', 'list', 'kanban', 'chatter'):
                    continue  # smart buttons, warnings, the status bar and embedded lists are not where fields go
                if ch.tag == 'field':
                    n = ch.get('name')
                    # fields that are hidden, or only appear in some cases, are left out
                    inv = ch.get('invisible') not in (None, '0', 'False') or ch.get('column_invisible') not in (None, '0', 'False')
                    if n in M._fields and (not inv or n in hidden) and n not in seen:
                        seen.add(n)
                        if section not in out:
                            out.append(section)
                        section['fields'].append({'name': n, 'label': ch.get('string') or M._fields[n].string, 'studio': n.startswith('x_studio_')})
                    continue
                walk(ch, section)

        walk(doc, {'label': 'Top', 'fields': []})
        return out
