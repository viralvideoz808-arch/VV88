# CAR-LOS Rentals

This one program runs the whole business:

| | What it is | Where |
|---|---|---|
| **Website** | The public site for car rentals and used-car sales. It has search, live prices, booking requests, sell/trade-in and financing forms, FAQ and policies, and a chat assistant. | `http://localhost:8080` |
| **Console** | The private operations console. It runs the agents, and it's where you approve messages, work the inbox, confirm bookings, manage inventory and photos, and change settings. | `http://localhost:8081` |
| **Agents** | Six agents that find customers on Threads, Instagram and Facebook, message them, and handle the conversations. | run in the background |

```
 Lead Scout ──► Lead Qualifier ──► Outreach Writer ──► [you approve] ──► send
 (Threads search,   (rent / buy / sell,   (short, helpful first                │
  comments on our    score 0–100)          reply with a tracked link)          ▼
  IG/FB posts,                                                     Conversation Concierge
  IG hashtags,                                                     (website chat, IG + Messenger DMs,
  Meta webhooks)                                                    Threads replies: prices, availability,
                                                                    booking requests, test drives, trade-ins,
                                                                    hand-off to you)
 Follow-up & Reminders: pickup reminders, thank-yous, one follow-up per web inquiry, morning digest
 Fleet & Website Manager: pickups and returns, stale requests, listings that need fixing,
                          featured cars by demand, daily "car of the day" post (needs your approval)
```

## Start it (5 minutes)

```bash
cd carlos-rentals
python3 -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e .
cp .env.example .env                               # then fill in the business details at the top
carlos run
```

The first run prints the console password, and also saves it in `.env` as `CARLOS_OPS_PASSWORD`. Open the
website on port 8080 and the console on port 8081.

**Demo mode.** With no social accounts connected, the system runs safely on its own:

- The Scout reads a built-in sample feed.
- Every "send" goes to a sandbox, so nothing reaches real people.
- A few sample customers write back, so you can watch the Concierge answer them.

Use demo mode to learn the console. Then connect the real accounts (see below).

**Sample cars.** An empty database gets 15 sample cars (10 rentals and 5 for sale), so the site isn't
blank. Each one shows a "Sample listing" badge on the website. Replace them with the real fleet: open
**Inventory**, then edit each car's details and photos. Saving a car removes its badge.

Other commands:

```bash
carlos run site            # website only
carlos run ops             # console + agents only
carlos agents              # run every agent once, from the terminal
carlos agents scout        # run one agent
pytest                     # 46 tests (pip install -e ".[dev]")
```

Docker: `cp .env.example .env && docker compose up -d`. Data lives in `./data`. The console port is
published on 127.0.0.1 only.

## Your daily routine in the console

1. **Dashboard.** Check what needs you: messages to approve, conversations handed to you, and bookings to confirm.
2. **Approvals.** Read each first message the agents drafted, next to the post that prompted it. Edit it, send it, or reject it.
3. **Inbox.** Every conversation is in one place. **Take over** pauses the assistant for that customer. **Draft with assistant** writes a suggested reply for you to edit.
4. **Bookings.** Confirm or cancel requests. The customer is emailed automatically once SMTP is set up. Mark cars as picked up or returned (the Fleet agent also does this by date).
5. **Inventory.** Add cars, upload photos, mark cars sold or in maintenance. Changes show on the site immediately.

## What the agents will and won't do, and why

Your friend's business has to keep these accounts. Meta bans Pages and Instagram accounts that scrape
profiles or send cold DMs, and US rules on automated messages are strict. So the agents only use
Meta's official APIs, and they follow these rules:

- **No cold DMs.** First contact is either a public reply to a public post (Threads), or the single
  private reply Meta allows to someone who commented on *your* post (Instagram and Facebook, within 7 days).
- **DMs only inside Meta's 24-hour window** after the customer's last message.
- **No scraping** of profiles, Facebook groups or Marketplace. Meta offers no API for these. If you
  see a good lead there, paste it into **Leads → Add a lead by hand**. It gets scored and you get a
  suggested reply to send yourself.
- **Opt-outs are permanent.** "STOP", "unsubscribe" or "leave me alone" blocks that handle on that platform.
- **Daily caps and quiet hours** for anything the business starts (set them in Settings).
- **One outreach per lead,** and you approve each one by default.
- The assistant **says it's a virtual assistant** in its first reply. California's B.O.T. Act and
  similar laws require this.
- **Booking-related actions happen on the website or by email.** Chat and DM bookings are *requests*.
  You confirm them, and payment, ID and insurance are handled at pickup. The site never collects
  card numbers or SSNs.

## Making it smarter: Claude

Add `ANTHROPIC_API_KEY` to `.env` and restart. The default model is `claude-opus-5-5` (change it with
`CARLOS_MODEL`). With Claude:

- the Qualifier reads posts the way a person would;
- the Outreach Writer writes a natural first reply about the specific car;
- the Concierge can look up the fleet, quote exact dates, file booking requests and test-drive or
  trade-in inquiries **inside the chat**, and hand off to you when it should.

Requests use server-side refusal fallbacks (`fallbacks: "default"`). If Claude is ever unavailable,
every agent falls back to its built-in rules.

## Going live

1. **Hosting.** Any small VPS or container host works (Render, Railway, Fly, DigitalOcean).
   - Point a domain at port 8080 behind HTTPS and set `CARLOS_SITE_URL=https://yourdomain.com`.
   - Keep the console private: use a separate subdomain behind HTTPS, set `CARLOS_HTTPS_COOKIES=true`, or reach it over SSH/VPN.
2. **Accounts.** Connect them by following [docs/CONNECT.md](docs/CONNECT.md): Meta app, Facebook Page, Instagram, Threads, webhooks and email.
3. **Legal pages.** Have the rental terms and privacy policy reviewed for the state you operate in. Both pages say so.
4. **Set the switches.** In **Settings**, set the search phrases, the areas you serve, prices, tax and rental rules. Keep approval on for the first couple of weeks.

## Project layout

```
carlos/
  agents/        scout, qualifier, outreach, concierge, followup, fleet + scheduler
  channels/      meta.py (FB Page + Instagram), threads.py, email.py, demo.py (sandbox)
  compliance.py  every outgoing message passes these checks
  messaging.py   conversations, outbox, dispatcher
  inventory.py   cars, quotes, availability, bookings, inquiries
  web/site.py    public website + chat API + Meta webhooks
  web/ops.py     operations console
  web/templates, web/static
tests/           46 tests: pricing, compliance, agents (incl. Claude tool use), web, webhooks
```
