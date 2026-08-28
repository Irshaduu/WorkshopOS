from django.shortcuts import render

from ..decorators import owner_required


@owner_required
def about(request):
    """
    Owner-only tour of what is currently in the system.

    Owner-only on the owner's instruction. It is also the honest gate: the page
    describes Profit, Cash Tracking, Deletion History and the two shop ledgers,
    and describing a door somebody cannot open is the same defect as rendering
    one - the rule the audit menu and the frozen-advance menu already follow.

    It carries NO links, deliberately. The brief was "scroll and read all": a
    page of shortcuts into other sections is a menu, and there is already a
    menu one tap away in the drawer this page is reached from.

    Static, so no context and no queries. The map header is an {% include %} of
    a GENERATED partial - see scratchpad/build_system_map.py - so the drawing
    on this page and the printed A4 sheet can never disagree.
    """
    return render(request, 'workshop/about.html')
