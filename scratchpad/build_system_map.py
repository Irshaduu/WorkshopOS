"""
Generates SYSTEM_MAP.html - the one-page visual map of WorkshopOS.

WHY THIS IS GENERATED RATHER THAN HAND-WRITTEN
----------------------------------------------
The map is ~56 cards and ~30 connectors on a fixed A4-landscape canvas. Hand
placing that in raw SVG means every nudge to one zone is a manual re-flow of
everything after it. Here a zone declares its grid and the cards fall into it,
and connectors are drawn between *named anchors* rather than typed coordinates,
so moving a card moves its lines with it.

WHY NOT MERMAID
---------------
Mermaid picks its own layout. It cannot be made to fill a page, it sprawls
vertically on a landscape sheet, and it wants a label on every edge. This
drawing has to be dense, deliberate and wordless - colour carries the meaning
that edge labels would otherwise spend sentences on.

CORRIDORS ARE THE WHOLE TRICK
-----------------------------
Zones are separated by real gutters (H1/H2 horizontally, VL/VM/VE/VR
vertically) and every long connector is routed through one. A first version
packed the zones tight and let the router find its own way: 12 of 32 lines cut
straight through unrelated cards. Corridors are what stop a map like this
turning into spaghetti.

NO RUNTIME DEPENDENCY
---------------------
Output is one self-contained HTML file: inline SVG, system fonts, no CDN, no
script. It prints to PDF identically offline, which is the point - it is handed
to owners and posted publicly.

    python scratchpad/build_system_map.py
"""

import html
from pathlib import Path

W, H = 1414, 1000          # A4 landscape ratio (1.414)

# The app's own palette, so the map looks like the product it describes.
INK    = '#0f172a'
MUTED  = '#64748b'
FAINT  = '#94a3b8'
LINE   = '#e2e8f0'
GROUND = '#f8fafc'
CARD   = '#ffffff'

# Flow types. Colour IS the meaning - there are no edge labels anywhere.
FLOW = {
    'work':  '#2563eb',   # the car's journey through the workshop
    'in':    '#059669',   # money coming in
    'out':   '#dc2626',   # money going out
    'stock': '#7c3aed',   # parts moving on and off the shelf
    'data':  '#94a3b8',   # reference data and reporting feeds
    'alert': '#f59e0b',   # alerts, audit and history
}

parts = []
def add(s): parts.append(s)
def esc(s): return html.escape(str(s), quote=True)

anchors = {}
LINKS = []


# --- geometry -----------------------------------------------------------------

def rounded(points, r=7):
    if len(points) < 2:
        return ''
    d = ['M %.1f %.1f' % points[0]]
    for i in range(1, len(points) - 1):
        x0, y0 = points[i - 1]
        x1, y1 = points[i]
        x2, y2 = points[i + 1]
        dx1, dy1 = x1 - x0, y1 - y0
        l1 = max(abs(dx1), abs(dy1)) or 1
        rr1 = min(r, l1 / 2)
        ax, ay = x1 - dx1 / l1 * rr1, y1 - dy1 / l1 * rr1
        dx2, dy2 = x2 - x1, y2 - y1
        l2 = max(abs(dx2), abs(dy2)) or 1
        rr2 = min(r, l2 / 2)
        bx, by = x1 + dx2 / l2 * rr2, y1 + dy2 / l2 * rr2
        d.append('L %.1f %.1f Q %.1f %.1f %.1f %.1f' % (ax, ay, x1, y1, bx, by))
    d.append('L %.1f %.1f' % points[-1])
    return ' '.join(d)


def side(cid, where, t=0.5):
    """A point on one edge of a card; `t` slides it along that edge."""
    x, y, w, h = anchors[cid]
    return {'l': (x, y + h * t), 'r': (x + w, y + h * t),
            't': (x + w * t, y), 'b': (x + w * t, y + h)}[where]


def link(a, b, kind, va='r', vb='l', bend=None, via=None, ta=0.5, tb=0.5, dash=False):
    """
    `via` is an explicit list of corridor coordinates - ('x', 570), ('y', 451) -
    walked in order, so a long connector is steered through the gutters instead
    of being left to find its own way across the page.
    """
    p0, p1 = side(a, va, ta), side(b, vb, tb)
    pts, cur = [p0], p0
    if via:
        for axis, val in via:
            nxt = (val, cur[1]) if axis == 'x' else (cur[0], val)
            if nxt != cur:
                pts.append(nxt); cur = nxt
        # Enter the target edge straight on, at whatever the corridor left us
        # at - snapping back to the edge centre is what sent earlier versions
        # of these lines back through the cards they had just cleared.
        p1 = (p1[0], cur[1]) if vb in 'lr' else (cur[0], p1[1])
    elif va in 'lr' and vb in 'lr':
        mx = bend if bend is not None else (p0[0] + p1[0]) / 2
        pts += [(mx, p0[1]), (mx, p1[1])]
    elif va in 'tb' and vb in 'tb':
        my = bend if bend is not None else (p0[1] + p1[1]) / 2
        pts += [(p0[0], my), (p1[0], my)]
    elif va in 'lr' and vb in 'tb':
        pts += [(p1[0], p0[1])]
    else:
        pts += [(p0[0], p1[1])]
    pts.append(p1)
    out = [pts[0]]
    for q in pts[1:]:
        if q != out[-1]:
            out.append(q)
    LINKS.append((a, b, out))
    c = FLOW[kind]
    da = ' stroke-dasharray="4 3"' if dash else ''
    add('<path d="%s" fill="none" stroke="%s" stroke-width="1.3" '
        'stroke-linecap="round" opacity=".85" marker-end="url(#ar-%s)"%s/>'
        % (rounded(out), c, kind, da))


# --- primitives ---------------------------------------------------------------

def zone(x, y, w, h, label, accent):
    add('<rect x="%s" y="%s" width="%s" height="%s" rx="10" fill="%s" stroke="%s"/>'
        % (x, y, w, h, CARD, LINE))
    add('<rect x="%s" y="%s" width="4" height="%s" rx="2" fill="%s" opacity=".55"/>'
        % (x, y, h, accent))
    add('<text x="%s" y="%s" font-size="9" font-weight="700" letter-spacing="1.4" '
        'fill="%s">%s</text>' % (x + 14, y + 17, MUTED, esc(label.upper())))


def card(cid, x, y, w, h, title, chips=(), accent=INK, mark=None):
    anchors[cid] = (x, y, w, h)
    add('<rect x="%s" y="%s" width="%s" height="%s" rx="7" fill="%s" stroke="%s"/>'
        % (x, y, w, h, GROUND, LINE))
    add('<rect x="%s" y="%s" width="3" height="%s" rx="1.5" fill="%s"/>'
        % (x, y + 6, h - 12, accent))
    add('<text x="%s" y="%s" font-size="9.6" font-weight="700" fill="%s" '
        'letter-spacing=".2">%s</text>' % (x + 10, y + 15, INK, esc(title)))
    if mark:
        add('<text x="%s" y="%s" font-size="11" text-anchor="end" fill="%s" '
            'font-weight="700">%s</text>' % (x + w - 9, y + 15, accent, esc(mark)))
    cy = y + 27
    for c in chips:
        add('<text x="%s" y="%s" font-size="7.6" fill="%s">%s</text>'
            % (x + 10, cy, MUTED, esc(c)))
        cy += 9.4


def states(x, y, w, label, items, colour):
    """One state machine as a row of pills - the states a record moves through."""
    add('<text x="%s" y="%s" font-size="7" font-weight="700" letter-spacing="1.1" '
        'fill="%s">%s</text>' % (x, y + 14, FAINT, esc(label)))
    px = x + 34
    pw = (w - 34 - (len(items) - 1) * 15) / len(items)
    for i, it in enumerate(items):
        add('<rect x="%.1f" y="%s" width="%.1f" height="19" rx="9.5" fill="%s" '
            'stroke="%s" stroke-width="1"/>' % (px, y, pw, CARD, colour))
        add('<text x="%.1f" y="%s" font-size="7.2" font-weight="600" '
            'text-anchor="middle" fill="%s">%s</text>'
            % (px + pw / 2, y + 13, colour, esc(it)))
        if i < len(items) - 1:
            add('<path d="M %.1f %s L %.1f %s" stroke="%s" stroke-width="1.1" '
                'marker-end="url(#ar-%s)" opacity=".8"/>'
                % (px + pw + 3, y + 9.5, px + pw + 10, y + 9.5, colour,
                   'work' if colour == FLOW['work'] else 'in'))
        px += pw + 15


def grid(zx, zy, zw, zh, cols, rows, pad=10, top=24, gx=8, gy=8):
    cw = (zw - pad * 2 - gx * (cols - 1)) / cols
    ch = (zh - top - pad - gy * (rows - 1)) / rows
    return [(zx + pad + c * (cw + gx), zy + top + r * (ch + gy), cw, ch)
            for r in range(rows) for c in range(cols)]


# --- canvas -------------------------------------------------------------------

add('<rect width="%s" height="%s" fill="%s"/>' % (W, H, GROUND))
add('<defs>')
for k, c in FLOW.items():
    add('<marker id="ar-%s" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="5.5" '
        'markerHeight="5.5" orient="auto"><path d="M0 1 L7 4 L0 7 z" fill="%s"/></marker>'
        % (k, c))
add('</defs>')

add('<text x="18" y="34" font-size="23" font-weight="800" fill="%s" '
    'letter-spacing="-.3">WorkshopOS</text>' % INK)
add('<text x="151" y="34" font-size="23" font-weight="300" fill="%s">/ Titan</text>' % FAINT)
add('<text x="18" y="48" font-size="8" font-weight="600" letter-spacing="1.7" '
    'fill="%s">SYSTEM MAP</text>' % MUTED)

lg = [('work', 'workflow'), ('in', 'money in'), ('out', 'money out'),
      ('stock', 'stock'), ('data', 'data'), ('alert', 'alert / audit')]
lx = W - 18
for k, lb in reversed(lg):
    lx -= len(lb) * 4.35 + 26
    add('<line x1="%.1f" y1="31" x2="%.1f" y2="31" stroke="%s" stroke-width="2.2" '
        'stroke-linecap="round"/>' % (lx, lx + 11, FLOW[k]))
    add('<text x="%.1f" y="34" font-size="7.8" fill="%s">%s</text>' % (lx + 15, MUTED, lb))
add('<text x="%s" y="47" font-size="7.4" text-anchor="end" fill="%s">'
    '&#8709; connected to nothing, by design</text>' % (W - 18, FAINT))
add('<line x1="18" y1="57" x2="%s" y2="57" stroke="%s"/>' % (W - 18, LINE))

# --- layout -------------------------------------------------------------------
H1, H2 = 451, 749          # horizontal corridors, between the bands
VL, VR = 262, 1088         # vertical corridors, either side of Operations
VM, VE = 570, 1010         # vertical corridors inside band 2

B1Y, B1H = 66, 374
B2Y, B2H = 462, 276
B3Y, B3H = 760, 226

zone(16, B1Y, 236, B1H, 'Access', FLOW['alert'])
for (cid, t, ch), cell in zip([
        ('r_own',  'OWNER',          ['everything']),
        ('r_off',  'OFFICE',         ['no cost side']),
        ('r_flo',  'FLOOR',          ['no prices']),
        ('signin', 'SIGN-IN',        ['user - email - mobile']),
        ('lock',   'LOCKOUT',        ['5 account - 20 network']),
        ('reset',  'PASSWORD RESET', ['6-digit code by email']),
        ('hub',    'CONTROL HUB',    ['logins - staff - sessions'])],
        grid(16, B1Y, 236, B1H, 1, 7, top=26)):
    card(cid, *cell, t, ch, accent=FLOW['alert'])

zone(272, B1Y, 806, B1H, 'Operations', FLOW['work'])
card('est', 282, B1Y + 28, 246, 56, 'ESTIMATE', ['quote - print - history'],
     accent=FLOW['work'], mark='⊘')

states(548, B1Y + 26, 520, 'CARD',
       ['ADMITTED', 'WORKING', 'ON HOLD', 'COMPLETED'], FLOW['work'])
states(548, B1Y + 58, 520, 'BILL',
       ['PENDING', 'PART PAID', 'PAID', 'FLEET PAID'], FLOW['in'])

JX, JY, JW, JH = 282, B1Y + 96, 786, 152
add('<rect x="%s" y="%s" width="%s" height="%s" rx="9" fill="%s" stroke="%s" '
    'stroke-width="1.6"/>' % (JX, JY, JW, JH, CARD, FLOW['work']))
anchors['job'] = (JX, JY, JW, JH)
add('<text x="%s" y="%s" font-size="14" font-weight="800" fill="%s" '
    'letter-spacing=".3">JOB CARD</text>' % (JX + 14, JY + 22, FLOW['work']))
add('<text x="%s" y="%s" font-size="8" fill="%s">the hub</text>' % (JX + 106, JY + 22, FAINT))

secs = [('sec_veh', 'VEHICLE', 'reg - brand - km'),
        ('sec_cus', 'CUSTOMER', 'office only'),
        ('sec_con', 'CONCERNS', 'pending to fixed'),
        ('sec_lab', 'JOBS', 'one labour charge'),
        ('sec_spr', 'SPARE PARTS', 'from a shop'),
        ('sec_inv', 'INVENTORY', 'off the shelf'),
        ('sec_pho', 'PHOTOS', 'car - part')]
sw = (JW - 28 - 6 * 5) / 7
for i, (cid, t, sub) in enumerate(secs):
    sx, sy = JX + 14 + i * (sw + 5), JY + 34
    anchors[cid] = (sx, sy, sw, 100)
    add('<rect x="%.1f" y="%s" width="%.1f" height="100" rx="6" fill="%s" stroke="%s"/>'
        % (sx, sy, sw, GROUND, LINE))
    add('<rect x="%.1f" y="%s" width="%.1f" height="2.5" rx="1.2" fill="%s" opacity=".5"/>'
        % (sx, sy, sw, FLOW['work']))
    add('<text x="%.1f" y="%s" font-size="8.4" font-weight="700" text-anchor="middle" '
        'fill="%s">%s</text>' % (sx + sw / 2, sy + 24, INK, esc(t)))
    add('<text x="%.1f" y="%s" font-size="6.9" text-anchor="middle" fill="%s">%s</text>'
        % (sx + sw / 2, sy + 36, MUTED, esc(sub)))

for i, (cid, t, ch, ac) in enumerate([
        ('done',   'COMPLETED',        ['car handed over'],     FLOW['work']),
        ('bill',   'INVOICE',          ['A4 - one parts list'], FLOW['work']),
        ('settle', 'SETTLEMENT CHECK', ['what is unfilled'],    FLOW['alert'])]):
    card(cid, 282 + i * 238, B1Y + 264, 230, 56, t, ch, accent=ac)

zone(1098, B1Y, 300, B1H, 'Boards & History', FLOW['data'])
for (cid, t, ch), cell in zip([
        ('dash',  'DASHBOARD',        ['cars on the floor - progress']),
        ('live',  'LIVE REPORT',      ['billed but unfilled - crews']),
        ('cars',  'CAR PROFILES',     ['history by registration']),
        ('jlist', 'JOB CARDS',        ['search - filter']),
        ('ehist', 'ESTIMATE HISTORY', ['searchable'])],
        grid(1098, B1Y, 300, B1H, 1, 5, top=26)):
    card(cid, *cell, t, ch, accent=FLOW['data'])

zone(16, B2Y, 544, B2H, 'Parts, Shops & Stock', FLOW['stock'])
for (cid, t, ch, ac), cell in zip([
        ('sshop', 'SPARE SHOPS',    ['ledger - balance - pay'],     FLOW['out']),
        ('supp',  'SUPPLIES SHOPS', ['suppliers - catalog'],        FLOW['out']),
        ('ware',  'WAREHOUSE',      ['items - categories'],         FLOW['stock']),
        ('unass', 'UNASSIGNED',     ['floor adds - office prices'], FLOW['out']),
        ('rest',  'RESTOCK BILLS',  ['discount pro-rata'],          FLOW['out']),
        ('low',   'LOW STOCK',      ['under 25% - negatives'],      FLOW['stock']),
        ('shist', 'STOCK HISTORY',  ['who drew what'],              FLOW['stock']),
        ('sig',   'STOCK SIGNALS',  ['8 handlers - automatic'],     FLOW['stock']),
        ('cost',  'AVERAGE COST',   ['weighted - full replay'],     FLOW['stock'])],
        grid(16, B2Y, 544, B2H, 3, 3, top=26)):
    card(cid, *cell, t, ch, accent=ac)

zone(580, B2Y, 420, B2H, 'Money', FLOW['in'])
add('<text x="593" y="%s" font-size="7.6" font-weight="700" letter-spacing="1.2" '
    'fill="%s">IN</text>' % (B2Y + 36, FLOW['in']))
add('<text x="797" y="%s" font-size="7.6" font-weight="700" letter-spacing="1.2" '
    'fill="%s">OUT</text>' % (B2Y + 36, FLOW['out']))
for (cid, t, ch, ac), cell in zip([
        ('pend',  'PENDING BILLS',    ['unpaid - part paid'],       FLOW['in']),
        ('cash',  'CASHBOOK',         ['rent - power - scrap'],     FLOW['out']),
        ('paid',  'PAID BILLS',       ['settled - by date'],        FLOW['in']),
        ('sal',   'SALARY & ADVANCE', ['advances - settlement'],    FLOW['out']),
        ('fleet', 'FLEET ACCOUNTS',   ['cascade - advance credit'], FLOW['in']),
        ('disc',  'DISCOUNT AUDIT',   ['over ₹3,500'],         FLOW['alert'])],
        grid(580, B2Y, 420, B2H, 2, 3, top=42)):
    card(cid, *cell, t, ch, accent=ac)

zone(1020, B2Y, 378, B2H, 'Analysis & Audit', FLOW['data'])
for (cid, t, ch, ac), cell in zip([
        ('profit', 'PROFIT', ['turnover − expenses',
                              'the distribution figure'],          FLOW['in']),
        ('deep',   'DEEP ANALYSIS', ['mechanics - spares - vehicles',
                                     'fleet - shops - operations'], FLOW['data']),
        ('del',    'DELETION HISTORY', ['every permanent delete',
                                        'owner only - no restore'], FLOW['alert'])],
        grid(1020, B2Y, 378, B2H, 1, 3, top=26)):
    card(cid, *cell, t, ch, accent=ac)

zone(16, B3Y, 674, B3H, 'Rules & Automation', FLOW['data'])
for (cid, t, ch, ac), cell in zip([
        ('mast',  'MASTER LISTS',   ['brands - models', 'spares - concerns'], FLOW['data']),
        ('auto',  'AUTOCOMPLETE',   ['learns as you type'],                   FLOW['data']),
        ('clean', 'DATA CLEANUP',   ['rename - merge', 'reaches old cards'],  FLOW['data']),
        ('rbac',  'RBAC',           ['3 tiers - server side'],                FLOW['alert']),
        ('lockf', 'FINANCIAL LOCK', ['settled = frozen'],                     FLOW['alert']),
        ('money', 'MONEY BOUNDS',   ['read from the column'],                 FLOW['alert']),
        ('dates', 'DATE RULES',     ['ordered before received'],              FLOW['alert']),
        ('arch',  'ARCHIVE MODEL',  ['accounts archived', 'records logged'],  FLOW['alert'])],
        grid(16, B3Y, 674, B3H, 4, 2, top=26)):
    card(cid, *cell, t, ch, accent=ac)

zone(710, B3Y, 688, B3H, 'Platform', FLOW['work'])
for (cid, t, ch, ac), cell in zip([
        ('notif', 'NOTIFICATIONS', ['14 events - 10 critical', 'owners only'],   FLOW['alert']),
        ('push',  'WEB PUSH',      ['critical goes to a phone'],                 FLOW['alert']),
        ('store', 'PHOTO STORAGE', ['S3 - presigned', 'browser uploads direct'], FLOW['work']),
        ('mail',  'EMAIL',         ['reset codes only'],                         FLOW['alert']),
        ('pwa',   'INSTALLABLE',   ['home screen - offline page'],               FLOW['work']),
        ('back',  'BACKUPS',       ['pg_dump - keeps 14'],                       FLOW['work'])],
        grid(710, B3Y, 688, B3H, 3, 2, top=26)):
    card(cid, *cell, t, ch, accent=ac)

# --- connections --------------------------------------------------------------
# Row 3 leaves two 8px gaps (x=516, x=754) and a clear channel at x=1010.
# Every line that has to cross band 1 vertically uses one of those three.
G1, G2, CH = 516, 754, 1010

link('r_off', 'job', 'alert', 'r', 'l', bend=VL, tb=0.35)
link('r_flo', 'job', 'alert', 'r', 'l', bend=VL, tb=0.65)

link('job', 'done', 'work', 'b', 't', bend=B1Y + 252, ta=0.12)
link('done', 'bill', 'work', 'r', 'l')
link('bill', 'settle', 'work', 'r', 'l')

# Parts leave the hub by one of exactly two routes
link('sec_spr', 'sshop', 'out',   'b', 't',
     via=[('y', 322), ('x', G2), ('y', H1 + 1), ('x', 77)])
link('sec_inv', 'ware',  'stock', 'b', 't',
     via=[('y', 326), ('x', G1), ('y', H1 + 7), ('x', 465)])

# On and off the shelf
link('supp', 'rest', 'out', 'b', 't')
link('rest', 'ware', 'stock', 'r', 'l', bend=376)
link('ware', 'low',  'stock', 'b', 't')
link('sig',  'cost', 'stock', 'r', 'l')
link('sig',  'shist', 'stock', 'l', 'r')
link('unass', 'sshop', 'out',  't', 'b')

# The bill reaches the money states
link('bill', 'pend', 'in', 'b', 't', via=[('y', H1 - 4), ('x', 645)])
link('pend', 'paid', 'in', 'b', 't')
link('paid', 'fleet', 'in', 'b', 't')

# Every stream of money reaches one number
link('sshop', 'profit', 'out', 't', 'l', ta=0.75,
     via=[('y', H1 - 6), ('x', 1004), ('y', 500)])
link('supp',  'profit', 'out', 't', 'l', ta=0.75,
     via=[('y', H1 + 13), ('x', 1000), ('y', 510)])
link('paid',  'profit', 'in',  'r', 'l', via=[('y', 577), ('x', 1006), ('y', 522)])
link('fleet', 'profit', 'in',  'r', 'l', via=[('y', 654), ('x', 1002), ('y', 534)])
link('cash',  'profit', 'out', 'r', 'l', via=[('x', 1010), ('y', 546)])
link('sal',   'profit', 'out', 'r', 'l', via=[('x', 1014), ('y', 556)])

# Reference data feeds the hub; the hub feeds the boards
link('mast', 'auto', 'data', 'r', 'l')
link('auto', 'job',  'data', 't', 'b', via=[('y', H2 - 10), ('x', CH)])
link('job',  'dash', 'data', 'r', 'l', bend=VR, ta=0.3)
link('job',  'cars', 'data', 'r', 'l', bend=VR + 5, ta=0.7)
link('settle', 'live', 'alert', 'r', 'l')

# Audit, alerting and evidence
link('disc', 'notif', 'alert', 'b', 't', via=[('y', H2 - 4), ('x', 828)])
link('del',  'notif', 'alert', 'b', 't', via=[('y', H2 + 4), ('x', 890)])
link('notif', 'push', 'alert', 'r', 'l')
link('sec_pho', 'store', 'work', 'b', 't',
     via=[('y', 330), ('x', CH - 6), ('y', H2 + 10), ('x', 1279)])

svg = ('<svg viewBox="0 0 %s %s" width="100%%" xmlns="http://www.w3.org/2000/svg" '
       'font-family="Inter, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif">'
       % (W, H)) + '\n'.join(parts) + '</svg>'

page = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WorkshopOS - System Map</title>
<style>
  *{margin:0;padding:0;box-sizing:border-box}
  html,body{background:#eef2f6;
    font-family:Inter,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
  .sheet{width:min(calc(100vw - 24px),1720px);margin:18px auto;background:__G__;
    border-radius:12px;box-shadow:0 10px 40px rgba(15,23,42,.16);overflow:hidden}
  svg{display:block;width:100%;height:auto}
  @page{size:A4 landscape;margin:0}
  @media print{
    html,body{background:#fff}
    .sheet{width:100%;margin:0;border-radius:0;box-shadow:none}
    *{-webkit-print-color-adjust:exact!important;print-color-adjust:exact!important}
  }
</style>
</head>
<body>
<div class="sheet">
__SVG__
</div>
</body>
</html>
""".replace('__G__', GROUND).replace('__SVG__', svg)

out = Path(__file__).resolve().parent.parent / 'SYSTEM_MAP.html'
out.write_text(page, encoding='utf-8')
print('wrote %s  (%.1f KB, %d cards)' % (out.name, len(page) / 1024, len(anchors)))
