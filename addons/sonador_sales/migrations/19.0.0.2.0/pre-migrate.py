"""Delivery orders get the states of Eco Green's earlier system: 'open' (to deliver) becomes Draft here and is then
set from its batches by the post-migration (Ready once a batch is validated)."""


def migrate(cr, version):
    cr.execute("UPDATE sb_delivery_order SET state = 'draft' WHERE state = 'open'")
