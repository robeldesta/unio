"""Recruitment screens. HR runs openings and the pipeline; interviewers see and score the interviews they are on;
hiring hands the candidate to People (and onboarding starts)."""
from collections import Counter, defaultdict
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.sonador_books.models.ethiopian_calendar import format_ethiopian

from .recruit import STAGES


def _s(d):
    return fields.Date.to_string(d) if d else False


class SbRecruit(models.AbstractModel):
    _name = 'sb.recruit'
    _description = 'Recruitment'

    @api.model
    def _hide_odoo_recruitment(self):
        menu = self.env.ref('hr_recruitment.menu_hr_recruitment_root', raise_if_not_found=False)
        if menu:
            menu.sudo().group_ids = [(6, 0, self.env.ref('sonador_books.group_sb_legacy_apps').ids)]

    def _is_hr(self):
        return self.env['sb.people']._is_hr()

    def _me(self):
        return self.env['sb.people']._me()

    def _require_hr(self):
        if not self._is_hr():
            raise AccessError(self.env._('Recruitment is run by HR.'))

    # ------------------------------------------------------------------ context
    @api.model
    def ctx(self):
        c = self.env.company
        return {'is_hr': self._is_hr(), 'me': self._me().id or False, 'stages': [{'id': k, 'name': v} for k, v in STAGES],
                'reasons': [{'id': r.id, 'name': r.name, 'by_candidate': r.by_candidate} for r in self.env['sb.recruit.reason'].sudo().search([])],
                'departments': [{'id': d.id, 'name': d.name} for d in self.env['hr.department'].sudo().search([('company_id', 'in', (c.id, False))], order='name')],
                'people': [{'id': e.id, 'name': e.name} for e in self.env['hr.employee'].sudo().search([('company_id', '=', c.id), ('active', '=', True)], order='name')],
                'today': _s(fields.Date.context_today(self))}

    # ------------------------------------------------------------------ openings
    def _job_dict(self, j, counts=None):
        counts = counts or {}
        return {'id': j.id, 'name': j.name, 'open': j.sb_open, 'openings': j.sb_openings, 'department': (j.sb_department_id or j.department_id).name or '',
                'department_id': (j.sb_department_id or j.department_id).id, 'manager': j.sb_hiring_manager_id.name or '',
                'manager_id': j.sb_hiring_manager_id.id, 'close': _s(j.sb_close_date), 'close_ec': format_ethiopian(j.sb_close_date) if j.sb_close_date else '',
                'salary_from': j.sb_salary_from, 'salary_to': j.sb_salary_to, 'description': j.sb_description or '', 'requirements': j.sb_requirements or '',
                'counts': counts.get(j.id, {}), 'total': sum(counts.get(j.id, {}).values()),
                'employees': j.no_of_employee if 'no_of_employee' in j._fields else 0}

    @api.model
    def openings(self, show_closed=False):
        self._require_hr()
        A = self.env['sb.recruit.application'].sudo()
        counts = defaultdict(Counter)
        for job, stage, n in A._read_group([('company_id', '=', self.env.company.id), ('active', '=', True)], ['job_id', 'stage'], ['__count']):
            counts[job.id][stage] = n
        week = fields.Date.context_today(self) - timedelta(days=7)
        new = dict((j.id, n) for j, n in A._read_group([('company_id', '=', self.env.company.id), ('applied_on', '>=', week)], ['job_id'], ['__count']))
        J = self.env['hr.job'].sudo()
        dom = [('company_id', 'in', (self.env.company.id, False))]
        with_apps = [j for j in counts if j]
        jobs = J.search(dom + (['|', ('sb_open', '=', True), ('id', 'in', with_apps)] if show_closed else [('sb_open', '=', True)]))
        out = [dict(self._job_dict(j, counts), new_week=new.get(j.id, 0)) for j in jobs]
        unassigned = sum(counts[False].values()) if False in counts else 0
        return {'jobs': sorted(out, key=lambda x: (-x['total'], x['name'])), 'unassigned': unassigned,
                'closed_with_applicants': len([j for j in counts if j and j not in jobs.ids])}

    @api.model
    def opening_save(self, vals):
        self._require_hr()
        J = self.env['hr.job'].sudo()
        j = J.browse(int(vals['id'])) if vals.get('id') else J.create({'name': vals['name'], 'company_id': self.env.company.id})
        j.write({'name': vals['name'], 'sb_open': bool(vals.get('open', True)), 'sb_openings': int(vals.get('openings') or 1),
                 'sb_department_id': int(vals['department_id']) if vals.get('department_id') else False,
                 'sb_hiring_manager_id': int(vals['manager_id']) if vals.get('manager_id') else False,
                 'sb_close_date': vals.get('close') or False, 'sb_salary_from': float(vals.get('salary_from') or 0),
                 'sb_salary_to': float(vals.get('salary_to') or 0), 'sb_description': vals.get('description') or False,
                 'sb_requirements': vals.get('requirements') or False})
        return j.id

    # ------------------------------------------------------------------ pipeline
    def _app_card(self, a):
        c = a.candidate_id
        ivs = a.interview_ids
        scored = ivs.filtered('score')
        return {'id': a.id, 'name': c.name, 'email': c.email or '', 'phone': c.phone or '', 'stage': a.stage, 'rating': a.rating,
                'applied': _s(a.applied_on), 'applied_ec': format_ethiopian(a.applied_on) if a.applied_on else '',
                'job': a.job_id.name or '', 'job_id': a.job_id.id, 'active': a.active, 'reason': a.reason_id.name or '',
                'interviews': len(ivs), 'score': round(sum(scored.mapped('score')) / len(scored), 1) if scored else None,
                'expected': a.expected_salary, 'education': c.education or '', 'hired': bool(c.employee_id)}

    @api.model
    def pipeline(self, job_id, search='', archived=False, limit=400):
        self._require_hr()
        A = self.env['sb.recruit.application'].sudo().with_context(active_test=False)
        dom = [('company_id', '=', self.env.company.id), ('active', '=', not archived)]
        dom.append(('job_id', '=', int(job_id) if job_id else False))
        if search:
            dom += ['|', '|', ('candidate_id.name', 'ilike', search), ('candidate_id.email', 'ilike', search), ('candidate_id.phone', 'ilike', search)]
        apps = A.search(dom, limit=limit)
        cols = {k: [] for k, _v in STAGES}
        for a in apps:
            cols[a.stage].append(self._app_card(a))
        job = self.env['hr.job'].sudo().browse(int(job_id)) if job_id else None
        return {'job': self._job_dict(job) if job else {'id': False, 'name': 'Not linked to an opening'}, 'columns': cols,
                'count': len(apps), 'archived': archived}

    @api.model
    def application(self, app_id):
        a = self.env['sb.recruit.application'].sudo().with_context(active_test=False).browse(int(app_id))
        me = self._me()
        if not self._is_hr() and me not in a.interview_ids.interviewer_ids:
            raise AccessError(self.env._('Not allowed.'))
        c = a.candidate_id
        return dict(self._app_card(a), candidate={'id': c.id, 'name': c.name, 'email': c.email or '', 'phone': c.phone or '',
                                                   'linkedin': c.linkedin or '', 'education': c.education or '', 'experience_years': c.experience_years,
                                                   'city': c.city or '', 'notes': c.notes or '', 'source': c.source or '',
                                                   'cv': {'id': c.cv_id.id, 'name': c.cv_id.name} if c.cv_id else False,
                                                   'employee_id': c.employee_id.id},
                    notes=a.notes or '', proposed=a.proposed_salary, is_hr=self._is_hr(),
                    others=[self._app_card(x) for x in c.with_context(active_test=False).application_ids if x != a],
                    interviews=[self._iv_dict(i) for i in a.interview_ids])

    def _iv_dict(self, i):
        me = self._me()
        return {'id': i.id, 'when': fields.Datetime.to_string(i.when), 'kind': i.kind, 'place': i.place or '', 'done': i.done,
                'interviewers': [{'id': e.id, 'name': e.name} for e in i.interviewer_ids],
                'score_skills': i.score_skills, 'score_experience': i.score_experience, 'score_attitude': i.score_attitude,
                'score_communication': i.score_communication, 'score': i.score, 'recommendation': i.recommendation or '',
                'notes': i.notes or '', 'can_score': self._is_hr() or (me and me in i.interviewer_ids),
                'candidate': i.application_id.candidate_id.name, 'job': i.application_id.job_id.name or '', 'application_id': i.application_id.id}

    @api.model
    def application_save(self, vals):
        self._require_hr()
        C = self.env['sb.recruit.candidate'].sudo()
        A = self.env['sb.recruit.application'].sudo().with_context(active_test=False)
        cv = vals.get('candidate') or {}
        if not (cv.get('name') or '').strip():
            raise ValidationError(self.env._('Enter the candidate\'s name.'))
        a = A.browse(int(vals['id'])) if vals.get('id') else A
        c = a.candidate_id if a else (C.search([('email', '=ilike', cv['email'])], limit=1) if cv.get('email') else C)
        cdata = {k: cv.get(k) or False for k in ('name', 'email', 'phone', 'linkedin', 'education', 'city', 'notes', 'source')}
        cdata['experience_years'] = float(cv.get('experience_years') or 0)
        if c:
            c.write(cdata)
        else:
            c = C.create(cdata)
        if cv.get('cv_data'):
            c.cv_id = self.env['ir.attachment'].sudo().create({'name': cv.get('cv_name') or 'CV', 'datas': cv['cv_data'],
                                                                'res_model': 'sb.recruit.candidate', 'res_id': c.id})
        adata = {'job_id': int(vals['job_id']) if vals.get('job_id') else False, 'expected_salary': float(vals.get('expected') or 0),
                 'proposed_salary': float(vals.get('proposed') or 0), 'rating': int(vals.get('rating') or 0), 'notes': vals.get('notes') or False}
        if a:
            a.write(adata)
        else:
            a = A.create(dict(adata, candidate_id=c.id, owner_id=self.env.user.id))
        return self.application(a.id)

    @api.model
    def move(self, app_id, stage):
        self._require_hr()
        a = self.env['sb.recruit.application'].sudo().browse(int(app_id))
        if stage == 'hired':
            raise UserError(self.env._('Use Hire: it creates the employee in People.'))
        a.write({'stage': stage, 'stage_date': fields.Date.context_today(self)})
        return self._app_card(a)

    @api.model
    def reject(self, app_id, reason_id):
        self._require_hr()
        a = self.env['sb.recruit.application'].sudo().browse(int(app_id))
        a.write({'active': False, 'reason_id': int(reason_id) if reason_id else False})
        return True

    @api.model
    def restore(self, app_id):
        self._require_hr()
        self.env['sb.recruit.application'].sudo().with_context(active_test=False).browse(int(app_id)).write({'active': True, 'reason_id': False})
        return True

    @api.model
    def rate(self, app_id, rating):
        self._require_hr()
        self.env['sb.recruit.application'].sudo().browse(int(app_id)).rating = int(rating)
        return True

    # ------------------------------------------------------------------ interviews
    @api.model
    def interview_save(self, vals):
        I = self.env['sb.recruit.interview'].sudo()
        i = I.browse(int(vals['id'])) if vals.get('id') else I
        me = self._me()
        if i and not (self._is_hr() or me in i.interviewer_ids):
            raise AccessError(self.env._('Only the interviewers score this interview.'))
        if not i and not self._is_hr():
            raise AccessError(self.env._('Interviews are scheduled by HR.'))
        data = {}
        for k in ('score_skills', 'score_experience', 'score_attitude', 'score_communication'):
            if k in vals:
                v = int(vals.get(k) or 0)
                if v and not 1 <= v <= 5:
                    raise ValidationError(self.env._('Scores are 1 to 5.'))
                data[k] = v
        for k in ('recommendation', 'notes', 'place', 'kind'):
            if k in vals:
                data[k] = vals.get(k) or False
        if 'done' in vals:
            data['done'] = bool(vals['done'])
        if self._is_hr():
            if vals.get('when'):
                data['when'] = vals['when'].replace('T', ' ') + (':00' if len(vals['when']) == 16 else '')
            if 'interviewer_ids' in vals:
                data['interviewer_ids'] = [(6, 0, [int(x) for x in vals['interviewer_ids'] or []])]
        if i:
            i.write(data)
        else:
            i = I.create(dict(data, application_id=int(vals['application_id'])))
            a = i.application_id
            if a.stage in ('new', 'screening'):
                a.write({'stage': 'interview', 'stage_date': fields.Date.context_today(self)})
        return self._iv_dict(i)

    @api.model
    def my_interviews(self):
        me = self._me()
        I = self.env['sb.recruit.interview'].sudo()
        dom = [('interviewer_ids', 'in', me.ids or [-1])] if not self._is_hr() else ['|', ('interviewer_ids', 'in', me.ids or [-1]), ('done', '=', False)]
        return [self._iv_dict(i) for i in I.search(dom, limit=100)]

    # ------------------------------------------------------------------ hire
    @api.model
    def hire(self, app_id, vals):
        self._require_hr()
        a = self.env['sb.recruit.application'].sudo().browse(int(app_id))
        c = a.candidate_id
        if c.employee_id:
            raise UserError(self.env._('%s is already an employee.', c.name))
        job = a.job_id
        eid = self.env['sb.people'].hire({
            'name': c.name, 'job': job.name if job else vals.get('job'), 'start': vals.get('start'),
            'department_id': vals.get('department_id') or (job.sb_department_id or job.department_id).id or False,
            'parent_id': vals.get('parent_id') or job.sb_hiring_manager_id.id or False, 'wage': vals.get('wage') or a.proposed_salary,
            'email': vals.get('email') or False, 'phone': c.phone or False, 'type': vals.get('type') or 'employee',
            'contract_end': vals.get('contract_end') or False})
        e = self.env['hr.employee'].sudo().browse(eid)
        if c.email:
            e.private_email = c.email
        c.employee_id = e
        a.write({'stage': 'hired', 'stage_date': fields.Date.context_today(self), 'hired_on': fields.Date.context_today(self)})
        if job and job.sb_open:
            hired = self.env['sb.recruit.application'].sudo().search_count([('job_id', '=', job.id), ('stage', '=', 'hired')])
            if hired >= job.sb_openings:
                job.sb_open = False
        return eid

    # ------------------------------------------------------------------ numbers
    @api.model
    def analytics(self):
        self._require_hr()
        A = self.env['sb.recruit.application'].sudo().with_context(active_test=False)
        apps = A.search([('company_id', '=', self.env.company.id)])
        order = [k for k, _v in STAGES]
        reached = Counter()
        for a in apps:
            for k in order[:order.index(a.stage) + 1]:
                reached[k] += 1
        reasons = Counter(a.reason_id.name or 'No reason given' for a in apps if not a.active)
        hired = apps.filtered('hired_on')
        tth = [(a.hired_on - a.applied_on).days for a in hired if a.applied_on]
        by_month = Counter(a.applied_on.strftime('%Y-%m') for a in apps if a.applied_on)
        months = sorted(by_month)[-12:]
        return {'funnel': [{'stage': dict(STAGES)[k], 'count': reached[k]} for k in order],
                'reasons': sorted(reasons.items(), key=lambda kv: -kv[1]),
                'time_to_hire': round(sum(tth) / len(tth)) if tth else None, 'hired': len(hired), 'total': len(apps),
                'active': len(apps.filtered('active')), 'by_month': {'labels': months, 'values': [by_month[m] for m in months]}}
