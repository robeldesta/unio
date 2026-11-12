"""Files in Unio AI: receipts, bills and photos the person gives the assistant. The assistant reads them (pictures and
PDFs); a record created from its draft gets the file attached; an existing record gets it on a card the person clicks.
Files go to the model only with the message they came with; later messages refer to them by number. Unio HQ can switch
files off (they cost more than text)."""
import base64

from odoo import api, models
from odoo.exceptions import UserError
from odoo.tools.image import image_process

IMAGES = {'image/jpeg', 'image/png', 'image/webp', 'image/gif'}
MAX_FILES, MAX_MB = 5, 10


class SbAiFiles(models.AbstractModel):
    _inherit = 'sb.ai'

    @api.model
    def upload(self, name, mimetype, data):
        """A file for the next message (base64). Pictures are brought down to the size the model reads (long side 1568)."""
        if not self.env['unio.ai'].meter().get('files', True):
            raise UserError(self.env._('Files are switched off for Unio AI.'))
        raw = base64.b64decode(data or b'')
        if mimetype not in IMAGES and mimetype != 'application/pdf':
            raise UserError(self.env._('Unio AI reads pictures (JPEG, PNG, WebP) and PDFs.'))
        if len(raw) > MAX_MB * 1024 * 1024:
            raise UserError(self.env._('%s is over %s MB.', name, MAX_MB))
        if mimetype in IMAGES:
            raw = image_process(raw, size=(1568, 1568), quality=85, output_format='JPEG')
            mimetype = 'image/jpeg'
        att = self.env['ir.attachment'].sudo().create({'name': name, 'mimetype': mimetype, 'raw': raw,
                                                       'res_model': 'sb.ai.chat', 'res_id': 0})
        return {'id': att.id, 'name': att.name, 'mimetype': mimetype}

    def _files(self, ids):
        """The person's own uploaded files, in the order given."""
        A = self.env['ir.attachment'].sudo()
        found = A.browse([int(i) for i in ids or []]).exists().filtered(
            lambda a: a.res_model == 'sb.ai.chat' and a.create_uid.id == self.env.uid)
        if len(found) > MAX_FILES:
            raise UserError(self.env._('At most %s files in one message.', MAX_FILES))
        return found

    def _expand(self, messages, current):
        """Messages for the model: the files of this message as pictures / documents, earlier files as a short note."""
        A = self.env['ir.attachment'].sudo()
        out = []
        for m in messages:
            content = m.get('content')
            if m.get('role') != 'user' or not isinstance(content, list) or not any(b.get('type') == 'unio_file' for b in content):
                out.append(m)
                continue
            blocks = []
            for b in content:
                if b.get('type') != 'unio_file':
                    blocks.append(b)
                    continue
                att = A.browse(b['id']).exists()
                if att and b['id'] in current:
                    data = base64.b64encode(att.raw).decode()
                    kind = 'image' if att.mimetype in IMAGES else 'document'
                    blocks.append({'type': 'text', 'text': 'File #%d: %s' % (att.id, att.name)})
                    blocks.append({'type': kind, 'source': {'type': 'base64', 'media_type': att.mimetype, 'data': data}})
                else:
                    blocks.append({'type': 'text', 'text': '(File #%d: %s, sent earlier in this chat)' % (b['id'], b.get('name', ''))})
            out.append({'role': 'user', 'content': blocks})
        return out

    def _attach(self, file_ids, model, res_id):
        rec = self.env[model].browse(int(res_id)).exists()
        if not rec:
            raise UserError(self.env._('That record no longer exists.'))
        rec.check_access('write')
        for att in self._files(file_ids):
            att.copy({'res_model': model, 'res_id': rec.id})
        return rec
