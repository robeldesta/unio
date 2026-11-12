"""EFRIS - Uganda Revenue Authority's Electronic Fiscal Receipting and Invoicing Solution, system-to-system mode.

Every posted sales invoice, cash sale and credit note of a VAT-registered Ugandan company is sent to URA. URA answers
with the fiscal document number (FDN), a verification code and the QR code text; they are stored on the voucher and
printed on the invoice. Sending is queued (cron every 5 minutes, and straight away on posting) and retried; an invoice
URA refuses stays visible with URA's message until it is fixed.

Protocol (URA "EFRIS system-to-system interface" for taxpayers' own ERP):
- every call is a JSON envelope {data: {content, signature, dataDescription}, globalInfo: {...}, returnStateInfo: {...}}
  posted to the interface URL; globalInfo carries the TIN, the device number issued by URA, the interface code
  (T101 server time, T103 sign-in, T104 symmetric key, T109 invoice upload, T110 credit note application, ...) and a
  unique exchange id;
- the business content is JSON, AES-encrypted with the key obtained from T104 and base64-encoded; it is signed
  with the taxpayer's private key (SHA1withRSA) from the key file URA issues with the device;
- answers come back the same way and are decrypted with the same key.

Modes: Off; Sandbox (URA's test server, needs URA test credentials); Live; and Simulation, which never calls URA and
gives every invoice a clearly marked test number, for demonstrations and training only."""
import base64
import gzip
import json
import logging
import uuid
from datetime import datetime

import requests

from odoo import api, fields, models
from odoo.exceptions import UserError

from odoo.addons.sonador_books.models.move_guard import controlled

_logger = logging.getLogger(__name__)

URLS = {'sandbox': 'https://efristest.ura.go.ug/efrisws/ws/taapp/getInformation',
        'live': 'https://efrisws.ura.go.ug/ws/taapp/getInformation'}
APP_ID, VERSION = 'AP04', '1.1.20191201'
TAX_CATEGORY = {18.0: ('01', '18%'), 0.0: ('02', '0%')}  # 01 standard, 02 zero rated, 03 exempt
UNIT_CODES = {'Units': '101', 'Unit': '101', 'kg': '103', 'g': '104', 'L': '102', 'm': '105', 'Carton': '113', 'Pack': '112'}


class ResCompany(models.Model):
    _inherit = 'res.company'

    sb_efris_mode = fields.Selection([('off', 'Off'), ('manual', 'Issued in the URA portal (numbers typed in)'),
                                      ('simulation', 'Simulation (no URA, test numbers)'), ('sandbox', 'URA sandbox'),
                                      ('live', 'Live')], 'EFRIS', default='off', required=True,
                                     help='Issued in the URA portal: each invoice is issued in EFRIS by hand and its fiscal '
                                          'document number and verification code are typed on the invoice; our print is then '
                                          'an invoice that refers to the EFRIS e-invoice.')
    sb_efris_device = fields.Char('EFRIS device number', help='Issued by URA when the system-to-system device is registered.')
    sb_efris_key = fields.Binary('EFRIS private key file (.pfx / .p12)', groups='base.group_system')
    sb_efris_key_password = fields.Char('Key file password', groups='base.group_system')
    sb_efris_branch = fields.Char('EFRIS branch id')
    sb_efris_operator = fields.Char('Operator name on invoices', default='Administrator')


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    sb_efris_category = fields.Char('EFRIS commodity code', help="URA's goods and services category code (e.g. 48181000 toilet paper).")
    sb_efris_registered = fields.Boolean('Registered with EFRIS', copy=False)


class SbVoucher(models.Model):
    _inherit = 'sb.voucher'

    sb_efris_state = fields.Selection([('none', 'Not for EFRIS'), ('manual', 'To issue in the URA portal'), ('waiting', 'Waiting to send'),
                                       ('done', 'Fiscalised'), ('error', 'Refused by URA')],
                                      'EFRIS', default='none', copy=False, index=True)
    sb_efris_fdn = fields.Char('Fiscal document number (FDN)', copy=False, index=True)
    sb_efris_code = fields.Char('Verification code', copy=False)
    sb_efris_qr = fields.Text('QR code', copy=False)
    sb_efris_invoice_id = fields.Char('URA invoice id', copy=False)
    sb_efris_error = fields.Char('URA message', copy=False)
    sb_efris_at = fields.Datetime('Fiscalised at', copy=False)
    sb_efris_test = fields.Boolean('Test number', copy=False, help='Simulation mode: not a fiscal document.')
    sb_efris_qr_png = fields.Binary('QR code image', compute='_compute_qr_png')
    sb_efris_mode = fields.Selection(related='company_id.sb_efris_mode')

    @api.depends('sb_efris_qr')
    def _compute_qr_png(self):
        R = self.env['ir.actions.report']
        for v in self:
            v.sb_efris_qr_png = base64.b64encode(R.barcode('QR', v.sb_efris_qr, width=150, height=150)) if v.sb_efris_qr else False

    def _sb_efris_due(self):
        self.ensure_one()
        c = self.company_id
        return c.country_id.code == 'UG' and c.sb_efris_mode != 'off' and self.voucher_type in ('sale', 'cash_sale', 'sale_return')

    def action_post(self):
        res = super().action_post()
        due = self.filtered(lambda v: v.state == 'posted' and v.sb_efris_state == 'none' and v._sb_efris_due())
        manual = due.filtered(lambda v: v.company_id.sb_efris_mode == 'manual')
        controlled(manual).write({'sb_efris_state': 'manual'})
        due -= manual
        if due:
            controlled(due).write({'sb_efris_state': 'waiting'})  # fiscalisation status: part of posting
            if not self.env.context.get('sb_efris_no_send'):
                self.env['sb.efris']._send(due)
        return res

    def _sb_free_fields(self):
        # issued in the URA portal: its numbers are typed on the posted invoice
        return super()._sb_free_fields() | {'sb_efris_fdn', 'sb_efris_code', 'sb_efris_state'}

    def write(self, vals):
        res = super().write(vals)
        if vals.get('sb_efris_fdn') and 'sb_efris_state' not in vals:
            # issued in the URA portal: the number typed in makes it fiscalised
            self.filtered(lambda v: v.sb_efris_state == 'manual').write({'sb_efris_state': 'done'})
        return res

    def action_sb_efris_send(self):
        controlled(self.filtered(lambda v: v.sb_efris_state in ('waiting', 'error'))).write({'sb_efris_state': 'waiting'})
        self.env['sb.efris']._send(self.filtered(lambda v: v.sb_efris_state == 'waiting'))
        return True


class SbEfrisLog(models.Model):
    _name = 'sb.efris.log'
    _description = 'EFRIS exchange'
    _order = 'id desc'

    company_id = fields.Many2one('res.company', required=True, index=True)
    voucher_id = fields.Many2one('sb.voucher', ondelete='set null', index=True)
    interface = fields.Char()
    ok = fields.Boolean()
    message = fields.Char()
    request = fields.Text(groups='base.group_system')
    response = fields.Text(groups='base.group_system')


class SbEfris(models.AbstractModel):
    _name = 'sb.efris'
    _description = 'EFRIS client'

    # ------------------------------------------------------------------ payloads
    def _payload_invoice(self, v):
        """T109 invoice upload (T110 application for a credit note), business content."""
        c = v.company_id
        lines, taxes = [], {}
        for i, l in enumerate(v.line_ids.filtered(lambda x: x.product_id and x.quantity)):
            rate = l.tax_ids[:1].amount if l.tax_ids else 0.0
            code, name = TAX_CATEGORY.get(rate, ('01', '%g%%' % rate))
            gross = l.price_total if 'price_total' in l._fields else l.price_subtotal * (1 + rate / 100.0)
            tax = gross - l.price_subtotal
            p = l.product_id
            lines.append({'item': (l.name or p.name)[:200], 'itemCode': p.default_code or str(p.id), 'qty': '%g' % l.quantity,
                          'unitOfMeasure': UNIT_CODES.get(p.uom_id.name, '101'), 'unitPrice': '%.2f' % (gross / l.quantity),
                          'total': '%.2f' % gross, 'taxRate': '%.2f' % (rate / 100.0), 'tax': '%.2f' % tax, 'orderNumber': str(i),
                          'discountFlag': '2', 'deemedFlag': '2', 'exciseFlag': '2', 'goodsCategoryId': p.sb_efris_category or '',
                          'vatApplicableFlag': '1' if rate else '0'})
            t = taxes.setdefault(code, {'taxCategoryCode': code, 'netAmount': 0.0, 'taxRate': '%.2f' % (rate / 100.0), 'taxAmount': 0.0,
                                        'grossAmount': 0.0, 'taxRateName': name})
            t['netAmount'] += l.price_subtotal
            t['taxAmount'] += tax
            t['grossAmount'] += gross
        for t in taxes.values():
            for k in ('netAmount', 'taxAmount', 'grossAmount'):
                t[k] = '%.2f' % t[k]
        buyer = v.partner_id
        return {
            'sellerDetails': {'tin': c.vat or '', 'legalName': c.name, 'businessName': c.name, 'address': ' '.join(filter(None, [c.street, c.city])),
                              'mobilePhone': c.phone or '', 'emailAddress': c.email or '', 'referenceNo': v.name, 'branchId': c.sb_efris_branch or ''},
            'basicInformation': {'invoiceNo': '', 'antifakeCode': '', 'deviceNo': c.sb_efris_device or '',
                                 'issuedDate': fields.Datetime.to_string(fields.Datetime.now()), 'operator': c.sb_efris_operator or 'Administrator',
                                 'currency': c.currency_id.name, 'oriInvoiceId': '', 'invoiceType': '1', 'invoiceKind': '1',
                                 'dataSource': '103', 'invoiceIndustryCode': '101', 'isBatch': '0'},
            'buyerDetails': {'buyerTin': buyer.vat or '', 'buyerLegalName': buyer.name or 'Cash customer', 'buyerType': '0' if buyer.vat else '1',
                             'buyerAddress': buyer.city or '', 'buyerMobilePhone': buyer.phone or '', 'buyerEmail': buyer.email or ''},
            'goodsDetails': lines,
            'taxDetails': list(taxes.values()),
            'summary': {'netAmount': '%.2f' % v.amount_untaxed, 'taxAmount': '%.2f' % v.amount_tax, 'grossAmount': '%.2f' % v.amount_total,
                        'itemCount': str(len(lines)), 'modeCode': '1', 'remarks': (v.memo or '')[:100], 'qrCode': ''},
            'payWay': [{'paymentMode': '102' if v.voucher_type == 'cash_sale' else '107', 'paymentAmount': '%.2f' % v.amount_total, 'orderNumber': 'a'}],
        }

    def _envelope(self, company, interface, content, key=None, private_key=None):
        raw = json.dumps(content, ensure_ascii=False).encode() if content is not None else b''
        encrypt = '0'
        if raw and key:
            raw = self._aes(key, raw, encrypt=True)
            encrypt = '2'
        content_b64 = base64.b64encode(raw).decode() if raw else ''
        signature = self._sign(private_key, content_b64) if (private_key and content_b64) else ''
        return {
            'data': {'content': content_b64, 'signature': signature,
                     'dataDescription': {'codeType': '1' if encrypt != '0' else '0', 'encryptCode': encrypt, 'zipCode': '0'}},
            'globalInfo': {'appId': APP_ID, 'version': VERSION, 'dataExchangeId': uuid.uuid4().hex, 'interfaceCode': interface,
                           'requestCode': 'TP', 'requestTime': datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'responseCode': 'TA',
                           'userName': 'admin', 'deviceMAC': 'FFFFFFFFFFFF', 'deviceNo': company.sb_efris_device or '',
                           'tin': company.vat or '', 'brn': '', 'taxpayerID': '1', 'longitude': '32.5825', 'latitude': '0.3476',
                           'extendField': {'responseDateFormat': 'dd/MM/yyyy', 'responseTimeFormat': 'dd/MM/yyyy HH:mm:ss'}},
            'returnStateInfo': {'returnCode': '', 'returnMessage': ''},
        }

    # ------------------------------------------------------------------ crypto
    def _aes(self, key, data, encrypt=True):
        from cryptography.hazmat.primitives import padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        k = base64.b64decode(key) if isinstance(key, str) else key
        cipher = Cipher(algorithms.AES(k), modes.ECB())
        if encrypt:
            p = padding.PKCS7(128).padder()
            data = p.update(data) + p.finalize()
            e = cipher.encryptor()
            return e.update(data) + e.finalize()
        d = cipher.decryptor()
        out = d.update(data) + d.finalize()
        u = padding.PKCS7(128).unpadder()
        return u.update(out) + u.finalize()

    def _private_key(self, company):
        from cryptography.hazmat.primitives.serialization import pkcs12
        c = company.sudo()
        if not c.sb_efris_key:
            raise UserError(self.env._('Upload the EFRIS key file URA issued for the device (Accounting settings).'))
        key, _cert, _extra = pkcs12.load_key_and_certificates(base64.b64decode(c.sb_efris_key),
                                                              (c.sb_efris_key_password or '').encode() or None)
        return key

    def _sign(self, private_key, text):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        return base64.b64encode(private_key.sign(text.encode(), padding.PKCS1v15(), hashes.SHA1())).decode()

    def _decrypt_key(self, private_key, b64):
        from cryptography.hazmat.primitives.asymmetric import padding
        return private_key.decrypt(base64.b64decode(b64), padding.PKCS1v15())

    # ------------------------------------------------------------------ calls
    def _call(self, company, interface, content=None, key=None, private_key=None, voucher=None):
        env_ = self._envelope(company, interface, content, key, private_key)
        Log = self.env['sb.efris.log'].sudo()
        try:
            r = requests.post(URLS[company.sb_efris_mode], json=env_, timeout=40)
            r.raise_for_status()
            ans = r.json()
        except Exception as e:  # noqa: BLE001
            Log.create({'company_id': company.id, 'voucher_id': voucher.id if voucher else False, 'interface': interface, 'ok': False,
                        'message': str(e)[:250], 'request': json.dumps(env_)[:20000]})
            raise UserError(self.env._('URA could not be reached: %s', e)) from e
        state = ans.get('returnStateInfo') or {}
        ok = state.get('returnCode') == '00'
        data = (ans.get('data') or {})
        content_b64 = data.get('content') or ''
        body = None
        if content_b64:
            raw = base64.b64decode(content_b64)
            desc = data.get('dataDescription') or {}
            if desc.get('encryptCode') == '2' and key:
                raw = self._aes(key, raw, encrypt=False)
            if desc.get('zipCode') == '1':
                raw = gzip.decompress(raw)
            try:
                body = json.loads(raw.decode())
            except ValueError:
                body = raw.decode(errors='replace')
        Log.create({'company_id': company.id, 'voucher_id': voucher.id if voucher else False, 'interface': interface, 'ok': ok,
                    'message': state.get('returnMessage') or ('OK' if ok else '?'), 'request': json.dumps(env_)[:20000],
                    'response': json.dumps(body)[:20000] if body is not None else ''})
        if not ok:
            raise UserError(self.env._('URA: %s', state.get('returnMessage') or state.get('returnCode')))
        return body

    def _session_key(self, company, private_key):
        """T104: the symmetric key for this session, sent encrypted with the device's public key."""
        body = self._call(company, 'T104', None, private_key=private_key)
        key_enc = (body or {}).get('passowrdDes') or (body or {}).get('passwordDes')
        if not key_enc:
            raise UserError(self.env._('URA did not return the session key (T104).'))
        return base64.b64encode(self._decrypt_key(private_key, key_enc)).decode()

    # ------------------------------------------------------------------ sending
    @api.model
    def _send(self, vouchers):
        vouchers = controlled(vouchers)  # writes the fiscalisation status of posted vouchers
        by_company = {}
        for v in vouchers:
            by_company.setdefault(v.company_id, vouchers.browse())
            by_company[v.company_id] |= v
        for company, vs in by_company.items():
            if company.sb_efris_mode == 'simulation':
                for v in vs:
                    self._simulate(v)
                continue
            try:
                pk = self._private_key(company)
                key = self._session_key(company, pk)
            except UserError as e:
                vs.write({'sb_efris_state': 'error', 'sb_efris_error': str(e)[:250]})
                continue
            for v in vs:
                try:
                    with self.env.cr.savepoint():
                        content = self._payload_invoice(v)
                        if v.voucher_type == 'sale_return':
                            orig = v.origin_id if v.origin_id and v.origin_id._name == 'sb.voucher' else False
                            content['basicInformation'].update(invoiceType='2', oriInvoiceId=orig.sb_efris_invoice_id if orig else '',
                                                               oriInvoiceNo=orig.sb_efris_fdn if orig else '')
                            body = self._call(company, 'T110', content, key, pk, v)
                        else:
                            body = self._call(company, 'T109', content, key, pk, v)
                        info = (body or {}).get('basicInformation') or {}
                        summ = (body or {}).get('summary') or {}
                        v.write({'sb_efris_state': 'done', 'sb_efris_fdn': info.get('invoiceNo') or (body or {}).get('referenceNo'),
                                 'sb_efris_code': info.get('antifakeCode') or '', 'sb_efris_invoice_id': info.get('invoiceId') or '',
                                 'sb_efris_qr': summ.get('qrCode') or '', 'sb_efris_at': fields.Datetime.now(), 'sb_efris_error': False})
                except UserError as e:
                    v.write({'sb_efris_state': 'error', 'sb_efris_error': str(e)[:250]})
        return True

    def _simulate(self, v):
        """Simulation: a recognisable test number and QR text, never shown as fiscal."""
        n = self.env['ir.sequence'].sudo().next_by_code('sb.efris.sim') or str(v.id)
        fdn = 'TEST-%s' % n.zfill(10)
        code = uuid.uuid5(uuid.NAMESPACE_URL, v.name + fdn).hex[:20].upper()
        qr = 'https://efris.ura.go.ug/site_mobile/#/invoiceValidation?invoiceNo=%s&antiFakeCode=%s' % (fdn, code)
        v.write({'sb_efris_state': 'done', 'sb_efris_fdn': fdn, 'sb_efris_code': code, 'sb_efris_qr': qr, 'sb_efris_test': True,
                 'sb_efris_at': fields.Datetime.now(), 'sb_efris_error': False})
        self.env['sb.efris.log'].sudo().create({'company_id': v.company_id.id, 'voucher_id': v.id, 'interface': 'SIM', 'ok': True,
                                                'message': 'Simulation: %s' % fdn})

    @api.model
    def _cron_send(self):
        waiting = self.env['sb.voucher'].search([('sb_efris_state', '=', 'waiting')], limit=200)
        self._send(waiting)

    @api.model
    def overview(self):
        V = self.env['sb.voucher']
        c = self.env.company
        return {'mode': c.sb_efris_mode, 'waiting': V.search_count([('sb_efris_state', '=', 'waiting'), ('company_id', '=', c.id)]),
                'errors': V.search_count([('sb_efris_state', '=', 'error'), ('company_id', '=', c.id)]),
                'done': V.search_count([('sb_efris_state', '=', 'done'), ('company_id', '=', c.id)])}
