"""Review cycles (bi-annual, annual, probation ...): a form of weighted criteria scored 1-5 by the employee (self),
the manager and peers. The manager's review is the rating of record; it also sets potential for the 9-box."""
from odoo import api, fields, models

RATINGS = [(90, 'Outstanding'), (75, 'Exceeds expectations'), (60, 'Meets expectations'), (45, 'Needs improvement'),
           (0, 'Unsatisfactory')]


def rating_label(pct):
    if pct is None:
        return ''
    for floor, label in RATINGS:
        if pct >= floor:
            return label
    return RATINGS[-1][1]


class SbPerfTemplate(models.Model):
    _name = 'sb.perf.template'
    _description = 'Review form'

    name = fields.Char(required=True)
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company)
    criterion_ids = fields.One2many('sb.perf.criterion', 'template_id', 'Criteria')
    active = fields.Boolean(default=True)


class SbPerfCriterion(models.Model):
    _name = 'sb.perf.criterion'
    _description = 'Review criterion'
    _order = 'sequence, id'

    template_id = fields.Many2one('sb.perf.template', required=True, ondelete='cascade')
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True)
    description = fields.Char()
    weight = fields.Float(default=1.0, help='Relative weight within the form.')
    applies = fields.Selection([('all', 'Everyone'), ('staff', 'Staff only'), ('manager', 'Managers only')], default='all',
                               required=True, help='Managers: people with a team.')


class SbPerfCycle(models.Model):
    _name = 'sb.perf.cycle'
    _description = 'Review cycle'
    _order = 'date_to desc, id desc'

    name = fields.Char(required=True)
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company, required=True)
    kind = fields.Selection([('bi_annual', 'Bi-annual'), ('annual', 'Annual'), ('quarterly', 'Quarterly'),
                             ('monthly', 'Monthly'), ('probation', 'End of probation')], default='bi_annual', required=True)
    date_from = fields.Date('Period from', required=True)
    date_to = fields.Date('Period to', required=True)
    deadline = fields.Date('Reviews due')
    template_id = fields.Many2one('sb.perf.template', 'Review form', required=True)
    self_review = fields.Boolean('Self review', default=True)
    peer_review = fields.Boolean('Peer review (360)', default=False)
    state = fields.Selection([('draft', 'Draft'), ('open', 'In progress'), ('calibration', 'Calibration'), ('closed', 'Closed')],
                             default='draft', required=True)
    review_ids = fields.One2many('sb.perf.review', 'cycle_id', 'Reviews')
    note = fields.Char()


class SbPerfReview(models.Model):
    _name = 'sb.perf.review'
    _description = 'Performance review'
    _order = 'cycle_id desc, employee_id, role'

    cycle_id = fields.Many2one('sb.perf.cycle', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='cycle_id.company_id', store=True)
    employee_id = fields.Many2one('hr.employee', required=True, index=True, ondelete='cascade')
    reviewer_id = fields.Many2one('hr.employee', 'Reviewer')
    role = fields.Selection([('self', 'Self'), ('manager', 'Manager'), ('peer', 'Peer')], required=True, default='manager')
    state = fields.Selection([('draft', 'To do'), ('submitted', 'Submitted'), ('acknowledged', 'Acknowledged')], default='draft')
    line_ids = fields.One2many('sb.perf.review.line', 'review_id', 'Scores')
    score_pct = fields.Float('Score %', compute='_compute_score', store=True)
    rating = fields.Char(compute='_compute_score', store=True)
    potential = fields.Selection([('1', 'Low'), ('2', 'Medium'), ('3', 'High')], help='Manager review: potential, for the 9-box.')
    strengths = fields.Text()
    improvements = fields.Text('To improve')
    comment = fields.Text('Overall comment')
    employee_comment = fields.Text('Employee comment')
    pay_raise_pct = fields.Float('Proposed raise %', help='Pay review: proposed increase of basic salary.')
    submitted_on = fields.Date()
    acknowledged_on = fields.Date()
    source = fields.Char(help='Where the review came from (e.g. Odoo 18).')

    @api.depends('line_ids.score', 'line_ids.weight')
    def _compute_score(self):
        for r in self:
            lines = r.line_ids.filtered(lambda l: l.score)
            w = sum(lines.mapped('weight'))
            r.score_pct = round(sum(l.score * l.weight for l in lines) / (5.0 * w) * 100.0, 1) if w else 0.0
            r.rating = rating_label(r.score_pct) if w else ''


class SbPerfReviewLine(models.Model):
    _name = 'sb.perf.review.line'
    _description = 'Review score'
    _order = 'sequence, id'

    review_id = fields.Many2one('sb.perf.review', required=True, ondelete='cascade', index=True)
    criterion_id = fields.Many2one('sb.perf.criterion', ondelete='set null')
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True)
    weight = fields.Float(default=1.0)
    score = fields.Integer('Score (1-5)')
    comment = fields.Char()
