# Studio end to end, in an odoo shell on a scratch database:
#   docker compose exec -T odoo odoo shell -d <db> --no-http ... < tests_manual/studio.py
import io
import json
import zipfile

from lxml import etree

A = env['sb.studio.api']
ok = []


def check(cond, what):
    print(('ok    ' if cond else 'FAIL  ') + what)
    ok.append(bool(cond))


# 1. every template becomes a working app
for key in A.home()['templates']:
    k = key['key']
    res = A.app_create(key['name'], key['icon'], key['color'], k)
    app = env['sb.studio.app'].browse(res['app_id'])
    for sm in app.model_ids.filtered(lambda m: not m.parent_id):
        M = env[sm.model]
        arch = M.get_view(sm.view_ids.filtered(lambda v: v.type == 'form')[:1].id, 'form')['arch']
        check(sm.action_id and app.menu_id and 'sheet' in arch, '%s: %s has its screens and menu' % (k, sm.label))
    env.cr.commit()

# 2. records, formulas and automations work in the maintenance app
app = env['sb.studio.app'].search([('name', '=', 'Maintenance')], limit=1)
eq_model = app.model_ids.filtered(lambda m: m.label == 'Equipment').model
job_model = app.model_ids.filtered(lambda m: m.label == 'Maintenance job').model
eq = env[eq_model].create({'x_name': 'Rewinder 2', 'x_responsible': env.user.id})
job = env[job_model].create({'x_name': 'Bearing noise', 'x_equipment': eq.id, 'x_technician': env.user.id, 'x_priority': True, 'x_labour': 300,
                             'x_parts': [(0, 0, {'x_name': 'Bearing 6204', 'x_qty': 2, 'x_price': 450}), (0, 0, {'x_name': 'Grease', 'x_qty': 1, 'x_price': 120})]})
check(abs(job.x_total - (2 * 450 + 120 + 300)) < 0.01, 'formula total = %s (parts 1020 + labour 300)' % job.x_total)
check(env['mail.activity'].search_count([('res_model', '=', job_model), ('res_id', '=', job.id)]) == 1, 'urgent breakdown made an activity for the technician')
job.x_stage = 'done'
check(bool(job.x_done), 'stage Done filled the finish date')
check(job.x_currency_id == env.company.currency_id, 'amounts in the company currency')

# 3. editing: add, rename, move, remove, then go back
spec = A.load(job_model)['spec']
spec['fields'].append({'name': 'x_cause', 'label': 'Root cause', 'type': 'choice', 'options': [{'value': 'wear', 'label': 'Wear'}, {'value': 'misuse', 'label': 'Misuse'}]})
spec['form']['sections'][0]['columns'][0].append('x_cause')
spec['list'].append('x_cause')
spec['fields'] = [f for f in spec['fields'] if f['name'] != 'x_kind']
for s in spec['form']['sections']:
    s['columns'] = [[n for n in c if n != 'x_kind'] for c in s['columns']]
spec['list'] = [n for n in spec['list'] if n != 'x_kind']
m = A.save(job_model, spec, 'added Root cause, removed Kind')
check('x_cause' in env[job_model]._fields and 'x_kind' not in env[job_model]._fields, 'field added and removed')
before = m['versions'][1]
m = A.revert(before['id'])
check('x_kind' in env[job_model]._fields and 'x_cause' not in env[job_model]._fields, 'went back: Kind is back, Root cause gone')
check(m['versions'][0]['summary'].startswith('Back to'), 'the return is itself a version (%s)' % m['versions'][0]['summary'])

# 4. an app's own screen: a field after the phone, one hidden, one renamed
m = A.load('res.partner')
spec = m['spec']
spec['fields'] = [{'name': 'x_studio_region', 'label': 'Sales region', 'type': 'choice',
                   'options': [{'value': 'addis', 'label': 'Addis Ababa'}, {'value': 'north', 'label': 'North'}]},
                  {'name': 'x_studio_since', 'label': 'Customer since', 'type': 'date'}]
spec['place'] = [{'field': 'x_studio_region', 'after': 'phone'}]
spec['list'] = ['x_studio_region']
spec['hide'] = ['website']
spec['relabel'] = {'email': 'Email address'}
A.save('res.partner', spec, 'region and customer since')
arch = env['res.partner'].get_view(env.ref('base.view_partner_form').id, 'form')['arch']
doc = etree.fromstring(arch)
phone = doc.xpath("//field[@name='phone']")[0]
check(phone.getnext() is not None and phone.getnext().get('name') == 'x_studio_region', 'Sales region right after the phone')
check(doc.xpath("//field[@name='website']")[0].get('invisible') in ('1', 'True'), 'website hidden')
check(doc.xpath("//field[@name='email']")[0].get('string') == 'Email address', 'email renamed')
check(bool(doc.xpath("//field[@name='x_studio_since']")), 'unplaced field shows under More details')
check(len(A.load('res.partner')['outline']) > 0, 'outline of the partner form')

# 5. an automation made in Studio
vis = env['sb.studio.app'].search([('name', '=', 'Visitors')], limit=1).model_ids[:1].model
autos = A.save_automation(vis, {'name': 'Note on arrival', 'trigger': 'stage', 'field': 'x_stage', 'value': 'on_site',
                                'actions': [{'do': 'note', 'body': '{x_name} from {x_company} has arrived'}]})
check(any(a['name'] == 'Note on arrival' and a['studio'] for a in autos), 'automation listed with its description')
v = env[vis].create({'x_name': 'Hana Bekele', 'x_company': 'Selam Trading', 'x_host': env.user.id})
v.x_stage = 'on_site'
msgs = v.message_ids.mapped('body')
check(any('Hana Bekele from Selam Trading has arrived' in str(b) for b in msgs), 'note posted with the values')
check(bool(v.x_arrival), 'arrival time set by the template automation')
check(env['mail.activity'].search_count([('res_model', '=', vis), ('res_id', '=', v.id)]) == 1, 'host reminded')

# 6. drafts from plain words
d = A.draft('Track our delivery trucks fuel fill-ups with the truck, driver, date, litres, price per litre, amount and the station, and notes')
names = [f['label'] for f in d['models'][0]['fields']]
check(len(names) >= 6, 'draft (no assistant) has fields: %s' % ', '.join(names))
res = A.app_create(d['name'], d['icon'], d['color'], None, None, d['description'], d['models'])
check(bool(env['sb.studio.model'].search([('model', '=', res['model'])])), 'draft created as an app')
d2 = A.draft('We need an app for visitors at the gate')
check(d2.get('template') == 'visitors', 'a description matching a template uses it')

# 7. export as a module
name, data = env['sb.studio.export'].export_app(app.id)
z = zipfile.ZipFile(io.BytesIO(data))
files = z.namelist()
check(any(f.endswith('__manifest__.py') for f in files) and any(f.endswith('data/app.xml') for f in files), 'export %s: %s' % (name, ', '.join(files)))
xml = z.read([f for f in files if f.endswith('data/app.xml')][0])
etree.fromstring(xml)
check(xml.count(b'model="ir.model.fields"') >= 20, 'export has the fields (%d)' % xml.count(b'model="ir.model.fields"'))

env.cr.commit()
print('\n%d of %d passed' % (sum(ok), len(ok)))
