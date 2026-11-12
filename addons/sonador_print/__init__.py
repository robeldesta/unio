from . import controllers
from . import models


def uninstall_hook(env):
    """Give the fixed layouts their place in the Print menu back."""
    for xmlid, model in (('sonador_books.action_report_sale_document', 'sb.voucher'), ('sonador_inventory.action_report_stock_voucher', 'sb.stock.voucher'),
                         ('sonador_sales.action_report_delivery_note', 'sb.delivery.batch')):
        report = env.ref(xmlid, raise_if_not_found=False)
        if report:
            report.binding_model_id = env['ir.model']._get(model)
