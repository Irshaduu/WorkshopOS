"""
Generates SYSTEM_MAP.html and SYSTEM_MAP_DARK.html - the master architectural
system blueprint of WorkshopOS, in light and dark themes from one set of coordinates.

WHY THIS IS GENERATED RATHER THAN HAND-WRITTEN
----------------------------------------------
The map is ~56 cards and ~31 connectors on a fixed A4-landscape canvas (1414 x 1000).
A zone declares its grid and cards fall into it; connectors are drawn between
named anchors rather than typed coordinates, so moving a card moves its lines with it.
The two themes are the same geometry with different palettes - they cannot drift apart.

PURE SQUARE ARCHITECTURAL SCHEMATIC
-----------------------------------
1. Pure square containers with crisp 90-degree geometric edges.
2. CAD precision drafting grid.
3. Clean executive title block and high-contrast telemetry flows.
4. Single outer container with zero corner clutter.

    py scratchpad/build_system_map.py
    py scratchpad/check_system_map.py
"""

import html
from pathlib import Path

W, H = 1414, 1000          # A4 landscape ratio (1.414)

THEMES = {
    'light': dict(
        out='SYSTEM_MAP.html',
        INK='#091322',
        INK_SEC='#334155',
        MUTED='#475569',
        FAINT='#64748b',
        LINE='#cbd5e1',
        LINE_ACCENT='#94a3b8',
        GROUND='#f1f5f9',
        GROUND_ALT='#e2e8f0',
        CARD='#ffffff',
        CARD_INNER='#f8fafc',
        CHROME='#0b1120',
        GRID='#cbd5e1',
        GRID_OPACITY='0.35',
        SHADOW='rgba(15,23,42,.12)',
        CARD_SHADOW='0 2px 6px rgba(0,0,0,0.06)',
        BEVEL_TOP='rgba(255,255,255,0.9)',
        FLOW={
            'work':  '#1d4ed8',   # Blueprint Royal Cobalt
            'in':    '#047857',   # Deep Emerald Revenue
            'out':   '#b91c1c',   # Crimson Expense
            'stock': '#6d28d9',   # Deep Violet Inventory
            'data':  '#475569',   # Slate Telemetry
            'alert': '#b45309',   # Amber Security & Audit
        },
        FLOW_GLOW={
            'work':  'rgba(29,78,216,0.15)',
            'in':    'rgba(4,120,87,0.15)',
            'out':   'rgba(185,28,28,0.15)',
            'stock': 'rgba(109,40,217,0.15)',
            'data':  'rgba(71,85,105,0.12)',
            'alert': 'rgba(180,83,9,0.15)',
        },
        THEME_NAME='LIGHT BLUEPRINT'
    ),
    'dark': dict(
        out='SYSTEM_MAP_DARK.html',
        INK='#f8fafc',
        INK_SEC='#e2e8f0',
        MUTED='#94a3b8',
        FAINT='#64748b',
        LINE='#334155',
        LINE_ACCENT='#475569',
        GROUND='#0b0f19',
        GROUND_ALT='#080b12',
        CARD='#151d30',
        CARD_INNER='#0e1526',
        CHROME='#030712',
        GRID='#1e293b',
        GRID_OPACITY='0.45',
        SHADOW='rgba(0,0,0,.75)',
        CARD_SHADOW='0 4px 12px rgba(0,0,0,0.4)',
        BEVEL_TOP='rgba(255,255,255,0.07)',
        FLOW={
            'work':  '#38bdf8',   # Electric Cyan Flow
            'in':    '#34d399',   # Neon Emerald Inflow
            'out':   '#f87171',   # Coral Crimson Outflow
            'stock': '#a78bfa',   # Radiant Purple Stock
            'data':  '#94a3b8',   # Precision Silver Data
            'alert': '#fbbf24',   # Solar Amber Audit
        },
        FLOW_GLOW={
            'work':  'rgba(56,189,248,0.3)',
            'in':    'rgba(52,211,153,0.3)',
            'out':   'rgba(248,113,113,0.3)',
            'stock': 'rgba(167,139,250,0.3)',
            'data':  'rgba(148,163,184,0.2)',
            'alert': 'rgba(251,191,36,0.3)',
        },
        THEME_NAME='DARK SCHEMATIC'
    ),
}


def build(theme):
    T = THEMES[theme]
    INK, INK_SEC = T['INK'], T['INK_SEC']
    MUTED, FAINT = T['MUTED'], T['FAINT']
    LINE, LINE_ACCENT = T['LINE'], T['LINE_ACCENT']
    GROUND, GROUND_ALT = T['GROUND'], T['GROUND_ALT']
    CARD, CARD_INNER = T['CARD'], T['CARD_INNER']
    FLOW, FLOW_GLOW = T['FLOW'], T['FLOW_GLOW']
    BEVEL_TOP = T['BEVEL_TOP']

    parts, anchors, links = [], {}, []

    def add(s):
        parts.append(s)

    def esc(s):
        return html.escape(str(s), quote=True)

    # --- geometry ------------------------------------------------------------
    def rounded(points, r=6):
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

    def draw(pts, kind, width=1.5, arrow=True):
        out = [pts[0]]
        for q in pts[1:]:
            if q != out[-1]:
                out.append(q)
        path_str = rounded(out)
        if theme == 'dark':
            add('<path d="%s" fill="none" stroke="%s" stroke-width="4.5" '
                'stroke-linecap="square" opacity=".22"/>'
                % (path_str, FLOW[kind]))
        add('<path d="%s" fill="none" stroke="%s" stroke-width="%s" '
            'stroke-linecap="square" stroke-linejoin="miter" opacity=".95"%s/>'
            % (path_str, FLOW[kind], width,
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
        """A high-definition architectural bus power rail."""
        out = [pts[0]]
        for q in pts[1:]:
            if q != out[-1]:
                out.append(q)
        path_str = rounded(out)
        if theme == 'dark':
            add('<path d="%s" fill="none" stroke="%s" stroke-width="6.5" '
                'stroke-linecap="square" opacity=".25"/>'
                % (path_str, FLOW[kind]))
        add('<path d="%s" fill="none" stroke="%s" stroke-width="2.6" '
            'stroke-linecap="square" stroke-linejoin="miter" opacity=".95"/>'
            % (path_str, FLOW[kind]))
        return out

    def tap(cid, where, t, point, kind):
        """One card feeding the trunk with a square/circular terminal node."""
        links.append((cid, None, draw([side(cid, where, t), point], kind, width=1.5)))
        add('<rect x="%.1f" y="%.1f" width="6" height="6" fill="%s" opacity=".35"/>'
            % (point[0] - 3, point[1] - 3, FLOW[kind]))
        add('<rect x="%.1f" y="%.1f" width="4" height="4" fill="%s"/>'
            % (point[0] - 2, point[1] - 2, FLOW[kind]))
        add('<rect x="%.1f" y="%.1f" width="1.6" height="1.6" fill="%s"/>'
            % (point[0] - 0.8, point[1] - 0.8, INK))

    # --- pure square architectural primitives --------------------------------
    def zone(x, y, w, h, code, label, accent):
        # Small circled zone reference indicator
        num = code.split('.')[1].split()[0] if '.' in code else ''
        cx, cy = x + 10, y + 12
        add('<circle cx="%.1f" cy="%.1f" r="7.5" fill="none" stroke="%s" '
            'stroke-width="0.6" opacity=".35"/>' % (cx, cy, accent))
        add('<text x="%.1f" y="%.1f" font-size="6.5" font-weight="700" '
            'text-anchor="middle" fill="%s" opacity=".45">%s</text>'
            % (cx, cy + 2.2, accent, esc(num)))

    def card(cid, x, y, w, h, title, chips=(), accent=INK, mark=None):
        anchors[cid] = (x, y, w, h)
        # Pure square card container with sharp 90-degree corners
        add('<g class="map-card" id="card-%s">' % cid)
        _sh = ' filter="url(#card-shadow)"' if theme == 'dark' else ''
        add('<rect x="%s" y="%s" width="%s" height="%s" fill="%s" stroke="%s" stroke-width="1"%s/>'
            % (x, y, w, h, CARD, LINE, _sh))
        add('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="%s" stroke-width="0.8"/>'
            % (x + 1, y + 1, x + w - 1, y + 1, BEVEL_TOP))
        # Left accent square bar
        add('<rect x="%s" y="%s" width="3" height="%s" fill="%s"/>'
            % (x, y + 4, h - 8, accent))
        # Title
        add('<text x="%s" y="%s" font-size="9.6" font-weight="700" fill="%s" '
            'letter-spacing=".2">%s</text>' % (x + 12, y + 16, INK, esc(title)))
        # Optional architectural symbol / mark
        if mark:
            add('<text x="%s" y="%s" font-size="11" text-anchor="end" fill="%s" '
                'font-weight="700">%s</text>' % (x + w - 9, y + 16, accent, esc(mark)))
        # Chips / metadata lines
        cy = y + 28
        for c in chips:
            add('<text x="%s" y="%s" font-size="7.6" font-weight="500" fill="%s">%s</text>'
                % (x + 12, cy, MUTED, esc(c)))
            cy += 9.6
        add('</g>')

    def states(x, y, w, label, items, kind):
        colour = FLOW[kind]
        # Label square badge
        add('<rect x="%s" y="%s" width="42" height="18" fill="%s" stroke="%s" stroke-width="0.8"/>'
            % (x, y, CARD_INNER, colour))
        add('<text x="%s" y="%s" font-size="7.2" font-weight="800" letter-spacing="1.1" '
            'text-anchor="middle" fill="%s">%s</text>' % (x + 21, y + 12, colour, esc(label)))
        
        px = x + 48
        pw = (w - 48 - (len(items) - 1) * 14) / len(items)
        for i, it in enumerate(items):
            # Pure square state box
            add('<g class="state-pill">')
            add('<rect x="%.1f" y="%s" width="%.1f" height="18" fill="%s" '
                'stroke="%s" stroke-width="1"/>' % (px, y, pw, CARD_INNER, colour))
            # Step index indicator
            add('<rect x="%.1f" y="%s" width="4" height="4" fill="%s"/>'
                % (px + 6, y + 7, colour))
            # Text
            add('<text x="%.1f" y="%s" font-size="7.2" font-weight="700" '
                'text-anchor="middle" fill="%s">%s</text>'
                % (px + 8 + (pw - 8) / 2, y + 12.5, INK_SEC, esc(it)))
            add('</g>')
            if i < len(items) - 1:
                # Inter-state connector arrow
                add('<path d="M %.1f %s L %.1f %s" stroke="%s" stroke-width="1.3" '
                    'marker-end="url(#ar-%s)" opacity=".9"/>'
                    % (px + pw + 2, y + 9, px + pw + 10, y + 9, colour, kind))
            px += pw + 14

    def grid(zx, zy, zw, zh, cols, rows, pad=10, top=32, gx=8, gy=8):
        cw = (zw - pad * 2 - gx * (cols - 1)) / cols
        ch = (zh - top - pad - gy * (rows - 1)) / rows
        return [(zx + pad + c * (cw + gx), zy + top + r * (ch + gy), cw, ch)
                for r in range(rows) for c in range(cols)]

    # --- canvas background & CAD drafting grid -------------------------------
    # Single clean square background
    add('<rect width="%s" height="%s" fill="%s"/>' % (W, H, GROUND))
    
    # SVG Definitions: Markers, Grid Pattern
    add('<defs>')
    add('<pattern id="cad-grid" width="20" height="20" patternUnits="userSpaceOnUse">')
    add('<path d="M 20 0 L 0 0 0 20" fill="none" stroke="%s" stroke-width="0.5" opacity="%s"/>'
        % (T['GRID'], T['GRID_OPACITY']))
    add('</pattern>')
    for k, c in FLOW.items():
        add('<marker id="ar-%s" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="5.5" '
            'markerHeight="5.5" orient="auto">'
            '<path d="M0 1 L7 4 L0 7 z" fill="%s"/></marker>'
            % (k, c))
    # Card shadow filter (dark theme only)
    if theme == 'dark':
        add('<filter id="card-shadow" x="-2%%" y="-2%%" width="104%%" height="108%%">'
            '<feDropShadow dx="0" dy="1.5" stdDeviation="2.5" flood-color="#000" '
            'flood-opacity="0.4"/></filter>')
        add('<radialGradient id="vignette" cx="50%%" cy="50%%" r="72%%" fx="50%%" fy="50%%">'
            '<stop offset="55%%" stop-color="#000" stop-opacity="0"/>'
            '<stop offset="100%%" stop-color="#000" stop-opacity="0.25"/>'
            '</radialGradient>')
    add('</defs>')

    # CAD Grid Overlay
    add('<rect width="%s" height="%s" fill="url(#cad-grid)"/>' % (W, H))

    # --- Precision Border Frame (#8) ------------------------------------------
    add('<rect x="8" y="8" width="%s" height="%s" fill="none" stroke="%s" '
        'stroke-width="0.5" opacity=".4"/>' % (W - 16, H - 16, LINE_ACCENT))

    # --- Coordinate Tick Marks (#6) -------------------------------------------
    tick_cols, tick_rows = 10, 7
    for i in range(1, tick_cols):
        tx = 8 + i * (W - 16) / tick_cols
        add('<line x1="%.1f" y1="8" x2="%.1f" y2="14" stroke="%s" '
            'stroke-width="0.4" opacity=".35"/>' % (tx, tx, LINE_ACCENT))
        add('<line x1="%.1f" y1="%s" x2="%.1f" y2="%s" stroke="%s" '
            'stroke-width="0.4" opacity=".35"/>' % (tx, H - 8, tx, H - 14, LINE_ACCENT))
        add('<text x="%.1f" y="6" font-size="4.5" text-anchor="middle" fill="%s" opacity=".3" '
            'font-family="\'JetBrains Mono\', monospace" font-weight="600">%s</text>'
            % (tx, MUTED, chr(64 + i)))
    for i in range(1, tick_rows):
        ty = 8 + i * (H - 16) / tick_rows
        add('<line x1="8" y1="%.1f" x2="14" y2="%.1f" stroke="%s" '
            'stroke-width="0.4" opacity=".35"/>' % (ty, ty, LINE_ACCENT))
        add('<line x1="%s" y1="%.1f" x2="%s" y2="%.1f" stroke="%s" '
            'stroke-width="0.4" opacity=".35"/>' % (W - 8, ty, W - 14, ty, LINE_ACCENT))
        add('<text x="5" y="%.1f" font-size="4.5" text-anchor="middle" fill="%s" opacity=".3" '
            'font-family="\'JetBrains Mono\', monospace" font-weight="600">%s</text>'
            % (ty + 1.5, MUTED, str(i)))

    # --- Clean Pure Square Title Block ---------------------------------------
    add('<g id="title-block">')
    add('<rect x="16" y="14" width="280" height="34" fill="%s" stroke="%s" stroke-width="1"/>'
        % (CARD, LINE))
    add('<rect x="16" y="14" width="4" height="34" fill="%s"/>' % FLOW['work'])
    add('<text x="28" y="36" font-size="17" font-weight="900" fill="%s" letter-spacing="-.3">WORKSHOP<tspan fill="%s">OS</tspan></text>'
        % (INK, FLOW['work']))
    add('<text x="146" y="36" font-size="13" font-weight="700" font-family="\'JetBrains Mono\', monospace" fill="%s">// TITAN</text>'
        % MUTED)
    add('</g>')

    # Legend Block with square swatches (No isolated text)
    add('<g id="legend-block">')
    lx = W - 16
    legend_items = [
        ('work', 'workflow'),
        ('in', 'money in'),
        ('out', 'money out'),
        ('stock', 'stock flow'),
        ('data', 'telemetry'),
        ('alert', 'alert / audit')
    ]
    leg_x = lx
    for k, lb in reversed(legend_items):
        item_w = len(lb) * 4.9 + 26
        leg_x -= item_w + 6
        add('<rect x="%.1f" y="14" width="%.1f" height="34" fill="%s" stroke="%s" stroke-width="1"/>'
            % (leg_x, item_w, CARD, LINE))
        add('<line x1="%.1f" y1="31" x2="%.1f" y2="31" stroke="%s" stroke-width="2.6" stroke-linecap="square"/>'
            % (leg_x + 8, leg_x + 18, FLOW[k]))
        add('<text x="%.1f" y="34" font-size="8" font-weight="700" fill="%s">%s</text>'
            % (leg_x + 22, MUTED, lb))
    add('</g>')

    # Divider Line
    add('<line x1="16" y1="56" x2="%s" y2="56" stroke="%s" stroke-width="1"/>' % (W - 16, LINE))

    # --- layout geometry -----------------------------------------------------
    H1, H2 = 449, 749          # horizontal corridors between bands
    VL, VR = 262, 1088         # vertical corridors either side of Operations
    B1Y, B1H = 66, 374
    B2Y, B2H = 462, 276
    B3Y, B3H = 760, 226

    # --- Dashed Band Separators (#2) ------------------------------------------
    add('<line x1="16" y1="%s" x2="%s" y2="%s" stroke="%s" stroke-width="0.5" '
        'stroke-dasharray="8 4" opacity=".25"/>' % (H1, W - 16, H1, LINE_ACCENT))
    add('<line x1="16" y1="%s" x2="%s" y2="%s" stroke="%s" stroke-width="0.5" '
        'stroke-dasharray="8 4" opacity=".25"/>' % (H2, W - 16, H2, LINE_ACCENT))

    # =========================================================================
    # ZONE 01: ACCESS & RBAC
    # =========================================================================
    zone(16, B1Y, 236, B1H, 'SEC.01 // ACCESS', 'Access & Security', FLOW['alert'])
    for (cid, t, ch), cell in zip([
            ('r_own',  'OWNER',          ['everything unrestricted']),
            ('r_off',  'OFFICE',         ['no cost side access']),
            ('r_flo',  'FLOOR',          ['no prices visible']),
            ('signin', 'SIGN-IN',        ['user - email - mobile']),
            ('lock',   'LOCKOUT',        ['5 account - 20 network']),
            ('reset',  'PASSWORD RESET', ['6-digit code by email']),
            ('hub',    'CONTROL HUB',    ['logins - staff - sessions'])],
            grid(16, B1Y, 236, B1H, 1, 7, top=32)):
        card(cid, *cell, t, ch, accent=FLOW['alert'])

    # =========================================================================
    # ZONE 02: OPERATIONS & CORE ENGINE
    # =========================================================================
    zone(272, B1Y, 806, B1H, 'CORE.02 // OPERATIONS', 'Operations Engine', FLOW['work'])
    card('est', 282, B1Y + 32, 246, 54, 'ESTIMATE', ['quote - print - history'],
         accent=FLOW['work'], mark='⊘')
    states(548, B1Y + 30, 520, 'CARD',
           ['ADMITTED', 'WORKING', 'ON HOLD', 'COMPLETED'], 'work')
    states(548, B1Y + 60, 520, 'BILL',
           ['PENDING', 'PART PAID', 'PAID', 'FLEET PAID'], 'in')

    # Job Card Central Hub - anchor matches visible bay card bounds
    JX, JY, JW, JH = 282, B1Y + 96, 786, 152
    bay_inset_x = 14
    bay_inset_top = 24
    bay_h = 110
    # Anchor = exact bounding box of the 7 visible bay cards
    anchors['job'] = (JX + bay_inset_x, JY + bay_inset_top,
                      JW - bay_inset_x * 2, bay_h)

    # 7 Modular Bay Cards
    sw = (JW - 28 - 6 * 5) / 7
    for i, (cid, t, sub) in enumerate([
            ('sec_veh', 'VEHICLE', 'reg - brand - km'),
            ('sec_cus', 'CUSTOMER', 'office only'),
            ('sec_con', 'CONCERNS', 'pending to fixed'),
            ('sec_lab', 'JOBS', 'one labour charge'),
            ('sec_spr', 'SPARE PARTS', 'from a shop'),
            ('sec_inv', 'INVENTORY', 'off the shelf'),
            ('sec_pho', 'PHOTOS', 'car - part')]):
        sx, sy = JX + 14 + i * (sw + 5), JY + 24
        anchors[cid] = (sx, sy, sw, 110)
        add('<g class="hub-module map-card" id="bay-%s">' % cid)
        _sh = ' filter="url(#card-shadow)"' if theme == 'dark' else ''
        add('<rect x="%.1f" y="%s" width="%.1f" height="110" fill="%s" stroke="%s" stroke-width="1"%s/>'
            % (sx, sy, sw, CARD, LINE, _sh))
        add('<line x1="%.1f" y1="%s" x2="%.1f" y2="%s" stroke="%s" stroke-width="0.8"/>'
            % (sx + 1, sy + 1, sx + sw - 1, sy + 1, BEVEL_TOP))
        add('<rect x="%.1f" y="%s" width="%.1f" height="3" fill="%s" opacity=".85"/>'
            % (sx, sy, sw, FLOW['work']))
        add('<text x="%.1f" y="%s" font-size="8.8" font-weight="700" text-anchor="middle" '
            'fill="%s">%s</text>' % (sx + sw / 2, sy + 28, INK, esc(t)))
        add('<text x="%.1f" y="%s" font-size="7.4" font-weight="500" text-anchor="middle" fill="%s">%s</text>'
            % (sx + sw / 2, sy + 44, MUTED, esc(sub)))
        add('</g>')

    # Bay connector rail - bus bar linking all 7 modules (#5)
    rail_y = JY + 20
    first_sx = JX + 14
    last_sx = JX + 14 + 6 * (sw + 5) + sw
    add('<line x1="%.1f" y1="%s" x2="%.1f" y2="%s" stroke="%s" '
        'stroke-width="1.2" opacity=".5"/>'
        % (first_sx, rail_y, last_sx, rail_y, FLOW['work']))
    for i in range(7):
        tick_x = JX + 14 + i * (sw + 5) + sw / 2
        add('<line x1="%.1f" y1="%s" x2="%.1f" y2="%s" stroke="%s" '
            'stroke-width="0.8" opacity=".45"/>'
            % (tick_x, rail_y, tick_x, rail_y + 4, FLOW['work']))

    for i, (cid, t, ch, ac) in enumerate([
            ('done',   'COMPLETED',        ['car handed over'],     FLOW['work']),
            ('bill',   'INVOICE',          ['A4 - one parts list'], FLOW['work']),
            ('settle', 'SETTLEMENT CHECK', ['what is unfilled'],    FLOW['alert'])]):
        card(cid, 282 + i * 238, B1Y + 264, 230, 56, t, ch, accent=ac)

    # =========================================================================
    # ZONE 03: BOARDS & TELEMETRY HISTORY
    # =========================================================================
    zone(1098, B1Y, 300, B1H, 'TEL.03 // TELEMETRY', 'Boards & History', FLOW['data'])
    for (cid, t, ch), cell in zip([
            ('dash',  'DASHBOARD',        ['cars on the floor - progress']),
            ('live',  'LIVE REPORT',      ['billed but unfilled - crews']),
            ('cars',  'CAR PROFILES',     ['history by registration']),
            ('jlist', 'JOB CARDS',        ['search - filter']),
            ('ehist', 'ESTIMATE HISTORY', ['searchable'])],
            grid(1098, B1Y, 300, B1H, 1, 5, top=32)):
        card(cid, *cell, t, ch, accent=FLOW['data'])

    # =========================================================================
    # ZONE 04: LOGISTICS & INVENTORY MANIFOLD
    # =========================================================================
    zone(16, B2Y, 544, B2H, 'LOG.04 // LOGISTICS', 'Parts, Shops & Stock', FLOW['stock'])
    for (cid, t, ch, ac), cell in zip([
            ('sshop', 'SPARE SHOPS',    ['ledger - balance - pay'],     FLOW['out']),
            ('supp',  'SUPPLIES SHOPS', ['suppliers - catalog'],        FLOW['out']),
            ('ware',  'WAREHOUSE',      ['items - categories'],         FLOW['stock']),
            ('unass', 'UNASSIGNED',     ['floor adds - office prices'], FLOW['out']),
            ('rest',  'RESTOCK BILLS',  ['discount pro-rata'],          FLOW['out']),
            ('low',   'LOW STOCK',      ['under 25% - negatives'],      FLOW['stock']),
            ('shist', 'STOCK HISTORY',  ['who drew what'],              FLOW['stock']),
            ('sig',   'STOCK SIGNALS',  ['10 handlers - automatic'],     FLOW['stock']),
            ('cost',  'AVERAGE COST',   ['weighted - full replay'],     FLOW['stock'])],
            grid(16, B2Y, 544, B2H, 3, 3, top=32)):
        card(cid, *cell, t, ch, accent=ac)

    # =========================================================================
    # ZONE 05: FINANCIAL FLOW MANIFOLD
    # =========================================================================
    zone(580, B2Y, 420, B2H, 'FIN.05 // CASHLINK', 'Financial Flow Manifold', FLOW['in'])
    for (cid, t, ch, ac), cell in zip([
            ('pend',  'PENDING BILLS',    ['unpaid - part paid'],       FLOW['in']),
            ('cash',  'CASHBOOK',         ['rent - power - scrap'],     FLOW['out']),
            ('paid',  'PAID BILLS',       ['settled - by date'],        FLOW['in']),
            ('sal',   'SALARY & ADVANCE', ['advances - settlement'],    FLOW['out']),
            ('fleet', 'FLEET ACCOUNTS',   ['cascade - advance credit'], FLOW['in']),
            ('disc',  'DISCOUNT AUDIT',   ['over ₹3,500'],              FLOW['alert'])],
            grid(580, B2Y, 420, B2H, 2, 3, top=47)):
        card(cid, *cell, t, ch, accent=ac)

    # =========================================================================
    # ZONE 06: BUSINESS INTELLIGENCE & AUDIT
    # =========================================================================
    zone(1030, B2Y, 368, B2H, 'BI.06 // AUDIT', 'Analysis & Profit Engine', FLOW['data'])
    for (cid, t, ch, ac), cell in zip([
            ('profit', 'PROFIT', ['turnover − expenses',
                                  'the distribution figure'],           FLOW['in']),
            ('deep',   'DEEP ANALYSIS', ['mechanics - spares - vehicles',
                                         'fleet - shops - operations'], FLOW['data']),
            ('del',    'DELETION HISTORY', ['every permanent delete',
                                            'owner only - no restore'], FLOW['alert'])],
            grid(1030, B2Y, 368, B2H, 1, 3, top=32)):
        card(cid, *cell, t, ch, accent=ac)

    # =========================================================================
    # ZONE 07: AUTOMATION & RULES
    # =========================================================================
    zone(16, B3Y, 674, B3H, 'SYS.07 // RULES', 'Rules & Automation', FLOW['data'])
    for (cid, t, ch, ac), cell in zip([
            ('mast',  'MASTER LISTS',   ['brands - models', 'spares - concerns'], FLOW['data']),
            ('auto',  'AUTOCOMPLETE',   ['learns as you type'],                   FLOW['data']),
            ('clean', 'DATA CLEANUP',   ['rename - merge', 'reaches old cards'],  FLOW['data']),
            ('rbac',  'RBAC',           ['3 tiers - server side'],                FLOW['alert']),
            ('lockf', 'FINANCIAL LOCK', ['settled = frozen'],                     FLOW['alert']),
            ('money', 'MONEY BOUNDS',   ['read from the column'],                 FLOW['alert']),
            ('dates', 'DATE RULES',     ['ordered before received'],              FLOW['alert']),
            ('arch',  'ARCHIVE MODEL',  ['accounts archived', 'records logged'],  FLOW['alert'])],
            grid(16, B3Y, 674, B3H, 4, 2, top=32)):
        card(cid, *cell, t, ch, accent=ac)

    # =========================================================================
    # ZONE 08: INFRASTRUCTURE & DAEMONS
    # =========================================================================
    zone(710, B3Y, 688, B3H, 'INFRA.08 // CLOUD', 'Platform & Services', FLOW['work'])
    for (cid, t, ch, ac), cell in zip([
            ('notif', 'NOTIFICATIONS', ['14 events - 10 critical', 'owners only'],   FLOW['alert']),
            ('push',  'WEB PUSH',      ['critical goes to a phone'],                 FLOW['alert']),
            ('store', 'PHOTO STORAGE', ['S3 - presigned', 'browser uploads direct'], FLOW['work']),
            ('mail',  'EMAIL',         ['reset codes only'],                         FLOW['alert']),
            ('pwa',   'INSTALLABLE',   ['home screen - offline page'],               FLOW['work']),
            ('back',  'BACKUPS',       ['pg_dump - keeps 14'],                       FLOW['work'])],
            grid(710, B3Y, 688, B3H, 3, 2, top=32)):
        card(cid, *cell, t, ch, accent=ac)

    # --- connections & bus routing -------------------------------------------
    G1, G2 = 516, 754

    link('r_off', 'job', 'alert', 'r', 'l', bend=VL, tb=0.30)
    link('r_flo', 'job', 'alert', 'r', 'l', bend=VL, tb=0.70)

    link('job', 'done', 'work', 'b', 't', bend=B1Y + 252, ta=0.10)
    link('done', 'bill', 'work', 'r', 'l')
    link('bill', 'settle', 'work', 'r', 'l')

    # Parts leave the hub by one of exactly two routes
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

    # THE EXPENSE TRUNK
    TX, TY = 1006, H1
    trunk([(140, TY), (TX, TY), (TX, 620)], 'out')
    # Junction dot at trunk corner (#9)
    add('<circle cx="%s" cy="%s" r="3.5" fill="%s" opacity=".3"/>' % (TX, TY, FLOW['out']))
    add('<circle cx="%s" cy="%s" r="2" fill="%s" opacity=".8"/>' % (TX, TY, FLOW['out']))
    add('<circle cx="%s" cy="%s" r="0.8" fill="%s"/>' % (TX, TY, INK))
    tap('sshop', 't', 0.75, (153, TY), 'out')
    tap('supp', 't', 0.25, (245.7, TY), 'out')
    tap('cash', 'r', 0.5, (TX, 538.7), 'out')
    tap('sal', 'r', 0.5, (TX, 616), 'out')
    
    # Node into Profit
    add('<rect x="%.1f" y="517" width="6" height="6" fill="%s" opacity=".35"/>' % (TX - 3, FLOW['out']))
    add('<rect x="%.1f" y="518" width="4" height="4" fill="%s"/>' % (TX - 2, FLOW['out']))
    add('<rect x="%.1f" y="519.2" width="1.6" height="1.6" fill="%s"/>' % (TX - 0.8, INK))
    draw([(TX, 520), (1040, 520)], 'out')

    # Revenue reaches it too
    link('paid', 'profit', 'in', 'r', 'l', via=[('y', 577), ('x', 1014), ('y', 535)])
    link('fleet', 'profit', 'in', 'r', 'l', via=[('y', 654), ('x', 1020), ('y', 550)])

    # Reference data feeds the hub; the hub feeds the boards
    link('mast', 'auto', 'data', 'r', 'l')
    link('auto', 'job', 'data', 't', 'b',
         via=[('y', H2 - 10), ('x', 570), ('y', 452), ('x', G1)])
    link('job', 'dash', 'data', 'r', 'l', bend=VR, ta=0.25)
    link('job', 'cars', 'data', 'r', 'l', bend=VR + 5, ta=0.75)
    link('settle', 'live', 'alert', 'r', 'l')

    # Audit, alerting and evidence
    link('disc', 'notif', 'alert', 'b', 't', via=[('y', 745), ('x', 828)])
    link('del', 'notif', 'alert', 'b', 't', via=[('y', 752), ('x', 890)])
    link('notif', 'push', 'alert', 'r', 'l')
    link('sec_pho', 'store', 'work', 'b', 't',
         via=[('y', 330), ('x', 1026), ('y', 766), ('x', 1279)])

    # --- Revision Stamp (#1) --------------------------------------------------
    add('<text x="%s" y="%s" font-size="5.5" font-weight="600" text-anchor="end" '
        'font-family="\'JetBrains Mono\', monospace" fill="%s" opacity=".3">'
        'REV 3.0 \u00b7 A4-L \u00b7 56 MODULES \u00b7 31 SIGNALS</text>'
        % (W - 14, H - 12, MUTED))

    # --- Vignette Overlay - dark theme only (#12) -----------------------------
    if theme == 'dark':
        add('<rect width="%s" height="%s" fill="url(#vignette)" '
            'pointer-events="none"/>' % (W, H))

    svg = ('<svg viewBox="0 0 %s %s" preserveAspectRatio="xMidYMid meet" '
           'xmlns="http://www.w3.org/2000/svg" '
           'font-family="Inter, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif">' % (W, H)) + '\n'.join(parts) + '</svg>'

    page = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WorkshopOS - Master System Architecture Schematic</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&family=JetBrains+Mono:wght@500;700&display=swap" rel="stylesheet">
<style>
  *{margin:0;padding:0;box-sizing:border-box}
  html,body{background:__GROUND__;
    font-family:'Inter',-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    color:__INK__;-webkit-font-smoothing:antialiased;
    min-height:100vh;margin:0;padding:0}
  
  .sheet-wrapper{
    width:100%;
    min-height:100vh;
    display:flex;
    flex-direction:column;
    align-items:center;
    justify-content:flex-start;
    padding:0;
    margin:0;
  }
  .sheet{
    width:100%;
    max-width:1720px;
    background:transparent;
    border:none;
    box-shadow:none;
    margin:0 auto;
    position:relative;
  }
  svg{display:block;width:100%;height:auto}

  /* Interactive CAD feel on web */
  .map-card{cursor:default;transition:transform 0.15s ease}
  .map-card:hover rect:first-child{stroke-width:1.6;filter:brightness(1.1)}
  .state-pill:hover rect{stroke-width:1.5;filter:brightness(1.15)}
  .hub-module:hover rect:first-child{stroke-width:1.5;filter:brightness(1.1)}

  /* Exactly one page A4 landscape PDF export */
  @page{size:A4 landscape;margin:0}
  @media print{
    html,body{background:__GROUND__;width:297mm;height:210mm;overflow:hidden;margin:0;padding:0}
    .sheet-wrapper{padding:0;margin:0;min-height:auto;width:297mm;height:210mm}
    .sheet{width:297mm;height:210mm;margin:0;border:none;box-shadow:none;
      overflow:hidden;break-inside:avoid;page-break-inside:avoid}
    svg{width:100%;height:100%}
    *{-webkit-print-color-adjust:exact!important;print-color-adjust:exact!important}
  }
</style>
</head>
<body>
<div class="sheet-wrapper">
<div class="sheet">
__SVG__
</div>
</div>
</body>
</html>
"""
    for k, v in (('__CHROME__', T['CHROME']), ('__GROUND__', GROUND),
                 ('__SHADOW__', T['SHADOW']), ('__LINE__', LINE),
                 ('__INK__', INK), ('__SVG__', svg)):
        page = page.replace(k, v)

    out = Path(__file__).resolve().parent.parent / T['out']
    out.write_text(page, encoding='utf-8')
    print('wrote %-22s (%.1f KB, %d cards, %d connectors)'
          % (T['out'], len(page) / 1024, len(anchors), len(links)))
    return anchors, links


def to_pdf(root):
    """
    Render each sheet to PDF with headless Chrome / Edge.
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
