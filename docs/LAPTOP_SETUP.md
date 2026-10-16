# Laptop setup: profiles + linking (for a Claude session on the owner's laptop)

This part needs the owner's own browser: the accounts are signed in there, and some steps need a person (2FA, captchas). A cloud session can't reach that browser. So run these steps from a Claude session **on the laptop**, using the Claude Desktop app with the Claude in Chrome extension, or Claude Code started on the laptop.

Identity, already decided by the owner:

| | |
|---|---|
| Account email | viralvideoz808@gmail.com |
| Username on all four platforms | **@allviralvideozz** (already claimed; don't change it) |
| Brand kit | `brand/` in this repo: `brand.json`, `avatar_1024.png`, `banner_youtube_2560x1440.png`, `header_x_1500x500.png` |

## Ground rules for the session doing this

- Only use the accounts already signed in to Chrome. Never change an account's email, password, 2-step verification or recovery settings.
- If a login, 2FA code, captcha or "confirm it's you" screen appears, stop and let the owner do it.
- Don't touch any profile until the owner has approved the brand kit. Check with `vv88 brand show`: it must print `approved: yes`. If it doesn't, show the owner the images and text and wait for them to run `vv88 brand approve --fingerprint <fp>`.
- Keep the owner informed: show a screenshot of each profile after it's updated.

## 1. Get the code

```bash
git clone https://github.com/viralvideoz808-arch/VV88.git && cd VV88
git checkout ccr-c1e9d539-i5e0r4
pip install -e ".[all]"        # FFmpeg must also be installed (winget/brew/apt)
cp config.example.yaml config.yaml && cp .env.example .env
vv88 doctor
```

## 2. Approve the brand kit (owner)

```bash
vv88 brand show                                  # owner reviews images + every word
vv88 brand approve --fingerprint <fingerprint>   # owner runs this
vv88 brand apply                                 # writes brand/APPLY_CHECKLIST.md (+ YouTube text if linked)
```

## 3. Update each profile in Chrome, using the exact text from `brand/brand.json`

| Platform | Page | Set |
|---|---|---|
| TikTok | tiktok.com → Profile → Edit profile | Photo `brand/avatar_1024.png` · Name `display_names.tiktok` · Bio `bios.tiktok` |
| Instagram | instagram.com/accounts/edit | Photo · Name `display_names.instagram` · Bio `bios.instagram` |
| X | x.com/settings/profile | Photo · Header `brand/header_x_1500x500.png` · Name `display_names.x` · Bio `bios.x` |
| YouTube | studio.youtube.com → Customization | Profile picture · Banner `brand/banner_youtube_2560x1440.png` · Name `display_names.youtube` · Description `bios.youtube_description` · keywords under Settings → Channel |

Don't edit the usernames; `@allviralvideozz` is already set. If a platform rejects a field (length, rate limit on name changes), report it and move on.

## 4. Link the accounts so they stay linked

**Recommended: Postiz.** One key covers all four platforms, and Postiz keeps the tokens fresh.

1. In Chrome, open platform.postiz.com and sign in or sign up with viralvideoz808@gmail.com.
2. Click **Add channel** for TikTok, Instagram, YouTube and X. Each opens the platform's own consent screen in the signed-in session; approve each one.
3. Go to Postiz **Settings → Public API** and copy the key.
4. Run `vv88 connect postiz`, paste the key, and check that it lists all four channel IDs.
5. In `config.yaml`, set `publishing.backend: postiz` and `enabled: true` for the four platforms.

**To also read view counts** (needed by the optimizer), add direct links later by following `docs/CONNECT.md`: `vv88 connect youtube` / `tiktok` / `instagram` / `x`.

**Tools**

| Tool | Where to get it | Command |
|---|---|---|
| Apify (campaign discovery) | console.apify.com → Settings → API & Integrations → copy token | `vv88 connect apify` |
| YouTube API key (niche signal) | console.cloud.google.com → enable YouTube Data API v3 → Credentials → API key | `vv88 connect youtube-key` |

## 5. Check and start

```bash
vv88 connect status      # everything linked
vv88 doctor              # no [!!] lines
vv88 run --once          # one full cycle; then `vv88 run` (or a scheduled task) to keep it going
```
