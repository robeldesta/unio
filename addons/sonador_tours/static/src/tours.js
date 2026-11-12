/** @odoo-module **/
import { registry } from "@web/core/registry";

/** The tours. Each walks a person through doing the real thing, on their own data. */
const T = registry.category("unio_tours");
const NEW = { sel: ".o_list_button_add, .o-kanban-button-new, .o_form_button_create", advance: "click" };
const f = (name) => `.o_field_widget[name='${name}'] input, .o_field_widget[name='${name}'] textarea, .o_field_widget[name='${name}'] select, .o_field_widget[name='${name}']`;
const addLine = (name) => ({ sel: `div[name='${name}'] .o_field_x2many_list_row_add a`, advance: "click" });
const SAVE = { sel: ".o_form_button_save", advance: "click" };

T.add("welcome", {
    name: "Welcome to Unio", app: "Start here", minutes: 1, sequence: 1,
    steps: [
        { title: "Welcome to Unio", body: "One minute to find your way around. Every app you use is in one place, and they all share the same books, stock and people." },
        { sel: ".mk_apps_sidebar_menu, .o_navbar_apps_menu button", title: "Your apps", body: "Open any app from here: Accounting, Sales, Inventory, People... Only the apps your kind of business needs are switched on." },
        { sel: ".o_saas_help_wrap > button", optional: true, title: "Help, any time", body: "Search the guides, get a direct answer, start a tour like this one, or ask us. We answer here and by email." },
        { sel: ".o_ai_btn", optional: true, title: "Unio AI", body: "Ask about your business in plain words: \"who owes us the most?\", \"sales this month by product\". It drafts records for you to check." },
        { sel: ".o_saas_refer_btn", optional: true, title: "Refer and earn", body: "Send your link to another business. When they subscribe you both get a discount." },
        { sel: ".o_user_menu", title: "You", body: "Your preferences, language and password are here. That's it. Pick a tour from the ? whenever you start something new." },
    ],
});

T.add("invoice", {
    name: "Your first invoice", app: "Accounting", minutes: 2, sequence: 10,
    steps: [
        { action: "sonador_books.action_sb_sale", clear: true, title: "Sales invoices", body: "Every invoice you send is here. Let's make one." },
        { ...NEW, title: "New invoice", body: "Click New." },
        { sel: f("partner_id"), advance: "input", title: "The customer", body: "Type the customer's name and pick them. A new name can be created on the spot." },
        { sel: f("date"), title: "The date", body: "Today by default. In Ethiopia the invoice number is the fiscal receipt number (FS No.) or the Ministry e-invoice reference: type it in the box beside the date." },
        { ...addLine("line_ids"), title: "What you sold", body: "Add a line: the item or service, how many, and the price. VAT and withholding are worked out for you." },
        { sel: "div[name='line_ids'] .o_data_row", place: "above", title: "One line per item", body: "Pick the item, then the quantity and price. Add as many lines as you need, then press Next." },
        { sel: "button[name='action_post_close']", advance: "click", title: "Post it", body: "Post & Close writes it to the books: the customer owes you, sales and VAT go up. In Ethiopia the customer's invoice itself comes from your fiscal cash register or the Ministry's e-invoice." },
        { title: "Done", body: "That's an invoice. Next: \"Receive a payment\" to record the money when it comes in." },
    ],
});

T.add("receipt", {
    name: "Receive a payment", app: "Accounting", minutes: 2, sequence: 11,
    steps: [
        { action: "sonador_books.action_sb_receipt", clear: true, title: "Receipts", body: "Money coming in from customers is recorded here." },
        { ...NEW, title: "New receipt", body: "Click New." },
        { sel: f("partner_id"), advance: "input", title: "Received from", body: "Pick the customer. Their open invoices appear below." },
        { sel: f("received_amount"), title: "How much", body: "The amount you received." },
        { sel: "div[name='apply_ids']", place: "above", title: "Which invoices it pays", body: "Tick or type the amount against each invoice it settles. Part payments are fine." },
        { sel: "div[name='cash_line_ids']", place: "above", optional: true, title: "Cash, bank or cheque", body: "Where the money went: the cash box, CBE, telebirr, or a cheque with its number and date." },
        { sel: "button[name='action_post_close']", advance: "click", title: "Post it", body: "The customer's balance goes down and your cash or bank goes up." },
        { title: "Done", body: "Check who still owes you in Reports, Aged receivables." },
    ],
});

T.add("bill", {
    name: "Record a supplier bill", app: "Accounting", minutes: 2, sequence: 12,
    steps: [
        { action: "sonador_books.action_sb_bill", clear: true, title: "Bills", body: "What you owe suppliers. Record a bill when it arrives." },
        { ...NEW, title: "New bill", body: "Click New." },
        { sel: f("partner_id"), advance: "input", title: "The supplier", body: "Pick the supplier." },
        { ...addLine("line_ids"), title: "What you bought", body: "One line per item or expense, with the amount. Withholding is worked out where it applies." },
        { sel: "button[name='action_post_close']", advance: "click", title: "Post it", body: "The supplier's balance goes up. Pay it later with a Payment." },
    ],
});

T.add("reports", {
    name: "Find a report", app: "Accounting", minutes: 1, sequence: 13,
    steps: [
        { action: "sonador_books.action_sb_report_center", clear: true, title: "Reports", body: "Every report in one place: profit and loss, balance sheet, VAT, who owes you, stock, payroll..." },
        { sel: "input[placeholder^='Find a report']", advance: "input", title: "Search", body: "Type what you want, e.g. \"VAT\" or \"aged\"." },
        { title: "Open, filter, print", body: "Open a report, change the dates, then print it or export to Excel. Every number opens the entries behind it." },
    ],
});

T.add("quotation", {
    name: "Quotation to delivery", app: "Sales", minutes: 2, sequence: 20,
    steps: [
        { action: "sonador_sales.action_sb_quotations", clear: true, title: "Quotations", body: "Offers to customers. When they accept, a quotation becomes an order." },
        { ...NEW, title: "New quotation", body: "Click New." },
        { sel: f("partner_id"), advance: "input", title: "The customer", body: "Pick the customer." },
        { ...addLine("order_line"), title: "Products", body: "Add what they are buying. Prices come from your price list." },
        { sel: "button[name='action_quotation_send']", title: "Send it", body: "Email the quotation as a PDF, or print it." },
        { sel: "button[name='action_confirm']", advance: "click", title: "They said yes", body: "Confirm turns it into an order. Stock is reserved and the delivery is ready in Inventory." },
        { title: "Then", body: "Deliver from Inventory, then invoice from the order. Everything stays linked." },
    ],
});

T.add("purchase", {
    name: "Buy from a supplier", app: "Purchase", minutes: 2, sequence: 30,
    steps: [
        { action: "sonador_purchase.action_sb_rfq", clear: true, title: "Requests for quotation", body: "Ask suppliers for prices, then confirm the one you take." },
        { ...NEW, title: "New request", body: "Click New." },
        { sel: f("partner_id"), advance: "input", title: "The supplier", body: "Pick the supplier." },
        { ...addLine("order_line"), title: "What you need", body: "Add the products and quantities." },
        { sel: "button[name='action_rfq_send']", title: "Send it", body: "Email it to the supplier." },
        { sel: "button[name='button_confirm']", advance: "click", title: "Confirm the order", body: "It becomes a purchase order. Receive the goods in Inventory (a GRN), then record the bill." },
    ],
});

T.add("grn", {
    name: "Receive goods (GRN)", app: "Inventory", minutes: 2, sequence: 40,
    steps: [
        { action: "sonador_inventory.action_sb_grn", clear: true, title: "Goods received", body: "Every delivery from a supplier gets a GRN. Stock goes up at cost." },
        { ...NEW, title: "New GRN", body: "Click New, or open one made from a purchase order." },
        { sel: f("partner_id"), advance: "input", title: "From whom", body: "Pick the supplier." },
        { ...addLine("line_ids"), title: "What arrived", body: "The items and quantities you actually received." },
        { sel: "button[name='action_post']", title: "Post it", body: "Stock and its value go up. Then \"Create bill\" makes the supplier bill from it." },
    ],
});

T.add("count", {
    name: "Count your stock", app: "Inventory", minutes: 2, sequence: 41,
    steps: [
        { action: "sonador_inventory.action_sb_stock_count", clear: true, title: "Stock counts", body: "Count what is on the shelf; Unio posts the difference." },
        { ...NEW, title: "New count", body: "Click New." },
        { title: "Count and post", body: "Load the items, type what you counted, and post. Shortages and surpluses go to the books at cost." },
    ],
});

T.add("employee", {
    name: "Hire an employee", app: "People", minutes: 1, sequence: 50,
    steps: [
        { action: "sonador_people.action_people_directory", clear: true, title: "Your people", body: "Everyone who works for you, with their job, pay and papers." },
        { sel: "button.btn-primary", text: "hire", advance: "click", title: "Hire someone", body: "Click Hire." },
        { sel: "input[placeholder^='e.g. Abebe']", advance: "input", title: "Their name", body: "Full name as on their ID." },
        { title: "Job and pay", body: "Start date, position, department, salary and allowances. Payroll, leave and attendance pick them up from here. Click Hire to finish." },
    ],
});

T.add("payroll", {
    name: "Run payroll", app: "Payroll", minutes: 2, sequence: 51,
    steps: [
        { action: "sonador_books.action_sb_payroll", clear: true, title: "Payroll", body: "One run per month. Income tax, pension and the payslips are worked out for you." },
        { ...NEW, title: "New run", body: "Click New." },
        { sel: "button[name='action_load']", advance: "click", title: "Load employees", body: "Brings in everyone with their salary, allowances and attendance." },
        { sel: "button[name='action_post']", title: "Check, then post", body: "Check the lines, then Post. Salaries, tax and pension owed go to the books." },
        { title: "Pay", body: "Record the payment from the bank, and print the payslips and the bank letter." },
    ],
});

T.add("crm", {
    name: "Track a deal", app: "CRM", minutes: 1, sequence: 60,
    steps: [
        { action: "sonador_crm.action_crm_deals", clear: true, title: "Deals", body: "Every sale you are chasing, by stage. Drag a card when it moves forward." },
        { sel: "button.btn-primary", text: "deal", advance: "click", title: "New deal", body: "Add a deal: who, what, how much and when you expect it." },
        { title: "Your day", body: "\"My day\" lists the calls and follow-ups due today, so nothing is forgotten." },
    ],
});

T.add("print", {
    name: "Design your invoice", app: "Printing", minutes: 2, sequence: 70,
    steps: [
        { action: "sonador_print.action_designer", clear: true, title: "Your printouts", body: "Invoices, receipts, payment vouchers, payslips and statements all print in your design." },
        { sel: ".o_spd_kinds", place: "right", title: "Pick a document", body: "Choose which document to design. A green dot means it has its own design; the others use the common one." },
        { sel: ".o_spd_tpls", place: "right", title: "A design", body: "Classic, Modern, Compact or Bold. Click one and watch the preview." },
        { sel: ".o_spd_panel select", place: "right", title: "Paper", body: "A4, A5, or an 80 mm receipt roll for a receipt printer." },
        { sel: ".o_spd_tabs", place: "right", title: "What shows, and the words", body: "\"What to show\": logo, TIN, amount in words, bank accounts, signatures, stamp. \"Texts\": the title and notes, in English, Amharic or both." },
        { sel: ".o_spd_preview", place: "left", title: "The preview", body: "This is exactly what prints, with your real data." },
        { sel: ".o_pp button.btn-primary, .o_spd_body button.btn-primary, button.btn-primary", text: "save", title: "Save", body: "Save it for this document, or use it for every document. Every Print button then uses your design." },
    ],
});

T.add("studio", {
    name: "Make it yours (Studio)", app: "Studio", minutes: 2, sequence: 80,
    steps: [
        { action: "sonador_studio.action_studio", clear: true, title: "Studio", body: "Build an app for any process, or change the screens of the apps you have. No code, and every change can be undone." },
        { sel: ".o_sbs_describe textarea", advance: "input", title: "Describe it", body: "Say what you want to track, in a sentence: what, who, when, and any reminder. Try one." },
        { sel: ".o_sbs_describe button.btn-primary", advance: "click", title: "Draft my app", body: "Studio drafts the app: its fields, list, form and reminders. You check it before it is made." },
        { sel: ".o_sbs_card_new", title: "Or start blank", body: "A blank app, or one of the templates below it (fuel log, visitors, equipment, complaints...)." },
        { title: "Change the apps you have", body: "Further down: open any screen to add a field, move or hide one, rename labels, and add automatic rules. Every version is kept in the history." },
    ],
});

T.add("website", {
    name: "Your website and shop", app: "Website", minutes: 2, sequence: 75,
    steps: [
        { action: "sonador_site.action_site", clear: true, title: "Your website", body: "A fast website and online shop at your own address, fed straight from Unio: products, prices, pictures and stock." },
        { sel: ".o_ws_check", place: "right", title: "Getting ready", body: "Five things and you're online. Click any of them to do it." },
        { sel: ".o_ws_tabs button", text: "products", advance: "click", title: "Products", body: "Click Products." },
        { sel: ".o_ws_products .form-switch", place: "left", title: "On the site or not", body: "Switch a product on and it appears in your shop. The star makes it a favourite on the home page." },
        { sel: ".o_ws_tabs button", text: "pages", advance: "click", title: "Pages", body: "Click Pages." },
        { sel: ".o_ws_blocks", place: "right", title: "Pages are made of blocks", body: "A banner, your products, why us, what customers say... Click a block to change its words and pictures, move it up or down, or add new ones." },
        { sel: ".o_ws_preview", place: "left", title: "See it as you go", body: "The preview shows your changes at once, on a computer or a phone. Nothing is public until you press Publish." },
        { sel: ".o_ws_tabs button", text: "shop", title: "Delivery and payment", body: "Under Shop and contact: pickup or delivery and its fee, pay on delivery, bank transfer or telebirr. Orders land in Sales and in your email." },
    ],
});

T.add("apps", {
    name: "Switch apps on or off", app: "Settings", minutes: 1, sequence: 90,
    steps: [
        { action: "sonador_saas_client.action_apps", clear: true, title: "Apps", body: "Your kind of business decided which apps are on. Every app is included in your subscription." },
        { sel: ".o_saas_app .form-switch", title: "On or off", body: "Switch an app on and it appears in the left bar. Switch it off and it is hidden; nothing is deleted." },
    ],
});
