"""Unio AI: an assistant that answers from the company's own data.

Claude gets tools that read with the person's own rights (never more than they could open themselves): find records,
total and group them, run the accounting reports, describe a kind of record. It never changes data: it can propose a
draft (a quotation, an invoice, a task, a contact, a lead), which the person creates with one click.

The Anthropic key is not here: every call goes through the Unio gateway (unio.ai), which keeps each company within
its weekly allowance."""
import json
import logging
import re

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)

# kinds of record the assistant does not read (technical, security, or files)
HIDDEN = re.compile(r'^(ir\.|base\.|bus\.|res\.users\.|res\.config|auth|mail\.(?!activity|message$)|saas\.(?!tenant$|payment$|plan$|ticket$|ai\.usage$)|sb\.ai\.|iap\.|web\.|digest|spreadsheet|html|onboarding)')
# what it may propose to create, as a draft the person confirms
PROPOSABLE = {'sale.order': 'Quotation', 'purchase.order': 'Request for quotation', 'sb.voucher': 'Voucher (draft)',
              'crm.lead': 'Lead', 'project.task': 'Task', 'res.partner': 'Contact', 'product.template': 'Item',
              'mail.activity': 'Reminder', 'sb.ai.memory': 'Something to remember'}
# records that belong to the company being worked in when the assistant creates them (else they show in every company)
COMPANY_OWNED = ('product.template', 'res.partner')
MAX_ROUNDS = 8

TOOLS = [
    {'name': 'list_models', 'description': 'The kinds of records in this company system (technical name and label). Use when unsure which model holds the data.',
     'input_schema': {'type': 'object', 'properties': {'search': {'type': 'string', 'description': 'word to filter labels, e.g. invoice, employee'}}}},
    {'name': 'describe_model', 'description': 'Fields of a model: name, label, type, and for links the model they point to.',
     'input_schema': {'type': 'object', 'properties': {'model': {'type': 'string'}}, 'required': ['model']}},
    {'name': 'find_records', 'description': 'Search records with an Odoo domain, e.g. [["state","=","posted"],["date",">=","2026-01-01"]]. Returns the fields asked for.',
     'input_schema': {'type': 'object', 'properties': {
         'model': {'type': 'string'}, 'domain': {'type': 'array', 'items': {}}, 'fields': {'type': 'array', 'items': {'type': 'string'}},
         'limit': {'type': 'integer', 'description': 'max 80'}, 'order': {'type': 'string', 'description': 'e.g. "date desc"'}}, 'required': ['model']}},
    {'name': 'summarize', 'description': 'Totals and counts, optionally grouped: e.g. model sb.voucher, group_by ["partner_id"], measures ["amount_total:sum"].',
     'input_schema': {'type': 'object', 'properties': {
         'model': {'type': 'string'}, 'domain': {'type': 'array', 'items': {}}, 'group_by': {'type': 'array', 'items': {'type': 'string'}},
         'measures': {'type': 'array', 'items': {'type': 'string'}}, 'limit': {'type': 'integer'}}, 'required': ['model']}},
    {'name': 'list_reports', 'description': 'The accounting and business reports available (Report Center): key, name, what it shows.',
     'input_schema': {'type': 'object', 'properties': {'search': {'type': 'string'}}}},
    {'name': 'run_report', 'description': 'Run a report from list_reports for a period. Returns its main rows. Dates YYYY-MM-DD.',
     'input_schema': {'type': 'object', 'properties': {'key': {'type': 'string'}, 'date_from': {'type': 'string'}, 'date_to': {'type': 'string'}},
                      'required': ['key']}},
    {'name': 'propose_record', 'description': 'Propose a DRAFT for the person to create with one click (nothing is created by you). '
                                              'Models: ' + ', '.join('%s (%s)' % kv for kv in PROPOSABLE.items()) +
                                              '. values: field values as for create(), lines as [[0,0,{...}]].',
     'input_schema': {'type': 'object', 'properties': {'model': {'type': 'string'}, 'values': {'type': 'object'}, 'summary': {'type': 'string'},
                                                        'attach_files': {'type': 'array', 'items': {'type': 'integer'},
                                                                         'description': 'File numbers to attach to the record when created '
                                                                                        '(default: the files of this message).'}},
                      'required': ['model', 'values', 'summary']}},
    {'name': 'propose_attachment', 'description': 'Propose attaching files the person gave you (File #n) to an existing record; they '
                                                  'attach them with one click.',
     'input_schema': {'type': 'object', 'properties': {'model': {'type': 'string'}, 'record_id': {'type': 'integer'},
                                                        'file_ids': {'type': 'array', 'items': {'type': 'integer'}}, 'summary': {'type': 'string'}},
                      'required': ['model', 'record_id', 'file_ids', 'summary']}},
]


class SbAiChat(models.Model):
    _name = 'sb.ai.chat'
    _description = 'Conversation with Unio AI'
    _order = 'write_date desc'

    name = fields.Char()
    user_id = fields.Many2one('res.users', default=lambda s: s.env.user, required=True, index=True, ondelete='cascade')
    messages = fields.Text(default='[]', help='The conversation as sent to the model (JSON).')
    shown = fields.Text(default='[]', help='The conversation as shown to the person (JSON).')
    proposals = fields.Text(default='{}')


class SbAi(models.AbstractModel):
    _name = 'sb.ai'
    _description = 'Unio AI'

    # ------------------------------------------------------------------ reaching Claude
    @api.model
    def enabled(self):
        return self.env['unio.ai'].enabled()

    @api.model
    def meter(self):
        return self.env['unio.ai'].meter()

    def _claude(self, feature, payload):
        """Through the Unio gateway: it holds the key, picks the model for the company's AI plan and keeps the weekly
        allowance."""
        return self.env['unio.ai'].ask(feature, payload)

    # ------------------------------------------------------------------ the conversation
    def _system(self):
        c = self.env.company
        u = self.env.user
        today = fields.Date.context_today(self)
        try:
            from odoo.addons.sonador_books.models.ethiopian_calendar import format_ethiopian, gregorian
            ec = '' if gregorian() else ' (Ethiopian calendar: %s)' % format_ethiopian(today)
        except Exception:  # noqa: BLE001
            ec = ''
        memory = self.env['sb.ai.memory'].for_prompt()
        stable = (
            "You are Unio AI, the assistant inside the business system of %(company)s (%(country)s, currency %(cur)s).\n"
            "Answer from the company's own data using the tools; never invent figures. If the data is not there, say so. "
            "Be brief and concrete: lead with the answer, then the few figures that support it. Use the company currency.\n"
            "Link records you mention as [label](record:MODEL:ID) and lists as [label](list:MODEL:DOMAIN_JSON). "
            "You cannot change data. To create something, use propose_record: the person sees a card and creates it with one click.\n"
            "The system is Unio (accounting with vouchers: model sb.voucher with voucher_type sale, cash_sale, receipt, bill, payment, journal...; "
            "customers and suppliers are res.partner; items are product.product; employees are hr.employee or sb.employee). "
            "For financial statements, VAT, ageing, ledgers and payroll reports use list_reports and run_report.\n"
            "In Unio HQ, the subscribing companies are saas.tenant (state, plan_id, trial_end, monthly_amount), their payments "
            "saas.payment, support questions saas.ticket and AI use saas.ai.usage.\n"
            "Company memory: facts and house rules this company keeps for you. Follow them. When the person teaches you something "
            "worth keeping for everyone (how a bill is booked, which account is used for what, a rule), offer to remember it with "
            "propose_record on sb.ai.memory (values: {\"text\": one short line, \"source\": \"assistant\"}); an admin adds it.\n"
            "Files: the person may give you pictures or PDFs (File #n): read them carefully (amounts, dates, numbers, the supplier) and "
            "say what you read before you rely on it. A record you propose gets the files of that message attached when created; to put "
            "a file on an existing record use propose_attachment.\n"
            "Reply in the language the person writes in (English or Amharic)." % {
                'company': c.name, 'country': c.country_id.name or '', 'cur': c.currency_id.name})
        if memory:
            stable += '\n\nCompany memory:\n' + '\n'.join('- %s' % m for m in memory)
        person = 'You are talking to %s. Today is %s%s.' % (u.name, fields.Date.to_string(today), ec)
        profile = u._sb_ai_profile()
        if profile:
            person += '\nAbout them:\n' + '\n'.join('- %s' % x for x in profile)
        # the company part first and cached; the person part after it
        return [{'type': 'text', 'text': stable, 'cache_control': {'type': 'ephemeral'}}, {'type': 'text', 'text': person}]

    @api.model
    def chats(self):
        return [{'id': c.id, 'name': c.name or 'Conversation', 'date': fields.Datetime.to_string(c.write_date)[:16]}
                for c in self.env['sb.ai.chat'].search([('user_id', '=', self.env.uid)], limit=30)]

    @api.model
    def load_chat(self, chat_id):
        c = self.env['sb.ai.chat'].browse(chat_id)
        return {'id': c.id, 'shown': json.loads(c.shown or '[]'), 'proposals': json.loads(c.proposals or '{}')}

    @api.model
    def ask(self, chat_id, text, file_ids=None):
        """The person's message (and files); runs Claude with the tools until it answers. Returns the conversation as shown."""
        text = (text or '').strip()
        files = self._files(file_ids) if file_ids else self.env['ir.attachment']
        if not text and not files:
            raise UserError(self.env._('Write your question.'))
        text = text or self.env._('Read the file(s) and tell me what they are.')
        Chat = self.env['sb.ai.chat']
        chat = Chat.browse(chat_id) if chat_id else Chat.create({'name': text[:60]})
        messages = json.loads(chat.messages or '[]')
        shown = json.loads(chat.shown or '[]')
        proposals = json.loads(chat.proposals or '{}')
        if files:
            files.write({'res_id': chat.id})
            messages.append({'role': 'user', 'content': [{'type': 'unio_file', 'id': f.id, 'name': f.name} for f in files] +
                             [{'type': 'text', 'text': text}]})
        else:
            messages.append({'role': 'user', 'content': text})
        shown.append({'role': 'user', 'text': text, 'files': [{'id': f.id, 'name': f.name} for f in files]})
        self = self.with_context(sb_ai_turn_files=files.ids)  # noqa: PLW0642 - the files of this message, for the drafts
        steps = []
        start = self._window_start(messages)  # fixed for this answer: its own steps must see the same beginning
        for _round in range(MAX_ROUNDS):
            data = self._claude('assistant', {'system': self._system(), 'messages': self._expand(self._window(messages, start), set(files.ids)),
                                              'tools': TOOLS, 'max_tokens': 2500})
            content = data.get('content') or []
            messages.append({'role': 'assistant', 'content': content})
            uses = [b for b in content if b.get('type') == 'tool_use']
            if not uses:
                answer = '\n'.join(b.get('text', '') for b in content if b.get('type') == 'text').strip()
                shown.append({'role': 'assistant', 'text': answer, 'steps': steps,
                              'proposals': [p for p in proposals.values() if p.get('turn') == len(shown) + 1]})
                break
            results = []
            for u in uses:
                steps.append(self._step_label(u))
                try:
                    out = self._tool(u['name'], u.get('input') or {}, proposals, len(shown))
                    results.append({'type': 'tool_result', 'tool_use_id': u['id'], 'content': json.dumps(out, default=str)[:30000]})
                except (UserError, AccessError, ValueError, KeyError) as e:
                    results.append({'type': 'tool_result', 'tool_use_id': u['id'], 'content': 'Error: %s' % e, 'is_error': True})
            messages.append({'role': 'user', 'content': results})
        else:
            shown.append({'role': 'assistant', 'text': self.env._('That took too many steps; ask it more simply, or in parts.'), 'steps': steps})
        chat.write({'messages': json.dumps(messages, default=str), 'shown': json.dumps(shown), 'proposals': json.dumps(proposals)})
        return {'id': chat.id, 'shown': shown, 'proposals': proposals}

    @staticmethod
    def _typed(m):
        """A message the person wrote (not tool results)."""
        c = m.get('content')
        return m.get('role') == 'user' and (isinstance(c, str) or any(b.get('type') in ('text', 'unio_file') for b in c or []))

    def _window_start(self, messages, size=30):
        """Where the conversation sent for this answer begins: about the last `size` messages, at something the person
        wrote (never in the middle of a tool exchange)."""
        i = max(len(messages) - size, 0)
        while i < len(messages) and not self._typed(messages[i]):
            i += 1
        if i >= len(messages):
            i = max((k for k, m in enumerate(messages) if self._typed(m)), default=0)
        return i

    def _window(self, messages, start=None):
        """The conversation as sent, from `start` (fixed for the whole answer, so the model's own steps always see the
        same beginning), with the model's thinking from earlier turns left out: thinking is bound to the conversation
        and the instructions it was written under, which change with updates; only the turn in progress keeps its own."""
        window = messages[self._window_start(messages) if start is None else start:]
        typed = self._typed
        last = max((i for i, m in enumerate(window) if typed(m)), default=0)
        out = []
        for i, m in enumerate(window):
            c = m.get('content')
            if i < last and m.get('role') == 'assistant' and isinstance(c, list):
                kept = [b for b in c if b.get('type') not in ('thinking', 'redacted_thinking')]
                m = dict(m, content=kept or [{'type': 'text', 'text': '…'}])
            out.append(m)
        return out

    def _step_label(self, use):
        i = use.get('input') or {}
        return {'list_models': 'Looked at the kinds of records', 'describe_model': 'Read the fields of %s' % i.get('model'),
                'find_records': 'Searched %s' % i.get('model'), 'summarize': 'Totalled %s' % i.get('model'),
                'list_reports': 'Looked at the reports', 'run_report': 'Ran the report %s' % i.get('key'),
                'propose_record': 'Prepared a draft', 'propose_attachment': 'Prepared an attachment'}.get(use.get('name'), use.get('name'))

    # ------------------------------------------------------------------ tools (with the person's own rights)
    def _model(self, name):
        if not name or HIDDEN.match(name) or name not in self.env:
            raise UserError(self.env._('%s is not available to the assistant.', name))
        M = self.env[name]
        if M._transient or M._abstract:
            raise UserError(self.env._('%s is not available to the assistant.', name))
        M.check_access('read')
        return M

    def _readable(self, M, wanted=None):
        ok = {n: f for n, f in M._fields.items() if f.type not in ('binary', 'image') and not n.startswith('_')
              and n not in ('password', 'new_password', 'signature', 'access_token', 'message_ids', 'activity_ids', 'website_message_ids')
              and (not f.groups or self.env.user.has_groups(f.groups))}
        if wanted:
            return [n for n in wanted if n in ok]
        prefer = ['display_name', 'name', 'date', 'partner_id', 'amount_total', 'amount_untaxed', 'amount_residual', 'state', 'voucher_type',
                  'reference', 'due_date', 'email', 'phone', 'list_price', 'qty_available', 'user_id', 'stage_id', 'date_order']
        return [n for n in prefer if n in ok][:10] or [n for n in ok if ok[n].store][:8]

    def _clean(self, value):
        if isinstance(value, tuple) and len(value) == 2:
            return {'id': value[0], 'name': value[1]}
        if isinstance(value, list) and value and isinstance(value[0], int) and len(value) > 20:
            return value[:20] + ['…%d more' % (len(value) - 20)]
        return value

    def _bad_values(self, M, values, where=''):
        """Values the record would refuse: a choice not in the field's list, a field it does not have. Lines are checked too."""
        bad = []
        for k, v in (values or {}).items():
            f = M._fields.get(k)
            if not f:
                bad.append('%s%s: no such field' % (where, k))
            elif f.type == 'selection' and v not in (False, None):
                choices = [c for c, _l in f._description_selection(self.env)]
                if v not in choices:
                    bad.append('%s%s: %r is not one of %s' % (where, k, v, choices))
            elif f.type in ('one2many', 'many2many') and isinstance(v, list):
                for i, line in enumerate(v):
                    vals = line[2] if isinstance(line, (list, tuple)) and len(line) == 3 and isinstance(line[2], dict) else line
                    if isinstance(vals, dict):
                        bad += self._bad_values(self.env[f.comodel_name], vals, '%s%s[%d].' % (where, k, i))
        return bad

    def _tool(self, name, inp, proposals, turn):
        if name == 'list_models':
            word = (inp.get('search') or '').lower()
            out = []
            for m in self.env['ir.model'].sudo().search([('transient', '=', False)], order='model'):
                if HIDDEN.match(m.model) or m.model not in self.env or self.env[m.model]._abstract:
                    continue
                if word and word not in m.name.lower() and word not in m.model:
                    continue
                if not self.env[m.model].has_access('read'):
                    continue
                out.append({'model': m.model, 'label': m.name})
            return out[:150]
        if name == 'describe_model':
            M = self._model(inp['model'])
            return [{'name': n, 'label': M._fields[n].string, 'type': M._fields[n].type, 'link': getattr(M._fields[n], 'comodel_name', None),
                     'choices': dict(M._fields[n]._description_selection(self.env)) if M._fields[n].type == 'selection' else None}
                    for n in self._readable(M, list(M._fields)) if M._fields[n].store or M._fields[n].type in ('monetary', 'float', 'integer')][:120]
        if name == 'find_records':
            M = self._model(inp['model'])
            limit = max(1, min(int(inp.get('limit') or 20), 80))
            domain = inp.get('domain') or []
            rows = M.search_read(domain, self._readable(M, inp.get('fields')), limit=limit, order=inp.get('order') or None)
            total = M.search_count(domain)
            return {'total': total, 'shown': len(rows), 'records': [{k: self._clean(v) for k, v in r.items()} for r in rows]}
        if name == 'summarize':
            M = self._model(inp['model'])
            measures = []
            for m in inp.get('measures') or ['__count']:
                fname = m.split(':')[0]
                if m == '__count' or (fname in M._fields and M._fields[fname].type in ('integer', 'float', 'monetary')):
                    measures.append(m if ':' in m or m == '__count' else '%s:sum' % m)
            groups = [g for g in (inp.get('group_by') or []) if g.split(':')[0] in M._fields][:2]
            rows = M._read_group(inp.get('domain') or [], groups, measures or ['__count'], limit=max(1, min(int(inp.get('limit') or 30), 100)))
            out = []
            for r in rows:
                row = {}
                for g, v in zip(groups, r[:len(groups)]):
                    row[g] = v.display_name if hasattr(v, 'display_name') else v
                for m, v in zip(measures or ['__count'], r[len(groups):]):
                    row[m] = v
                out.append(row)
            return out
        if name == 'list_reports':
            word = (inp.get('search') or '').lower()
            cat = self.env['sb.report'].get_catalog()
            entries = cat.get('reports') if isinstance(cat, dict) else cat
            return [{'key': e['key'], 'name': e['name'], 'about': e.get('description', '')} for e in entries or []
                    if not e.get('custom') and (not word or word in (e['name'] + ' ' + e.get('description', '')).lower())][:80]
        if name == 'run_report':
            R = self.env['sb.report']
            cat = R.get_catalog()
            entries = cat.get('reports') if isinstance(cat, dict) else cat
            entry = next((e for e in entries or [] if e['key'] == inp['key']), None)
            if not entry:
                raise UserError(self.env._('There is no report %s.', inp['key']))
            opts = dict(entry.get('options') or {})
            if inp.get('date_from'):
                opts['date_from'] = inp['date_from']
            if inp.get('date_to'):
                opts['date_to'] = inp['date_to']
            doc = R.get_report(entry['report'], opts)
            cols = [c['key'] for c in doc.get('columns', [])]
            rows = []
            for r in doc.get('rows', []):
                if (r.get('level') or 0) > 1 or r.get('kind') == 'detail':
                    continue
                vals = r.get('values') or {}
                rows.append({'row': r.get('label'), **{k: vals.get(k) for k in cols if vals.get(k) not in (None, '', 0, 0.0)}})
                if len(rows) >= 80:
                    break
            return {'title': doc.get('title'), 'subtitle': doc.get('subtitle'), 'columns': [c['label'] for c in doc.get('columns', [])], 'rows': rows}
        if name == 'propose_record':
            model = inp['model']
            if model not in PROPOSABLE or model not in self.env:
                raise UserError(self.env._('Drafts can be prepared for: %s.', ', '.join(PROPOSABLE.values())))
            self.env[model].check_access('create')
            bad = self._bad_values(self.env[model], inp.get('values'))
            if bad:  # back to the model to correct, before the person sees a card that cannot be created
                raise UserError(self.env._('Fix these values and propose again: %s', '; '.join(bad)))
            pid = 'p%d' % (len(proposals) + 1)
            files = inp.get('attach_files') or self.env.context.get('sb_ai_turn_files') or []
            proposals[pid] = {'id': pid, 'model': model, 'kind': PROPOSABLE[model], 'values': inp['values'], 'summary': inp.get('summary') or '',
                              'turn': turn + 1, 'created': False, 'files': [int(i) for i in files]}
            return {'proposal': pid, 'status': 'shown to the person as a card; created only if they click Create',
                    'files_attached_on_create': len(files)}
        if name == 'propose_attachment':
            model = inp['model']
            if model not in self.env or HIDDEN.match(model):
                raise UserError(self.env._('Files cannot be attached to %s.', model))
            rec = self.env[model].browse(int(inp['record_id'])).exists()
            if not rec:
                raise UserError(self.env._('No such record.'))
            rec.check_access('write')
            ids = [int(i) for i in inp['file_ids']]
            pid = 'p%d' % (len(proposals) + 1)
            proposals[pid] = {'id': pid, 'type': 'attach', 'model': model, 'record_id': rec.id, 'kind': 'Attach file',
                              'values': {}, 'summary': inp.get('summary') or 'Attach to %s' % rec.display_name,
                              'turn': turn + 1, 'created': False, 'files': ids}
            return {'proposal': pid, 'status': 'shown to the person as a card; attached only if they click it'}
        raise UserError(self.env._('Unknown tool %s.', name))

    # ------------------------------------------------------------------ the person clicks Create on a proposal
    @api.model
    def create_proposal(self, chat_id, pid):
        chat = self.env['sb.ai.chat'].browse(chat_id)
        proposals = json.loads(chat.proposals or '{}')
        p = proposals.get(pid)
        if not p or p.get('created'):
            raise UserError(self.env._('This draft was already created.'))
        M = self.env[p['model']]
        if p.get('type') == 'attach':
            rec = self._attach(p.get('files') or [], p['model'], p['record_id'])
        else:
            values = {k: v for k, v in (p['values'] or {}).items() if k in M._fields}
            if p['model'] == 'sb.voucher':
                values.pop('state', None)  # always a draft
            if p['model'] in COMPANY_OWNED and 'company_id' in M._fields and not values.get('company_id'):
                values['company_id'] = self.env.company.id
            bad = self._bad_values(M, values)
            if bad:
                raise UserError(self.env._('This draft has values Unio does not accept (%s). Ask the assistant to prepare it again.', '; '.join(bad)))
            rec = M.create(values)
            if p.get('files'):
                self._attach(p['files'], p['model'], rec.id)
        p['created'] = rec.id
        chat.proposals = json.dumps(proposals)
        return {'type': 'ir.actions.act_window', 'res_model': p['model'], 'res_id': rec.id, 'views': [[False, 'form']], 'target': 'current'}
