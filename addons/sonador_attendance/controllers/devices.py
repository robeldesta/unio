"""ZKTeco devices.

1. Devices with ADMS ("Cloud server" / "PUSH" in the device's Comm. menu) push straight to the server: set the server address
   and port of the server on the device; it is recognised by its serial number, which HR registers in Attendance > Setup.
2. Older devices (pull only): the sync agent (static/agent/zk_agent.py) runs on a PC on the same network, reads the device and
   posts to /attendance/push with the device's token.
"""
import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

OPTIONS = """GET OPTION FROM: {sn}
ATTLOGStamp=None
OPERLOGStamp=9999
ATTPHOTOStamp=None
ErrorDelay=60
Delay=30
TransTimes=00:00;14:05
TransInterval=1
TransFlag=TransData AttLog OpLog EnrollUser
TimeZone=3
Realtime=1
Encrypt=None
"""


def _text(body, status=200):
    return request.make_response(body, headers=[('Content-Type', 'text/plain')], status=status)


class AttendanceDevices(http.Controller):

    def _device(self, sn):
        if not sn:
            return None
        return request.env['sb.att.device'].sudo().search([('serial', '=', sn), ('active', '=', True)], limit=1)

    @http.route('/iclock/cdata', type='http', auth='public', methods=['GET', 'POST'], csrf=False, save_session=False)
    def adms_cdata(self, SN=None, table=None, **kw):
        dev = self._device(SN)
        if not dev:
            _logger.warning('Attendance push from unknown device serial %s', SN)
            return _text('Unknown device', 403)
        dev._touch()
        if request.httprequest.method == 'GET':
            return _text(OPTIONS.format(sn=SN))
        body = request.httprequest.get_data(as_text=True) or ''
        if not body and request.httprequest.form:
            # some firmware labels the text as a web form: put the lines back together
            body = '&'.join(k + ('=' + v if v else '') for k, v in request.httprequest.form.items(multi=True))
        if table == 'ATTLOG':
            rows = []
            for line in body.splitlines():
                parts = line.strip().split('\t')
                if len(parts) >= 2 and parts[0]:
                    rows.append({'bio': parts[0].strip(), 'time': parts[1].strip(), 'status': parts[2] if len(parts) > 2 else ''})
            n = dev._ingest(rows, local=True)
            return _text('OK: %d' % n)
        if table == 'OPERLOG':
            users = []
            for line in body.splitlines():
                if line.startswith('USER '):
                    f = dict(x.split('=', 1) for x in line[5:].split('\t') if '=' in x)
                    if f.get('PIN'):
                        users.append({'bio': f['PIN'].strip(), 'name': (f.get('Name') or '').strip()})
            dev._ingest([], users=users)
            return _text('OK')
        return _text('OK')

    @http.route(['/iclock/getrequest', '/iclock/devicecmd'], type='http', auth='public', methods=['GET', 'POST'], csrf=False,
                save_session=False)
    def adms_request(self, SN=None, **kw):
        dev = self._device(SN)
        if not dev:
            return _text('Unknown device', 403)
        dev._touch()
        return _text('OK')

    @http.route(['/attendance/push', '/sonador/attendance/push'], type='http', auth='public', methods=['POST'], csrf=False, save_session=False)
    def agent_push(self, **kw):
        try:
            data = json.loads(request.httprequest.get_data(as_text=True) or '{}')
        except ValueError:
            return request.make_json_response({'error': 'bad json'}, status=400)
        dev = request.env['sb.att.device'].sudo().search([('token', '=', data.get('token') or '-'), ('active', '=', True)], limit=1)
        if not data.get('token') or not dev:
            return request.make_json_response({'error': 'unknown device token'}, status=403)
        dev._touch()
        n = dev._ingest(data.get('punches') or [], users=data.get('users') or [], local=True)
        return request.make_json_response({'ok': True, 'new': n, 'device': dev.name})
