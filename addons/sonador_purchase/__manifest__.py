{
    'name': 'Purchase',
    'version': '19.0.0.1.0',
    'summary': 'Purchase orders for procurement: batched receipts (GRN), bills from several GRNs, advance payments linked to the order',
    'description': """
Purchase
========
Requests for quotation and purchase orders on Odoo Purchase (no Odoo pickings, no Odoo bills). Goods arrive on
GRNs, in as many batches as the supplier delivers, against the order lines; the order tracks ordered,
received, billed and remaining per line. One bill clears all the GRNs not yet billed. Payments (CPV) can be made
against the order before the goods arrive (advance) and the order shows what is paid, billed and still owed.
""",
    'author': 'Sonador Systems',
    'category': 'Purchases',
    'license': 'LGPL-3',
    'depends': ['sonador_inventory', 'purchase'],
    'data': [
        'security/groups.xml',
        'security/ir.model.access.csv',
        'security/approval.xml',
        'views/purchase_views.xml',
        'views/document_views.xml',
        'views/purchase_app.xml',
        'views/series_views.xml',
        'views/approval_views.xml',
        'data/hide_odoo_purchase.xml',
    ],
    'assets': {'web.assets_backend': ['sonador_purchase/static/src/js/*']},
    'installable': True,
    'application': False,
}
