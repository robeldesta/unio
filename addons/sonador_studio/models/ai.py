"""Describe a process in plain words, get a draft app: the assistant (Claude, when a key is set in Settings) writes a
Studio spec; without a key a careful reading of the sentence still gives a useful start. Nothing is created until
the person looks at the draft and presses Create."""
import json
import logging
import re

from odoo import api, models
from odoo.exceptions import UserError

from .builder import ICONS, TYPES
from .templates import TEMPLATES, opts

_logger = logging.getLogger(__name__)

SYSTEM = """You design small business apps for an ERP (Odoo 19 based). Read the description and answer with JSON only, no prose:
{"name": "<app name, 1-3 words>", "icon": "<one of: %(icons)s>", "color": "#RRGGBB", "description": "<one sentence>",
 "models": [<model spec>, ...]}
A model spec:
{"model": "x_<snake>", "label": "<Singular>", "plural": "<Plural>", "own": true, "chatter": true,
 "default_view": "list" | "kanban" | "calendar",
 "fields": [{"name": "x_<snake>", "label": "<Label>", "type": "<type>", "required": bool,
             "relation": "<model, for link/tags>", "options": [{"value": "<snake>", "label": "<Label>"}] (choice/stage),
             "formula": "<for formula: field names, numbers, + - * / ( ), sum(x_lines.x_col)>",
             "lines": {"fields": [<field specs of the table columns, without lines>]} (for type lines)}],
 "form": {"title": "x_name", "status": "<the stage field or null>", "sections": [{"label": "", "columns": [[<names>], [<names>]]}],
          "tabs": [{"label": "...", "fields": [<names>]}]},
 "list": [<names>], "kanban": {"title": "x_name", "fields": [<names>]}, "calendar": "<a date field or null>",
 "automations": [{"name": "...", "trigger": "create" | "change" | "stage" | "date", "field": "<field>", "value": "<stage value>",
                  "days": <int>, "when": "before" | "after", "conditions": [{"field": "...", "op": "=", "value": ...}],
                  "actions": [{"do": "set", "field": "...", "value": "... or today"},
                              {"do": "activity", "user_field": "<a link to res.users>", "summary": "text with {x_field}", "days": 0},
                              {"do": "note", "body": "text with {x_field}"},
                              {"do": "email", "to_field": "<link to res.partner>", "subject": "...", "body": "..."}]}]}
Types: %(types)s. Every model has x_name (type text) as its first field. Links may point to res.partner (contacts, customers,
suppliers), res.users (staff who use the system), product.product, hr.employee, or another model of this app listed before it.
Use stages for the life of a record, money for amounts, a lines table for items/rows, formulas for totals. Keep it practical:
5-14 fields per model, 1-3 models, 0-4 automations. Labels in the language of the description."""

# words in a description and the kind of field they suggest
HINTS = [
    (r'\b(date|day|deadline|due|when|birthday|expiry|expires)\b', 'date'),
    (r'\b(time|arrival|departure|check[- ]?in|check[- ]?out)\b', 'datetime'),
    (r'\b(amount|cost|price|fee|salary|budget|value|payment|total|charge)\b', 'money'),
    (r'\b(quantity|qty|number of|count|hours|weight|km|litres|liters)\b', 'decimal'),
    (r'\b(photo|picture|image|logo)\b', 'image'),
    (r'\b(file|document|attachment|scan|contract|certificate)\b', 'file'),
    (r'\b(notes?|description|comments?|remarks?|details)\b', 'longtext'),
    (r'\b(status|stage|state)\b', 'stage'),
    (r'\b(urgent|approved|paid|done|active|yes|confirmed)\b', 'checkbox'),
    (r'\b(customer|client|supplier|vendor|contact|partner|company)\b', 'link:res.partner'),
    (r'\b(employee|staff|assigned|responsible|owner|manager|technician|driver|host|user)\b', 'link:res.users'),
    (r'\b(product|item)\b', 'link:product.product'),
]


ICON_HINTS = [(r'truck|vehicle|fuel|car\b|fleet|delivery|driver', 'truck'), (r'machine|repair|maintenance|breakdown|equipment', 'tool'),
              (r'visitor|staff|employee|training|member|people|student', 'people'), (r'complain|feedback|support|ticket|message', 'chat'),
              (r'booking|reservation|appointment|schedule|event|meeting', 'calendar'), (r'stock|product|item|parcel|inventory|return', 'box'),
              (r'customer|sale|order|shop', 'cart'), (r'building|asset|property|room|house', 'building'), (r'safety|security|risk|incident', 'shield'),
              (r'farm|crop|plant|harvest|tree', 'leaf'), (r'document|contract|policy|book|library', 'book'), (r'idea|project|goal|target', 'flag')]


class SbStudioAi(models.AbstractModel):
    _name = 'sb.studio.ai'
    _description = 'Studio assistant'

    @api.model
    def draft(self, description):
        description = (description or '').strip()
        if len(description) < 8:
            raise UserError(self.env._('Describe the process in a sentence or two.'))
        on = 'unio.ai' in self.env and self.env['unio.ai'].enabled()
        draft = None
        if on:
            try:
                draft = self._ask(description)
            except UserError as e:  # out of this week's allowance: say so, the plain reading still gives a start
                draft = dict(self._read(description), note=str(e))
            except Exception as e:  # the plain reading below still gives a start
                _logger.warning('Studio assistant failed: %s', e)
        draft = draft or self._read(description)
        draft['assistant'] = on and draft.get('assistant', True)
        return self._clean(draft)

    def _ask(self, description):
        """Through the Unio gateway: it holds the key, picks the model for the company's AI plan and keeps the weekly
        allowance."""
        payload = {'max_tokens': 8000, 'system': SYSTEM % {'icons': ', '.join(ICONS), 'types': ', '.join(TYPES)},
                   'messages': [{'role': 'user', 'content': description}]}
        content = self.env['unio.ai'].ask('studio', payload).get('content', [])
        text = ''.join(b.get('text', '') for b in content)
        m = re.search(r'\{.*\}', text, re.S)
        return json.loads(m.group(0))

    def _read(self, description):
        """Without the assistant: a template when the description matches one, otherwise fields from the words used."""
        low = description.lower()
        for key, t in TEMPLATES.items():
            if re.search(r'\b%s' % re.escape(t['name'].lower().rstrip('s')), low):
                return {'name': t['name'], 'icon': t['icon'], 'color': t['color'], 'description': t['description'],
                        'models': json.loads(json.dumps(t['models'])), 'assistant': False, 'template': key}
        # the thing being tracked: "track X", "register of X", "manage X", "log X"
        m = re.search(r"\b(?:track|tracking|record|recording|register of|log|logging|manage|managing|list of|keep|follow up)\s+(?:of\s+)?(?:our |the |all |every |each )?([a-z][a-z' -]{2,40}?)(?:\s+(?:with|and|for|that|which|by|so|including|from|at|in)\b|[,.:;]|$)", low)
        words = (m.group(1) if m else 'record').replace("'s", '').replace("s'", 's').split()
        # the thing itself is the last word or two ("delivery trucks fuel fill-ups" -> "fuel fill-up")
        thing = ' '.join(words[-2:] if len(words) > 1 and len(words[-1]) < 12 and len(words) > 2 else words[-1:])
        thing = re.sub(r'(?<=[a-z])s$', '', thing) or 'record'
        label = thing[:1].upper() + thing[1:]
        fields = [{'name': 'x_name', 'label': 'Name', 'type': 'text', 'required': True}]
        # the details: what comes after "with" / "including", split on commas and "and"
        m = re.search(r'\b(?:with|including|capturing|recording|showing|fields?:?)\s+(.+)', description, re.I | re.S)
        seen = {'name'}
        for part in re.split(r',|\band\b|;|\n', m.group(1) if m else ''):
            words = re.sub(r'\b(the|a|an|its|their|our|of each|each)\b', ' ', part.strip(' .'), flags=re.I).strip()
            words = re.sub(r'\s+', ' ', words)[:40]
            if not words or words.lower() in seen:
                continue
            seen.add(words.lower())
            ftype, rel = 'text', None
            for rx, kind in HINTS:
                if re.search(rx, words.lower()):
                    ftype, _c, rel = kind.partition(':')
                    break
            f = {'name': 'x_' + re.sub(r'[^a-z0-9]+', '_', words.lower()).strip('_')[:30], 'label': words[:1].upper() + words[1:], 'type': ftype}
            if rel:
                f['relation'] = rel
            if ftype == 'stage':
                f['options'] = opts('New', 'In progress', 'Done')
            fields.append(f)
        if not any(f['type'] == 'stage' for f in fields):
            fields.append({'name': 'x_stage', 'label': 'Stage', 'type': 'stage', 'options': opts('New', 'In progress', 'Done')})
        names = [f['name'] for f in fields if f['name'] != 'x_name']
        status = next(f['name'] for f in fields if f['type'] == 'stage')
        plain = [n for n in names if n != status and next(f for f in fields if f['name'] == n)['type'] not in ('longtext', 'richtext', 'lines')]
        long_ = [n for n in names if next(f for f in fields if f['name'] == n)['type'] in ('longtext', 'richtext', 'lines')]
        half = (len(plain) + 1) // 2
        spec = {'model': 'x_draft', 'label': label, 'plural': label + 's', 'own': True, 'chatter': True, 'fields': fields,
                'form': {'title': 'x_name', 'status': status, 'sections': [{'label': '', 'columns': [plain[:half], plain[half:]]}],
                         'tabs': [{'label': 'Notes', 'fields': long_}] if long_ else []},
                'list': ['x_name'] + plain[:5] + [status]}
        icon = next((ic for rx, ic in ICON_HINTS if re.search(rx, low)), 'clipboard')
        return {'name': (label + 's') if not label.endswith('s') else label, 'icon': icon, 'color': '#2D6BFF', 'description': description[:200], 'models': [spec], 'assistant': False}

    def _clean(self, draft):
        """Make a draft safe to build: valid names, known types and icons, links only to models that exist."""
        known = {m['model'] for m in draft.get('models', [])}
        draft['icon'] = draft.get('icon') if draft.get('icon') in ICONS else 'box'
        if not re.fullmatch(r'#[0-9A-Fa-f]{6}', draft.get('color') or ''):
            draft['color'] = '#2D6BFF'
        for spec in draft.get('models', []):
            spec['own'] = True
            spec.setdefault('label', 'Record')
            clean = []
            names = set()
            for f in spec.get('fields', []):
                f['name'] = 'x_' + re.sub(r'[^a-z0-9_]+', '_', (f.get('name') or f.get('label') or 'field').lower().replace('x_', '', 1)).strip('_')[:40]
                if f.get('type') not in TYPES or f['name'] in names or f['name'] in ('x_active', 'x_currency_id', 'x_parent_id', 'x_sequence'):
                    continue
                if f['type'] in ('link', 'tags') and f.get('relation') not in self.env and f.get('relation') not in known:
                    continue
                if f['type'] in ('choice', 'stage') and not f.get('options'):
                    continue
                f['label'] = (f.get('label') or f['name'][2:].replace('_', ' ').title())[:60]
                names.add(f['name'])
                clean.append(f)
            if 'x_name' not in names:
                clean.insert(0, {'name': 'x_name', 'label': 'Name', 'type': 'text', 'required': True})
            spec['fields'] = clean
            names.add('x_name')
            form = spec.setdefault('form', {})
            form['title'] = 'x_name'
            if form.get('status') not in names:
                form['status'] = next((f['name'] for f in clean if f['type'] == 'stage'), None)
            for sec in form.get('sections') or []:
                sec['columns'] = [[n for n in col if n in names] for col in sec.get('columns') or []]
            for tab in form.get('tabs') or []:
                tab['fields'] = [n for n in tab.get('fields') or [] if n in names]
            spec['list'] = [n for n in spec.get('list') or [] if n in names] or ['x_name']
            if spec.get('calendar') not in names:
                spec.pop('calendar', None)
            # formulas that refer to dropped fields are dropped too
            for f in [f for f in clean if f['type'] == 'formula']:
                try:
                    self.env['sb.studio.builder']._formula_code(spec, f)
                except UserError:
                    clean.remove(f)
        return draft
