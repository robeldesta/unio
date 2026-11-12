from odoo import api, models

from .ethiopian_calendar import gregorian

EC_FIELDS = {'date_ec', 'sb_date_ec', 'lock_date_ec'}


class Base(models.AbstractModel):
    _inherit = 'base'

    @api.model
    def _get_view(self, view_id=None, view_type='form', **options):
        """A database on the Gregorian calendar has no use for the Ethiopian date: drop it from every form and list."""
        arch, view = super()._get_view(view_id, view_type, **options)
        if gregorian():
            for node in arch.iter('field'):
                if node.get('name') in EC_FIELDS:
                    node.set('column_invisible' if node.getparent().tag == 'list' else 'invisible', '1')
            for node in arch.iter('label'):
                if node.get('for') in EC_FIELDS:
                    node.set('invisible', '1')
        return arch, view
