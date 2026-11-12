"""Server side of the planning gate: while a person is locked, data calls outside Planning and the open areas are refused."""
from odoo import http
from odoo.exceptions import UserError
from odoo.http import request

from odoo.addons.web.controllers.dataset import DataSet


class PlanningGateDataSet(DataSet):

    def _sb_plan_check(self, model):
        env = request.env
        if env.su or not env.user or env.user._is_superuser():
            return
        gate = env['sb.plan.gate'].sudo()
        if not gate.allowed(env.user, model):
            reasons = gate.state(env.user)['reasons']
            raise UserError(env._('Planning first:\n%s\n\nOpen Planning to finish it.', '\n'.join('• ' + r['msg'] for r in reasons)))

    @http.route()
    def call_kw(self, model, method, args, kwargs, path=None):
        self._sb_plan_check(model)
        return super().call_kw(model, method, args, kwargs, path=path)

    @http.route()
    def call_button(self, model, method, args, kwargs, path=None):
        self._sb_plan_check(model)
        return super().call_button(model, method, args, kwargs, path=path)
