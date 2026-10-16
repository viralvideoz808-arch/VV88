# Linking TikTok, Instagram, YouTube and X

There are two ways to link the accounts. Do this once, on your own computer, signed in to each account in your browser.

## Option 1 — Postiz (easiest: no developer apps)

1. Sign up at postiz.com, or self-host it for free (github.com/gitroomhq/postiz-app).
2. In Postiz, click **Add channel** and connect TikTok, Instagram, YouTube and X. Postiz runs each platform's sign-in, and its TikTok app is already approved for public posting.
3. In Postiz, open **Settings → Public API** and copy the API key.
4. In the VV88 folder:
   ```bash
   vv88 connect postiz          # paste the key; your channel IDs are saved automatically
   ```
5. In `config.yaml`, set `publishing.backend: postiz`.

Postiz keeps every platform's tokens fresh, so the accounts stay linked. To also pull **view counts** back into the optimizer, link the accounts directly as well (Option 2). Posting can stay on Postiz.

## Option 2 — Official APIs (more control, more setup)

Each platform needs a free developer app. Create the app, copy its keys into `.env`, then run `vv88 connect <platform>`. Use this callback / redirect URL everywhere:

```
http://127.0.0.1:8788/callback/      (Facebook/Instagram: http://localhost:8788/callback/)
```

### YouTube
1. Open console.cloud.google.com and create a project.
2. Go to **APIs & Services → Library** and enable **YouTube Data API v3**.
3. Set up the **OAuth consent screen**: External, then add yourself.
4. Click **Publish app** so it's "In production". In Testing mode Google expires the link after 7 days.
5. Go to **Credentials → Create credentials → OAuth client ID → Desktop app**. Put the client ID and secret into `.env` as `YOUTUBE_CLIENT_ID` and `YOUTUBE_CLIENT_SECRET`.
6. Optional: **Create credentials → API key**, then run `vv88 connect youtube-key`. This turns on the niche-traffic signal.
7. Run `vv88 connect youtube`.

### TikTok
1. Open developers.tiktok.com, go to **Manage apps** and create an app.
2. Add **Login Kit** (Desktop) and the **Content Posting API** with Direct Post turned on.
3. Add the redirect URI above, and the scopes `user.info.basic`, `video.publish`, `video.list`.
4. Put the keys into `.env` as `TIKTOK_CLIENT_KEY` and `TIKTOK_CLIENT_SECRET`.
5. Run `vv88 connect tiktok`.
6. Until TikTok audits the app, its posts are private. Submit the audit once a test post works.

### Instagram
1. In the Instagram app, switch the account to **Creator** or **Business**, and link it to a Facebook Page you manage.
2. Open developers.facebook.com and create an app (Business type).
3. Add **Facebook Login for Business** and the **Instagram Graph API**.
4. Add `http://localhost:8788/callback/` as a valid OAuth redirect URI.
5. Put the keys into `.env` as `FACEBOOK_APP_ID` and `FACEBOOK_APP_SECRET`.
6. Run `vv88 connect instagram`.
7. The token lasts about 60 days. `vv88 doctor` warns 10 days before it lapses; run the same command again to renew.

### X
1. Open developer.x.com, go to **Projects & Apps**, and create an app.
2. Under **User authentication settings**, choose OAuth 2.0, permissions **Read and write**, and the callback URL above.
3. Put the keys into `.env` as `X_CLIENT_ID` (and `X_CLIENT_SECRET` if the app is confidential).
4. Run `vv88 connect x`.
5. Posting through the API is pay-per-use on X, so add credits in the developer console.

### Tools
```bash
vv88 connect apify          # Whop campaign discovery (Apify > Settings > API & Integrations)
vv88 connect anthropic      # optional Claude re-ranking
vv88 connect status         # everything at a glance
```

## Safety

- VV88 never asks for or stores passwords. It keeps only tokens you can revoke at any time:
  - Google: myaccount.google.com/permissions
  - TikTok / Instagram / X: Settings → Apps or Security
- `.env` holds those tokens. Never commit it or share it (it is in `.gitignore`).
- Turn on 2-step verification on the Gmail account that owns everything. Every account here can be recovered through that inbox.
