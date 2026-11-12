"""Ready-made apps to start from (same format as anything built in Studio; model names are placeholders)."""


def F(name, label, type, **kw):
    return dict(kw, name=name, label=label, type=type)


def opts(*labels):
    import re
    return [{'value': re.sub(r'[^a-z0-9]+', '_', l.lower()).strip('_'), 'label': l} for l in labels]


TEMPLATES = {
    'visitors': {
        'name': 'Visitors', 'icon': 'people', 'color': '#1FD1B5',
        'description': 'Sign visitors in and out, tell their host, keep the log for security.',
        'models': [{
            'model': 'x_tpl_visit', 'label': 'Visit', 'plural': 'Visits', 'own': True, 'chatter': True, 'default_view': 'kanban',
            'fields': [F('x_name', 'Visitor name', 'text', required=True), F('x_company', 'Company', 'text'), F('x_phone', 'Phone', 'text'),
                       F('x_host', 'Host', 'link', relation='res.users'), F('x_purpose', 'Purpose', 'choice', options=opts('Meeting', 'Delivery', 'Interview', 'Contractor', 'Other')),
                       F('x_arrival', 'Arrived at', 'datetime'), F('x_departure', 'Left at', 'datetime'), F('x_badge', 'Badge no.', 'text'),
                       F('x_vehicle', 'Vehicle plate', 'text'), F('x_photo', 'Photo', 'image'),
                       F('x_stage', 'Status', 'stage', options=opts('Expected', 'On site', 'Left')), F('x_notes', 'Notes', 'longtext')],
            'form': {'title': 'x_name', 'status': 'x_stage', 'image': 'x_photo',
                     'sections': [{'label': '', 'columns': [['x_company', 'x_phone', 'x_vehicle'], ['x_host', 'x_purpose', 'x_badge']]},
                                  {'label': 'Times', 'columns': [['x_arrival'], ['x_departure']]}],
                     'tabs': [{'label': 'Notes', 'fields': ['x_notes']}]},
            'list': ['x_name', 'x_company', 'x_host', 'x_purpose', 'x_arrival', 'x_departure', 'x_stage'],
            'kanban': {'title': 'x_name', 'fields': ['x_company', 'x_arrival', 'x_host']}, 'calendar': 'x_arrival',
            'automations': [
                {'name': 'Tell the host', 'trigger': 'stage', 'field': 'x_stage', 'value': 'on_site',
                 'actions': [{'do': 'set', 'field': 'x_arrival', 'value': 'today'},
                             {'do': 'activity', 'user_field': 'x_host', 'summary': 'Your visitor has arrived', 'days': 0}]},
                {'name': 'Time of leaving', 'trigger': 'stage', 'field': 'x_stage', 'value': 'left',
                 'actions': [{'do': 'set', 'field': 'x_departure', 'value': 'today'}]},
            ]}]},
    'maintenance': {
        'name': 'Maintenance', 'icon': 'tool', 'color': '#F8642A',
        'description': 'Machines and equipment, breakdowns and servicing, parts used and the cost of each job.',
        'models': [
            {'model': 'x_tpl_equipment', 'label': 'Equipment', 'plural': 'Equipment', 'own': True, 'chatter': True, 'default_view': 'kanban',
             'fields': [F('x_name', 'Name', 'text', required=True), F('x_code', 'Asset code', 'text'), F('x_location', 'Location', 'text'),
                        F('x_serial', 'Serial number', 'text'), F('x_bought', 'Bought on', 'date'), F('x_next_service', 'Next service', 'date'),
                        F('x_responsible', 'Responsible', 'link', relation='res.users'), F('x_photo', 'Photo', 'image'),
                        F('x_condition', 'Condition', 'stage', options=opts('Working', 'Needs attention', 'Out of order'))],
             'form': {'title': 'x_name', 'status': 'x_condition', 'image': 'x_photo',
                      'sections': [{'label': '', 'columns': [['x_code', 'x_serial', 'x_location'], ['x_responsible', 'x_bought', 'x_next_service']]}]},
             'list': ['x_name', 'x_code', 'x_location', 'x_responsible', 'x_next_service', 'x_condition'],
             'kanban': {'title': 'x_name', 'fields': ['x_location', 'x_next_service', 'x_photo']},
             'automations': [{'name': 'Service is due', 'trigger': 'date', 'field': 'x_next_service', 'days': 3, 'when': 'before',
                              'actions': [{'do': 'activity', 'user_field': 'x_responsible', 'summary': 'Service due in 3 days', 'days': 3}]}]},
            {'model': 'x_tpl_job', 'label': 'Maintenance job', 'plural': 'Jobs', 'own': True, 'chatter': True, 'default_view': 'kanban',
             'fields': [F('x_name', 'Problem', 'text', required=True), F('x_equipment', 'Equipment', 'link', relation='x_tpl_equipment'),
                        F('x_kind', 'Kind', 'choice', options=opts('Breakdown', 'Planned service', 'Inspection')),
                        F('x_priority', 'Urgent', 'checkbox'), F('x_technician', 'Technician', 'link', relation='res.users'),
                        F('x_reported', 'Reported on', 'date'), F('x_done', 'Finished on', 'date'),
                        F('x_parts', 'Parts used', 'lines', lines={'fields': [F('x_name', 'Part', 'text', required=True), F('x_qty', 'Quantity', 'decimal'),
                                                                              F('x_price', 'Unit price', 'money'),
                                                                              F('x_amount', 'Amount', 'formula', formula='x_qty * x_price')]}),
                        F('x_labour', 'Labour cost', 'money'), F('x_total', 'Total cost', 'formula', formula='sum(x_parts.x_amount) + x_labour'),
                        F('x_stage', 'Stage', 'stage', options=opts('New', 'In progress', 'Waiting for parts', 'Done')),
                        F('x_report', 'Work done', 'richtext')],
             'form': {'title': 'x_name', 'status': 'x_stage',
                      'sections': [{'label': '', 'columns': [['x_equipment', 'x_kind', 'x_priority'], ['x_technician', 'x_reported', 'x_done']]}],
                      'tabs': [{'label': 'Parts and cost', 'fields': ['x_parts', 'x_labour', 'x_total']}, {'label': 'Work done', 'fields': ['x_report']}]},
             'list': ['x_name', 'x_equipment', 'x_kind', 'x_technician', 'x_reported', 'x_total', 'x_stage'],
             'kanban': {'title': 'x_name', 'fields': ['x_equipment', 'x_technician']},
             'automations': [{'name': 'Job finished', 'trigger': 'stage', 'field': 'x_stage', 'value': 'done',
                              'actions': [{'do': 'set', 'field': 'x_done', 'value': 'today'}]},
                             {'name': 'Urgent breakdown', 'trigger': 'create', 'conditions': [{'field': 'x_priority', 'op': '=', 'value': True}],
                              'actions': [{'do': 'activity', 'user_field': 'x_technician', 'summary': 'Urgent breakdown: {x_name}', 'days': 0}]}]},
        ]},
    'complaints': {
        'name': 'Complaints', 'icon': 'chat', 'color': '#E8475B',
        'description': 'Customer complaints from first call to resolution, with who handles them and how long it took.',
        'models': [{
            'model': 'x_tpl_complaint', 'label': 'Complaint', 'plural': 'Complaints', 'own': True, 'chatter': True, 'default_view': 'kanban',
            'fields': [F('x_name', 'Subject', 'text', required=True), F('x_customer', 'Customer', 'link', relation='res.partner'),
                       F('x_channel', 'Received by', 'choice', options=opts('Phone', 'Email', 'Walk-in', 'Social media')),
                       F('x_category', 'About', 'choice', options=opts('Product quality', 'Delivery', 'Invoice', 'Staff', 'Other')),
                       F('x_handler', 'Handled by', 'link', relation='res.users'), F('x_received', 'Received on', 'date'),
                       F('x_resolved', 'Resolved on', 'date'), F('x_amount', 'Compensation', 'money'),
                       F('x_stage', 'Stage', 'stage', options=opts('New', 'Investigating', 'Resolved', 'Closed')),
                       F('x_details', 'What happened', 'richtext'), F('x_resolution', 'How it was resolved', 'richtext')],
            'form': {'title': 'x_name', 'status': 'x_stage',
                     'sections': [{'label': '', 'columns': [['x_customer', 'x_channel', 'x_category'], ['x_handler', 'x_received', 'x_resolved', 'x_amount']]}],
                     'tabs': [{'label': 'What happened', 'fields': ['x_details']}, {'label': 'Resolution', 'fields': ['x_resolution']}]},
            'list': ['x_name', 'x_customer', 'x_category', 'x_handler', 'x_received', 'x_stage'],
            'kanban': {'title': 'x_name', 'fields': ['x_customer', 'x_category', 'x_handler']},
            'automations': [{'name': 'Assign the complaint', 'trigger': 'change', 'field': 'x_handler', 'conditions': [{'field': 'x_handler', 'op': 'set'}],
                             'actions': [{'do': 'activity', 'user_field': 'x_handler', 'summary': 'Complaint from {x_customer}: {x_name}', 'days': 2}]},
                            {'name': 'Resolved', 'trigger': 'stage', 'field': 'x_stage', 'value': 'resolved',
                             'actions': [{'do': 'set', 'field': 'x_resolved', 'value': 'today'}]}]}]},
    'bookings': {
        'name': 'Bookings', 'icon': 'calendar', 'color': '#2D6BFF',
        'description': 'Book meeting rooms, pool cars or equipment, see who has what on a calendar, approve requests.',
        'models': [
            {'model': 'x_tpl_resource', 'label': 'Resource', 'plural': 'Resources', 'own': True, 'chatter': False,
             'fields': [F('x_name', 'Name', 'text', required=True), F('x_kind', 'Kind', 'choice', options=opts('Room', 'Vehicle', 'Equipment')),
                        F('x_capacity', 'Seats', 'number'), F('x_location', 'Where', 'text')],
             'form': {'title': 'x_name', 'sections': [{'label': '', 'columns': [['x_kind', 'x_capacity'], ['x_location']]}]},
             'list': ['x_name', 'x_kind', 'x_capacity', 'x_location']},
            {'model': 'x_tpl_booking', 'label': 'Booking', 'plural': 'Bookings', 'own': True, 'chatter': True, 'default_view': 'calendar',
             'fields': [F('x_name', 'Purpose', 'text', required=True), F('x_resource', 'Resource', 'link', relation='x_tpl_resource', required=True),
                        F('x_by', 'Booked by', 'link', relation='res.users'), F('x_from', 'From', 'datetime', required=True), F('x_to', 'Until', 'datetime'),
                        F('x_stage', 'Status', 'stage', options=opts('Requested', 'Approved', 'Declined'))],
             'form': {'title': 'x_name', 'status': 'x_stage', 'sections': [{'label': '', 'columns': [['x_resource', 'x_by'], ['x_from', 'x_to']]}]},
             'list': ['x_name', 'x_resource', 'x_by', 'x_from', 'x_to', 'x_stage'], 'calendar': 'x_from'},
        ]},
    'inspections': {
        'name': 'Inspections', 'icon': 'clipboard', 'color': '#4CD07D',
        'description': 'Quality and safety inspections with a checklist, a score and follow-up actions.',
        'models': [{
            'model': 'x_tpl_inspection', 'label': 'Inspection', 'plural': 'Inspections', 'own': True, 'chatter': True,
            'fields': [F('x_name', 'Subject', 'text', required=True), F('x_area', 'Area', 'choice', options=opts('Production', 'Warehouse', 'Office', 'Vehicles')),
                       F('x_inspector', 'Inspector', 'link', relation='res.users'), F('x_date', 'Date', 'date'),
                       F('x_checks', 'Checklist', 'lines', lines={'fields': [F('x_name', 'Check', 'text', required=True), F('x_ok', 'Passed', 'checkbox'),
                                                                             F('x_points', 'Points', 'number'), F('x_remark', 'Remark', 'text')]}),
                       F('x_score', 'Score', 'formula', formula='sum(x_checks.x_points)'),
                       F('x_stage', 'Result', 'stage', options=opts('Planned', 'Passed', 'Failed', 'Follow-up'))],
            'form': {'title': 'x_name', 'status': 'x_stage', 'sections': [{'label': '', 'columns': [['x_area', 'x_inspector'], ['x_date', 'x_score']]}],
                     'tabs': [{'label': 'Checklist', 'fields': ['x_checks']}]},
            'list': ['x_name', 'x_area', 'x_inspector', 'x_date', 'x_score', 'x_stage'], 'calendar': 'x_date',
            'automations': [{'name': 'Failed inspection', 'trigger': 'stage', 'field': 'x_stage', 'value': 'failed',
                             'actions': [{'do': 'activity', 'user_field': 'x_inspector', 'summary': 'Plan the follow-up of {x_name}', 'days': 7}]}]}]},
}
