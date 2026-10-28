# Connecting the real accounts

Each step below is optional. The system switches each part from the sandbox to live as soon as that
part's settings are in `.env`. Restart `carlos run` after editing `.env`.

> Meta renames permissions and changes review steps from time to time. The names below match Meta's
> developer docs at the time of writing. If a name differs in the dashboard, pick the closest match.

## 1. Facebook Page + Instagram (Meta)

You need:
- a Facebook **Page** for the business;
- an Instagram **professional** account (Business or Creator) linked to that Page;
- a free Meta developer account.

1. Go to [developers.facebook.com](https://developers.facebook.com) and create an app (type **Business**).
   Add the **Messenger** and **Instagram** products, and **Webhooks**.
2. Request these permissions. Using them with people outside your own team needs *Advanced Access*,
   which takes **App Review** and **Business Verification**. Plan a few days for this.
   - `pages_show_list`, `pages_messaging`, `pages_read_engagement`, `pages_read_user_content`,
     `pages_manage_engagement`, `pages_manage_metadata`, `pages_manage_posts`
   - `instagram_basic`, `instagram_manage_comments`, `instagram_manage_messages`
   - Hashtag search also needs the **Instagram Public Content Access** feature.
3. Generate a **long-lived Page access token**. In Graph API Explorer, select the Page and request the
   permissions above. Then exchange the token for a long-lived one, and get the Page token from
   `/me/accounts`.
4. Find the IDs:
   - `FB_PAGE_ID`: shown in the Page's About section, or returned by `/me/accounts`.
   - `IG_USER_ID`: `GET /{page-id}?fields=instagram_business_account`.
5. Put them in `.env`:
   ```
   META_APP_SECRET=...      # App settings → Basic → App secret
   FB_PAGE_ID=...
   FB_PAGE_TOKEN=...
   IG_USER_ID=...
   ```
6. **Webhooks** (instant DMs and comments). Your site must be public over HTTPS first.
   - Callback URL: `https://YOUR-DOMAIN/webhooks/meta`
   - Verify token: the `META_VERIFY_TOKEN` value from `.env` (it was generated on first run).
   - Subscribe **Page** fields `messages` and `feed`, and **Instagram** fields `messages` and `comments`.
     Then subscribe the app to your Page.
   - Every webhook is checked against `X-Hub-Signature-256` using `META_APP_SECRET`.

Without webhooks, the Scout still reads comments on your recent posts every run. DMs, though, only
arrive through webhooks.

## 2. Threads

1. In the same Meta app (or a new one), add the **Threads API** use case.
2. Permissions:
   - `threads_basic` and `threads_content_publish`;
   - `threads_manage_replies` and `threads_read_replies`;
   - `threads_keyword_search`, which is needed to search other people's public posts and requires App Review.
3. Generate a long-lived Threads user token for the business profile. Then set:
   ```
   THREADS_USER_ID=...
   THREADS_ACCESS_TOKEN=...
   ```
4. Long-lived Threads tokens expire after about 60 days. Refresh yours before then (Meta's
   `refresh_access_token` endpoint), or the Scout logs an error in **Agents**.

## 3. Email

Any SMTP provider works, such as Google Workspace, Zoho, Postmark or SendGrid:

```
SMTP_HOST=smtp.example.com
SMTP_PORT=587            # 465 for SSL
SMTP_USER=...
SMTP_PASSWORD=...
SMTP_FROM=CAR-LOS Rentals <bookings@yourdomain.com>
OWNER_EMAIL=owner@yourdomain.com   # gets new-booking alerts, hand-offs and the morning digest
```

## 4. Claude

```
ANTHROPIC_API_KEY=sk-ant-...
```

## After connecting

- Open **Settings → Connections** in the console. Each service should show *connected*.
- Run **Agents → Lead Scout** once and check the result line for errors (for example, a missing permission).
- Keep **"I approve every first message"** on until you're happy with what the agents write.
