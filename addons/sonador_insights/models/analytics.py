"""HR analytics over People, Payroll, Leave Requests, Performance and Recruitment, and the pulse survey calls."""
from collections import Counter, defaultdict

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.sonador_books.models.ethiopian_calendar import fiscal_periods, fiscal_year_of, format_ethiopian

from .pulse import MIN_GROUP

DEFAULT_QUESTIONS = [
    ('enps', 'How likely are you to recommend working here to a friend? (0-10)'),
    ('scale', 'I know what is expected of me at work.'),
    ('scale', 'My manager gives me useful feedback.'),
    ('scale', 'I have the tools and materials I need to do my job well.'),
    ('scale', 'I am paid fairly for the work I do.'),
    ('scale', 'I see myself working here in a year.'),
    ('text', 'What one thing would make this a better place to work?'),
]


class SbInsights(models.AbstractModel):
    _name = 'sb.insights'
    _description = 'HR analytics'

    def _is_hr(self):
        return self.env['sb.people']._is_hr()

    def _months(self, n=24):
        today = fields.Date.context_today(self)
        fy = fiscal_year_of(today)
        out = []
        for year in range(fy - 3, fy + 1):
            for _no, name, start, end in fiscal_periods(year):
                if start <= today:
                    out.append((start, end, name))
        return out[-n:]

    @api.model
    def dashboard(self):
        if not self._is_hr():
            raise AccessError(self.env._('HR analytics is for HR.'))
        c = self.env.company
        today = fields.Date.context_today(self)
        E = self.env['hr.employee'].sudo().with_context(active_test=False)
        emps = E.search([('company_id', '=', c.id)]).filtered(
            lambda e: (e.sb_payroll_id or e.current_version_id.contract_date_start) and (e.active or e.departure_date))  # archived without a leaving date: unknown, left out
        start = {e.id: e.current_version_id.contract_date_start or (e.version_ids.sorted('date_version')[:1].date_version) for e in emps}
        months = self._months(24)
        head, hires, leavers = [], [], []
        for ms, me, _n in months:
            head.append(sum(1 for e in emps if start[e.id] and start[e.id] <= me and (not e.departure_date or e.departure_date > me)))
            hires.append(sum(1 for e in emps if start[e.id] and ms <= start[e.id] <= me))
            leavers.append(sum(1 for e in emps if e.departure_date and ms <= e.departure_date <= me))
        last12 = sum(leavers[-12:])
        avg_head = (sum(head[-12:]) / 12.0) if head else 0
        active = emps.filtered('active')
        tenure, ages = Counter(), Counter()
        for e in active:
            s = start[e.id]
            yrs = relativedelta(today, s).years if s else None
            tenure['< 1 year' if yrs is not None and yrs < 1 else '1-2 years' if yrs is not None and yrs < 3 else
                   '3-5 years' if yrs is not None and yrs < 6 else '6-10 years' if yrs is not None and yrs < 11 else '10+ years' if yrs is not None else 'Unknown'] += 1
            if e.birthday:
                a = relativedelta(today, e.birthday).years
                ages['Under 25' if a < 25 else '25-34' if a < 35 else '35-44' if a < 45 else '45-54' if a < 55 else '55+'] += 1
            else:
                ages['Unknown'] += 1
        gender = Counter({'male': 'Men', 'female': 'Women'}.get(e.current_version_id.sex, 'Not set') for e in active)
        dept = Counter(e.department_id.name or 'No department' for e in active)
        # payroll cost by Ethiopian month (all payrolls, the earlier system's included)
        L = self.env['sb.payroll.line'].sudo()
        cost, overtime, dept_cost = [], [], Counter()
        for ms, me, _n in months[-12:]:
            lines = L.search([('run_id.company_id', '=', c.id), ('run_id.date', '>=', ms), ('run_id.date', '<=', me)])
            cost.append(round(sum(lines.mapped('gross')) + sum(lines.mapped('pension_er')), 2))
            overtime.append(round(sum(lines.mapped('overtime')), 2))
        last_cost = next(((v, months[-12:][i][2]) for i, v in reversed(list(enumerate(cost))) if v), (0, ''))
        recent = L.search([('run_id.company_id', '=', c.id), ('run_id.date', '>=', months[-3][0])])
        for l in recent:
            dept_cost[l.hr_employee_id.department_id.name or l.employee_id.department or 'No department'] += l.gross + l.pension_er
        # leave this fiscal year
        fy = c.compute_fiscalyear_dates(today)
        lv = self.env['sb.leave'].sudo().search([('company_id', '=', c.id), ('state', '=', 'approved'), ('date_from', '>=', fy['date_from'])])
        leave = Counter()
        for l in lv:
            leave[l.type_id.name] += l.days
        # performance: the latest closed cycle with manager scores
        C = self.env['sb.perf.cycle'].sudo()
        cyc = C.search([('company_id', '=', c.id), ('state', '=', 'closed'), ('kind', 'in', ('bi_annual', 'annual'))], limit=1) or \
            C.search([('company_id', '=', c.id), ('state', '=', 'closed')], limit=1)
        ratings = Counter(r.rating for r in cyc.review_ids.filtered(lambda r: r.role == 'manager' and r.rating))
        # recruitment
        A = self.env['sb.recruit.application'].sudo()
        rec = {'open_positions': self.env['hr.job'].sudo().search_count([('sb_open', '=', True), ('company_id', 'in', (c.id, False))]),
               'in_pipeline': A.search_count([('company_id', '=', c.id), ('stage', '!=', 'hired')]),
               'hired_fy': A.search_count([('company_id', '=', c.id), ('hired_on', '>=', fy['date_from'])])}
        labels = [n.split(' ')[0] + ' ' + n.split(' ')[-1][-2:] for _s, _e, n in months]
        return {
            'kpis': {'headcount': len(active), 'turnover': round(last12 / avg_head * 100.0, 1) if avg_head else 0.0, 'leavers12': last12,
                     'hires12': sum(hires[-12:]), 'payroll_month': last_cost[0], 'payroll_label': last_cost[1], 'women': gender.get('Women', 0)},
            'trend': {'labels': labels, 'head': head, 'hires': hires, 'leavers': leavers},
            'cost': {'labels': labels[-12:], 'cost': cost, 'overtime': overtime},
            'tenure': sorted(tenure.items(), key=lambda kv: ['< 1 year', '1-2 years', '3-5 years', '6-10 years', '10+ years', 'Unknown'].index(kv[0])),
            'ages': sorted(ages.items(), key=lambda kv: ['Under 25', '25-34', '35-44', '45-54', '55+', 'Unknown'].index(kv[0])),
            'gender': sorted(gender.items(), key=lambda kv: -kv[1]), 'dept': sorted(dept.items(), key=lambda kv: -kv[1]),
            'dept_cost': sorted(((k, round(v, 2)) for k, v in dept_cost.items()), key=lambda kv: -kv[1]),
            'leave': sorted(leave.items(), key=lambda kv: -kv[1]), 'fy': '%s - %s' % (format_ethiopian(fy['date_from']), format_ethiopian(fy['date_to'])),
            'ratings': {'cycle': cyc.name or '', 'rows': sorted(ratings.items(), key=lambda kv: -kv[1])}, 'recruit': rec,
        }

    # ------------------------------------------------------------------ pulse surveys
    def _me(self):
        return self.env['sb.people']._me()

    @api.model
    def surveys(self):
        me = self._me()
        P = self.env['sb.pulse'].sudo()
        dom = [('company_id', '=', self.env.company.id)]
        if not self._is_hr():
            dom.append(('state', '=', 'open'))
        heads = self.env['hr.employee'].sudo().search_count([('company_id', '=', self.env.company.id), ('active', '=', True)])
        out = []
        for p in P.search(dom):
            out.append({'id': p.id, 'name': p.name, 'state': p.state, 'open': format_ethiopian(p.date_open) if p.date_open else '',
                        'close': format_ethiopian(p.date_close) if p.date_close else '', 'responses': len(p.response_ids),
                        'heads': heads, 'taken': bool(me and p.taken_ids.filtered(lambda t: t.employee_id == me)),
                        'questions': [{'id': q.id, 'name': q.name, 'kind': q.kind} for q in p.question_ids]})
        return {'surveys': out, 'is_hr': self._is_hr(), 'me': me.id or False}

    @api.model
    def survey_save(self, vals):
        if not self._is_hr():
            raise AccessError(self.env._('Surveys are run by HR.'))
        P = self.env['sb.pulse'].sudo()
        p = P.browse(int(vals['id'])) if vals.get('id') else P.create({'name': vals['name'],
                                                                      'question_ids': [(0, 0, {'kind': k, 'name': n, 'sequence': i}) for i, (k, n) in enumerate(DEFAULT_QUESTIONS)]})
        data = {'name': vals['name'], 'date_close': vals.get('close') or False}
        if 'state' in vals:
            data['state'] = vals['state']
            if vals['state'] == 'open':
                data['date_open'] = fields.Date.context_today(self)
        p.write(data)
        if vals.get('questions') is not None and not p.response_ids:
            p.question_ids.unlink()
            p.question_ids = [(0, 0, {'kind': q.get('kind') or 'scale', 'name': q['name'], 'sequence': i}) for i, q in enumerate(vals['questions']) if q.get('name')]
        return self.surveys()

    @api.model
    def take(self, pulse_id, answers):
        me = self._me()
        if not me:
            raise UserError(self.env._('Your login is not linked to an employee.'))
        p = self.env['sb.pulse'].sudo().browse(int(pulse_id))
        if p.state != 'open':
            raise UserError(self.env._('This survey is closed.'))
        if p.taken_ids.filtered(lambda t: t.employee_id == me):
            raise UserError(self.env._('You have already answered this survey.'))
        lines = []
        for q in p.question_ids:
            a = (answers or {}).get(str(q.id))
            if q.kind == 'text':
                if a:
                    lines.append((0, 0, {'question_id': q.id, 'text': str(a)[:2000]}))
                continue
            if a in (None, ''):
                raise ValidationError(self.env._('Answer: %s', q.name))
            v = int(a)
            if (q.kind == 'enps' and not 0 <= v <= 10) or (q.kind == 'scale' and not 1 <= v <= 5):
                raise ValidationError(self.env._('Out of range: %s', q.name))
            lines.append((0, 0, {'question_id': q.id, 'value': v}))
        # who answered and what was answered are stored apart: the answers carry the department only
        self.env['sb.pulse.taken'].sudo().create({'pulse_id': p.id, 'employee_id': me.id})
        self.env['sb.pulse.response'].sudo().create({'pulse_id': p.id, 'department': me.department_id.name or False, 'answer_ids': lines})
        return self.surveys()

    @api.model
    def results(self, pulse_id):
        if not self._is_hr():
            raise AccessError(self.env._('Results are for HR.'))
        p = self.env['sb.pulse'].sudo().browse(int(pulse_id))
        out = {'name': p.name, 'responses': len(p.response_ids), 'questions': [], 'enps': None, 'by_dept': []}
        by_dept = defaultdict(list)
        for r in p.response_ids:
            by_dept[r.department or 'No department'].append(r)
        for q in p.question_ids:
            ans = p.response_ids.answer_ids.filtered(lambda a: a.question_id == q)
            row = {'id': q.id, 'name': q.name, 'kind': q.kind, 'count': len(ans)}
            if q.kind == 'enps' and ans:
                vals = ans.mapped('value')
                pro = sum(1 for v in vals if v >= 9) / len(vals) * 100
                det = sum(1 for v in vals if v <= 6) / len(vals) * 100
                row.update(score=round(pro - det), promoters=round(pro), detractors=round(det), passives=round(100 - pro - det))
                out['enps'] = row['score']
            elif q.kind == 'scale' and ans:
                vals = ans.mapped('value')
                row.update(avg=round(sum(vals) / len(vals), 2), dist=[sum(1 for v in vals if v == k) for k in (1, 2, 3, 4, 5)],
                           favourable=round(sum(1 for v in vals if v >= 4) / len(vals) * 100))
            elif q.kind == 'text':
                row['comments'] = [a.text for a in ans if a.text][:200]
            out['questions'].append(row)
        enps_q = p.question_ids.filtered(lambda q: q.kind == 'enps')[:1]
        for d, rs in sorted(by_dept.items()):
            if len(rs) < MIN_GROUP:
                out['by_dept'].append({'name': d, 'n': len(rs), 'hidden': True})
                continue
            vals = [a.value for r in rs for a in r.answer_ids if a.question_id == enps_q]
            enps = round((sum(1 for v in vals if v >= 9) - sum(1 for v in vals if v <= 6)) / len(vals) * 100) if vals else None
            scale = [a.value for r in rs for a in r.answer_ids if a.question_id.kind == 'scale']
            out['by_dept'].append({'name': d, 'n': len(rs), 'hidden': False, 'enps': enps,
                                   'favourable': round(sum(1 for v in scale if v >= 4) / len(scale) * 100) if scale else None})
        out['min_group'] = MIN_GROUP
        return out
