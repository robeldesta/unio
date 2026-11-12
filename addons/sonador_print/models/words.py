"""Amounts in words: English (Odoo's own) and Amharic (birr and santim)."""
ONES = ['', 'አንድ', 'ሁለት', 'ሶስት', 'አራት', 'አምስት', 'ስድስት', 'ሰባት', 'ስምንት', 'ዘጠኝ']
TENS = ['', 'አስር', 'ሃያ', 'ሰላሳ', 'አርባ', 'ሃምሳ', 'ስልሳ', 'ሰባ', 'ሰማንያ', 'ዘጠና']
SCALES = [(10 ** 9, 'ቢሊዮን'), (10 ** 6, 'ሚሊዮን'), (1000, 'ሺህ')]


def _below_thousand(n):
    out = []
    h, r = divmod(n, 100)
    if h:
        out.append((ONES[h] + ' ' if h > 1 else 'አንድ ') + 'መቶ')
    t, o = divmod(r, 10)
    if t == 1 and o:
        out.append('አስራ ' + ONES[o])  # 11-19: አስራ አንድ, አስራ አምስት ...
        return ' '.join(out)
    if t:
        out.append(TENS[t])
    if o:
        out.append(ONES[o])
    return ' '.join(out)


def amharic_number(n):
    n = int(n)
    if n == 0:
        return 'ዜሮ'
    out = []
    for value, name in SCALES:
        q, n = divmod(n, value)
        if q:
            out.append('%s %s' % (amharic_number(q) if q >= 1000 else _below_thousand(q), name))
    if n:
        out.append(_below_thousand(n))
    return ' '.join(out)


def amharic_amount(amount, unit='ብር', sub='ሳንቲም'):
    amount = round(abs(amount), 2)
    whole = int(amount)
    cents = int(round((amount - whole) * 100))
    text = '%s %s' % (amharic_number(whole), unit)
    if cents:
        text += ' ከ%s %s' % (amharic_number(cents), sub)
    return text
