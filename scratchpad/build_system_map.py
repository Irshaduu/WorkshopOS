"""
Generates SYSTEM_MAP.html and SYSTEM_MAP_DARK.html - the master architectural
system blueprint of WorkshopOS, in light and dark themes from one set of coordinates.

WHY THIS IS GENERATED RATHER THAN HAND-WRITTEN
----------------------------------------------
Cards and connectors sit on a fixed A4-landscape canvas (1414 x 1000). A zone
declares its grid and cards fall into it; connectors are drawn between named
anchors rather than typed coordinates, so moving a card moves its lines with it.
The two themes are the same geometry with different palettes - they cannot drift apart.

DO NOT WRITE THE COUNTS DOWN HERE. The build prints them on every run, which is
where they are useful. Every hard-coded count in this project's docs has gone
stale, and this docstring was no exception. (They were also stamped along the
bottom edge of the sheet until 2026-08-29 - removed, because nobody reads a
module count off a drawing.)

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
    # Brightened 2026-08-29 on the owner's instruction ("page so dim now").
    # Four things were doing the dimming and all four were lifted together:
    # the CARD fill sat only 10 steps off the GROUND, the borders were #334155
    # against it, every card carried a 40%-opacity drop shadow, and a black
    # radial vignette lay over the whole canvas at 25%. Lifting only the text
    # would have left the cards themselves reading as holes.
    'dark': dict(
        out='SYSTEM_MAP_DARK.html',
        INK='#ffffff',
        INK_SEC='#eef3fa',
        MUTED='#adbdd2',      # was #94a3b8 - sub-labels are the smallest type here
        FAINT='#8496b0',      # was #64748b - grid refs and rulers
        LINE='#4a5d7e',       # was #334155 - card borders now read against CARD
        LINE_ACCENT='#65799b',
        GROUND='#111a2b',     # was #0b0f19 - off pure black, so cards can sit above it
        GROUND_ALT='#0c1322',
        CARD='#1f2c47',       # was #151d30 - the single biggest lift
        CARD_INNER='#18233c',
        CHROME='#0a1120',
        GRID='#2f4160',
        GRID_OPACITY='0.55',
        SHADOW='rgba(0,0,0,.55)',
        CARD_SHADOW='0 3px 9px rgba(0,0,0,0.28)',
        BEVEL_TOP='rgba(255,255,255,0.13)',
        FLOW={
            'work':  '#54cbff',   # Electric Cyan Flow
            'in':    '#4ce0a6',   # Neon Emerald Inflow
            'out':   '#ff8f8f',   # Coral Crimson Outflow
            'stock': '#bda4ff',   # Radiant Purple Stock
            'data':  '#adbdd2',   # Precision Silver Data
            'alert': '#ffcb45',   # Solar Amber Audit
        },
        FLOW_GLOW={
            'work':  'rgba(84,203,255,0.34)',
            'in':    'rgba(76,224,166,0.34)',
            'out':   'rgba(255,143,143,0.34)',
            'stock': 'rgba(189,164,255,0.34)',
            'data':  'rgba(173,189,210,0.24)',
            'alert': 'rgba(255,203,69,0.34)',
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
        """The expense bus rail.

        Appended to `links` like any connector, which it was NOT until
        2026-08-29 - so check_system_map.py could not see it, and a tap
        landing 31px short of the rail's own start shipped as a line floating
        in space under CONTROL HUB. The checker is only as good as what it is
        shown; anything drawn that a reader will read as a connection belongs
        in this list.
        """
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
        links.append(('trunk', None, out))
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
        """A zone is POSITION and COLOUR, and draws nothing of its own.

        It used to stamp a small numbered circle - 01 through 08 - with no
        legend anywhere on the sheet saying what a number meant. The cards
        group themselves by where they sit and what colour they carry; a
        reference number nobody can look up is decoration.

        Kept as a call so the zone declarations still read as the layout's
        structure, and so re-introducing a heading later is one edit here.
        """
        return

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

    def states(x, y, w, label, items, kind, breaks=()):
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
            # An arrow is a CLAIM that one state follows the other, and two of
            # these rows are not sequences all the way along. ON HOLD is a side
            # state a car can drop into and come back from, not a step between
            # WORKING and COMPLETED; and PART PAID only ever happens to a fleet
            # card - a walk-in pays once at pickup and any shortfall becomes a
            # discount, so PENDING goes straight to PAID. `breaks` names the
            # gaps that get no arrow, and the row reads as states rather than
            # as a journey nobody takes.
            if i < len(items) - 1 and i not in breaks:
                add('<path d="M %.1f %s L %.1f %s" stroke="%s" stroke-width="1.3" '
                    'marker-end="url(#ar-%s)" opacity=".9"/>'
                    % (px + pw + 2, y + 9, px + pw + 10, y + 9, colour, kind))
            px += pw + 14

    def card_h(n_chips):
        """The height a card actually needs for its own contents.

        Read straight off what card() draws: the title sits on a baseline at
        y+16, the chips start at y+28 and step by 9.6. So n chips end at
        y + 28 + 9.6*(n-1), and 7px below that closes the box. Anything taller
        is empty space inside a card - which, on a sheet whose connectors have
        to find their way between 56 of them, is space taken from the routing.
        """
        return 35.0 + 9.6 * (max(1, n_chips) - 1)

    # Gutters widened from 8 to 13/11 once cards became content-sized. The
    # space reclaimed from inside the cards is spent here, as real routing
    # lanes between them - a connector that has to thread an 8px channel is
    # legal by the checker and unreadable on the sheet.
    def grid(zx, zy, zw, zh, cols, rows, pad=10, top=32, gx=13, gy=11, chips=None):
        """Positions for a zone's cards.

        Default: divide the zone evenly (cards stretch to fill it).
        With `chips` - a list of per-card chip counts in the same order - each
        ROW is instead only as tall as its tallest card, and whatever is left
        over at the foot of the zone becomes routing corridor. Rows rather than
        individual cards, so a row still reads as a row.
        """
        cw = (zw - pad * 2 - gx * (cols - 1)) / cols
        if chips is None:
            ch = (zh - top - pad - gy * (rows - 1)) / rows
            return [(zx + pad + c * (cw + gx), zy + top + r * (ch + gy), cw, ch)
                    for r in range(rows) for c in range(cols)]

        row_h = [card_h(max(chips[r * cols:(r + 1) * cols] or [1]))
                 for r in range(rows)]
        out, y = [], zy + top
        for r in range(rows):
            for c in range(cols):
                out.append((zx + pad + c * (cw + gx), y, cw, row_h[r]))
            y += row_h[r] + gy
        return out

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
        # Both were lifted in the 2026-08-29 brightening. The shadow is now a
        # seating cue rather than a wash, and the vignette is faint enough to
        # round the sheet off without darkening the outer zones - which is
        # where ACCESS, RULES and CLOUD live, so it was dimming real content.
        add('<filter id="card-shadow" x="-2%%" y="-2%%" width="104%%" height="108%%">'
            '<feDropShadow dx="0" dy="1.2" stdDeviation="1.8" flood-color="#000" '
            'flood-opacity="0.22"/></filter>')
        add('<radialGradient id="vignette" cx="50%%" cy="50%%" r="78%%" fx="50%%" fy="50%%">'
            '<stop offset="70%%" stop-color="#000" stop-opacity="0"/>'
            '<stop offset="100%%" stop-color="#000" stop-opacity="0.10"/>'
            '</radialGradient>')
    add('</defs>')

    # CAD Grid Overlay
    add('<rect width="%s" height="%s" fill="url(#cad-grid)"/>' % (W, H))

    # --- Precision Border Frame (#8) ------------------------------------------
    add('<rect x="8" y="8" width="%s" height="%s" fill="none" stroke="%s" '
        'stroke-width="0.5" opacity=".4"/>' % (W - 16, H - 16, LINE_ACCENT))

    # --- Coordinate rulers: REMOVED 2026-08-29 -------------------------------
    # A-I across the top and 1-6 down the side, plus a numbered circle per
    # zone. They were drafting-sheet costume: nothing on this map is ever
    # referenced by grid square, and the zone circles carried a number with no
    # legend anywhere to say what 04 meant. Ink that answers no question, on a
    # sheet whose whole problem is density.

    # --- Clean Pure Square Title Block ---------------------------------------
    add('<g id="title-block">')
    add('<rect x="16" y="14" width="220" height="34" fill="%s" stroke="%s" stroke-width="1"/>'
        % (CARD, LINE))
    add('<rect x="16" y="14" width="4" height="34" fill="%s"/>' % FLOW['work'])
    add('<text x="28" y="36" font-size="17" font-weight="900" fill="%s" '
        'letter-spacing="-.3">SYSTEM <tspan fill="%s">MAP</tspan></text>'
        % (INK, FLOW['work']))
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
    _access = [
        ('r_own',  'OWNER',          ['everything unrestricted']),
        ('r_off',  'OFFICE',         ['no cost side access']),
        ('r_flo',  'FLOOR',          ['no prices visible']),
        ('signin', 'SIGN-IN',        ['username - owner email']),
        ('lock',   'LOCKOUT',        ['5 wrong tries - 15 mins']),
        ('reset',  'PASSWORD RESET', ['6-digit code by email']),
        ('sess',   'SESSIONS',       ['device - ip - terminate']),
        ('hub',    'CONTROL HUB',    ['logins - staff - roster']),
    ]
    for (cid, t, ch), cell in zip(_access, grid(
            16, B1Y, 236, B1H, 1, len(_access), top=32, gy=8,
            chips=[len(i[2]) for i in _access])):
        card(cid, *cell, t, ch, accent=FLOW['alert'])

    # =========================================================================
    # ZONE 02: OPERATIONS & CORE ENGINE
    # =========================================================================
    zone(272, B1Y, 806, B1H, 'CORE.02 // OPERATIONS', 'Operations Engine', FLOW['work'])
    card('est', 282, B1Y + 32, 246, 54, 'ESTIMATE', ['quote - print - history'],
         accent=FLOW['work'], mark='⊘')
    # ON HOLD moved to the END and lost its incoming arrow: a car drops into
    # hold from anywhere and comes back out again, so sitting it between
    # WORKING and COMPLETED drew a step that is not in the sequence.
    states(548, B1Y + 30, 520, 'CARD',
           ['ADMITTED', 'WORKING', 'COMPLETED', 'ON HOLD'], 'work', breaks={2})
    # PENDING -> PAID is the walk-in, who pays once at pickup and whose
    # shortfall becomes a discount. PART PAID -> FLEET PAID is the fleet lane,
    # and it is the ONLY lane PART PAID happens on - so no arrow joins them.
    states(548, B1Y + 60, 520, 'BILL',
           ['PENDING', 'PAID', 'PART PAID', 'FLEET PAID'], 'in', breaks={1})

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
            ('done',   'COMPLETED',        ['work done - ready to bill',
                                'floor marks it'],       FLOW['work']),
            ('bill',   'INVOICE',          ['A4 - one parts list'], FLOW['work']),
            ('settle', 'SETTLEMENT CHECK', ['what is unfilled'],    FLOW['alert'])]):
        card(cid, 282 + i * 238, B1Y + 264, 230, 56, t, ch, accent=ac)

    # =========================================================================
    # ZONE 03: BOARDS & TELEMETRY HISTORY
    # =========================================================================
    zone(1098, B1Y, 300, B1H, 'TEL.03 // TELEMETRY', 'Boards & History', FLOW['data'])
    # Four-tuples, like every other zone, because the last card is NOT data -
    # see OWNER WITHDRAWALS below.
    _tel = [
        ('dash',  'DASHBOARD',        ['cars on the floor - progress'], FLOW['data']),
        ('live',  'LIVE REPORT',      ['billed but unfilled - crews'],  FLOW['data']),
        ('cars',  'CAR PROFILES',     ['history by registration'],      FLOW['data']),
        ('jlist', 'JOB CARDS',        ['every card - searchable'],      FLOW['data']),
        ('ehist', 'ESTIMATE HISTORY', ['searchable'],                   FLOW['data']),
        # The only section that was on no part of the sheet. It is a HISTORY -
        # a list of what each owner took, which is what this zone holds - but
        # it is the one card here that moves money, so it wears the OUT accent
        # rather than the zone's data blue, and the line under it says where
        # that money is read. The zone had 123px of corridor at its foot and
        # this takes 56 of it, which also puts the card directly above CASH
        # TRACKING in the row below.
        ('wdraw', 'OWNER WITHDRAWALS', ['what each owner took',
                                        'not a cost - cash out only'], FLOW['out']),
    ]
    for (cid, t, ch, ac), cell in zip(_tel, grid(
            1098, B1Y, 300, B1H, 1, len(_tel), top=32,
            chips=[len(i[2]) for i in _tel])):
        card(cid, *cell, t, ch, accent=ac)

    # =========================================================================
    # ZONE 04: LOGISTICS & INVENTORY MANIFOLD
    # =========================================================================
    # Ordered by what connects to what, not by category - a line between two
    # cards with a third sitting between them has nowhere to go.
    zone(16, B2Y, 544, B2H, 'LOG.04 // LOGISTICS', 'Parts, Shops & Stock', FLOW['stock'])
    # WAREHOUSE sits top-right so the draw coming down from the job card's
    # INVENTORY bay has a clear run into it; in the first pass it was boxed in
    # on all four sides by its own neighbours and the connector had nowhere to
    # land. LOW STOCK sits directly under it, which is the shelf's own reading.
    _log = [
        ('sshop', 'SPARE SHOPS',    ['ledger - balance - pay'],     FLOW['out']),
        ('unass', 'UNASSIGNED',     ['floor adds - office prices'], FLOW['out']),
        ('ware',  'WAREHOUSE',      ['the shelf - may go negative'], FLOW['stock']),

        ('catal', 'SHOP CATALOG',   ['what each shop sells'],       FLOW['stock']),
        ('rest',  'RESTOCK BILLS',  ['discount pro-rata'],          FLOW['out']),
        ('low',   'LOW STOCK',      ['under 25% - negatives'],      FLOW['stock']),

        ('supp',  'SUPPLIES SHOPS', ['ledger - instalments'],       FLOW['out']),
        ('cats',  'CATEGORIES',     ['the generic part name'],      FLOW['stock']),
        ('cost',  'AVERAGE COST',   ['weighted - full replay'],     FLOW['stock']),

        ('shist', 'STOCK HISTORY',  ['who drew what'],              FLOW['stock']),
        ('sig',   'STOCK SIGNALS',  ['10 handlers - automatic'],    FLOW['stock']),
    ]
    for (cid, t, ch, ac), cell in zip(_log, grid(
            16, B2Y, 544, B2H, 3, 4, top=32, gy=20,
            chips=[len(i[2]) for i in _log] + [1])):
        card(cid, *cell, t, ch, accent=ac)

    # =========================================================================
    # ZONE 05: FINANCIAL FLOW MANIFOLD
    # =========================================================================
    zone(580, B2Y, 420, B2H, 'FIN.05 // CASHLINK', 'Financial Flow Manifold', FLOW['in'])
    # Right-hand column is everything that has to reach OUT of this zone -
    # PAID and FLEET east into PROFIT, CASHBOOK east onto the expense trunk.
    # The empty slot at row 4 col 2 is deliberate: it is the lane SALARY uses
    # to reach the trunk without crossing the card beside it.
    _fin = [
        ('pend',  'PENDING BILLS',    ['unpaid - part paid'],        FLOW['in']),
        ('paid',  'PAID BILLS',       ['settled - by date'],         FLOW['in']),

        ('spay',  'SHOP PAYMENTS',    ['clears oldest bills first',
                               'settles debt - not a cost'], FLOW['out']),
        ('fleet', 'FLEET ACCOUNTS',   ['cascade - advance credit'],  FLOW['in']),

        ('disc',  'DISCOUNT AUDIT',   ['over ₹3,500'],               FLOW['alert']),
        ('cash',  'CASHBOOK',         ['rent - power - scrap'],      FLOW['out']),

        # The Mechanic model is the whole STAFF ROSTER, not just mechanics -
        # a top-level model with its own screen, and until now it was only
        # implied by a chip on CONTROL HUB. It sits beside the thing that pays
        # it. SALARY keeps col 2 so it can still tap the trunk to its right.
        ('staff', 'STAFF ROSTER',     ['mechanics - office - helpers'], FLOW['data']),
        ('sal',   'SALARY & ADVANCE', ['advances - settlement'],     FLOW['out']),
    ]
    for (cid, t, ch, ac), cell in zip(_fin, grid(
            580, B2Y, 420, B2H, 2, 4, top=47, gx=18, gy=14,
            chips=[len(i[2]) for i in _fin] + [1])):
        card(cid, *cell, t, ch, accent=ac)

    # =========================================================================
    # ZONE 06: BUSINESS INTELLIGENCE & AUDIT
    # =========================================================================
    zone(1030, B2Y, 368, B2H, 'BI.06 // AUDIT', 'Analysis & Profit Engine', FLOW['data'])
    _bi = [
        ('profit', 'PROFIT', ['turnover − expenses',
                              'the distribution figure'],           FLOW['in']),
        ('cashpos', 'CASH TRACKING', ['by the day money moved',
                                      'a change, never a balance'],  FLOW['in']),
        ('deep',   'DEEP ANALYSIS', ['mechanics - spares - vehicles',
                                     'fleet - shops - operations'], FLOW['data']),
        ('del',    'DELETION HISTORY', ['every permanent delete',
                                        'owner only - no restore'], FLOW['alert']),
    ]
    for (cid, t, ch, ac), cell in zip(_bi, grid(
            1030, B2Y, 368, B2H, 1, len(_bi), top=32,
            chips=[len(i[2]) for i in _bi])):
        card(cid, *cell, t, ch, accent=ac)

    # =========================================================================
    # ZONE 07: AUTOMATION & RULES
    # =========================================================================
    zone(16, B3Y, 674, B3H, 'SYS.07 // RULES', 'Rules & Automation', FLOW['data'])
    _rules = [
        ('mast',  'MASTER LISTS',   ['brands - models - spares',
                             'the list sets the spelling'], FLOW['data']),
        ('auto',  'AUTOCOMPLETE',   ['learns as you type'],                   FLOW['data']),
        ('clean', 'DATA CLEANUP',   ['spare & concern names',
                             'how often each is used'],   FLOW['data']),
        ('rbac',  'RBAC',           ['3 tiers - server side', 'no-store - noindex'], FLOW['alert']),
        ('lockf', 'FINANCIAL LOCK', ['settled = frozen'],                     FLOW['alert']),

        ('money', 'AMOUNT CHECKS',  ['every typed figure checked'],           FLOW['alert']),
        ('dates', 'DATE CHECKS',    ['nothing dated in future',
                             'arrival after the order'],              FLOW['alert']),
        ('arch',  'ARCHIVE',        ['shops and staff put away',
                             'deletes are written down'],             FLOW['alert']),
        ('dwin',  'DELETE WINDOW',  ['office 7 days', 'older is an owner'],   FLOW['alert']),
        # spare_dates.py is DATE RULES above; this one is money_dates.py -
        # which day a rupee is filed under, not whether a pair is in order.
        ('mdate', 'MONEY DATES',    ['filed by the day it moved'],            FLOW['alert']),
    ]
    for (cid, t, ch, ac), cell in zip(_rules, grid(
            16, B3Y, 674, B3H, 5, 2, top=32,
            chips=[len(i[2]) for i in _rules])):
        card(cid, *cell, t, ch, accent=ac)

    # =========================================================================
    # ZONE 08: INFRASTRUCTURE & DAEMONS
    # =========================================================================
    zone(710, B3Y, 688, B3H, 'INFRA.08 // CLOUD', 'Platform & Services', FLOW['work'])
    _infra = [
        ('notif', 'NOTIFICATIONS', ['14 events - 11 critical', 'owners only'],   FLOW['alert']),
        ('push',  'WEB PUSH',      ['critical goes to a phone'],                 FLOW['alert']),
        ('mail',  'EMAIL',         ['reset codes only'],                         FLOW['alert']),
        ('store', 'PHOTO STORAGE', ['S3 - presigned', 'browser uploads direct'], FLOW['work']),

        ('pwa',   'INSTALLABLE',   ['home screen - offline page'],               FLOW['work']),
        ('back',  'BACKUPS',       ['pg_dump - keeps 14'],                       FLOW['work']),
        ('db',    'POSTGRES',      ['one database - migrations'],                FLOW['work']),
        ('retain', 'PHOTO PURGE',  ['one year - skips unpaid', 'orphan blob sweep'], FLOW['work']),
    ]
    for (cid, t, ch, ac), cell in zip(_infra, grid(
            710, B3Y, 688, B3H, 4, 2, top=32,
            chips=[len(i[2]) for i in _infra])):
        card(cid, *cell, t, ch, accent=ac)

    # --- connections & bus routing -------------------------------------------
    G1, G2 = 516, 754

    link('r_off', 'job', 'alert', 'r', 'l', bend=VL, tb=0.30)
    link('r_flo', 'job', 'alert', 'r', 'l', bend=VL, tb=0.70)

    link('job', 'done', 'work', 'b', 't', bend=B1Y + 252, ta=0.10)
    link('done', 'bill', 'work', 'r', 'l')
    link('bill', 'settle', 'work', 'r', 'l')

    # Parts leave the hub by one of exactly two routes. Both drop into the
    # 296-330 band above COMPLETED/INVOICE/SETTLE, then down a named lane:
    # 512-520 (between DONE and INVOICE) and 750-758 (between INVOICE and
    # SETTLE). Those two gaps are the only way through band 1.
    link('sec_spr', 'sshop', 'out', 'b', 't',
         via=[('y', 316), ('x', 258), ('y', 478), ('x', 60)])
    link('sec_inv', 'ware', 'stock', 'b', 't',
         via=[('y', 308), ('x', 754), ('y', 478), ('x', 467)])

    # On and off the shelf. y=534 and y=580 are the clear lanes between the
    # LOG rows, opened up by content-sized cards.
    link('catal', 'rest', 'stock', 'r', 'l')
    link('supp', 'rest', 'out', 't', 'b', bend=598)
    link('rest', 'ware', 'stock', 't', 'b', bend=535, tb=0.7)
    link('rest', 'cost', 'stock', 'b', 't', bend=590)
    link('ware', 'low', 'stock', 'b', 't', ta=0.3, tb=0.3)
    link('sig', 'cost', 'stock', 'r', 'b')
    # Stock moves ONLY via signals - so the signal handlers, not the bill and
    # not the job card, are what actually write Item.current_stock.
    # (This replaced a sig -> shist line, which was simply untrue: Stock
    # History is a live query over JobCardSpareItem and fires no signal at all.
    # ConsumptionRecord is dormant - referenced by admin.py and models.py and
    # nothing else. The real source of Stock History is job -> shist, below.)
    link('sig', 'ware', 'stock', 'r', 'l', via=[('x', 377), ('y', 511)])
    link('unass', 'sshop', 'out', 'l', 'r')

    # Paying a shop settles that ledger and NOTHING else - it never reaches
    # PROFIT. Drawn deliberately: these two lines stop at the shops.
    link('staff', 'sal', 'data', 'r', 'l')
    link('spay', 'sshop', 'out', 'l', 't', via=[('x', 566), ('y', 486), ('x', 150)])
    link('spay', 'supp', 'out', 'l', 'b', via=[('x', 578), ('y', 649), ('x', 109)])

    # The bill reaches the money states
    link('bill', 'pend', 'in', 'b', 't', via=[('y', 443), ('x', 645)])
    link('pend', 'paid', 'in', 'r', 'l')
    # fleet -> paid, not the reverse: bulk_payer_pay is what SETS BULK_PAID
    # (views/bulk_payer.py:455), so the fleet account produces the settled
    # bill. Drawn the other way round it said Paid Bills feed the account.
    link('fleet', 'paid', 'in', 't', 'b', ta=0.3, tb=0.3)

    # THE EXPENSE TRUNK - the four streams the equation actually charges.
    # It taps WAREHOUSE, not SUPPLIES SHOPS: a part is a cost on the day it is
    # DRAWN off the shelf, never on the day the delivery was billed. Tapping
    # the bill here would be the double-count rule broken in a drawing.
    TX, TY = 1006, H1
    # Starts at 109, which is where SPARE SHOPS taps it - not 140, which left
    # the tap ending 31px short of the rail with a terminal node floating in
    # clear space. check_system_map.py now asserts every tap lands on it.
    trunk([(109, TY), (TX, TY), (TX, 690)], 'out')
    # Junction dot at trunk corner (#9)
    add('<circle cx="%s" cy="%s" r="3.5" fill="%s" opacity=".3"/>' % (TX, TY, FLOW['out']))
    add('<circle cx="%s" cy="%s" r="2" fill="%s" opacity=".8"/>' % (TX, TY, FLOW['out']))
    add('<circle cx="%s" cy="%s" r="0.8" fill="%s"/>' % (TX, TY, INK))
    tap('sshop', 't', 0.5, (109, TY), 'out')
    tap('ware', 't', 0.5, (467, TY), 'out')
    tap('cash', 'r', 0.5, (TX, 624.5), 'out')
    tap('sal', 'r', 0.5, (TX, 673.5), 'out')
    
    # Node into Profit
    add('<rect x="%.1f" y="517" width="6" height="6" fill="%s" opacity=".35"/>' % (TX - 3, FLOW['out']))
    add('<rect x="%.1f" y="518" width="4" height="4" fill="%s"/>' % (TX - 2, FLOW['out']))
    add('<rect x="%.1f" y="519.2" width="1.6" height="1.6" fill="%s"/>' % (TX - 0.8, INK))
    draw([(TX, 520), (1040, 520)], 'out')

    # Revenue reaches it too, up the 990-1040 lane between CASHLINK and AUDIT
    link('paid', 'profit', 'in', 'r', 'l', bend=1013)
    link('fleet', 'profit', 'in', 'r', 'l', bend=1024, tb=0.75, ta=0.3)

    # CASH TRACKING is NOT downstream of PROFIT. analysis_engine.py:1261 says
    # it outright - "Nothing here appears in build_profit_report" - and the
    # whole point of the card is that the two can never be added together.
    # A profit -> cashpos arrow claimed exactly the thing that comment forbids,
    # and cashpos -> deep claimed a derivation between two sibling views.
    # What IS true is the input that only Cash Tracking reads: fleet money
    # comes from BulkPaymentHistory, one row per payment.
    link('fleet', 'cashpos', 'in', 'r', 'l', bend=1013, ta=0.7)

    # An owner withdrawal reaches exactly ONE figure in the whole engine, and
    # this is it - `cash_position()`'s money-out list, dated by the day the
    # cash was taken. It is in no expense line, no margin and nowhere inside
    # build_profit_report, which is why the arrow points here and at nothing
    # else on the sheet.
    #
    # DOWN THE OUTER MARGIN, not the 1000-1030 lane between CASHLINK and
    # AUDIT. A straight drop from the card is impossible - PROFIT sits
    # directly above the target - and that inner lane already carries the
    # EXPENSE TRUNK, which is the same coral: check 5 measured the two running
    # 7px apart for 122px, which is exactly the "three red lines side by side"
    # this drawing was rebuilt once to get rid of. x=1404 is outside both zone
    # boxes and carries nothing else, so the line reads as its own run and
    # arrives at CASH TRACKING's right edge.
    link('wdraw', 'cashpos', 'out', 'r', 'r', via=[('x', 1404), ('y', 571.9)])

    # Reference data feeds the hub; the hub feeds the boards
    link('mast', 'auto', 'data', 'r', 'l')
    link('auto', 'job', 'data', 't', 'b',
         via=[('y', H2 - 10), ('x', 562), ('y', 452), ('x', 516)])
    # These two leave from the TOP of the hub, not its right edge. The hub's
    # anchor is the bounding box of the seven bays, so its right edge IS the
    # PHOTOS bay's right edge - and both lines appeared to come out of PHOTOS,
    # reading as "photos feed the dashboard". Leaving from the top, in the
    # GAPS between bays (INVENTORY|PHOTOS and SPARE PARTS|INVENTORY), they
    # come off the bay rail instead, which is the whole card.
    link('job', 'dash', 'data', 't', 'l', ta=0.859,     # x ~= 947, a bay gap
         via=[('y', 160), ('x', 1088), ('y', 115.5)])
    link('job', 'cars', 'data', 't', 'l', ta=0.7157,    # x ~= 838, a bay gap
         via=[('y', 176), ('x', 1093), ('y', 201.5)])
    link('job', 'shist', 'stock', 'b', 'b',
         via=[('y', 304), ('x', 272), ('y', 470), ('x', 198), ('y', 730),
              ('x', 109)], ta=0.02)
    link('settle', 'live', 'alert', 'r', 'l', bend=1075)

    # Audit, alerting and evidence. The 691-792 band is clear right across the
    # sheet, so everything heading for NOTIFICATIONS travels along it.
    link('disc', 'notif', 'alert', 'l', 't', via=[('x', 576), ('y', 740), ('x', 760)])
    link('del', 'notif', 'alert', 'b', 't', via=[('y', 752), ('x', 830)], ta=0.2)
    link('notif', 'push', 'alert', 'r', 'l')
    link('sec_pho', 'store', 'work', 'b', 't',
         via=[('y', 302), ('x', 1030), ('y', 766), ('x', 1305)])

    # --- Revision stamp: REMOVED 2026-08-29 -----------------------------------
    # "REV 4.0 - A4-L - 66 MODULES - 39 SIGNALS" in 5.5px along the bottom
    # edge. It went the same way as the drafting rulers and the zone numbers,
    # and for the same reason: nobody reads a module count off a drawing, and
    # at that size it was a smudge rather than a fact. The build still PRINTS
    # both counts to stdout on every run, which is where they are actually
    # useful - so do not write them down here or anywhere else.

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

    root = Path(__file__).resolve().parent.parent
    out = root / T['out']
    out.write_text(page, encoding='utf-8')
    print('wrote %-22s (%.1f KB, %d cards, %d connectors)'
          % (T['out'], len(page) / 1024, len(anchors), len(links)))

    # The dark sheet is ALSO emitted as a Django include, so the in-app About
    # page draws THIS geometry rather than a copy of it. A pasted <svg> would
    # be a second set of coordinates free to drift from the printed sheet, and
    # the drift would be invisible - both would still look like a map.
    #
    # Only the font changes: the standalone file loads Inter from a CDN, and
    # the app is deliberately third-party-free, so the embed asks for the
    # vendored Barlow instead. Same glyphs' worth of space either way.
    if theme == 'dark':
        embed = svg.replace(
            'font-family="Inter, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif"',
            'font-family="Barlow, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif"')
        part = root / 'workshop' / 'templates' / 'workshop' / 'includes' / '_system_map_svg.html'
        part.parent.mkdir(parents=True, exist_ok=True)
        part.write_text(
            '{% comment %}\n'
            'GENERATED FILE - DO NOT EDIT.\n'
            'Written by scratchpad/build_system_map.py, from the same coordinates\n'
            'as SYSTEM_MAP_DARK.html and its PDF. To change the map, edit that\n'
            'script and re-run it; editing here is overwritten on the next build.\n'
            '{% endcomment %}\n' + embed + '\n', encoding='utf-8')
        print('wrote %-22s (%.1f KB)'
              % ('_system_map_svg.html', len(embed) / 1024))

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
