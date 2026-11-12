"""PowerPoint export without any library: a .pptx is a zip of XML parts. A title slide, then one slide per section: its
title, the person's words, then each block's figures and table (a native PowerPoint table, editable)."""
import io
import zipfile
from xml.sax.saxutils import escape

from odoo import models

EMU = 914400  # per inch
W, H = 12192000, 6858000  # 16:9

NS = ('xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
      'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"')

CONTENT_TYPES = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>
<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>
<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>
%s
</Types>'''

THEME = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Unio"><a:themeElements>
<a:clrScheme name="Unio"><a:dk1><a:srgbClr val="1F2A37"/></a:dk1><a:lt1><a:srgbClr val="FFFFFF"/></a:lt1><a:dk2><a:srgbClr val="1F3A5F"/></a:dk2>
<a:lt2><a:srgbClr val="EEF2F7"/></a:lt2><a:accent1><a:srgbClr val="1F3A5F"/></a:accent1><a:accent2><a:srgbClr val="2E8B57"/></a:accent2>
<a:accent3><a:srgbClr val="D98E04"/></a:accent3><a:accent4><a:srgbClr val="5B7DB1"/></a:accent4><a:accent5><a:srgbClr val="8E5BB1"/></a:accent5>
<a:accent6><a:srgbClr val="C0504D"/></a:accent6><a:hlink><a:srgbClr val="1F3A5F"/></a:hlink><a:folHlink><a:srgbClr val="5B7DB1"/></a:folHlink></a:clrScheme>
<a:fontScheme name="Unio"><a:majorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:majorFont>
<a:minorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont></a:fontScheme>
<a:fmtScheme name="Unio"><a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>
<a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst><a:lnStyleLst><a:ln w="9525"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>
<a:ln w="25400"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln><a:ln w="38100"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst>
<a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst>
<a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst>
</a:fmtScheme></a:themeElements></a:theme>'''

MASTER = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sldMaster %s><p:cSld><p:bg><p:bgPr><a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill><a:effectLst/></p:bgPr></p:bg><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/></p:spTree></p:cSld>
<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>
<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst></p:sldMaster>''' % NS

LAYOUT = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sldLayout %s type="blank"><p:cSld name="Blank"><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/></p:spTree></p:cSld>
<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>''' % NS

REL = '<Relationship Id="%s" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/%s" Target="%s"/>'


def rels(items):
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">%s</Relationships>'
            % ''.join(REL % i for i in items))


def run(text, size=14, bold=False, color='1F2A37'):
    return ('<a:r><a:rPr lang="en-US" sz="%d"%s dirty="0"><a:solidFill><a:srgbClr val="%s"/></a:solidFill></a:rPr><a:t>%s</a:t></a:r>'
            % (size * 100, ' b="1"' if bold else '', color, escape(str(text))))


def textbox(sid, x, y, w, h, paragraphs):
    """paragraphs: [(text, size, bold, color)]"""
    body = ''.join('<a:p>%s</a:p>' % run(*p) for p in paragraphs) or '<a:p/>'
    return ('<p:sp><p:nvSpPr><p:cNvPr id="%d" name="Text %d"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>'
            '<p:spPr><a:xfrm><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>'
            '<p:txBody><a:bodyPr wrap="square"><a:normAutofit/></a:bodyPr><a:lstStyle/>%s</p:txBody></p:sp>') % (sid, sid, x, y, w, h, body)


def table(sid, x, y, w, columns, rows, money_cols):
    n = max(len(columns), 1)
    cw = w // n
    def cell(v, head=False, right=False):
        txt = '{:,.2f}'.format(v) if isinstance(v, float) else ('{:,}'.format(v) if isinstance(v, int) and not isinstance(v, bool) else v)
        fill = '<a:solidFill><a:srgbClr val="E8EEF6"/></a:solidFill>' if head else ''
        return ('<a:tc><a:txBody><a:bodyPr/><a:lstStyle/><a:p><a:pPr algn="%s"/>%s</a:p></a:txBody><a:tcPr marL="45720" marR="45720">%s</a:tcPr></a:tc>'
                % ('r' if right else 'l', run('' if txt is None else txt, 10, head), fill))
    grid = ''.join('<a:gridCol w="%d"/>' % cw for _ in range(n))
    head = '<a:tr h="300000">%s</a:tr>' % ''.join(cell(c, True, i in money_cols) for i, c in enumerate(columns))
    body = ''.join('<a:tr h="260000">%s</a:tr>' % ''.join(cell(v, False, i in money_cols or isinstance(v, (int, float))) for i, v in enumerate(r))
                   for r in rows)
    return ('<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="%d" name="Table %d"/><p:cNvGraphicFramePr><a:graphicFrameLocks noGrp="1"/></p:cNvGraphicFramePr><p:nvPr/></p:nvGraphicFramePr>'
            '<p:xfrm><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></p:xfrm><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/table">'
            '<a:tbl><a:tblPr firstRow="1"/><a:tblGrid>%s</a:tblGrid>%s%s</a:tbl></a:graphicData></a:graphic></p:graphicFrame>'
            % (sid, sid, x, y, w, 300000 + 260000 * len(rows), grid, head, body))


def slide(shapes):
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<p:sld %s><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
            '<p:grpSpPr/>%s</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>') % (NS, ''.join(shapes))


class SbRepPptx(models.AbstractModel):
    _name = 'sb.rep.pptx'
    _description = 'Report to PowerPoint'

    def build(self, data):
        slides = [slide([textbox(2, 600000, 2200000, W - 1200000, 1000000, [(data['name'], 36, True, '1F3A5F')]),
                         textbox(3, 600000, 3300000, W - 1200000, 800000, [('%s · %s' % (data['who'], data['period']), 20, False, '5B6B7F')])])]
        for s in data['sections']:
            shapes, sid, y = [textbox(2, 500000, 300000, W - 1000000, 600000, [(s['title'], 28, True, '1F3A5F')])], 3, 950000
            if s['narrative']:
                lines = [(t, 14, False, '1F2A37') for t in s['narrative'].splitlines() if t.strip()][:8]
                shapes.append(textbox(sid, 500000, y, W - 1000000, 300000 * len(lines) + 100000, lines))
                sid += 1
                y += 300000 * len(lines) + 200000
            for code in s['blocks']:
                b = data['blocks'].get(code) or {}
                if not b or y > H - 800000:
                    continue
                kp = '   '.join('%s: %s' % (k['label'], '{:,.2f}'.format(k['value']) if k.get('money') else k['value']) for k in b.get('kpis') or [])
                shapes.append(textbox(sid, 500000, y, W - 1000000, 400000, [(b.get('title') or code, 16, True, '1F3A5F')] + ([(kp, 12, False, '1F2A37')] if kp else [])))
                sid += 1
                y += 650000 if kp else 400000
                t = b.get('table')
                if t and t.get('rows'):
                    room = max(1, (H - y - 300000) // 260000 - 1)
                    rows = t['rows'][:room] + ([t['total']] if t.get('total') and len(t['rows']) <= room else [])
                    shapes.append(table(sid, 500000, y, W - 1000000, t['columns'], rows, t.get('money') or []))
                    sid += 1
                    y += 300000 + 260000 * len(rows) + 250000
            slides.append(slide(shapes))
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('[Content_Types].xml', CONTENT_TYPES % ''.join(
                '<Override PartName="/ppt/slides/slide%d.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>' % (i + 1)
                for i in range(len(slides))))
            z.writestr('_rels/.rels', rels([('rId1', 'officeDocument', 'ppt/presentation.xml')]))
            z.writestr('ppt/presentation.xml', (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<p:presentation %s><p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
                '<p:sldIdLst>%s</p:sldIdLst><p:sldSz cx="%d" cy="%d"/><p:notesSz cx="6858000" cy="9144000"/></p:presentation>')
                % (NS, ''.join('<p:sldId id="%d" r:id="rId%d"/>' % (256 + i, i + 3) for i in range(len(slides))), W, H))
            z.writestr('ppt/_rels/presentation.xml.rels', rels([('rId1', 'slideMaster', 'slideMasters/slideMaster1.xml'), ('rId2', 'theme', 'theme/theme1.xml')]
                                                               + [('rId%d' % (i + 3), 'slide', 'slides/slide%d.xml' % (i + 1)) for i in range(len(slides))]))
            z.writestr('ppt/slideMasters/slideMaster1.xml', MASTER)
            z.writestr('ppt/slideMasters/_rels/slideMaster1.xml.rels', rels([('rId1', 'slideLayout', '../slideLayouts/slideLayout1.xml'),
                                                                             ('rId2', 'theme', '../theme/theme1.xml')]))
            z.writestr('ppt/slideLayouts/slideLayout1.xml', LAYOUT)
            z.writestr('ppt/slideLayouts/_rels/slideLayout1.xml.rels', rels([('rId1', 'slideMaster', '../slideMasters/slideMaster1.xml')]))
            z.writestr('ppt/theme/theme1.xml', THEME)
            for i, s in enumerate(slides):
                z.writestr('ppt/slides/slide%d.xml' % (i + 1), s)
                z.writestr('ppt/slides/_rels/slide%d.xml.rels' % (i + 1), rels([('rId1', 'slideLayout', '../slideLayouts/slideLayout1.xml')]))
        return out.getvalue()
