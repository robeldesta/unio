"""What Studio keeps: the apps people build, each customised model as one description (spec), and every earlier
version of it so any change can be undone.

A spec is plain JSON, the single source of truth that the editor changes, the builder turns into models, fields,
screens and menus, the history keeps, the export writes out as a module, and the assistant drafts from a sentence:

    {"model": "x_visitor", "label": "Visitor", "own": true, "chatter": true,
     "fields": [{"name": "x_name", "label": "Name", "type": "text", "required": true}, ...],
     "form": {"title": "x_name", "status": "x_stage",
              "sections": [{"label": "", "columns": [["x_company", "x_phone"], ["x_host"]]}],
              "tabs": [{"label": "Notes", "fields": ["x_notes"]}]},
     "list": ["x_name", "x_company"], "kanban": {"title": "x_name", "fields": ["x_company"]},
     "calendar": "x_date", "pivot": true}

For a model that already exists (own: false) the spec only adds: fields, and where each goes on its screens
("place": [{"field": "x_studio_region", "after": "phone"}], "list": ["x_studio_region"]).
"""
import json

from odoo import api, fields, models
from odoo.exceptions import UserError


class SbStudioApp(models.Model):
    _name = 'sb.studio.app'
    _description = 'App built in Studio'
    _order = 'sequence, id'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=50)
    description = fields.Text()
    icon = fields.Char(default='box', help='Symbol of the app icon.')
    color = fields.Char(default='#2D6BFF')
    menu_id = fields.Many2one('ir.ui.menu', readonly=True, ondelete='set null')
    model_ids = fields.One2many('sb.studio.model', 'app_id')
    active = fields.Boolean(default=True)


class SbStudioModel(models.Model):
    _name = 'sb.studio.model'
    _description = 'Model changed in Studio'
    _order = 'app_id, sequence, id'

    app_id = fields.Many2one('sb.studio.app', ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    model = fields.Char(required=True, index=True)
    label = fields.Char()
    own = fields.Boolean(help='Made in Studio (otherwise Studio only adds to a model of an app).')
    parent_id = fields.Many2one('sb.studio.model', ondelete='cascade', help='For the lines of a table field: the model they belong to.')
    spec = fields.Text(default='{}')
    menu_id = fields.Many2one('ir.ui.menu', readonly=True, ondelete='set null')
    action_id = fields.Many2one('ir.actions.act_window', readonly=True, ondelete='set null')
    view_ids = fields.Many2many('ir.ui.view', readonly=True)
    version_ids = fields.One2many('sb.studio.version', 'studio_model_id')

    _uniq = models.Constraint('unique(model)', 'This model is already in Studio.')

    def get_spec(self):
        self.ensure_one()
        return json.loads(self.spec or '{}')


class SbStudioVersion(models.Model):
    _name = 'sb.studio.version'
    _description = 'Earlier version of a Studio model'
    _order = 'id desc'

    studio_model_id = fields.Many2one('sb.studio.model', required=True, ondelete='cascade', index=True)
    summary = fields.Char()
    spec = fields.Text(help='The description as it was after this change.')
    user_id = fields.Many2one('res.users', default=lambda s: s.env.user)
    create_date = fields.Datetime(readonly=True)

    @api.model
    def _record(self, sm, summary):
        self.create({'studio_model_id': sm.id, 'summary': summary, 'spec': sm.spec})
        # keep the last 200 versions of each model
        old = self.search([('studio_model_id', '=', sm.id)], offset=200)
        old.unlink()


def require_studio(env):
    if not env.user.has_group('base.group_system'):
        raise UserError(env._('Studio is for administrators (Settings access).'))


class SbStudioRule(models.Model):
    _name = 'sb.studio.rule'
    _description = 'Studio automation (as described in Studio)'

    automation_id = fields.Many2one('base.automation', required=True, ondelete='cascade', index=True)
    spec = fields.Text()
