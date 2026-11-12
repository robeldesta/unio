"""Production reports: register, material usage against the recipe, and the cost sheet per product."""
from collections import defaultdict

from odoo import fields, models

from odoo.addons.sonador_books.models.report_catalog import E

EXTRA = [
    E('production_register', 'production', 'Production Register', 'Every build: item, quantity, materials, labour, overhead and cost per unit.', 1),
    E('material_usage', 'production', 'Material Usage vs Recipe', 'What each build used against what the recipe says, with the variance in quantity and value.', 1),
    E('cost_sheet', 'production', 'Cost Sheet', 'Per product for the period: quantity produced, material, labour and overhead per unit - live from the builds.', 1),
    E('recipe_list', 'production', 'Recipes (Bills of Materials)', 'Every recipe and version with components per unit and standard cost.', 1),
]
BLURBS = {
    'production_register': 'Every build with what it consumed and what the finished goods cost.',
    'material_usage': 'Recipe quantity against actual use. Positive variance: more was used than the recipe allows.',
    'cost_sheet': 'Cost of production per unit, split into materials, labour and overhead - the year-end cost sheet, built live from the books.',
    'recipe_list': 'Components per unit of each product, valued at current cost, with labour and overhead.',
}


class SbReportProduction(models.AbstractModel):
    _inherit = 'sb.report'

    def _extra_catalog(self):
        return super()._extra_catalog() + EXTRA

    def _extra_categories(self):
        return super()._extra_categories() + [('production', 'Production', 'Manufacturing and Costing', 'inventory')]

    def _present(self, doc):
        super()._present(doc)
        if not doc.get('blurb') and doc['key'] in BLURBS:
            doc['blurb'] = BLURBS[doc['key']]

    def _builds(self, opts):
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        return self.env['sb.stock.voucher'].search([('company_id', '=', self.env.company.id), ('voucher_type', 'in', ('build', 'fprr')),
                                                     ('state', '=', 'posted'), ('date', '>=', df), ('date', '<=', dt)], order='date, name')

    def _range_sub(self, opts):
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        return '%s  |  %s' % (self._period_name(df, dt), self._label_range_ec(df, dt))

    def _report_production_register(self, opts):
        rows, grand = [], defaultdict(float)
        for v in self._builds(opts):
            if v.voucher_type == 'fprr':
                for l in v.line_ids:
                    if not self._search_ok(opts, v.name, l.product_id.name, l.product_id.default_code, v.reference):
                        continue
                    grand['total'] += l.value
                    rows.append({'id': 'f%d' % l.id, 'parent': None, 'level': 0, 'kind': 'line', 'label': v.name,
                                 'values': {'date': fields.Date.to_string(v.date), 'ref': v.name, 'item': l.product_id.display_name,
                                            'recipe': 'FPRR (at cost from WIP)', 'qty': l.quantity, 'total': l.value, 'unit': l.unit_cost},
                                 'open': {'model': 'sb.stock.voucher', 'id': v.id}})
                continue
            p = v.build_product_id
            if not self._search_ok(opts, v.name, p.name, p.default_code, v.reference):
                continue
            vals = {'date': fields.Date.to_string(v.date), 'ref': v.name, 'item': p.display_name, 'recipe': v.build_bom_id.display_name or '',
                    'qty': v.build_qty, 'material': v.material_value, 'labour': v.labour_value, 'overhead': v.overhead_value,
                    'total': v.build_value, 'unit': v.build_unit_cost}
            for k in ('material', 'labour', 'overhead', 'total'):
                grand[k] += vals[k]
            gid = 'v%d' % v.id
            rows.append({'id': gid, 'parent': None, 'level': 0, 'kind': 'group', 'expandable': True, 'label': v.name, 'values': vals,
                         'open': {'model': 'sb.stock.voucher', 'id': v.id}})
            for l in v.line_ids:
                rows.append({'id': 'l%d' % l.id, 'parent': gid, 'level': 1, 'kind': 'detail', 'label': l.product_id.display_name,
                             'values': {'ref': l.product_id.display_name, 'qty': l.quantity, 'material': l.value,
                                        'unit': l.unit_cost}})
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total', 'values': dict(grand, ref='Total')})
        cols = [{'key': 'ref', 'label': 'Build / Component', 'type': 'text'}, {'key': 'date', 'label': 'Date', 'type': 'date'},
                {'key': 'item', 'label': 'Item produced', 'type': 'text'}, {'key': 'recipe', 'label': 'Recipe', 'type': 'text'},
                {'key': 'qty', 'label': 'Qty', 'type': 'number'}, {'key': 'material', 'label': 'Materials', 'type': 'monetary'},
                {'key': 'labour', 'label': 'Labour', 'type': 'monetary'}, {'key': 'overhead', 'label': 'Overhead', 'type': 'monetary'},
                {'key': 'total', 'label': 'Cost of production', 'type': 'monetary'}, {'key': 'unit', 'label': 'Per unit', 'type': 'number'}]
        return self._doc('production_register', opts, cols, rows, self._range_sub(opts), 'Production Register')

    def _report_material_usage(self, opts):
        rows, grand = [], defaultdict(float)
        for v in self._builds(opts):
            if v.voucher_type != 'build':
                continue
            for l in v.line_ids:
                p = l.product_id
                if not self._search_ok(opts, v.name, p.name, p.default_code, v.build_product_id.name):
                    continue
                var_value = l.qty_variance * l.unit_cost
                grand['std_value'] += l.qty_standard * l.unit_cost
                grand['value'] += l.value
                grand['var_value'] += var_value
                rows.append({'id': 'l%d' % l.id, 'parent': None, 'level': 0, 'kind': 'line', 'label': v.name,
                             'warning': abs(l.qty_variance) > 1e-6 and l.qty_standard and abs(l.qty_variance) / l.qty_standard > 0.05,
                             'values': {'date': fields.Date.to_string(v.date), 'ref': v.name, 'built': v.build_product_id.display_name,
                                        'item': p.display_name, 'std': l.qty_standard, 'qty': l.quantity, 'var': l.qty_variance,
                                        'pct': 100.0 * l.qty_variance / l.qty_standard if l.qty_standard else 0.0,
                                        'cost': l.unit_cost, 'var_value': var_value},
                             'open': {'model': 'sb.stock.voucher', 'id': v.id}})
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total',
                     'values': {'ref': 'Total', 'var_value': grand['var_value']}})
        cols = [{'key': 'date', 'label': 'Date', 'type': 'date'}, {'key': 'ref', 'label': 'Build', 'type': 'text'},
                {'key': 'built', 'label': 'Item produced', 'type': 'text'}, {'key': 'item', 'label': 'Component', 'type': 'text'},
                {'key': 'std', 'label': 'Recipe qty', 'type': 'number'}, {'key': 'qty', 'label': 'Used', 'type': 'number'},
                {'key': 'var', 'label': 'Variance', 'type': 'number'}, {'key': 'pct', 'label': 'Variance %', 'type': 'percent'},
                {'key': 'cost', 'label': 'Unit cost', 'type': 'number'}, {'key': 'var_value', 'label': 'Variance value', 'type': 'monetary'}]
        return self._doc('material_usage', opts, cols, rows, self._range_sub(opts), 'Material Usage vs Recipe')

    def _report_cost_sheet(self, opts):
        agg = defaultdict(lambda: defaultdict(float))
        for v in self._builds(opts):
            if v.voucher_type == 'fprr':  # received at cost from WIP: no split into materials / labour / overhead
                for l in v.line_ids:
                    a = agg[l.product_id]
                    a['qty'] += l.quantity
                    a['total'] += l.value
                    a['builds'] += 1
                continue
            a = agg[v.build_product_id]
            a['qty'] += v.build_qty
            a['material'] += v.material_value
            a['labour'] += v.labour_value
            a['overhead'] += v.overhead_value
            a['total'] += v.build_value
            a['builds'] += 1
        rows, grand = [], defaultdict(float)
        for p in sorted(agg, key=lambda x: x.display_name):
            a = agg[p]
            q = a['qty'] or 1.0
            for k in ('qty', 'material', 'labour', 'overhead', 'total'):
                grand[k] += a[k]
            rows.append({'id': 'p%d' % p.id, 'parent': None, 'level': 0, 'kind': 'line', 'label': p.display_name,
                         'values': {'item': p.display_name, 'uom': p.uom_id.name, 'builds': a['builds'], 'qty': a['qty'],
                                    'material_u': a['material'] / q, 'labour_u': a['labour'] / q, 'overhead_u': a['overhead'] / q,
                                    'total_u': a['total'] / q, 'total': a['total']},
                         'drill': {'report': 'production_register', 'search': p.default_code or p.name}})
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total',
                     'values': {'item': 'Total', 'qty': grand['qty'], 'total': grand['total']}})
        cols = [{'key': 'item', 'label': 'Product', 'type': 'text'}, {'key': 'uom', 'label': 'Unit', 'type': 'text'},
                {'key': 'builds', 'label': 'Builds', 'type': 'number_int'}, {'key': 'qty', 'label': 'Produced', 'type': 'number'},
                {'key': 'material_u', 'label': 'Materials / unit', 'type': 'number'}, {'key': 'labour_u', 'label': 'Labour / unit', 'type': 'number'},
                {'key': 'overhead_u', 'label': 'Overhead / unit', 'type': 'number'}, {'key': 'total_u', 'label': 'Cost / unit', 'type': 'number'},
                {'key': 'total', 'label': 'Cost of production', 'type': 'monetary'}]
        return self._doc('cost_sheet', opts, cols, rows, self._range_sub(opts), 'Cost Sheet')

    def _report_recipe_list(self, opts):
        rows = []
        for bom in self.env['mrp.bom'].search([('company_id', 'in', (self.env.company.id, False))], order='product_tmpl_id, sb_date_from'):
            name = bom.product_tmpl_id.display_name
            if not self._search_ok(opts, name, bom.code, bom.product_tmpl_id.default_code):
                continue
            gid = 'b%d' % bom.id
            per = bom.product_qty or 1.0
            comp = sum(l.product_id.standard_price * l.product_uom_id._compute_quantity(l.product_qty, l.product_id.uom_id) for l in bom.bom_line_ids) / per
            valid = ' to '.join(filter(None, [bom.sb_date_from and fields.Date.to_string(bom.sb_date_from),
                                              bom.sb_date_to and fields.Date.to_string(bom.sb_date_to)])) or 'always'
            rows.append({'id': gid, 'parent': None, 'level': 0, 'kind': 'group', 'expandable': True, 'label': name,
                         'values': {'item': '%s%s' % (name, ' - %s' % bom.code if bom.code else ''), 'valid': valid,
                                    'material': comp, 'labour': bom.sb_labour_cost, 'overhead': bom.sb_overhead_cost,
                                    'total': comp + bom.sb_labour_cost + bom.sb_overhead_cost},
                         'open': {'model': 'mrp.bom', 'id': bom.id}})
            for l in bom.bom_line_ids:
                q = l.product_uom_id._compute_quantity(l.product_qty, l.product_id.uom_id) / per
                rows.append({'id': 'bl%d' % l.id, 'parent': gid, 'level': 1, 'kind': 'detail', 'label': l.product_id.display_name,
                             'values': {'item': l.product_id.display_name, 'qty': q, 'uom': l.product_id.uom_id.name,
                                        'cost': l.product_id.standard_price, 'material': q * l.product_id.standard_price}})
        cols = [{'key': 'item', 'label': 'Product / Component', 'type': 'text'}, {'key': 'valid', 'label': 'Valid', 'type': 'text'},
                {'key': 'qty', 'label': 'Per unit', 'type': 'number'}, {'key': 'uom', 'label': 'Unit', 'type': 'text'},
                {'key': 'cost', 'label': 'Unit cost', 'type': 'number'}, {'key': 'material', 'label': 'Materials', 'type': 'number'},
                {'key': 'labour', 'label': 'Labour', 'type': 'number'}, {'key': 'overhead', 'label': 'Overhead', 'type': 'number'},
                {'key': 'total', 'label': 'Standard cost / unit', 'type': 'number'}]
        return self._doc('recipe_list', opts, cols, rows, 'Standard cost per unit at current component costs', 'Recipes (Bills of Materials)')
