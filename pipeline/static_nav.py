"""The app's nav on the static report and article pages.

2026-11-09, user: "Make the nav the same on the articles/reports as it is on
the rest of the site." Humans land on these pages on any direct load (Caddy
serves /reports/* and /articles/* file-first), and they carried a one-line
text header while every app page has the logo with the sliding drawer.

The stylesheet IS the app's NavBar.css, read from the client source when a
page is rendered, so the two cannot drift. The tokens it reads are scoped to
the header: the report pages use --brand for their link blue, the nav uses it
for the site's yellow. A short script stands in for NavBar.tsx: tap-to-open
on touch and narrow screens (hover opens it on desktop, in CSS), hide on
scroll down and return on scroll up, outside click or Escape to close, and a
swap to the signed-in links when the account cookie answers.

Also here: the front-page call to action that closes every report and
article (same request), and an article's next steps (2026-11-13): every card
the piece is about after its opening section, and after the piece those cards
again, a free portfolio and more articles on the game.
"""
import html
import os

HERE = os.path.dirname(os.path.abspath(__file__))
NAVBAR_CSS = os.path.join(HERE, "..", "client", "src", "styles", "components", "NavBar.css")
GAMEPICK_CSS = os.path.join(HERE, "..", "client", "src", "styles", "components", "GamePicker.css")

# The nav's game picker (2026-11-15), as the app draws it (GamePicker.tsx):
# key, name, the mark in the circle, the game's colour. Mirrors
# client/src/lib/games.ts (GAMES, GAME_COLOR) and siteGame.ts (GAME_MARK).
PICK_GAMES = [
    ("cyberpunk", "Cyberpunk", "CYB", "#f0b429"), ("digimon", "Digimon", "DIG", "#3fd98a"),
    ("fleshandblood", "Flesh and Blood", "FAB", "#e05c5c"), ("gundam", "Gundam", "GUN", "#f06292"),
    ("lorcana", "Lorcana", "LOR", "#4dd0e1"), ("magic", "Magic", "MTG", "#ff9e64"),
    ("onepiece", "One Piece", "OP", "#3d7dca"), ("pokemon", "Pok\u00e9mon", "PKM", "#ffcb05"),
    ("riftbound", "Riftbound", "RIF", "#5a9cf0"), ("sorcery", "Sorcery", "SOR", "#9ccc65"),
    ("starwars", "Star Wars Unlimited", "SWU", "#e8ecf4"), ("yugioh", "Yu-Gi-Oh!", "YGO", "#c678dd"),
]
GAME_COLOR = {k: c for k, _, _, c in PICK_GAMES}
# Games with a picture for the mark (client/public/images/games), as in siteGame.ts.
PICTURED = {"cyberpunk", "digimon", "fleshandblood", "gundam", "lorcana", "magic", "onepiece",
            "pokemon", "riftbound", "sorcery", "yugioh"}
SITE = "https://cardstock.guide"

# The tokens NavBar.css reads, copied from client/src/styles/tokens.css and
# scoped to the header so the page's own --brand (link blue) is untouched.
TOKENS = """
:root{--nav-bar-h:64px;--page-top:30px}
@media (max-width:1023px){:root{--nav-bar-h:56px;--page-top:calc(var(--nav-bar-h) + 2px + 20px)}}
.navbar,.navbar__scrim,.gamepick{--brand:#ffcb05;--on-brand:#101623;--border:#2e3a52;--nav-link:#dbe4f0;
--nav-glass:rgba(12,16,26,.55);--radius:15px;--space-5:5px;--space-10:10px;--space-15:15px;
--space-20:20px;--space-25:25px;--space-95:95px;--control-h:38px;--sidebar-w:300px;--logo-top:30px;
--container-max:1280px;--container-pad:40px;
--content-left:calc(max(0px,(100vw - var(--container-max)) / 2) + var(--container-pad));
--logo-h:40px;--logo-w:calc(var(--logo-h) * 63 / 88);
--tipped-logo-left:calc(var(--content-left) / 2 + var(--logo-w) / 2);
--nav-w:max(var(--sidebar-w),calc(var(--tipped-logo-left) + 226px));--ticker-h:40px}
@media (max-width:1023px){.navbar,.navbar__scrim,.gamepick{--radius:24px;--container-pad:24px}}
@media (max-width:767px){.navbar,.navbar__scrim,.gamepick{--radius:26px;--container-pad:20px}}
.gamepick{--text:#e8ecf4;--surface-2:#141c2e;--font-mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
font-family:system-ui,-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif}
.gamepick__menu li{margin:0}
.navbar__scrim[hidden]{display:none!important}
.navbar a{text-decoration:none}
.navbar__link{font-family:system-ui,-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif}
/* The page starts level with the logo (desktop) or under the top bar. */
.wrap{padding-top:var(--page-top)}
"""

# Fallback if the client source is not beside the pipeline: the plain header
# the pages had before. The nightly and the trainer both have the repo, so
# this should never print.
PLAIN = """<header class="site">
  <a class="logo" href="{site}/">CardStock</a>
  <nav><a href="{site}/catalog">Catalog</a><a href="{site}/reports">Reports</a>
  <a href="{site}/articles">Articles</a><a href="{site}/about">About</a></nav>
</header>"""

LINKS = [("catalog", "CATALOG"), ("reports", "REPORTS"), ("articles", "ARTICLES"), ("about", "ABOUT")]


def nav_css():
    try:
        with open(NAVBAR_CSS, encoding="utf-8") as f:
            css = f.read()
        try:
            with open(GAMEPICK_CSS, encoding="utf-8") as f:
                css += f.read()
        except OSError:
            print(f"static_nav: {GAMEPICK_CSS} not found, no game picker styles", flush=True)
        return css + TOKENS
    except OSError:
        print(f"static_nav: {NAVBAR_CSS} not found, plain header", flush=True)
        return ""


def nav_html(active=None, site=SITE):
    """The header, mirroring NavBar.tsx signed out; the script swaps in the
    signed-in links when account/user-info answers 200."""
    if not nav_css():
        return PLAIN.format(site=site)
    items = "".join(
        f'<li><a class="navbar__link{" active" if key == active else ""}" href="{site}/{key}">{label}</a></li>'
        for key, label in LINKS)
    img = lambda key: f"{site}/images/games/{key}.webp" if key in PICTURED else ""
    options = "".join(
        f'<li><button type="button" role="option" class="gamepick__option" data-game="{key}" '
        f'data-mark="{mark}" data-color="{color}" data-img="{img(key)}"><span class="gamepick__dot" '
        f'style="background:{color}"></span>{html.escape(label)}</button></li>'
        for key, label, mark, color in PICK_GAMES)
    picker = f"""
<div class="gamepick" id="gamepick">
  <button type="button" class="gamepick__mark" aria-haspopup="listbox" aria-expanded="false" aria-label="Choose a game"><span class="gamepick__img gamepick__img--all"></span></button>
  <ul class="gamepick__menu" role="listbox" aria-label="Choose a game">
    <li><button type="button" role="option" class="gamepick__option" data-game=""><span class="gamepick__dot"></span>All games</button></li>
    {options}
  </ul>
</div>"""
    return f"""<div class="navbar__scrim" id="navbar-scrim" hidden></div>
<header class="navbar" id="navbar">
  <div class="navbar__bar">
    <a class="navbar__brand" id="navbar-brand" href="{site}/" aria-label="CardStock home" aria-expanded="false">
      <svg class="navbar__logo" viewBox="0 0 63 88" fill="none" role="img" aria-hidden="true">
        <rect x="2.5" y="2.5" width="58" height="83" rx="7" stroke="#fff" stroke-width="4"/>
        <polyline class="navbar__logo-trend" points="52,76 30,54 40,44 18,22 28,12 -6,-22"/>
        <path class="navbar__logo-arrow" d="M-4 -11 L-6 -22 L5 -20"/>
      </svg>
      <span class="navbar__wordmark" aria-hidden="true" style="margin-top:5px">
        <span class="navbar__word navbar__word--1">card</span>
        <span class="navbar__word navbar__word--2">STOCK</span>
      </span>
    </a>
  </div>
  <div class="navbar__drawer">
    <div class="navbar__panel">
      <nav class="navbar__nav">
        <ul class="navbar__links" id="navbar-links">
          <li class="navbar__home-item"><a class="navbar__link" href="{site}/">HOME</a></li>
          {items}
        </ul>
      </nav>
      <div class="navbar__end" id="navbar-end">
        <ul class="navbar__links navbar__auth">
          <li><a class="navbar__link" href="{site}/login">LOGIN</a></li>
          <li><a class="navbar__link" href="{site}/register">REGISTER</a></li>
        </ul>
      </div>
    </div>
  </div>
</header>{picker}"""


NAV_JS = """
(function () {
  var nav = document.getElementById('navbar'), brand = document.getElementById('navbar-brand'),
      scrim = document.getElementById('navbar-scrim');
  if (!nav || !brand || !scrim) return;
  var open = false;
  function set(o) {
    open = o;
    nav.classList.toggle('navbar--open', o);
    brand.setAttribute('aria-expanded', String(o));
    scrim.hidden = !o;
    if (o) nav.classList.remove('navbar--hidden');
  }
  // Touch devices and the tablet/mobile top bar: the logo toggles the drawer
  // (the drawer's HOME link navigates). Desktop hover devices: the logo is a
  // link and hovering opens the drawer (NavBar.css).
  brand.addEventListener('click', function (e) {
    if (matchMedia('(hover: none), (max-width: 1023px)').matches) { e.preventDefault(); set(!open); }
  });
  scrim.addEventListener('click', function () { set(false); });
  document.addEventListener('click', function (e) {
    if (open && !nav.contains(e.target)) set(false);
  });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') set(false); });
  // Hide on scroll down, return on scroll up; always shown near the top.
  var last = window.scrollY, raf = 0;
  window.addEventListener('scroll', function () {
    if (raf) return;
    raf = requestAnimationFrame(function () {
      raf = 0;
      var y = window.scrollY, dy = y - last; last = y;
      if (y < 24) nav.classList.remove('navbar--hidden');
      else if (dy > 6) nav.classList.add('navbar--hidden');
      else if (dy < -6) nav.classList.remove('navbar--hidden');
    });
  }, { passive: true });
  // Signed in: the same links the app shows (portfolio, watchlist, scan;
  // account + logout at the foot). The page is static, so it asks.
  try {
    fetch('/api/account/user-info', { credentials: 'include' }).then(function (r) {
      if (r.status !== 200) return;   // anonymous visitors get 204, not an error
      var links = document.getElementById('navbar-links'), end = document.getElementById('navbar-end');
      if (!links || !end) return;
      [['portfolio', 'PORTFOLIO'], ['watchlist', 'WATCHLIST'], ['scan', 'SCAN']].forEach(function (p) {
        var li = document.createElement('li'), a = document.createElement('a');
        a.className = 'navbar__link'; a.href = '/' + p[0]; a.textContent = p[1];
        li.appendChild(a); links.appendChild(li);
      });
      end.innerHTML = '<div class="navbar__usermenu"><a class="navbar__link" href="/settings" title="Account settings">ACCOUNT</a>'
        + '<button class="navbar__link" type="button" id="navbar-logout">LOGOUT</button></div>';
      document.getElementById('navbar-logout').addEventListener('click', function () {
        fetch('/api/account/logout', { method: 'POST', credentials: 'include' })
          .then(function () { location.href = '/'; }, function () { location.href = '/'; });
      });
      // An article's portfolio step opens the portfolio for a member, not
      // the sign-up form.
      [].forEach.call(document.querySelectorAll('[data-signed-in-href]'), function (a) {
        a.href = a.getAttribute('data-signed-in-href');
        [].forEach.call(a.querySelectorAll('[data-signed-in-text]'), function (t) {
          t.textContent = t.getAttribute('data-signed-in-text');
        });
      });
    }).catch(function () {});
  } catch (e) {}
})();
// The game picker (GamePicker.tsx): the choice lives in this browser under
// the app's own keys (client/src/lib/siteGame.ts), so the home page and the
// catalog open on it. Nothing on this page changes but the mark.
(function () {
  var el = document.getElementById('gamepick');
  if (!el) return;
  var mark = el.querySelector('.gamepick__mark'), opts = el.querySelectorAll('[data-game]');
  function get() { try { return localStorage.getItem('cs.game') || ''; } catch (e) { return ''; } }
  function paint() {
    var g = get(), on = null;
    [].forEach.call(opts, function (o) {
      var hit = o.getAttribute('data-game') === g;
      o.classList.toggle('gamepick__option--on', hit);
      o.setAttribute('aria-selected', String(hit));
      if (hit) on = o;
    });
    var src = on && g ? on.getAttribute('data-img') : '';
    mark.textContent = '';
    if (on && g) {
      if (src) {
        var im = document.createElement('img');
        im.className = 'gamepick__img'; im.alt = ''; im.width = im.height = 26; im.src = src;
        mark.appendChild(im);
      } else mark.textContent = on.getAttribute('data-mark');
      mark.style.borderColor = mark.style.color = on.getAttribute('data-color');
    } else {
      var all = document.createElement('span');
      all.className = 'gamepick__img gamepick__img--all';
      mark.appendChild(all);
      mark.style.borderColor = mark.style.color = '';
    }
  }
  function set(o) { el.classList.toggle('gamepick--open', o); mark.setAttribute('aria-expanded', String(o)); }
  paint();
  mark.addEventListener('click', function () { set(!el.classList.contains('gamepick--open')); });
  [].forEach.call(opts, function (o) {
    o.addEventListener('click', function () {
      try {
        localStorage.setItem('cs.game', o.getAttribute('data-game'));
        localStorage.setItem('cs.pick', '1');
      } catch (e) {}
      paint(); set(false);
      if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
    });
  });
  document.addEventListener('click', function (e) { if (!el.contains(e.target)) set(false); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') set(false); });
})();
// An article's game advert (ArticleNext.tsx, GameMovers): the four cards of
// its game we expect to move most next month, filled in from the same list
// the game's home page shows. Stays empty (and hidden) if the list fails.
(function () {
  var box = document.querySelector('.article-browse__cards[data-game]');
  if (!box) return;
  var game = box.getAttribute('data-game'), label = box.getAttribute('data-label');
  function money(n) { return '$' + n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
  function span(cls, text) { var s = document.createElement('span'); s.className = cls; s.textContent = text; return s; }
  try {
    fetch('/api/cards/movers?count=24&horizon=1m&trend=6m&game=' + encodeURIComponent(game))
      .then(function (r) { return r.ok ? r.json() : []; })
      .then(function (cards) {
        cards = (cards || []).filter(function (c) { return c.price && c.fcstTo != null && c.pictureUrl; }).slice(0, 4);
        if (cards.length < 2) return;
        box.appendChild(span('article-browse__cards-label', 'The ' + label + ' cards we expect to move most next month'));
        cards.forEach(function (c) {
          var a = document.createElement('a'), img = document.createElement('img');
          a.className = 'article-browse__card'; a.href = '/catalog/' + game + '/' + c.id;
          img.className = 'article-browse__art'; img.src = c.pictureUrl; img.alt = ''; img.loading = 'lazy';
          a.appendChild(img);
          a.appendChild(span('article-browse__card-name', c.name));
          var pct = (c.fcstTo / c.price - 1) * 100, flat = Math.abs(pct) < 0.5;
          var price = span('article-browse__card-price', money(c.price) + ' ');
          price.appendChild(span('article-browse__move' + (flat ? '' : pct > 0 ? ' article-browse__move--up' : ' article-browse__move--down'),
            (flat ? '0' : (pct > 0 ? '+' : '') + pct.toFixed(0)) + '%'));
          a.appendChild(price);
          box.appendChild(a);
        });
      }).catch(function () {});
  } catch (e) {}
})();
"""

# ---- front-page call to action --------------------------------------------
# 2026-11-09, user: "add a CTA on the articles/report to go to the front page."
# Closes every report and article, above the footer. The button is the
# site's primary yellow pill; the copy is the collector voice.
CTA_CSS = """
.home-cta{display:flex;align-items:center;justify-content:space-between;gap:20px;flex-wrap:wrap;
margin:36px 0;padding:22px 24px;border:1px solid var(--border);border-radius:14px;
background:linear-gradient(180deg,#1a2333,#141c2e)}
.home-cta__text{flex:1 1 320px;min-width:0}
.home-cta__kicker{font-family:ui-monospace,Menlo,monospace;font-size:12px;letter-spacing:.08em;
text-transform:uppercase;color:#ffcb05;margin:0 0 6px}
.home-cta__title{font-size:21px;line-height:1.25;font-weight:800;margin:0 0 6px;color:var(--text)}
.home-cta__copy{margin:0;color:var(--text-muted);font-size:14.5px}
.home-cta__btn{display:inline-flex;align-items:center;justify-content:center;height:42px;
padding:0 22px;border-radius:999px;background:#ffcb05;color:#101623;font-weight:700;font-size:15px;
white-space:nowrap;text-decoration:none;transition:filter .15s ease}
.home-cta__btn:hover{text-decoration:none;filter:brightness(1.08)}
@media (max-width:540px){.home-cta{padding:18px}.home-cta__btn{width:100%}}
"""


def cta_html(site=SITE):
    return f"""<aside class="home-cta">
  <div class="home-cta__text">
    <p class="home-cta__kicker">Today on CardStock</p>
    <p class="home-cta__title">See the cards moving right now</p>
    <p class="home-cta__copy">The front page shows the biggest moves across every game we track, the cards our model expects to move next, and a live price crawl.</p>
  </div>
  <a class="home-cta__btn" href="{site}/">Go to the front page</a>
</aside>"""


# ---- an article's next steps ----------------------------------------------
# 2026-11-13: thirteen readers came from Reddit to the One Piece Heroines
# article in a day and every one left without opening a card, the catalog or
# the home page. The page never asked them to. Two blocks do now: after the
# opening section, every card the piece is about in the catalog; after the
# piece, those cards again, a free portfolio for the cards you own, and more
# articles on the game. The app's article page shows the same two blocks
# (ArticleNext.tsx) and THIS is its stylesheet, read when a page is rendered
# like NavBar.css above, with the tokens it reads scoped to the two blocks
# (the page's own --brand is its link blue; the blocks use the site yellow).
ARTICLE_NEXT_CSS = os.path.join(HERE, "..", "client", "src", "styles", "features", "ArticleNext.css")

NEXT_TOKENS = """
.article-browse,.article-next{--brand:#ffcb05;--brand-border:#e0b000;--on-brand:#101623;
--surface:#1a2333;--surface-tint:#141c2e;--link:#7fb0ea;
--display:"Big Shoulders Display",Impact,"Avenir Next Condensed","Arial Narrow",sans-serif;
--mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
--space-5:5px;--space-10:10px;--space-15:15px;--space-20:20px;--space-30:30px;--space-40:40px;
--up:#3fd98a;--down:#ff7a7a}
"""


def next_css():
    try:
        with open(ARTICLE_NEXT_CSS, encoding="utf-8") as f:
            return f.read() + NEXT_TOKENS
    except OSError:
        # The blocks still work unstyled: plain text and links.
        print(f"static_nav: {ARTICLE_NEXT_CSS} not found, next steps unstyled", flush=True)
        return ""


def _esc(s):
    return html.escape(str(s), quote=True)


def _cards(browse, cls):
    """The headline's subject, the number in the brand yellow: 'all 98
    cards in this set', 'all 2,185 Gundam cards'; 'every card' when the
    count is unknown."""
    n, game = browse.get("count"), "" if browse.get("set") else f"{_esc(browse['label'])} "
    where = " in this set" if browse.get("set") else ""
    if n and n > 1:
        return f'all <span class="{cls}">{n:,} {game}cards</span>{where}'
    return f"every {game}card{where}"


def _browse_copy(browse):
    if browse.get("set"):
        return (f"Today's price, the price history and our forecast for every card in "
                f"{_esc(browse['set'])}.")
    return (f"Today's price, the price history and our forecast for every "
            f"{_esc(browse['label'])} card we track.")


def browse_html(browse, site=SITE):
    """The block after an article's opening section. `browse` names the cards
    the piece is about (article_pages.browse_for): href (site-relative, into
    the catalog), set (None for the whole game), label (the game's name) and
    count (None when the card DB could not say)."""
    kicker = "This set on CardStock" if browse.get("set") else f"{_esc(browse['label'])} on CardStock"
    button = "Browse the set" if browse.get("set") else "Browse the cards"
    game = browse.get("game") or ""
    tint = f' style="--game:{GAME_COLOR[game]}"' if game in GAME_COLOR else ""
    advert = (f'\n  <div class="article-browse__cards" data-game="{_esc(game)}" '
              f'data-label="{_esc(browse["label"])}"></div>') if game else ""
    return f"""<aside class="article-browse" aria-label="Every card in this piece"{tint}>
  <div class="article-browse__text">
    <span class="article-browse__kicker">{kicker}</span>
    <span class="article-browse__title">See {_cards(browse, "article-browse__count")} with our forecasts</span>
    <span class="article-browse__copy">{_browse_copy(browse)}</span>
  </div>
  <a class="article-browse__btn" href="{site}{_esc(browse['href'])}">{button}</a>{advert}
</aside>"""


def next_html(browse, more, more_title, all_href, all_text, site=SITE):
    """The block after the article: (1) the cards again, (2) a free
    portfolio (the nav script points it at the portfolio for a member), and
    more articles. `more` is [(slug, title, meta)], newest first; an empty
    list leaves that part out."""
    go = "Browse the set" if browse.get("set") else "Browse the cards"
    title = _cards(browse, "article-next__count")
    items = "".join(
        f'<li><a class="article-next__item" href="{site}/articles/{_esc(slug)}">'
        f'<span class="article-next__item-title">{_esc(t)}</span>'
        f'<span class="article-next__item-meta">{_esc(meta)}</span></a></li>'
        for slug, t, meta in more)
    more_html = f"""
  <div class="article-next__more">
    <div class="article-next__more-head">
      <h3 class="article-next__more-title">{_esc(more_title)}</h3>
      <a class="article-next__all" href="{site}{_esc(all_href)}">{_esc(all_text)} &rarr;</a>
    </div>
    <ul class="article-next__list">{items}</ul>
  </div>""" if more else ""
    return f"""<section class="article-next" aria-labelledby="article-next-title">
  <span class="article-next__kicker">Keep going</span>
  <h2 class="article-next__title" id="article-next-title">Where to next</h2>
  <div class="article-next__steps">
    <a class="article-next__step" href="{site}{_esc(browse['href'])}">
      <span class="article-next__num">1</span>
      <span class="article-next__step-title">See {title}</span>
      <span class="article-next__step-copy">{_browse_copy(browse)}</span>
      <span class="article-next__go">{go} &rarr;</span>
    </a>
    <a class="article-next__step" href="{site}/register" data-signed-in-href="{site}/portfolio">
      <span class="article-next__num">2</span>
      <span class="article-next__step-title">Track the cards you own</span>
      <span class="article-next__step-copy">Add them to a free portfolio and see what your collection is worth today, with our forecast beside every card.</span>
      <span class="article-next__go" data-signed-in-text="Open your portfolio &rarr;">Start a free portfolio &rarr;</span>
    </a>
  </div>{more_html}
</section>"""
