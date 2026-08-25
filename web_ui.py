"""HTML rendering for the Instamart web UI.

There is deliberately no JavaScript in this file, and none is served. Every
interaction is a plain form POST or a link, which means:

  * the two pieces of UI that usually need scripting do not - the payment
    modal is a CSS ":target" overlay, and the address drawer is <details>;
  * nothing can happen in the browser that the server did not render, so the
    server's view of the cart is the only view; and
  * the whole app stays inside the standard library and the project's
    Python-only rule.

The trade is one round trip per interaction. On a local server against a
remote API that round trip is free next to the Swiggy call it wraps.

This module renders; it never calls Swiggy. Pages take plain data so they can
be rendered (and eyeballed) without a live account.
"""

from html import escape

from instamart import CHECKOUT_LIMIT_PAISE
from money import rupees

BRAND = "Mealwise"
ACCENT = "#fc8019"          # Swiggy orange

# Product photos come from Swiggy's own CDN. Every variation observed carried
# one (206/206) and all of them were https on this single host, so the page's
# CSP can name exactly this origin and keep blocking everything else.
IMAGE_HOST = "https://media-assets.swiggy.com/"

# That host is a Cloudinary delivery endpoint, and the catalogue serves
# full-size shots: one PNG measured 758 KB, which is absurd for a 48px
# thumbnail and would be ~6 MB for a page of eight. Inserting a transformation
# after "/image/upload/" resizes on the fly - verified 2026-08-25 across four
# real URLs, 2.0 MB became 15.0 KB, a 135x cut, every one HTTP 200.
#
# This is a CDN convention, NOT a documented Swiggy API. So it is used only
# where it is cosmetic: if Cloudinary ever rejects the transform the
# thumbnails break and nothing else does. 96px for a 48px box keeps it sharp
# on a retina screen.
_UPLOAD_MARK = "/image/upload/"
_THUMB_TRANSFORM = "w_96,h_96,c_fit,q_auto,f_auto/"


def thumb_src(url):
    """The image URL, asking the CDN for a thumbnail instead of a poster."""
    if _UPLOAD_MARK in url:
        return url.replace(_UPLOAD_MARK, _UPLOAD_MARK + _THUMB_TRANSFORM, 1)
    return url

CSS = """
*,*::before,*::after{box-sizing:border-box}
:root{
  --bg:#f4f5f7; --card:#fff; --ink:#16181d; --muted:#6b7280; --line:#e4e6eb;
  --accent:%(accent)s; --accent-ink:#fff; --ok:#0f7b3f; --ok-bg:#e7f6ec;
  --warn:#8a5300; --warn-bg:#fff4e0; --bad:#a8231b; --bad-bg:#fdeceb;
  --shadow:0 1px 2px rgba(16,24,40,.06),0 8px 24px rgba(16,24,40,.08);
}
@media (prefers-color-scheme:dark){
  :root{
    --bg:#111317; --card:#191c22; --ink:#eceef2; --muted:#9aa1ad; --line:#2a2f38;
    --ok:#6ee7a0; --ok-bg:#12291d; --warn:#f6c25c; --warn-bg:#2b2211;
    --bad:#ff9b92; --bad-bg:#2c1614;
    --shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px rgba(0,0,0,.45);
  }
}
html{-webkit-text-size-adjust:100%%}
body{
  margin:0; background:var(--bg); color:var(--ink);
  font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
}
a{color:inherit}
.wrap{max-width:760px;margin:0 auto;padding:0 1rem 4rem}

/* ---- top bar: address chip sits in the upper left ---- */
.top{
  position:sticky;top:0;z-index:20;background:var(--card);
  border-bottom:1px solid var(--line);margin-bottom:1.25rem;
}
.top-in{
  max-width:760px;margin:0 auto;padding:.6rem 1rem;
  display:flex;gap:.75rem;align-items:flex-start;
}
.brand{
  margin-left:auto;font-weight:700;letter-spacing:-.01em;color:var(--accent);
  white-space:nowrap;padding-top:.35rem;
}
.who{font-size:11px;color:var(--muted);font-weight:400;display:block;text-align:right}
.linkish{background:none;border:0;padding:0;font:inherit;color:var(--muted);
  text-decoration:underline;cursor:pointer}
.linkish:hover{color:var(--accent)}

details.addr{min-width:0;flex:1}
details.addr>summary{
  list-style:none;cursor:pointer;display:inline-flex;gap:.5rem;align-items:center;
  padding:.35rem .5rem;border-radius:10px;max-width:100%%;
}
details.addr>summary::-webkit-details-marker{display:none}
details.addr>summary:hover{background:var(--bg)}
.pin{color:var(--accent);flex:none;font-size:15px}
.addr-txt{min-width:0}
.addr-kicker{display:block;font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
.addr-name{display:block;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:38ch}
.caret{color:var(--muted);flex:none;font-size:11px}
.addr-list{
  margin:.5rem 0 .35rem;border:1px solid var(--line);border-radius:12px;
  background:var(--card);overflow:hidden auto;
  /* A real account had 24 saved addresses, which ran the drawer off the
     bottom of the window. Scroll inside it instead. */
  max-height:min(46vh,340px);
}
.addr-row{display:flex;align-items:center;gap:.5rem;border-top:1px solid var(--line)}
.addr-row:first-child{border-top:0}
.addr-row form{flex:1;margin:0}
.addr-row button{
  width:100%%;text-align:left;background:none;border:0;padding:.7rem .85rem;
  font:inherit;color:inherit;cursor:pointer;display:flex;gap:.6rem;align-items:baseline;
}
.addr-row button:hover{background:var(--bg)}
.addr-row .tick{color:var(--accent);width:1em;flex:none}
.addr-id{margin-left:auto;font-size:11px;color:var(--muted);font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.addr-add{display:block;padding:.6rem .85rem;border:1px solid var(--line);
  border-radius:12px;background:var(--card);color:var(--accent);font-weight:600;
  text-decoration:none}
.addr-add:hover{background:var(--bg)}

/* ---- generic blocks ---- */
h1{font-size:1.35rem;margin:0 0 .25rem;letter-spacing:-.02em}
h2{font-size:1.05rem;margin:0 0 .75rem}
h3{font-size:.95rem;margin:0}
p{margin:.4rem 0}
.sub{color:var(--muted);margin:0 0 1.25rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;
  padding:1.1rem;margin-bottom:1rem;box-shadow:var(--shadow)}
.card>h2:first-child{margin-top:0}
.muted{color:var(--muted)}
.small{font-size:13px}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}
hr{border:0;border-top:1px solid var(--line);margin:.9rem 0}

textarea,input[type=text],input[type=tel],select{
  width:100%%;padding:.7rem .8rem;border:1px solid var(--line);border-radius:12px;
  background:var(--bg);color:var(--ink);font:inherit;
}
textarea{min-height:96px;resize:vertical}
textarea:focus,input:focus,select:focus{outline:2px solid var(--accent);outline-offset:1px}
label{display:block;font-weight:600;font-size:13px;margin:.75rem 0 .3rem}
label .opt{font-weight:400;color:var(--muted)}

.btn{
  display:inline-block;padding:.7rem 1.15rem;border-radius:12px;border:1px solid transparent;
  background:var(--accent);color:var(--accent-ink);font:inherit;font-weight:650;
  cursor:pointer;text-decoration:none;text-align:center;
}
.btn:hover{filter:brightness(.94)}
.btn[disabled]{opacity:.45;cursor:not-allowed;filter:none}
.btn.ghost{background:transparent;color:var(--ink);border-color:var(--line)}
.btn.ghost:hover{background:var(--bg);filter:none}
.btn.danger{background:var(--bad);color:#fff}
.btn.block{display:block;width:100%%}
.btn.sm{padding:.4rem .7rem;font-size:13px;border-radius:9px}
.row{display:flex;gap:.6rem;flex-wrap:wrap;align-items:center}
.row.end{justify-content:flex-end}
.grow{flex:1}
form.inline{display:inline;margin:0}

.note{border-radius:12px;padding:.7rem .85rem;margin:0 0 1rem;border:1px solid transparent}
.note.ok{background:var(--ok-bg);color:var(--ok);border-color:currentColor}
.note.warn{background:var(--warn-bg);color:var(--warn);border-color:currentColor}
.note.bad{background:var(--bad-bg);color:var(--bad);border-color:currentColor}
.note.info{background:var(--bg);color:var(--muted);border-color:var(--line)}
.note b{color:inherit}
.note ul{margin:.35rem 0 0;padding-left:1.1rem}

/* ---- lines and bills ---- */
.line{display:flex;gap:.75rem;align-items:flex-start;padding:.7rem 0;border-top:1px solid var(--line)}
.line:first-of-type{border-top:0}
.line-main{flex:1;min-width:0}
.line-name{font-weight:600}
.line-meta{color:var(--muted);font-size:13px}
.line-amt{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
.qty{display:flex;align-items:center;gap:.2rem;border:1px solid var(--line);border-radius:10px;overflow:hidden}
.qty button{background:none;border:0;padding:.3rem .55rem;font:inherit;color:inherit;cursor:pointer}
.qty button:hover{background:var(--bg)}
.qty span{padding:0 .3rem;min-width:1.3em;text-align:center;font-variant-numeric:tabular-nums}

.bill{width:100%%;border-collapse:collapse;font-variant-numeric:tabular-nums}
.bill td{padding:.3rem 0}
.bill td+td{text-align:right;white-space:nowrap}
.bill tr.total td{font-weight:700;font-size:1.05rem;border-top:1px solid var(--line);padding-top:.6rem}
.free{color:var(--ok);font-weight:600}

.bar{height:6px;border-radius:99px;background:var(--line);overflow:hidden;margin:.5rem 0 .35rem}
.bar>i{display:block;height:100%%;background:var(--accent)}
.bar>i.over{background:var(--bad)}

/* ---- the payment modal: opened by :target, closed by a link ---- */
.modal{position:fixed;top:0;right:0;bottom:0;left:0;z-index:60;
  display:none;align-items:center;justify-content:center;padding:1rem}
.modal:target{display:flex}
.modal-back{position:absolute;top:0;right:0;bottom:0;left:0;background:rgba(9,11,15,.6)}
.modal-card{position:relative;background:var(--card);border:1px solid var(--line);
  border-radius:18px;box-shadow:var(--shadow);width:100%%;max-width:440px;
  max-height:85vh;overflow:auto;padding:1.1rem}
.modal-head{display:flex;align-items:center;gap:.5rem;margin-bottom:.35rem}
.modal-x{margin-left:auto;text-decoration:none;color:var(--muted);font-size:1.3rem;
  line-height:1;padding:.1rem .4rem;border-radius:8px}
.modal-x:hover{background:var(--bg)}
.pay-opt{width:100%%;text-align:left;background:none;border:1px solid var(--line);
  border-radius:12px;padding:.7rem .8rem;margin:.4rem 0;font:inherit;color:inherit;cursor:pointer}
.pay-opt:hover{border-color:var(--accent)}
.pay-opt b{display:block}
.pay-opt span{color:var(--muted);font-size:12.5px}
.pay-group{font-size:11px;text-transform:uppercase;letter-spacing:.06em;
  color:var(--muted);margin:.9rem 0 .1rem}

.pick{display:flex;gap:.65rem;align-items:center;border:1px solid var(--line);
  border-radius:12px;padding:.6rem .7rem;margin:.4rem 0;cursor:pointer;font-weight:400}
.pick:hover{border-color:var(--accent)}
.pick input{margin:0;flex:none}
.pick-body{min-width:0;flex:1}
.pick .tag{font-size:11px;border-radius:99px;padding:.1rem .45rem;margin-left:.4rem;
  background:var(--ok-bg);color:var(--ok);font-weight:600;white-space:nowrap}
.pick .tag.grey{background:var(--bg);color:var(--muted)}
.line-name .tag{font-size:11px;border-radius:99px;padding:.1rem .45rem;
  margin-left:.4rem;font-weight:600;white-space:nowrap;vertical-align:1px}
.line-name .tag.grey{background:var(--bg);color:var(--muted)}
.line-name .tag.warnish{background:var(--warn-bg);color:var(--warn)}
.qty.static{border-style:dashed;color:var(--muted)}
.pick .amt{margin-left:auto;text-align:right;font-variant-numeric:tabular-nums;
  font-weight:600;white-space:nowrap}
.unit{display:block;font-size:12px;color:var(--muted)}

/* Product photo. Sized in CSS and in the attributes, so the row does not
   jump about while the image is still on its way. */
.thumb{width:48px;height:48px;flex:none;border-radius:10px;object-fit:contain;
  background:var(--bg);border:1px solid var(--line)}
.thumb-none{display:inline-block}

/* "who am I" popover, same <details> trick as the address drawer */
details.me{position:relative;display:inline-block;text-align:left}
details.me>summary{
  list-style:none;cursor:pointer;display:inline-flex;gap:.3rem;align-items:center;
  padding:.3rem .6rem;border:1px solid var(--line);border-radius:99px;
  font-size:12px;line-height:1.2;color:var(--ink);background:var(--card);
  min-height:26px;max-width:22ch;
}
details.me>summary:hover{border-color:var(--accent);color:var(--accent)}
details.me[open]>summary{border-color:var(--accent);color:var(--accent)}
details.me>summary::-webkit-details-marker{display:none}
.me-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.me-panel{position:absolute;right:0;top:1.5rem;z-index:30;width:236px;padding:.75rem;
  background:var(--card);border:1px solid var(--line);border-radius:12px;
  box-shadow:var(--shadow);font-size:13px;color:var(--ink);font-weight:400;
  /* .brand sets nowrap to keep the header on one line; the panel is prose
     and must wrap, or its explanation runs off the side of the box. */
  white-space:normal;text-align:left}
.me-panel input[type=text]{margin-bottom:.45rem}
.was{color:var(--muted);text-decoration:line-through;font-weight:400;font-size:12px}

.steps{display:flex;gap:.4rem;font-size:12px;color:var(--muted);margin-bottom:1rem;flex-wrap:wrap}
.steps b{color:var(--accent)}
.hero{text-align:center;padding:2.5rem 1rem}
.hero .mark{font-size:2.6rem}
""" % {"accent": ACCENT}


def esc(value):
    return escape("" if value is None else str(value), quote=True)


def product_label(row):
    """Brand and name, without saying the brand twice.

    Live catalogue data goes both ways: displayName is usually already
    brand-prefixed, so concatenating gives "Amul Amul Fresh Paneer" and
    "Mother Dairy Mother Dairy Fresh Paneer" - but some rows carry a bare
    name, so the brand cannot simply be dropped either.
    """
    brand = (row.get("brand") or "").strip()
    name = (row.get("name") or "").strip()
    if not brand or name.lower().startswith(brand.lower()):
        return name or brand
    return "%s %s" % (brand, name)


def thumb(url, alt):
    """A product photo, or a neutral placeholder of the same size.

    Only Swiggy's own asset origin is ever emitted. The page's CSP allows that
    one host, so a URL from anywhere else would be blocked by the browser and
    render as a broken image - a placeholder is the better failure.
    """
    if url and url.startswith(IMAGE_HOST):
        return ("<img class=\"thumb\" src=\"%s\" alt=\"%s\" width=\"48\" height=\"48\" "
                "loading=\"lazy\" decoding=\"async\" referrerpolicy=\"no-referrer\">"
                % (esc(thumb_src(url)), esc(alt)))
    return "<span class=\"thumb thumb-none\" aria-hidden=\"true\"></span>"


# ---------------------------------------------------------------- shell

def page(title, body, head="", ctx=None):
    top = topbar(ctx) if ctx else ""
    return (
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>%s</title><style>%s</style>%s</head><body>%s<div class=\"wrap\">%s</div></body></html>"
        % (esc(title), CSS, head, top, body)
    )


def topbar(ctx):
    account = account_line(ctx)
    return ("<div class=\"top\"><div class=\"top-in\">%s<div class=\"brand\">%s%s</div>"
            "</div></div>" % (address_chip(ctx), esc(BRAND), account))


def account_line(ctx):
    """Who is signed in - by their own label, since Swiggy never sends one.

    The OAuth token carries a user_id and nothing else: no name, no phone, no
    email. get_addresses returns no name either, and there is no profile tool.
    So the name here is whatever the person typed, kept on this machine, and
    the panel says so rather than implying Swiggy told us.
    """
    if not ctx.get("user_id"):
        return ""
    name = (ctx.get("display_name") or "").strip()
    shown = ("<span class=\"me-name\">%s</span><span class=\"caret\">&#9660;</span>"
             % esc(name)) if name else "+ Add your name"
    return (
        "<span class=\"who\"><details class=\"me\">"
        "<summary title=\"Signed in as Swiggy account %s\">%s</summary>"
        "<div class=\"me-panel\">"
        "<form method=\"post\" action=\"/profile\">%s"
        "<label style=\"margin-top:0\">Show me as</label>"
        "<input type=\"text\" name=\"display_name\" value=\"%s\" maxlength=\"40\" "
        "placeholder=\"your name\">"
        "<button class=\"btn sm block\" type=\"submit\">Save</button></form>"
        "<p class=\"small muted\">Swiggy&rsquo;s API never sends your name, so this "
        "is just a label for this app, stored on this machine. Signed in as "
        "account %s.</p>"
        "<form method=\"post\" action=\"/logout\">%s"
        "<button class=\"linkish\" type=\"submit\">sign out of this account</button>"
        "</form></div></details></span>"
        % (esc(ctx["user_id"]), shown, nonce_field(ctx), esc(name),
           esc(ctx["user_id"]), nonce_field(ctx)))


def address_chip(ctx):
    """The upper-left address, expanding to the full list. Pure <details>.

    There is no way to read the device's real location without JavaScript, and
    the Instamart MCP has no "nearest address" tool - so this shows the saved
    address actually in use and says exactly that. It never implies it knows
    where you are.
    """
    if not ctx.get("signed_in"):
        return "<div class=\"grow\"></div>"

    label = ctx.get("address_label") or "Choose a delivery address"
    rows = []
    for addr_label, addr_id in ctx.get("addresses") or []:
        current = addr_id == ctx.get("address_id")
        rows.append(
            "<div class=\"addr-row\"><form method=\"post\" action=\"/address/select\">%s"
            "<input type=\"hidden\" name=\"address_id\" value=\"%s\">"
            "<button type=\"submit\"><span class=\"tick\">%s</span>"
            "<span>%s</span><span class=\"addr-id\">%s</span></button></form></div>"
            % (nonce_field(ctx), esc(addr_id), "&#10003;" if current else "",
               esc(addr_label), esc(addr_id[:10])))
    if not rows:
        rows.append("<div class=\"addr-row\"><button type=\"button\" disabled "
                    "style=\"color:var(--muted)\">No saved addresses yet</button></div>")

    return (
        "<details class=\"addr\"><summary><span class=\"pin\">&#9679;</span>"
        "<span class=\"addr-txt\"><span class=\"addr-kicker\">Delivering to</span>"
        "<span class=\"addr-name\">%s</span></span><span class=\"caret\">&#9660;</span>"
        "</summary><div class=\"addr-list\">%s</div>"
        "<a class=\"addr-add\" href=\"/address/new\">+ Add a new address</a>"
        "<p class=\"small muted\">Your saved Swiggy addresses. Switching one "
        "rebuilds the cart, because a stored item id is only valid at the "
        "address it was found from.</p></details>"
        % (esc(label), "".join(rows)))


def nonce_field(ctx):
    return "<input type=\"hidden\" name=\"_t\" value=\"%s\">" % esc((ctx or {}).get("nonce"))


def notes(ctx):
    out = []
    if ctx.get("dry_run"):
        out.append(note("warn", "<b>Dry run.</b> Your cart is never written and "
                                "no order can be placed."))
    for kind, text in ctx.get("flash") or []:
        out.append(note(kind, text))
    return "".join(out)


def note(kind, html):
    return "<p class=\"note %s\">%s</p>" % (kind, html)


def steps(active):
    names = ["List", "Pick", "Cart", "Pay"]
    return "<div class=\"steps\">%s</div>" % " &rsaquo; ".join(
        ("<b>%s</b>" if n == active else "%s") % n for n in names)


# ---------------------------------------------------------------- pages

def login_page(error=None, nonce=None):
    body = (
        "<div class=\"hero\"><div class=\"mark\">&#127859;</div>"
        "<h1>%s</h1><p class=\"sub\">Order Instamart groceries by typing a list. "
        "See the real total before you pay.</p>%s"
        "<form method=\"post\" action=\"/login\">%s"
        "<button class=\"btn\" type=\"submit\">Sign in with Swiggy</button></form>"
        "<p class=\"small muted\" style=\"margin-top:1.5rem;max-width:44ch;"
        "margin-left:auto;margin-right:auto\">Opens Swiggy&rsquo;s own sign-in for "
        "your phone number and OTP. This app never sees your password, and your "
        "session is stored under your home directory, not in this folder.</p></div>"
        % (esc(BRAND), note("bad", esc(error)) if error else "",
           nonce_field({"nonce": nonce}))
    )
    return page("Sign in - %s" % BRAND, body)


def home_page(ctx):
    status = ctx.get("status")
    cart = ""
    if ctx.get("lines"):
        rows = "".join(
            "<div class=\"line\">%s"
            "<div class=\"line-main\"><div class=\"line-name\">%s</div>"
            "<div class=\"line-meta\">%s each &times; %d</div></div>"
            "<div class=\"line-amt\">%s</div></div>"
            % (thumb(line.get("image"), line["name"]),
               esc(line["name"]), rupees(line["price"]), line["quantity"],
               rupees(line["price"] * line["quantity"]))
            for line in ctx["lines"])
        cart = ("<div class=\"card\"><h2>In your basket</h2>%s%s"
                "<div class=\"row end\" style=\"margin-top:.9rem\">"
                "<a class=\"btn ghost\" href=\"/cart\">Go to cart &rarr;</a></div></div>"
                % (rows, budget_meter(status)))

    # Only promise what is actually wired up: without a key the box is a
    # plain list box, and saying otherwise would be a lie in the placeholder.
    if ctx.get("recipes_on"):
        subtitle = "One item per line - or just describe what you want to cook."
        placeholder = ("1 litre amul milk&#10;500 gms paneer&#10;"
                       "I want to make a mango smoothie")
        hint = ("Understands g / kg / ml / litre / pieces / dozen, packs and "
                "spelled-out numbers - and works out the ingredients if you "
                "name a dish.")
    else:
        subtitle = "One item per line, or all on one line separated by commas."
        placeholder = ("1 litre amul milk&#10;500 gms paneer&#10;"
                       "a dozen eggs, 2 packets of maggi")
        hint = ("Understands g / kg / ml / litre / pieces / dozen, packs and "
                "spelled-out numbers.")

    body = (
        "%s%s<h1>%s</h1>"
        "<p class=\"sub\">%s</p>"
        "<div class=\"card\"><form method=\"post\" action=\"/order\">%s"
        "<textarea name=\"text\" autofocus placeholder=\"%s\">%s</textarea>"
        "<p class=\"small muted\">%s You confirm every item before anything is "
        "added.</p>"
        "<button class=\"btn block\" type=\"submit\" style=\"margin-top:.6rem\">"
        "Find these items</button></form></div>%s"
        % (notes(ctx), steps("List"),
           "What else do you need?" if ctx.get("lines") else "What do you need?",
           subtitle, nonce_field(ctx),
           placeholder, esc(ctx.get("draft") or ""), hint, cart)
    )
    return page(BRAND, body, ctx=ctx)


def budget_meter(status):
    """A running total against the ceiling, drawn as one bar.

    While fees are unmeasured this shows the item total and says so. It does
    not show a fee, because there is no fee to show: Swiggy prices them per
    cart, per address, per hour, and anything put here before that would be a
    number the server never said.
    """
    if status is None:
        return ""
    limit = max(status.limit, 1)
    pct = min(100, int(round(100.0 * status.projected / limit)))
    if status.measured:
        headline = rupees(status.projected)
        breakdown = "items %s + fees %s, priced by Swiggy" % (
            rupees(status.item_total), rupees(status.fees))
        footer = "%s of headroom left." % rupees(status.headroom)
    else:
        headline = rupees(status.item_total)
        breakdown = "items only - fees are added when Swiggy prices your cart"
        footer = "%s under the ceiling before fees." % rupees(status.headroom)
    return (
        "<hr><div class=\"row\"><div class=\"grow\"><b>%s</b> "
        "<span class=\"small muted\">%s</span></div>"
        "<div class=\"small muted\">ceiling %s</div></div>"
        "<div class=\"bar\"><i class=\"%s\" style=\"width:%d%%\"></i></div>"
        "<div class=\"small %s\">%s</div>"
        % (headline, breakdown, rupees(status.limit),
           "" if status.ok else "over", pct,
           "muted" if status.ok else "note bad",
           footer if status.ok else esc(status.message()))
    )


def review_page(ctx, requests):
    """Echo every parsed quantity back before it can reach the cart.

    Section 1.10 of the field notes: a quantity was silently swallowed by the
    parser once and nothing caught it. This page is that catch.
    """
    rows = "".join(
        "<div class=\"line\"><div class=\"line-main\"><div class=\"line-name\">%s</div>"
        "<div class=\"line-meta\">%s</div></div><div class=\"line-amt\">&times;%d</div></div>"
        % (esc(request.name), esc(str(request.size) if request.size else "any size"),
           request.count)
        for request in requests)
    # When the list was suggested rather than typed, say so plainly. The
    # person never wrote these words, so "did I read that right" would be the
    # wrong question - the question is whether the suggestion is any good.
    suggested = (ctx.get("suggested_from") or "").strip()
    if suggested:
        # Never build a sentence around the person's words - they already
        # wrote a sentence. "To make " + "I want to make a mango smoothie"
        # reads as "To make I want to make a mango smoothie". Quote them
        # instead, and the heading stays right whatever they typed.
        heading = "What you&rsquo;ll need"
        blurb = ("For &ldquo;%s&rdquo; &mdash; a suggested shopping list, worked "
                 "out by an AI, so check it. Quantities are shop pack sizes, "
                 "not recipe amounts. Nothing is searched or added until you "
                 "say yes." % esc(suggested.rstrip(".?! ")))
        back = "Ask for something else"
    else:
        heading = "Did I read that right?"
        blurb = "Nothing is searched or added until you say yes."
        back = "Edit the list"
    body = (
        "%s%s<h1>%s</h1>"
        "<p class=\"sub\">%s</p>"
        "<div class=\"card\">%s<hr>"
        "<form method=\"post\" action=\"/review\">%s"
        "<label>Anything missing? <span class=\"opt\">added to the list above"
        "</span></label>"
        "<textarea name=\"add\" rows=\"2\" placeholder=\"200 g vanilla ice cream, "
        "1 packet straws\"></textarea>"
        "<div class=\"row end\" style=\"margin-top:.7rem\">"
        "<a class=\"btn ghost\" href=\"/\">%s</a>"
        "<button class=\"btn ghost\" type=\"submit\" name=\"more\" value=\"1\">"
        "Add to list</button>"
        "<button class=\"btn\" type=\"submit\">Yes, find these</button>"
        "</div></form></div>"
        % (notes(ctx), steps("List"), heading, blurb, rows, nonce_field(ctx), back)
    )
    return page("Check your list - %s" % BRAND, body, ctx=ctx)


def choose_page(ctx, item):
    """Pick a variant by hand, because nothing matched the size asked for."""
    request = item["request"]
    picks = []
    for index, option in enumerate(item["options"]):
        row, size, exact = option["row"], option["size"], option["exact"]
        tag = ""
        if exact:
            tag = "<span class=\"tag\">exact size</span>"
        elif size is not None:
            tag = "<span class=\"tag grey\">%s</span>" % esc(str(size))
        was = ("  <span class=\"was\">%s</span>" % rupees(row["mrp"])
               if row["mrp"] and row["mrp"] > row["price"] else "")
        cap = ("<span class=\"tag grey\">max %d</span>" % row["maxQuantity"]
               if row["maxQuantity"] and row["maxQuantity"] < 5 else "")
        promoted = ("<span class=\"tag grey\">sponsored</span>"
                    if row["promoted"] else "")
        unit = ("<span class=\"unit\">%s</span>" % esc(row["unitPrice"])
                if row.get("unitPrice") else "")
        picks.append(
            "<label class=\"pick\">"
            "<input type=\"radio\" name=\"pick\" value=\"%d\"%s>%s"
            "<span class=\"pick-body\"><b>%s</b> %s%s%s%s%s</span>"
            "<span class=\"amt\">%s%s</span></label>"
            % (index, " checked" if index == 0 else "",
               thumb(row.get("image"), row["name"]),
               esc(product_label(row)),
               esc(row["variant"]), tag, cap, promoted, unit,
               rupees(row["price"]), was))

    cap = item["max_quantity"]
    options = "".join("<option value=\"%d\"%s>%d</option>"
                      % (q, " selected" if q == item["quantity"] else "", q)
                      for q in range(1, cap + 1))
    why = item.get("why") or ""
    body = (
        "%s%s<h1>Which %s?</h1><p class=\"sub\">%s</p>"
        "<div class=\"card\"><form method=\"post\" action=\"/choose\">%s"
        "%s<label>How many <span class=\"opt\">(Swiggy allows up to %d per order)"
        "</span></label><select name=\"quantity\">%s</select>"
        "<div class=\"row end\" style=\"margin-top:.9rem\">"
        "<button class=\"btn ghost\" type=\"submit\" name=\"action\" value=\"skip\">"
        "Skip this item</button>"
        "<button class=\"btn\" type=\"submit\" name=\"action\" value=\"add\">"
        "Add to basket</button></div></form></div>%s"
        % (notes(ctx), steps("Pick"), esc(request.name), esc(why),
           nonce_field(ctx), "".join(picks), cap, options,
           "<p class=\"small muted\">%d more item(s) to go.</p>" % item["remaining"]
           if item["remaining"] else "")
    )
    return page("Choose - %s" % BRAND, body, ctx=ctx)


def cart_page(ctx, view):
    """The real, server-priced cart plus the Rs 1000 gate."""
    rows = (view or {}).get("rows") or []
    lines = "".join(cart_row(ctx, row) for row in rows)

    if not rows:
        body = ("%s%s<h1>Your cart is empty</h1>"
                "<p class=\"sub\">Nothing has been ordered.</p>"
                "<a class=\"btn\" href=\"/\">Start a list</a>"
                % (notes(ctx), steps("Cart")))
        return page("Cart - %s" % BRAND, body, ctx=ctx)

    bill_rows = "".join(
        "<tr><td>%s</td><td class=\"%s\">%s</td></tr>"
        % (esc(label), "free" if str(value).strip().upper() == "FREE" else "", esc(value))
        for label, value in view["bill_lines"])
    to_pay = view["to_pay"]
    bill = ("<table class=\"bill\">%s<tr class=\"total\"><td>To pay</td>"
            "<td>%s</td></tr></table>" % (bill_rows, rupees(to_pay)))

    gate = ""
    if view["blocked"]:
        gate = note("bad", "<b>Over the &#8377;1,000 limit.</b> %s Swiggy refuses "
                           "checkout at &#8377;1,000 or more, so remove something below."
                    % esc(view["gate_message"]))
    elif view["near_limit"]:
        gate = note("warn", "Only %s below the &#8377;1,000 checkout limit."
                    % rupees(CHECKOUT_LIMIT_PAISE - to_pay))

    if view["warnings"]:
        gate += note("warn", "<b>Swiggy changed part of this cart:</b><ul>%s</ul>"
                     % "".join("<li>%s</li>" % esc(w) for w in view["warnings"]))

    if view.get("foreign"):
        gate += note("warn",
                     "<b>%d item%s in your Swiggy cart came from somewhere else"
                     "</b> - the phone app, most likely. %s marked "
                     "&ldquo;added elsewhere&rdquo; below, and included in the "
                     "total. Removing one empties the cart on Swiggy&rsquo;s "
                     "side and writes back only your basket."
                     % (len(view["foreign"]),
                        "" if len(view["foreign"]) == 1 else "s",
                        "It is" if len(view["foreign"]) == 1 else "They are"))

    if view["drift"]:
        gate += note("info", "The running estimate said %s; the server says %s. "
                             "The server&rsquo;s figure is the one you pay."
                     % (rupees(view["projected"]), rupees(to_pay)))

    if ctx.get("payment"):
        pay_row = (
            "<div class=\"row\" style=\"margin-top:1rem\">"
            "<div class=\"grow small\"><span class=\"muted\">Paying with</span><br>"
            "<b>%s</b></div><a class=\"btn ghost sm\" href=\"#payment\">Change</a></div>"
            "<a class=\"btn block\" href=\"/confirm\" style=\"margin-top:.8rem\">"
            "Review &amp; place order</a>"
            % esc(ctx["payment"]["label"]))
    else:
        pay_row = ("<a class=\"btn block\" href=\"#payment\" style=\"margin-top:1rem\">"
                   "Choose payment method</a>")
    if view["blocked"] or ctx.get("dry_run"):
        reason = ("Dry run - ordering is disabled." if ctx.get("dry_run")
                  else "Remove items to get under &#8377;1,000.")
        pay_row = ("<button class=\"btn block\" disabled style=\"margin-top:1rem\">"
                   "Checkout unavailable</button><p class=\"small muted\" "
                   "style=\"text-align:center\">%s</p>" % reason)

    body = (
        "%s%s<h1>Your cart</h1>"
        "<p class=\"sub\">Priced by Swiggy just now, at %s.</p>%s"
        "<div class=\"card\">%s<hr>"
        "<a class=\"btn ghost block\" href=\"/\">+ Add more items</a></div>"
        "<div class=\"card\"><h2>Bill</h2>%s%s</div>%s"
        % (notes(ctx), steps("Cart"), esc(ctx.get("address_label") or "your address"),
           gate, lines, bill, pay_row, payment_modal(ctx, view["payment_options"]))
    )
    return page("Cart - %s" % BRAND, body, ctx=ctx)


def cart_row(ctx, row):
    """One cart line. Ours gets controls; a stranger gets an explanation.

    An item this session did not add still appears here, because Swiggy is
    billing for it either way. Hiding it is how a total ends up disagreeing
    with the rows above it.
    """
    tags = ""
    if not row["in_stock"]:
        tags += ("<span class=\"tag grey\">out of stock - not billed</span>")
    if not row["ours"]:
        tags += "<span class=\"tag warnish\">added elsewhere</span>"

    if row["ours"]:
        controls = (
            "<form method=\"post\" action=\"/cart/qty\" class=\"inline\">%s"
            "<input type=\"hidden\" name=\"index\" value=\"%d\">"
            "<span class=\"qty\">"
            "<button type=\"submit\" name=\"delta\" value=\"-1\" "
            "title=\"one fewer\">&minus;</button><span>%d</span>"
            "<button type=\"submit\" name=\"delta\" value=\"1\" "
            "title=\"one more\">+</button></span></form>"
            % (nonce_field(ctx), row["index"], row["quantity"]))
        action = ("<form method=\"post\" action=\"/cart/remove\" class=\"inline\">%s"
                  "<input type=\"hidden\" name=\"index\" value=\"%d\">"
                  "<button class=\"btn ghost sm\" type=\"submit\">Remove</button>"
                  "</form>" % (nonce_field(ctx), row["index"]))
    else:
        controls = "<span class=\"qty static\"><span>%d</span></span>" % row["quantity"]
        action = ("<form method=\"post\" action=\"/cart/rebuild\" class=\"inline\">%s"
                  "<button class=\"btn ghost sm\" type=\"submit\" "
                  "title=\"Empty the Swiggy cart and write back only your basket\">"
                  "Remove</button></form>" % nonce_field(ctx))

    return ("<div class=\"line\">%s"
            "<div class=\"line-main\"><div class=\"line-name\">%s%s</div>"
            "<div class=\"line-meta\">%s each</div></div>%s"
            "<div class=\"line-amt\">%s<br>%s</div></div>"
            % (thumb(row.get("image"), row["name"]), esc(row["name"]), tags,
               rupees(row["price"]), controls,
               rupees(row["price"] * row["quantity"]), action))


def payment_modal(ctx, options):
    """A real modal with no JavaScript: :target opens it, a link closes it."""
    if options is None:
        inner = ("<p class=\"muted\">Could not load payment methods. "
                 "<a href=\"/cart\">Try again</a>.</p>")
    elif not options:
        inner = "<p class=\"muted\">Swiggy offered no payment methods for this cart.</p>"
    else:
        blocks, last_group = [], None
        for index, option in enumerate(options):
            if option["group"] != last_group:
                blocks.append("<p class=\"pay-group\">%s</p>" % esc(option["group"]))
                last_group = option["group"]
            blocks.append(
                "<form method=\"post\" action=\"/payment\">%s"
                "<input type=\"hidden\" name=\"index\" value=\"%d\">"
                "<button class=\"pay-opt\" type=\"submit\"><b>%s</b><span>%s</span>"
                "</button></form>"
                % (nonce_field(ctx), index, esc(option["name"]), esc(option["hint"])))
        inner = "".join(blocks)

    return (
        "<div class=\"modal\" id=\"payment\"><a class=\"modal-back\" href=\"#\" "
        "aria-label=\"Close\"></a><div class=\"modal-card\">"
        "<div class=\"modal-head\"><h3>How would you like to pay?</h3>"
        "<a class=\"modal-x\" href=\"#\">&times;</a></div>"
        "<p class=\"small muted\">Only the methods Swiggy offers for this cart "
        "are shown.</p>%s</div></div>" % inner)


def confirm_page(ctx, view):
    body = (
        "%s%s<h1>Place this order?</h1>"
        "<p class=\"sub\">Re-checked with Swiggy a moment ago.</p>"
        "%s"
        "<div class=\"card\"><table class=\"bill\">"
        "<tr><td>Deliver to</td><td>%s</td></tr>"
        "<tr><td>Items</td><td>%d</td></tr>"
        "<tr><td>Paying with</td><td>%s</td></tr>"
        "<tr class=\"total\"><td>You will pay</td><td>%s</td></tr></table></div>"
        "<div class=\"card\"><form method=\"post\" action=\"/checkout\">%s"
        "<input type=\"hidden\" name=\"expect_paise\" value=\"%d\">"
        "<button class=\"btn block danger\" type=\"submit\">"
        "Place the order &middot; pay %s</button></form>"
        "<a class=\"btn ghost block\" href=\"/cart\" style=\"margin-top:.6rem\">"
        "Back to cart</a></div>"
        % (notes(ctx), steps("Pay"),
           note("bad", "<b>This places a real order and charges real money.</b> "
                       "Swiggy&rsquo;s MCP has no cancellation tool &mdash; once "
                       "placed, cancelling means calling Swiggy care on 080-67466729."),
           esc(ctx.get("address_label")), view["item_count"],
           esc(ctx["payment"]["label"]), rupees(view["to_pay"]),
           nonce_field(ctx), view["to_pay"], rupees(view["to_pay"]))
    )
    return page("Confirm - %s" % BRAND, body, ctx=ctx)


def placed_page(ctx, view):
    body = (
        "%s<div class=\"card\" style=\"text-align:center\">"
        "<div style=\"font-size:2.2rem\">&#10003;</div>"
        "<h1>%s</h1><p class=\"sub\">%s</p>"
        "<table class=\"bill\" style=\"text-align:left\">"
        "<tr><td>Order</td><td class=\"mono\">%s</td></tr>"
        "<tr><td>Status</td><td>%s</td></tr>"
        "<tr><td>Paid</td><td>%s</td></tr>"
        "<tr><td>Deliver to</td><td>%s</td></tr></table>"
        "<a class=\"btn\" href=\"/\" style=\"margin-top:1rem\">Start another order</a>"
        "</div>%s"
        % (notes(ctx), esc(view["headline"]), esc(view.get("detail") or ""),
           esc(view.get("order_id") or "?"), esc(view.get("status") or "?"),
           rupees(view.get("total")), esc(ctx.get("address_label")),
           ("<p class=\"small muted\">Track it with <span class=\"mono\">%s</span></p>"
            % esc(view["track"]) if view.get("track") else ""))
    )
    return page("Order placed - %s" % BRAND, body, ctx=ctx)


def pending_payment_page(ctx, view):
    """Waiting on a UPI approval. Refreshes sparsely, never in a tight loop."""
    head = ("<meta http-equiv=\"refresh\" content=\"15\">"
            if view.get("keep_polling") else "")
    body = (
        "%s<div class=\"card\" style=\"text-align:center\">"
        "<h1>Approve the payment</h1>"
        "<p class=\"sub\">Open your UPI app and approve %s for order "
        "<span class=\"mono\">%s</span>.</p>"
        "%s"
        "<p class=\"small muted\">Last checked: %s%s</p>"
        "<div class=\"row\" style=\"justify-content:center\">"
        "<a class=\"btn ghost\" href=\"/payment/pending\">Check now</a>"
        "<a class=\"btn\" href=\"/placed\">Stop checking</a></div></div>"
        % (notes(ctx), rupees(view.get("total")), esc(view.get("order_id")),
           note("info", esc(view.get("status_text") or "waiting for the payment")),
           esc(view.get("checked_at") or "just now"),
           " &middot; rechecking every 15s" if view.get("keep_polling") else "")
    )
    return page("Payment pending - %s" % BRAND, body, head=head, ctx=ctx)


def address_form_page(ctx, values=None, error=None):
    values = values or {}

    def field(name, label, hint="", kind="text", value=None):
        return ("<label>%s%s</label><input type=\"%s\" name=\"%s\" value=\"%s\">"
                % (esc(label), " <span class=\"opt\">%s</span>" % esc(hint) if hint else "",
                   kind, name, esc(value if value is not None else values.get(name, ""))))

    categories = ["HOME", "WORK", "OFFICE", "FRIENDS_AND_FAMILY", "OTHER"]
    picked = values.get("addressCategory") or "HOME"
    cats = "".join("<option value=\"%s\"%s>%s</option>"
                   % (c, " selected" if c == picked else "", c.replace("_", " ").title())
                   for c in categories)
    body = (
        "%s<h1>Add a delivery address</h1>"
        "<p class=\"sub\">Write it as you would on a parcel. Swiggy resolves the "
        "coordinates itself.</p>%s"
        "<div class=\"card\"><form method=\"post\" action=\"/address/new\">%s"
        "<label>Full address</label>"
        "<textarea name=\"fullAddress\" autofocus placeholder=\"Flat 402, Rose "
        "Apartments, 17th Main, HSR Layout Sector 3, Bengaluru 560102\">%s</textarea>"
        "%s%s"
        "<details style=\"margin-top:.9rem\"><summary class=\"small muted\" "
        "style=\"cursor:pointer\">Correct the parts I pulled out (optional)</summary>"
        "%s%s%s</details>"
        "<label>Address type</label><select name=\"addressCategory\">%s</select>"
        "%s%s"
        "<button class=\"btn block\" type=\"submit\" style=\"margin-top:1rem\">"
        "Save address</button></form>"
        "<a class=\"btn ghost block\" href=\"/\" style=\"margin-top:.6rem\">Cancel</a>"
        "</div>"
        % (notes(ctx), note("bad", esc(error)) if error else "", nonce_field(ctx),
           esc(values.get("fullAddress", "")),
           field("userName", "Your name"),
           field("userPhone", "Your phone number", kind="tel"),
           field("addressLine", "House / building / street",
                 "left blank, taken from the address above"),
           field("addressLine2", "Apartment / floor / wing", "if any"),
           field("locality", "Locality / area", "optional"),
           cats,
           field("city", "City", "left blank, taken from the address above"),
           field("postalCode", "Postal code", "left blank, taken from the address above"))
    )
    return page("Add an address - %s" % BRAND, body, ctx=ctx)


def error_page(ctx, title, detail, retry="/", relogin=False):
    extra = ""
    if relogin:
        extra = ("<form method=\"post\" action=\"/login\">%s<button class=\"btn\" "
                 "type=\"submit\">Sign in again</button></form>" % nonce_field(ctx))
    body = (
        "%s<div class=\"card\"><h1>%s</h1>%s"
        "<p class=\"small muted\">Nothing was ordered.</p>"
        "<div class=\"row\"><a class=\"btn ghost\" href=\"%s\">Go back</a>%s</div></div>"
        % (notes(ctx), esc(title), note("bad", esc(detail)), esc(retry), extra)
    )
    return page("%s - %s" % (title, BRAND), body, ctx=ctx)
