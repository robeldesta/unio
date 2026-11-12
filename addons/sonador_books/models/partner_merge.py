"""Merging contacts that are in the books.

Odoo's merge moves every reference of the merged contacts onto the one kept, by SQL, journal items included: the
customer or vendor of posted entries changes without a trace. Here, contacts with posted journal items are merged by an
accounting manager only, never when one of those items is dated in a closed period (the closed books would change), and
the merge is written in the kept contact's history and in the period log (the merged contacts are deleted by the merge).
"""
from odoo import models
from odoo.exceptions import UserError
from odoo.tools.misc import format_date


class BasePartnerMergeAutomaticWizard(models.TransientModel):
    _inherit = 'base.partner.merge.automatic.wizard'

    def _merge(self, partner_ids, dst_partner=None, extra_checks=True):
        Partner = self.env['res.partner']
        partners = Partner.browse(partner_ids).exists()
        if len(partners) < 2 or self.env.user._is_superuser():
            return super()._merge(partner_ids, dst_partner=dst_partner, extra_checks=extra_checks)
        kept = dst_partner if dst_partner and dst_partner in partners else None
        AML = self.env['account.move.line'].sudo()
        items = AML.search([('partner_id', 'in', partners.ids), '|', ('move_id.state', '=', 'posted'),
                            '&', ('move_id.state', '=', 'cancel'), ('move_id.posted_before', '=', True)])
        if not items:
            return super()._merge(partner_ids, dst_partner=dst_partner, extra_checks=extra_checks)
        if not self.env.user.has_group('sonador_books.group_sb_manager'):
            raise UserError(self.env._(
                '%s have posted journal items: merging them changes the customer or vendor of posted entries. Only an '
                'accounting manager can merge them.', ', '.join(partners.mapped('display_name'))))
        moved = items.filtered(lambda l: l.partner_id != kept) if kept else items
        for company in moved.company_id:
            lock = max(filter(None, [company.fiscalyear_lock_date, company.hard_lock_date, company._sb_closed_through()]),
                       default=None)
            closed = moved.filtered(lambda l: l.company_id == company and lock and l.date <= lock)
            if closed:
                raise UserError(self.env._(
                    '%(p)s have journal items in a closed period (%(e)s ..., books closed through %(lock)s): merging them '
                    'would change closed books. Keep the contacts apart (archive the one not used any more).',
                    p=', '.join(partners.mapped('display_name')), e=', '.join(closed.move_id[:3].mapped('name')),
                    lock=format_date(self.env, lock)))
        before = {p.id: (p.display_name, len(items.filtered(lambda l, p=p: l.partner_id == p))) for p in partners}
        res = super()._merge(partner_ids, dst_partner=dst_partner, extra_checks=extra_checks)
        survivor = partners.exists()
        gone = [before[pid] for pid in partners.ids if pid not in survivor.ids]
        note = self.env._('Contacts merged by %(user)s into %(kept)s: %(gone)s.', user=self.env.user.name,
                          kept=', '.join(survivor.mapped('display_name')),
                          gone='; '.join(self.env._('%(name)s (%(n)s posted journal items moved)', name=n, n=c) for n, c in gone))
        for partner in survivor:
            partner.sudo().message_post(body=note, author_id=self.env.user.partner_id.id)
        for company in items.company_id or self.env.company:
            self.env['sb.lock.log']._log(company, 'partner_merge', note=note[:250])
        return res
