"""Amharic for the fixed wording printed on documents (labels, column heads, totals, marks)."""
import re

AM = {
    # parties
    'Customer': 'ደንበኛ', 'Supplier': 'አቅራቢ', 'Cash customer': 'የጥሬ ገንዘብ ደንበኛ', 'Received from': 'የተቀበልነው ከ', 'Paid to': 'የተከፈለው ለ',
    'From': 'ከ', 'To': 'ወደ', 'Partner': 'አጋር', 'Deliver to': 'የሚረከበው', 'Issued to': 'የተሰጠው ለ',
    # details
    'Reference': 'ማጣቀሻ', 'Due': 'የመክፈያ ቀን', 'Order': 'ትዕዛዝ', 'Paid by': 'የተከፈለበት', 'By': 'የክፍያ ዘዴ', 'Your reference': 'የእርስዎ ማጣቀሻ',
    'Valid until': 'የሚያገለግለው እስከ', 'Supplier reference': 'የአቅራቢ ማጣቀሻ', 'Deliver by': 'የማስረከቢያ ቀን', 'Salesperson': 'ሻጭ', 'Buyer': 'ገዢ',
    'Delivery order': 'የማስረከቢያ ትዕዛዝ', 'Vehicle': 'ተሽከርካሪ', 'Driver': 'ሹፌር', 'Store': 'መጋዘን', 'To store': 'ወደ መጋዘን',
    'Received / requested by': 'የተረከበው / የጠየቀው',
    # columns
    'Description': 'መግለጫ', 'Qty': 'ብዛት', 'Quantity': 'ብዛት', 'Unit': 'መለኪያ', 'Unit price': 'የአንዱ ዋጋ', 'Tax': 'ታክስ', 'Amount': 'መጠን',
    'For': 'ለ', 'Date': 'ቀን', 'Account': 'ሂሳብ', 'Debit': 'ዴቢት', 'Credit': 'ክሬዲት', 'Item': 'እቃ', 'Unit cost': 'የአንዱ ወጪ', 'Value': 'ዋጋ',
    'Invoice amount': 'የደረሰኙ መጠን', 'Exempt': 'ነፃ',
    # payslips and statements
    'Employee': 'ሰራተኛ', 'Period': 'ጊዜ', 'Pay date': 'የክፍያ ቀን', 'Paid days': 'የተከፈለባቸው ቀናት', 'Position': 'የስራ መደብ',
    'Pension no.': 'የጡረታ ቁጥር', 'Salary account': 'የደመወዝ ሂሳብ', 'Pay item': 'የክፍያ አይነት', 'Earnings': 'ገቢ', 'Deductions': 'ተቀናሾች',
    'Basic salary': 'መሰረታዊ ደመወዝ', 'House allowance': 'የቤት አበል', 'Position allowance': 'የሃላፊነት አበል', 'Cash indemnity': 'የገንዘብ ተያዥ አበል',
    'Other taxable allowances': 'ሌሎች ግብር የሚከፈልባቸው አበሎች', 'Overtime': 'የትርፍ ሰዓት ክፍያ', 'Commission': 'ኮሚሽን', 'Bonus': 'ጉርሻ',
    'Annual leave pay-out': 'የዓመት ፈቃድ ክፍያ', 'Severance': 'የስንብት ክፍያ', 'Compensation': 'ካሳ', 'Pay in lieu of notice': 'የማስጠንቀቂያ ጊዜ ክፍያ',
    'Transport / non-taxable allowances': 'የትራንስፖርት / ከግብር ነፃ አበሎች', 'Employment income tax (PAYE)': 'የስራ ግብር',
    'Other deductions': 'ሌሎች ተቀናሾች',
    'Gross pay': 'ጠቅላላ ደመወዝ', 'Total deductions': 'ጠቅላላ ተቀናሽ', 'Net pay': 'የተጣራ ክፍያ',
    'Statement date': 'የመግለጫው ቀን', 'Credit limit': 'የዱቤ ገደብ', 'Document': 'ሰነድ', 'Charges': 'ዕዳ', 'Bills': 'ደረሰኞች',
    'Payments': 'ክፍያዎች', 'Balance': 'ቀሪ', 'Opening balance': 'የመነሻ ቀሪ', 'Balance due': 'ያልተከፈለ ቀሪ', 'Balance owed': 'የምንከፍለው ቀሪ',
    # totals
    'Amount before tax': 'ከታክስ በፊት', 'VAT': 'ተ.እ.ታ', 'Total': 'ጠቅላላ', 'Withholding': 'ተቀናሽ ግብር', 'Received': 'የተቀበልነው', 'Paid': 'የተከፈለ',
    'Total debit': 'ጠቅላላ ዴቢት', 'Total credit': 'ጠቅላላ ክሬዲት', 'Total quantity': 'ጠቅላላ ብዛት', 'Total value': 'ጠቅላላ ዋጋ',
    # fixed wording on the page
    'Amount in words': 'የገንዘቡ መጠን በፊደል', 'Pay to': 'የባንክ ሂሳባችን', 'Terms': 'ውሎች', 'Name, signature, date': 'ስም፣ ፊርማ፣ ቀን',
    'Stamp': 'ማህተም', 'ORIGINAL': 'ዋና ቅጂ', 'TIN': 'የግብር መለያ ቁጥር', 'VAT reg.': 'የተ.እ.ታ ምዝገባ',
    'DRAFT': 'ረቂቅ', 'VOID': 'የተሰረዘ', 'CANCELLED': 'የተሰረዘ', 'NOT YET DELIVERED': 'ገና ያልደረሰ',
    'Prepared by': 'ያዘጋጀው', 'Approved by': 'ያጸደቀው', 'Received by': 'የተረከበው', 'Checked by': 'ያረጋገጠው', 'Delivered by': 'ያስረከበው',
}
_CUR = re.compile(r'^(.*) ([A-Z]{3})$')


def amharic(s):
    """The Amharic for a label, or None; 'Total USD' -> 'ጠቅላላ USD'."""
    if not s:
        return None
    if s in AM:
        return AM[s]
    m = _CUR.match(s)
    if m and m.group(1) in AM:
        return '%s %s' % (AM[m.group(1)], m.group(2))
    return None


def translator(lang):
    """A function printing a label in the design's language: English, Amharic, or both."""
    def t(s):
        am = amharic(s) if lang in ('am', 'both') else None
        if not am:
            return s
        return am if lang == 'am' else '%s · %s' % (s, am)
    return t
