"""What the School app shows and does (one service, used by the OWL screens)."""
from collections import defaultdict
from datetime import timedelta

from odoo import Command, api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.sonador_books.models.ethiopian_calendar import format_ethiopian


def _s(d):
    return fields.Date.to_string(d) if d else False


class SchoolService(models.AbstractModel):
    _name = 'school.service'
    _description = 'School'

    # ------------------------------------------------------------------ who
    def _office(self):
        return self.env.user.has_group('sonador_school.group_office')

    def _need_office(self):
        if not self._office():
            raise AccessError(self.env._('This is for the school office.'))

    def _me(self):
        return self.env['sb.people']._me()

    def _my_sections(self, year):
        S = self.env['school.section'].sudo()
        if self._office():
            return S.search([('year_id', '=', year.id)])
        me = self._me()
        if not me:
            return S.browse()
        return S.search([('year_id', '=', year.id), '|', ('homeroom_id', '=', me.id), ('teacher_ids.teacher_id', '=', me.id)])

    @api.model
    def ctx(self):
        Y = self.env['school.year'].sudo()
        year = Y.search([('current', '=', True)], limit=1) or Y.search([], limit=1)
        if not year:
            return {'setup': True, 'office': self._office()}
        today = fields.Date.context_today(self)
        term = year.term_ids.filtered(lambda t: t.date_start <= today <= t.date_end)[:1] or year.term_ids[:1]
        secs = self._my_sections(year)
        return {'setup': False, 'office': self._office(), 'head': self.env.user.has_group('sonador_school.group_head'),
                'year': {'id': year.id, 'name': year.name}, 'today': _s(today), 'today_ec': format_ethiopian(today),
                'terms': [{'id': t.id, 'name': t.name} for t in year.term_ids], 'term_id': term.id,
                'grades': [{'id': g.id, 'name': g.name, 'level': g.level} for g in self.env['school.grade'].sudo().search([])],
                'sections': [{'id': s.id, 'name': s.display_name, 'grade_id': s.grade_id.id, 'count': len(s.student_ids.filtered(lambda x: x.state == 'enrolled')),
                              'homeroom': s.homeroom_id.name or ''} for s in secs.sorted(lambda s: (s.grade_id.sequence, s.name))],
                'currency': self.env.company.currency_id.symbol or 'Br'}

    # ------------------------------------------------------------------ overview
    @api.model
    def overview(self):
        self._need_office()
        St = self.env['school.student'].sudo()
        enrolled = St.search([('state', '=', 'enrolled')])
        year = self.env['school.year'].sudo().get_current()
        today = fields.Date.context_today(self)
        by_grade = defaultdict(lambda: {'m': 0, 'f': 0})
        for s in enrolled:
            by_grade[s.grade_id.name or '-'][s.sex] += 1
        grades = [{'name': g.name, 'm': by_grade[g.name]['m'], 'f': by_grade[g.name]['f']}
                  for g in self.env['school.grade'].sudo().search([]) if g.name in by_grade]
        B = self.env['school.bill'].sudo()
        bills = B.search([('state', '=', 'posted'), ('date', '>=', year.date_start), ('date', '<=', year.date_end)])
        billed = sum(bills.mapped('amount'))
        owed = sum(bills.mapped('residual'))
        month0 = today.replace(day=1)
        A = self.env['school.attendance.line'].sudo()
        todays = A.search([('date', '=', today)])
        week = A.search([('date', '>=', today - timedelta(days=6))])
        rate = lambda ls: round(100.0 * len(ls.filtered(lambda l: l.status in ('present', 'late'))) / len(ls), 1) if ls else None  # noqa: E731
        families = defaultdict(float)
        for b in bills:
            if b.residual > 0.01:
                families[b.payer_id] += b.residual
        top = sorted(families.items(), key=lambda x: -x[1])[:8]
        return {'students': len(enrolled), 'girls': len(enrolled.filtered(lambda s: s.sex == 'f')),
                'applicants': St.search_count([('state', 'in', ('applicant', 'admitted'))]), 'grades': grades,
                'billed': billed, 'collected': billed - owed, 'owed': owed, 'collected_pct': round(100.0 * (billed - owed) / billed, 1) if billed else 0,
                'families_owing': len(families), 'attendance_today': rate(todays), 'attendance_week': rate(week),
                'registers_today': len(todays.attendance_id), 'sections': self.env['school.section'].sudo().search_count([('year_id', '=', year.id)]),
                'top_owing': [{'id': p.id, 'name': p.name, 'phone': p.phone or '', 'owed': v,
                               'children': ', '.join(St.search([('payer_id', '=', p.id), ('state', '=', 'enrolled')]).mapped('name'))} for p, v in top]}

    # ------------------------------------------------------------------ students
    def _student_row(self, s):
        return {'id': s.id, 'name': s.name, 'name_am': s.name_am or '', 'no': s.student_no or '', 'sex': s.sex,
                'class': s.section_id.display_name or (s.applied_grade_id.name and 'Applying: %s' % s.applied_grade_id.name) or '',
                'section_id': s.section_id.id, 'state': s.state, 'payer': s.payer_id.name or '', 'phone': s.payer_id.phone or '',
                'photo': '/web/image/school.student/%d/photo/128x128' % s.id if s.photo else False,
                'scholarship': s.scholarship_pct, 'transport': s.transport}

    @api.model
    def students(self, search='', section_id=False, state='enrolled'):
        dom = []
        if state:
            dom.append(('state', 'in', ['applicant', 'admitted'] if state == 'applicants' else [state]))
        if section_id:
            dom.append(('section_id', '=', int(section_id)))
        if search:
            dom += ['|', '|', '|', ('name', 'ilike', search), ('name_am', 'ilike', search), ('student_no', 'ilike', search),
                    ('payer_id.name', 'ilike', search)]
        if not self._office():
            year = self.env['school.year'].sudo().get_current()
            dom.append(('section_id', 'in', self._my_sections(year).ids))
        return [self._student_row(s) for s in self.env['school.student'].sudo().search(dom, limit=400, order='name')]

    @api.model
    def student(self, student_id):
        s = self.env['school.student'].sudo().browse(int(student_id))
        if not self._office():
            self._section(s.section_id.id)
        out = self._student_row(s)
        out.update({'birth_date': _s(s.birth_date), 'birth_ec': format_ethiopian(s.birth_date) if s.birth_date else '',
                    'payer_id': s.payer_id.id, 'second_guardian': s.second_guardian or '', 'second_phone': s.second_phone or '',
                    'medical': s.medical or '', 'joined': _s(s.joined), 'previous_school': s.previous_school or '',
                    'applied_grade_id': s.applied_grade_id.id, 'siblings': [{'id': x.id, 'name': x.name, 'class': x.section_id.display_name or ''}
                                                                             for x in s.siblings()]})
        A = self.env['school.attendance.line'].sudo().search([('student_id', '=', s.id)])
        out['attendance'] = {'days': len(A), 'absent': len(A.filtered(lambda l: l.status == 'absent')),
                             'late': len(A.filtered(lambda l: l.status == 'late'))}
        if self._office() and s.payer_id:
            out['family'] = self.family(s.payer_id.id)
        return out

    @api.model
    def save_student(self, vals):
        self._need_office()
        St = self.env['school.student'].sudo()
        payer = vals.get('payer_id')
        if not payer and vals.get('payer_name'):
            payer = self.env['res.partner'].sudo().create({'name': vals['payer_name'], 'phone': vals.get('payer_phone') or False,
                                                           'customer_rank': 1}).id
        keys = ('name', 'name_am', 'sex', 'birth_date', 'applied_grade_id', 'section_id', 'second_guardian', 'second_phone',
                'scholarship_pct', 'transport', 'medical', 'previous_school', 'state')
        v = {k: vals[k] for k in keys if k in vals}
        for k in ('applied_grade_id', 'section_id'):
            if k in v:
                v[k] = int(v[k]) if v[k] else False
        if payer:
            v['payer_id'] = int(payer)
        if vals.get('photo'):
            v['photo'] = vals['photo']
        s = St.browse(int(vals['id'])) if vals.get('id') else St
        if s:
            s.write(v)
        else:
            s = St.create(v)
        if s.section_id and s.state in ('applicant', 'admitted'):
            s.state = 'enrolled'
        return self.student(s.id)

    @api.model
    def set_state(self, student_id, state, section_id=False, reason=''):
        self._need_office()
        s = self.env['school.student'].sudo().browse(int(student_id))
        vals = {'state': state}
        if state == 'enrolled':
            if not section_id:
                raise UserError(self.env._('Choose the class.'))
            vals['section_id'] = int(section_id)
        if state == 'left':
            vals['left_reason'] = reason or False
        s.write(vals)
        return self.student(s.id)

    # ------------------------------------------------------------------ attendance
    def _section(self, section_id):
        sec = self.env['school.section'].sudo().browse(int(section_id))
        if not self._office() and sec not in self._my_sections(sec.year_id):
            raise AccessError(self.env._('Not your class.'))
        return sec

    @api.model
    def register(self, section_id, day):
        sec = self._section(section_id)
        day = fields.Date.to_date(day)
        att = self.env['school.attendance'].sudo().search([('section_id', '=', sec.id), ('date', '=', day)], limit=1)
        st = {l.student_id.id: l for l in att.line_ids}
        students = sec.student_ids.filtered(lambda s: s.state == 'enrolled').sorted('name')
        return {'section': sec.display_name, 'date': _s(day), 'date_ec': format_ethiopian(day), 'taken': bool(att),
                'rows': [{'id': s.id, 'name': s.name, 'no': s.student_no, 'status': st[s.id].status if s.id in st else 'present',
                          'note': st[s.id].note or '' if s.id in st else ''} for s in students]}

    @api.model
    def save_register(self, section_id, day, rows):
        sec = self._section(section_id)
        Att = self.env['school.attendance'].sudo()
        att = Att.search([('section_id', '=', sec.id), ('date', '=', day)], limit=1) or Att.create({'section_id': sec.id, 'date': day})
        att.line_ids.unlink()
        att.line_ids = [Command.create({'student_id': int(r['id']), 'status': r['status'], 'note': r.get('note') or False}) for r in rows]
        att.taken_by = self.env.user
        return self.register(section_id, day)

    # ------------------------------------------------------------------ marks
    @api.model
    def marks(self, section_id, term_id, subject_id):
        sec = self._section(section_id)
        Ass = self.env['school.assessment'].sudo().search([])
        M = self.env['school.mark'].sudo().search([('section_id', '=', sec.id), ('term_id', '=', int(term_id)), ('subject_id', '=', int(subject_id))])
        got = {(m.student_id.id, m.assessment_id.id): m.score for m in M}
        students = sec.student_ids.filtered(lambda s: s.state == 'enrolled').sorted('name')
        return {'section': sec.display_name, 'subjects': [{'id': x.id, 'name': x.name} for x in sec.grade_id.subject_ids],
                'assessments': [{'id': a.id, 'name': a.name, 'max': a.max_mark} for a in Ass],
                'rows': [{'id': s.id, 'name': s.name, 'no': s.student_no,
                          'scores': {str(a.id): got.get((s.id, a.id)) for a in Ass}} for s in students]}

    @api.model
    def save_marks(self, section_id, term_id, subject_id, rows):
        sec = self._section(section_id)
        M = self.env['school.mark'].sudo()
        Ass = {a.id: a for a in self.env['school.assessment'].sudo().search([])}
        for r in rows:
            for aid, score in (r.get('scores') or {}).items():
                a = Ass.get(int(aid))
                if not a:
                    continue
                key = [('term_id', '=', int(term_id)), ('student_id', '=', int(r['id'])), ('subject_id', '=', int(subject_id)), ('assessment_id', '=', a.id)]
                m = M.search(key, limit=1)
                if score in (None, ''):
                    m.unlink()
                    continue
                score = float(score)
                if score < 0 or score > a.max_mark:
                    raise UserError(self.env._('%(n)s: %(a)s is out of %(m)s.', n=self.env['school.student'].browse(int(r['id'])).name, a=a.name, m=a.max_mark))
                if m:
                    m.score = score
                else:
                    M.create({'term_id': int(term_id), 'section_id': sec.id, 'student_id': int(r['id']), 'subject_id': int(subject_id),
                              'assessment_id': a.id, 'score': score})
        return self.marks(section_id, term_id, subject_id)

    # ------------------------------------------------------------------ results and report cards
    @api.model
    def results(self, section_id, term_id):
        sec = self._section(section_id)
        term = self.env['school.term'].sudo().browse(int(term_id))
        subjects = sec.grade_id.subject_ids
        M = self.env['school.mark'].sudo().search([('section_id', '=', sec.id), ('term_id', '=', term.id)])
        tot = defaultdict(float)
        has = set()
        for m in M:
            tot[(m.student_id.id, m.subject_id.id)] += m.score
            has.add((m.student_id.id, m.subject_id.id))
        students = sec.student_ids.filtered(lambda s: s.state == 'enrolled')
        A = self.env['school.attendance.line'].sudo().search([('student_id', 'in', students.ids), ('date', '>=', term.date_start), ('date', '<=', term.date_end)])
        TR = {r.student_id.id: r for r in self.env['school.term.result'].sudo().search([('term_id', '=', term.id), ('student_id', 'in', students.ids)])}
        rows = []
        for s in students:
            marks = {str(x.id): round(tot[(s.id, x.id)], 1) if (s.id, x.id) in has else None for x in subjects}
            done = [v for v in marks.values() if v is not None]
            total = round(sum(done), 1)
            avg = round(total / len(done), 2) if done else None
            mine = A.filtered(lambda l: l.student_id == s)
            rows.append({'id': s.id, 'name': s.name, 'no': s.student_no, 'sex': s.sex, 'marks': marks, 'total': total, 'average': avg,
                         'absent': len(mine.filtered(lambda l: l.status == 'absent')), 'complete': len(done) == len(subjects),
                         'conduct': TR[s.id].conduct or '' if s.id in TR else '', 'comment': TR[s.id].comment or '' if s.id in TR else ''})
        ranked = sorted([r for r in rows if r['average'] is not None], key=lambda r: -r['average'])
        rank, prev = 0, None
        for i, r in enumerate(ranked):
            if r['average'] != prev:
                rank = i + 1
                prev = r['average']
            r['rank'] = rank
        for r in rows:
            r.setdefault('rank', None)
            r['result'] = None if r['average'] is None else ('Pass' if r['average'] >= 50 else 'Fail')
        rows.sort(key=lambda r: (r['rank'] is None, r['rank'] or 0, r['name']))
        return {'section': sec.display_name, 'term': term.name, 'year': sec.year_id.name, 'count': len(rows),
                'subjects': [{'id': x.id, 'name': x.name} for x in subjects], 'rows': rows,
                'class_average': round(sum(r['average'] for r in ranked) / len(ranked), 2) if ranked else None,
                'pdf': '/report/pdf/sonador_school.report_card_document/%s?term=%d' % (','.join(str(r['id']) for r in rows), term.id)}

    # ------------------------------------------------------------------ fees
    @api.model
    def fees(self):
        self._need_office()
        items = self.env['school.fee.item'].sudo().search([])
        grades = self.env['school.grade'].sudo().search([])
        return {'items': [{'id': i.id, 'name': i.name, 'kind': i.kind, 'transport_only': i.transport_only,
                           'amounts': {str(g.id): i.amount_for(g) for g in grades}} for i in items],
                'grades': [{'id': g.id, 'name': g.name} for g in grades],
                'sibling': {'second': float(self._param('sibling_2', 10)), 'third': float(self._param('sibling_3', 15))},
                'banks': [{'id': a.id, 'name': a.display_name} for a in self.env['account.account'].sudo().search(
                    [('account_type', '=', 'asset_cash'), ('company_ids', 'in', self.env.company.id)]).sorted(
                    lambda a: 'cash' in (a.name or '').lower())],  # banks and telebirr first: most fees come in that way
                'recent': [{'id': b.id, 'name': b.name, 'student': b.student_id.name, 'period': b.period, 'amount': b.amount,
                            'residual': b.residual, 'date': _s(b.date)} for b in self.env['school.bill'].sudo().search([('state', '=', 'posted')], limit=30)]}

    def _param(self, key, default):
        return self.env['ir.config_parameter'].sudo().get_param('school.%s' % key, default)

    @api.model
    def bill_run(self, kinds, period, day=False, due=False, section_ids=None):
        """Bill every enrolled student the fees of these kinds (monthly / term / annual), with the sibling discount
        (2nd child, 3rd and later) and scholarships. One bill per student and period; running it again bills nobody twice."""
        self._need_office()
        day = day or _s(fields.Date.context_today(self))
        St = self.env['school.student'].sudo()
        dom = [('state', '=', 'enrolled'), ('payer_id', '!=', False)]
        if section_ids:
            dom.append(('section_id', 'in', [int(x) for x in section_ids]))
        students = St.search(dom, order='payer_id, birth_date, id')
        items = self.env['school.fee.item'].sudo().search([('kind', 'in', list(kinds))])
        B = self.env['school.bill'].sudo()
        d2, d3 = float(self._param('sibling_2', 10)), float(self._param('sibling_3', 15))
        order = defaultdict(int)
        made = B.browse()
        for s in students:
            order[s.payer_id.id] += 1
            if B.search_count([('student_id', '=', s.id), ('period', '=', period), ('state', '!=', 'cancel')]):
                continue
            pct = s.scholarship_pct or 0.0
            sib = d2 if order[s.payer_id.id] == 2 else d3 if order[s.payer_id.id] >= 3 else 0.0
            lines = []
            for it in items:
                if it.transport_only and not s.transport:
                    continue
                amt = it.amount_for(s.grade_id)
                if not amt:
                    continue
                disc, note = 0.0, []
                if it.discountable and (pct or sib):
                    rate = min(100.0, pct + sib)
                    disc = round(amt * rate / 100.0, 2)
                    note = ([('Scholarship %g%%' % pct)] if pct else []) + ([('Sibling %g%%' % sib)] if sib else [])
                lines.append(Command.create({'item_id': it.id, 'name': it.name, 'amount': amt, 'discount': disc, 'discount_note': ', '.join(note)}))
            if lines:
                made |= B.create({'student_id': s.id, 'payer_id': s.payer_id.id, 'period': period, 'date': day, 'due_date': due or day,
                                  'line_ids': lines})
        made.action_post()
        return {'bills': len(made), 'total': sum(made.mapped('amount')), 'fees': self.fees()}

    @api.model
    def arrears(self, search=''):
        self._need_office()
        bills = self.env['school.bill'].sudo().search([('state', '=', 'posted')])
        fam = defaultdict(lambda: {'owed': 0.0, 'oldest': None, 'bills': 0})
        for b in bills:
            r = b.residual
            if r > 0.01:
                f = fam[b.payer_id]
                f['owed'] += r
                f['bills'] += 1
                f['oldest'] = min(f['oldest'] or b.date, b.date)
        St = self.env['school.student'].sudo()
        today = fields.Date.context_today(self)
        rows = []
        for p, f in fam.items():
            if search and search.lower() not in (p.name or '').lower():
                continue
            kids = St.search([('payer_id', '=', p.id), ('state', '=', 'enrolled')])
            rows.append({'id': p.id, 'name': p.name, 'phone': p.phone or '', 'owed': round(f['owed'], 2), 'bills': f['bills'],
                         'days': (today - f['oldest']).days, 'children': ', '.join('%s (%s)' % (k.name, k.section_id.display_name or '') for k in kids)})
        return sorted(rows, key=lambda r: -r['owed'])

    @api.model
    def family(self, payer_id):
        p = self.env['res.partner'].sudo().browse(int(payer_id))
        bills = self.env['school.bill'].sudo().search([('payer_id', '=', p.id), ('state', '=', 'posted')], limit=60)
        V = self.env['sb.voucher'].sudo().search([('partner_id', '=', p.id), ('voucher_type', '=', 'receipt'), ('state', '=', 'posted')], limit=30)
        kids = self.env['school.student'].sudo().search([('payer_id', '=', p.id)])
        return {'id': p.id, 'name': p.name, 'phone': p.phone or '', 'code': 'F%05d' % p.id,
                'children': [{'id': k.id, 'name': k.name, 'class': k.section_id.display_name or '', 'no': k.student_no} for k in kids],
                'owed': round(sum(bills.mapped('residual')), 2),
                'bills': [{'id': b.id, 'name': b.name, 'student': b.student_id.name, 'period': b.period, 'date': _s(b.date),
                           'amount': b.amount, 'residual': round(b.residual, 2)} for b in bills],
                'payments': [{'id': v.id, 'name': v.name, 'date': _s(v.date), 'amount': v.amount_total if 'amount_total' in v._fields else 0.0,
                              'ref': v.reference or ''} for v in V]}

    @api.model
    def pay(self, payer_id, amount, account_id, reference='', day=False):
        """A payment from a family (bank slip, telebirr, cash): a receipt applied to its oldest open fee bills."""
        self._need_office()
        amount = round(float(amount or 0), 2)
        if amount <= 0:
            raise UserError(self.env._('Enter the amount paid.'))
        p = self.env['res.partner'].sudo().browse(int(payer_id))
        day = day or _s(fields.Date.context_today(self))
        open_lines = self.env['school.bill'].sudo().search([('payer_id', '=', p.id), ('state', '=', 'posted')], order='date, id') \
            .move_id.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable' and l.amount_residual > 0.005)
        left = amount
        applies = []
        for l in open_lines.sorted(lambda l: (l.date, l.id)):
            if left <= 0:
                break
            take = min(left, l.amount_residual)
            applies.append(Command.create({'line_id': l.id, 'pay': True, 'amount_applied': take}))
            left = round(left - take, 2)
        V = self.env['sb.voucher'].sudo().with_context(default_voucher_type='receipt')
        v = V.create({'voucher_type': 'receipt', 'partner_id': p.id, 'payer_type': 'customer', 'date': day,
                      'reference': (reference or '')[:60] or False, 'memo': self.env._('School fees'),
                      'received_amount': amount, 'apply_ids': applies,
                      'cash_line_ids': [Command.clear(), Command.create({'account_id': int(account_id), 'amount_currency': amount,
                                                                         'reference': (reference or '')[:60] or False})]})
        v.action_post()
        return {'receipt': v.name, 'unapplied': left, 'family': self.family(p.id)}


    # ------------------------------------------------------------------ setup
    @api.model
    def start_year(self, name, date_start, date_end, grade_ids, per_grade=1, mid=False):
        """Open an academic year: two semesters split at `mid`, and sections A, B, ... for each grade taught."""
        self._need_office()
        Y = self.env['school.year'].sudo()
        d0, d1 = fields.Date.to_date(date_start), fields.Date.to_date(date_end)
        mid = fields.Date.to_date(mid) if mid else d0 + (d1 - d0) / 2
        Y.search([('current', '=', True)]).current = False
        y = Y.create({'name': name, 'date_start': d0, 'date_end': d1, 'current': True, 'term_ids': [
            Command.create({'name': 'Semester 1', 'name_am': '1ኛ ወሰነ ትምህርት', 'sequence': 1, 'date_start': d0, 'date_end': mid}),
            Command.create({'name': 'Semester 2', 'name_am': '2ኛ ወሰነ ትምህርት', 'sequence': 2, 'date_start': mid + timedelta(days=1), 'date_end': d1})]})
        S = self.env['school.section'].sudo()
        for g in self.env['school.grade'].sudo().browse([int(x) for x in grade_ids]):
            for i in range(max(1, int(per_grade or 1))):
                S.create({'name': chr(65 + i), 'year_id': y.id, 'grade_id': g.id})
        return self.ctx()

    @api.model
    def classes(self):
        year = self.env['school.year'].sudo().get_current()
        secs = self._my_sections(year)
        return {'sections': [{'id': s.id, 'name': s.display_name, 'grade_id': s.grade_id.id, 'section': s.name, 'room': s.room or '',
                              'homeroom_id': s.homeroom_id.id, 'homeroom': s.homeroom_id.name or '', 'capacity': s.capacity,
                              'boys': len(s.student_ids.filtered(lambda x: x.state == 'enrolled' and x.sex == 'm')),
                              'girls': len(s.student_ids.filtered(lambda x: x.state == 'enrolled' and x.sex == 'f')),
                              'teachers': [{'subject': t.subject_id.name, 'teacher': t.teacher_id.name or ''} for t in s.teacher_ids]}
                             for s in secs.sorted(lambda s: (s.grade_id.sequence, s.name))],
                'staff': [{'id': e.id, 'name': e.name} for e in self.env['hr.employee'].sudo().search([], order='name')] if self._office() else []}

    @api.model
    def save_section(self, vals):
        self._need_office()
        S = self.env['school.section'].sudo()
        v = {k: vals[k] for k in ('name', 'room', 'capacity') if k in vals}
        if 'homeroom_id' in vals:
            v['homeroom_id'] = int(vals['homeroom_id']) if vals['homeroom_id'] else False
        if vals.get('id'):
            S.browse(int(vals['id'])).write(v)
        else:
            v.update({'year_id': self.env['school.year'].sudo().get_current().id, 'grade_id': int(vals['grade_id'])})
            S.create(v)
        return self.classes()

    @api.model
    def save_conduct(self, term_id, rows):
        """Conduct and the homeroom teacher's comment, per student, for the report card."""
        R = self.env['school.term.result'].sudo()
        for r in rows:
            st = self.env['school.student'].sudo().browse(int(r['id']))
            self._section(st.section_id.id)
            rec = R.search([('term_id', '=', int(term_id)), ('student_id', '=', st.id)], limit=1)
            vals = {'conduct': r.get('conduct') or False, 'comment': r.get('comment') or False}
            if rec:
                rec.write(vals)
            elif vals['conduct'] or vals['comment']:
                R.create(dict(vals, term_id=int(term_id), student_id=st.id))
        return True

    @api.model
    def save_fee(self, item_id, grade_id, amount):
        """The amount of a fee for one grade (or for all grades when grade_id is empty)."""
        self._need_office()
        it = self.env['school.fee.item'].sudo().browse(int(item_id))
        amount = float(amount or 0)
        if not grade_id:
            it.amount = amount
        else:
            p = it.price_ids.filtered(lambda x: x.grade_id.id == int(grade_id))
            if p:
                p.amount = amount
            else:
                it.price_ids = [Command.create({'grade_id': int(grade_id), 'amount': amount})]
        return self.fees()

    # ------------------------------------------------------------------ year end
    @api.model
    def promote(self, section_id, to_section_id):
        """Move a class's students who passed (yearly average 50 or more) to a section of the next grade."""
        self._need_office()
        sec = self.env['school.section'].sudo().browse(int(section_id))
        to = self.env['school.section'].sudo().browse(int(to_section_id))
        moved, kept = 0, 0
        averages = defaultdict(list)
        for t in sec.year_id.term_ids:
            for r in self.results(sec.id, t.id)['rows']:
                if r['average'] is not None:
                    averages[r['id']].append(r['average'])
        for s in sec.student_ids.filtered(lambda x: x.state == 'enrolled'):
            avs = averages.get(s.id)
            if avs and sum(avs) / len(avs) >= 50:
                s.section_id = to
                moved += 1
            else:
                kept += 1
        return {'promoted': moved, 'kept': kept}
