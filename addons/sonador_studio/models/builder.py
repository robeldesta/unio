"""Turns a Studio spec into the real thing: the model, its fields, its screens (form, list, board, calendar, pivot,
graph, search), the menu and the access rights. Applying a spec again brings everything in line with it, so the
same code serves editing, undo (apply an older spec) and installing an exported or drafted app."""
import base64
import json
import re

from markupsafe import escape

from odoo import api, models
from odoo.exceptions import UserError

from .studio import require_studio

# Studio type: (Odoo field type, label shown in the editor)
TYPES = {
    'text': ('char', 'Text'), 'longtext': ('text', 'Long text'), 'richtext': ('html', 'Formatted text'),
    'number': ('integer', 'Whole number'), 'decimal': ('float', 'Decimal number'), 'money': ('monetary', 'Amount of money'),
    'date': ('date', 'Date'), 'datetime': ('datetime', 'Date and time'), 'checkbox': ('boolean', 'Yes / no'),
    'choice': ('selection', 'Choice'), 'stage': ('selection', 'Stages'), 'link': ('many2one', 'Link to a record'),
    'tags': ('many2many', 'Tags (several links)'), 'lines': ('one2many', 'Table of lines'), 'file': ('binary', 'File'),
    'image': ('binary', 'Image'), 'formula': ('float', 'Formula'),
}
# fields the builder adds by itself
HELPERS = {'x_active', 'x_currency_id', 'x_parent_id', 'x_sequence'}
NAME_RE = re.compile(r'^x_[a-z0-9_]{1,50}$')
FORMULA_RE = re.compile(r"^[\sx_a-z0-9.+\-*/(),<>=!%]*$")
FORMULA_FUNCS = {'round', 'min', 'max', 'sum', 'abs', 'len', 'if', 'else', 'and', 'or', 'not'}

ICONS = {
    'box': '<path d="M15 20 L25 15 L35 20 L35 32 L25 37 L15 32 Z M15 20 L25 25 L35 20 M25 25 L25 37"/>',
    'people': '<circle cx="20" cy="20" r="5"/><path d="M11 36 Q20 25 29 36"/><circle cx="32" cy="19" r="4"/><path d="M29 28 Q35 25 39 34"/>',
    'truck': '<rect x="10" y="17" width="18" height="13" rx="1"/><path d="M28 21 H35 L40 26 V30 H28 Z"/><circle cx="17" cy="33" r="3"/><circle cx="34" cy="33" r="3"/>',
    'tool': '<path d="M15 35 L29 21"/><circle cx="32" cy="18" r="5"/>',
    'calendar': '<rect x="13" y="15" width="24" height="22" rx="3"/><path d="M13 22 H37 M19 12 V17 M31 12 V17"/>',
    'clipboard': '<rect x="15" y="13" width="20" height="25" rx="3"/><path d="M20 21 H30 M20 27 H30 M20 33 H26"/>',
    'star': '<path d="M25 12 L29 21 L38 21 L31 27 L34 36 L25 31 L16 36 L19 27 L12 21 L21 21 Z"/>',
    'heart': '<path d="M25 36 C12 27 13 16 20 16 C23 16 25 19 25 19 C25 19 27 16 30 16 C37 16 38 27 25 36 Z"/>',
    'leaf': '<path d="M14 36 C14 20 26 14 37 13 C36 25 30 36 14 36 Z M14 36 L28 22"/>',
    'tag': '<path d="M13 13 H25 L37 25 L25 37 L13 25 Z"/><circle cx="19" cy="19" r="2"/>',
    'building': '<rect x="15" y="12" width="20" height="26" rx="1"/><path d="M20 18 H23 M27 18 H30 M20 24 H23 M27 24 H30 M23 38 V32 H27 V38"/>',
    'cart': '<path d="M11 14 H16 L20 29 H35 L38 19 H18"/><circle cx="21" cy="34" r="2.5"/><circle cx="33" cy="34" r="2.5"/>',
    'book': '<path d="M13 15 C18 13 22 14 25 17 C28 14 32 13 37 15 V35 C32 33 28 34 25 37 C22 34 18 33 13 35 Z M25 17 V37"/>',
    'chart': '<path d="M13 37 H38 M17 33 V25 M24 33 V17 M31 33 V22"/>',
    'flag': '<path d="M16 38 V12 M16 13 H34 L30 19 L34 25 H16"/>',
    'bell': '<path d="M17 31 V23 C17 14 33 14 33 23 V31 L36 34 H14 Z M22 37 C23 39 27 39 28 37"/>',
    'home': '<path d="M12 25 L25 13 L38 25 M16 22 V37 H34 V22 M22 37 V29 H28 V37"/>',
    'key': '<circle cx="19" cy="25" r="6"/><path d="M25 25 H38 M34 25 V30 M30 25 V29"/>',
    'pin': '<path d="M25 38 C18 29 15 25 15 20 A10 10 0 0 1 35 20 C35 25 32 29 25 38 Z"/><circle cx="25" cy="20" r="3.5"/>',
    'camera': '<rect x="11" y="17" width="28" height="19" rx="3"/><path d="M19 17 L22 13 H28 L31 17"/><circle cx="25" cy="26" r="5"/>',
    'gift': '<rect x="13" y="20" width="24" height="7"/><rect x="15" y="27" width="20" height="11"/><path d="M25 20 V38 M25 20 C19 12 14 18 25 20 C36 18 31 12 25 20"/>',
    'shield': '<path d="M25 12 L36 16 V25 C36 32 31 36 25 39 C19 36 14 32 14 25 V16 Z"/>',
    'bolt': '<path d="M28 11 L16 27 H25 L22 39 L34 22 H25 Z"/>',
    'cup': '<path d="M14 18 H32 V28 C32 33 28 36 23 36 C18 36 14 33 14 28 Z M32 21 H35 C38 21 38 28 35 28 H32"/>',
    'chat': '<path d="M12 16 H38 V31 H22 L15 37 V31 H12 Z"/>',
}


def icon_svg(icon, color):
    color = color if re.fullmatch(r'#[0-9A-Fa-f]{6}', color or '') else '#2D6BFF'
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 50 50">'
            '<rect width="50" height="50" rx="12" fill="%s"/>'
            '<g fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">%s</g></svg>'
            % (color, ICONS.get(icon, ICONS['box'])))


def slug(label, prefix='x_', size=40):
    s = re.sub(r'[^a-z0-9]+', '_', (label or '').lower()).strip('_')[:size].strip('_') or 'item'
    if s[0].isdigit():
        s = 'n' + s
    return prefix + s


def q(v):
    """A value for an XML attribute."""
    return escape(str(v))


class SbStudioBuilder(models.AbstractModel):
    _name = 'sb.studio.builder'
    _description = 'Studio builder'

    # ------------------------------------------------------------------ checking a spec
    def _check_spec(self, spec, own=True):
        names = set()
        for f in spec.get('fields', []):
            if f.get('type') not in TYPES:
                raise UserError(self.env._('Unknown kind of field: %s', f.get('type')))
            if not NAME_RE.match(f.get('name') or ''):
                raise UserError(self.env._('"%s" is not a valid technical name (x_ followed by small letters, digits and _).', f.get('name')))
            if not own and not f['name'].startswith('x_studio_'):
                raise UserError(self.env._('Fields added to an existing app are named x_studio_...'))
            if f['name'] in names or f['name'] in HELPERS:
                raise UserError(self.env._('Two fields are named %s.', f['name']))
            names.add(f['name'])
            if not (f.get('label') or '').strip():
                raise UserError(self.env._('Every field needs a label.'))
            if f['type'] in ('link', 'tags') and (f.get('relation') or '') not in self.env:
                raise UserError(self.env._('"%(f)s" links to %(m)s, which does not exist.', f=f['label'], m=f.get('relation') or '?'))
            if f['type'] in ('choice', 'stage') and not f.get('options'):
                raise UserError(self.env._('"%s" needs at least one choice.', f['label']))
            if f['type'] == 'formula':
                self._formula_code(spec, f)
        return names

    def _formula_code(self, spec, f):
        """A formula like "x_qty * x_price" or "sum(x_lines.x_amount)" as the field's compute code and dependencies."""
        expr = (f.get('formula') or '').strip()
        if not expr or not FORMULA_RE.match(expr) or '__' in expr:
            raise UserError(self.env._('The formula of "%s" can only use fields, numbers and + - * / ( ).', f['label']))
        known = {x['name']: x for x in spec.get('fields', [])}
        deps = []
        for word in set(re.findall(r'[a-z_][a-z0-9_.]*', expr)):
            if word in FORMULA_FUNCS:
                continue
            base, _dot, sub = word.partition('.')
            if base not in known or base == f['name']:
                raise UserError(self.env._('The formula of "%(f)s" uses "%(w)s", which is not a field of this record.', f=f['label'], w=word))
            if sub:
                child = {x['name'] for x in known[base].get('lines', {}).get('fields', [])} | {'x_name'}
                if known[base]['type'] != 'lines' or sub not in child:
                    raise UserError(self.env._('The formula of "%(f)s" uses "%(w)s", which is not a column of that table.', f=f['label'], w=word))
            deps.append(word)
        py = re.sub(r'\b(x_[a-z0-9_]+)\.(x_[a-z0-9_]+)\b', r"record.\1.mapped('\2')", expr)
        py = re.sub(r"(?<![.'])\b(x_[a-z0-9_]+)\b(?!')", r'record.\1', py)
        code = "for record in self:\n    try:\n        record['%s'] = %s\n    except (ZeroDivisionError, TypeError):\n        record['%s'] = 0.0" % (f['name'], py, f['name'])
        return code, ','.join(sorted(set(deps)))

    # ------------------------------------------------------------------ fields
    def _field_vals(self, model_rec, spec, f):
        ttype = TYPES[f['type']][0]
        vals = {'name': f['name'], 'field_description': f['label'].strip(), 'ttype': ttype, 'model_id': model_rec.id, 'state': 'manual',
                'required': bool(f.get('required')) and f['type'] not in ('formula', 'checkbox'), 'help': f.get('help') or False,
                'copied': f['type'] not in ('lines',), 'store': True}
        if 'tracking' in self.env['ir.model.fields']._fields and spec.get('chatter') and f['type'] in ('stage', 'choice', 'link', 'money', 'date'):
            vals['tracking'] = 1
        if f['type'] in ('link', 'tags'):
            vals['relation'] = f['relation']
        if f['type'] == 'link':
            # a record that must be linked keeps what it links to from being deleted
            vals['on_delete'] = 'restrict' if vals['required'] else 'set null'
        if f['type'] == 'tags':
            table = model_rec.model.replace('.', '_')
            vals.update(relation_table=('x_%s_%s_rel' % (table[:24], f['name'][2:26])), column1='src_id', column2='dst_id')
        if f['type'] == 'money':
            vals['currency_field'] = spec.get('currency_field') or 'x_currency_id'
        if f['type'] == 'stage':
            vals['group_expand'] = True
        if f['type'] == 'formula':
            vals['compute'], vals['depends'] = self._formula_code(spec, f)
            vals['readonly'] = True
        if f['type'] == 'lines':
            vals.update(relation=self._line_model(model_rec.model, f), relation_field='x_parent_id')
        return vals

    def _line_model(self, model, f):
        base = model if model.startswith('x_') else 'x_' + model.replace('.', '_')
        return f.get('relation') or ('%s_%s' % (base, f['name'][2:].replace('studio_', '')))[:60]

    def _sync_selection(self, field, options):
        Sel = self.env['ir.model.fields.selection'].sudo()
        wanted = [(o['value'], o['label']) for o in options]
        have = {s.value: s for s in field.selection_ids}
        for seq, (value, label) in enumerate(wanted):
            if value in have:
                have[value].write({'name': label, 'sequence': seq})
            else:
                Sel.create({'field_id': field.id, 'value': value, 'name': label, 'sequence': seq})
        gone = field.selection_ids.filtered(lambda s: s.value not in dict(wanted))
        if gone:
            gone.unlink()

    def _sync_fields(self, model_rec, spec, owned_prefix):
        Fields = self.env['ir.model.fields'].sudo()
        existing = {f.name: f for f in Fields.search([('model_id', '=', model_rec.id), ('state', '=', 'manual')])}
        # simple fields first, formulas last (they read the others)
        for f in sorted(spec.get('fields', []), key=lambda x: x['type'] == 'formula'):
            vals = self._field_vals(model_rec, spec, f)
            if f['type'] == 'lines':
                self._sync_lines(model_rec, spec, f)
            cur = existing.get(f['name'])
            if cur:
                if cur.ttype != vals['ttype'] or (vals.get('relation') and cur.relation != vals['relation']):
                    raise UserError(self.env._('"%s" cannot change its kind: remove it and add a new field.', f['label']))
                change = {k: v for k, v in vals.items() if k not in ('name', 'model_id', 'ttype', 'state', 'relation', 'relation_table',
                                                                        'column1', 'column2', 'relation_field')
                          and cur[k] != v}
                if change:
                    cur.write(change)
            else:
                cur = Fields.create(vals)
            if f['type'] in ('choice', 'stage'):
                self._sync_selection(cur, f['options'])
            if f['type'] == 'stage' and f.get('options'):
                # new records start at the first stage
                self.env['ir.default'].sudo().set(model_rec.model, f['name'], f['options'][0]['value'])
        # fields taken out of the spec go (with their data: the history keeps the description to put them back)
        wanted = {f['name'] for f in spec.get('fields', [])} | HELPERS
        gone = Fields.browse([f.id for n, f in existing.items() if n.startswith(owned_prefix) and n not in wanted])
        if gone:
            self._drop_from_views(model_rec.model, gone.mapped('name'))
            gone.unlink()

    def _drop_from_views(self, model, names):
        """Studio screens are rewritten afterwards; any other view still naming the field would block its removal."""
        for v in self.env['ir.ui.view'].sudo().search([('model', '=', model), ('key', '=like', 'studio.%')]):
            if any(n in (v.arch_db or '') for n in names):
                v.unlink()

    def _helper(self, model_rec, name, vals):
        Fields = self.env['ir.model.fields'].sudo()
        if not Fields.search_count([('model_id', '=', model_rec.id), ('name', '=', name)]):
            Fields.create(dict(vals, name=name, model_id=model_rec.id, state='manual'))

    def _sync_lines(self, parent_rec, parent_spec, f):
        """A table field: its lines are a model of their own, linked to the record and deleted with it."""
        lines = f.setdefault('lines', {})
        lines.setdefault('fields', [{'name': 'x_name', 'label': 'Description', 'type': 'text'}])
        model = self._line_model(parent_rec.model, f)
        child_spec = {'model': model, 'label': '%s line' % parent_spec.get('label', ''), 'own': True, 'fields': lines['fields'],
                      'list': lines.get('list') or [x['name'] for x in lines['fields']]}
        self._check_spec(child_spec)
        rec = self._ensure_model(child_spec, chatter=False)
        self._helper(rec, 'x_parent_id', {'field_description': parent_spec.get('label') or 'Parent', 'ttype': 'many2one',
                                          'relation': parent_rec.model, 'on_delete': 'cascade', 'required': True, 'index': True})
        self._helper(rec, 'x_sequence', {'field_description': 'Sequence', 'ttype': 'integer'})
        if any(x['type'] == 'money' for x in lines['fields']):
            self._money_helper(rec, related=True)
        self._sync_fields(rec, child_spec, 'x_')
        self._access(model)
        sm = self.env['sb.studio.model'].sudo().search([('model', '=', model)])
        parent_sm = self.env['sb.studio.model'].sudo().search([('model', '=', parent_rec.model)])
        vals = {'model': model, 'label': child_spec['label'], 'own': True, 'spec': json.dumps(child_spec), 'parent_id': parent_sm.id,
                'app_id': parent_sm.app_id.id}
        sm.write(vals) if sm else sm.create(vals)

    def _money_helper(self, rec, related=False):
        if related:
            self._helper(rec, 'x_currency_id', {'field_description': 'Currency', 'ttype': 'many2one', 'relation': 'res.currency',
                                                'related': 'x_parent_id.x_currency_id', 'store': False, 'readonly': True})
        else:
            self._helper(rec, 'x_currency_id', {'field_description': 'Currency', 'ttype': 'many2one', 'relation': 'res.currency',
                                                'on_delete': 'restrict'})
            self.env['ir.default'].sudo().set(rec.model, 'x_currency_id', self.env.company.currency_id.id)

    def _has_money(self, spec):
        """Amounts on the record or in one of its tables: the record then carries the currency."""
        return any(f['type'] == 'money' or (f['type'] == 'lines' and any(c['type'] == 'money' for c in f.get('lines', {}).get('fields', [])))
                   for f in spec.get('fields', []))

    # ------------------------------------------------------------------ models
    def _ensure_model(self, spec, chatter):
        IrModel = self.env['ir.model'].sudo()
        rec = IrModel.search([('model', '=', spec['model'])])
        if not rec:
            if not re.match(r'^x_[a-z0-9_]{1,55}$', spec['model']):
                raise UserError(self.env._('"%s" is not a valid technical name for a model.', spec['model']))
            name_field = next((f for f in spec.get('fields', []) if f['name'] == 'x_name'), {'label': 'Name'})
            rec = IrModel.create({'model': spec['model'], 'name': spec.get('label') or spec['model'], 'state': 'manual',
                                  'field_id': [(0, 0, {'name': 'x_name', 'field_description': name_field['label'], 'ttype': 'char',
                                                       'state': 'manual', 'required': bool(name_field.get('required'))})]})
        elif rec.name != spec.get('label') and spec.get('label'):
            rec.name = spec['label']
        if chatter and 'is_mail_thread' in rec._fields and not rec.is_mail_thread:
            rec.write({'is_mail_thread': True, 'is_mail_activity': True})
        return rec

    def _access(self, model):
        Access = self.env['ir.model.access'].sudo()
        m = self.env['ir.model']._get(model)
        if not Access.search_count([('model_id', '=', m.id)]):
            Access.create({'name': '%s (everyone)' % model, 'model_id': m.id, 'group_id': self.env.ref('base.group_user').id,
                           'perm_read': True, 'perm_write': True, 'perm_create': True, 'perm_unlink': True})

    # ------------------------------------------------------------------ screens of a model made in Studio
    def _fdef(self, spec, name):
        if name in ('x_name',):
            return next((f for f in spec['fields'] if f['name'] == 'x_name'), {'name': 'x_name', 'type': 'text', 'label': 'Name'})
        return next((f for f in spec['fields'] if f['name'] == name), None)

    def _form_field(self, spec, name, in_group=True):
        f = self._fdef(spec, name)
        if not f:
            return ''
        a = ''
        if f['type'] == 'tags':
            a = ' widget="many2many_tags"'
        elif f['type'] == 'image':
            a = ' widget="image" options="{\'size\': [0, 120]}"'
        elif f['type'] == 'choice' and f.get('widget') == 'radio':
            a = ' widget="radio"'
        elif f['type'] == 'checkbox' and f.get('widget') == 'toggle':
            a = ' widget="boolean_toggle"'
        elif f['type'] == 'link' and f.get('relation') == 'res.users':
            a = ' widget="many2one_avatar_user"'
        if f.get('placeholder'):
            a += ' placeholder="%s"' % q(f['placeholder'])
        if f['type'] == 'lines':
            child = f.get('lines', {})
            cols = ''.join(self._list_col(child, n) for n in (child.get('list') or [x['name'] for x in child.get('fields', [])]))
            body = '<field name="%s" nolabel="1" colspan="2"><list editable="bottom"><field name="x_sequence" widget="handle"/>%s%s</list></field>' % (
                f['name'], cols, '<field name="x_currency_id" column_invisible="1"/>' if any(x['type'] == 'money' for x in child.get('fields', [])) else '')
            return '<separator string="%s" colspan="2"/>%s' % (q(f['label']), body) if in_group else body
        if f['type'] == 'richtext' and in_group:
            return '<separator string="%s" colspan="2"/><field name="%s" nolabel="1" colspan="2"%s/>' % (q(f['label']), f['name'], a)
        return '<field name="%s"%s/>' % (f['name'], a)

    def _list_col(self, spec, name):
        f = self._fdef(spec, name)
        if not f or f['type'] in ('lines', 'richtext', 'file', 'image'):
            return ''
        a = ''
        if f['type'] in ('money', 'decimal', 'formula', 'number') and f.get('total', f['type'] in ('money', 'formula')):
            a = ' sum="Total"'
        if f['type'] == 'tags':
            a = ' widget="many2many_tags"'
        if f['type'] == 'stage':
            a = ' widget="badge"'
        if f['type'] == 'link' and f.get('relation') == 'res.users':
            a = ' widget="many2one_avatar_user"'
        return '<field name="%s"%s/>' % (f['name'], a)

    def _views(self, spec):
        """The arch of every screen of a Studio model: {view type: arch}."""
        label = q(spec.get('label') or spec['model'])
        form = spec.get('form') or {}
        title = form.get('title') or 'x_name'
        status = form.get('status')
        money = self._has_money(spec)
        placed = {title, status}
        out = {}
        # form
        parts = ['<form string="%s">' % label]
        if status:
            parts.append('<header><field name="%s" widget="statusbar" options="{\'clickable\': \'1\'}"/></header>' % status)
        parts.append('<sheet>')
        parts.append('<widget name="web_ribbon" title="Archived" bg_color="text-bg-danger" invisible="x_active"/>')
        if form.get('image'):
            parts.append('<field name="%s" widget="image" class="oe_avatar"/>' % form['image'])
            placed.add(form['image'])
        tf = self._fdef(spec, title) or {'label': 'Name'}
        parts.append('<div class="oe_title"><label for="%s" string="%s"/><h1><field name="%s" placeholder="%s"/></h1></div>'
                     % (title, q(tf['label']), title, q(tf.get('placeholder') or '')))
        for sec in form.get('sections') or []:
            cols = [c for c in sec.get('columns') or [] if c] or [[]]
            inner = ''.join('<group>%s</group>' % ''.join(self._form_field(spec, n) for n in col) for col in cols)
            parts.append('<group%s>%s</group>' % (' string="%s"' % q(sec['label']) if sec.get('label') else '', inner))
            placed.update(n for col in cols for n in col)
        tabs = form.get('tabs') or []
        if tabs:
            parts.append('<notebook>')
            for t in tabs:
                body = ''
                group = []
                for n in t.get('fields') or []:
                    f = self._fdef(spec, n)
                    if f and f['type'] in ('lines', 'richtext'):
                        if group:
                            body += '<group>%s</group>' % ''.join(group)
                            group = []
                        body += self._form_field(spec, n, in_group=False)
                    else:
                        group.append(self._form_field(spec, n))
                    placed.add(n)
                if group:
                    body += '<group>%s</group>' % ''.join(group)
                parts.append('<page string="%s">%s</page>' % (q(t.get('label') or 'Details'), body))
            parts.append('</notebook>')
        # fields not placed anywhere still show, at the end, so nothing is ever lost from sight
        rest = [f['name'] for f in spec['fields'] if f['name'] not in placed and f['name'] != 'x_name']
        if rest:
            parts.append('<group>%s</group>' % ''.join(self._form_field(spec, n) for n in rest))
        if money:
            parts.append('<field name="x_currency_id" invisible="1"/>')
        parts.append('<field name="x_active" invisible="1"/></sheet>')
        if spec.get('chatter'):
            parts.append('<chatter/>')
        parts.append('</form>')
        out['form'] = ''.join(parts)
        # list
        cols = spec.get('list') or [title] + [f['name'] for f in spec['fields'] if f['name'] != title][:5]
        out['list'] = '<list string="%s"%s>%s%s</list>' % (label, ' multi_edit="1"', ''.join(self._list_col(spec, n) for n in cols),
                                                            '<field name="x_currency_id" column_invisible="1"/>' if money else '')
        # board (kanban), grouped by stage when there are stages
        kb = spec.get('kanban') or {}
        ktitle = kb.get('title') or title
        kfields = kb.get('fields') or [n for n in cols if n not in (ktitle, status)][:3]
        card = '<field name="%s" class="fw-bold fs-5"/>' % ktitle
        for n in kfields:
            f = self._fdef(spec, n)
            if not f or f['type'] in ('lines', 'richtext', 'file'):
                continue
            if f['type'] == 'tags':
                card += '<field name="%s" widget="many2many_tags"/>' % n
            elif f['type'] == 'image':
                card += '<field name="%s" widget="image" options="{\'size\': [0, 60]}"/>' % n
            elif f['type'] == 'link' and f.get('relation') == 'res.users':
                card += '<footer><field name="%s" widget="many2one_avatar_user" class="ms-auto"/></footer>' % n
            else:
                card += '<div><span class="text-muted">%s: </span><field name="%s"/></div>' % (q(f['label']), n)
        out['kanban'] = '<kanban%s>%s<templates><t t-name="card">%s</t></templates></kanban>' % (
            ' default_group_by="%s"' % status if status else '', '<field name="x_currency_id"/>' if money else '', card)
        # search: text and links to search in, filters on stages and dates, grouping
        s = ['<search>', '<field name="%s"/>' % title]
        for f in spec['fields']:
            if f['type'] in ('text', 'link', 'tags', 'longtext') and f['name'] != title:
                s.append('<field name="%s"/>' % f['name'])
        for f in spec['fields']:
            if f['type'] == 'checkbox':
                s.append('<filter name="f_%s" string="%s" domain="[(\'%s\', \'=\', True)]"/>' % (f['name'], q(f['label']), f['name']))
        for f in spec['fields']:
            if f['type'] in ('date', 'datetime'):
                s.append('<filter name="f_%s" string="%s" date="%s"/>' % (f['name'], q(f['label']), f['name']))
        s.append('<separator/><filter name="archived" string="Archived" domain="[(\'x_active\', \'=\', False)]"/>')
        groups = [f for f in spec['fields'] if f['type'] in ('stage', 'choice', 'link')]
        if groups:
            s.append('<group>%s</group>' % ''.join('<filter name="g_%s" string="%s" context="{\'group_by\': \'%s\'}"/>' % (f['name'], q(f['label']), f['name'])
                                                   for f in groups))
        s.append('</search>')
        out['search'] = ''.join(s)
        # calendar, pivot and graph when there is something to show in them
        cal = spec.get('calendar') or next((f['name'] for f in spec['fields'] if f['type'] in ('date', 'datetime')), None)
        if cal and self._fdef(spec, cal):
            out['calendar'] = '<calendar string="%s" date_start="%s" mode="month"><field name="%s"/></calendar>' % (label, cal, title)
        measures = [f['name'] for f in spec['fields'] if f['type'] in ('money', 'decimal', 'number', 'formula')]
        row = status or next((f['name'] for f in spec['fields'] if f['type'] in ('choice', 'link')), None)
        if row or measures:
            out['pivot'] = '<pivot string="%s">%s%s</pivot>' % (label, '<field name="%s" type="row"/>' % row if row else '',
                                                                  ''.join('<field name="%s" type="measure"/>' % m for m in measures))
            out['graph'] = '<graph string="%s">%s%s</graph>' % (label, '<field name="%s"/>' % row if row else '',
                                                                 ''.join('<field name="%s" type="measure"/>' % m for m in measures[:1]))
        return out

    def _sync_views(self, sm, spec):
        View = self.env['ir.ui.view'].sudo()
        arches = self._views(spec)
        keep = View
        for vtype, arch in arches.items():
            key = 'studio.%s.%s' % (spec['model'], vtype)
            v = View.search([('key', '=', key)], limit=1)
            vals = {'name': 'Studio: %s %s' % (spec.get('label'), vtype), 'model': spec['model'], 'type': vtype, 'arch_db': arch,
                    'key': key, 'mode': 'primary', 'priority': 16}
            if v:
                v.write({'arch_db': arch, 'name': vals['name']})
            else:
                v = View.create(vals)
            keep |= v
        (sm.view_ids - keep).unlink()
        sm.view_ids = [(6, 0, keep.ids)]
        order = ['kanban', 'list', 'form', 'calendar', 'pivot', 'graph'] if spec.get('default_view') == 'kanban' else \
            ['list', 'kanban', 'form', 'calendar', 'pivot', 'graph']
        if spec.get('default_view') == 'calendar' and 'calendar' in arches:
            order.remove('calendar')
            order.insert(0, 'calendar')
        mode = ','.join(t for t in order if t in arches)
        Act = self.env['ir.actions.act_window'].sudo()
        avals = {'name': spec.get('plural') or spec.get('label'), 'res_model': spec['model'], 'view_mode': mode,
                 'search_view_id': keep.filtered(lambda v: v.type == 'search')[:1].id,
                 'help': '<p class="o_view_nocontent_smiling_face">%s</p>' % escape(spec.get('empty_help') or 'Add the first %s' % (spec.get('label') or '').lower())}
        if sm.action_id:
            sm.action_id.write(avals)
        else:
            sm.action_id = Act.create(avals)
        # views in the order of the action
        sm.action_id.view_ids.unlink()
        for seq, t in enumerate(mode.split(',')):
            v = keep.filtered(lambda x, t=t: x.type == t)[:1]
            if v:
                self.env['ir.actions.act_window.view'].sudo().create({'act_window_id': sm.action_id.id, 'view_mode': t, 'view_id': v.id, 'sequence': seq})

    # ------------------------------------------------------------------ app menu
    def _sync_app_menu(self, app):
        Menu = self.env['ir.ui.menu'].sudo()
        icon = base64.b64encode(icon_svg(app.icon, app.color).encode())
        tops = app.model_ids.filtered(lambda m: m.own and not m.parent_id and m.action_id).sorted('sequence')
        vals = {'name': app.name, 'web_icon_data': icon, 'sequence': app.sequence,
                'action': ('ir.actions.act_window,%d' % tops[0].action_id.id) if tops else False}
        if app.menu_id:
            app.menu_id.write(vals)
        else:
            app.menu_id = Menu.create(vals)
        for seq, sm in enumerate(tops):
            mvals = {'name': sm.get_spec().get('plural') or sm.label, 'parent_id': app.menu_id.id, 'sequence': seq,
                     'action': 'ir.actions.act_window,%d' % sm.action_id.id}
            if sm.menu_id:
                sm.menu_id.write(mvals)
            else:
                sm.menu_id = Menu.create(mvals)
        app.active = True

    # ------------------------------------------------------------------ applying a whole spec
    @api.model
    def apply(self, sm, spec, summary):
        """Bring the model in line with spec, keep it as the latest version, and refresh the app's menu."""
        require_studio(self.env)
        if spec.get('own', sm.own):
            self._check_spec(spec)
            rec = self._ensure_model(spec, chatter=spec.get('chatter', True))
            self._helper(rec, 'x_active', {'field_description': 'Active', 'ttype': 'boolean'})
            self.env['ir.default'].sudo().set(spec['model'], 'x_active', True)
            if self._has_money(spec):
                self._money_helper(rec)
            self._sync_fields(rec, spec, 'x_')
            self._access(spec['model'])
            sm.write({'spec': json.dumps(spec), 'label': spec.get('label'), 'own': True})
            self.env.flush_all()
            self.env.registry.clear_cache()
            self._sync_views(sm, spec)
        else:
            self.env['sb.studio.extend']._apply(sm, spec)
        self.env['sb.studio.version']._record(sm, summary)
        if sm.app_id:
            self._sync_app_menu(sm.app_id)
        return sm
