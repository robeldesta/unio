"""The report card: every subject by semester, total, average, rank among the class, conduct and days absent."""
from odoo import api, models

try:
    from odoo.addons.sonador_print.models.engine import ethiopic_font_face
except ImportError:  # printing without the print designer installed
    def ethiopic_font_face():
        return ''


class ReportCard(models.AbstractModel):
    _name = 'report.sonador_school.report_card_document'
    _description = 'Report card'

    @api.model
    def _get_report_values(self, docids, data=None):
        data = data or {}
        students = self.env['school.student'].sudo().browse(docids).exists()
        Svc = self.env['school.service']
        T = self.env['school.term'].sudo()
        term = T.browse(int(data['term'])) if data.get('term') else T.browse()
        cards, cache = [], {}
        for s in students:
            sec = s.section_id
            year = sec.year_id
            t = term if term and term.year_id == year else year.term_ids[:1]
            terms = year.term_ids.filtered(lambda x: x.sequence <= t.sequence)
            per = []
            for x in terms:
                key = (sec.id, x.id)
                if key not in cache:
                    cache[key] = Svc.results(sec.id, x.id)
                res = cache[key]
                per.append({'term': x, 'row': next((r for r in res['rows'] if r['id'] == s.id), None), 'count': res['count']})
            subjects = []
            for sub in sec.grade_id.subject_ids:
                marks = [p['row']['marks'].get(str(sub.id)) if p['row'] else None for p in per]
                done = [m for m in marks if m is not None]
                subjects.append({'name': sub.name, 'name_am': sub.name_am or '', 'marks': marks,
                                 'average': round(sum(done) / len(done), 1) if done else None})
            avgs = [p['row']['average'] for p in per if p['row'] and p['row']['average'] is not None]
            overall = round(sum(avgs) / len(avgs), 2) if avgs else None
            last = t == year.term_ids[-1:]
            conduct = {r.term_id.id: r for r in self.env['school.term.result'].sudo().search([('student_id', '=', s.id), ('term_id', 'in', terms.ids)])}
            cards.append({
                's': s, 'section': sec, 'year': year, 'terms': per, 'subjects': subjects, 'overall': overall,
                'conduct': [conduct[p['term'].id].conduct if p['term'].id in conduct else '' for p in per],
                'comment': conduct[t.id].comment if t.id in conduct else '',
                'final': last and overall is not None,
                'promoted': overall is not None and overall >= 50,
                'next_grade': sec.grade_id.next_grade_id,
            })
        return {'doc_ids': docids, 'docs': students, 'cards': cards, 'company': self.env.company, 'font_face': ethiopic_font_face()}
