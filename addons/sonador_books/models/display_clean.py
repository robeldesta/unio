"""Words of the systems the books came from stay in the data (the sync finds its records by them) but not on screen:
"O18|CPV/10326" reads "CPV/10326", "Commercial Bank of Ethiopia (Odoo 18)" reads "Commercial Bank of Ethiopia (earlier
system)"."""
import re

from odoo import api, models

_PATTERNS = [
    (re.compile(r'\bO18\|'), ''),
    (re.compile(r',?\s*until the Peachtree FY\s*\d+ GL is imported', re.I), ''),
    (re.compile(r'\bUnallocated \(Peachtree\)\s*', re.I), 'Unallocated opening balance - '),
    (re.compile(r'\(\s*Peachtree\s*\)', re.I), '(earlier system)'),
    (re.compile(r'\(\s*Peachtree\s+(?=[\d.]+\))', re.I), '(former no. '),
    (re.compile(r'\bPeachtree\b', re.I), 'earlier system'),
    (re.compile(r'\(\s*Odoo 18\s*\)', re.I), '(earlier system)'),
    (re.compile(r'\(\s*Odoo 18\s*,\s*', re.I), '(earlier system, '),
    (re.compile(r'\bFrom Odoo 18\b', re.I), 'From the earlier system'),
    (re.compile(r'\bOdoo 18\b', re.I), 'the earlier system'),
]


def clean(text):
    if not isinstance(text, str) or ('O18|' not in text and 'doo 18' not in text and 'DOO 18' not in text
                                     and 'eachtree' not in text and 'EACHTREE' not in text):
        return text
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


def clean_doc(doc):
    """Every label and text value of a report document."""
    for key in ('title', 'subtitle'):
        doc[key] = clean(doc.get(key))
    for row in doc.get('rows') or []:
        row['label'] = clean(row.get('label'))
        values = row.get('values') or {}
        for k, v in values.items():
            if isinstance(v, str):
                values[k] = clean(v)
    return doc


class AccountJournal(models.Model):
    _inherit = 'account.journal'

    @api.depends('name', 'currency_id', 'company_id')
    def _compute_display_name(self):
        super()._compute_display_name()
        for j in self:
            j.display_name = clean(j.display_name)


class AccountMove(models.Model):
    _inherit = 'account.move'

    def _compute_display_name(self):
        super()._compute_display_name()
        for m in self:
            m.display_name = clean(m.display_name)


# text fields shown in lists and forms that carry the earlier systems' words: cleaned on the way to the screen only (the
# stored value stays, the sync finds its records by it; a form saves only the fields the user changed)
CLEAN_FIELDS = {
    'account.move.line': ('name', 'ref', 'move_name'),
    'account.move': ('ref', 'name', 'payment_reference'),
    'sb.voucher': ('reference', 'memo'),
    'sb.stock.voucher': ('reference', 'memo'),
    'sb.bank.rec.line': ('name', 'ref', 'move_name'),
    'res.partner': ('name', 'complete_name', 'display_name'),
}


def _clean_values(values, names):
    for row in values:
        for name in names:
            if isinstance(row.get(name), str):
                row[name] = clean(row[name])
        for value in row.values():
            if isinstance(value, dict) and isinstance(value.get('display_name'), str):
                value['display_name'] = clean(value['display_name'])  # many2one {id, display_name}
    return values


def _clean_tree(model, rows, specification):
    """Clean the rows of `model` and, through the specification, the lines of its one2many / many2many fields (the
    bank reconciliation's items, a voucher's lines)."""
    names = CLEAN_FIELDS.get(model._name)
    if names:
        _clean_values(rows, names)
    for fname, sub in (specification or {}).items():
        field = model._fields.get(fname)
        if not field or field.type not in ('one2many', 'many2many') or not isinstance(sub, dict) or not sub.get('fields'):
            continue
        comodel = model.env[field.comodel_name]
        for row in rows:
            lines = row.get(fname)
            if isinstance(lines, list) and lines and isinstance(lines[0], dict):
                _clean_tree(comodel, lines, sub['fields'])
    return rows


class Base(models.AbstractModel):
    _inherit = 'base'

    def web_read(self, specification):
        return _clean_tree(self, super().web_read(specification), specification)

    @api.model
    def web_search_read(self, domain, specification, offset=0, limit=None, order=None, count_limit=None):
        res = super().web_search_read(domain, specification, offset=offset, limit=limit, order=order, count_limit=count_limit)
        _clean_tree(self, res.get('records') or [], specification)
        return res


class ResPartner(models.Model):
    _inherit = 'res.partner'

    def _compute_display_name(self):
        super()._compute_display_name()
        for p in self:
            p.display_name = clean(p.display_name)
        if self.env.context.get('formatted_display_name'):
            self._sb_tell_apart()

    def _sb_tell_apart(self):
        """In pickers, contacts sharing a name ("Meskele Geda" the customer, and twice as an employee) say which is which:
        their code, customer / vendor / employee, city or e-mail, in grey after the name."""
        names = {p.name for p in self if p.name}
        if not names:
            return
        groups = self.sudo().with_context(active_test=True)._read_group([('name', 'in', list(names))], ['name'], ['__count'])
        twins = {name for name, count in groups if count > 1}
        for p in self.filtered(lambda x: x.name in twins):
            p_ = p.sudo()
            hints = [p_.ref] if p_.ref else []
            if p_.customer_rank:
                hints.append(self.env._('customer'))
            if p_.supplier_rank:
                hints.append(self.env._('vendor'))
            if 'employee_ids' in p_._fields and p_.with_context(active_test=False).employee_ids:
                hints.append(self.env._('employee'))
            hints += [x for x in (p_.city, p_.email or p_.phone) if x][:1 if hints else 2]
            if not hints:
                hints.append('#%s' % p.id)
            p.display_name = '%s\t--%s--' % (p.display_name.split('\t')[0], ' · '.join(hints))


class ProductProduct(models.Model):
    _inherit = 'product.product'

    def _compute_display_name(self):
        """Item pickers read "[1502-7003] 1 Liter Sticker", as on vouchers and reports, not the name with the code glued
        after it ("1 Liter Sticker1502-7003")."""
        super()._compute_display_name()
        if not self.env.context.get('formatted_display_name'):
            return
        for p in self:
            name = p.display_name or ''
            if '\t--' in name and name.endswith('--'):
                text, code = name.split('\t--', 1)
                p.display_name = '[%s] %s' % (code[:-2], text)


class IrModelAccess(models.Model):
    _inherit = 'ir.model.access'

    _ACTIONS = {'read': 'see', 'write': 'change', 'create': 'create', 'unlink': 'delete'}

    def _make_access_error(self, model, mode):
        """A refused screen or action says what in plain words, never the technical name: "You cannot see Vouchers.
        Ask your administrator for one of these roles: Accountant, Clerk." instead of "... 'Voucher' (sb.voucher) records"."""
        error = super()._make_access_error(model, mode)
        kind = self.env['ir.model']._get(model).name or self.env._('these documents')
        roles = sorted({g.split('/')[-1].strip() for g in self.group_names_with_access(model, mode)})
        msg = self.env._('You cannot %(action)s %(kind)s.', action=self._ACTIONS.get(mode, mode), kind=kind)
        if roles:
            msg += ' ' + self.env._('Ask your administrator for one of these roles: %s.', ', '.join(roles[:6]))
        else:
            msg += ' ' + self.env._('Ask your administrator.')
        return type(error)(msg)
