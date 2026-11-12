"""Posting speed after a restart or module update.

The first posting after the registry loads (server start, module update, a worker recycled) spends 5 to 15 seconds in
Odoo working out which computed fields depend on the journal entry fields being written (the "trigger trees"); later
postings take about a second. The trees are worked out once per registry: here a background thread works them out for
the accounting models as soon as the registry is ready, so no user waits for them."""
import logging
import threading

from odoo import models

_logger = logging.getLogger(__name__)

WARM_MODELS = ('account.move', 'account.move.line', 'account.partial.reconcile', 'account.full.reconcile', 'sb.voucher',
               'sb.voucher.line', 'sb.voucher.apply', 'sb.voucher.cash', 'stock.move', 'stock.move.line', 'stock.quant')


def _warm(registry, fields):
    try:
        for field in fields:
            registry.get_field_trigger_tree(field)
    except Exception:  # noqa: BLE001 - the registry was replaced meanwhile: nothing to warm any more
        _logger.debug('trigger tree warm-up stopped', exc_info=True)


class SbVoucher(models.Model):
    _inherit = 'sb.voucher'

    def _register_hook(self):
        super()._register_hook()
        registry = self.env.registry
        fields = [f for name in WARM_MODELS if name in registry for f in registry[name]._fields.values()]
        threading.Thread(target=_warm, args=(registry, fields), name='sb-trigger-warmup', daemon=True).start()
