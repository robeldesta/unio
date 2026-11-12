import logging
import os
from datetime import datetime

from odoo import api, fields, models, tools
from odoo.exceptions import AccessError

_logger = logging.getLogger(__name__)
KEEP = 14


class SbMaintenance(models.AbstractModel):
    _name = 'sb.maintenance'
    _description = 'Data Maintenance'

    @api.model
    def _backup_dir(self):
        path = os.path.join(tools.config['data_dir'], 'backups', self.env.cr.dbname)
        os.makedirs(path, exist_ok=True)
        return path

    def _check(self):
        if not self.env.user.has_group('base.group_system'):
            raise AccessError(self.env._('Only administrators can manage backups.'))

    @api.model
    def get_status(self):
        self._check()
        path = self._backup_dir()
        files = []
        for name in sorted(os.listdir(path), reverse=True):
            if name.endswith('.zip'):
                full = os.path.join(path, name)
                st = os.stat(full)
                files.append({'name': name, 'size': st.st_size,
                              'when': datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M')})
        cron = self.env.ref('sonador_books.cron_sb_backup', raise_if_not_found=False)
        size = 0
        self.env.cr.execute('SELECT pg_database_size(current_database())')
        size = self.env.cr.fetchone()[0]
        return {'files': files, 'keep': KEEP, 'auto': bool(cron and cron.active),
                'next': fields.Datetime.to_string(cron.nextcall) if cron else '',
                'db_size': size, 'folder': path}

    @api.model
    def backup_now(self):
        self._check()
        return self._write_backup()

    def _write_backup(self):
        from odoo.service import db
        path = self._backup_dir()
        name = '%s_%s.zip' % (self.env.cr.dbname, datetime.now().strftime('%Y-%m-%d_%H%M%S'))
        full = os.path.join(path, name)
        with open(full, 'wb') as fh:
            db.dump_db(self.env.cr.dbname, fh, 'zip')
        files = sorted(f for f in os.listdir(path) if f.endswith('.zip'))
        for old in files[:-KEEP]:
            os.remove(os.path.join(path, old))
        _logger.info('Accounting backup written: %s', full)
        return name

    @api.model
    def _cron_backup(self):
        self.sudo()._write_backup()

    @api.model
    def set_auto(self, active):
        self._check()
        cron = self.env.ref('sonador_books.cron_sb_backup')
        cron.sudo().active = bool(active)
        return True

    @api.model
    def verify(self):
        """Quick integrity check: every posted entry balances and no posted line lacks an account."""
        self._check()
        self.env.cr.execute("""
            SELECT m.name FROM account_move m JOIN account_move_line l ON l.move_id = m.id
            WHERE m.state = 'posted' GROUP BY m.id, m.name HAVING abs(sum(l.debit) - sum(l.credit)) > 0.01 LIMIT 20""")
        unbalanced = [r[0] for r in self.env.cr.fetchall()]
        self.env.cr.execute("SELECT count(*) FROM account_move_line WHERE account_id IS NULL AND parent_state = 'posted'")
        no_account = self.env.cr.fetchone()[0]
        return {'ok': not unbalanced and not no_account, 'unbalanced': unbalanced, 'no_account': no_account}
