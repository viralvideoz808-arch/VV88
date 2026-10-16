# VV88 — automated clipping engine

VV88 picks the clipping campaign that should pay the most per clip right now, downloads the
footage that campaign authorises, finds the moments most likely to hold a cold audience,
edits them into vertical clips in the current viral styles, and posts them to TikTok,
YouTube Shorts, Instagram Reels and X on a schedule. It then reads back the view counts and
shifts toward whatever is winning on your accounts this week.

```
 discover ──► rank ──► fetch ──► transcribe ──► find highlights ──► render ──► schedule ──► publish
 (Whop /      ($/clip =   (yt-dlp,   (Whisper,     (hook, energy,      (FFmpeg,    (best slots,   (Postiz or
  your list)   payout ×    rights-    local, free)  pace, payoff,       local,      caps, gaps)    official APIs)
               traffic ×   checked)                 length, brand)      free)                          │
               your data)                                                                               ▼
     ▲                                                                                          collect views
     └───────────────────── optimizer: style / length / hook type, 7-day half-life ◄────────────────┘
```

## What the request asked for, and how VV88 answers it

**"Find the highest-traffic niche in clipper rewards."**
There's no fixed answer: it changes week to week, so VV88 works it out on every cycle.
Each campaign is scored as

`expected $ per clip = reward per 1K views × expected views on each platform it pays for × budget left`

Expected views start from a prior. That prior is scaled by a live niche signal: the median views per hour of Shorts published in that niche over the last 7 days (YouTube Data API, free key). As your own posts collect views, the prior shifts toward your actual numbers. `vv88 rank` prints the niche leaderboard and the top campaigns.
Campaigns come from Whop Content Rewards (through an Apify actor, because Whop publishes no listing API) and from your own `campaigns.yaml` for any other platform or direct deal.

**"Clip in the most viral TikTok style in the world this week."**
Four style presets, built on 2026 short-form data:
- About 80% of viral-tier clips have burned-in captions.
- About 79% use animated captions.
- Word-by-word timing is now the norm.
- The viral-tier median length is about 41 s.

| style | look |
|---|---|
| `karaoke_pop` | bold uppercase, 2–3 words at a time, yellow active word that pops |
| `dynamic_minimal` | word-by-word but restrained: white text, unspoken words dimmed |
| `soft_lowercase` | single-line lowercase, soft shadow ("quiet luxury" niches) |
| `brainrot_stack` | speaker on top, gameplay underneath, 3-colour kinetic captions |

Every clip also gets:
- a 9:16 frame that follows the speaker's face, falling back to a blurred fill
- jump cuts over dead air, with alternating punch-in zoom to hide the cuts
- a hook banner for the first ~2.6 s
- a progress bar
- audio at −14 LUFS

"This week" is handled by the optimizer. Thompson sampling A/B-tests style, clip length and hook type. Results lose half their weight every 7 days, so traffic moves to whatever is winning on your accounts now.

**"The free editing tool that can edit the most videos a day."**
FFmpeg with libass, plus faster-whisper, both running on your own machine. There are no credits, no watermark, no daily cap and no fee. The only limit is CPU. Measured with `vv88 benchmark` on a 4-vCPU cloud container:

> a 26.5 s 1080×1920 clip renders in 26.1 s (≈1× realtime) → **~3,300 clips/day, $0**

Whisper transcription adds roughly 0.1–0.3× the source length per source video on CPU, and runs much faster on a GPU. Hosted clipping tools typically limit free use with credits, minutes or watermarks. So the editor is not your bottleneck; the platforms' posting limits are (below).

**"Post to the attached TikTok, YouTube, IG and X pages automatically."**
There are two backends:
- `postiz` (recommended). [Postiz](https://github.com/gitroomhq/postiz-app) is open source, so you can self-host it for free. It handles OAuth and app review for all four platforms behind one API key.
- `direct`. The official APIs: YouTube Data API v3, Instagram Graph API (Reels), TikTok Content Posting API (Direct Post) and X API v2.

The default is `dry_run`. It writes each video plus its post copy to `data/outbox/<platform>/`, so you can see exactly what would be posted before connecting anything.

## Real-world limits you should know before scaling

| platform | what limits volume |
|---|---|
| TikTok | Until your developer app passes TikTok's audit, API posts are **forced to private** and only private accounts can be posted to. TikTok also enforces an unpublished per-user daily cap (`spam_risk_too_many_posts`). Postiz's audited app avoids the first problem. |
| Instagram | Meta caps API publishing per account per rolling 24 h (its docs say 100; one reference page still says 50). VV88 reads the live quota before posting. |
| YouTube | 10,000 quota units/day per Google Cloud project by default. Google's revision history says an upload now costs ~100 units (it was 1,600). |
| X | New developers have no free write tier; posting is pay-per-use, about a cent or two per post. The X daily cap is effectively a cost cap. |

Posting faster than an account can absorb splits its reach and looks like spam. VV88 enforces a per-platform daily cap, a minimum gap between posts, and ±7 min jitter. It also stops rendering when the posting queues are two days deep.

## The control website

```bash
pip install -e ".[all]"
vv88 web                 # http://127.0.0.1:8088. The first run prints the password and saves it to .env
```

Everything runs from the browser:

| Page | What you do there |
|---|---|
| **Dashboard** | KPIs, the autopilot switch, one-click full cycle / discover / publish / pull metrics |
| **Campaigns** | campaigns ranked by expected $ per clip, the niche leaderboard, add or archive campaigns |
| **Clip studio** | paste a URL or upload footage; pick campaign, clip count and style |
| **Clips** | watch every rendered clip, see where it's posted, schedule it |
| **Queue** | scheduled, posted and failed posts; publish now, cancel or retry |
| **Performance** | the winning style / length / hook this week, views, earnings, payout links |
| **Brand** | profile picture, banners and bios with character counts; approve, then apply |
| **Connections** | one-click account linking (OAuth), API keys, developer-app credentials |
| **Settings** | posting caps and slots, clip lengths, styles, AI re-ranking, system check, benchmark |
| **Jobs** | every background job, with live logs |

Long work (downloading, rendering, publishing) runs in a background worker, so the site stays responsive. Autopilot runs the full cycle on a timer and publishes posts as their slots arrive.

Security:
- Password login, locked for 10 minutes after 5 failed attempts.
- CSRF tokens on every form, and a strict Content-Security-Policy.
- Media is served only from the clips, outbox and brand folders.
- Secrets are never shown in the UI.
- By default the site listens only on this computer. To reach it from elsewhere, put it behind an HTTPS reverse proxy and pass `vv88 web --host 0.0.0.0 --https-cookies`. Set `VV88_WEB_BASE_URL` to the public https address so the OAuth redirects match.
- Behind a reverse proxy on the same machine, make the proxy *replace* `X-Forwarded-For` with the real client address (nginx: `proxy_set_header X-Forwarded-For $remote_addr;`) so the per-address sign-in limit works. Sign-in is also capped at 50 wrong passwords per 10 minutes across all addresses, except for browsers that have signed in before (they're remembered with a signed cookie), so others' failed guesses can't lock you out. Use a long random `VV88_WEB_PASSWORD` (the generated one is). Changing the password signs out every browser.

For account linking from the website, register `<site address>/oauth/callback` as the redirect URI in each developer app.

## Connect your accounts once (they stay linked)

Run these **on your own computer**, in the browser where you're signed in, from the repo folder:

```bash
vv88 connect status      # what's linked
vv88 connect all         # walks through every tool and account below
```

| target | how it links | stays linked because |
|---|---|---|
| `postiz` | paste API key → checked live → your Postiz channel IDs are saved automatically | Postiz keeps each platform's tokens fresh for you (simplest option) |
| `youtube` | Google sign-in page (PKCE, local `127.0.0.1` callback) | Google refresh tokens don't expire, **once your Google Cloud OAuth consent screen is set to "In production"**. In "Testing" Google expires them after 7 days. |
| `tiktok` | TikTok Login Kit for Desktop (PKCE) | refresh token rotates; the newest is always saved |
| `x` | X OAuth 2.0 + `offline.access` | refresh token rotates; the newest is always saved |
| `instagram` | Facebook Login for Business → your Instagram professional account | ~60-day token; `vv88 doctor` warns 10 days early and reconnecting is one click |
| `apify`, `youtube-key`, `anthropic` | paste key (hidden input) → checked live | API keys don't expire until you revoke them |

VV88 never sees or stores your passwords. It keeps only revocable tokens, in `.env` and `data/vv88.db`; both files are private to your user (permissions 600).

Register `http://127.0.0.1:8788/callback/` as the redirect URI in each developer app (Facebook: `http://localhost:8788/callback/`). If an app accepts only https redirects, register any https URL you control and run `vv88 connect <platform> --paste --redirect <that url>`. After you approve, copy the address-bar URL back into the terminal.

Secrets are masked in all logs and error messages.

Setting the profiles and linking from your own logged-in browser: see `docs/LAPTOP_SETUP.md`.

## Brand kit: one look across all four accounts

A brand kit is a folder: `brand.json` plus images. It holds one profile picture, names, bios sized to each platform's limit, a YouTube banner and an X header.

```bash
vv88 brand show                           # every image path, name, full bio + char counts, and a fingerprint
vv88 brand approve --fingerprint <fp>     # you approve exactly what `show` printed
vv88 brand apply                          # writes the checklist, then applies what the APIs allow
```

Approval names the fingerprint that `brand show` printed. Any change to the images or text produces a new fingerprint, and that needs a fresh approval. What gets applied automatically:
- **YouTube:** channel description and keywords, via the API. Every other channel setting is preserved.
- **TikTok, Instagram and X:** their APIs don't allow profile editing, and the API can't change YouTube's channel name or profile picture either. `brand apply` writes `APPLY_CHECKLIST.md` with the exact text to paste and the image to upload on each platform.

## Guardrails (on by default)

- **Rights.** Only footage that a campaign lists as source material, or that matches your `rights_allowlist`, is downloaded. Clipping creators who haven't opted in gets your channels struck and your submissions rejected.
- **Disclosure.** Paid clipping is paid promotion, so VV88:
  - adds `#ad`
  - sets TikTok's `brand_content_toggle`
  - sets YouTube's paid-product-placement flag

  Turn this off only if a campaign tells you it isn't sponsored.
- **No fake engagement.** VV88 grows views only through better clips, better timing and more surface area. It contains no bots, no view buying and no account farms; those get accounts banned and campaign payouts clawed back.

## Quick start

```bash
pip install -e ".[all]"          # ffmpeg must be installed separately (apt/brew/choco)
cp config.example.yaml config.yaml
cp campaigns.example.yaml campaigns.yaml   # add the campaigns you've joined
cp .env.example .env                       # add keys for what you use
vv88 doctor                                # checks tools, fonts, credentials

vv88 rank                                  # best campaigns + niche leaderboard
vv88 clip <url-or-file> --campaign <id> -n 5         # render clips (dry run by default)
vv88 clip ./my-own-video.mp4 -n 3 --post             # your own footage, straight to the queue
vv88 run                                   # the full loop, every 30 min
vv88 report                                # which style/length/hook is winning + $ estimate
vv88 links                                 # posted URLs per campaign, to submit for payout
```

Run it 24/7 with Docker (`docker build -t vv88 . && docker run -p 127.0.0.1:8088:8088 -v $PWD:/work vv88`, which starts the control website; turn on Autopilot there), with cron (`*/30 * * * * cd /path && vv88 run --once`), or as a systemd service.

On an Android phone, run it in Termux: `bash scripts/termux_setup.sh`, then `bash scripts/termux_start.sh` (full steps in [docs/TERMUX.md](docs/TERMUX.md)). Phones can't install faster-whisper, so captions are transcribed with whisper.cpp instead. VV88 switches to it automatically when faster-whisper is missing.

### Optional: Claude as editor-in-chief

Set `llm.enabled: true` and `ANTHROPIC_API_KEY`. The heuristic shortlist (about 15 windows of transcript text, not the video) goes to Claude. Claude picks the best clips and writes the hook text, title, caption and niche hashtags. This costs cents per source video; everything else stays free.

## Commands

| command | does |
|---|---|
| `doctor` | checks FFmpeg/libass, Whisper, OpenCV, yt-dlp, fonts, credentials |
| `discover` | pulls campaigns from Whop (Apify) and `campaigns.yaml` |
| `rank` | expected $ per clip per campaign + niche leaderboard |
| `clip SOURCE` | clips one URL/file (`--campaign`, `-n`, `--style`, `--post`, `--i-have-rights`) |
| `run [--once]` | discover → rank → clip → schedule → publish → metrics, on a loop |
| `publish` | publishes posts whose slot has arrived |
| `metrics` | pulls views for recent posts (needs read access on the direct APIs) |
| `report` | optimizer results + estimated earnings from measured views |
| `links` | posted URLs grouped by campaign |
| `connect [target]` | link accounts/tools once (`status`, `all`, `youtube`, `tiktok`, `instagram`, `x`, `postiz`, `apify`, `youtube-key`, `anthropic`) |
| `brand show/approve/apply` | profile picture + bios kit with an approval gate |
| `styles` | lists caption/layout presets |
| `benchmark` | measures clips/day on this machine |

## Layout

```
vv88/
  discovery/   manual.py, whop_apify.py, niche.py (live traffic), ranker.py ($/clip)
  sourcing.py  rights check + yt-dlp / direct download
  analysis/    transcribe.py (faster-whisper), highlights.py (scoring + NMS), llm.py (optional Claude)
  editing/     styles.py, captions.py (ASS karaoke), reframe.py (face track), timeline.py (jump cuts), render.py
  publishing/  compose.py (copy/hashtags/disclosure), schedule.py, postiz.py, youtube.py, instagram.py,
               tiktok.py, x.py
  connect.py   one-time OAuth linking (PKCE, local callback) + API-key checks; see docs/CONNECT.md
  brand.py     brand kit validation, approval gate, YouTube channel update, profile checklist
  optimizer.py Thompson-sampling style/length/hook optimizer
  pipeline.py  the engine
  cli.py       `vv88` command
```

Tests: `pip install -e ".[dev]" && pytest`. The end-to-end test renders real clips with FFmpeg.

## Sources

- Opus Clip, [The Anatomy of a Viral TikTok in 2026](https://www.opus.pro/blog/anatomy-of-a-viral-tiktok-2026) and [11 Editing Aesthetics Dominating Short-Form in 2026](https://www.opus.pro/blog/editing-aesthetics-dominating-short-form-2026) (vendor research; treat as directional)
- [TikTok Content Posting API — Direct Post](https://developers.tiktok.com/doc/content-posting-api-reference-direct-post)
- [Instagram Platform — Content Publishing](https://developers.facebook.com/docs/instagram-platform/content-publishing)
- [X API — chunked media upload](https://docs.x.com/x-api/media/quickstart/media-upload-chunked)
- [YouTube Data API — quota costs](https://developers.google.com/youtube/v3/determine_quota_cost)
- [Whop Content Rewards docs](https://docs.whop.com/memberships-and-access/third-party-apps/content-rewards) and the [Apify Whop Content Rewards scraper](https://apify.com/automation-lab/whop-content-rewards-scraper)
- [Postiz public API](https://docs.postiz.com/public-api)
