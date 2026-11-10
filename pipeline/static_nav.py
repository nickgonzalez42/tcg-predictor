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
article (same request).
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
NAVBAR_CSS = os.path.join(HERE, "..", "client", "src", "styles", "components", "NavBar.css")
SITE = "https://cardstock.guide"

# The tokens NavBar.css reads, copied from client/src/styles/tokens.css and
# scoped to the header so the page's own --brand (link blue) is untouched.
TOKENS = """
:root{--nav-bar-h:64px;--page-top:30px}
@media (max-width:1023px){:root{--nav-bar-h:56px;--page-top:calc(var(--nav-bar-h) + 2px + 20px)}}
.navbar,.navbar__scrim{--brand:#ffcb05;--on-brand:#101623;--border:#2e3a52;--nav-link:#dbe4f0;
--nav-glass:rgba(12,16,26,.55);--radius:15px;--space-5:5px;--space-10:10px;--space-15:15px;
--space-20:20px;--space-25:25px;--space-95:95px;--control-h:38px;--sidebar-w:300px;--logo-top:30px;
--container-max:1280px;--container-pad:40px;
--content-left:calc(max(0px,(100vw - var(--container-max)) / 2) + var(--container-pad));
--logo-h:40px;--logo-w:calc(var(--logo-h) * 63 / 88);
--tipped-logo-left:calc(var(--content-left) / 2 + var(--logo-w) / 2);
--nav-w:max(var(--sidebar-w),calc(var(--tipped-logo-left) + 226px));--ticker-h:40px}
@media (max-width:1023px){.navbar,.navbar__scrim{--radius:24px;--container-pad:24px}}
@media (max-width:767px){.navbar,.navbar__scrim{--radius:26px;--container-pad:20px}}
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
            return f.read() + TOKENS
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
</header>"""


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
margin:44px 0 0;padding:22px 24px;border:1px solid var(--border);border-radius:14px;
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
