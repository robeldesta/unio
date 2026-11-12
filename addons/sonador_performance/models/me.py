"""My scorecard as a card in Me (sonador_me section registry)."""
from odoo import models


class SbMe(models.AbstractModel):
    _inherit = 'sb.me'

    def _me_sections(self):
        out = super()._me_sections()

        def data():
            return self.env['sb.perf'].my_scorecard_card() or False
        out.append({'key': 'scorecard', 'title': 'My scorecard', 'icon': 'fa-bullseye', 'sequence': 20, 'wide': True, 'data': data})
        return out


class SbPerf(models.AbstractModel):
    _inherit = 'sb.perf'

    def my_scorecard_card(self):
        """This period's scorecard (or the last one with results), the last 6 periods and my key results."""
        me = self._me()
        if not me or not self._assignments(me):
            return None
        months = self._months()
        cur = months[-1]
        sc = self._scorecard(me, cur)
        if not sc['results'] and len(months) > 1:
            prev = self._scorecard(me, months[-2])
            if prev['results']:
                cur, sc = months[-2], prev
        S = self.env['sb.perf.kpi.score'].sudo()
        hist = {s.month: s.percent for s in S.search([('employee_id', '=', me.id), ('results', '>', 0)], limit=12)}
        trend = [{'label': m['label'].split(' ')[0], 'score': hist.get(m['start'])} for m in months[-6:]]
        krs = self.env['sb.perf.kr'].sudo().search([('goal_id.employee_id', '=', me.id),
                                                    ('goal_id.status', 'not in', ('done', 'dropped'))], limit=6)
        return {'employee_id': me.id, 'month': {'key': cur['key'], 'label': cur['label']}, 'total': sc['total'],
                'rating': sc['rating'], 'achievement': sc['achievement'], 'results': sc['results'], 'kpis': len(sc['rows']),
                'rows': [{'name': r['name'], 'unit': r['unit'], 'target': r['target'], 'actual': r['actual'],
                          'achievement': r['achievement'], 'score': r['score'], 'level': r['level'], 'color': r['level_color'],
                          'weight': r['weight'], 'auto': r['source'] == 'auto'} for r in sc['rows']],
                'trend': trend,
                'krs': [{'name': k.name, 'goal': k.goal_id.name, 'progress': k.progress, 'measure': k.measure} for k in krs]}
