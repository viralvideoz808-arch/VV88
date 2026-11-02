# MKB — Moses Bright Kang'ara

**Build. Trade. Scale.** This folder holds the brand identity and website for MKB: IT services, the trading desk, the academy, packages, a partnership/proposal panel and a private admin dashboard.

**Brand:** the **Candle M** logo with the **Trading Terminal** theme (black, dark green and electric jungle green; Space Grotesk + JetBrains Mono; square edges). The other options considered are in `brand-options/`.

```
mkb/
├── public/                 ← the website (works on any host)
│   ├── index.html          Home: the 3D MKB Bull hero, pillars, work, packages, academy, Q&A
│   ├── about.html          Story, values, journey, skills (managed in admin)
│   ├── services.html       IT services, process, live cost estimator, FAQ
│   ├── work.html           Portfolio + case studies
│   ├── trading.html        Forex · Binary · Crypto desk: live prices, TradingView chart, session clock,
│   │                       6 calculators, creations, lessons, economic calendar
│   ├── academy.html        Courses with curriculum, enrolment and waitlists
│   ├── packages.html       Build / Trade / Grow packages in KES or USD, checkout with reference codes
│   ├── connect.html        Ask anything · propose a partnership · pitch an idea · hire me → straight to your DMs
│   ├── brand.html          Brand book: logo, colours, type, mascot, voice, bios, downloadable kit
│   ├── admin/              Private admin dashboard
│   ├── content/content.json  All site content (skills, services, prices, courses, lessons…)
│   └── assets/             CSS, JS, logos (brand/), social kit (kit/), icons (img/)
├── server/server.js        Node server: site + API + admin login (no dependencies)
├── tests/                  `npm test`
└── tools/                  Regenerate logos (gen_brand.py) and the social kit (kit.html + export-kit.js)
```

## What visitors can do

| | |
|---|---|
| **Message you on any platform** | Every "contact" button opens your DMs with the visitor's message already written: WhatsApp (0710 910 301), X DM (@mosesbright888), email, SMS and calls. Instagram and Threads don't allow pre-filled DMs, so the site copies the message and opens your profile/inbox for them to paste. The floating chat button is on every page. |
| **Send proposals** | Connect page: a 4-step panel for questions, partnership offers, business ideas and project enquiries. Each gets a reference code (e.g. `MKB-7Q2K9P`) and opens the chosen app. With the server running, a copy lands in your admin inbox and visitors can track the status. |
| **Buy packages & courses** | Each order gets a reference. Visitors see your M-Pesa Till/Paybill (set it in Admin → Settings) and confirm in your DMs. Add a Paystack public key to show a "Pay with M-Pesa or card" button. |
| **Use your trading tools** | Position size (live FX conversion), risk:reward, binary expectancy with a Monte Carlo simulation, compounding, drawdown recovery, crypto averaging, a session clock in Nairobi time, a downloadable journal and a pre-trade checklist. |
| **Estimate a project** | Services page: pick type, pages and features to get a price range in KES/USD, then send it to you. |
| **Verify it's really you** | A "Verify it's really me" panel lists your official accounts and warns visitors about impersonators. |

## Run it on your computer

Needs Node.js 18 or newer.

```bash
cd mkb
MKB_ADMIN_PASSWORD='choose-a-long-password' npm start
# open http://localhost:8080 and http://localhost:8080/admin/
npm test        # server tests
```

## Put it online

### Option A: live admin + inbox (recommended)
Use the Node server so your edits go live instantly and proposals are stored.

**Render** (about $7/month with a 1 GB disk):
1. Render → **New → Blueprint** → choose this repository and branch.
2. Set **Blueprint file path** to `mkb/render.yaml`.
3. Enter `MKB_ADMIN_PASSWORD` (12+ characters).
4. Add your domain under the service's **Settings → Custom domains**.

**Any VPS or Docker host:**
```bash
docker build -t mkb ./mkb
docker run -d -p 80:8080 -e MKB_ADMIN_PASSWORD='…' -v mkb-data:/data --restart unless-stopped mkb
```
Put it behind HTTPS (Caddy, Nginx or Cloudflare). Data (content, inbox, uploads, password) lives in the `/data` volume.

| Variable | Purpose |
|---|---|
| `MKB_ADMIN_PASSWORD` | Admin password. You can change it later in Admin → Backup & security. |
| `MKB_DATA_DIR` | Where data is stored (default `mkb/data`, `/data` in Docker). |
| `MKB_NOTIFY_WEBHOOK` | Optional: Slack/Discord/Zapier/Make webhook called for every new proposal or order. |
| `MKB_TELEGRAM_BOT_TOKEN` + `MKB_TELEGRAM_CHAT_ID` | Optional: get a Telegram message for every new proposal or order. |
| `MKB_TRUST_PROXY=1` | Set when behind a reverse proxy so rate limits see real visitor IPs (automatic on Render). |

### Option B: free static hosting (Vercel, Netlify, GitHub Pages, cPanel)
Upload the **`public/`** folder. Everything works except the server-only parts: visitors still reach you directly in your DMs, but there is no inbox or live tracking. To edit content, open `/admin/`, make changes (they're saved in your browser), then **Backup → Export content.json** and replace `public/content/content.json` on the host. On Vercel, set the project's root directory to `mkb/public`.

To also keep a copy of every form on static hosting, paste a Formspree or Web3Forms endpoint into Admin → Settings → Forms.

## Using the admin (`/admin/`)

- **Skills:** add every new skill as you grow. It shows on the About page with a "New" badge for 60 days and joins the homepage marquee and stats.
- **IT services / Projects:** your offer and portfolio. "Featured" projects appear on the homepage; upload cover images or let the site draw animated covers.
- **Packages / Courses:** prices in KES (USD is converted with the rate in Settings). Courses can be *enrolling*, *coming soon* (waitlist) or *closed*, with a full curriculum editor.
- **Trading:** edit your Forex/Binary/Crypto approach, add creations (indicators, bots, templates) and write lessons (`## Heading`, `- bullet`, `**bold**`).
- **Inbox:** every proposal, question, order and waitlist entry. Reply on WhatsApp or email in one tap, set a status, and leave a note the visitor sees when tracking. **Publish as Q&A** turns a good question into a public answer.
- **Settings:** availability, announcement bar, contact handles, M-Pesa details, Paystack key, USD rate, crypto watchlist, chart symbols, your photo.
- **Backup & security:** export/import everything, download the inbox as CSV, change the password.

## Review before launch

The site ships with real details (your contacts, your existing projects VV88 and PumpScan) and sensible starting content you should confirm or change in the admin:

- **Prices** for every service, package and course: they're placeholders based on common Kenyan market rates.
- **About:** the story and the "journey" chapters are written in general terms; add your real dates and milestones and, if you like, a photo.
- **Skills and levels:** adjust to match you.
- **Courses:** delivery format, schedule and the FAQ answers (certificates, payment upfront).
- **Q&A:** the default answers say you don't trade or manage money for clients. Keep that if it's true; it protects you and your audience.
- **Payments:** add your M-Pesa Till/Paybill. Until then, orders ask buyers to confirm on WhatsApp first.
- **Testimonials:** empty on purpose. Add only real ones; the section appears once one is published.

## Brand kit

- Logos: `public/assets/brand/` (SVG: Candle M badge mark, horizontal and stacked lockups, light/dark/one-colour versions, favicon).
- Social and print: `public/assets/kit/` (profile picture 1080², X header 1500×500, Instagram post 1080×1350, story/status 1080×1920, business card front and back, transparent PNG logos) and `public/assets/img/og-image.png` for link previews.
- Guidelines: open `/brand.html`, or read [BRAND.md](BRAND.md).
- Regenerate the logos: `python tools/gen_brand.py --grotesk SpaceGrotesk-700.ttf --mono JetBrainsMono-700.ttf` (Google Fonts TTFs and `pip install fonttools`).
- Regenerate the social kit: serve `mkb/` (`python3 -m http.server 8799`) and run `node tools/export-kit.js` (needs Playwright).

## Security notes

- Admin uses a scrypt-hashed password, signed session tokens (12 h) and login rate limiting. Changing the password signs out every other session.
- Forms are rate limited, size limited and have a bot honeypot. Uploads accept only real PNG/JPG/WebP/GIF files up to 5 MB.
- Responses carry a Content Security Policy and other security headers. Everything visitors type is escaped before it's shown.
- Tracking a reference only reveals its status and your public note, never names or contact details.
