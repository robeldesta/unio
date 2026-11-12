"""Report Center catalog and the register / journal / list reports built on the report engine.

Every entry: key, category, name, description, priority (1 = available, 2/3 = coming), report (engine key)
and preset options. Many Peachtree reports are the same engine report with a preset (e.g. Sales Journal =
journal report on the sales journal).
"""
from collections import defaultdict
from datetime import date, timedelta

from odoo import api, fields, models
from odoo.tools import html2plaintext

from .ethiopian_calendar import format_ethiopian
from .locale import locale_of

CATEGORIES = [
    ('ar', 'Accounts Receivable', 'Customers and Sales'),
    ('ap', 'Accounts Payable', 'Vendors and Purchases'),
    ('payroll', 'Payroll', 'Employees and Payroll'),
    ('gl', 'General Ledger', 'Chart of Accounts and Journals'),
    ('fs', 'Financial Statements', 'IFRS Statements'),
    ('tax', 'Taxes', 'Tax declarations'),
    ('inventory', 'Inventory', 'Items, Stock and Costing'),
    ('jobs', 'Job Reports', 'Project Information'),
    ('time', 'Time / Expense Reports', 'Time and Expense Tickets'),
    ('bank', 'Account Reconciliation', 'Banks, Cash and Cheques'),
    ('assets', 'Fixed Assets', 'Assets and Deferred Revenue'),
    ('company', 'Company Reports', 'Audit Trail and Controls'),
    ('groups', 'Report Groups', 'Report Packs'),
]


def E(key, cat, name, desc, prio=1, report=None, options=None, form=False):
    return {'key': key, 'category': cat, 'name': name, 'description': desc, 'priority': prio,
            'report': report or key, 'options': options or {}, 'form': form}


CATALOG = [
    # ---------------------------------------------------------------- Accounts receivable
    E('aged_ar', 'ar', 'Aged Receivables', 'Open customer balances by days overdue, as at any date.', 1, 'aged', {'partner_type': 'customer'}),
    E('crj', 'ar', 'Cash Receipts Journal', 'Receipts (CRV) and cash sales with their debit and credit lines.', 1, 'journal', {'registers': ['receipts']}),
    E('sj', 'ar', 'Sales Journal', 'Credit sales invoices and credit notes posted to the ledger.', 1, 'journal', {'registers': ['sales']}),
    E('cust_ledger', 'ar', 'Customer Ledgers', 'Every transaction per customer with running balance.', 1, 'partner_ledger', {'partner_type': 'customer'}),
    E('cust_statement', 'ar', 'Customer Statement', 'Statement to send a customer: opening balance, transactions, amount due, ageing.', 1, 'partner_statement', {'partner_type': 'customer'}, form=True),
    E('invoice_register', 'ar', 'Invoice Register', 'Sales invoices with VAT, total, amount paid and balance due.', 1, 'document_register', {'register': 'sale'}),
    E('cash_sales_register', 'ar', 'Cash Sales Register', 'Cash sales with how they were banked (bank, cash, mobile money, cheque).', 1, 'document_register', {'register': 'cash_sale'}),
    E('cust_sales_history', 'ar', 'Customer Sales History', 'Sales per customer (net of returns), with VAT and invoice count.', 1, 'partner_sales', {'partner_type': 'customer'}),
    E('cust_tx_history', 'ar', 'Customer Transaction History', 'All documents per customer including unposted.', 2),
    E('cust_mgmt', 'ar', 'Customer Management Detail', 'Open invoices, due dates and contacts per customer.', 2),
    E('cust_list', 'ar', 'Customer List', 'Customers with TIN, VAT number, phone and address.', 1, 'partner_list', {'partner_type': 'customer'}),
    E('contact_list', 'ar', 'Contact List', 'Contact person, phone, email and address for each customer.', 1, 'partner_list', {'partner_type': 'customer'}),
    E('cust_master', 'ar', 'Customer Master File List', 'Full detail for every customer: TIN, VAT no., terms, balance.', 1, 'partner_list', {'partner_type': 'customer'}),
    E('cust_advances', 'ar', 'Customer Advances & Unapplied Receipts', 'Money received not yet applied to invoices, and open credit notes.', 1, 'open_credits', {'partner_type': 'customer'}),
    E('wht_received', 'ar', 'Withholding Received', 'Tax withheld by customers: receipt number, date, customer TIN.', 1, 'wht_register', {'side': 'received'}),
    E('items_sold', 'ar', 'Items Sold to Customers', 'Quantities and amounts per item and customer.', 3),
    E('so_register', 'ar', 'Quote / Sales Order Register', 'Quotations and sales orders.', 3),
    E('picklist', 'ar', 'Picklist Report', 'Items on open sales orders to pick and deliver.', 3),
    E('proposal_register', 'ar', 'Proposal Register', 'Proposals and their open or closed status.', 3),
    E('proposal_report', 'ar', 'Proposal Report', 'Accepted proposals with amount billed and remaining.', 3),
    E('backorder', 'ar', 'Sales Backorder Report', 'Items on open sales orders not yet delivered.', 3),
    E('prospects', 'ar', 'Prospect List', 'Leads and prospects.', 3),
    # ---------------------------------------------------------------- Accounts payable
    E('aged_ap', 'ap', 'Aged Payables', 'Open vendor balances by days overdue, as at any date.', 1, 'aged', {'partner_type': 'vendor'}),
    E('cdj', 'ap', 'Cash Disbursements Journal', 'Payments (CPV) and cash purchases with their debit and credit lines.', 1, 'journal', {'registers': ['disbursements']}),
    E('pj', 'ap', 'Purchase Journal', 'Vendor bills and debit notes posted to the ledger.', 1, 'journal', {'registers': ['purchases']}),
    E('vend_ledger', 'ap', 'Vendor Ledgers', 'Every transaction per vendor with running balance.', 1, 'partner_ledger', {'partner_type': 'vendor'}),
    E('cash_requirements', 'ap', 'Cash Requirements', 'Unpaid bills by due date: what has to be paid when.', 1, 'cash_requirements'),
    E('cheque_register', 'ap', 'Cheque Register', 'Cheques issued and received with status (on hand, cleared, bounced).', 1, 'cheque_register'),
    E('epay_register', 'ap', 'Electronic Payment Register', 'Bank transfers made, by bank.', 2),
    E('vend_list', 'ap', 'Vendor List', 'Vendors with TIN, VAT number, phone and address.', 1, 'partner_list', {'partner_type': 'vendor'}),
    E('vend_master', 'ap', 'Vendor Master File List', 'Full detail for every vendor: TIN, VAT no., terms, balance.', 1, 'partner_list', {'partner_type': 'vendor'}),
    E('vend_advances', 'ap', 'Vendor Advances', 'Money paid to vendors not yet applied to bills, and open debit notes.', 1, 'open_credits', {'partner_type': 'vendor'}),
    E('wht_payable', 'ap', 'Withholding Payable Register', 'Tax withheld from vendors for the monthly remittance: vendor TIN, receipt no., base, amount.', 1, 'wht_register', {'side': 'payable'}),
    E('vend_history', 'ap', 'Vendor Transaction History', 'All documents per vendor including unposted.', 2),
    E('vend_purchases', 'ap', 'Vendor Purchase History', 'Purchases per vendor with VAT and bill count.', 1, 'partner_sales', {'partner_type': 'vendor'}),
    E('items_purchased', 'ap', 'Items Purchased from Vendors', 'Quantities and amounts per item and vendor.', 3),
    E('po_register', 'ap', 'Purchase Order Register', 'Purchase orders and receipts.', 3),
    # ---------------------------------------------------------------- Payroll
    E('payroll_register', 'payroll', 'Payroll Register', 'Every employee per month: basic, allowances, PAYE, pension, deductions and net pay.', 1, 'payroll_register'),
    E('payroll_journal', 'payroll', 'Payroll Journal', 'The payroll entries and salary payments as posted to the ledger.', 3),
    E('earnings', 'payroll', 'Yearly Earnings', 'Earnings, tax and pension per employee for the period (year to date by default).', 1, 'employee_earnings', {'mode': 'ytd'}),
    E('employee_list', 'payroll', 'Employee List', 'Employees with position, TIN, pension number and pay.', 1, 'employee_list'),
    E('lst_return', 'payroll', 'Local Service Tax Schedule', 'Local Service Tax deducted per employee for the local government (July - October).', 3),
    E('emp_income_tax', 'payroll', 'Employment Income Tax Declaration', 'Monthly PAYE declaration for the tax office: TIN, taxable income, tax withheld.', 1, 'paye_declaration'),
    E('poessa', 'payroll', 'Pension Declaration (employee + employer)', 'Monthly pension declaration: pension number, basic salary, employee and employer contributions.', 1, 'poessa_declaration'),
    E('payroll_cheques', 'payroll', 'Payroll Check Register', 'Salary payments by bank and reference.', 3),
    E('leave', 'payroll', 'Leave Balances', 'Leave taken and remaining.', 3),
    # ---------------------------------------------------------------- General ledger
    E('gl', 'gl', 'General Ledger', 'Every account with opening balance, transactions and running balance.', 1, 'general_ledger'),
    E('tb', 'gl', 'General Ledger Trial Balance', 'Opening, debits, credits and closing per account.', 1, 'trial_balance'),
    E('working_tb', 'gl', 'Working Trial Balance', 'Trial balance with blank adjustment columns for year-end work.', 2),
    E('genj', 'gl', 'General Journal', 'Journal vouchers and opening balances.', 1, 'journal', {'registers': ['general']}),
    E('all_journals', 'gl', 'All Journals', 'Every posted entry in date order, any journal.', 1, 'journal'),
    E('tx_detail', 'gl', 'Transaction Detail', 'Search every ledger line by account, partner, reference or amount.', 1, 'transactions'),
    E('coa', 'gl', 'Chart of Accounts', 'Accounts with type, IFRS line item and balance.', 1, 'chart'),
    E('cash_register', 'gl', 'Cash Account Register', 'Bank and cash accounts: every movement with running balance.', 1, 'cash_book'),
    E('budget', 'gl', 'Budget', 'Budget per account and period.', 2),
    E('acc_variance', 'gl', 'Account Variance', 'Actual against budget per account.', 2),
    # ---------------------------------------------------------------- Financial statements
    E('sfp', 'fs', 'Statement of Financial Position', 'Balance sheet on IFRS line items, with comparison dates.', 1, 'balance_sheet'),
    E('is_ytd', 'fs', 'Income Statement', 'Current period and year to date, with % of revenue.', 1, 'income_ytd'),
    E('is_12', 'fs', 'Income Statement 12 Periods', 'Hamle to Sene, one column per Ethiopian month.', 1, 'profit_loss', {'comparison': 'months', 'mode': 'fy'}),
    E('is_2y', 'fs', 'Income Statement 2 Years', 'This fiscal year against last fiscal year with variance.', 1, 'profit_loss', {'comparison': 'last_year', 'mode': 'fy'}),
    E('pl_oci', 'fs', 'Profit or Loss and OCI (IFRS)', 'Statement of profit or loss and other comprehensive income (IAS 1).', 1, 'profit_loss', {'oci': True}),
    E('cash_flow', 'fs', 'Statement of Cash Flows (indirect)', 'Operating (indirect), investing and financing cash flows (IAS 7).', 1, 'cash_flow', {'comparison': 'last_year'}),
    E('cash_flow_simple', 'fs', 'Cash Flow (simple)', 'Cash in and out by account, period by period.', 1, 'cash_flow', {'simple': True}),
    E('gl_summary', 'fs', 'GL Account Summary', 'Opening, movement and closing per account (summary of the ledger).', 1, 'trial_balance', {'summary': True}),
    E('is_budget', 'fs', 'Income Statement / Budget', 'Actual against budget.', 2),
    E('equity_changes', 'fs', 'Statement of Changes in Equity', 'Movements in share capital, reserves and retained earnings.', 2, 'equity_changes', {'comparison': 'last_year'}),
    E('retained', 'fs', 'Retained Earnings', 'Opening, profit, dividends and closing retained earnings.', 2),
    E('notes_pack', 'fs', 'Notes to the Financial Statements', 'One note per line of the statement of financial position, with the accounts in it and the previous year-end.', 2),
    E('designer', 'fs', 'Statement Designer', 'Design custom statements: rows and columns on a grid.', 2),
    # ---------------------------------------------------------------- Taxes
    E('vat_return', 'tax', 'VAT Declaration (monthly)', 'Output VAT, input VAT and VAT payable for the month, by tax grid.', 1, 'vat_return'),
    E('vat_sales', 'tax', 'VAT Sales Register', 'Every taxable sale with buyer TIN, invoice no., base and VAT (for e-filing).', 1, 'vat_register', {'side': 'sale'}),
    E('vat_purchases', 'tax', 'VAT Purchase Register', 'Every taxable purchase with seller TIN, receipt no., base and VAT (for e-filing).', 1, 'vat_register', {'side': 'purchase'}),
    E('wht_return', 'tax', 'Withholding Tax Declaration (monthly)', 'Tax withheld from vendors this month, per vendor TIN.', 1, 'wht_register', {'side': 'payable', 'mode': 'month'}),
    E('tot_return', 'tax', 'TOT Declaration (historic, before 8 Jul 2024)', 'Turnover tax, repealed from Hamle 1, 2017 E.C. (8 July 2024) by VAT Proclamation 1341/2024: for periods before that date only.', 2, 'tot_return'),
    E('profit_tax', 'tax', 'Profit Tax Worksheet', 'Accounting profit to taxable profit.', 2),
    # ---------------------------------------------------------------- Inventory
    E('stock_card', 'inventory', 'Stock Card', 'Bin card: every receipt, issue, sale and count per item, with running quantity and value.', 3),
    E('item_costing', 'inventory', 'Item Costing', 'FIFO receipts still in stock and average costs, per item.', 3),
    E('inv_valuation', 'inventory', 'Inventory Valuation', 'Quantity and value on hand per item and item class, at any date.', 3),
    E('cogs_journal', 'inventory', 'Cost of Goods Sold Journal', 'Every sale with its cost of goods and gross margin.', 3),
    E('adj_journal', 'inventory', 'Inventory Adjustment Journal', 'Stock counts and adjustments: books, counted and the difference posted.', 3),
    E('grn_register', 'inventory', 'GRN Register', 'Goods received notes per supplier, with items and value.', 3),
    E('giv_register', 'inventory', 'GIV Register', 'Goods issued to departments, sites and jobs, with the expense charged.', 3),
    E('reorder', 'inventory', 'Reorder Worksheet', 'Stock items at or below their reorder point, with the quantity to order.', 3),
    E('price_list', 'inventory', 'Item List / Price List', 'Every item with its type, class, unit, price, cost and quantity on hand.', 3),
    # ---------------------------------------------------------------- Jobs
    E('job_ledger', 'jobs', 'Job Ledger', 'Costs and revenue per project.', 3),
    E('job_profit', 'jobs', 'Job Profitability', 'Revenue, cost and margin per project.', 3),
    E('job_list', 'jobs', 'Job List', 'Projects with customer and status.', 3),
    E('unbilled_job', 'jobs', 'Unbilled Job Expense', 'Project costs not yet invoiced.', 3),
    E('cost_codes', 'jobs', 'Cost Code List', 'Job cost codes with type and status.', 3),
    E('job_est_exp', 'jobs', 'Estimated Job Expenses', 'Estimated against actual expenses per job.', 3),
    E('job_est_rev', 'jobs', 'Estimated Job Revenue', 'Estimated against actual revenue per job.', 3),
    E('job_cost_type', 'jobs', 'Job Costs by Type', 'Costs and units by cost type for jobs with phases.', 3),
    E('job_estimates', 'jobs', 'Job Estimates', 'Estimated units and expenses per job, phase and cost code.', 3),
    E('job_master', 'jobs', 'Job Master File List', 'Jobs with full detail.', 3),
    E('job_register', 'jobs', 'Job Register', 'Job activity for a period and current balance.', 3),
    E('phase_list', 'jobs', 'Phase List', 'Job phases and their status.', 3),
    E('time_tickets', 'time', 'Time Ticket Register', 'Time tickets by employee, customer and job.', 3),
    E('expense_tickets', 'time', 'Expense Ticket Register', 'Expense tickets to bill to customers.', 3),
    # ---------------------------------------------------------------- Bank / reconciliation
    E('account_register', 'bank', 'Account Register', 'One bank or cash account: every movement with running balance.', 1, 'cash_book'),
    E('bank_deposits', 'bank', 'Bank Deposit Report', 'What went into each bank: deposit rows with slip numbers.', 1, 'deposit_report', {'direction': 'in'}),
    E('deposits_transit', 'bank', 'Deposits in Transit', 'Cheques received and not yet deposited / cleared.', 1, 'cheque_register', {'cheque_state': 'open', 'direction': 'in'}),
    E('outstanding_cheques', 'bank', 'Outstanding Cheques', 'Cheques issued and not yet paid by the bank.', 1, 'cheque_register', {'cheque_state': 'open', 'direction': 'out'}),
    E('cheques_on_hand', 'bank', 'Cheques on Hand / Post-dated', 'Received cheques held, with cheque date.', 1, 'cheque_register', {'cheque_state': 'open', 'direction': 'in', 'post_dated': True}),
    E('bank_rec', 'bank', 'Account Reconciliation', 'Statement balance, deposits in transit and outstanding cheques against the books.', 1, 'bank_rec'),
    E('other_outstanding', 'bank', 'Other Outstanding Items', 'Uncleared items other than cheques.', 2),
    # ---------------------------------------------------------------- Fixed assets
    E('asset_register', 'assets', 'Asset Register', 'Assets with cost, accumulated depreciation and carrying amount.', 2),
    E('depreciation', 'assets', 'Depreciation Schedule', 'Depreciation per asset and period.', 2),
    E('ppe_movement', 'assets', 'PPE Movement (IAS 16)', 'Opening, additions, disposals, depreciation, closing.', 2),
    E('asset_book_tax', 'assets', 'Fixed Assets: Books vs Tax', 'Carrying amount against tax written-down value per asset, and the deferred tax (IAS 12).', 2),
    E('asset_control', 'assets', 'Fixed Assets Control', 'Each class\'s ledger accounts against the asset register, and ledger lines no asset accounts for.', 2),
    E('deferred_rev', 'assets', 'Deferred Revenue Schedule (IFRS 15)', 'Contract liabilities and recognition schedule.', 2),
    # ---------------------------------------------------------------- Company
    E('audit_trail', 'company', 'Audit Trail', 'Who created, posted, edited and voided which voucher, when.', 1, 'audit_trail'),
    E('daily_register', 'company', 'Daily Register', 'Everything posted, day by day and journal by journal.', 1, 'daily_register'),
    E('acc_review', 'company', 'Internal Accounting Review', 'Checklist before closing a period: unposted vouchers, unapplied money, missing TINs, suspense balances ...', 1, 'accounting_review'),
    E('find_tx', 'company', 'Find Transactions', 'Search all vouchers by number, reference, partner, amount or date.', 1, 'find_vouchers', {'mode': 'fy'}),
    E('voided', 'company', 'Voided Vouchers Register', 'Every voided voucher with its reversing entry.', 1, 'find_vouchers', {'state': 'void'}),
    E('period_log', 'company', 'Period Close Log', 'When each period was closed and reopened, and by whom.', 2),
    # ---------------------------------------------------------------- Groups
]

CATALOG_BY_KEY = {e['key']: e for e in CATALOG}


class SbReportCatalog(models.AbstractModel):
    _inherit = 'sb.report'

    # ================================================================== catalog for the Report Center
    @api.model
    def get_catalog(self):
        customs = self.env['sb.report.view'].search([
            '|', ('user_id', '=', self.env.uid), ('shared', '=', True), ('company_id', '=', self.env.company.id)])
        custom_rows = []
        for v in customs:
            base = CATALOG_BY_KEY.get(v.catalog_key or '') or next((e for e in CATALOG if e['report'] == v.report), None)
            custom_rows.append({'key': 'view_%d' % v.id, 'view_id': v.id, 'category': base['category'] if base else 'gl',
                                'name': v.name, 'description': 'Custom copy of %s' % (base['name'] if base else v.report),
                                'priority': 1, 'report': v.report, 'options': (v.options or {}).get('options', {}),
                                'ui': (v.options or {}).get('ui', {}), 'custom': True, 'mine': v.user_id.id == self.env.uid,
                                'form': base['form'] if base else False})
        packs = []
        if 'sb.report.pack' in self.env:
            for p in self.env['sb.report.pack'].search([('company_id', '=', self.env.company.id)]):
                names = ', '.join(CATALOG_BY_KEY[l.report_key]['name'] for l in p.line_ids if l.report_key in CATALOG_BY_KEY)
                packs.append({'key': 'pack_%d' % p.id, 'pack_id': p.id, 'category': 'groups', 'name': p.name,
                              'description': p.description or names, 'priority': 1, 'report': 'pack', 'options': {}, 'form': False})
        if self.env.company.country_id.code == 'UG':
            names = {'emp_income_tax': 'PAYE Return (URA)', 'poessa': 'NSSF Contributions Schedule', 'vat_return': 'VAT Return (URA)',
                     'wht_return': 'Withholding Tax Return (URA)'}
            hidden = {'tot_return'}
        else:
            names, hidden = {}, {'lst_return'}
        known = {e['key'] for e in CATALOG}
        entries = CATALOG + [e for e in self._extra_catalog() if e['key'] not in known]
        entries = [dict(e, priority=1) if e['priority'] > 1 and hasattr(self, '_report_%s' % e['report']) else e for e in entries]
        entries = [dict(e, name=names.get(e['key'], e['name'])) for e in entries
                   if e['key'] not in hidden and self._report_allowed(e['report'], e.get('options'))]
        cats = list(CATEGORIES)
        for key, name, title, after in self._extra_categories():
            if key not in {c[0] for c in cats}:
                i = next((n + 1 for n, c in enumerate(cats) if c[0] == after), len(cats))
                cats.insert(i, (key, name, title))
        return {'categories': [{'key': k, 'name': n, 'title': t} for k, n, t in cats],
                'reports': custom_rows + entries + packs}

    def _extra_categories(self):
        """Report Center sections added by other apps: (key, name, title, insert after key)."""
        return []

    def _extra_catalog(self):
        """Report Center entries added by other apps (inventory, manufacturing, ...)."""
        return []

    # ================================================================== Journals (CRJ, CDJ, SJ, PJ, GENJ)
    def _report_journal(self, opts):
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        dom = self._base_domain(opts, df, dt)
        regs = opts.get('registers') or []
        outside = transfers = 0
        if regs:
            # money in or out recorded in a journal of no register (bank journals brought over from another system)
            # belongs in the Cash Receipts / Cash Disbursements Journal by its direction, as Peachtree lists it;
            # a transfer between two banks nets to nothing and stays out
            extra = []
            if {'receipts', 'disbursements'} & set(regs):
                self.env.cr.execute("""
                    SELECT l.move_id, SUM(l.balance) FROM account_move_line l
                      JOIN account_account a ON a.id = l.account_id JOIN account_journal j ON j.id = l.journal_id
                     WHERE l.company_id = %s AND l.parent_state = 'posted' AND l.date BETWEEN %s AND %s
                       AND a.account_type = 'asset_cash' AND COALESCE(j.sb_register, 'other') = 'other'
                     GROUP BY l.move_id HAVING ABS(SUM(l.balance)) > 0.005""", (self.env.company.id, df, dt))
                for mid, net in self.env.cr.fetchall():
                    if (net > 0 and 'receipts' in regs) or (net < 0 and 'disbursements' in regs):
                        extra.append(mid)
            dom += ['|', ('journal_id.sb_register', 'in', regs), ('move_id', 'in', extra)]
            # what no register lists: entries of journals of no register that move no cash, and transfers between banks
            # (cash lines netting to nothing)
            self.env.cr.execute("""
                SELECT COUNT(*) FILTER (WHERE NOT has_cash), COUNT(*) FILTER (WHERE has_cash) FROM (
                    SELECT l.move_id, BOOL_OR(a.account_type = 'asset_cash') AS has_cash,
                           SUM(CASE WHEN a.account_type = 'asset_cash' THEN l.balance ELSE 0 END) AS cash
                      FROM account_move_line l JOIN account_journal j ON j.id = l.journal_id
                      JOIN account_account a ON a.id = l.account_id
                     WHERE l.company_id = %s AND l.parent_state = 'posted' AND l.date BETWEEN %s AND %s
                       AND COALESCE(j.sb_register, 'other') = 'other'
                     GROUP BY l.move_id) m
                 WHERE ABS(m.cash) <= 0.005""", (self.env.company.id, df, dt))
            outside, transfers = self.env.cr.fetchone()
        if opts.get('journal_codes'):
            dom.append(('journal_id.code', 'in', opts['journal_codes']))
        if opts.get('move_ids') is not None:
            dom.append(('move_id', 'in', opts['move_ids']))
        aml = self.env['account.move.line'].search(dom, order='date, move_name, id')
        vmap = self._voucher_map(aml.move_id)
        rows, grand = [], defaultdict(float)
        # plain lists keep the prefetch of the whole search (recordset unions lost it: one query per entry)
        by_move = defaultdict(list)
        order = []
        for l in aml:
            if l.move_id.id not in by_move:
                order.append(l.move_id)
            by_move[l.move_id.id].append(l)
        for move in order:
            lines = by_move[move.id]
            v = vmap.get(move.id)
            if not self._search_ok(opts, move.name, move.ref, move.partner_id.name, *(l.name for l in lines)):
                continue
            dr, cr = sum(l.debit for l in lines), sum(l.credit for l in lines)
            grand['debit'] += dr
            grand['credit'] += cr
            rid = 'm%d' % move.id
            rows.append({'id': rid, 'parent': None, 'level': 0, 'kind': 'group', 'label': move.name, 'expandable': True,
                         'values': {'date': fields.Date.to_string(move.date), 'date_ec': format_ethiopian(move.date),
                                    'number': move.name, 'partner': move.partner_id.name or '',
                                    'name': move.ref or (v.memo if v else '') or '', 'debit': dr, 'credit': cr},
                         'open': {'model': 'sb.voucher', 'id': v.id} if v else {'model': 'account.move', 'id': move.id}})
            for l in lines:
                rows.append({'id': 'l%d' % l.id, 'parent': rid, 'level': 1, 'kind': 'detail', 'label': l.account_id.code,
                             'values': {'number': '%s %s' % (l.account_id.code, l.account_id.name),
                                        'partner': l.partner_id.name or '', 'name': l.name or '',
                                        'debit': l.debit, 'credit': l.credit},
                             'drill': {'report': 'general_ledger', 'account_ids': [l.account_id.id],
                                       'date_from': opts['date_from'], 'date_to': opts['date_to']}})
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total',
                     'values': {'number': 'Total', **dict(grand)}})
        if outside:
            rows.append({'id': 'outside', 'parent': None, 'level': 0, 'kind': 'note',
                         'label': '%d entries of the period are in journals outside the five registers (stock, cost of '
                                  'sales ...). All Journals lists them; set a journal\'s register on '
                                  'the journal if it belongs here.' % outside, 'values': {}})
        if transfers:
            rows.append({'id': 'transfers', 'parent': None, 'level': 0, 'kind': 'note',
                         'label': 'Transfers between banks: %d entries of the period move money from one bank or cash '
                                  'account to another, neither in nor out. All Journals lists them.' % transfers, 'values': {}})
        cols = [
            {'key': 'date', 'label': 'Date', 'type': 'date'},
            {'key': 'date_ec', 'label': 'E.C. Date', 'type': 'text'},
            {'key': 'number', 'label': 'Reference / Account', 'type': 'text'},
            {'key': 'partner', 'label': 'Customer / Vendor', 'type': 'text'},
            {'key': 'name', 'label': 'Description', 'type': 'text'},
            {'key': 'debit', 'label': 'Debit', 'type': 'monetary'},
            {'key': 'credit', 'label': 'Credit', 'type': 'monetary'},
        ]
        title = opts.get('_title') or 'Journal'
        return self._doc('journal', opts, cols, rows, '%s  |  %s' % (self._period_name(df, dt), self._label_range_ec(df, dt)), title)

    # ================================================================== Sales documents as the VAT return counts them
    def _sale_doc_bases(self, opts, df, dt):
        """{entry id: {'base', 'taxed', 'tax'}} for the sales documents' entries dated in the period (sales positive, returns
        and voids negative): invoices, cash sales and credit notes (vouchers, Odoo's own, and the documents brought over),
        and the reversal of a voided one in the month of the void. What counts is what the document sold, whatever account
        it went to: every sales-account line, and on the sales side (credits of a sale, debits of a return) any other line
        that is not the money, withholding, the customer's balance (trade debtors, advances) or the VAT. The VAT return and the sales
        registers both read it, so they tie."""
        company = self.env.company
        docs = self.env['sb.voucher'].search([('company_id', '=', company.id), ('state', 'in', ('posted', 'void')),
                                              ('voucher_type', 'in', ('sale', 'cash_sale', 'sale_return')),
                                              ('date', '<=', dt), ('move_id', '!=', False)])
        doc_moves = set(docs.move_id.ids)
        money = set(self._sale_money_accounts().ids)
        dom = self._base_domain(dict(opts, exclude_closing=True, partner_ids=[], account_ids=[], journal_ids=[]), df, dt) + [
            '|', '|', ('move_id.move_type', 'in', ('out_invoice', 'out_refund', 'out_receipt')),
            ('move_id', 'in', list(doc_moves)), ('move_id.reversed_entry_id', 'in', list(doc_moves))]
        rows = self.env['account.move.line'].search_read(dom, ['move_id', 'account_id', 'balance', 'tax_line_id', 'tax_ids',
                                                               'display_type', 'account_type'])
        mtypes = {m['id']: m['move_type'] for m in self.env['account.move'].search_read(
            [('id', 'in', list({r['move_id'][0] for r in rows}))], ['move_type'])}
        partner_side = set(self.env['account.account'].search([('sb_partner_side', '!=', False)]).ids)
        out = defaultdict(lambda: {'base': 0.0, 'taxed': 0.0, 'tax': 0.0})
        for r in rows:
            mid = r['move_id'][0]
            mtype = mtypes[mid]
            # the sales side: credits of an invoice / cash sale, debits of a credit note and of a void's reversal
            credit_side = mtype in ('out_invoice', 'out_receipt') or (mtype == 'entry' and mid in doc_moves)
            bal = r['balance']
            if r['tax_line_id']:
                out[mid]['tax'] -= bal
                continue
            if r['display_type'] == 'payment_term' or r['account_id'][0] in money:
                continue
            if r['account_type'] != 'income':
                # the customer's balance (trade debtors, advances) is what the document is paid with, not what it sold
                if r['account_id'][0] in partner_side or r['account_type'] in ('asset_receivable', 'liability_payable'):
                    continue
                if not (bal < 0 if credit_side else bal > 0):
                    continue
            out[mid]['base'] -= bal
            if r['tax_ids']:
                out[mid]['taxed'] -= bal
        return out

    def _sale_money_accounts(self):
        """Money and withholding on a sales document: bank, cash and cheque accounts, the withholding and bank charges
        accounts."""
        c = self.env.company
        return self.env['account.account'].search([('account_type', 'in', ('asset_cash', 'liability_credit_card'))]) | \
            c.sb_cheque_on_hand_account_id | c.sb_cheque_outstanding_account_id | c.sb_wht_receivable_account_id | \
            c.sb_wht_payable_account_id | c.sb_bank_charges_account_id

    # ================================================================== Document registers (invoices, cash sales)
    def _report_document_register(self, opts):
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        register = opts.get('register') or 'sale'
        vtypes = {'sale': ['sale', 'sale_return'], 'cash_sale': ['cash_sale'], 'bill': ['bill', 'purchase_return'],
                  'cash_purchase': ['cash_purchase']}[register]
        states = ['posted'] if opts.get('posted_only', True) else ['posted', 'draft']
        vouchers = self.env['sb.voucher'].search([('voucher_type', 'in', vtypes), ('date', '>=', df), ('date', '<=', dt),
                                                  ('state', 'in', states + ['void']), ('company_id', '=', self.env.company.id)],
                                                 order='date, name, id')
        rows, grand = [], defaultdict(float)
        cash = register in ('cash_sale', 'cash_purchase')
        # sales registers follow the VAT return (_sale_doc_bases): each document at what it sold in the ledger (the
        # documents brought over from the old books too), a voided one in its own month and its reversal, negative, in
        # the month of the void; Odoo's own invoices and credit notes are listed with the invoices
        sales = register in ('sale', 'cash_sale')
        bases = self._sale_doc_bases(dict(opts, posted_only=True), df, dt) if sales else {}
        Move = self.env['account.move']
        entries = []  # (date, name, voucher, entry, reversal of a void)
        for v in vouchers:
            entries.append((v.date, v.name or '', v, v.move_id if v.state != 'draft' else Move, False))
        natives = Move
        if sales:
            company = self.env.company
            voided = self.env['sb.voucher'].search([('voucher_type', 'in', vtypes), ('state', '=', 'void'), ('date', '<=', dt),
                                                    ('company_id', '=', company.id), ('move_id', '!=', False)])
            by_move = {v.move_id.id: v for v in voided}
            for rev in Move.search([('reversed_entry_id', 'in', list(by_move)), ('state', '=', 'posted'),
                                    ('date', '>=', df), ('date', '<=', dt)]):
                entries.append((rev.date, rev.name or '', by_move[rev.reversed_entry_id.id], rev, True))
            if register == 'sale':
                found = Move.search([('company_id', '=', company.id), ('state', '=', 'posted'), ('date', '>=', df),
                                     ('date', '<=', dt), ('move_type', 'in', ('out_invoice', 'out_refund', 'out_receipt'))])
                natives = found - Move.union(*found._sb_documents().keys())
                for m in natives:
                    entries.append((m.date, m.name or '', self.env['sb.voucher'], m, False))
        entries.sort(key=lambda e: (e[0], e[1]))
        status_names = dict(self.env['sb.voucher']._fields['payment_state']._description_selection(self.env))
        for day, _name, v, move, reversal in entries:
            if v:
                if not self._search_ok(opts, v.name, v.partner_id.name, v.reference, v.memo):
                    continue
                sign = -1 if v.voucher_type.endswith('return') else 1
                void = v.state == 'void'
                partner, number, ref = v.partner_id, v.name, v.reference or ''
            else:
                if not self._search_ok(opts, move.name, move.partner_id.name, move.ref):
                    continue
                sign = -1 if move.move_type == 'out_refund' else 1
                void = False
                partner, number, ref = move.commercial_partner_id, move.name, move.ref or ''
            if sales and move:
                b = bases.get(move.id) or {'base': 0.0, 'tax': 0.0}
                untaxed, tax = b['base'], b['tax']
            else:
                untaxed, tax = sign * v.amount_untaxed, sign * v.amount_tax
                if void and not sales:
                    untaxed = tax = 0.0
            if reversal:
                status = 'Void of %s' % v.name
                number = move.name
            elif void:
                status = 'Void'
            elif v and v.state == 'draft':
                status = 'Not posted'
            elif cash:
                status = 'Paid'
            else:
                status = status_names.get(v.payment_state if v else move.payment_state, '')
            vals = {'date': fields.Date.to_string(day), 'date_ec': format_ethiopian(day), 'number': number,
                    'partner': partner.name or '', 'vat': partner.vat or '', 'ref': ref,
                    'untaxed': untaxed, 'tax': tax, 'total': untaxed + tax, 'status': status}
            if not cash:
                settled = not (void or reversal)
                if v:
                    vals['paid'] = sign * (v.amount_total - v.amount_residual) if settled else 0.0
                    vals['due'] = sign * v.amount_residual if settled else 0.0
                else:
                    vals['paid'] = sign * (abs(move.amount_total_signed) - abs(move.amount_residual_signed))
                    vals['due'] = sign * abs(move.amount_residual_signed)
            elif v and not reversal:
                vals['banked'] = ', '.join('%s %s' % (r.account_id.name, '{:,.2f}'.format(r.amount)) for r in v.cash_line_ids)
            for k in ('untaxed', 'tax', 'total', 'paid', 'due'):
                if k in vals:
                    grand[k] += vals[k]
            rid = ('x%d' % move.id) if reversal or not v else ('v%d' % v.id)
            expand = cash and bool(v) and not reversal and bool(v.cash_line_ids)
            rows.append({'id': rid, 'parent': None, 'level': 0, 'kind': 'line', 'label': number, 'values': vals,
                         'open': {'model': 'sb.voucher', 'id': v.id} if v else {'model': 'account.move', 'id': move.id},
                         'expandable': expand})
            if expand:
                for r in v.cash_line_ids:
                    rows.append({'id': 'r%d' % r.id, 'parent': rid, 'level': 1, 'kind': 'detail', 'label': r.account_id.name,
                                 'values': {'partner': r.account_id.name, 'ref': r._label(), 'total': r.amount}})
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total',
                     'values': {'number': 'Total', **dict(grand)}})
        cols = [
            {'key': 'date', 'label': 'Date', 'type': 'date'},
            {'key': 'number', 'label': 'Number', 'type': 'text'},
            {'key': 'partner', 'label': 'Customer' if register in ('sale', 'cash_sale') else 'Vendor', 'type': 'text'},
            {'key': 'vat', 'label': 'TIN', 'type': 'text'},
            {'key': 'ref', 'label': 'Reference', 'type': 'text'},
            {'key': 'untaxed', 'label': 'Before tax', 'type': 'monetary'},
            {'key': 'tax', 'label': 'VAT / tax', 'type': 'monetary'},
            {'key': 'total', 'label': 'Total', 'type': 'monetary'},
        ]
        if cash:
            cols.insert(8, {'key': 'banked', 'label': 'Banked in', 'type': 'text'})
        else:
            cols += [{'key': 'paid', 'label': 'Paid', 'type': 'monetary'}, {'key': 'due', 'label': 'Balance due', 'type': 'monetary'}]
        cols.append({'key': 'status', 'label': 'Status', 'type': 'text'})
        title = {'sale': 'Invoice Register', 'cash_sale': 'Cash Sales Register', 'bill': 'Bill Register',
                 'cash_purchase': 'Cash Purchases Register'}[register]
        return self._doc('document_register', opts, cols, rows, '%s  |  %s' % (self._period_name(df, dt), self._label_range_ec(df, dt)), title)

    # ================================================================== Sales / purchases per partner
    def _report_partner_sales(self, opts):
        """Sales per customer built from revenue, purchases per vendor from what was bought, so the total is the ledger's:
        every entry's revenue lines (credits less debits) go to the entry's customer, whatever document or plain entry
        carried them; moving a balance between partner accounts (van sales suspense to the customer) is not a sale."""
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        kind = opts.get('partner_type') or 'customer'
        customer = kind == 'customer'
        company = self.env.company
        AML = self.env['account.move.line']
        # the period's entries without the partner filter: a revenue line rarely carries the customer itself
        dom = self._base_domain(dict(opts, partner_ids=[], exclude_closing=True), df, dt)
        accounts = self._accounts()
        side = accounts.filtered(lambda a: a.sb_partner_side == kind) | (
            company.sb_customer_advance_account_id if customer else company.sb_vendor_advance_account_id)
        if customer:
            # the income statement's revenue: accounts on a revenue line item (income-type ones not yet mapped)
            main = accounts.filtered(lambda a: a.sb_ifrs_line_id.section == 'revenue'
                                     if a.sb_ifrs_line_id else a.account_type == 'income')
            moves = set(AML.search(dom + [('account_id', 'in', main.ids)]).move_id.ids)
        else:
            # what was bought: expense, inventory and other asset accounts on entries with a vendor (tax, cash and
            # partner accounts are not purchases)
            tax_accounts = self.env['account.tax.repartition.line'].search(
                [('company_id', '=', company.id), ('account_id', '!=', False)]).account_id
            main = accounts.filtered(lambda a: a.account_type in (
                'expense', 'expense_direct_cost', 'asset_current', 'asset_prepayments', 'asset_fixed', 'asset_non_current')
                and not a.sb_partner_side) - side - tax_accounts
            moves = set(AML.search(dom + [('account_id', 'in', side.ids)]).move_id.ids)
            moves |= set(self.env['sb.voucher'].search([
                ('voucher_type', 'in', ('bill', 'cash_purchase', 'purchase_return')), ('state', '=', 'posted'),
                ('date', '>=', df), ('date', '<=', dt), ('company_id', '=', company.id)]).move_id.ids)
            moves -= self._opening_move_ids()  # balances brought over from the old books are not purchases
        returns_doc = set(self.env['sb.voucher'].search([
            ('move_id', 'in', list(moves)), ('voucher_type', '=', 'sale_return' if customer else 'purchase_return')]).move_id.ids)
        returns_doc |= set(self.env['account.move'].search([
            ('id', 'in', list(moves)), ('move_type', '=', 'out_refund' if customer else 'in_refund')]).ids)
        tax_use = 'sale' if customer else 'purchase'
        main_ids, side_ids = set(main.ids), set(side.ids)
        entry = defaultdict(lambda: {'lines': [], 'tax_lines': [], 'weights': defaultdict(float),
                                     'cash_partners': defaultdict(float), 'cash': False})
        for l in AML.search_read(dom + [('move_id', 'in', list(moves))],
                                 ['move_id', 'partner_id', 'account_id', 'balance', 'tax_line_id', 'account_type']):
            e = entry[l['move_id'][0]]
            pid = l['partner_id'][0] if l['partner_id'] else False
            acc = l['account_id'][0]
            if l['tax_line_id']:
                e['tax_lines'].append((l['tax_line_id'][0], l['balance']))
            elif acc in main_ids:
                e['lines'].append((pid, l['balance']))
            elif acc in side_ids and pid:
                e['weights'][pid] += abs(l['balance'])
            elif l['account_type'] == 'asset_cash':
                e['cash'] = True
                if pid:
                    e['cash_partners'][pid] += abs(l['balance'])
        taxes = self.env['account.tax'].browse({t for e in entry.values() for t, _b in e['tax_lines']})
        tax_ok = set(taxes.filtered(lambda t: t.type_tax_use == tax_use and t.amount >= 0).ids)  # VAT, not withholding
        sign = -1 if customer else 1  # sales are credits, purchases debits
        agg = defaultdict(lambda: defaultdict(float))
        counted = defaultdict(set)
        move_partner = {m.id: m.partner_id.id for m in self.env['account.move'].browse(list(entry))}
        commercial = {}
        for move_id, e in entry.items():
            if not e['lines']:
                continue
            weights = e['weights'] or e['cash_partners'] or ({move_partner[move_id]: 1.0} if move_partner.get(move_id) else {})
            wsum = sum(weights.values())
            net = sign * sum(bal for _p, bal in e['lines'])
            tax = sign * sum(b for t, b in e['tax_lines'] if t in tax_ok)
            is_return = move_id in returns_doc  # a credit note
            for pid, bal in e['lines']:
                amt = sign * bal
                split = [(pid, 1.0)] if pid else [(wp, w / wsum) for wp, w in weights.items()] if wsum else [(0, 1.0)]
                for sp, share in split:
                    if sp not in commercial:
                        commercial[sp] = self.env['res.partner'].browse(sp).commercial_partner_id.id if sp else 0
                    a = agg[commercial[sp]]
                    counted[commercial[sp]].add(move_id)
                    part = amt * share
                    if is_return or (customer and part < 0):
                        a['returns'] -= part  # a debit on a sales account is a return or an allowance
                    else:
                        a['cash' if e['cash'] else 'credit'] += part
                    a['untaxed'] += part
                    ptax = tax * part / net if abs(net) > 0.005 else 0.0
                    a['tax'] += ptax
                    a['total'] += part + ptax
        names = {pid: name for pid, name in self.env['res.partner'].browse([p for p in agg if p]).mapped(
            lambda p: (p.id, p.name))}
        names[0] = 'Walk-in / no customer' if customer else 'No vendor'
        wanted = set(opts.get('partner_ids') or [])
        rows, grand = [], defaultdict(float)
        for pid in sorted(agg, key=lambda p: -agg[p]['untaxed']):
            if (wanted and pid not in wanted) or not self._search_ok(opts, names[pid]):
                continue
            vals = dict(agg[pid], count=len(counted[pid]))
            if all(abs(v) < 0.005 for k, v in vals.items() if k != 'count'):
                continue
            for k, val in vals.items():
                grand[k] += val
            rows.append({'id': 'p%d' % pid, 'parent': None, 'level': 0, 'kind': 'line', 'label': names[pid],
                         'values': {'partner': names[pid], **vals},
                         'drill': {'report': 'partner_ledger', 'partner_ids': [pid] if pid else [], 'partner_type': kind}})
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total',
                     'values': {'partner': 'Total', **dict(grand)}})
        if customer:
            # proof: the same figure as revenue on the income statement; a filter or a search leaves a visible difference
            ledger = -sum(v['balance'] for v in self._sums(dom + [('account_id', 'in', main.ids)], ()).values())
            rows.append({'id': 'ledger', 'parent': None, 'level': 0, 'kind': 'note', 'label': 'Total revenue per ledger',
                         'values': {'partner': 'Total revenue per ledger', 'untaxed': ledger}})
            if abs(ledger - grand['untaxed']) > 0.005:
                rows.append({'id': 'difference', 'parent': None, 'level': 0, 'kind': 'note', 'warning': True,
                             'label': 'Difference (customers left out by the filter)',
                             'values': {'partner': 'Difference (customers left out by the filter)',
                                        'untaxed': ledger - grand['untaxed']}})
        cols = [{'key': 'partner', 'label': 'Customer' if customer else 'Vendor', 'type': 'text'},
                {'key': 'count', 'label': 'Entries', 'type': 'number_int'},
                {'key': 'cash', 'label': 'Cash', 'type': 'monetary'},
                {'key': 'credit', 'label': 'On account', 'type': 'monetary'},
                {'key': 'returns', 'label': 'Returns', 'type': 'monetary'},
                {'key': 'untaxed', 'label': 'Net before tax', 'type': 'monetary'},
                {'key': 'tax', 'label': 'VAT', 'type': 'monetary'},
                {'key': 'total', 'label': 'Total', 'type': 'monetary'}]
        title = 'Customer Sales History' if customer else 'Vendor Purchase History'
        return self._doc('partner_sales', opts, cols, rows, '%s  |  %s' % (self._period_name(df, dt), self._label_range_ec(df, dt)), title)

    # ================================================================== Partner master lists
    def _report_partner_list(self, opts):
        kind = opts.get('partner_type') or 'customer'
        field = 'customer_rank' if kind == 'customer' else 'supplier_rank'
        partners = self.env['res.partner'].search([(field, '>', 0), ('parent_id', '=', False)], order='name')
        bal = self._sums(self._base_domain(opts, None, opts['date_to']) + self._partner_acc_dom(kind), ('partner_id',))
        sign = 1 if kind == 'customer' else -1
        rows = []
        for p in partners:
            if not self._search_ok(opts, p.name, p.vat, p.phone, p.email):
                continue
            rows.append({'id': 'p%d' % p.id, 'parent': None, 'level': 0, 'kind': 'line', 'label': p.name,
                         'values': {'name': p.name, 'vat': p.vat or '', 'phone': p.phone or '',
                                    'email': p.email or '', 'city': ', '.join(filter(None, [p.street, p.city])),
                                    'balance': sign * bal.get(p.id, {}).get('balance', 0.0)},
                         'open': {'model': 'res.partner', 'id': p.id}})
        cols = [{'key': 'name', 'label': 'Name', 'type': 'text'}, {'key': 'vat', 'label': 'TIN', 'type': 'text'},
                {'key': 'phone', 'label': 'Phone', 'type': 'text'}, {'key': 'email', 'label': 'Email', 'type': 'text'},
                {'key': 'city', 'label': 'Address', 'type': 'text'}, {'key': 'balance', 'label': 'Balance', 'type': 'monetary'}]
        return self._doc('partner_list', opts, cols, rows, '%d %s' % (len(rows), 'customers' if kind == 'customer' else 'vendors'),
                         'Customer List' if kind == 'customer' else 'Vendor List')

    # ================================================================== Advances / credits not applied
    def _report_open_credits(self, opts):
        """Money received (paid) and not yet applied, as at the date: on the same customer (vendor) accounts as the aged
        report (sb_partner_side, advances included), with the matches that count on that date, so its total is the aged
        report's "Advances" column. Staff advances and other receivable-type accounts kept per person are not customers'."""
        kind = opts.get('partner_type') or 'customer'
        dt = self._d(opts['date_to'])
        sign = 1 if kind == 'customer' else -1
        dom = self._base_domain(opts, None, dt) + self._partner_acc_dom(kind)
        lines = self.env['account.move.line'].search(dom, order='partner_id, date')
        settled = self._settled_as_at(lines, dt)[0]
        vmap = self._voucher_map(lines.move_id)
        rows, grand = [], 0.0
        for l in lines:
            open_amt = l.balance - settled.get(l.id, 0.0)
            if abs(open_amt) < 0.005 or sign * open_amt > 0:
                continue
            if not self._search_ok(opts, l.partner_id.name, l.move_id.name, l.name):
                continue
            amt = abs(open_amt)
            grand += amt
            v = vmap.get(l.move_id.id)
            rows.append({'id': 'l%d' % l.id, 'parent': None, 'level': 0, 'kind': 'line', 'label': l.move_id.name,
                         'values': {'date': fields.Date.to_string(l.date), 'number': l.move_id.name,
                                    'partner': l.partner_id.name or '', 'name': l.name or '', 'account': l.account_id.name,
                                    'original': abs(l.balance), 'open': amt, 'days': (dt - l.date).days},
                         'open': {'model': 'sb.voucher', 'id': v.id} if v else {'model': 'account.move', 'id': l.move_id.id}})
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total', 'values': {'number': 'Total', 'open': grand}})
        cols = [{'key': 'date', 'label': 'Date', 'type': 'date'}, {'key': 'number', 'label': 'Document', 'type': 'text'},
                {'key': 'partner', 'label': 'Customer' if kind == 'customer' else 'Vendor', 'type': 'text'},
                {'key': 'name', 'label': 'Description', 'type': 'text'}, {'key': 'account', 'label': 'Account', 'type': 'text'},
                {'key': 'original', 'label': 'Original', 'type': 'monetary'}, {'key': 'open', 'label': 'Not yet applied', 'type': 'monetary'},
                {'key': 'days', 'label': 'Days', 'type': 'number_int'}]
        dt = self._d(opts['date_to'])
        title = 'Customer Advances & Unapplied Receipts' if kind == 'customer' else 'Vendor Advances'
        return self._doc('open_credits', opts, cols, rows, 'As at %s' % dt.strftime('%d %B %Y'), title)

    # ================================================================== Withholding registers
    def _report_wht_register(self, opts):
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        side = opts.get('side') or 'payable'
        company = self.env.company
        cur = company.currency_id
        account = company.sb_wht_payable_account_id if side == 'payable' else company.sb_wht_receivable_account_id
        lines = self.env['account.move.line'].search(self._base_domain(opts, df, dt) + [('account_id', '=', account.id)],
                                                     order='date, id')
        # tax withheld only: the payments to (refunds from) the tax authority settle the account and are not declared.
        # A line is withholding when its entry, or the entry it voids, withholds (credit payable / debit receivable).
        taken = 'credit' if side == 'payable' else 'debit'
        # a cancelled / bounced cheque takes its payment's withholding back: the receipt shows void, like a voided voucher
        bounced = {r.settle_voucher_id.move_id: r.voucher_id.move_id for r in self.env['sb.voucher.cash'].search([
            ('cheque_state', '=', 'bounced'), ('settle_voucher_id.move_id', 'in', lines.move_id.ids)])}
        voids = lambda m: m.reversed_entry_id or bounced.get(m, m.browse())  # noqa: E731
        lines = lines.filtered(lambda l: any(
            o[taken] for o in (voids(l.move_id) or l.move_id).line_ids if o.account_id == account))
        vmap = self._voucher_map(lines.move_id | lines.move_id.reversed_entry_id | lines.move_id.browse([m.id for m in bounced.values()]))
        vat_rate = company.account_purchase_tax_id.amount if company.account_purchase_tax_id.amount_type == 'percent' else 0.0
        vat_factor = 1.0 + max(vat_rate, 15.0) / 100.0

        def derived(move, tax, partner):
            """Withholding brought over from the previous system has the tax line only: the base is the tax divided by the
            rate. The rate is the one in force for the supplier (a licensed company with a TIN, or not), unless the other
            one is what fits the payment: the amount withheld on lies between the payment before VAT and with VAT."""
            paid = sum(o['debit' if side == 'payable' else 'credit'] for o in move.line_ids if o.account_id != account)
            licensed = side == 'received' or (partner.is_company and partner.vat and partner.sb_trade_license)
            rates = [r for r in ((company.sb_wht_rate, company.sb_wht_individual_rate) if licensed
                                 else (company.sb_wht_individual_rate, company.sb_wht_rate)) if r]
            for rate in rates:
                base = cur.round(tax * 100.0 / rate)
                if paid and paid / vat_factor - 1.0 <= base <= paid + 1.0:
                    return base, rate
            return (cur.round(tax * 100.0 / rates[0]), rates[0]) if rates else (0.0, 0.0)

        rows, grand, no_tin, n_derived = [], defaultdict(float), 0, 0
        for l in lines:
            voided = bool(voids(l.move_id))
            entry = voids(l.move_id) or l.move_id
            v = vmap.get(entry.id) if voided else vmap.get(l.move_id.id)
            partner = (v.partner_id if v else l.partner_id).commercial_partner_id
            cert = (v.wht_certificate_no if v else '') or entry.sb_wht_receipt_no or ''
            if not self._search_ok(opts, partner.name, partner.vat, l.move_id.name, cert):
                continue
            amount = l.credit - l.debit if side == 'payable' else l.debit - l.credit
            sign = -1 if voided else 1
            note = 'Void: the cheque was cancelled' if l.move_id in bounced else ''
            brought_over = not (v and v.wht_base)
            if not brought_over:
                # a cheque that was part of the payment takes back its share of the withholding
                base = sign * cur.round(v.wht_base * (abs(amount) / v.wht_amount if voided and v.wht_amount else 1.0))
            elif entry.sb_wht_base:
                base, note = sign * entry.sb_wht_base, 'Base entered after posting'
            else:
                base, rate = derived(entry, abs(amount), partner)
                base *= sign
                note = 'Derived: tax / %g%%' % rate if rate else 'No base: enter it on the entry'
                n_derived += 1
            cert_date = (v.wht_certificate_date if v else False) or entry.sb_wht_receipt_date
            grand['base'] += base
            grand['amount'] += amount
            no_tin += not partner.vat
            rows.append({'id': 'l%d' % l.id, 'parent': None, 'level': 0, 'kind': 'line', 'label': partner.name or '',
                         'warning': not partner.vat or not cert,
                         'values': {'date': fields.Date.to_string(l.date), 'partner': partner.name or '', 'vat': partner.vat or '',
                                    'number': l.move_id.name, 'cert': cert,
                                    'cert_date': fields.Date.to_string(cert_date) if cert_date else '',
                                    'base': base, 'rate': (amount / base * 100.0) if base else None, 'amount': amount, 'note': note},
                         # brought over: the entry, where the base and the receipt number are filled in
                         'open': {'model': 'account.move', 'id': entry.id} if brought_over else {'model': 'sb.voucher', 'id': v.id}})
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total',
                     'values': {'partner': 'Total', **dict(grand)}})
        if n_derived:
            rows.append({'id': 'derived', 'parent': None, 'level': 0, 'kind': 'note', 'values': {},
                         'label': '%d lines were brought over without the amount withheld on: the taxable amount is derived from '
                                  'the tax and the rate (see Note). Open the entry to enter the base and the receipt number.' % n_derived})
        if no_tin:
            rows.append({'id': 'no_tin', 'parent': None, 'level': 0, 'kind': 'note', 'warning': True, 'values': {},
                         'label': '%d lines have no TIN: complete the contacts before filing.' % no_tin})
        if side == 'payable':
            # payments over the threshold withheld less than the rule asks, or not at all, with the reason given
            exempt = self.env['sb.voucher'].search([('company_id', '=', company.id), ('state', '=', 'posted'), ('wht_due', '>', 0),
                                                    ('date', '>=', df), ('date', '<=', dt)], order='date, id')
            if exempt:
                rows.append({'id': 'exc', 'parent': None, 'level': 0, 'kind': 'section', 'expandable': True,
                             'label': 'Not withheld, or withheld less (with a reason)',
                             'values': {'partner': 'Not withheld, or withheld less (with a reason)',
                                        'amount': sum(exempt.mapped(lambda x: x.wht_amount if x.withholding else 0.0))}})
                for x in exempt:
                    p = x.partner_id.commercial_partner_id
                    taken_amt = x.wht_amount if x.withholding else 0.0
                    rows.append({'id': 'x%d' % x.id, 'parent': 'exc', 'level': 1, 'kind': 'detail', 'label': p.name or '',
                                 'values': {'date': fields.Date.to_string(x.date), 'partner': p.name or '', 'vat': p.vat or '',
                                            'number': x.name, 'cert': x.wht_certificate_no or '',
                                            'base': x.wht_due * 100.0 / x.wht_rate if x.wht_rate else 0.0, 'rate': x.wht_rate,
                                            'amount': taken_amt,
                                            'note': 'Due %s: %s' % ('{:,.2f}'.format(x.wht_due), x.wht_exempt_reason or '')},
                                 'open': {'model': 'sb.voucher', 'id': x.id}})
        cols = [{'key': 'date', 'label': 'Date', 'type': 'date'},
                {'key': 'partner', 'label': 'Vendor' if side == 'payable' else 'Customer (withholding agent)', 'type': 'text'},
                {'key': 'vat', 'label': 'TIN', 'type': 'text'}, {'key': 'number', 'label': 'Voucher', 'type': 'text'},
                {'key': 'cert', 'label': 'Withholding receipt no.', 'type': 'text'}, {'key': 'cert_date', 'label': 'Receipt date', 'type': 'date'},
                {'key': 'base', 'label': 'Taxable amount', 'type': 'monetary'}, {'key': 'rate', 'label': 'Rate', 'type': 'percent'},
                {'key': 'amount', 'label': 'Tax withheld', 'type': 'monetary'}, {'key': 'note', 'label': 'Note', 'type': 'text'}]
        title = 'Withholding Payable Register' if side == 'payable' else 'Withholding Received'
        if opts.get('mode') == 'month':
            title = 'Withholding Tax Return (URA)' if self.env.company.country_id.code == 'UG' else 'Withholding Tax Declaration'
        return self._doc('wht_register', opts, cols, rows, '%s  |  %s' % (self._period_name(df, dt), self._label_range_ec(df, dt)), title)

    # ================================================================== Cash requirements
    def _report_cash_requirements(self, opts):
        dt = self._d(opts['date_to'])
        # what was owed as at the date (matching made later undone, as in the aged payables), so the total equals the
        # aged payables; money paid in advance or not yet applied shows as a negative line of its vendor
        lines = self.env['account.move.line'].search(self._base_domain(opts, None, dt) + self._partner_acc_dom('vendor'),
                                                     order='date_maturity, date')
        partials = self.env['account.partial.reconcile'].search([
            '|', ('debit_move_id', 'in', lines.ids), ('credit_move_id', 'in', lines.ids), ('max_date', '<=', dt)])
        settled = defaultdict(float)
        for pr in partials:
            settled[pr.debit_move_id.id] += pr.amount
            settled[pr.credit_move_id.id] -= pr.amount
        vmap = self._voucher_map(lines.move_id)
        rows, grand, running = [], 0.0, 0.0
        open_lines = []
        for l in lines:
            residual = (l.balance - settled.get(l.id, 0.0)) if l.account_id.reconcile else l.balance
            if abs(residual) > 0.005:
                open_lines.append((l, -residual))
        open_lines.sort(key=lambda x: (x[0].date_maturity or x[0].date, x[0].id))
        for l, amt in open_lines:
            if not self._search_ok(opts, l.partner_id.name, l.move_id.name, l.move_id.ref):
                continue
            running += amt
            grand += amt
            due = l.date_maturity or l.date
            v = vmap.get(l.move_id.id)
            rows.append({'id': 'l%d' % l.id, 'parent': None, 'level': 0, 'kind': 'line', 'label': l.move_id.name,
                         'values': {'due': fields.Date.to_string(due), 'due_ec': format_ethiopian(due), 'partner': l.partner_id.name or '',
                                    'number': l.move_id.name, 'ref': (l.move_id.ref or '') if amt > 0 else 'Paid in advance / not applied',
                                    'amount': amt, 'overdue': max((dt - due).days, 0) if amt > 0 else 0, 'cumulative': running},
                         'open': {'model': 'sb.voucher', 'id': v.id} if v else {'model': 'account.move', 'id': l.move_id.id}})
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total', 'values': {'due': 'Total', 'amount': grand}})
        cols = [{'key': 'due', 'label': 'Due date', 'type': 'date'}, {'key': 'due_ec', 'label': 'E.C.', 'type': 'text'},
                {'key': 'partner', 'label': 'Vendor', 'type': 'text'}, {'key': 'number', 'label': 'Bill', 'type': 'text'},
                {'key': 'ref', 'label': 'Their ref.', 'type': 'text'}, {'key': 'amount', 'label': 'Amount due', 'type': 'monetary'},
                {'key': 'overdue', 'label': 'Days overdue', 'type': 'number_int'},
                {'key': 'cumulative', 'label': 'Cumulative', 'type': 'monetary'}]
        return self._doc('cash_requirements', opts, cols, rows, 'Unpaid bills as at %s' % dt.strftime('%d %B %Y'), 'Cash Requirements')

    # ================================================================== Cheques and deposits
    def _report_cheque_register(self, opts):
        dom = [('method', '=', 'cheque'), ('cheque_state', '!=', 'none'), ('company_id', '=', self.env.company.id)]
        if opts.get('cheque_state'):
            dom.append(('cheque_state', '=', opts['cheque_state']))
        if opts.get('direction'):
            dom.append(('direction', '=', opts['direction']))
        if not opts.get('cheque_state'):
            dom += [('voucher_date', '>=', opts['date_from']), ('voucher_date', '<=', opts['date_to'])]
        rows_ = self.env['sb.voucher.cash'].search(dom, order='voucher_date, id')
        today = fields.Date.context_today(self)
        rows, grand = [], 0.0
        for r in rows_:
            if not self._search_ok(opts, r.partner_id.name, r.reference, r.cheque_bank, r.voucher_id.name):
                continue
            grand += r.amount
            state = dict(r._fields['cheque_state']._description_selection(self.env)).get(r.cheque_state)
            if r.cheque_state == 'open' and r.cheque_date and r.cheque_date > today:
                state = 'Post-dated'
            rows.append({'id': 'r%d' % r.id, 'parent': None, 'level': 0, 'kind': 'line', 'label': r.reference or '',
                         'values': {'date': fields.Date.to_string(r.voucher_date), 'direction': 'Received' if r.direction == 'in' else 'Issued',
                                    'number': r.reference or '', 'partner': r.partner_id.name or '', 'bank': r.cheque_bank or '',
                                    'cheque_date': fields.Date.to_string(r.cheque_date) if r.cheque_date else '',
                                    'account': r.account_id.name, 'amount': r.amount, 'state': state,
                                    'voucher': r.voucher_id.name, 'settled': r.settle_voucher_id.name or ''},
                         'open': {'model': 'sb.voucher', 'id': r.voucher_id.id}})
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total', 'values': {'date': 'Total', 'amount': grand}})
        cols = [{'key': 'date', 'label': 'Date', 'type': 'date'}, {'key': 'direction', 'label': 'In / Out', 'type': 'text'},
                {'key': 'number', 'label': 'Cheque no.', 'type': 'text'}, {'key': 'partner', 'label': 'From / To', 'type': 'text'},
                {'key': 'bank', 'label': 'Drawn on', 'type': 'text'}, {'key': 'cheque_date', 'label': 'Cheque date', 'type': 'date'},
                {'key': 'account', 'label': 'Bank account', 'type': 'text'}, {'key': 'amount', 'label': 'Amount', 'type': 'monetary'},
                {'key': 'state', 'label': 'Status', 'type': 'text'}, {'key': 'voucher', 'label': 'Voucher', 'type': 'text'},
                {'key': 'settled', 'label': 'Deposited / cleared by', 'type': 'text'}]
        title = opts.get('_title') or 'Cheque Register'
        sub = 'Open items' if opts.get('cheque_state') == 'open' else '%s  |  %s' % (
            self._period_name(self._d(opts['date_from']), self._d(opts['date_to'])),
            self._label_range_ec(opts['date_from'], opts['date_to']))
        return self._doc('cheque_register', opts, cols, rows, sub, title)

    def _report_deposit_report(self, opts):
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        rows_ = self.env['sb.voucher.cash'].search([
            ('company_id', '=', self.env.company.id), ('voucher_date', '>=', df), ('voucher_date', '<=', dt),
            ('voucher_state', '=', 'posted'), ('direction', '=', 'in')], order='account_id, voucher_date, id')
        by_acc = defaultdict(list)
        for r in rows_:
            by_acc[r.account_id].append(r)
        rows, grand = [], 0.0
        for account, items in sorted(by_acc.items(), key=lambda kv: kv[0].code or ''):
            items = [r for r in items if self._search_ok(opts, account.name, r.reference, r.partner_id.name, r.voucher_id.name)]
            if not items:
                continue
            tot = sum(r.amount for r in items)
            grand += tot
            gid = 'acc%d' % account.id
            rows.append({'id': gid, 'parent': None, 'level': 0, 'kind': 'group', 'label': '%s %s' % (account.code, account.name),
                         'values': {'date': '%s %s' % (account.code, account.name), 'amount': tot}, 'expandable': True})
            for r in items:
                rows.append({'id': 'r%d' % r.id, 'parent': gid, 'level': 1, 'kind': 'detail', 'label': r.voucher_id.name,
                             'values': {'date': fields.Date.to_string(r.voucher_date), 'voucher': r.voucher_id.name,
                                        'partner': r.partner_id.name or '',
                                        'method': dict(r._fields['method']._description_selection(self.env)).get(r.method),
                                        'ref': r.reference or '', 'charges': r.charges, 'amount': r.amount},
                             'open': {'model': 'sb.voucher', 'id': r.voucher_id.id}})
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total', 'values': {'date': 'Total', 'amount': grand}})
        cols = [{'key': 'date', 'label': 'Date', 'type': 'date'}, {'key': 'voucher', 'label': 'Voucher', 'type': 'text'},
                {'key': 'partner', 'label': 'From', 'type': 'text'}, {'key': 'method', 'label': 'Method', 'type': 'text'},
                {'key': 'ref', 'label': 'Slip / ref.', 'type': 'text'}, {'key': 'charges', 'label': 'Charges', 'type': 'monetary'},
                {'key': 'amount', 'label': 'Amount', 'type': 'monetary'}]
        return self._doc('deposit_report', opts, cols, rows, '%s  |  %s' % (self._period_name(df, dt), self._label_range_ec(df, dt)),
                         'Bank Deposit Report')

    # ================================================================== Transaction detail / chart of accounts
    def _report_transactions(self, opts):
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        dom = self._base_domain(opts, df, dt)
        s = (opts.get('search') or '').strip()
        if s:
            num = None
            try:
                num = float(s.replace(',', ''))
            except ValueError:
                pass
            sub = ['|', '|', '|', '|', '|', ('move_id.name', 'ilike', s), ('move_id.ref', 'ilike', s), ('name', 'ilike', s),
                   ('partner_id.name', 'ilike', s), ('account_id.code', '=like', s + '%'), ('account_id.name', 'ilike', s)]
            if num is not None:
                sub = ['|', '|'] + sub + [('debit', '=', num), ('credit', '=', num)]
            dom += sub
        aml = self.env['account.move.line'].search(dom, order='date desc, id desc', limit=2000)
        vmap = self._voucher_map(aml.move_id)
        rows = []
        for l in aml:
            v = vmap.get(l.move_id.id)
            rows.append({'id': 'l%d' % l.id, 'parent': None, 'level': 0, 'kind': 'line', 'label': l.move_id.name,
                         'values': {'date': fields.Date.to_string(l.date), 'number': l.move_id.name, 'jrnl': l.journal_id.code,
                                    'account': '%s %s' % (l.account_id.code, l.account_id.name), 'partner': l.partner_id.name or '',
                                    'name': l.name or '', 'debit': l.debit, 'credit': l.credit},
                         'open': {'model': 'sb.voucher', 'id': v.id} if v else {'model': 'account.move', 'id': l.move_id.id}})
        cols = [{'key': 'date', 'label': 'Date', 'type': 'date'}, {'key': 'number', 'label': 'Reference', 'type': 'text'},
                {'key': 'jrnl', 'label': 'Jrnl', 'type': 'text'}, {'key': 'account', 'label': 'Account', 'type': 'text'},
                {'key': 'partner', 'label': 'Customer / Vendor', 'type': 'text'}, {'key': 'name', 'label': 'Description', 'type': 'text'},
                {'key': 'debit', 'label': 'Debit', 'type': 'monetary'}, {'key': 'credit', 'label': 'Credit', 'type': 'monetary'}]
        if len(rows) == 2000:
            rows.append({'id': 'cap', 'parent': None, 'level': 0, 'kind': 'note', 'warning': True,
                         'label': 'Only the latest 2,000 lines are listed: narrow the dates or the search to see the rest.',
                         'values': {}})
        sub = '%s  |  %d lines%s' % (self._period_name(df, dt), min(len(rows), 2000), ' (latest 2,000)' if len(rows) > 2000 else '')
        return self._doc('transactions', opts, cols, rows, sub, 'Transaction Detail')

    def _report_chart(self, opts):
        bal = self._sums(self._base_domain(opts, None, opts['date_to']))
        rows = []
        for a in self._accounts():
            if not self._search_ok(opts, a.code, a.name, a.sb_ifrs_line_id.name):
                continue
            rows.append({'id': 'a%d' % a.id, 'parent': None, 'level': 0, 'kind': 'line', 'label': a.name, 'code': a.code,
                         'values': {'code': a.code, 'name': a.name,
                                    'type': dict(a._fields['account_type']._description_selection(self.env)).get(a.account_type),
                                    'ifrs': a.sb_ifrs_line_id.display_name or 'NOT MAPPED',
                                    'cf': dict(a._fields['sb_cash_flow']._description_selection(self.env)).get(a.sb_cash_flow) if a.sb_cash_flow else '',
                                    'balance': bal.get(a.id, {}).get('balance', 0.0)},
                         'warning': not a.sb_ifrs_line_id, 'open': {'model': 'account.account', 'id': a.id}})
        cols = [{'key': 'code', 'label': 'Account', 'type': 'text'}, {'key': 'name', 'label': 'Description', 'type': 'text'},
                {'key': 'type', 'label': 'Type', 'type': 'text'}, {'key': 'ifrs', 'label': 'IFRS line item', 'type': 'text'},
                {'key': 'cf', 'label': 'Cash flow class', 'type': 'text'}, {'key': 'balance', 'label': 'Balance', 'type': 'monetary'}]
        return self._doc('chart', opts, cols, rows, '%d accounts, balances at %s' % (len(rows), opts['date_to']), 'Chart of Accounts')

    # ================================================================== Income statement current + YTD
    def _report_income_ytd(self, opts):
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        ytd_from = fields.Date.to_string(self._fy_start(dt))
        cur = self._report_profit_loss(dict(opts, comparison='none'))
        ytd = self._report_profit_loss(dict(opts, comparison='none', date_from=ytd_from))
        ymap = {r['id']: r['values'].get('p0', 0.0) for r in ytd['rows']}
        rev_cur = next((r['values'].get('p0', 0.0) for r in cur['rows'] if r['id'] == 'revenue'), 0.0)
        rev_ytd = ymap.get('revenue', 0.0)
        cmap = {r['id']: r['values'].get('p0', 0.0) for r in cur['rows']}
        # the layout of the whole span (period and year to date together) keeps every account in its section, also
        # when the period starts in the previous fiscal year
        span_from = min(df, self._d(ytd_from))
        layout = ytd if span_from == self._d(ytd_from) else self._report_profit_loss(
            dict(opts, comparison='none', date_from=fields.Date.to_string(span_from)))
        seen = {r['id'] for r in layout['rows']}
        rows = []
        for r in layout['rows'] + [r for r in cur['rows'] + ytd['rows'] if r['id'] not in seen]:
            v = cmap.get(r['id'], 0.0)
            y = ymap.get(r['id'], 0.0)
            r = dict(r, values={'cur': v, 'cur_pct': (v / rev_cur * 100.0) if abs(rev_cur) > 0.005 else None,
                                'ytd': y, 'ytd_pct': (y / rev_ytd * 100.0) if abs(rev_ytd) > 0.005 else None})
            rows.append(r)
        cols = [{'key': 'label', 'label': '', 'type': 'text'},
                {'key': 'cur', 'label': 'Current period', 'sub': self._period_name(df, dt), 'type': 'monetary'},
                {'key': 'cur_pct', 'label': '% of revenue', 'type': 'percent'},
                {'key': 'ytd', 'label': 'Year to date', 'sub': self._label_range(self._d(ytd_from), dt), 'type': 'monetary'},
                {'key': 'ytd_pct', 'label': '% of revenue', 'type': 'percent'}]
        return self._doc('income_ytd', opts, cols, rows, '%s  |  %s' % (self._period_name(df, dt), self._label_range_ec(df, dt)),
                         'Income Statement')

    # ================================================================== Cash flow (IAS 7 indirect) from the IFRS cash-flow classes
    def _report_cash_flow(self, opts):
        """The statement for the period, with the comparative period(s) (previous / same period last year) beside it:
        each column computed on its own dates; the rows are the union, in the current period's order."""
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        if opts.get('simple'):
            return self._cash_flow_simple(opts, df, dt)
        main = self._cash_flow_period(opts, df, dt)
        if opts.get('comparison') not in ('previous', 'last_year'):
            main['rows'] = [r for r in main['rows'] if not r.get('cf_recon')]  # no overdraft: no reconciliation
            return main
        periods = [c for c in self._column_periods(opts) if c['key'] != 'p0' and not c.get('is_total')]
        docs = [('amount', main['columns'][1]['label'], main)] + [
            (c['key'], c['label'], self._cash_flow_period(opts, c['date_from'], c['date_to'])) for c in periods]
        keys = [k for k, _l, _d in docs]
        # the rows: the union of every period's lines, in the statement's own order (each row's 'seq'), every column
        # filled (a line a period does not have is 0.00 there) and each figure drilling to its own period
        merged, notes = {}, defaultdict(dict)
        for key, _label, doc in docs:
            for r in doc['rows']:
                if r['kind'] == 'note':
                    notes[r['id']][key] = r
                    continue
                m = merged.get(r['id'])
                if not m:
                    m = merged[r['id']] = dict(r, values={}, drills={}, signs=set())
                m['values'][key] = r['values'].get('amount', 0.0)
                m['drills'][key] = r.get('drill')
                m['signs'].add(r['values'].get('amount', 0.0) < 0)
                m['cf_recon'] = m.get('cf_recon', True) and r.get('cf_recon', False)
        rows = []
        for m in merged.values():
            if m['cf_recon']:
                continue  # no overdraft in any period shown
            for k in keys:
                m['values'].setdefault(k, 0.0)
                m['drills'].setdefault(k, None)
            m['drill'] = m['drills']['amount'] or next((d for d in m['drills'].values() if d), None)
            if m.get('cf_line') and len(m.pop('signs')) > 1:
                # bought in one period, sold in another: one neutral label
                m['label'] = 'Purchase and disposal of %s (net)' % (m['cf_line'][:1].lower() + m['cf_line'][1:])
            m.pop('signs', None)
            rows.append(m)
        for nid, by_key in notes.items():
            texts = {r['label'] for r in by_key.values()}
            if len(by_key) == len(docs) and len(texts) == 1:
                rows.append(next(iter(by_key.values())))  # the same note for every period
                continue
            for key, label, _doc in docs:
                r = by_key.get(key)
                if r:
                    rows.append(dict(r, id='%s_%s' % (nid, key), label='%s: %s' % (label, r['label']),
                                     values={key: r['values']['amount']} if 'amount' in r['values'] else {},
                                     seq=tuple(r['seq']) + (keys.index(key),)))
        rows.sort(key=lambda r: r['seq'])
        main['rows'] = rows
        for key, label, _doc in docs[1:]:
            c = next(c for c in periods if c['key'] == key)
            main['columns'].append({'key': key, 'label': label, 'sub': c['sub'], 'type': 'monetary'})
        return main

    def _cash_flow_period(self, opts, df, dt):
        # balances brought over from the old books are not cash flows: dated inside the period or not, they count as
        # if before it, in the opening cash and the opening balances
        brought = list(self._opening_move_ids())
        open_bal = self._sums(self._base_domain(opts, None, dt) + ['|', ('date', '<', df), ('move_id', 'in', brought)])
        close_bal = self._sums(self._base_domain(opts, None, dt))
        n_brought = self.env['account.move'].search_count([('id', 'in', brought), ('date', '>=', df), ('date', '<=', dt)])
        accounts = self._accounts(set(open_bal) | set(close_bal))
        # the profit is the income statement's, whole; the part of it that came over with the opening balances (the
        # migration year's results brought in by the opening entries) is taken out on its own line, as it is no cash flow
        pl_dom = self._base_domain(dict(opts, exclude_closing=True), df, dt) + [('account_id.include_initial_balance', '=', False)]
        pl = self._sums(pl_dom, ())
        profit = -(pl.get((), {}).get('balance', 0.0)) if pl else 0.0
        pl_brought = self._sums(pl_dom + [('move_id', 'in', brought)], ('account_id',)) if brought else {}
        profit_brought = -sum(v['balance'] for v in pl_brought.values())
        brought_in = sorted(self.env['account.move'].search([('id', 'in', brought), ('date', '>=', df), ('date', '<=', dt)]).ids) \
            if pl_brought else []
        # interest and income tax: the charge on the income statement (by IFRS line: finance costs PL-10, income tax
        # PL-11) is added back, and the cash paid shown on its own line. Tax paid is the charge plus the change in the
        # tax accounts the charge is booked against (profit tax payable, prepaid profit tax, withholding credits: the
        # current tax lines SFP-CL-04 / SFP-CA-05). Interest is taken as paid when charged: a payable that holds unpaid
        # interest cannot be told from the other payables in it
        cur_pl = self._sums(pl_dom + [('move_id', 'not in', brought)], ('account_id',))
        fin_cost_acc = self.env['account.account'].search([('company_ids', 'in', self.env.company.id), ('sb_ifrs_line_id.code', '=', 'PL-10')])
        tax_acc = self.env['account.account'].search([('company_ids', 'in', self.env.company.id), ('sb_ifrs_line_id.code', '=', 'PL-11')])
        interest_charge = sum(cur_pl.get(a.id, {}).get('balance', 0.0) for a in fin_cost_acc)
        tax_charge = sum(cur_pl.get(a.id, {}).get('balance', 0.0) for a in tax_acc)

        def booked_against(charge_accounts):
            if not charge_accounts:
                return set()
            AML = self.env['account.move.line'].sudo()
            moves = [m.id for [m] in AML._read_group([('company_id', '=', self.env.company.id), ('account_id', 'in', charge_accounts.ids),
                                                     ('debit', '>', 0)], ['move_id'])]
            accs = self.env['account.account'].browse([a.id for [a] in AML._read_group(
                [('move_id', 'in', moves), ('credit', '>', 0), ('account_id.include_initial_balance', '=', True)], ['account_id'])])
            return set(accs.filtered(lambda a: a.sb_ifrs_line_id.code in ('SFP-CL-04', 'SFP-CA-05')).ids)
        tax_against = booked_against(tax_acc)
        interest_against = set()
        # equity moves without the year-end closing (it only carries the profit, already counted above, into equity),
        # and without transfers inside equity (retained earnings to the legal reserve): no cash moves there
        eq_lines = self._sums(self._base_domain(dict(opts, exclude_closing=True), df, dt)
                              + [('account_id.account_type', 'in', ('equity', 'equity_unaffected')),
                                 ('move_id', 'not in', brought)], ('move_id', 'account_id'))
        eq_net = defaultdict(float)
        for (move_id, _aid), v in eq_lines.items():
            eq_net[move_id] += v['balance']
        eq_moves = defaultdict(lambda: {'balance': 0.0})
        eq_move_ids = defaultdict(set)
        for (move_id, aid), v in eq_lines.items():
            if abs(eq_net[move_id]) > 0.005:
                eq_moves[aid]['balance'] += v['balance']
                eq_move_ids[aid].add(move_id)
        d0, d1 = fields.Date.to_string(df), fields.Date.to_string(dt)
        drills = {}  # row label -> drill: the ledger of the accounts (and, for equity, the entries) behind the line

        def add_drill(group, label, account, moves=None):
            d = drills.setdefault((group, label), {'report': 'general_ledger', 'account_ids': [], 'date_from': d0, 'date_to': d1})
            d['account_ids'].append(account.id)
            if moves is not None:
                d['move_ids'] = sorted(set(d.get('move_ids', [])) | moves)
        dep_accounts, cash_accounts = [], []
        depreciation = dep_charged = dep_reversed = 0.0
        dep_ids = set(self._depreciation_accounts().ids)
        dep_split = self._dep_split(opts, df, dt, dep_ids, brought)
        tax_change = interest_change = 0.0  # change in the debit balance of the tax / interest accounts
        tax_drill, interest_drill = [], []
        overdraft_close = 0.0  # bank accounts in credit at the end: liabilities on the statement of financial position
        groups = defaultdict(lambda: defaultdict(float))
        liability_lines = set()
        cash_open = cash_close = 0.0
        for a in accounts:
            if not a.include_initial_balance:
                continue
            change = close_bal.get(a.id, {}).get('balance', 0.0) - open_bal.get(a.id, {}).get('balance', 0.0)
            cf = a.sb_cash_flow or ('cash' if a.account_type == 'asset_cash' else 'working_capital')
            if cf == 'cash':
                cash_open += open_bal.get(a.id, {}).get('balance', 0.0)
                cash_close += close_bal.get(a.id, {}).get('balance', 0.0)
                cash_accounts.append(a.id)
                if close_bal.get(a.id, {}).get('balance', 0.0) < -0.005 and self._is_bank_account(a):
                    overdraft_close += -close_bal[a.id]['balance']
                continue
            line = a.sb_ifrs_line_id.name or a.name
            if a.account_type in ('equity', 'equity_unaffected'):
                # capital paid in, dividends, and entries made straight to equity (prior-year corrections): every
                # movement except the closing, so the statement always ends on the cash in the ledger
                moved = -eq_moves.get(a.id, {}).get('balance', 0.0)
                if abs(moved) < 0.005:
                    continue
                if cf == 'profit' or a.account_type == 'equity_unaffected':
                    lab, grp = 'Entries made directly to retained earnings', 'working_capital'
                else:
                    lab, grp = line if cf == 'financing' else '%s (entries made directly to equity)' % line, 'financing'
                groups[grp][lab] += moved
                add_drill('operating' if grp == 'working_capital' else grp, lab, a, eq_move_ids.get(a.id, set()))
                continue
            if cf in ('profit', 'none'):
                continue
            # accumulated depreciation and amortisation (by the asset categories and the depreciation entries, not by
            # name): the charge (net of corrections) is the non-cash item added back, wherever the expense went
            # (the depreciation line, or production cost and so cost of sales); the depreciation eliminated on disposals
            # goes with the disposed cost, in investing (the carrying amount disposed)
            if a.id in dep_ids:
                split = dep_split.get(a.id) or {'charge': 0.0, 'reversed': 0.0, 'disposal': 0.0}
                depreciation += split['charge'] + split['reversed']
                dep_charged += split['charge']
                dep_reversed += split['reversed']
                dep_accounts.append(a.id)
                rest = -change - (split['charge'] + split['reversed'])  # the disposals (and anything else)
                if abs(rest) > 0.005:
                    grp = cf if cf in ('investing', 'financing') else 'investing'
                    groups[grp][line] += rest
                    add_drill(grp, line, a)
                continue
            if a.id in tax_against:
                tax_change += change
                tax_drill.append(a.id)
                continue
            if a.id in interest_against:
                interest_change += change
                interest_drill.append(a.id)
                continue
            if a.account_type.startswith('liability'):
                liability_lines.add(line)
            # increase in an asset uses cash (negative); increase in liability/equity provides cash
            groups[cf][line] += -change
            add_drill('operating' if cf in ('working_capital', 'non_cash') else cf, line, a)
        rows = []
        # 'seq' is each row's place in the statement, the same in every period: comparatives line up row for row
        sec_no = {'operating': 0, 'investing': 1, 'financing': 2}

        def section(key, label, items, extra=None, tail=None):
            total = sum(items.values()) + sum(x[1] for x in (extra or [])) + sum(x[1] for x in (tail or []))
            s = sec_no[key]
            rows.append({'id': key, 'parent': None, 'level': 0, 'kind': 'section', 'label': label, 'values': {'amount': total},
                         'expandable': True, 'seq': (s, -1, 0)})
            for n, (lab, v, drill) in enumerate(extra or []):
                if lab != 'Profit for the period' and abs(v) < 0.005:
                    continue
                rows.append({'id': '%s_%s' % (key, lab), 'parent': key, 'level': 1, 'kind': 'line', 'label': lab, 'values': {'amount': v},
                             'drill': drill, 'seq': (s, 0, n)})
            for lab, v in sorted(items.items()):
                if abs(v) > 0.005:
                    if key == 'investing':
                        text = ('Purchase of %s' if v < 0 else 'Proceeds from %s') % lab[:1].lower() + lab[1:] \
                            if not lab.startswith('Entries made') else lab
                    elif key != 'operating' or lab.startswith('Entries made'):
                        text = lab
                    elif lab in liability_lines:
                        text = 'Increase / (decrease) in %s' % lab
                    else:
                        text = '(Increase) / decrease in %s' % lab
                    rows.append({'id': '%s_%s' % (key, lab), 'parent': key, 'level': 1, 'kind': 'line', 'label': text,
                                 'values': {'amount': v}, 'drill': drills.get((key, lab)), 'seq': (s, 1, lab),
                                 'cf_line': lab if key == 'investing' and not lab.startswith('Entries made') else None})
            for n, (lab, v, drill) in enumerate(tail or []):
                if abs(v) > 0.005:
                    rows.append({'id': '%s_%s' % (key, lab), 'parent': key, 'level': 1, 'kind': 'line', 'label': lab,
                                 'values': {'amount': v}, 'drill': drill, 'seq': (s, 2, n)})
            return total

        def note_after(row_id, nid, text, seq):
            # a note on a line, right under it
            idx = next((i for i, r in enumerate(rows) if r['id'] == row_id), None)
            if idx is not None:
                rows.insert(idx + 1, {'id': nid, 'parent': rows[idx]['parent'], 'level': 1, 'kind': 'note',
                                      'label': text, 'values': {}, 'seq': seq})

        op_items = dict(groups['working_capital'])
        for k, v in groups['non_cash'].items():
            op_items[k] = op_items.get(k, 0.0) + v
        def gl(ids, **kw):
            return dict({'report': 'general_ledger', 'account_ids': sorted(ids), 'date_from': d0, 'date_to': d1}, **kw) if ids else None
        operating = section('operating', 'Cash flows from operating activities', op_items,
                            [('Profit for the period', profit, {'report': 'profit_loss', 'date_from': d0, 'date_to': d1}),
                             ('Less: profit included in the opening balances brought over', -profit_brought,
                              gl(list(pl_brought), move_ids=brought_in, exclude_closing=True)),
                             ('Income tax expense', tax_charge, gl(tax_acc.ids, exclude_closing=True)),
                             ('Finance costs', interest_charge, gl(fin_cost_acc.ids, exclude_closing=True)),
                             ('Depreciation and amortisation', depreciation, gl(dep_accounts))],
                            [('Interest paid', -(interest_charge + interest_change), gl(interest_drill or fin_cost_acc.ids)),
                             ('Income tax paid', -(tax_charge + tax_change), gl(tax_drill or tax_acc.ids))])
        fmt = '{:,.2f}'.format
        # the depreciation added back against the income statement's depreciation line: the part charged to production
        # (and so in inventory / cost of sales) and the corrections, said under the line
        pl_dep_acc = self.env['account.account'].search([('company_ids', 'in', self.env.company.id), ('sb_ifrs_line_id.code', '=', 'PL-06')])
        pl_dep = sum(cur_pl.get(a.id, {}).get('balance', 0.0) for a in pl_dep_acc)
        dep_text = []
        if abs(dep_reversed) > 0.005:
            dep_text.append('the charge of %s less %s reversed (corrections of depreciation charged earlier, not disposals)'
                            % (fmt(dep_charged), fmt(-dep_reversed)))
        if abs(depreciation - pl_dep) > 0.005:
            dep_text.append('of which %s is depreciation of production assets charged to production cost (inventory and cost of '
                            'sales) and %s is on the depreciation and amortisation line of the income statement'
                            % (fmt(depreciation - pl_dep), fmt(pl_dep)))
        if dep_text:
            note_after('operating_Depreciation and amortisation', 'operating_dep_note',
                       'Depreciation and amortisation: %s.' % '; '.join(dep_text), (0, 0, 4.5))
        if abs(interest_charge) > 0.005:
            note_after('operating_Interest paid', 'operating_interest_note',
                       'Interest paid is taken as the finance costs charged in the period: interest accrued but unpaid '
                       'cannot be told apart from the other payables it sits in.', (0, 2, 0.5))
        investing = section('investing', 'Cash flows from investing activities', dict(groups['investing']))
        financing = section('financing', 'Cash flows from financing activities', dict(groups['financing']))
        if any(abs(v['disposal']) > 0.005 for v in dep_split.values()):
            disposed = sum(v['disposal'] for v in dep_split.values())
            rows.append({'id': 'investing_dep_note', 'parent': 'investing', 'level': 1, 'kind': 'note', 'values': {},
                         'seq': (1, 3, 0),
                         'label': 'Disposals are shown at their carrying amount: the accumulated depreciation eliminated '
                                  '(%s) is netted with the cost disposed in the lines above.' % fmt(disposed)})
            rows.sort(key=lambda r: r['seq'])  # the note at the end of its section
        net = operating + investing + financing
        rows.append({'id': 'net', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Net increase / (decrease) in cash',
                     'values': {'amount': net}, 'sum_of': [('operating', 1), ('investing', 1), ('financing', 1)], 'seq': (3, 0, 0)})
        rows.append({'id': 'open', 'parent': None, 'level': 0, 'kind': 'line', 'label': 'Cash and cash equivalents at beginning'
                     + (' (with the balances brought forward)' if n_brought else ''), 'values': {'amount': cash_open},
                     'drill': {'report': 'cash_book', 'account_ids': cash_accounts, 'date_from': d0, 'date_to': d1} if cash_accounts else None,
                     'seq': (4, 0, 0)})
        rows.append({'id': 'close', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Cash and cash equivalents at end',
                     'values': {'amount': cash_open + net}, 'sum_of': [('net', 1), ('open', 1)], 'seq': (5, 0, 0)})
        # the cash of this statement keeps the overdrafts (IAS 7.8); the statement of financial position shows them as
        # liabilities: the two reconciled. Made for every period, kept when any period shown has an overdraft
        no_od = overdraft_close <= 0.005
        rows.append({'id': 'recon', 'parent': None, 'level': 0, 'kind': 'section', 'expandable': True, 'cf_recon': no_od,
                     'label': 'Reconciliation to the statement of financial position', 'values': {'amount': cash_close},
                     'seq': (6, -1, 0)})
        rows.append({'id': 'recon_cash', 'parent': 'recon', 'level': 1, 'kind': 'line', 'cf_recon': no_od,
                     'label': 'Cash and cash equivalents on the statement of financial position',
                     'values': {'amount': cash_close + overdraft_close},
                     'drill': {'report': 'balance_sheet', 'date_from': d0, 'date_to': d1}, 'seq': (6, 0, 0)})
        rows.append({'id': 'recon_od', 'parent': 'recon', 'level': 1, 'kind': 'line', 'cf_recon': no_od,
                     'label': 'Bank overdrafts repayable on demand (in current liabilities)', 'values': {'amount': -overdraft_close or 0.0},
                     'drill': {'report': 'balance_sheet', 'date_from': d0, 'date_to': d1}, 'seq': (6, 0, 1)})
        if abs(cash_open + net - cash_close) > 0.01:
            if opts.get('analytic_ids') or opts.get('analytic_plan_ids'):
                # an analytic filter keeps only the lines tagged to the accounts chosen: the cash side of an entry is
                # seldom tagged, so the statement cannot reconcile to cash; said, not flagged as an error
                rows.append({'id': 'diff', 'parent': None, 'level': 0, 'kind': 'note', 'seq': (8, 0, 0),
                             'label': 'Analytic filter: only the lines tagged to the analytic accounts chosen count (by their '
                                      'share), and the cash side of an entry is seldom tagged, so this statement does not '
                                      'reconcile to cash (%s left over). Remove the analytic filter for the full statement.'
                                      % fmt(cash_close - cash_open - net),
                             'values': {}})
            else:
                rows.append({'id': 'diff', 'parent': None, 'level': 0, 'kind': 'note', 'warning': True, 'seq': (8, 0, 0),
                             'label': 'Difference to cash per ledger (%s): check IFRS cash-flow classes' % '{:,.2f}'.format(cash_close),
                             'values': {'amount': cash_close - cash_open - net}})
        if n_brought:
            rows.append({'id': 'brought', 'parent': None, 'level': 0, 'kind': 'note', 'values': {}, 'seq': (8, 0, 1),
                         'label': '%d opening balance entries dated in this period are not cash flows: their cash is in the '
                                  'cash at beginning, their other balances in the opening balances.' % n_brought})
        cols = [{'key': 'label', 'label': '', 'type': 'text'},
                {'key': 'amount', 'label': self._period_name(df, dt), 'sub': self._label_range(df, dt), 'type': 'monetary'}]
        return self._doc('cash_flow', opts, cols, rows, '%s  |  %s' % (self._period_name(df, dt), self._label_range_ec(df, dt)),
                         'Statement of Cash Flows')

    def _cash_flow_simple(self, opts, df, dt):
        """Cash in / out by counter-account: what came in and what went out of bank and cash."""
        AML = self.env['account.move.line']
        cash_by_move = defaultdict(float)
        brought = list(self._opening_move_ids())  # balances brought over are not cash in or out
        for r in AML.search_read(self._base_domain(opts, df, dt) + [('account_id.account_type', '=', 'asset_cash'),
                                                                     ('move_id', 'not in', brought)],
                                 ['move_id', 'balance']):
            cash_by_move[r['move_id'][0]] += r['balance']
        # counter-lines grouped once per entry and account (a per-entry filtered() was quadratic: minutes on a busy ledger)
        others = defaultdict(lambda: defaultdict(float))
        for r in AML.search_read([('move_id', 'in', list(cash_by_move)), ('account_id.account_type', '!=', 'asset_cash')],
                                 ['move_id', 'account_id', 'balance']):
            others[r['move_id'][0]][r['account_id'][0]] += abs(r['balance'])
        names = {a.id: '%s %s' % (a.code, a.name) for a in self.env['account.account'].browse(
            {a for o in others.values() for a in o}).exists()}
        inflow, outflow = defaultdict(float), defaultdict(float)
        for move_id, cash in cash_by_move.items():
            parts = others.get(move_id, {})
            total = sum(parts.values()) or 1.0
            for acc, amount in parts.items():
                share = cash * amount / total
                (inflow if share > 0 else outflow)[names.get(acc, '')] += share
        rows = []
        for key, label, items in (('in', 'Cash in', inflow), ('out', 'Cash out', outflow)):
            rows.append({'id': key, 'parent': None, 'level': 0, 'kind': 'section', 'label': label,
                         'values': {'amount': sum(items.values())}, 'expandable': True})
            for k, v in sorted(items.items(), key=lambda kv: -abs(kv[1])):
                rows.append({'id': '%s_%s' % (key, k), 'parent': key, 'level': 1, 'kind': 'line', 'label': k, 'values': {'amount': v}})
        rows.append({'id': 'net', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Net cash movement',
                     'values': {'amount': sum(inflow.values()) + sum(outflow.values())}, 'sum_of': [('in', 1), ('out', 1)]})
        cols = [{'key': 'label', 'label': '', 'type': 'text'}, {'key': 'amount', 'label': self._period_name(df, dt), 'type': 'monetary'}]
        return self._doc('cash_flow', opts, cols, rows, '%s  |  %s' % (self._period_name(df, dt), self._label_range_ec(df, dt)), 'Cash Flow')

    # ================================================================== VAT
    def _report_vat_return(self, opts):
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        dom = self._base_domain(opts, df, dt)
        base = self._sums(dom + [('tax_ids', '!=', False)], ('tax_ids',))
        tax = self._sums(dom + [('tax_line_id', '!=', False)], ('tax_line_id',))
        taxes = self._vat_taxes(self.env['account.tax'].browse(set(base) | set(tax)).exists())
        company = self.env.company
        registered = company.sb_vat_registered
        nil_kind = self._vat_nil_kind
        nil_labels = {
            'sale': {'zero': 'Zero-rated sales (exports and other supplies at 0%)', 'exempt': 'Exempt sales',
                     'out': 'Sales outside the scope of VAT'},
            'purchase': {'zero': 'Zero-rated purchases', 'exempt': 'Exempt purchases', 'out': 'Purchases outside the scope of VAT',
                         'none': 'Purchases without VAT (exempt goods, or suppliers who charge no VAT)'},
        }
        rows = []
        totals = {'sale': 0.0, 'purchase': 0.0}
        not_claimed = 0.0
        for side, label, sign in (('sale', 'Output tax (sales)', -1), ('purchase', 'Input tax (purchases)', 1)):
            side_rows = []
            nil = {'zero': 0.0, 'exempt': 0.0, 'out': 0.0, 'none': 0.0}
            s_base = s_tax = 0.0
            for t in taxes.filtered(lambda t: t.type_tax_use == side).sorted('sequence'):
                b = sign * base.get(t.id, {}).get('balance', 0.0)
                x = sign * tax.get(t.id, {}).get('balance', 0.0)
                if abs(b) < 0.005 and abs(x) < 0.005:
                    continue
                s_base += b
                if t.amount_type == 'percent' and not t.amount:
                    nil[nil_kind(t)] += b  # declared on their own lines below, as the return has them
                    continue
                s_tax += x
                side_rows.append({'id': '%s_%d' % (side, t.id), 'parent': side, 'level': 1, 'kind': 'line', 'label': t.name,
                                  'values': {'base': b, 'tax': x},
                                  'drill': {'report': 'vat_register', 'side': side}})
            if side == 'sale':
                # sales with no tax on them at all (a company not registered for VAT, or exempt goods entered without a
                # tax): exempt turnover, so the return shows the whole sales. Only sales documents count (invoices, cash
                # sales, credit notes), every line of what they sold whatever its account (_sale_doc_bases): a journal
                # voucher crediting income, exchange gains or interest is no supply (a void's reversal of a sale counts
                # too, in its own month, so a voided sale nets to nothing)
                b = sum(x['base'] - x['taxed'] for x in self._sale_doc_bases(opts, df, dt).values())
                if abs(b) >= 0.005:
                    s_base += b
                    nil['exempt'] += b
            else:
                # bills and cash purchases with no tax on a line: bought without VAT
                b = sum(self._vat_free_purchase_lines(opts, df, dt).mapped('balance'))
                if abs(b) >= 0.005:
                    s_base += b
                    nil['none'] += b
            for kind in ('zero', 'exempt', 'out', 'none'):
                if abs(nil[kind]) >= 0.005:
                    side_rows.append({'id': '%s_%s' % (side, kind), 'parent': side, 'level': 1, 'kind': 'line',
                                      'label': nil_labels[side][kind], 'values': {'base': nil[kind], 'tax': 0.0},
                                      'drill': {'report': 'vat_register', 'side': side}})
            if side == 'purchase':
                customs = sum(self._customs_vat_lines(df, dt).mapped('balance'))
                if abs(customs) >= 0.005:
                    s_tax += customs
                    side_rows.append({'id': 'purchase_customs', 'parent': side, 'level': 1, 'kind': 'line', 'label': 'Import VAT paid at customs',
                                      'values': {'base': 0.0, 'tax': customs}, 'drill': {'report': 'vat_register', 'side': side}})
                if not registered and abs(s_tax) >= 0.005:
                    # not registered for VAT: the VAT paid on purchases is not claimable; it is part of their cost
                    not_claimed = s_tax
                    for r in side_rows:
                        r['values']['cost'], r['values']['tax'] = r['values']['tax'], 0.0
                    s_tax = 0.0
                    label = 'Input tax (purchases): not claimable'
            totals[side] = s_tax
            values = {'base': s_base, 'tax': s_tax}
            if side == 'purchase' and not_claimed:
                values['cost'] = not_claimed
            rows.append({'id': side, 'parent': None, 'level': 0, 'kind': 'section', 'label': label,
                         'values': values, 'expandable': True})
            rows += side_rows
        rows.append({'id': 'payable', 'parent': None, 'level': 0, 'kind': 'total',
                     'label': 'VAT payable / (credit carried forward)',
                     'values': {'tax': totals['sale'] - totals['purchase']}})
        cols = [{'key': 'label', 'label': '', 'type': 'text'}, {'key': 'base', 'label': 'Taxable amount', 'type': 'monetary'},
                {'key': 'tax', 'label': 'VAT', 'type': 'monetary'}]
        if not_claimed:
            vat_accounts = taxes.filtered(lambda t: t.type_tax_use == 'purchase').invoice_repartition_line_ids.account_id
            rows.append({'id': 'not_registered', 'parent': None, 'level': 0, 'kind': 'note', 'warning': True,
                         'values': {'cost': not_claimed},
                         'label': 'The company is not registered for VAT (Accounting settings): the %s of VAT paid on purchases '
                                  'is not claimable and is no credit. It is part of the cost of what was bought; move it from %s '
                                  'to the cost accounts with a journal voucher.' % (
                                      '{:,.2f}'.format(not_claimed), ', '.join(vat_accounts.mapped('display_name')) or 'input VAT')})
            cols.append({'key': 'cost', 'label': 'VAT in cost (not claimable)', 'type': 'monetary'})
        title = 'VAT Return (URA)' if company.country_id.code == 'UG' else 'VAT Declaration'
        return self._doc('vat_return', opts, cols, rows, '%s  |  %s  |  TIN %s' % (self._period_name(df, dt), self._label_range_ec(df, dt), company.vat or 'not set'),
                         title)

    # ================================================================== TOT (Ethiopia)
    TOT_REPEALED = date(2024, 7, 8)  # Hamle 1, 2017 E.C.: VAT Proclamation 1341/2024 repealed turnover tax

    def _report_tot_return(self, opts):
        """Turnover Tax declaration (Proclamation 308/2002 as amended): a person not registered for VAT pays TOT on the
        gross receipts of its sales: 2% on goods sold locally, 2% (contractors, grain mills, tractors, combine harvesters)
        or 10% (other services) on services; supplies exempt from VAT are exempt (e.g. fertilizer). Worked from the sales
        documents of the period (invoices, cash sales, credit notes, and the reversals of voided ones), line by line on
        their sales accounts, and tied to the sales income in the ledger."""
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        company = self.env.company
        cur = company.currency_id
        repealed = 'Turnover tax was repealed from Hamle 1, 2017 E.C. (8 July 2024) by VAT Proclamation 1341/2024; nothing is due'
        cols = [{'key': 'label', 'label': '', 'type': 'text'}, {'key': 'rate', 'label': 'Rate %', 'type': 'number'},
                {'key': 'base', 'label': 'Gross receipts', 'type': 'monetary'}, {'key': 'tax', 'label': 'TOT', 'type': 'monetary'}]
        if df >= self.TOT_REPEALED:
            # no turnover tax exists for the period: no amounts, only the reason
            return self._doc('tot_return', opts, cols, [{'id': 'repealed', 'parent': None, 'level': 0, 'kind': 'note',
                                                         'label': repealed + '.', 'values': {}}],
                             '%s  |  %s  |  TIN %s' % (self._period_name(df, dt), self._label_range_ec(df, dt), company.vat or 'not set'),
                             'TOT Declaration (historic)')
        cut = dt >= self.TOT_REPEALED
        if cut:
            # a period running past the repeal: TOT only on the sales before it
            dt = self.TOT_REPEALED - timedelta(days=1)
            opts = dict(opts, date_to=fields.Date.to_string(dt))
        sale_moves = self.env['sb.voucher'].search([('company_id', '=', company.id), ('voucher_type', 'in', ('sale', 'cash_sale', 'sale_return')),
                                                    ('state', 'in', ('posted', 'void')), ('move_id', '!=', False)]).move_id
        income = self._base_domain(dict(opts, exclude_closing=True), df, dt) + [('account_id.account_type', '=', 'income')]
        lines = self.env['account.move.line'].search(income + [
            '|', '|', ('move_id.move_type', 'in', ('out_invoice', 'out_refund', 'out_receipt')), ('move_id', 'in', sale_moves.ids),
            ('move_id.reversed_entry_id', 'in', sale_moves.ids)])
        rates = {'goods': company.sb_tot_goods_rate or 0.0, 'service': company.sb_tot_service_rate or 0.0, 'exempt': 0.0}
        base = {'goods': 0.0, 'service': 0.0, 'exempt': 0.0}
        for l in lines:
            exempt_tax = any(t.amount_type == 'percent' and not t.amount and self._vat_nil_kind(t) == 'exempt' for t in l.tax_ids)
            kind = 'service' if l.product_id.type == 'service' else 'goods'
            if exempt_tax or (kind == 'goods' and company.sb_tot_goods_exempt):
                kind = 'exempt'
            base[kind] -= l.balance
        labels = {'goods': 'Goods sold (%g%%)' % rates['goods'], 'service': 'Services rendered (%g%%)' % rates['service'],
                  'exempt': 'Exempt sales (e.g. fertilizer and other agricultural inputs)'}
        rows = [{'id': 'sales', 'parent': None, 'level': 0, 'kind': 'section', 'label': 'Sales of the period (sales documents)',
                 'values': {'base': sum(base.values()), 'tax': 0.0}, 'expandable': True}]
        due = 0.0
        for kind in ('goods', 'service', 'exempt'):
            tax = cur.round(base[kind] * rates[kind] / 100.0)
            due += tax
            rows.append({'id': 'tot_%s' % kind, 'parent': 'sales', 'level': 1, 'kind': 'line', 'label': labels[kind],
                         'values': {'rate': rates[kind], 'base': base[kind], 'tax': tax}})
        rows[0]['values']['tax'] = due
        rows.append({'id': 'payable', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'TOT payable for the period',
                     'values': {'tax': due}})
        # the tie: every sales-account line of the period, from whatever entry
        gl = -(self._sums(income, ()).get((), {}).get('balance', 0.0))
        rows.append({'id': 'gl', 'parent': None, 'level': 0, 'kind': 'line', 'label': 'Sales income in the ledger (sales accounts)',
                     'values': {'base': gl}, 'drill': {'report': 'general_ledger', 'account_ids': self.env['account.account'].search(
                         [('company_ids', 'in', company.id), ('account_type', '=', 'income')]).ids}})
        diff = cur.round(gl - sum(base.values()))
        if not cur.is_zero(diff):
            rows.append({'id': 'diff', 'parent': None, 'level': 0, 'kind': 'note', 'warning': True, 'values': {'base': diff},
                         'label': 'Sales income not entered as a sales document (journal vouchers or other entries on a sales '
                                  'account): %s. Check it; a sale belongs on a Cash Sale or Sales Invoice.' % '{:,.2f}'.format(diff)})
        if company.sb_vat_registered:
            rows.append({'id': 'vat_registered', 'parent': None, 'level': 0, 'kind': 'note', 'warning': True, 'values': {},
                         'label': 'The company is registered for VAT (Accounting settings): it charges VAT and pays no TOT.'})
        if cut:
            rows.append({'id': 'repealed', 'parent': None, 'level': 0, 'kind': 'note', 'values': {},
                         'label': '%s: sales up to 7 July 2024 only.' % repealed})
        return self._doc('tot_return', opts, cols, rows, '%s  |  %s  |  TIN %s' % (
            self._period_name(df, dt), self._label_range_ec(df, dt), company.vat or 'not set'), 'TOT Declaration')

    def _vat_taxes(self, taxes):
        """The VAT among taxes: withholding (a negative rate, e.g. the chart's old "2% WH") and turnover tax (TOT) are not
        VAT and stay off the VAT return and registers."""
        def not_vat(t):
            group = (t.tax_group_id.name or '').lower()
            return t.amount < 0 or 'withholding' in group or group.split()[:1] == ['tot'] or 'turnover' in group
        return taxes.filtered(lambda t: not not_vat(t))

    def _vat_nil_kind(self, t):
        """A tax of nil: zero-rated (exports and other supplies taxed at 0%), exempt, or outside the scope of VAT. The VAT
        return and the VAT registers file it the same way."""
        words = ' '.join(html2plaintext(str(x or '')) for x in (t.name, t.description, t.invoice_label)).lower()
        if 'exempt' in words:
            return 'exempt'
        if 'out of scope' in words or 'out' in t.name.lower().split():
            return 'out'
        return 'zero'

    def _vat_free_purchase_lines(self, opts, df, dt):
        """What was bought with no tax on the line (bills, debit notes and cash purchases): the purchase side of the VAT
        return counts it with the exempt and zero-rated purchases."""
        c = self.env.company
        cash_purchase = self.env['account.journal'].search([('code', '=', 'CP'), ('company_id', '=', c.id)])
        vat_accounts = self.env['account.tax'].search([('company_id', '=', c.id)]).invoice_repartition_line_ids.account_id
        return self.env['account.move.line'].search(self._base_domain(dict(opts, exclude_closing=True), df, dt) + [
            ('tax_ids', '=', False), ('tax_line_id', '=', False), ('account_id', 'not in', vat_accounts.ids),
            ('account_id.account_type', 'in', ('expense', 'expense_direct_cost', 'asset_current', 'asset_non_current',
                                               'asset_fixed', 'asset_prepayments')),
            '|', ('move_id.move_type', 'in', ('in_invoice', 'in_refund')), ('journal_id', 'in', cash_purchase.ids)])

    def _customs_vat_lines(self, df, dt):
        """VAT paid straight to the input-VAT account without a tax on the line: import VAT at customs (the monthly return
        settlement credits the account and is left out)."""
        c = self.env.company
        purchase = self.env['account.tax'].search([('company_id', '=', c.id), ('type_tax_use', '=', 'purchase'), ('amount', '>', 0)])
        accounts = purchase.invoice_repartition_line_ids.account_id
        if not accounts:
            return self.env['account.move.line']
        return self.env['account.move.line'].search([('company_id', '=', c.id), ('parent_state', '=', 'posted'), ('account_id', 'in', accounts.ids),
                                                     ('tax_line_id', '=', False), ('debit', '>', 0), ('date', '>=', df), ('date', '<=', dt)])

    def _report_vat_register(self, opts):
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        side = opts.get('side') or 'sale'
        sign = -1 if side == 'sale' else 1
        vat = self._vat_taxes(self.env['account.tax'].with_context(active_test=False).search([
            ('company_id', '=', self.env.company.id), ('type_tax_use', '=', side)]))
        dom = self._base_domain(opts, df, dt) + ['|', ('tax_ids', 'in', vat.ids), ('tax_line_id', 'in', vat.ids)]
        lines = self.env['account.move.line'].search(dom, order='date, move_name')
        vmap = self._voucher_map(lines.move_id)
        per_move = defaultdict(lambda: defaultdict(float))
        for l in lines:
            m = per_move[l.move_id]
            if l.tax_line_id:
                m['tax'] += sign * l.balance
            else:
                for t in l.tax_ids & vat:
                    key = 'base_std' if t.amount else 'base_' + self._vat_nil_kind(t)  # filed as the VAT return files it
                    m[key] += sign * l.balance
        if side == 'purchase':
            for l in self._customs_vat_lines(df, dt):
                per_move[l.move_id]['tax'] += l.balance
            vmap.update(self._voucher_map(self._customs_vat_lines(df, dt).move_id))
        rows, grand = [], defaultdict(float)
        for move, vals in per_move.items():
            v = vmap.get(move.id)
            partner = (v.partner_id if v else move.partner_id).commercial_partner_id
            if not self._search_ok(opts, partner.name, partner.vat, move.name, move.ref):
                continue
            for k, val in vals.items():
                grand[k] += val
            total = sum(val for k, val in vals.items())
            grand['total'] += total
            rows.append({'id': 'm%d' % move.id, 'parent': None, 'level': 0, 'kind': 'line', 'label': move.name,
                         'values': {'date': fields.Date.to_string(move.date), 'number': move.name, 'ref': move.ref or '',
                                    'partner': partner.name or ('Walk-in customer' if side == 'sale' else ''),
                                    'vat': partner.vat or '', **vals, 'total': total},
                         'warning': not partner.vat,
                         'open': {'model': 'sb.voucher', 'id': v.id} if v else {'model': 'account.move', 'id': move.id}})
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total',
                     'values': {'date': 'Total', **dict(grand)}})
        cols = [{'key': 'date', 'label': 'Date', 'type': 'date'}, {'key': 'number', 'label': 'Document', 'type': 'text'},
                {'key': 'ref', 'label': 'Receipt / invoice no.', 'type': 'text'},
                {'key': 'partner', 'label': 'Buyer' if side == 'sale' else 'Seller', 'type': 'text'},
                {'key': 'vat', 'label': 'TIN', 'type': 'text'},
                {'key': 'base_std', 'label': 'Taxable (%g%%)' % locale_of(self.env.company).get('vat', 15.0), 'type': 'monetary'},
                {'key': 'base_zero', 'label': 'Zero-rated', 'type': 'monetary'},
                {'key': 'base_exempt', 'label': 'Exempt', 'type': 'monetary'},
                {'key': 'base_out', 'label': 'Out of scope', 'type': 'monetary'},
                {'key': 'tax', 'label': 'VAT', 'type': 'monetary'}, {'key': 'total', 'label': 'Total', 'type': 'monetary'}]
        title = 'VAT Sales Register' if side == 'sale' else 'VAT Purchase Register'
        return self._doc('vat_register', opts, cols, rows, '%s  |  %s  |  rows in red have no TIN' % (
            self._period_name(df, dt), self._label_range_ec(df, dt)), title)

    # ================================================================== Company: audit trail, find vouchers
    def _report_audit_trail(self, opts):
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        msgs = self.env['mail.message'].search([
            ('model', '=', 'sb.voucher'), ('date', '>=', fields.Datetime.to_string(df)),
            ('date', '<=', fields.Datetime.to_string(dt + timedelta(days=1)))], order='date desc', limit=20000)
        vouchers = self.env['sb.voucher'].browse(set(msgs.mapped('res_id'))).exists()
        vmap = {v.id: v for v in vouchers}
        rows = []
        for m in msgs:
            v = vmap.get(m.res_id)
            changes = '; '.join('%s: %s → %s' % (t.field_id.field_description, t.old_value_char or t.old_value_float or '',
                                                  t.new_value_char or t.new_value_float or '')
                                for t in m.tracking_value_ids)
            text = changes or (html2plaintext(m.body or '').strip()) or m.subtype_id.name or ''
            if not self._search_ok(opts, v.name if v else '', m.author_id.name, text):
                continue
            rows.append({'id': 'msg%d' % m.id, 'parent': None, 'level': 0, 'kind': 'line', 'label': v.name if v else '',
                         'values': {'when': fields.Datetime.to_string(m.date)[:16], 'user': m.author_id.name or '',
                                    'voucher': v.name if v else '#%d' % m.res_id,
                                    'type': dict(v._fields['voucher_type']._description_selection(self.env)).get(v.voucher_type) if v else '',
                                    'what': text[:300]},
                         'open': {'model': 'sb.voucher', 'id': v.id} if v else None})
        if len(msgs) == 20000:
            rows.append({'id': 'cap', 'parent': None, 'level': 0, 'kind': 'note', 'warning': True,
                         'label': 'Only the latest 20,000 changes are listed: narrow the dates to see the rest.', 'values': {}})
        cols = [{'key': 'when', 'label': 'When', 'type': 'text'}, {'key': 'user', 'label': 'Who', 'type': 'text'},
                {'key': 'voucher', 'label': 'Voucher', 'type': 'text'}, {'key': 'type', 'label': 'Type', 'type': 'text'},
                {'key': 'what', 'label': 'What happened', 'type': 'text'}]
        return self._doc('audit_trail', opts, cols, rows, '%s  |  %s' % (self._period_name(df, dt), self._label_range_ec(df, dt)),
                         'Audit Trail')

    def _report_find_vouchers(self, opts):
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        dom = [('company_id', '=', self.env.company.id), ('date', '>=', df), ('date', '<=', dt)]
        if opts.get('state'):
            dom.append(('state', '=', opts['state']))
        s = (opts.get('search') or '').strip()
        if s:  # number, partner, reference, cheque / slip number, amount (25,500 or 25500.00)
            dom += list(self.env['sb.voucher']._sb_search_domain(s))
        vouchers = self.env['sb.voucher'].search(dom, order='date desc, id desc', limit=2000)
        rows = []
        for v in vouchers:
            rev = v.move_id.reversal_move_ids[:1]
            rows.append({'id': 'v%d' % v.id, 'parent': None, 'level': 0, 'kind': 'line', 'label': v.name,
                         'values': {'date': fields.Date.to_string(v.date), 'number': v.name,
                                    'type': dict(v._fields['voucher_type']._description_selection(self.env)).get(v.voucher_type),
                                    'partner': v.partner_id.name or '', 'ref': v.reference or '', 'memo': v.memo or '',
                                    'amount': v.amount_cash or v.amount_total or v.total_debit,
                                    'state': dict(v._fields['state']._description_selection(self.env)).get(v.state),
                                    'reversal': rev.name or '', 'reversal_date': fields.Date.to_string(rev.date) if rev else ''},
                         'open': {'model': 'sb.voucher', 'id': v.id}})
        # entries with no voucher behind them (Odoo 18's payments and receipts, brought over as entries): found too
        entries = self.env['sb.voucher']._sb_find_entries(s, df, dt) if opts.get('state') in (None, False, '', 'posted') else []
        for m in entries:
            rows.append({'id': 'm%d' % m.id, 'parent': None, 'level': 0, 'kind': 'line', 'label': m.name,
                         'values': {'date': fields.Date.to_string(m.date), 'number': m.name,
                                    'type': self.env._('Entry, %s', m.journal_id.display_name),
                                    'partner': m.partner_id.name or m.line_ids.partner_id[:1].name or '',
                                    'ref': m.ref or '', 'memo': '', 'amount': m.amount_total, 'state': 'Posted'},
                         'open': {'model': 'account.move', 'id': m.id}})
        cols = [{'key': 'date', 'label': 'Date', 'type': 'date'}, {'key': 'number', 'label': 'Number', 'type': 'text'},
                {'key': 'type', 'label': 'Type', 'type': 'text'}, {'key': 'partner', 'label': 'Customer / Vendor', 'type': 'text'},
                {'key': 'ref', 'label': 'Reference', 'type': 'text'}, {'key': 'memo', 'label': 'Description', 'type': 'text'},
                {'key': 'amount', 'label': 'Amount', 'type': 'monetary'}, {'key': 'state', 'label': 'Status', 'type': 'text'}]
        if opts.get('state') == 'void':
            cols += [{'key': 'reversal', 'label': 'Reversing entry', 'type': 'text'},
                     {'key': 'reversal_date', 'label': 'Reversed on', 'type': 'date'}]
        title = 'Voided Vouchers Register' if opts.get('state') == 'void' else 'Find Transactions'
        if len(vouchers) == 2000:
            rows.append({'id': 'cap', 'parent': None, 'level': 0, 'kind': 'note', 'warning': True,
                         'label': 'Only the latest 2,000 vouchers are listed: narrow the dates or the search.', 'values': {}})
        found = len(vouchers) + len(entries)
        return self._doc('find_vouchers', opts, cols, rows, '%s  |  %d found' % (self._period_name(df, dt), found)
                         if s else '%s  |  %d transactions' % (self._period_name(df, dt), found), title)

    # ================================================================== Customer statement (form)
    def _report_partner_statement(self, opts):
        doc = self._report_partner_ledger(dict(opts, hide_zero=True))
        rows = []
        for r in doc['rows']:
            if r.get('lazy'):
                rows.append(dict(r, lazy=None, expandable=True, page_break=bool(rows)))
                for c in self._lazy_partner_lines(opts, r['lazy']):
                    c['parent'] = r['id']
                    rows.append(c)
                ag = self._report_aged(dict(opts, partner_ids=[r['lazy']['partner_id']] if r['lazy']['partner_id'] else []))
                arow = next((x for x in ag['rows'] if x['kind'] == 'line'), None)
                if arow:
                    buckets = ', '.join('%s: %s' % (c['label'], '{:,.2f}'.format(arow['values'].get(c['key'], 0.0)))
                                        for c in ag['columns'] if c['type'] == 'monetary' and c['key'] != 'total'
                                        and abs(arow['values'].get(c['key'], 0.0)) > 0.005)
                    if arow['values'].get('fc'):
                        buckets += '  |  owed in %s' % arow['values']['fc']
                    rows.append({'id': 'age%s' % r['id'], 'parent': r['id'], 'level': 1, 'kind': 'note',
                                 'label': 'Ageing', 'values': {'name': 'Ageing: ' + (buckets or 'nothing overdue')}})
            else:
                rows.append(r)
        doc['rows'] = rows
        doc['key'] = 'partner_statement'
        doc['title'] = 'Customer Statement' if (opts.get('partner_type') or 'customer') == 'customer' else 'Vendor Statement'
        return doc


class SbReportCompanyChecks(models.AbstractModel):
    _inherit = 'sb.report'

    def _report_daily_register(self, opts):
        doc = self._report_journal(opts)
        days = {}
        rows = []
        for r in doc['rows']:
            if r['kind'] == 'group':
                day = r['values']['date']
                if day not in days:
                    days[day] = {'id': 'd' + day, 'parent': None, 'level': 0, 'kind': 'section', 'expandable': True,
                                 'label': '%s  (%s)' % (fields.Date.to_date(day).strftime('%A %d %B %Y'), r['values']['date_ec']),
                                 'values': {'date': day, 'debit': 0.0, 'credit': 0.0}}
                    rows.append(days[day])
                days[day]['values']['debit'] += r['values']['debit']
                days[day]['values']['credit'] += r['values']['credit']
                r = dict(r, parent=days[day]['id'], level=1)
            elif r['kind'] == 'detail':
                r = dict(r, level=2)
            rows.append(r)
        doc['rows'] = rows
        doc['key'] = 'daily_register'
        doc['title'] = 'Daily Register'
        return doc

    def _report_accounting_review(self, opts):
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        company = self.env.company
        V = self.env['sb.voucher']
        AML = self.env['account.move.line']
        today = fields.Date.context_today(self)
        checks = []

        def add(key, label, count, amount=None, advice='', drill=None, severity='warn'):
            checks.append({'id': key, 'parent': None, 'level': 0, 'kind': 'line', 'label': label,
                           'values': {'check': label, 'count': count, 'amount': amount, 'advice': advice,
                                      'status': 'OK' if not count else ('Fix' if severity == 'bad' else 'Review')},
                           'warning': bool(count) and severity == 'bad', 'drill': drill if count else None})

        drafts = V.search([('state', '=', 'draft'), ('date', '<=', dt), ('company_id', '=', company.id),
                           '|', ('line_ids', '!=', False), ('apply_ids.amount_applied', '!=', 0)])
        add('drafts', 'Vouchers saved but not posted', len(drafts), sum(drafts.mapped('amount_total')),
            'Post them or delete them: they are not in the ledger.', {'report': 'find_vouchers', 'state': 'draft'}, 'bad')
        overdue = AML.search([('company_id', '=', company.id), ('parent_state', '=', 'posted'), ('reconciled', '=', False),
                              ('account_id.account_type', '=', 'asset_receivable'), ('amount_residual', '>', 0),
                              ('date_maturity', '<', dt - timedelta(days=90))])
        add('overdue90', 'Customer invoices more than 90 days overdue', len(overdue), sum(overdue.mapped('amount_residual')),
            'Follow up, or provide for expected credit losses (IFRS 9).', {'report': 'aged', 'partner_type': 'customer'})
        # money paid or received in advance and not yet applied: the balances, as at the date, of the advance accounts kept
        # per customer / vendor (the company's advance accounts and every partner account named as an advance)
        adv_accounts = self.env['account.account'].search([('company_ids', 'in', company.id), ('sb_partner_side', '!=', False),
                                                          ('name', 'ilike', 'advance')])
        adv_accounts |= company.sb_customer_advance_account_id | company.sb_vendor_advance_account_id
        adv = [v['balance'] for v in self._sums(self._base_domain(opts, None, dt) + [('account_id', 'in', adv_accounts.ids)]).values()
               if abs(v['balance']) > 0.005]
        add('advances', 'Advance accounts not yet cleared to invoices or bills', len(adv), sum(abs(b) for b in adv),
            'Apply them on the next receipt or payment.', {'report': 'open_credits', 'partner_type': 'vendor'})
        stale = self.env['sb.voucher.cash'].search([('cheque_state', '=', 'open'), ('direction', '=', 'in'),
                                                    ('company_id', '=', company.id),
                                                    '|', ('cheque_date', '<', today - timedelta(days=7)),
                                                    '&', ('cheque_date', '=', False), ('voucher_date', '<', today - timedelta(days=7))])
        add('cheques', 'Received cheques still not deposited after a week', len(stale), sum(stale.mapped('amount')),
            'Deposit them (Banking > Cheques).', {'report': 'cheque_register', 'cheque_state': 'open', 'direction': 'in'}, 'bad')
        unmapped = self.env['account.account'].search([('company_ids', 'in', company.id), ('sb_ifrs_line_id', '=', False)])
        used = set(AML.search([('account_id', 'in', unmapped.ids)], limit=1000).mapped('account_id').ids)
        add('ifrs', 'Accounts with postings but no IFRS line item', len(used), None,
            'Map them in Setup > IFRS Mapping Check, or the statements will flag them.', {'report': 'chart'}, 'bad')
        vat_lines = AML.search([('company_id', '=', company.id), ('parent_state', '=', 'posted'), ('date', '>=', df),
                                ('date', '<=', dt), ('tax_line_id', '!=', False)])
        no_tin = vat_lines.move_id.filtered(lambda m: m.partner_id and not m.partner_id.commercial_partner_id.vat)
        add('tin', 'VAT documents whose customer / vendor has no TIN', len(no_tin), None,
            'Add the TIN on the contact: needed for the VAT registers.', {'report': 'vat_register', 'side': 'sale'})
        # the withholding registers' own lines (vouchers and entries brought over alike) without the receipt number
        for side, label in (('payable', 'Withholding without a receipt number'),
                            ('received', "Withholding by customers without their receipt")):
            missing = [r for r in self._report_wht_register(dict(opts, side=side, search=''))['rows']
                       if r['kind'] == 'line' and not r['values'].get('cert')]
            add('wht' if side == 'payable' else 'wht_received', label, len(missing),
                sum(r['values']['amount'] for r in missing),
                'Enter the withholding receipt number on the voucher or the entry.', {'report': 'wht_register', 'side': side})
        # cash and bank as at the report date, not today
        cash = self._sums(self._base_domain(opts, None, dt) + [('account_id.account_type', '=', 'asset_cash')])
        neg = [v['balance'] for v in cash.values() if v['balance'] < -0.005]
        add('negcash', 'Bank or cash accounts below zero', len(neg), sum(neg),
            'A cash account cannot be negative: look for a missing receipt or a wrong account. An overdraft shows under '
            'current liabilities on the statement of financial position.', {'report': 'cash_book', 'date_to': opts['date_to']}, 'bad')
        # suspense and clearing accounts, found by name as on the statement of financial position, and the company's own
        susp_accounts = self.env['account.account'].search([('company_ids', 'in', company.id), '|',
                                                           ('name', 'ilike', 'suspense'), ('name', 'ilike', 'clearing')])
        susp_accounts |= company.account_journal_suspense_account_id | self.env['account.journal'].search(
            [('company_id', '=', company.id)]).suspense_account_id
        susp = [v['balance'] for v in self._sums(self._base_domain(opts, None, dt)
                                                 + [('account_id', 'in', susp_accounts.ids)]).values() if abs(v['balance']) > 0.005]
        add('suspense', 'Suspense and clearing accounts with a balance', len(susp), sum(abs(b) for b in susp),
            'Clear them before closing.', {'report': 'trial_balance', 'date_to': opts['date_to']})
        cols = [{'key': 'check', 'label': 'Check', 'type': 'text'}, {'key': 'status', 'label': 'Status', 'type': 'text'},
                {'key': 'count', 'label': 'Items', 'type': 'number_int'}, {'key': 'amount', 'label': 'Amount', 'type': 'monetary'},
                {'key': 'advice', 'label': 'What to do', 'type': 'text'}]
        doc = self._doc('accounting_review', opts, cols, checks,
                        '%s  |  %s' % (self._period_name(df, dt), self._label_range_ec(df, dt)), 'Internal Accounting Review')
        return doc


class SbReportBankRec(models.AbstractModel):
    _inherit = 'sb.report'

    def _report_bank_rec(self, opts):
        Rec = self.env['sb.bank.rec']
        # without a reconciliation chosen: the latest one as at the report's date (a pack for a past period prints that
        # period's reconciliation, never a later one)
        rec = Rec.browse(opts['rec_id']).exists() if opts.get('rec_id') else Rec.search(
            [('company_id', '=', self.env.company.id), ('date', '<=', opts['date_to'])], order='date desc, id desc', limit=1)
        cols = [{'key': 'date', 'label': 'Date', 'type': 'date'}, {'key': 'number', 'label': 'Voucher', 'type': 'text'},
                {'key': 'ref', 'label': 'Reference', 'type': 'text'}, {'key': 'name', 'label': 'Description', 'type': 'text'},
                {'key': 'amount', 'label': 'Amount', 'type': 'monetary'}]
        if not rec:
            return self._doc('bank_rec', opts, cols, [], 'No reconciliation on or before %s: Banking > Reconcile Accounts'
                             % self._d(opts['date_to']).strftime('%d %b %Y'), 'Account Reconciliation')
        rows = []

        def line(rid, label, amount, kind='line', level=0, parent=None):
            rows.append({'id': rid, 'parent': parent, 'level': level, 'kind': kind, 'label': label,
                         'values': {'name': label, 'amount': amount}})

        line('stmt', 'Balance per bank statement', rec.statement_balance, 'section')
        dep = rec.line_ids.filtered(lambda l: not l.cleared and l.amount > 0)
        rows.append({'id': 'dep', 'parent': None, 'level': 0, 'kind': 'group', 'label': 'Add: deposits in transit',
                     'values': {'name': 'Add: deposits in transit', 'amount': sum(dep.mapped('amount'))}, 'expandable': True})
        for l in dep:
            rows.append({'id': 'd%d' % l.id, 'parent': 'dep', 'level': 1, 'kind': 'detail', 'label': l.move_name,
                         'values': {'date': fields.Date.to_string(l.date), 'number': l.move_name, 'ref': l.ref or '',
                                    'name': l.name or '', 'amount': l.amount}})
        out = rec.line_ids.filtered(lambda l: not l.cleared and l.amount < 0)
        rows.append({'id': 'out', 'parent': None, 'level': 0, 'kind': 'group', 'label': 'Less: outstanding cheques and withdrawals',
                     'values': {'name': 'Less: outstanding cheques and withdrawals', 'amount': sum(out.mapped('amount'))}, 'expandable': True})
        for l in out:
            rows.append({'id': 'o%d' % l.id, 'parent': 'out', 'level': 1, 'kind': 'detail', 'label': l.move_name,
                         'values': {'date': fields.Date.to_string(l.date), 'number': l.move_name,
                                    'ref': ('Cheque %s' % l.cheque_row_id.reference if l.cheque_row_id.reference else l.ref) or '',
                                    'name': l.name or '', 'amount': l.amount}})
        adjusted = rec.statement_balance + sum(dep.mapped('amount')) + sum(out.mapped('amount'))
        line('adj', 'Adjusted bank balance', adjusted, 'total')
        late = rec.late_amount if 'late_amount' in rec._fields else 0.0
        if abs(late) >= 0.005:
            # entries dated inside the reconciled period but posted after it was finished: the next reconciliation
            # carries them as outstanding
            line('late', 'Add: entries posted after reconciling, dated in this period', late)
            adjusted += late
            line('adj2', 'Adjusted bank balance with those entries', adjusted, 'total')
        line('gl', 'Balance per books', rec.gl_balance, 'total')
        diff = rec.currency_id.round(adjusted - rec.gl_balance)
        done = rec.state == 'done'
        rows.append({'id': 'diff', 'parent': None, 'level': 0, 'kind': 'note' if abs(diff) < 0.005 else 'total',
                     'warning': abs(diff) >= 0.005, 'label': 'Difference',
                     'values': {'name': 'Difference' + (' (reconciled)' if abs(diff) < 0.005 and done else ''), 'amount': diff}})
        if rec.carried_diff:
            # a difference the previous reconciliation left: shown on its own, with how it was cleared
            label = ('Carried from %s, cleared by %s' % (rec.carry_from_id.name, ', '.join(rec.carry_voucher_ids.mapped('name')))
                     if not rec.carried_open else 'of which carried from %s, not yet cleared' % rec.carry_from_id.name)
            rows.append({'id': 'carried', 'parent': None, 'level': 1, 'kind': 'note', 'warning': bool(rec.carried_open),
                         'label': label, 'values': {'name': label, 'amount': -rec.carried_diff}})
        if not done and abs(rec.difference) >= 0.005:
            label = 'Still to tick: statement balance less the cleared balance'
            rows.append({'id': 'unticked', 'parent': None, 'level': 0, 'kind': 'note', 'warning': True, 'label': label,
                         'values': {'name': label, 'amount': rec.difference}})
        doc = self._doc('bank_rec', opts, cols, rows, '%s  |  statement of %s (%s)  |  %s' % (
            rec.account_id.display_name, rec.date.strftime('%d %B %Y'), rec.date_ec,
            'Reconciled' if done else 'In progress'), 'Account Reconciliation')
        doc['rec_state'] = rec.state
        doc['rec_difference'] = rec.difference
        return doc


class SbReportPayroll(models.AbstractModel):
    _inherit = 'sb.report'

    def _payroll_lines(self, opts):
        dom = [('company_id', '=', self.env.company.id), ('date', '>=', opts['date_from']), ('date', '<=', opts['date_to'])]
        if opts.get('posted_only', True):
            dom.append(('run_id.state', 'in', ('posted', 'paid')))
        return self.env['sb.payroll.line'].search(dom, order='date, id')

    def _report_payroll_register(self, opts):
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        keys = ('basic', 'taxable_allowance', 'overtime', 'nontaxable_allowance', 'gross', 'income_tax', 'pension_ee',
                'other_deduction', 'net', 'pension_er')
        rows, grand = [], defaultdict(float)
        for run in self._payroll_lines(opts).run_id.sorted('date'):
            lines = run.line_ids
            tot = {k: sum(lines.mapped(k)) for k in keys}
            for k, v in tot.items():
                grand[k] += v
            rid = 'run%d' % run.id
            rows.append({'id': rid, 'parent': None, 'level': 0, 'kind': 'group', 'label': run.name, 'expandable': True,
                         'values': {'name': run.name, **tot}, 'open': {'model': 'sb.payroll.run', 'id': run.id}})
            for l in lines:
                if not self._search_ok(opts, l.employee_id.name, l.employee_id.tin):
                    continue
                rows.append({'id': 'pl%d' % l.id, 'parent': rid, 'level': 1, 'kind': 'line', 'label': l.employee_id.name,
                             'values': {'name': l.employee_id.name, **{k: l[k] for k in keys}}})
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total', 'values': {'name': 'Total', **dict(grand)}})
        cols = [{'key': 'name', 'label': 'Month / Employee', 'type': 'text'}, {'key': 'basic', 'label': 'Basic', 'type': 'monetary'},
                {'key': 'taxable_allowance', 'label': 'Taxable allow.', 'type': 'monetary'},
                {'key': 'overtime', 'label': 'Overtime', 'type': 'monetary'},
                {'key': 'nontaxable_allowance', 'label': 'Non-taxable allow.', 'type': 'monetary'},
                {'key': 'gross', 'label': 'Gross', 'type': 'monetary'}, {'key': 'income_tax', 'label': 'PAYE', 'type': 'monetary'},
                {'key': 'pension_ee', 'label': 'Pension 7%', 'type': 'monetary'},
                {'key': 'other_deduction', 'label': 'Other deductions', 'type': 'monetary'},
                {'key': 'net', 'label': 'Net pay', 'type': 'monetary'}, {'key': 'pension_er', 'label': 'Employer 11%', 'type': 'monetary'}]
        return self._doc('payroll_register', opts, cols, rows, '%s  |  %s' % (self._period_name(df, dt), self._label_range_ec(df, dt)),
                         'Payroll Register')

    def _per_employee(self, opts, keys):
        agg = defaultdict(lambda: defaultdict(float))
        emps = {}
        for l in self._payroll_lines(opts):
            e = l.employee_id
            emps[e.id] = e
            for k in keys:
                agg[e.id][k] += l[k]
        return emps, agg

    def _report_paye_declaration(self, opts):
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        emps, agg = self._per_employee(opts, ('taxable', 'income_tax', 'gross'))
        rows, grand = [], defaultdict(float)
        for i, (eid, e) in enumerate(sorted(emps.items(), key=lambda kv: kv[1].name)):
            v = agg[eid]
            for k in ('taxable', 'income_tax'):
                grand[k] += v[k]
            rows.append({'id': 'e%d' % eid, 'parent': None, 'level': 0, 'kind': 'line', 'label': e.name, 'warning': not e.tin,
                         'values': {'no': str(i + 1), 'name': e.name, 'tin': e.tin or 'TIN missing', 'start': fields.Date.to_string(e.start_date) if e.start_date else '',
                                    'taxable': v['taxable'], 'income_tax': v['income_tax']},
                         'open': {'model': 'sb.employee', 'id': eid}})
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total', 'values': {'name': 'Total', **dict(grand)}})
        cols = [{'key': 'no', 'label': '#', 'type': 'text'}, {'key': 'name', 'label': 'Employee', 'type': 'text'},
                {'key': 'tin', 'label': 'TIN', 'type': 'text'}, {'key': 'start', 'label': 'Start date', 'type': 'date'},
                {'key': 'taxable', 'label': 'Taxable income', 'type': 'monetary'}, {'key': 'income_tax', 'label': 'Tax withheld', 'type': 'monetary'}]
        company = self.env.company
        if company.country_id.code == 'UG':
            # URA monthly PAYE return: employee TIN, name, gross emoluments, chargeable income, PAYE
            for r in rows:
                if r['kind'] == 'line':
                    r['values']['gross'] = agg[int(r['id'][1:])]['gross']
            grand['gross'] = sum(agg[e]['gross'] for e in emps)
            rows[-1]['values']['gross'] = grand['gross']
            cols = [{'key': 'no', 'label': '#', 'type': 'text'}, {'key': 'tin', 'label': 'Employee TIN', 'type': 'text'},
                    {'key': 'name', 'label': 'Employee name', 'type': 'text'}, {'key': 'gross', 'label': 'Gross emoluments', 'type': 'monetary'},
                    {'key': 'taxable', 'label': 'Chargeable income', 'type': 'monetary'}, {'key': 'income_tax', 'label': 'PAYE deducted', 'type': 'monetary'}]
            return self._doc('paye_declaration', opts, cols, rows, '%s  |  Employer TIN %s' % (self._period_name(df, dt), company.vat or 'not set'),
                             'PAYE Return (URA)')
        return self._doc('paye_declaration', opts, cols, rows, '%s  |  %s  |  Employer TIN %s' % (
            self._period_name(df, dt), self._label_range_ec(df, dt), company.vat or 'not set'), 'Employment Income Tax Declaration')

    def _report_poessa_declaration(self, opts):
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        company = self.env.company
        ug = company.country_id.code in ('UG', 'KE')
        emps, agg = self._per_employee(opts, ('basic', 'gross', 'pension_ee', 'pension_er'))
        rows, grand = [], defaultdict(float)
        for i, (eid, e) in enumerate(sorted(emps.items(), key=lambda kv: kv[1].name)):
            v = dict(agg[eid])
            v['total'] = v['pension_ee'] + v['pension_er']
            for k, val in v.items():
                grand[k] += val
            rows.append({'id': 'e%d' % eid, 'parent': None, 'level': 0, 'kind': 'line', 'label': e.name, 'warning': not e.pension_no,
                         'values': {'no': str(i + 1), 'name': e.name, 'pension_no': e.pension_no or 'number missing', 'tin': e.tin or '', **v},
                         'open': {'model': 'sb.employee', 'id': eid}})
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total', 'values': {'name': 'Total', **dict(grand)}})
        cols = [{'key': 'no', 'label': '#', 'type': 'text'}, {'key': 'name', 'label': 'Employee', 'type': 'text'},
                {'key': 'pension_no', 'label': 'Pension no.', 'type': 'text'}, {'key': 'tin', 'label': 'TIN', 'type': 'text'},
                {'key': 'basic', 'label': 'Basic salary', 'type': 'monetary'},
                {'key': 'pension_ee', 'label': 'Employee %g%%' % company.sb_pension_employee, 'type': 'monetary'},
                {'key': 'pension_er', 'label': 'Employer %g%%' % company.sb_pension_employer, 'type': 'monetary'},
                {'key': 'total', 'label': 'Total %g%%' % (company.sb_pension_employee + company.sb_pension_employer), 'type': 'monetary'}]
        if ug:
            # NSSF schedule: member number, name, gross wages, 5% + 10%
            cols = [c for c in cols if c['key'] not in ('basic', 'tin')]
            cols[3:3] = [{'key': 'gross', 'label': 'Gross wages', 'type': 'monetary'}]
            for c in cols:
                if c['key'] == 'pension_no':
                    c['label'] = 'NSSF no.'
            return self._doc('poessa_declaration', opts, cols, rows, '%s  |  Employer NSSF schedule' % self._period_name(df, dt),
                             'NSSF Contributions Schedule')
        return self._doc('poessa_declaration', opts, cols, rows, '%s  |  %s' % (self._period_name(df, dt), self._label_range_ec(df, dt)),
                         'POESSA Pension Declaration')

    def _report_employee_earnings(self, opts):
        df, dt = self._d(opts['date_from']), self._d(opts['date_to'])
        keys = ('gross', 'taxable', 'income_tax', 'pension_ee', 'pension_er', 'net')
        emps, agg = self._per_employee(opts, keys)
        rows, grand = [], defaultdict(float)
        for eid, e in sorted(emps.items(), key=lambda kv: kv[1].name):
            v = agg[eid]
            for k in keys:
                grand[k] += v[k]
            rows.append({'id': 'e%d' % eid, 'parent': None, 'level': 0, 'kind': 'line', 'label': e.name,
                         'values': {'name': e.name, **dict(v)}, 'open': {'model': 'sb.employee', 'id': eid}})
        rows.append({'id': 'total', 'parent': None, 'level': 0, 'kind': 'total', 'label': 'Total', 'values': {'name': 'Total', **dict(grand)}})
        cols = [{'key': 'name', 'label': 'Employee', 'type': 'text'}] + [
            {'key': k, 'label': lab, 'type': 'monetary'} for k, lab in (('gross', 'Gross'), ('taxable', 'Taxable'), ('income_tax', 'PAYE'),
                                                                       ('pension_ee', 'Pension 7%'), ('pension_er', 'Employer 11%'), ('net', 'Net pay'))]
        return self._doc('employee_earnings', opts, cols, rows, '%s  |  %s' % (self._period_name(df, dt), self._label_range_ec(df, dt)),
                         'Yearly Earnings')

    def _report_employee_list(self, opts):
        rows = []
        for e in self.env['sb.employee'].search([('company_id', '=', self.env.company.id)], order='name'):
            if not self._search_ok(opts, e.name, e.tin, e.job):
                continue
            rows.append({'id': 'e%d' % e.id, 'parent': None, 'level': 0, 'kind': 'line', 'label': e.name,
                         'values': {'name': e.name, 'job': e.job or '', 'tin': e.tin or '', 'pension_no': e.pension_no or '',
                                    'start': fields.Date.to_string(e.start_date) if e.start_date else '',
                                    'basic': e.basic_salary, 'allow': e.taxable_allowance + e.nontaxable_allowance},
                         'open': {'model': 'sb.employee', 'id': e.id}})
        cols = [{'key': 'name', 'label': 'Employee', 'type': 'text'}, {'key': 'job', 'label': 'Position', 'type': 'text'},
                {'key': 'tin', 'label': 'TIN', 'type': 'text'}, {'key': 'pension_no', 'label': 'Pension no.', 'type': 'text'},
                {'key': 'start', 'label': 'Start date', 'type': 'date'}, {'key': 'basic', 'label': 'Basic salary', 'type': 'monetary'},
                {'key': 'allow', 'label': 'Allowances', 'type': 'monetary'}]
        return self._doc('employee_list', opts, cols, rows, '%d employees' % len(rows), 'Employee List')

    @api.model
    def payslip_doc(self, run):
        """All payslips of a run: one page per employee."""
        label = run._period()[1]
        rows = []
        for i, l in enumerate(run.line_ids):
            e = l.employee_id
            gid = 'p%d' % l.id
            rows.append({'id': gid, 'parent': None, 'level': 0, 'kind': 'section', 'page_break': i > 0,
                         'label': '%s  -  %s' % (e.name, e.job or ''),
                         'values': {'item': '%s  -  %s' % (e.name, e.job or ''), 'info': 'TIN %s  |  Pension no. %s' % (e.tin or '-', e.pension_no or '-')}})
            for item, amt in (('Basic salary', l.basic), ('Taxable allowances', l.taxable_allowance), ('Overtime', l.overtime),
                              ('Non-taxable allowances', l.nontaxable_allowance)):
                if amt:
                    rows.append({'id': '%s_%s' % (gid, item), 'parent': gid, 'level': 1, 'kind': 'line', 'label': item,
                                 'values': {'item': item, 'earn': amt}})
            rows.append({'id': gid + '_g', 'parent': gid, 'level': 1, 'kind': 'total', 'label': 'Gross pay', 'values': {'item': 'Gross pay', 'earn': l.gross}})
            for item, amt in (('Employment income tax', l.income_tax), ('Pension 7%', l.pension_ee), ('Other deductions', l.other_deduction)):
                if amt:
                    rows.append({'id': '%s_%s' % (gid, item), 'parent': gid, 'level': 1, 'kind': 'line', 'label': item,
                                 'values': {'item': item, 'ded': amt}})
            rows.append({'id': gid + '_n', 'parent': gid, 'level': 1, 'kind': 'total', 'label': 'Net pay',
                         'values': {'item': 'Net pay', 'net': l.net}})
            rows.append({'id': gid + '_er', 'parent': gid, 'level': 1, 'kind': 'note', 'label': 'Employer pension 11%',
                         'values': {'item': 'Employer pension 11%% (paid by the company): %s' % '{:,.2f}'.format(l.pension_er)}})
        cols = [{'key': 'item', 'label': 'Payslip', 'type': 'text'}, {'key': 'info', 'label': '', 'type': 'text'},
                {'key': 'earn', 'label': 'Earnings', 'type': 'monetary'}, {'key': 'ded', 'label': 'Deductions', 'type': 'monetary'},
                {'key': 'net', 'label': 'Net', 'type': 'monetary'}]
        doc = self._doc('payslips', {'date_from': fields.Date.to_string(run.date_from), 'date_to': fields.Date.to_string(run.date)},
                        cols, rows, '%s  |  paid %s' % (label, run.pay_date.strftime('%d %B %Y') if run.pay_date else 'not yet'),
                        'Payslips %s' % label)
        doc['signatures'] = False
        return doc
