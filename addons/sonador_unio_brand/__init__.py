from . import controllers
from . import models


def post_init_hook(env):
    env['unio.brand'].apply()
