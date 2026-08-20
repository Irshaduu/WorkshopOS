"""
Generates SYSTEM_MAP.html and SYSTEM_MAP_DARK.html - the one-page visual map of
WorkshopOS, in a light and a dark theme from one set of coordinates.

WHY THIS IS GENERATED RATHER THAN HAND-WRITTEN
----------------------------------------------
The map is ~56 cards and ~30 connectors on a fixed A4-landscape canvas. Hand
placing that in raw SVG means every nudge to one zone is a manual re-flow of
everything after it. Here a zone declares its grid and the cards fall into it,
and connectors are drawn between *named anchors* rather than typed coordinates,
so moving a card moves its lines with it. The two themes are the same geometry
with a different palette - they cannot drift apart.

WHY NOT MERMAID
---------------
Mermaid picks its own layout. It cannot be made to fill a page, it sprawls
vertically on a landscape sheet, and it wants a label on every edge. This
drawing has to be dense, deliberate and wordless - colour carries the meaning
that edge labels would otherwise spend sentences on.

CORRIDORS ARE THE WHOLE TRICK
-----------------------------
Zones are separated by real gutters and every long connector is routed through
one. A first version packed the zones tight and let the router find its own way:
12 of 32 lines cut straight through unrelated cards.

AND PARALLEL SAME-COLOUR LINES ARE THE OTHER HALF
-------------------------------------------------
Not crossing a card is not enough. Three long red lines running side by side
through one corridor are individually correct and collectively unreadable - you
cannot tell which one you are following. The four expense streams are therefore
drawn as ONE trunk with short taps into it, which is both easier to follow and a
truer picture: they really do add up to a single number.

ONE PAGE, EXACTLY
-----------------
The print block pins the sheet to 297x210mm with overflow hidden. Without that
the SVG's computed height rounds a fraction over the page box and the browser
emits a second, blank page.

    python scratchpad/build_system_map.py
"""

import html
from pathlib import Path

W, H = 1414, 1000          # A4 landscape ratio (1.414)

THEMES = {
    'light': dict(
        out='SYSTEM_MAP.html',
        INK='#0f172a', MUTED='#64748b', FAINT='#94a3b8',
        LINE='#e2e8f0', GROUND='#f8fafc', CARD='#ffffff',
        CHROME='#eef2f6', SHADOW='rgba(15,23,42,.16)',
        FLOW={'work': '#2563eb', 'in': '#059669', 'out': '#dc2626',
              'stock': '#7c3aed', 'data': '#94a3b8', 'alert': '#f59e0b'},
    ),
    'dark': dict(
        out='SYSTEM_MAP_DARK.html',
        INK='#f1f5f9', MUTED='#94a3b8', FAINT='#64748b',
        LINE='#334155', GROUND='#0f172a', CARD='#1e293b',
        CHROME='#020617', SHADOW='rgba(0,0,0,.55)',
        # Lifted off the light palette: the same hues need more luminance to
        # keep their meaning against a dark ground.
        FLOW={'work': '#60a5fa', 'in': '#34d399', 'out': '#f87171',
              'stock': '#a78bfa', 'data': '#94a3b8', 'alert': '#fbbf24'},
    ),
}


def build(theme):
    T = THEMES[theme]
    INK, MUTED, FAINT = T['INK'], T['MUTED'], T['FAINT']
    LINE, GROUND, CARD = T['LINE'], T['GROUND'], T['CARD']
    FLOW = T['FLOW']

    parts, anchors, links = [], {}, []

    def add(s):
        parts.append(s)

    def esc(s):
        return html.escape(str(s), quote=True)

    # --- geometry ------------------------------------------------------------
    def rounded(points, r=7):
        d = ['M %.1f %.1f' % points[0]]
        for i in range(1, len(points) - 1):
            x0, y0 = points[i - 1]
            x1, y1 = points[i]
            x2, y2 = points[i + 1]
            dx1, dy1 = x1 - x0, y1 - y0
            l1 = max(abs(dx1), abs(dy1)) or 1
            r1 = min(r, l1 / 2)
            ax, ay = x1 - dx1 / l1 * r1, y1 - dy1 / l1 * r1
            dx2, dy2 = x2 - x1, y2 - y1
            l2 = max(abs(dx2), abs(dy2)) or 1
            r2 = min(r, l2 / 2)
            bx, by = x1 + dx2 / l2 * r2, y1 + dy2 / l2 * r2
            d.append('L %.1f %.1f Q %.1f %.1f %.1f %.1f' % (ax, ay, x1, y1, bx, by))
        d.append('L %.1f %.1f' % points[-1])
        return ' '.join(d)

    def side(cid, where, t=0.5):
        x, y, w, h = anchors[cid]
        return {'l': (x, y + h * t), 'r': (x + w, y + h * t),
                't': (x + w * t, y), 'b': (x + w * t, y + h)}[where]

    def draw(pts, kind, width=1.3, arrow=True):
        out = [pts[0]]
        for q in pts[1:]:
            if q != out[-1]:
                out.append(q)
        add('<path d="%s" fill="none" stroke="%s" stroke-width="%s" '
            'stroke-linecap="round" opacity=".9"%s/>'
            % (rounded(out), FLOW[kind], width,
               ' marker-end="url(#ar-%s)"' % kind if arrow else ''))
        return out

    def link(a, b, kind, va='r', vb='l', bend=None, via=None, ta=0.5, tb=0.5):
        p0, p1 = side(a, va, ta), side(b, vb, tb)
        pts, cur = [p0], p0
        if via:
            for axis, val in via:
                nxt = (val, cur[1]) if axis == 'x' else (cur[0], val)
                if nxt != cur:
                    pts.append(nxt)
                    cur = nxt
            # Enter the target edge straight on, at whatever the corridor left
            # us at - snapping back to the edge centre is what sent earlier
            # versions of these lines back through cards they had just cleared.
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
        links.append((a, b, draw(pts, kind)))

    def trunk(pts, kind):
        """A shared rail. Carries no arrow - the taps and the outlet do."""
        draw(pts, kind, width=2.1, arrow=False)

    def tap(cid, where, t, point, kind):
        """One card feeding the trunk."""
        links.append((cid, None, draw([side(cid, where, t), point], kind)))
        add('<circle cx="%.1f" cy="%.1f" r="2.6" fill="%s"/>'
            % (point[0], point[1], FLOW[kind]))

    # --- primitives ----------------------------------------------------------
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

    def states(x, y, w, label, items, kind):
        colour = FLOW[kind]
        add('<text x="%s" y="%s" font-size="7" font-weight="700" letter-spacing="1.1" '
            'fill="%s">%s</text>' % (x, y + 14, FAINT, esc(label)))
        px = x + 34
        pw = (w - 34 - (len(items) - 1) * 15) / len(items)
        for i, it in enumerate(items):
            add('<rect x="%.1f" y="%s" width="%.1f" height="19" rx="9.5" fill="%s" '
                'stroke="%s"/>' % (px, y, pw, CARD, colour))
            add('<text x="%.1f" y="%s" font-size="7.2" font-weight="600" '
                'text-anchor="middle" fill="%s">%s</text>'
                % (px + pw / 2, y + 13, colour, esc(it)))
            if i < len(items) - 1:
                add('<path d="M %.1f %s L %.1f %s" stroke="%s" stroke-width="1.1" '
                    'marker-end="url(#ar-%s)" opacity=".85"/>'
                    % (px + pw + 3, y + 9.5, px + pw + 10, y + 9.5, colour, kind))
            px += pw + 15

    def grid(zx, zy, zw, zh, cols, rows, pad=10, top=24, gx=8, gy=8):
        cw = (zw - pad * 2 - gx * (cols - 1)) / cols
        ch = (zh - top - pad - gy * (rows - 1)) / rows
        return [(zx + pad + c * (cw + gx), zy + top + r * (ch + gy), cw, ch)
                for r in range(rows) for c in range(cols)]

    # --- canvas --------------------------------------------------------------
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

    lx = W - 18
    for k, lb in reversed([('work', 'workflow'), ('in', 'money in'), ('out', 'money out'),
                           ('stock', 'stock'), ('data', 'data'), ('alert', 'alert / audit')]):
        lx -= len(lb) * 4.35 + 26
        add('<line x1="%.1f" y1="31" x2="%.1f" y2="31" stroke="%s" stroke-width="2.2" '
            'stroke-linecap="round"/>' % (lx, lx + 11, FLOW[k]))
        add('<text x="%.1f" y="34" font-size="7.8" fill="%s">%s</text>' % (lx + 15, MUTED, lb))
    add('<text x="%s" y="47" font-size="7.4" text-anchor="end" fill="%s">'
        '&#8709; connected to nothing, by design</text>' % (W - 18, FAINT))
    add('<line x1="18" y1="57" x2="%s" y2="57" stroke="%s"/>' % (W - 18, LINE))

    # --- layout --------------------------------------------------------------
    H1, H2 = 449, 749          # horizontal corridors between the bands
    VL, VR = 262, 1088         # vertical corridors either side of Operations
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
           ['ADMITTED', 'WORKING', 'ON HOLD', 'COMPLETED'], 'work')
    states(548, B1Y + 58, 520, 'BILL',
           ['PENDING', 'PART PAID', 'PAID', 'FLEET PAID'], 'in')

    JX, JY, JW, JH = 282, B1Y + 96, 786, 152
    add('<rect x="%s" y="%s" width="%s" height="%s" rx="9" fill="%s" stroke="%s" '
        'stroke-width="1.6"/>' % (JX, JY, JW, JH, CARD, FLOW['work']))
    anchors['job'] = (JX, JY, JW, JH)
    add('<text x="%s" y="%s" font-size="14" font-weight="800" fill="%s" '
        'letter-spacing=".3">JOB CARD</text>' % (JX + 14, JY + 22, FLOW['work']))
    add('<text x="%s" y="%s" font-size="8" fill="%s">the hub</text>'
        % (JX + 106, JY + 22, FAINT))

    sw = (JW - 28 - 6 * 5) / 7
    for i, (cid, t, sub) in enumerate([
            ('sec_veh', 'VEHICLE', 'reg - brand - km'),
            ('sec_cus', 'CUSTOMER', 'office only'),
            ('sec_con', 'CONCERNS', 'pending to fixed'),
            ('sec_lab', 'JOBS', 'one labour charge'),
            ('sec_spr', 'SPARE PARTS', 'from a shop'),
            ('sec_inv', 'INVENTORY', 'off the shelf'),
            ('sec_pho', 'PHOTOS', 'car - part')]):
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
            ('disc',  'DISCOUNT AUDIT',   ['over ₹3,500'],              FLOW['alert'])],
            grid(580, B2Y, 420, B2H, 2, 3, top=42)):
        card(cid, *cell, t, ch, accent=ac)

    zone(1030, B2Y, 368, B2H, 'Analysis & Audit', FLOW['data'])
    for (cid, t, ch, ac), cell in zip([
            ('profit', 'PROFIT', ['turnover − expenses',
                                  'the distribution figure'],           FLOW['in']),
            ('deep',   'DEEP ANALYSIS', ['mechanics - spares - vehicles',
                                         'fleet - shops - operations'], FLOW['data']),
            ('del',    'DELETION HISTORY', ['every permanent delete',
                                            'owner only - no restore'], FLOW['alert'])],
            grid(1030, B2Y, 368, B2H, 1, 3, top=26)):
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

    # --- connections ---------------------------------------------------------
    # Row 3 leaves two 8px gaps (x=516, x=754) and the channel right of it is
    # clear from x=988. Everything crossing band 1 vertically uses one of those.
    G1, G2 = 516, 754

    link('r_off', 'job', 'alert', 'r', 'l', bend=VL, tb=0.35)
    link('r_flo', 'job', 'alert', 'r', 'l', bend=VL, tb=0.65)

    link('job', 'done', 'work', 'b', 't', bend=B1Y + 252, ta=0.12)
    link('done', 'bill', 'work', 'r', 'l')
    link('bill', 'settle', 'work', 'r', 'l')

    # Parts leave the hub by one of exactly two routes. The shop route drops
    # into band 1's own bottom padding (y=434) rather than the H1 corridor, so
    # it never runs alongside the red expense trunk.
    link('sec_spr', 'sshop', 'out', 'b', 'r',
         via=[('y', 322), ('x', G1), ('y', 434), ('x', 199), ('y', 525)])
    link('sec_inv', 'ware', 'stock', 'b', 't',
         via=[('y', 326), ('x', G2), ('y', 458), ('x', 465)])

    # On and off the shelf
    link('supp', 'rest', 'out', 'b', 't')
    link('rest', 'ware', 'stock', 'r', 'l', bend=376)
    link('ware', 'low', 'stock', 'b', 't')
    link('sig', 'cost', 'stock', 'r', 'l')
    link('sig', 'shist', 'stock', 'l', 'r')
    link('unass', 'sshop', 'out', 't', 'b')

    # The bill reaches the money states
    link('bill', 'pend', 'in', 'b', 't', via=[('y', 443), ('x', 645)])
    link('pend', 'paid', 'in', 'b', 't')
    link('paid', 'fleet', 'in', 'b', 't')

    # THE EXPENSE TRUNK. Four streams, one rail, one arrow into PROFIT - drawn
    # this way because four long red lines side by side cannot be told apart.
    TX, TY = 1006, H1
    trunk([(140, TY), (TX, TY), (TX, 620)], 'out')
    tap('sshop', 't', 0.75, (153, TY), 'out')
    tap('supp', 't', 0.25, (245.7, TY), 'out')
    tap('cash', 'r', 0.5, (TX, 538.7), 'out')
    tap('sal', 'r', 0.5, (TX, 616), 'out')
    add('<circle cx="%s" cy="520" r="2.6" fill="%s"/>' % (TX, FLOW['out']))
    draw([(TX, 520), (1040, 520)], 'out')

    # Revenue reaches it too, through the money zone's own row gaps
    link('paid', 'profit', 'in', 'r', 'l', via=[('y', 577), ('x', 1014), ('y', 535)])
    link('fleet', 'profit', 'in', 'r', 'l', via=[('y', 654), ('x', 1020), ('y', 550)])

    # Reference data feeds the hub; the hub feeds the boards
    link('mast', 'auto', 'data', 'r', 'l')
    link('auto', 'job', 'data', 't', 'b',
         via=[('y', H2 - 10), ('x', 570), ('y', 452), ('x', G1)])
    link('job', 'dash', 'data', 'r', 'l', bend=VR, ta=0.3)
    link('job', 'cars', 'data', 'r', 'l', bend=VR + 5, ta=0.7)
    link('settle', 'live', 'alert', 'r', 'l')

    # Audit, alerting and evidence
    # These three share the H2 corridor, so they are spaced by hand: 745, 752,
    # and 766 (that last one riding inside Platform's own label strip, which is
    # empty this far right). Closer than ~12px and two lines read as one.
    link('disc', 'notif', 'alert', 'b', 't', via=[('y', 745), ('x', 828)])
    link('del', 'notif', 'alert', 'b', 't', via=[('y', 752), ('x', 890)])
    link('notif', 'push', 'alert', 'r', 'l')
    link('sec_pho', 'store', 'work', 'b', 't',
         via=[('y', 330), ('x', 1026), ('y', 766), ('x', 1279)])

    svg = ('<svg viewBox="0 0 %s %s" preserveAspectRatio="xMidYMid meet" '
           'xmlns="http://www.w3.org/2000/svg" '
           'font-family="Inter, -apple-system, Segoe UI, Roboto, Helvetica, Arial, '
           'sans-serif">' % (W, H)) + '\n'.join(parts) + '</svg>'

    page = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WorkshopOS - System Map</title>
<style>
  *{margin:0;padding:0;box-sizing:border-box}
  html,body{background:__CHROME__;
    font-family:Inter,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
  .sheet{width:min(calc(100vw - 24px),1720px);margin:18px auto;background:__GROUND__;
    border-radius:12px;box-shadow:0 10px 40px __SHADOW__;overflow:hidden}
  svg{display:block;width:100%;height:auto}

  /* Exactly one page. Pinning the sheet to the paper box and hiding the
     overflow is what stops the SVG's computed height rounding a fraction over
     and emitting a second, blank page. */
  @page{size:A4 landscape;margin:0}
  @media print{
    html,body{background:__GROUND__;width:297mm;height:210mm;overflow:hidden}
    .sheet{width:297mm;height:210mm;margin:0;border-radius:0;box-shadow:none;
      overflow:hidden;break-inside:avoid;page-break-inside:avoid}
    svg{width:100%;height:100%}
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
"""
    for k, v in (('__CHROME__', T['CHROME']), ('__GROUND__', GROUND),
                 ('__SHADOW__', T['SHADOW']), ('__SVG__', svg)):
        page = page.replace(k, v)

    out = Path(__file__).resolve().parent.parent / T['out']
    out.write_text(page, encoding='utf-8')
    print('wrote %-22s (%.1f KB, %d cards, %d connectors)'
          % (T['out'], len(page) / 1024, len(anchors), len(links)))
    return anchors, links


def to_pdf(root):
    """
    Render each sheet to PDF with headless Chrome.

    THIS IS THE ONLY WAY TO GET A CLEAN PDF, and it is worth knowing why. The
    browser's own Print dialog stamps a date, the page title, the file path and
    a page number onto the sheet. That is not something the page can prevent -
    there is no CSS for it, and `@page{margin:0}` only suppresses it in some
    browsers. It is a checkbox in the dialog ("Headers and footers"), which
    means every person who ever prints this has to know to untick it.

    Headless Chrome with --no-pdf-header-footer never adds them, so the PDF is
    generated once, here, and handed over as a file. Nobody has to remember a
    setting.
    """
    import shutil
    import subprocess
    from urllib.request import pathname2url

    candidates = [
        r'C:\Program Files\Google\Chrome\Application\chrome.exe',
        r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
        r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
        r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
    ]
    chrome = next((c for c in candidates if Path(c).exists()), None) \
        or shutil.which('chrome') or shutil.which('chromium') \
        or shutil.which('google-chrome') or shutil.which('msedge')
    if not chrome:
        print('  (no Chrome or Edge found - skipping PDFs; the HTML still works)')
        return

    for T in THEMES.values():
        src = root / T['out']
        pdf = src.with_suffix('.pdf')
        subprocess.run([chrome, '--headless', '--disable-gpu',
                        '--no-pdf-header-footer',
                        '--print-to-pdf=%s' % pdf,
                        'file:' + pathname2url(str(src))],
                       capture_output=True, check=False)
        if pdf.exists():
            print('wrote %-22s (%.0f KB)' % (pdf.name, pdf.stat().st_size / 1024))


if __name__ == '__main__':
    for name in THEMES:
        build(name)
    to_pdf(Path(__file__).resolve().parent.parent)
