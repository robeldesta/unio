{
    'name': 'Returns and Damaged Stock',
    'version': '19.0.0.1.0',
    'summary': 'Store damage vouchers, customer sales returns and recycled product receiving, with damaged stock kept apart',
    'description': """
Returns and Damaged Stock
=========================
Every item has a damaged counterpart (its own item, in a damaged item class with its own inventory account) kept in a
Damaged location of the warehouse:

* Store Damage Voucher (SDV): goods damaged in the store move to their damaged counterpart at cost.
* Sales Return (RET): goods a customer brings back, against the invoice or cash sale; the credit note is the money side,
  the goods come back to the damaged counterpart at the cost they were sold at.
* Recycled Product Receiving (RPRV): damaged goods taken out of the damaged stock and received as a recycled item, at
  the damaged stock's cost.

The store prepares, finance posts.
""",
    'author': 'Sonador Systems',
    'category': 'Inventory',
    'license': 'LGPL-3',
    'depends': ['sonador_inventory', 'sonador_me'],
    'data': [
        'views/returns_views.xml',
        'views/config_views.xml',
        'report/replacement_report.xml',
    ],
    'installable': True,
    'application': False,
}
