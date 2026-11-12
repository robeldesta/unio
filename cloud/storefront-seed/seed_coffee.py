# Demo shop data for a local test company (run in `odoo shell`): coffee products with drawn pictures, categories, stock.
import base64
import io
import json
import math

from PIL import Image, ImageDraw, ImageFilter, ImageFont

R = '/usr/share/fonts/truetype/roboto/'


def font(size, bold=True):
    import glob
    files = glob.glob(R + '**/Roboto-Bold.ttf' if bold else R + '**/Roboto-Regular.ttf', recursive=True) or \
        glob.glob('/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf' % ('-Bold' if bold else ''))
    return ImageFont.truetype(files[0], size)


def bag(color, label, sub, kind='bag'):
    W = 1000
    img = Image.new('RGB', (W, W), '#F4EFE7')
    d = ImageDraw.Draw(img)
    # soft backdrop circle
    d.ellipse((140, 160, 860, 880), fill='#EAE1D3')
    sh = Image.new('L', (W, W), 0)
    ImageDraw.Draw(sh).ellipse((260, 820, 740, 900), fill=120)
    img.paste('#D9CDBA', (0, 0), sh.filter(ImageFilter.GaussianBlur(18)))
    if kind == 'bag':
        d.polygon([(300, 250), (700, 250), (730, 860), (270, 860)], fill=color)
        d.polygon([(300, 250), (700, 250), (690, 300), (310, 300)], fill=tuple(max(c - 40, 0) for c in color))
        d.rounded_rectangle((340, 420, 660, 700), 18, fill='#FBF7F0')
        d.text((500, 500), label, fill=color, font=font(46), anchor='mm')
        d.text((500, 565), sub, fill='#6B5B4B', font=font(30, False), anchor='mm')
        d.ellipse((470, 610, 530, 670), fill=color)
    elif kind == 'pot':  # jebena
        d.ellipse((330, 470, 670, 840), fill=color)
        d.rectangle((455, 300, 545, 500), fill=color)
        d.ellipse((440, 270, 560, 330), fill=tuple(max(c - 30, 0) for c in color))
        d.line((660, 600, 760, 470), fill=color, width=34)
        d.arc((240, 470, 380, 700), 90, 270, fill=color, width=26)
    elif kind == 'cups':
        for i, x in enumerate((300, 500, 700)):
            d.pieslice((x - 95, 470, x + 95, 760), 0, 180, fill=color)
            d.rectangle((x - 95, 600, x + 95, 615), fill=color)
            d.ellipse((x - 95, 585, x + 95, 640), fill='#FBF7F0')
            d.ellipse((x - 80, 595, x + 80, 630), fill='#5A3A22')
    elif kind == 'box':
        d.rectangle((270, 420, 730, 840), fill=color)
        d.rectangle((250, 360, 750, 450), fill=tuple(max(c - 30, 0) for c in color))
        d.rectangle((470, 360, 530, 840), fill='#F2C14E')
        d.ellipse((400, 280, 500, 370), outline='#F2C14E', width=22)
        d.ellipse((500, 280, 600, 370), outline='#F2C14E', width=22)
    elif kind == 'jar':
        d.rounded_rectangle((350, 380, 650, 840), 40, fill='#E9D8B8')
        d.rectangle((370, 330, 630, 400), fill=color)
        for i in range(14):
            x = 390 + (i * 53) % 230
            y = 520 + (i * 97) % 280
            d.ellipse((x, y, x + 34, y + 26), fill='#C9A15C')
    # beans
    for i in range(7):
        a = i * 0.9
        x, y = 170 + 90 * i, 900 - int(30 * math.sin(a))
        d.ellipse((x, y - 22, x + 52, y + 22), fill='#5A3A22')
        d.line((x + 8, y, x + 44, y), fill='#3D2716', width=4)
    buf = io.BytesIO()
    img.save(buf, 'JPEG', quality=88)
    return base64.b64encode(buf.getvalue())


env = env(context=dict(env.context, tracking_disable=True))
Cat = env['unio.site.category']
cats = {}
for i, n in enumerate(['Roasted coffee', 'Green coffee', 'Ground coffee', 'Coffee ceremony', 'Gifts']):
    cats[n] = Cat.search([('name', '=', n)], limit=1) or Cat.create({'name': n, 'sequence': i})

items = [
    ('Yirgacheffe washed, roasted 500 g', 950, 'Roasted coffee', (46, 94, 78), 'YIRGACHEFFE', 'Washed · 500 g', 'bag', True,
     'Floral and bright, with notes of jasmine, lemon and black tea.', 40),
    ('Sidamo natural, roasted 500 g', 880, 'Roasted coffee', (140, 58, 42), 'SIDAMO', 'Natural · 500 g', 'bag', True,
     'Sweet and fruity: blueberry, cocoa and a long finish.', 32),
    ('Guji natural, roasted 500 g', 990, 'Roasted coffee', (164, 98, 36), 'GUJI', 'Natural · 500 g', 'bag', True,
     'Strawberry, peach and dark chocolate. Our best seller.', 4),
    ('Harar longberry, roasted 500 g', 1050, 'Roasted coffee', (60, 52, 92), 'HARAR', 'Longberry · 500 g', 'bag', False,
     'Wild and winey, with blueberry and spice.', 18),
    ('Limu washed, green beans 1 kg', 1200, 'Green coffee', (92, 120, 52), 'LIMU', 'Green · 1 kg', 'bag', False,
     'Clean, balanced green coffee for home roasting.', 25),
    ('Jimma, green beans 1 kg', 980, 'Green coffee', (110, 128, 70), 'JIMMA', 'Green · 1 kg', 'bag', False,
     'Classic everyday coffee, full-bodied and earthy.', 0),
    ('Buna ground coffee 250 g', 420, 'Ground coffee', (120, 70, 40), 'BUNA', 'Ground · 250 g', 'bag', True,
     'Fine ground for the jebena or the moka pot.', 60),
    ('Espresso blend 1 kg', 1850, 'Ground coffee', (36, 36, 40), 'ESPRESSO', 'Blend · 1 kg', 'bag', False,
     'Guji and Sidamo blended for a sweet, syrupy shot.', 12),
    ('Jebena, clay coffee pot', 650, 'Coffee ceremony', (62, 44, 34), '', '', 'pot', True,
     'Hand made black clay jebena from Sululta.', 9),
    ('Sini cups, set of 6', 480, 'Coffee ceremony', (184, 60, 52), '', '', 'cups', False,
     'Porcelain sini cups for the coffee ceremony.', 22),
    ('Etan frankincense 100 g', 150, 'Coffee ceremony', (122, 96, 60), '', '', 'jar', False,
     'Fragrant frankincense from the north, for the ceremony.', 50),
    ('Coffee gift box', 2400, 'Gifts', (110, 30, 40), '', '', 'box', True,
     'Three roasted coffees, sini cups and etan, ready to give.', 7),
]
T = env['product.template']
wh = env['stock.warehouse'].search([], limit=1)
for name, price, cat, color, label, sub, kind, featured, desc, qty in items:
    t = T.search([('name', '=', name)], limit=1)
    vals = {'name': name, 'list_price': price, 'sale_ok': True, 'type': 'consu', 'is_storable': True,
            'description_sale': desc, 'site_published': True, 'site_featured': featured,
            'site_category_ids': [(6, 0, [cats[cat].id])], 'image_1920': bag(color, label, sub, kind),
            'site_description': '<p>%s</p><p>Roasted to order in Addis Ababa and packed the same day. Store in a cool, dry place.</p>' % desc}
    if t:
        t.write(vals)
    else:
        t = T.create(vals)
    if qty and wh:
        q = env['stock.quant']
        cur = sum(q.search([('product_id', '=', t.product_variant_id.id), ('location_id', '=', wh.lot_stock_id.id)]).mapped('quantity'))
        if cur != qty:
            q._update_available_quantity(t.product_variant_id, wh.lot_stock_id, qty - cur)

site = env['unio.site'].current()
site.write({'name': 'Addis Coffee Traders', 'tagline': 'Fresh Ethiopian coffee, roasted to order', 'published': True,
            'theme': 'fresh', 'color': '#2E5E4E', 'accent': '#C8873A', 'announcement': 'Free delivery in Addis over 2,000 birr',
            'phone': '+251 911 234 567', 'whatsapp': '+251911234567', 'telegram': '@addiscoffee', 'email': 'hello@addiscoffee.test',
            'address': 'Bole Road, near Edna Mall, Addis Ababa', 'hours': 'Mon–Sat 8:00–19:00', 'free_over': 2000, 'delivery_fee': 150,
            'pay_telebirr': True, 'telebirr_text': 'Till 123456 (Addis Coffee Traders)', 'bank_text': 'CBE 1000123456789 Addis Coffee Traders',
            'seo_description': 'Fresh Ethiopian specialty coffee from Yirgacheffe, Sidamo, Guji and Harar, roasted to order in Addis Ababa.'})
home = site.page_ids.filtered(lambda p: p.slug == 'home')
blocks = [
    {'type': 'hero', 'title': 'Fresh Ethiopian coffee, roasted to order', 'subtitle': 'Yirgacheffe, Sidamo, Guji and Harar from the farms we know. Delivered in Addis the same day.',
     'button': 'Shop coffee', 'link': '/shop', 'style': 'split', 'image': '/unio/site/img/p/%d' % T.search([('name', 'like', 'Guji')], limit=1).id},
    {'type': 'categories', 'title': 'Shop by category'},
    {'type': 'products', 'title': 'Our favourites', 'source': 'featured', 'limit': 8},
    {'type': 'features', 'title': 'Why coffee lovers choose us', 'items': [
        {'icon': 'leaf', 'title': 'Single origin', 'text': 'Every bag says which region and process it comes from.'},
        {'icon': 'clock', 'title': 'Roasted to order', 'text': 'We roast after you order, never weeks before.'},
        {'icon': 'truck', 'title': 'Same day in Addis', 'text': 'Order before 2 pm. Free over 2,000 birr.'},
        {'icon': 'cash', 'title': 'Pay on delivery', 'text': 'Cash, telebirr or bank transfer.'}]},
    {'type': 'testimonials', 'title': 'What our customers say', 'items': [
        {'quote': 'The Guji is the best coffee I have had outside of a ceremony. Delivered in two hours.', 'name': 'Meron A.', 'role': 'Bole'},
        {'quote': 'We buy all the coffee for our office here. Always fresh, always on time.', 'name': 'Dawit K.', 'role': 'Office manager'},
        {'quote': 'Beautiful gift box. My family in Toronto loved it.', 'name': 'Selam T.', 'role': 'Customer'}]},
    {'type': 'cta', 'title': 'Buying for a café or office?', 'subtitle': 'Ask for wholesale prices, monthly invoices and a tasting.', 'button': 'Talk to us', 'link': '/contact'},
]
home.write({'blocks': json.dumps(blocks), 'blocks_draft': json.dumps(blocks)})
env.cr.commit()
print('> seeded', T.search_count([('site_published', '=', True)]), 'products')
