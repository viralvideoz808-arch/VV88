"""Fleet & website manager: keeps bookings, car statuses and the website's featured cars up to date."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from carlos.agents.base import Agent, Context
from carlos.db import now_iso
from carlos.inventory import list_vehicles, money
from carlos.messaging import queue


class Fleet(Agent):
    name = "fleet"
    title = "Fleet & Website Manager"
    description = ("Moves bookings through pickup and return, expires stale requests, flags listings that need "
                   "photos or prices, rotates the featured cars on the homepage by demand, and drafts a daily "
                   "'car of the day' post for our own Threads/Facebook (always for your approval).")

    def run(self, ctx: Context) -> str:
        db, s = ctx.db, ctx.settings
        today = date.today().isoformat()
        stamp = now_iso()
        moved = {
            "picked up": db.run("UPDATE bookings SET status = 'active', updated_at = ? WHERE status = 'confirmed' "
                                "AND start_date <= ?", (stamp, today)),
            "returned": db.run("UPDATE bookings SET status = 'completed', updated_at = ? WHERE status = 'active' "
                               "AND end_date < ?", (stamp, today)),
            "expired requests": db.run("UPDATE bookings SET status = 'cancelled', notes = notes || ' [expired: not "
                                       "confirmed before pickup date]', updated_at = ? WHERE status = 'pending' "
                                       "AND start_date < ?", (stamp, today)),
        }

        issues = []
        for v in list_vehicles(db, public=False):
            if v["status"] in ("sold", "hidden"):
                continue
            problems = []
            if not v["photos"]:
                problems.append("no photos")
            if v["listing"] == "rent" and not v["daily_rate"]:
                problems.append("no daily rate")
            if v["listing"] == "sale" and not v["sale_price"]:
                problems.append("no price")
            if len(v["description"] or "") < 40:
                problems.append("short description")
            if v["sample"]:
                problems.append("sample listing — replace with a real car")
            if problems:
                issues.append({"id": v["id"], "title": v["title"], "problems": problems})
        db.set_setting("fleet.issues", issues)

        featured = 0
        if db.get_setting("fleet.auto_feature") is not False:
            since = (datetime.now(timezone.utc) - timedelta(days=30)).replace(microsecond=0).isoformat()
            demand = {r["vehicle_id"]: r["n"] for r in db.all(
                "SELECT vehicle_id, COUNT(*) * 10 AS n FROM bookings WHERE created_at >= ? GROUP BY vehicle_id",
                (since,))}
            for r in db.all("SELECT vehicle_id, COUNT(*) * 5 AS n FROM inquiries WHERE vehicle_id IS NOT NULL "
                            "AND created_at >= ? GROUP BY vehicle_id", (since,)):
                demand[r["vehicle_id"]] = demand.get(r["vehicle_id"], 0) + r["n"]
            for listing, slots in (("rent", 6), ("sale", 3)):
                cars = [v for v in list_vehicles(db, listing=listing, sort="recent") if v["status"] == "available"]
                ranked = sorted(cars, key=lambda v: -(demand.get(v["id"], 0) + v["views"] + 3 * len(v["photos"])))
                top = {v["id"] for v in ranked[:slots]}
                for v in cars:
                    want = int(v["id"] in top)
                    if v["featured"] != want:
                        db.update("vehicles", v["id"], {"featured": want})
                        featured += 1

        drafted = 0
        if db.get_setting("fleet.daily_post") is not False and db.get_setting("fleet.last_post_day") != today:
            cars = [v for v in list_vehicles(db, listing="rent") if v["status"] == "available"]
            if cars:
                car = cars[date.today().toordinal() % len(cars)]
                city = f" in {s.city}" if s.city else ""
                text = (f"Car of the day: the {car['title']} — {car['description'].split('.')[0].lower()}. "
                        f"From {money(car['daily_rate'])}/day{city}. Book in a minute: "
                        f"{s.site_url}/cars/{car['slug']}")
                channels = [c for c, on in (("threads_post", s.threads_enabled), ("fb_post", s.meta_enabled))
                            if on or s.demo_mode]
                for channel in channels:
                    queue(db, kind="post", channel=channel, target="sandbox" if s.demo_mode else "", body=text[:480],
                          agent=self.name, reason="daily promo post on our own account")
                    drafted += 1
                db.set_setting("fleet.last_post_day", today)

        parts = [f"{v} {k}" for k, v in moved.items() if v]
        if featured:
            parts.append(f"{featured} featured-car changes")
        parts.append(f"{len(issues)} listings need attention")
        if drafted:
            parts.append(f"{drafted} promo posts drafted for approval")
        return ", ".join(parts)
