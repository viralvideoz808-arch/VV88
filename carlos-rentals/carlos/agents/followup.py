"""Follow-up: booking reminders, thank-yous, one follow-up per web inquiry, stale-lead cleanup, owner digest."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from carlos.agents.base import Agent, Context
from carlos.agents.concierge import notify_owner
from carlos.db import now_iso
from carlos.inventory import get_vehicle, money
from carlos.messaging import queue


class FollowUp(Agent):
    name = "followup"
    title = "Follow-up & Reminders"
    description = ("Emails pickup reminders and thank-yous, follows up once on website inquiries, alerts you to "
                   "unconfirmed bookings, retires cold leads and sends you a morning digest.")

    def email(self, ctx: Context, to: str, subject: str, body: str, reason: str) -> bool:
        if not (ctx.settings.email_enabled and to and "@" in to):
            return False
        queue(ctx.db, kind="followup", channel="email", target=to, body=f"Subject: {subject}\n\n{body}",
              agent=self.name, approved=True, reason=reason)
        return True

    def run(self, ctx: Context) -> str:
        db, s = ctx.db, ctx.settings
        today = date.today()
        sign = f"\n\n— {s.business_name}" + (f"\n{s.phone}" if s.phone else "") + f"\n{s.site_url}"
        done = {"reminders": 0, "thanks": 0, "inquiry_followups": 0, "alerts": 0, "retired": 0}
        unsent = 0

        for b in db.all("SELECT * FROM bookings WHERE status = 'confirmed' AND reminder_sent = 0 AND start_date <= ?",
                        ((today + timedelta(days=1)).isoformat(),)):
            car = get_vehicle(db, b["vehicle_id"])
            body = (f"Hi {b['name'].split(' ')[0]},\n\nSee you soon! Your {car['title']} is booked for pickup on "
                    f"{b['start_date']} (return {b['end_date']}).\n\nPlease bring your driver's license and the card "
                    f"for the {money(b['deposit'])} refundable deposit.\nBooking: {s.site_url}/booking/{b['ref']}{sign}")
            if self.email(ctx, b["email"], f"Pickup reminder — {b['ref']}", body, f"reminder {b['ref']}"):
                done["reminders"] += 1
            else:
                unsent += 1
            db.run("UPDATE bookings SET reminder_sent = 1 WHERE id = ?", (b["id"],))

        for b in db.all("SELECT * FROM bookings WHERE status = 'completed' AND thanks_sent = 0"):
            body = (f"Hi {b['name'].split(' ')[0]},\n\nThanks for renting with us! If you have a minute, a quick "
                    f"review helps a small business a lot. Need a car again? Reply to this email or book at "
                    f"{s.site_url}{sign}")
            if self.email(ctx, b["email"], "Thanks for riding with CAR-LOS", body, f"thanks {b['ref']}"):
                done["thanks"] += 1
            else:
                unsent += 1
            db.run("UPDATE bookings SET thanks_sent = 1 WHERE id = ?", (b["id"],))

        day_ago = (datetime.now(timezone.utc) - timedelta(hours=24)).replace(microsecond=0).isoformat()
        for q in db.all("SELECT * FROM inquiries WHERE status = 'new' AND followed_up = 0 AND created_at <= ?",
                        (day_ago,)):
            car = get_vehicle(db, q["vehicle_id"]) if q["vehicle_id"] else None
            about = f" about the {car['title']}" if car else ""
            body = (f"Hi {q['name'].split(' ')[0]},\n\nJust checking in on your message{about}. Happy to answer "
                    f"questions or set a time that works for you — just reply here.{sign}")
            if self.email(ctx, q["email"], f"Following up{about}", body, f"inquiry #{q['id']}"):
                done["inquiry_followups"] += 1
            db.run("UPDATE inquiries SET followed_up = 1 WHERE id = ?", (q["id"],))

        half_day = (datetime.now(timezone.utc) - timedelta(hours=12)).replace(microsecond=0).isoformat()
        stale = db.all("SELECT ref FROM bookings WHERE status = 'pending' AND created_at <= ?", (half_day,))
        if stale and db.get_setting("followup.last_pending_alert") != today.isoformat():
            notify_owner(ctx, f"{len(stale)} booking request(s) waiting",
                         "Unconfirmed for 12h+: " + ", ".join(r["ref"] for r in stale))
            db.set_setting("followup.last_pending_alert", today.isoformat())
            done["alerts"] += 1

        cold = (datetime.now(timezone.utc) - timedelta(days=14)).replace(microsecond=0).isoformat()
        done["retired"] = db.run("UPDATE leads SET status = 'no_response', updated_at = ? WHERE status = 'contacted' "
                                 "AND last_contact_at <= ?", (now_iso(), cold))

        self.digest(ctx)
        sent = ctx.dispatcher.flush()
        out = ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in done.items() if v) or "nothing due"
        if unsent:
            out += f" · {unsent} customer emails not sent (connect SMTP in Settings)"
        if sent:
            out += " · outbox: " + ", ".join(f"{v} {k}" for k, v in sent.items())
        return out

    def digest(self, ctx: Context) -> None:
        db, s = ctx.db, ctx.settings
        try:
            local = datetime.now(ZoneInfo(s.timezone))
        except Exception:
            local = datetime.now(timezone.utc)
        if local.hour < 7 or db.get_setting("followup.last_digest") == local.date().isoformat():
            return
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).replace(microsecond=0).isoformat()
        stats = {
            "new leads": db.scalar("SELECT COUNT(*) FROM leads WHERE created_at >= ?", (since,)),
            "qualified": db.scalar("SELECT COUNT(*) FROM leads WHERE status = 'qualified'"),
            "awaiting your approval": db.scalar("SELECT COUNT(*) FROM outbox WHERE status = 'pending_approval'"),
            "conversations needing you": db.scalar("SELECT COUNT(*) FROM conversations WHERE mode = 'human' "
                                                   "AND needs_reply = 1"),
            "new booking requests": db.scalar("SELECT COUNT(*) FROM bookings WHERE created_at >= ?", (since,)),
            "new inquiries": db.scalar("SELECT COUNT(*) FROM inquiries WHERE created_at >= ?", (since,)),
            "pickups today": db.scalar("SELECT COUNT(*) FROM bookings WHERE status = 'confirmed' AND start_date = ?",
                                       (local.date().isoformat(),)),
        }
        notify_owner(ctx, f"Daily digest {local.date().isoformat()}",
                     "\n".join(f"{k}: {v}" for k, v in stats.items()))
        db.set_setting("followup.last_digest", local.date().isoformat())
