from datetime import date, timedelta

import pytest

from carlos.inventory import QuoteError, create_booking, get_vehicle, list_vehicles, quote


def car(db, slug="2025-toyota-corolla-rent"):
    return get_vehicle(db, slug)


def d(n):
    return (date.today() + timedelta(days=n)).isoformat()


def test_weekly_rate_applies_to_full_weeks(db):
    q = quote(db, car(db), d(1), d(10))  # 9 days = 1 week + 2 days
    assert q["days"] == 9
    assert q["base"] == 294 + 2 * 49
    assert q["saved"] == 9 * 49 - q["base"]
    assert q["total"] == round(q["base"] * 1.07, 2)


def test_extras_young_driver_and_tax(db):
    q = quote(db, car(db), d(1), d(4), extras=["gps", "delivery", "bogus"], driver_age=23)
    assert [x["key"] for x in q["extras"]] == ["gps", "delivery"]
    assert q["extras_total"] == 7 * 3 + 45
    assert q["fees"] == 25 * 3
    assert q["tax"] == round((147 + 66 + 75) * 0.07, 2)


@pytest.mark.parametrize("start,end,age,msg", [
    (-1, 2, None, "past"), (3, 3, None, "after"), (1, 90, None, "limited"), (1, 3, 19, "at least 21")])
def test_quote_rejects_bad_requests(db, start, end, age, msg):
    with pytest.raises(QuoteError, match=msg):
        quote(db, car(db), d(start), d(end), driver_age=age)


def test_sale_car_cannot_be_rented(db):
    with pytest.raises(QuoteError):
        quote(db, car(db, "2021-honda-accord-sale"), d(1), d(2))


def test_double_booking_blocked_but_back_to_back_allowed(db):
    v = car(db)
    create_booking(db, v, d(5), d(8), "A", "a@example.com")
    with pytest.raises(QuoteError, match="already booked"):
        create_booking(db, v, d(7), d(9), "B", "b@example.com")
    create_booking(db, v, d(8), d(10), "C", "c@example.com")  # pickup on the return day
    create_booking(db, v, d(2), d(5), "D", "d@example.com")


def test_cancelled_bookings_free_the_dates(db):
    v = car(db)
    b = create_booking(db, v, d(5), d(8), "A", "a@example.com")
    db.run("UPDATE bookings SET status = 'cancelled' WHERE id = ?", (b["id"],))
    assert quote(db, v, d(5), d(8))["available"]


def test_list_filters(db):
    suvs = list_vehicles(db, listing="rent", body="suv")
    assert suvs and all(v["body"] == "suv" and v["listing"] == "rent" for v in suvs)
    cheap = list_vehicles(db, listing="sale", max_price=22000)
    assert cheap and all(v["sale_price"] <= 22000 for v in cheap)
    assert all(v["seats"] >= 7 for v in list_vehicles(db, min_seats=7))
    assert [v["make"] for v in list_vehicles(db, q="tesla")] == ["Tesla"]
