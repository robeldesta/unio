"""Automations in plain terms: "when <something happens> [and <condition>], <do these things>", stored as Odoo's own
automation rules so they run with the rest of the system.

    {"name": "Remind the host", "trigger": "stage", "field": "x_stage", "value": "arrived",
     "conditions": [{"field": "x_vip", "op": "=", "value": true}],
     "actions": [{"do": "activity", "user_field": "x_host", "summary": "Meet your visitor", "days": 0},
                 {"do": "set", "field": "x_badge", "value": "printed"},
                 {"do": "email", "to_field": "x_contact", "subject": "Welcome", "body": "Hello {x_name}, ..."},
                 {"do": "note", "body": "Visitor arrived at {x_arrival}"}]}

trigger: "create" (a record is created), "change" (field changes), "stage" (field becomes value),
"date" (days before / after a date field: "days", "when": "before" | "after").
"""
import json
import re

from odoo import api, fields, models
from odoo.exceptions import UserError

from .studio import require_studio

OPS = {'=', '!=', '>', '<', '>=', '<=', 'ilike', 'set', 'not set'}


class SbStudioAutomation(models.AbstractModel):
    _name = 'sb.studio.automation'
    _description = 'Studio automations'

    def _field(self, model, name):
        f = self.env['ir.model.fields'].sudo().search([('model', '=', model), ('name', '=', name)], limit=1)
        if not f:
            raise UserError(self.env._('%(m)s has no field %(f)s.', m=model, f=name))
        return f

    def _domain(self, model, conds):
        dom = []
        for c in conds or []:
            f = self._field(model, c['field'])
            op = c.get('op') or '='
            if op not in OPS:
                raise UserError(self.env._('Unknown comparison %s.', op))
            if op == 'set':
                dom.append((f.name, '!=', False))
            elif op == 'not set':
                dom.append((f.name, '=', False))
            else:
                dom.append((f.name, op, c.get('value')))
        return dom

    def _text_code(self, model, text):
        """Text with {field} placeholders as a Python expression joining literals and the record's values."""
        M = self.env[model]
        parts = []
        for i, piece in enumerate(re.split(r'\{(x_[a-z0-9_]+|[a-z_][a-z0-9_]*)\}', text or '')):
            if i % 2 == 0:
                if piece:
                    parts.append(repr(piece))
            else:
                f = M._fields.get(piece)
                if not f:
                    raise UserError(self.env._('There is no field %s to put in the text.', piece))
                if f.type in ('many2one',):
                    parts.append("(record.%s.display_name or '')" % piece)
                elif f.type in ('many2many', 'one2many'):
                    parts.append("', '.join(record.%s.mapped('display_name'))" % piece)
                else:
                    parts.append("str(record.%s or '')" % piece)
        return ' + '.join(parts) or "''"

    def _actions(self, auto, model, actions):
        Srv = self.env['ir.actions.server'].sudo()
        auto.action_server_ids.unlink()
        mid = self.env['ir.model']._get(model).id
        for seq, a in enumerate(actions or []):
            base = {'name': a.get('label') or a['do'], 'model_id': mid, 'base_automation_id': auto.id, 'usage': 'base_automation', 'sequence': seq}
            if a['do'] == 'set':
                f = self._field(model, a['field'])
                val = a.get('value')
                if f.ttype == 'boolean':
                    val = 'True' if val else 'False'
                    Srv.create(dict(base, state='object_write', update_path=f.name, evaluation_type='equation', value=val))
                elif a.get('value') == 'today' and f.ttype in ('date', 'datetime'):
                    Srv.create(dict(base, state='object_write', update_path=f.name, evaluation_type='equation',
                                    value='datetime.date.today()' if f.ttype == 'date' else 'datetime.datetime.now()'))
                else:
                    Srv.create(dict(base, state='object_write', update_path=f.name, evaluation_type='value', value=str(val if val is not None else '')))
            elif a['do'] == 'activity':
                user = 'env.user.id'
                if a.get('user_field'):
                    f = self._field(model, a['user_field'])
                    if f.relation != 'res.users':
                        raise UserError(self.env._('Activities are for users: pick a field linking to a user.'))
                    user = '(record.%s.id or env.user.id)' % f.name
                elif a.get('user_id'):
                    user = str(int(a['user_id']))
                code = ("record.activity_schedule('mail.mail_activity_data_todo', date_deadline=datetime.date.today() + "
                        "datetime.timedelta(days=%d), summary=%s, user_id=%s)" % (int(a.get('days') or 0), self._text_code(model, a.get('summary') or 'To do'), user))
                Srv.create(dict(base, state='code', code='for record in records:\n    ' + code))
            elif a['do'] in ('email', 'note'):
                body = self._text_code(model, a.get('body'))
                if a['do'] == 'email':
                    to = a.get('to_field')
                    f = self._field(model, to) if to else None
                    if not f or f.relation not in ('res.partner', 'res.users'):
                        raise UserError(self.env._('Pick the contact or user to send the email to.'))
                    partners = 'record.%s.%s.ids' % (to, 'partner_id' if f.relation == 'res.users' else 'commercial_partner_id')
                    if f.relation == 'res.partner':
                        partners = 'record.%s.ids' % to
                    code = ("record.message_post(body=%s, subject=%s, partner_ids=%s, message_type='comment', subtype_xmlid='mail.mt_comment')"
                            % (body, self._text_code(model, a.get('subject') or auto.name), partners))
                else:
                    code = "record.message_post(body=%s, message_type='comment', subtype_xmlid='mail.mt_note')" % body
                Srv.create(dict(base, state='code', code='for record in records:\n    ' + code))
            else:
                raise UserError(self.env._('Unknown step %s.', a['do']))

    @api.model
    def save(self, model, spec, automation_id=False):
        require_studio(self.env)
        M = self.env['ir.model']._get(model)
        if spec.get('actions') and any(a['do'] in ('email', 'note', 'activity') for a in spec['actions']) and not getattr(M, 'is_mail_thread', False):
            raise UserError(self.env._('Emails and notes need the record to have a chatter (messages).'))
        vals = {'name': spec.get('name') or 'Automation', 'model_id': M.id, 'active': spec.get('active', True),
                'filter_domain': repr(self._domain(model, spec.get('conditions'))),
                'filter_pre_domain': False, 'trigger_field_ids': [(5, 0, 0)], 'trg_date_id': False}
        t = spec.get('trigger')
        if t == 'create':
            vals['trigger'] = 'on_create'
        elif t == 'change':
            vals.update(trigger='on_create_or_write', trigger_field_ids=[(6, 0, self._field(model, spec['field']).ids)])
        elif t == 'stage':
            f = self._field(model, spec['field'])
            dom = self._domain(model, spec.get('conditions')) + [(f.name, '=', spec.get('value'))]
            vals.update(trigger='on_create_or_write', trigger_field_ids=[(6, 0, f.ids)], filter_domain=repr(dom),
                        filter_pre_domain=repr([(f.name, '!=', spec.get('value'))]))
        elif t == 'date':
            f = self._field(model, spec['field'])
            vals.update(trigger='on_time', trg_date_id=f.id, trg_date_range=abs(int(spec.get('days') or 0)) or 0,
                        trg_date_range_type='day', trg_date_range_mode='before' if spec.get('when') == 'before' else 'after')
        else:
            raise UserError(self.env._('Choose when the automation runs.'))
        Auto = self.env['base.automation'].sudo()
        auto = Auto.browse(automation_id) if automation_id else Auto
        if auto:
            auto.write(vals)
        else:
            auto = Auto.create(vals)
        self._actions(auto, model, spec.get('actions'))
        Rule = self.env['sb.studio.rule'].sudo()
        rule = Rule.search([('automation_id', '=', auto.id)], limit=1)
        rule.write({'spec': json.dumps(spec)}) if rule else Rule.create({'automation_id': auto.id, 'spec': json.dumps(spec)})
        return auto.id

    @api.model
    def list(self, model):
        require_studio(self.env)
        out = []
        for a in self.env['base.automation'].sudo().with_context(active_test=False).search([('model_id.model', '=', model)]):
            rule = self.env['sb.studio.rule'].sudo().search([('automation_id', '=', a.id)], limit=1)
            spec = json.loads(rule.spec) if rule.spec else {}
            out.append({'id': a.id, 'name': a.name, 'active': a.active, 'spec': spec, 'studio': bool(spec)})
        return out

    @api.model
    def remove(self, automation_id):
        require_studio(self.env)
        self.env['base.automation'].sudo().browse(automation_id).unlink()
        return True

    def _export_xml(self, a, xid):
        from markupsafe import escape
        rid = xid('auto', a.model_id.model, a.name, str(a.id))
        out = ['<record id="%s" model="base.automation"><field name="name">%s</field><field name="model_id" ref="%s"/>'
               '<field name="trigger">%s</field><field name="filter_domain">%s</field>%s%s</record>' % (
                   rid, escape(a.name), xid('model', a.model_id.model), a.trigger, escape(a.filter_domain or '[]'),
                   '<field name="filter_pre_domain">%s</field>' % escape(a.filter_pre_domain) if a.filter_pre_domain else '',
                   ''.join('<field name="%s" eval="%r"/>' % (k, a[k]) for k in ('trg_date_range',) if a.trigger == 'on_time'))]
        for s in a.action_server_ids:
            fields_xml = ['<field name="name">%s</field>' % escape(s.name), '<field name="model_id" ref="%s"/>' % xid('model', s.model_id.model),
                          '<field name="base_automation_id" ref="%s"/>' % rid, '<field name="usage">base_automation</field>',
                          '<field name="state">%s</field>' % s.state]
            for k in ('code', 'update_path', 'value', 'evaluation_type', 'activity_summary', 'activity_user_type', 'activity_user_field_name'):
                if s[k]:
                    fields_xml.append('<field name="%s">%s</field>' % (k, escape(s[k])))
            out.append('<record id="%s" model="ir.actions.server">%s</record>' % (xid('srv', rid, str(s.id)), ''.join(fields_xml)))
        return out
