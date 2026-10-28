"""Vehicles, rental quotes, availability and bookings: the business logic the website and the agents share."""

from __future__ import annotations

import json
import re
import secrets
from datetime import date, datetime, timedelta

from carlos.db import Database, loads, now_iso

BODY_TYPES = ("sedan", "suv", "truck", "coupe", "convertible", "van", "hatchback")
LISTINGS = ("rent", "sale")
VEHICLE_STATUSES = ("available", "maintenance", "pending_sale", "sold", "hidden")
BOOKING_STATUSES = ("pending", "confirmed", "active", "completed", "cancelled")
HOLDING_STATUSES = ("pending", "confirmed", "active")  # these block the car for their dates


class QuoteError(ValueError):
    pass


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:80] or "car"


def unique_slug(db: Database, base: str, exclude_id: int | None = None) -> str:
    slug = slugify(base)
    candidate, n = slug, 2
    while True:
        row = db.one("SELECT id FROM vehicles WHERE slug = ?", (candidate,))
        if not row or row["id"] == exclude_id:
            return candidate
        candidate, n = f"{slug}-{n}", n + 1


def hydrate(row: dict | None) -> dict | None:
    if row is None:
        return None
    v = dict(row)
    v["features"] = loads(v.get("features"), [])
    v["photos"] = loads(v.get("photos"), [])
    v["title"] = f"{v['year']} {v['make']} {v['model']}"
    v["full_title"] = f"{v['title']} {v['trim']}".strip()
    return v


def get_vehicle(db: Database, key: int | str) -> dict | None:
    col = "id" if isinstance(key, int) or str(key).isdigit() else "slug"
    return hydrate(db.one(f"SELECT * FROM vehicles WHERE {col} = ?", (key,)))


def list_vehicles(db: Database, listing: str | None = None, body: str | None = None, q: str = "",
                  max_price: float | None = None, min_seats: int | None = None, sort: str = "featured",
                  public: bool = True, limit: int = 200) -> list[dict]:
    where, args = [], []
    if public:
        where.append("status NOT IN ('hidden', 'sold')")
    if listing in LISTINGS:
        where.append("listing = ?")
        args.append(listing)
    if body in BODY_TYPES:
        where.append("body = ?")
        args.append(body)
    if q:
        for word in q.lower().split()[:5]:
            where.append("(LOWER(make || ' ' || model || ' ' || trim || ' ' || body || ' ' || year) LIKE ?)")
            args.append(f"%{word}%")
    if max_price:
        where.append("(CASE listing WHEN 'rent' THEN daily_rate ELSE sale_price END) <= ?")
        args.append(max_price)
    if min_seats:
        where.append("seats >= ?")
        args.append(min_seats)
    order = {
        "featured": "featured DESC, views DESC, id",
        "price_low": "(CASE listing WHEN 'rent' THEN daily_rate ELSE sale_price END) ASC",
        "price_high": "(CASE listing WHEN 'rent' THEN daily_rate ELSE sale_price END) DESC",
        "newest": "year DESC, id DESC",
        "recent": "id DESC",
    }.get(sort, "featured DESC, id")
    sql = "SELECT * FROM vehicles" + (" WHERE " + " AND ".join(where) if where else "") + f" ORDER BY {order} LIMIT ?"
    return [hydrate(r) for r in db.all(sql, [*args, limit])]


def save_vehicle(db: Database, data: dict, vehicle_id: int | None = None) -> int:
    fields = {k: data[k] for k in (
        "make", "model", "year", "trim", "body", "listing", "daily_rate", "weekly_rate", "deposit", "sale_price",
        "mileage", "transmission", "fuel", "seats", "doors", "mpg", "color", "color_hex", "description", "status",
        "featured", "vin", "location", "sample") if k in data}
    for k in ("features", "photos"):
        if k in data:
            fields[k] = json.dumps(list(data[k]))
    fields["updated_at"] = now_iso()
    if vehicle_id:
        if {"make", "model", "year"} & fields.keys():
            cur = get_vehicle(db, vehicle_id) or {}
            base = f"{fields.get('year', cur.get('year'))} {fields.get('make', cur.get('make'))} {fields.get('model', cur.get('model'))}"
            fields["slug"] = unique_slug(db, base, exclude_id=vehicle_id)
        db.update("vehicles", vehicle_id, fields)
        return vehicle_id
    fields["slug"] = unique_slug(db, f"{data['year']} {data['make']} {data['model']} {data.get('listing', '')}")
    fields["created_at"] = fields["updated_at"]
    return db.insert("vehicles", fields)


# -- availability & quotes -----------------------------------------------------------------------
def parse_day(value: str | date) -> date:
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value).strip()[:10], "%Y-%m-%d").date()
    except ValueError as e:
        raise QuoteError("Dates must look like 2026-11-20.") from e


def booked_ranges(db: Database, vehicle_id: int, exclude_booking: int | None = None) -> list[tuple[date, date]]:
    rows = db.all(
        f"SELECT id, start_date, end_date FROM bookings WHERE vehicle_id = ? AND status IN "
        f"({','.join('?' * len(HOLDING_STATUSES))})", (vehicle_id, *HOLDING_STATUSES))
    return [(parse_day(r["start_date"]), parse_day(r["end_date"])) for r in rows if r["id"] != exclude_booking]


def is_available(db: Database, vehicle: dict, start: date, end: date, exclude_booking: int | None = None) -> bool:
    if vehicle["listing"] != "rent" or vehicle["status"] != "available":
        return False
    # A car returned on day X can go out again on day X.
    return all(end <= s or start >= e for s, e in booked_ranges(db, vehicle["id"], exclude_booking))


def next_free_dates(db: Database, vehicle: dict, start: date, days: int, horizon: int = 60) -> tuple[date, date] | None:
    for shift in range(horizon):
        s = start + timedelta(days=shift)
        if is_available(db, vehicle, s, s + timedelta(days=days)):
            return s, s + timedelta(days=days)
    return None


def quote(db: Database, vehicle: dict, start: str | date, end: str | date, extras: list[str] | None = None,
          driver_age: int | None = None, today: date | None = None) -> dict:
    if vehicle["listing"] != "rent":
        raise QuoteError("This car is for sale, not for rent.")
    s, e = parse_day(start), parse_day(end)
    today = today or date.today()
    if s < today:
        raise QuoteError("Pick-up date can't be in the past.")
    days = (e - s).days
    if days < 1:
        raise QuoteError("Return date must be at least one day after pick-up.")
    max_days = int(db.get_setting("rental.max_days") or 60)
    if days > max_days:
        raise QuoteError(f"Online bookings are limited to {max_days} days. Call us for longer rentals.")
    min_age = int(db.get_setting("rental.min_age") or 21)
    if driver_age is not None and driver_age < min_age:
        raise QuoteError(f"Drivers must be at least {min_age} years old.")

    daily = float(vehicle["daily_rate"] or 0)
    weekly = float(vehicle["weekly_rate"] or 0) or daily * 7
    weeks, rest = divmod(days, 7)
    base = round(min(weeks * weekly + rest * daily, days * daily), 2)

    lines = []
    catalog = db.get_setting("rental.extras") or {}
    for key in extras or []:
        item = catalog.get(key)
        if not item:
            continue
        amount = float(item.get("flat", 0)) + float(item.get("per_day", 0)) * days
        lines.append({"key": key, "label": item["label"], "amount": round(amount, 2)})
    extras_total = round(sum(x["amount"] for x in lines), 2)

    fees = 0.0
    young_age = int(db.get_setting("rental.young_driver_age") or 25)
    if driver_age is not None and driver_age < young_age:
        fees = round(float(db.get_setting("rental.young_driver_fee") or 0) * days, 2)

    tax_rate = float(db.get_setting("rental.tax_rate") or 0)
    tax = round((base + extras_total + fees) * tax_rate, 2)
    return {
        "start": s.isoformat(), "end": e.isoformat(), "days": days, "daily_rate": daily,
        "base": base, "saved": round(days * daily - base, 2), "extras": lines, "extras_total": extras_total,
        "fees": fees, "tax_rate": tax_rate, "tax": tax, "total": round(base + extras_total + fees + tax, 2),
        "deposit": float(vehicle["deposit"] or 0), "available": is_available(db, vehicle, s, e),
    }


def new_ref(db: Database) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    while True:
        ref = "CL-" + "".join(secrets.choice(alphabet) for _ in range(6))
        if not db.one("SELECT 1 FROM bookings WHERE ref = ?", (ref,)):
            return ref


def create_booking(db: Database, vehicle: dict, start: str, end: str, name: str, email: str, phone: str = "",
                   extras: list[str] | None = None, driver_age: int | None = None, pickup: str = "",
                   notes: str = "", source: str = "website", lead_id: int | None = None) -> dict:
    q = quote(db, vehicle, start, end, extras, driver_age)
    if not q["available"]:
        raise QuoteError("Sorry, that car is already booked for those dates.")
    stamp = now_iso()
    booking = {
        "ref": new_ref(db), "vehicle_id": vehicle["id"], "name": name.strip()[:120], "email": email.strip()[:200],
        "phone": phone.strip()[:40], "start_date": q["start"], "end_date": q["end"], "pickup": pickup[:200],
        "driver_age": driver_age, "extras": json.dumps([x["key"] for x in q["extras"]]), "days": q["days"],
        "subtotal": q["base"], "extras_total": q["extras_total"], "fees": q["fees"], "tax": q["tax"],
        "total": q["total"], "deposit": q["deposit"], "status": "pending", "source": source, "lead_id": lead_id,
        "notes": notes[:2000], "created_at": stamp, "updated_at": stamp,
    }
    booking["id"] = db.insert("bookings", booking)
    db.log(source, "booking.created", f"{booking['ref']} {vehicle['title']} {q['start']}→{q['end']} ${q['total']}")
    if lead_id:
        db.run("UPDATE leads SET status = 'converted', updated_at = ? WHERE id = ?", (stamp, lead_id))
    return booking


def get_booking(db: Database, ref: str) -> dict | None:
    row = db.one("SELECT b.*, v.slug AS vehicle_slug FROM bookings b JOIN vehicles v ON v.id = b.vehicle_id "
                 "WHERE b.ref = ?", (ref.strip().upper(),))
    if row:
        row["extras"] = loads(row["extras"], [])
        row["vehicle"] = get_vehicle(db, row["vehicle_id"])
    return row


def create_inquiry(db: Database, kind: str, name: str, email: str = "", phone: str = "", message: str = "",
                   vehicle_id: int | None = None, details: dict | None = None, source: str = "website",
                   lead_id: int | None = None) -> int:
    iid = db.insert("inquiries", {
        "kind": kind, "vehicle_id": vehicle_id, "name": name.strip()[:120], "email": email.strip()[:200],
        "phone": phone.strip()[:40], "message": message.strip()[:4000], "details": json.dumps(details or {}),
        "status": "new", "source": source, "lead_id": lead_id, "created_at": now_iso(),
    })
    db.log(source, "inquiry.created", f"#{iid} {kind} from {name}")
    return iid


def money(value: float | None) -> str:
    if value is None:
        return ""
    return f"${value:,.0f}" if float(value).is_integer() else f"${value:,.2f}"
