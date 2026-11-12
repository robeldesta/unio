from .models.rules import apply_rules


def post_init_hook(env):
    """Asset classes of each company's country, on their own accounts."""
    for company in env['res.company'].search([]):
        apply_rules(env, company)
