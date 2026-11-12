"""Demo school for sales demos: Bright Future Academy, KG1 to Grade 8, about 300 students in 12 classes,
fees billed for Meskerem and Tikimt 2019 with payments, attendance since school opened, Semester 1 marks.
Run in an odoo shell on an empty school database:  exec(open('/mnt/extra-addons/sonador_school/tests_manual/seed_demo.py').read())"""
import random
from datetime import date, timedelta

from odoo import Command

R = random.Random(2019)
Svc = env['school.service']
St = env['school.student']

if env['school.year'].search_count([]):
    raise SystemExit('already seeded')

c = env.company
c.write({'name': 'Bright Future Academy', 'phone': '+251 11 662 4410', 'city': 'Addis Ababa', 'street': 'Bole Sub-city, Woreda 03'})

MALE = [('Abel', 'አቤል'), ('Dawit', 'ዳዊት'), ('Yonas', 'ዮናስ'), ('Nahom', 'ናሆም'), ('Kidus', 'ቅዱስ'), ('Biruk', 'ብሩክ'), ('Henok', 'ሄኖክ'),
        ('Natnael', 'ናትናኤል'), ('Samuel', 'ሳሙኤል'), ('Eyob', 'ኢዮብ'), ('Mikiyas', 'ሚኪያስ'), ('Yared', 'ያሬድ'), ('Bereket', 'በረከት'),
        ('Amanuel', 'አማኑኤል'), ('Fitsum', 'ፍፁም'), ('Kaleb', 'ካሌብ'), ('Ermias', 'ኤርሚያስ'), ('Leul', 'ልዑል'), ('Surafel', 'ሱራፌል'),
        ('Abenezer', 'አቤኔዘር'), ('Yohannes', 'ዮሐንስ'), ('Elias', 'ኤልያስ'), ('Mussie', 'ሙሴ'), ('Tewodros', 'ቴዎድሮስ')]
FEMALE = [('Hana', 'ሐና'), ('Selam', 'ሰላም'), ('Bethlehem', 'ቤተልሔም'), ('Meron', 'ሜሮን'), ('Liya', 'ሊያ'), ('Saron', 'ሳሮን'),
          ('Mahlet', 'ማኅሌት'), ('Ruth', 'ሩት'), ('Hiwot', 'ሕይወት'), ('Tsion', 'ጽዮን'), ('Eden', 'ኤደን'), ('Feven', 'ፌቨን'),
          ('Kalkidan', 'ቃልኪዳን'), ('Mekdes', 'መቅደስ'), ('Hermela', 'ሄርሜላ'), ('Bezawit', 'ቤዛዊት'), ('Lidya', 'ሊዲያ'), ('Rahel', 'ራሔል'),
          ('Sara', 'ሣራ'), ('Yordanos', 'ዮርዳኖስ'), ('Nardos', 'ናርዶስ'), ('Blen', 'ብሌን'), ('Arsema', 'አርሴማ'), ('Elshaday', 'ኤልሻዳይ')]
FATHERS = [('Tesfaye', 'ተስፋዬ'), ('Alemu', 'አለሙ'), ('Girma', 'ግርማ'), ('Bekele', 'በቀለ'), ('Haile', 'ኃይሌ'), ('Mulugeta', 'ሙሉጌታ'),
           ('Getachew', 'ጌታቸው'), ('Solomon', 'ሰለሞን'), ('Dereje', 'ደረጀ'), ('Tadesse', 'ታደሰ'), ('Asfaw', 'አስፋው'), ('Kebede', 'ከበደ'),
           ('Mengistu', 'መንግሥቱ'), ('Wondimu', 'ወንድሙ'), ('Yilma', 'ይልማ'), ('Abebe', 'አበበ'), ('Desta', 'ደስታ'), ('Fikru', 'ፍቅሩ'),
           ('Gebre', 'ገብሬ'), ('Lemma', 'ለማ'), ('Negash', 'ነጋሽ'), ('Shiferaw', 'ሽፈራው'), ('Tilahun', 'ጥላሁን'), ('Worku', 'ወርቁ'),
           ('Zewdu', 'ዘውዱ'), ('Berhanu', 'ብርሃኑ'), ('Assefa', 'አሰፋ'), ('Demissie', 'ደምሴ'), ('Eshetu', 'እሸቱ'), ('Hailu', 'ኃይሉ')]
MOTHERS = ['Almaz', 'Tigist', 'Genet', 'Aster', 'Meseret', 'Yeshi', 'Birtukan', 'Etenesh', 'Mulu', 'Senait', 'Zinash', 'Haregewoin']

# ---------------------------------------------------------------- staff
TEACHERS = ['Ato Mesfin Kassa', 'W/ro Alem Tsegaye', 'W/t Martha Getahun', 'Ato Bisrat Tamrat', 'W/ro Fantaye Abera', 'Ato Kiros Hagos',
            'W/t Rediet Mamo', 'Ato Daniel Woldemariam', 'W/ro Tsehay Belay', 'Ato Yitbarek Admasu', 'W/t Netsanet Ayele', 'Ato Henok Zerihun',
            'W/ro Wubit Gebeyehu', 'Ato Samson Tefera', 'W/t Eleni Mekonnen', 'Ato Fasil Ayalew']
Emp = env['hr.employee']
staff = Emp.browse()
for n in TEACHERS:
    staff |= Emp.search([('name', '=', n)], limit=1) or Emp.create({'name': n, 'job_title': 'Teacher'})

# ---------------------------------------------------------------- year, classes, fees
G = {g.name: g for g in env['school.grade'].search([])}
taught = ['KG1', 'KG2', 'KG3'] + ['Grade %d' % i for i in range(1, 9)]
Svc.start_year('2019 E.C.', '2026-09-11', '2027-07-07', [G[n].id for n in taught], 1, '2027-01-24')
year = env['school.year'].get_current()
S = env['school.section']
S.create({'name': 'B', 'year_id': year.id, 'grade_id': G['Grade 1'].id})
secs = S.search([('year_id', '=', year.id)])
for i, s in enumerate(secs.sorted(lambda s: (s.grade_id.sequence, s.name))):
    s.write({'homeroom_id': staff[i % len(staff)].id, 'room': 'Room %d' % (101 + i), 'capacity': 32})

TUITION = {'KG1': 2800, 'KG2': 2800, 'KG3': 2800, 'Grade 1': 3200, 'Grade 2': 3200, 'Grade 3': 3200, 'Grade 4': 3200,
           'Grade 5': 3500, 'Grade 6': 3500, 'Grade 7': 3800, 'Grade 8': 3800}
tuition = env.ref('sonador_school.fee_tuition')
tuition.price_ids = [Command.create({'grade_id': G[n].id, 'amount': a}) for n, a in TUITION.items()]

# ---------------------------------------------------------------- families and students
AGE = {'KG1': 4, 'KG2': 5, 'KG3': 6, 'Grade 1': 7, 'Grade 2': 8, 'Grade 3': 9, 'Grade 4': 10, 'Grade 5': 11, 'Grade 6': 12,
       'Grade 7': 13, 'Grade 8': 14}
by_grade = {n: list(secs.filtered(lambda s, n=n: s.grade_id.name == n)) for n in taught}
room = {s.id: 0 for s in secs}
Partner = env['res.partner']
used = set()
n_fam = 0
while sum(room.values()) < 300:
    fa, fa_am = R.choice(FATHERS)
    gf, gf_am = R.choice(FATHERS)
    if (fa, gf) in used or fa == gf:
        continue
    used.add((fa, gf))
    n_fam += 1
    mother = '%s %s' % (R.choice(MOTHERS), R.choice(FATHERS)[0])
    payer = Partner.create({'name': '%s %s' % (fa, gf), 'phone': '+251 9%d %03d %04d' % (R.choice([1, 2, 3, 4]), R.randint(0, 999), R.randint(0, 9999)),
                            'customer_rank': 1, 'city': 'Addis Ababa'})
    kids = R.choices([1, 2, 3], weights=[58, 32, 10])[0]
    grades = sorted(R.sample(taught, kids), key=taught.index)
    transport = R.random() < 0.35
    for gname in grades:
        options = [s for s in by_grade[gname] if room[s.id] < 30]
        if not options:
            continue
        sec = min(options, key=lambda s: room[s.id])
        room[sec.id] += 1
        sex = R.choice('mf')
        first, first_am = R.choice(MALE if sex == 'm' else FEMALE)
        born = date(2026 - AGE[gname], 1, 1) + timedelta(days=R.randint(-150, 240))
        St.create({'name': '%s %s %s' % (first, fa, gf), 'name_am': '%s %s %s' % (first_am, fa_am, gf_am), 'sex': sex, 'birth_date': born,
                   'applied_grade_id': G[gname].id, 'section_id': sec.id, 'state': 'enrolled', 'payer_id': payer.id,
                   'second_guardian': mother, 'second_phone': '+251 9%d %03d %04d' % (R.choice([1, 2]), R.randint(0, 999), R.randint(0, 9999)),
                   'transport': transport, 'joined': date(2026 - R.randint(0, 4), 9, 11),
                   'scholarship_pct': R.choice([25, 50]) if R.random() < 0.03 else 0,
                   'medical': R.choice(['Asthma: inhaler in the bag', 'Allergic to peanuts', 'Wears glasses']) if R.random() < 0.04 else False})
# applicants waiting
for gname in ('KG1', 'Grade 3', 'Grade 6', 'Grade 9'):
    for _i in range(2):
        fa, fa_am = R.choice(FATHERS)
        gf, gf_am = R.choice(FATHERS)
        sex = R.choice('mf')
        first, first_am = R.choice(MALE if sex == 'm' else FEMALE)
        p = Partner.create({'name': '%s %s' % (fa, gf), 'phone': '+251 911 %03d %04d' % (R.randint(0, 999), R.randint(0, 9999)), 'customer_rank': 1})
        St.create({'name': '%s %s %s' % (first, fa, gf), 'name_am': '%s %s %s' % (first_am, fa_am, gf_am), 'sex': sex,
                   'applied_grade_id': G[gname].id, 'state': 'applicant', 'payer_id': p.id, 'previous_school': 'Hope Academy'})
env.cr.commit()
print('students', St.search_count([('state', '=', 'enrolled')]), 'families', n_fam)

# ---------------------------------------------------------------- fees and payments
Svc.bill_run(['monthly', 'annual', 'term'], 'Meskerem 2019', '2026-09-11', '2026-09-25')
Svc.bill_run(['monthly'], 'Tikimt 2019', '2026-10-11', '2026-10-25')
env.cr.commit()
Acc = env['account.account']
cash = Acc.search([('account_type', '=', 'asset_cash'), ('company_ids', 'in', c.id)])
bank = cash.filtered(lambda a: 'bank' in (a.name or '').lower())[:1] or cash[:1]
bank.name = 'Commercial Bank of Ethiopia'
tele = Acc.search([('name', '=', 'telebirr'), ('company_ids', 'in', c.id)], limit=1)
if not tele:
    code = int(bank.code) + 1 if bank.code.isdigit() else None
    while code and Acc.search_count([('code', '=', str(code))]):
        code += 1
    tele = Acc.create({'name': 'telebirr', 'code': str(code), 'account_type': 'asset_cash', 'company_ids': [Command.set(c.ids)]})
Bill = env['school.bill']
payers = Bill.search([('state', '=', 'posted')]).mapped('payer_id')
for p in payers:
    bills = Bill.search([('payer_id', '=', p.id), ('state', '=', 'posted')])
    mes = sum(bills.filtered(lambda b: b.period == 'Meskerem 2019').mapped('amount'))
    tik = sum(bills.filtered(lambda b: b.period == 'Tikimt 2019').mapped('amount'))
    acc = tele if R.random() < 0.45 else bank
    ref = lambda: ('TB%s' % R.randint(10 ** 9, 10 ** 10 - 1)) if acc == tele else 'Slip %d' % R.randint(100000, 999999)  # noqa: E731
    roll = R.random()
    if roll < 0.82:
        Svc.pay(p.id, mes, acc.id, ref(), str(date(2026, 9, R.randint(12, 26))))
    elif roll < 0.91:
        Svc.pay(p.id, round(mes * 0.5, -2), acc.id, ref(), str(date(2026, 9, R.randint(15, 30))))
    if roll < 0.82 and R.random() < 0.62:
        Svc.pay(p.id, tik, acc.id, ref(), str(date(2026, 10, R.randint(11, 20))))
env.cr.commit()
print('bills', Bill.search_count([('state', '=', 'posted')]), 'owed', round(sum(Bill.search([('state', '=', 'posted')]).mapped('residual')), 2))

# ---------------------------------------------------------------- attendance (school opened Meskerem 4 = 14 Sep)
students = St.search([('state', '=', 'enrolled')])
habit = {s.id: R.choice([0.01] * 8 + [0.04] * 3 + [0.12]) for s in students}
day = date(2026, 9, 14)
Att = env['school.attendance']
while day <= date(2026, 10, 21):
    if day.weekday() < 5:
        for sec in secs:
            lines = []
            for s in sec.student_ids.filtered(lambda x: x.state == 'enrolled'):
                r = R.random()
                st = 'absent' if r < habit[s.id] else 'late' if r < habit[s.id] + 0.03 else 'excused' if r < habit[s.id] + 0.035 else 'present'
                lines.append(Command.create({'student_id': s.id, 'status': st, 'note': 'Sick' if st == 'excused' else False}))
            if day == date(2026, 10, 21) and sec.grade_id.name in ('Grade 7', 'Grade 8'):
                continue  # these registers are not taken yet today
            Att.create({'section_id': sec.id, 'date': day, 'line_ids': lines})
    day += timedelta(days=1)
env.cr.commit()

# ---------------------------------------------------------------- Semester 1 marks, conduct, comments
term = year.term_ids.sorted('sequence')[0]
ass = env['school.assessment'].search([])
Mark = env['school.mark']
ability = {s.id: max(32, min(97, R.gauss(71, 12))) for s in students}
vals = []
for sec in secs:
    for s in sec.student_ids.filtered(lambda x: x.state == 'enrolled'):
        for sub in sec.grade_id.subject_ids:
            lvl = ability[s.id] + R.gauss(0, 6)
            for a in ass:
                pct = max(10, min(100, lvl + R.gauss(0, 8)))
                vals.append({'term_id': term.id, 'section_id': sec.id, 'student_id': s.id, 'subject_id': sub.id, 'assessment_id': a.id,
                             'score': round(a.max_mark * pct / 100 * 2) / 2})
Mark.create(vals)
TR = env['school.term.result']
GOOD = ['Excellent work this semester. Keep it up!', 'A hard-working and respectful student.', 'Very good progress, especially in Mathematics.']
OK = ['Good effort. Needs to read more at home.', 'Participates well in class; should finish homework on time.']
LOW = ['Needs support at home with reading and homework. Please see the homeroom teacher.', 'Can do much better; attendance must improve.']
for s in students:
    a = ability[s.id]
    TR.create({'term_id': term.id, 'student_id': s.id,
               'conduct': 'A' if a > 78 else 'B' if a > 60 else R.choice('BC') if a > 48 else R.choice('CD'),
               'comment': R.choice(GOOD if a > 78 else OK if a > 55 else LOW)})
env.cr.commit()
print('marks', Mark.search_count([]), 'attendance days', Att.search_count([]))
