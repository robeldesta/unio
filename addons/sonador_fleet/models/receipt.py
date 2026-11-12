"""telebirr receipt check: the public Ethio Telecom receipt page shows who was paid, how much, when, and (for fuel
stations) the vehicle plate. Used to catch reused, altered or someone else's receipts."""
import logging
import re

import requests
from lxml import html as lxml_html

from odoo import models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)
BASE = 'https://transactioninfo.ethiotelecom.et/receipt/'


def _birr(v):
    s = re.sub(r'[^\d.]', '', str(v or '').replace(',', ''))
    try:
        return float(s) if s else 0.0
    except ValueError:
        return 0.0


def plate_digits(plate):
    """The number part of an Ethiopian plate (last five digits), enough to match a receipt to a vehicle."""
    d = re.sub(r'\D', '', plate or '')
    return d[-5:] if len(d) >= 5 else d


class SbFleetReceipt(models.AbstractModel):
    _name = 'sb.fleet.receipt'
    _description = 'telebirr receipt check'

    def fetch(self, ref):
        ref = (ref or '').strip().upper()
        if not re.match(r'^[A-Z0-9]{6,20}$', ref):
            raise UserError(self.env._('That does not look like a telebirr transaction number.'))
        try:
            r = requests.get(BASE + ref, timeout=25, headers={'User-Agent': 'Mozilla/5.0 (compatible; Fleet/1.0)'})
            r.raise_for_status()
        except requests.RequestException as e:
            raise UserError(self.env._('Could not reach the telebirr receipt page: %s', e)) from e
        text = r.text or ''
        if 'telebirr' not in text.lower() and 'Invoice No' not in text:
            raise UserError(self.env._('Receipt %s was not found.', ref))
        return self.parse(text, ref)

    def parse(self, text, ref=''):
        tree = lxml_html.fromstring(text or '<html/>')
        out = {'ref': ref, 'plate': '', 'payee': '', 'payer': '', 'status': '', 'reason': '', 'amount': 0.0, 'date': ''}
        settled = total = 0.0
        for tr in tree.xpath('//tr'):
            cells = [' '.join((c.text_content() or '').split()) for c in tr.xpath('./td')]
            if len(cells) >= 2 and cells[0]:
                lab, val = cells[0].lower(), cells[1]
                if 'vehicle plate' in lab or 'ሰሌዳ' in cells[0]:
                    out['plate'] = val
                elif 'credited party name' in lab or 'ተቀባይ ስም' in cells[0]:
                    out['payee'] = val
                elif 'payer name' in lab or 'ከፋይ ስም' in cells[0]:
                    out['payer'] = val
                elif 'transaction status' in lab or 'ክፍያው ሁኔታ' in cells[0]:
                    out['status'] = val
                elif 'payment reason' in lab or 'ክፍያ ምክንያት' in cells[0]:
                    out['reason'] = val
                elif 'total paid amount' in lab or 'ጠቅላላ የተከፈለ' in cells[0]:
                    total = _birr(cells[-1])
                elif 'settled amount' in lab or 'ተከፈለው መጠን' in cells[0]:
                    settled = _birr(cells[-1])
            if len(cells) >= 3 and re.match(r'^[A-Z0-9]{6,}$', cells[0].upper()):
                out['date'] = cells[1]
                settled = _birr(cells[2]) or settled
        out['amount'] = total or settled
        if not out['plate'] and not out['amount']:
            raise UserError(self.env._('Could not read the amount from receipt %s.', ref))
        return out
