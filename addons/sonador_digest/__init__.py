from . import models


def post_init_hook(env):
    env['digest.digest'].search([])._sb_unio_defaults()
