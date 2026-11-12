"""The plan checker: catches what makes a plan worthless before a manager has to.

Each finding has a level and a reason. Only nonsense blocks: an empty plan, a line with neither a target nor a "done when",
a target of 0, placeholder or random text, the very same line twice, missing dates, and a plan without any of the role's
must-do items. Everything else (vague words, look-alike lines, a copy of last month) is a warning the manager sees and
decides on. Strictness (0-100, per person or company) only moves the warning thresholds. Works on English and Amharic (Ge'ez script)."""
import difflib
import re
from datetime import timedelta

from odoo import api, fields, models

from odoo.addons.sonador_books.models.ethiopian_calendar import format_ethiopian

PLACEHOLDER = re.compile(
    r'^(?:-+|\.+|_+|\?+|x+|n/?a|na|nil|none|tbd|tba|todo|to do|test|testing|asdf\w*|qwe\w*|zxc\w*|aaa+|abc|'
    r'ok|okay|done|yes|no|same|same as above|as above|ditto|"|etc|document|documents|report|reports|task|tasks|work|'
    r'activity|activities|plan|planning|measure\s*\d*|target\s*\d*|output\s*\d*|\d+|[a-z]|[a-z]\d*)$', re.I)
VAGUE = [
    'follow up', 'follow-up', 'followup', 'monitor', 'check in', 'ongoing', 'on going', 'as needed', 'as required',
    'when necessary', 'various', 'general', 'regular update', 'routine', 'continue', 'support', 'assist', 'help',
    'coordinate', 'stay focused', 'be on time', 'do my best', 'try to', 'etc', 'tbd',
    'ክትትል', 'እንደ አስፈላጊነቱ', 'ቀጣይነት', 'መደበኛ', 'ማገዝ', 'ማስተባበር',
]
ETHIOPIC = re.compile(r'[ሀ-፿]')
KEYBOARD_RUNS = ('qwert', 'asdfg', 'zxcvb', 'yuiop', 'hjkl', 'wasd', '12345', 'abcde')
MEASURE = re.compile(r'\d|%|\b(by|before|until|deadline|all|every|each|zero|no more than|at least|complete|completed|'
                     r'approved|signed|submitted|posted|delivered|installed|closed|paid|collected|reconciled|filed|'
                     r'published|trained|hired|launched)\b|ቀን|በመቶ|ሙሉ', re.I)


def norm(text):
    t = (text or '').lower()
    t = re.sub(r'\d+(\.\d+)?\s*%?', ' # ', t)
    t = re.sub(r'[^\wሀ-፿#]+', ' ', t)
    return re.sub(r'\s+', ' ', t).strip()


def similar(a, b):
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def gibberish(text):
    """Keyboard mash or random letters (Latin script). Amharic text is checked only for repeated characters."""
    t = (text or '').strip()
    if not t:
        return False
    if re.search(r'(.)\1{3,}', t):
        return True
    low = t.lower()
    if any(k in low for k in KEYBOARD_RUNS):
        return True
    if ETHIOPIC.search(t):
        return False
    words = re.findall(r'[a-z]{4,}', low)
    if not words:
        return False
    bad = [w for w in words if not re.search(r'[aeiouy]', w) or re.search(r'[bcdfghjklmnpqrstvwxz]{5,}', w)]
    return len(bad) >= max(1, len(words) // 2)


class SbPlanCoach(models.AbstractModel):
    _name = 'sb.plan.coach'
    _description = 'Plan checker'

    def _profile(self, strictness):
        s = max(0, min(int(strictness or 60), 100)) / 100.0

        def lerp(a, b):
            return a + (b - a) * s
        return {'strictness': round(s * 100), 'min_what': int(lerp(6, 18)), 'min_done': int(lerp(4, 14)),
                'dup_block': lerp(0.97, 0.88), 'dup_warn': lerp(0.92, 0.78), 'same_cols': lerp(0.95, 0.85),
                'vague_blocks': s >= 0.7, 'measure_blocks': s >= 0.8, 'short_blocks': s >= 0.5,
                'copy_month': lerp(0.85, 0.6), 'max_task_days': int(lerp(31, 12))}

    def strictness_of(self, employee):
        return employee.sb_pl_strictness or employee.company_id.sb_pl_strictness or 60

    @api.model
    def check(self, employee, month, tasks, plan_id=False):
        """tasks: [{key, name, done_when, output, target_qty, date_from, date_to, category_id, goal_id}] (dates ISO).
        Returns {'tasks': {key: [finding]}, 'plan': [finding], 'score': 0-100, 'blocking': bool}."""
        P = self._profile(self.strictness_of(employee))
        company = employee.company_id
        from .service import month_bounds
        m0, m1, mlabel = month_bounds(month)
        out = {t['key']: [] for t in tasks}
        plan_f = []

        def add(key, level, code, msg):
            (out[key] if key is not None else plan_f).append({'level': level, 'code': code, 'msg': msg})

        hol = self.env['sb.leave.service'].sudo()._holiday_dates(m0, m1, company)
        leave_days = set()
        for lv in self.env['sb.leave'].sudo().search([('employee_id', '=', employee.id), ('state', '=', 'approved'),
                                                      ('date_from', '<=', m1), ('date_to', '>=', m0)]):
            d = max(lv.date_from, m0)
            while d <= min(lv.date_to, m1):
                leave_days.add(d)
                d += timedelta(days=1)
        cal = employee.resource_calendar_id or company.resource_calendar_id
        workdays = {int(a.dayofweek) for a in cal.attendance_ids} if cal else {0, 1, 2, 3, 4, 5}
        working = [m0 + timedelta(days=i) for i in range((m1 - m0).days + 1)]
        working = [d for d in working if d.weekday() in workdays and d not in hol and d not in leave_days]

        def has_target(t):
            v = t.get('target_qty')
            return v not in (None, '', False)

        for t in tasks:
            k = t['key']
            what, done, output = (t.get('name') or '').strip(), (t.get('done_when') or '').strip(), (t.get('output') or '').strip()
            if not what:
                add(k, 'block', 'empty', self.env._('Say what the task is.'))
                continue
            for label, val in ((self.env._('What'), what), (self.env._('Done when'), done), (self.env._('Evidence'), output)):
                if val and PLACEHOLDER.match(norm(val).replace('#', '1').strip() or val):
                    evidence = val is output
                    add(k, 'warn' if evidence else 'block', 'placeholder',
                        self.env._('Evidence "%s" is vague: which report, form or record?', val[:40]) if evidence else
                        self.env._('%(f)s "%(v)s" is a placeholder, not a plan: nobody can tell what you will deliver.', f=label, v=val[:40]))
                elif val and gibberish(val):
                    add(k, 'block', 'gibberish', self.env._('%(f)s looks like random typing.', f=label))
            short_min = P['min_what'] // 2 if ETHIOPIC.search(what) else P['min_what']
            if len(what) < short_min:
                add(k, 'warn', 'short', self.env._('"What" is very short: will your manager know what you mean?'))
            target = float(t.get('target_qty') or 0) if has_target(t) else None
            if target is not None and target <= 0:
                add(k, 'block', 'zero_target', self.env._('A target of %s is not a target: put the number you aim for, or clear it '
                                                          'and say when it is done.', ('%g' % target)))
            elif target is None and not done:
                add(k, 'block', 'no_measure', self.env._('Give a target number or say when it is done: without one nobody can tell '
                                                         'whether it happened.'))
            elif done and target is None and not MEASURE.search(done):
                add(k, 'warn', 'not_measurable',
                    self.env._('"Done when" has no number, date or finished result: how will your manager check it?'))
            if (done and similar(what, done) >= P['same_cols']) or (output and len(output) > 12 and similar(done or what, output) >= P['same_cols']):
                add(k, 'warn', 'same_columns', self.env._('The same text is in several boxes: say the result in "Done when".'))
            low = ' '.join((what, done)).lower()
            for v in VAGUE:
                if v in low:
                    add(k, 'warn', 'vague', self.env._('"%s" is vague: name the result instead.', v))
                    break
            # dates
            df, dt = fields.Date.to_date(t.get('date_from')), fields.Date.to_date(t.get('date_to'))
            if not df or not dt:
                add(k, 'block', 'no_dates', self.env._('Pick the week it is due.'))
            else:
                if dt < df:
                    add(k, 'block', 'dates_order', self.env._('Ends before it starts.'))
                if df < m0 or dt > m1:
                    add(k, 'block', 'outside', self.env._('Outside %s: plan it in its own month.', mlabel))
                span_work = [d for d in working if df <= d <= dt]
                if not span_work and df >= m0 and dt <= m1:
                    add(k, 'warn', 'no_workday', self.env._('Falls only on holidays, leave or days off.'))
                if (dt - df).days + 1 > P['max_task_days'] and (dt - df).days + 1 >= 20:
                    add(k, 'warn', 'whole_month', self.env._('Spans almost the whole month: split it into steps with their own results.'))
            if company.sb_pl_require_goal and not t.get('goal_id'):
                add(k, 'block', 'no_goal', self.env._('Link the task to one of your goals (company rule).'))

        # duplicates inside the plan: only the very same line twice blocks; look-alikes are a hint
        def exact(t):
            txt = lambda x: re.sub(r'\s+', ' ', re.sub(r'[^\w\s%.,]+', ' ', (x or '').lower())).strip()
            return (txt(t.get('name')), txt(t.get('done_when')), str(t.get('target_qty') or ''), str(t.get('date_from') or ''),
                    str(t.get('date_to') or ''))
        for i, a in enumerate(tasks):
            if not (a.get('name') or '').strip():
                continue
            for b in tasks[i + 1:]:
                if not (b.get('name') or '').strip():
                    continue
                if exact(a) == exact(b):
                    add(b['key'], 'block', 'duplicate', self.env._('The same line is already in the plan (row "%s"): remove one.',
                                                                 (a.get('name') or '')[:50]))
                    continue
                r = max(similar(a.get('name'), b.get('name')), similar((a.get('name') or '') + ' ' + (a.get('done_when') or ''),
                                                                      (b.get('name') or '') + ' ' + (b.get('done_when') or '')))
                if r >= P['dup_warn']:
                    add(b['key'], 'warn', 'similar', self.env._('Looks like "%s": fine if it is a separate result, '
                                                                'otherwise merge them.', (a.get('name') or '')[:50]))

        # plan level
        n = len([t for t in tasks if (t.get('name') or '').strip()])
        if not n:
            add(None, 'block', 'empty_plan', self.env._('The plan is empty: add the tasks of the month.'))
        elif n < company.sb_pl_min_tasks:
            add(None, 'warn', 'few', self.env._('Only %(n)s task(s): usually at least %(m)s in a month.', n=n, m=company.sb_pl_min_tasks))
        if n > company.sb_pl_max_tasks:
            add(None, 'warn', 'many', self.env._('%(n)s tasks: more than the %(m)s usual. Can it really all be done?', n=n, m=company.sb_pl_max_tasks))
        # the role's must-do items
        role = employee.sudo().sb_pl_role_id
        items = role.item_ids.filtered('mandatory') if role else self.env['sb.plan.role.item']
        if items and n:
            def covers(item):
                return any(str(t.get('role_item_id') or '') == str(item.id) or similar(t.get('name'), item.name) >= 0.8 for t in tasks)
            missing = items.filtered(lambda i: not covers(i))
            if len(missing) == len(items):
                add(None, 'block', 'role_items', self.env._('None of the must-do items of the %(r)s role is in the plan (%(l)s). '
                                                            'Use "Add my role\'s items".', r=role.name,
                                                            l=', '.join(missing.mapped('name'))[:200]))
            elif missing:
                add(None, 'warn', 'role_items_some', self.env._('Must-do items of the %(r)s role not in the plan: %(l)s.', r=role.name,
                                                                l=', '.join(missing.mapped('name'))[:200]))
        ends = [fields.Date.to_date(t.get('date_to')) for t in tasks if t.get('date_to')]
        if len(ends) >= 4 and sum(1 for e in ends if e == m1) >= len(ends) * 0.7:
            add(None, 'warn', 'all_last_day', self.env._('Nearly everything is due on the last day: spread the work over the month.'))
        if working:
            load = sum(len([d for d in working if fields.Date.to_date(t['date_from']) <= d <= fields.Date.to_date(t['date_to'])])
                       for t in tasks if t.get('date_from') and t.get('date_to'))
            if load > len(working) * 4:
                add(None, 'warn', 'overload', self.env._('About %(x)s tasks running each working day: is that realistic?',
                                                         x=round(load / len(working), 1)))
            weeks_empty = []
            from .service import week_start
            ws = week_start(m0, company)
            while ws <= m1:
                we = ws + timedelta(days=6)
                wdays = [d for d in working if ws <= d <= we]
                if wdays and not any(fields.Date.to_date(t['date_from']) <= we and fields.Date.to_date(t['date_to']) >= ws
                                     for t in tasks if t.get('date_from') and t.get('date_to')):
                    weeks_empty.append(max(ws, m0))
                ws = we + timedelta(days=1)
            if weeks_empty:
                add(None, 'warn', 'empty_week', self.env._('No task in the week of %s.', ', '.join(format_ethiopian(w) for w in weeks_empty)))
        # copied from last month (a warning: the manager decides) or from a colleague
        Task = self.env['sb.plan.task'].sudo()
        prev = Task.search([('employee_id', '=', employee.id), ('plan_id.month', '<', m0)], order='date_from desc', limit=60)
        if prev and n >= 1:
            last_month = prev[0].plan_id
            prev_tasks = last_month.task_ids

            def key(name, dn, qty):
                return (norm(name), norm(dn), float(qty or 0))
            old = {key(p.name, p.done_when, p.target_qty) for p in prev_tasks}
            new = {key(t.get('name'), t.get('done_when'), t.get('target_qty')) for t in tasks if (t.get('name') or '').strip()}
            copied = sum(1 for t in tasks if any(similar((t.get('name') or '') + (t.get('done_when') or ''), p.name + (p.done_when or '')) >= 0.9
                                                 for p in prev_tasks))
            if new and new == old:
                add(None, 'warn', 'copied_all', self.env._('Same as the %s plan with nothing changed. Recurring work is fine; your '
                                                           'manager decides.', last_month.label))
            elif n >= 3 and copied / max(n, 1) >= P['copy_month']:
                add(None, 'warn', 'copied', self.env._('%(p)s%% of the tasks are the same as the %(m)s plan. Recurring work is fine; '
                                                       'say what is new this month.', p=round(100 * copied / n), m=last_month.label))
        others = Task.search([('plan_id.month', '=', m0), ('employee_id', '!=', employee.id), ('company_id', '=', company.id)])
        for t in tasks:
            for o in others:
                if similar(t.get('name'), o.name) >= 0.95 and similar(t.get('done_when'), o.done_when) >= 0.9:
                    add(t['key'], 'warn', 'colleague', self.env._('Same task as in %s\'s plan: shared work? Say your part.', o.employee_id.name))
                    break

        blocks = sum(1 for f in plan_f if f['level'] == 'block') + sum(1 for fs in out.values() for f in fs if f['level'] == 'block')
        warns = sum(1 for f in plan_f if f['level'] == 'warn') + sum(1 for fs in out.values() for f in fs if f['level'] == 'warn')
        score = max(0, 100 - blocks * 12 - warns * 3)
        return {'tasks': out, 'plan': plan_f, 'score': score, 'blocking': blocks > 0, 'strictness': P['strictness']}

    @api.model
    def trust_flags(self, employee, ws, we):
        """Honesty signals in a week's check-ins (for the manager and the weekly overview, never a lock)."""
        C = self.env['sb.plan.checkin'].sudo()
        cks = C.search([('employee_id', '=', employee.id), ('date', '>=', ws), ('date', '<=', we)], order='create_date')
        flags = []
        jump = cks.filtered(lambda c: c.status == 'done' and not (c.note or '').strip()
                            and not C.search_count([('task_id', '=', c.task_id.id), ('date', '<', c.date)]))
        if jump:
            flags.append(self.env._('%s task(s) marked done in one go, with no note', len(jump)))
        notes = [norm(c.note) for c in cks if (c.note or '').strip()]
        rep = [n for n in set(notes) if notes.count(n) >= 3]
        if rep:
            flags.append(self.env._('The same note used %s times or more', 3))
        late = cks.filtered('late')
        if len(late) >= 3:
            flags.append(self.env._('%s check-ins back-filled after the day', len(late)))
        if len(cks) >= 5:
            first, last = min(cks.mapped('create_date')), max(cks.mapped('create_date'))
            if (last - first).total_seconds() < 3600 and len(set(cks.mapped('date'))) >= 3:
                flags.append(self.env._('A whole week of check-ins entered within one hour'))
        done_no_output = cks.filtered(lambda c: c.status == 'done' and not (c.task_id.output or '').strip())
        if done_no_output:
            flags.append(self.env._('%s done without any evidence named', len(done_no_output)))
        return flags
