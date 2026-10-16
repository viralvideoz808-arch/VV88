"""Campaigns: ranked list, niche leaderboard, add a campaign by hand, archive / restore.

Campaigns added here are appended to the manual campaigns YAML (settings.discovery.manual_file), so the
CLI and every later discovery run see exactly the same list. The file is rewritten atomically and the
previous version is kept next to it as `<name>.bak` (hand-written comments survive there).
"""

from __future__ import annotations

import math
import os
import re
import shutil
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlparse

import yaml
from fastapi import APIRouter, Depends, Request

from vv88.discovery.manual import ManualCampaignSource
from vv88.models import PLATFORMS, Campaign, stable_id
from vv88.publishing.base import redact
from vv88.web.core import context, flash, redirect, render, require_user, verify_csrf

router = APIRouter()

ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
CATEGORY_RE = re.compile(r"^[\w][\w &/+.-]{0,39}$")
TAG_RE = re.compile(r"^[\w]{1,60}$")
MENTION_RE = re.compile(r"^[\w.]{1,60}$")
MAX_URLS = 50
_yaml_lock = threading.Lock()


class CampaignFileError(ValueError):
    """The manual campaigns YAML can't be read or written safely (message is shown to the user)."""


# ---- YAML helpers -----------------------------------------------------------------------
def manual_path(settings) -> Path:
    return Path(settings.discovery.manual_file).expanduser()


def _effective_id(item: dict) -> str:
    """Same id rule as ManualCampaignSource."""
    return str(item.get("id") or stable_id(item.get("title", ""), item.get("campaign_url", "")))


def load_manual_doc(path: Path) -> tuple[dict, list]:
    """Read the YAML as {"campaigns": [...]}; refuse (rather than clobber) anything unexpected."""
    if not path.exists():
        doc: dict = {"campaigns": []}
        return doc, doc["campaigns"]
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as e:
        raise CampaignFileError(f"Couldn't read {path.name}: {e}") from None
    except yaml.YAMLError:
        raise CampaignFileError(f"{path.name} isn't valid YAML, so it wasn't changed. Fix it by hand "
                                "(or move it away) and try again.") from None
    if doc is None:
        doc = {"campaigns": []}
    elif isinstance(doc, list):
        doc = {"campaigns": doc}
    if not isinstance(doc, dict):
        raise CampaignFileError(f"{path.name} should contain a 'campaigns:' list; it wasn't changed.")
    items = doc.get("campaigns")
    if items is None:
        doc["campaigns"] = items = []
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        raise CampaignFileError(f"{path.name} has a 'campaigns:' entry that isn't a campaign; it wasn't changed.")
    return doc, items


def write_manual_doc(path: Path, doc: dict) -> list[Campaign]:
    """Validate and atomically replace the YAML. Returns the campaigns it now defines."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=1000)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        try:
            campaigns = ManualCampaignSource(tmp).fetch()  # the whole file must still parse
        except (TypeError, ValueError, AttributeError) as e:
            raise CampaignFileError(f"{path.name} has an entry the engine can't read ({redact(str(e))[:160]}); "
                                    "it wasn't changed.") from None
        if path.exists():
            os.chmod(tmp, path.stat().st_mode & 0o777)
            shutil.copy2(path, path.with_name(path.name + ".bak"))
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return campaigns


# ---- form parsing -------------------------------------------------------------------------
def _s(form, key: str, limit: int = 500) -> str:
    v = form.get(key)
    return v.strip()[:limit] if isinstance(v, str) else ""


def http_url(value: str) -> str | None:
    s = value.strip()
    if not s or len(s) > 2048 or any(c.isspace() or ord(c) < 32 for c in s):
        return None
    try:
        p = urlparse(s)
    except ValueError:
        return None
    if p.scheme.lower() not in ("http", "https") or not p.hostname:
        return None
    return s


def safe_link(value: str) -> str:
    """Only http(s) links are rendered as clickable (campaign data may come from scraped listings)."""
    return http_url(value or "") or ""


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[\s,]+", text) if t]


def _slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return s[:48].strip("-")


def _number(raw: str, label: str, errors: list[str], required: bool) -> float | None:
    if raw == "":
        if required:
            errors.append(f"{label} is required.")
        return None
    try:
        v = float(raw.replace("$", "").replace(",", ""))
    except ValueError:
        errors.append(f"{label} must be a number.")
        return None
    if not math.isfinite(v) or v < 0:
        errors.append(f"{label} must be 0 or more.")
        return None
    if v > 1e9:
        errors.append(f"{label} is unrealistically large.")
        return None
    return v


def parse_campaign_form(form) -> tuple[dict, dict, list[str]]:
    """-> (yaml item, echo values for re-rendering, errors)."""
    errors: list[str] = []
    values = {k: _s(form, k, 5000 if k in ("requirements", "source_urls") else 500) for k in (
        "title", "brand", "category", "reward_per_1k_usd", "budget_remaining_usd", "source_urls",
        "required_hashtags", "required_mentions", "keywords", "campaign_url", "requirements", "id")}
    values["platforms"] = [p for p in form.getlist("platforms") if isinstance(p, str)]
    values["paid_partnership"] = form.get("paid_partnership") in ("on", "true", "1", "yes")

    title = values["title"]
    if not title:
        errors.append("Title is required.")
    elif len(title) > 200:
        errors.append("Title is too long (200 characters max).")
    brand = values["brand"]
    if len(brand) > 120:
        errors.append("Brand is too long (120 characters max).")
    category = (values["category"] or "general").lower()
    if not CATEGORY_RE.match(category):
        errors.append("Niche should be a short word or two, like 'finance' or 'gaming'.")

    reward = _number(values["reward_per_1k_usd"], "Payout per 1K views", errors, required=True)
    if reward is not None and reward > 10_000:
        errors.append("Payout per 1K views looks wrong (over $10,000).")
    budget = _number(values["budget_remaining_usd"], "Budget left", errors, required=False)

    bad_platforms = [p for p in values["platforms"] if p not in PLATFORMS]
    if bad_platforms:
        errors.append("Unknown platform: " + ", ".join(p[:20] for p in bad_platforms[:3]))
    platforms = [p for p in PLATFORMS if p in values["platforms"]]

    urls: list[str] = []
    for line in values["source_urls"].splitlines():
        line = line.strip()
        if not line:
            continue
        u = http_url(line)
        if u is None:
            errors.append(f"Source videos must be http(s) links, one per line: {line[:80]!r} isn't.")
            break
        if u not in urls:
            urls.append(u)
    if len(urls) > MAX_URLS:
        errors.append(f"At most {MAX_URLS} source links per campaign.")

    hashtags = []
    for t in _tokens(values["required_hashtags"]):
        t = t.lstrip("#")
        if not TAG_RE.match(t):
            errors.append(f"Hashtag {t[:40]!r} has characters platforms don't allow.")
            break
        hashtags.append("#" + t)
    mentions = []
    for t in _tokens(values["required_mentions"]):
        t = t.lstrip("@")
        if not MENTION_RE.match(t):
            errors.append(f"Mention {t[:40]!r} isn't a valid @handle.")
            break
        mentions.append("@" + t)
    kw_text = values["keywords"]
    keywords = [k.strip() for k in (kw_text.split(",") if "," in kw_text else kw_text.split()) if k.strip()]
    if len(hashtags) > 30 or len(mentions) > 30 or len(keywords) > 50:
        errors.append("Too many hashtags, mentions or keywords.")
    if any(len(k) > 60 for k in keywords):
        errors.append("Keywords should be short (60 characters max each).")

    campaign_url = ""
    if values["campaign_url"]:
        campaign_url = http_url(values["campaign_url"]) or ""
        if not campaign_url:
            errors.append("Campaign page must be an http(s) link.")
    requirements = values["requirements"]
    if len(requirements) > 4000:
        errors.append("Requirements are too long (4,000 characters max).")

    cid = values["id"].lower()
    if cid and not ID_RE.match(cid):
        errors.append("ID may only use a-z, 0-9 and dashes (max 64), e.g. 'money-talks'.")

    item: dict = {"title": title}
    if cid:
        item["id"] = cid
    if brand:
        item["brand"] = brand
    item["category"] = category
    item["reward_per_1k_usd"] = reward
    if budget is not None:
        item["budget_remaining_usd"] = budget
    item["platforms"] = platforms
    item["source_urls"] = urls
    for key, val in (("required_hashtags", hashtags), ("required_mentions", mentions), ("keywords", keywords)):
        if val:
            item[key] = val
    if campaign_url:
        item["campaign_url"] = campaign_url
    if requirements:
        item["requirements"] = requirements
    item["paid_partnership"] = values["paid_partnership"]
    item["status"] = "active"
    return item, values, errors


# ---- page -------------------------------------------------------------------------------
def _active_job(ctx, kinds: tuple[str, ...]):
    marks = ",".join("?" * len(kinds))
    return ctx.store.db.execute(
        f"SELECT id, kind, status FROM jobs WHERE kind IN ({marks}) AND status IN ('queued','running') "
        "ORDER BY id DESC LIMIT 1", kinds).fetchone()


def _leaderboard(active: list[dict]) -> list[dict]:
    niches: dict[str, dict] = {}
    for r in active:  # already sorted best-first, so the first campaign seen in a niche is its best
        cat = r["c"].category or "general"
        n = niches.setdefault(cat, {"category": cat, "best": r["score"], "count": 0, "top": r["c"].title})
        n["count"] += 1
    board = sorted(niches.values(), key=lambda n: (-n["best"], -n["count"], n["category"]))
    top = max((n["best"] for n in board), default=0.0)
    for n in board:
        n["pct"] = max(2, round(100 * n["best"] / top)) if top > 0 and n["best"] > 0 else 0
    return board


def _page(request: Request, ctx, form: dict | None = None, open_form: bool = False, status_code: int = 200):
    store = ctx.store
    scores = store.campaign_scores()
    every = store.campaigns(active_only=False)
    try:
        _, items = load_manual_doc(manual_path(ctx.settings))
        manual_ids = {_effective_id(i) for i in items}
    except CampaignFileError:
        manual_ids = set()

    def row(c: Campaign) -> dict:
        return {"c": c, "score": float(scores.get(c.id) or 0.0), "manual": c.id in manual_ids,
                "link": safe_link(c.campaign_url)}

    active = sorted((row(c) for c in every if c.status == "active"),
                    key=lambda r: (-r["score"], r["c"].title.lower()))
    archived = sorted((row(c) for c in every if c.status != "active"), key=lambda r: r["c"].title.lower())
    response = render(
        request, "campaigns.html", title="Campaigns",
        active=active, archived=archived, leaderboard=_leaderboard(active),
        known_niches=sorted({*ctx.settings.niche.keywords, *(r["c"].category for r in active)}),
        platforms=PLATFORMS, last_discovery=store.kv_get("last_discovery"),
        manual_file=manual_path(ctx.settings).name, whop_enabled=ctx.settings.discovery.whop_apify.enabled,
        form=form or {"platforms": list(PLATFORMS), "paid_partnership": True},
        open_form=open_form or not every, busy=_active_job(ctx, ("discover", "rank")),
    )
    response.status_code = status_code
    return response


@router.get("/campaigns", dependencies=[Depends(require_user)])
def campaigns_page(request: Request):
    return _page(request, context(request))


@router.post("/campaigns/discover", dependencies=[Depends(require_user), Depends(verify_csrf)])
def discover(request: Request):
    return _start(request, "discover")


@router.post("/campaigns/rank", dependencies=[Depends(require_user), Depends(verify_csrf)])
def rank(request: Request):
    return _start(request, "rank")


def _start(request: Request, kind: str):
    ctx = context(request)
    running = _active_job(ctx, (kind,))
    if running:
        return redirect(request, f"/jobs/{running['id']}", "Already running — showing that job.", "info")
    job_id = ctx.jobs.submit(kind, {"source": "campaigns"})
    label = "Discovering and ranking campaigns" if kind == "discover" else "Re-ranking campaigns"
    return redirect(request, f"/jobs/{job_id}", f"{label} (job #{job_id}).", "success")


@router.post("/campaigns/add", dependencies=[Depends(require_user), Depends(verify_csrf)])
async def add_campaign(request: Request):
    form = await request.form()
    ctx = context(request)
    item, values, errors = parse_campaign_form(form)
    path = manual_path(ctx.settings)
    if not errors:
        try:
            with _yaml_lock:
                doc, items = load_manual_doc(path)
                taken = {_effective_id(i) for i in items} | set(ctx.store.campaign_scores())
                if "id" in item:
                    if item["id"] in taken:
                        raise CampaignFileError(f"A campaign with ID '{item['id']}' already exists. "
                                                "Pick another ID or leave it empty.")
                else:
                    base = _slug(item["title"]) or stable_id(item["title"], item.get("campaign_url", ""), length=10)
                    cid, k = base, 2
                    while cid in taken:
                        cid, k = f"{base}-{k}", k + 1
                    item = {"id": cid, **item}
                items.append(item)
                campaigns = write_manual_doc(path, doc)
            ctx.store.upsert_campaigns(campaigns)
        except CampaignFileError as e:
            errors.append(str(e))
        except OSError as e:
            errors.append(f"Couldn't save {path.name}: {redact(str(e))[:200]}")
    if errors:
        flash(request, "Campaign not added. " + " ".join(errors[:4]), "error")
        return _page(request, ctx, form=values, open_form=True, status_code=400)
    msg = f"Added “{item['title']}” ({item['id']})."
    if ctx.jobs.busy_with("rank") or ctx.jobs.busy_with("discover"):
        msg += " Its expected $/clip appears after the ranking job that's running now."
    else:
        job_id = ctx.jobs.submit("rank", {"source": "campaigns"})
        msg += f" Scoring it in the background (job #{job_id}) — reload in a moment for its $/clip."
    return redirect(request, "/campaigns", msg, "success")


def _set_status(request: Request, campaign_id: str, status: str):
    ctx = context(request)
    campaign = ctx.store.campaign(campaign_id)
    path = manual_path(ctx.settings)
    in_yaml = False
    try:
        with _yaml_lock:
            doc, items = load_manual_doc(path)
            matches = [i for i in items if _effective_id(i) == campaign_id]
            if matches:
                in_yaml = True
                for i in matches:
                    i["status"] = status
                campaigns = write_manual_doc(path, doc)
                campaign = next((c for c in campaigns if c.id == campaign_id), campaign)
    except CampaignFileError as e:
        return redirect(request, "/campaigns", str(e), "error")
    except OSError as e:
        return redirect(request, "/campaigns", f"Couldn't save {path.name}: {redact(str(e))[:200]}", "error")
    if campaign is None:
        return redirect(request, "/campaigns", "That campaign doesn't exist (any more).", "error")
    campaign.status = status
    ctx.store.upsert_campaigns([campaign])
    return campaign, in_yaml


@router.post("/campaigns/{campaign_id:path}/archive", dependencies=[Depends(require_user), Depends(verify_csrf)])
def archive(request: Request, campaign_id: str):
    res = _set_status(request, campaign_id, "inactive")
    if not isinstance(res, tuple):
        return res
    campaign, in_yaml = res
    msg = f"Archived “{campaign.title}”. It won't be clipped or ranked until you restore it."
    if not in_yaml:
        msg += " It came from discovery, so it may come back the next time discovery still lists it."
    return redirect(request, "/campaigns", msg, "success")


@router.post("/campaigns/{campaign_id:path}/restore", dependencies=[Depends(require_user), Depends(verify_csrf)])
def restore(request: Request, campaign_id: str):
    res = _set_status(request, campaign_id, "active")
    if not isinstance(res, tuple):
        return res
    campaign, _ = res
    return redirect(request, "/campaigns", f"Restored “{campaign.title}”. Re-rank to refresh its $/clip.", "success")
