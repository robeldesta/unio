import json

from odoo.tools import file_path


AREAS = {'UG': 'sonador_crm/data/districts_ug.json'}


def ensure_areas(env, code):
    """Districts of a country other than Ethiopia, loaded the first time a company of that country uses the CRM."""
    W = env['sb.woreda'].sudo()
    if code not in AREAS or W.search_count([('country_code', '=', code)]):
        return
    rows = json.load(open(file_path(AREAS[code]), encoding='utf-8'))
    W.create([{'name': r['name'], 'zone': r['zone'], 'region': r['region'], 'code': r['code'], 'country_code': code} for r in rows])


def post_init_hook(env):
    """Ethiopia's woredas (CSA 2013 / FEWS 2021 admin-3, with centroids) for addresses and territory reports."""
    W = env['sb.woreda'].sudo()
    if W.search_count([]):
        return
    rows = json.load(open(file_path('sonador_crm/data/woredas.json'), encoding='utf-8'))
    W.create([{'name': r['name'], 'zone': r['zone'] or False, 'region': r['region'] or False, 'latitude': r['lat'] or 0.0,
               'longitude': r['lon'] or 0.0, 'code': r['code'] or False, 'active': r['active']} for r in rows])
