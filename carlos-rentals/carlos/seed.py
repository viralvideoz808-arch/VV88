"""Sample inventory so the site and the agents have something to work with on day one.

Every row is flagged sample=1: the website shows a "sample listing" badge on it and the console
reminds you to replace it with the real fleet (Inventory → Edit, or delete and add your own).
"""

from __future__ import annotations

from carlos.db import Database
from carlos.inventory import save_vehicle

RENTALS = [
    dict(make="Toyota", model="Corolla", year=2025, trim="LE", body="sedan", daily_rate=49, weekly_rate=294, deposit=200,
         seats=5, mpg="32 city / 41 hwy", color="Celestite Gray", color_hex="#7f8c99", mileage=8200,
         features=["Apple CarPlay & Android Auto", "Adaptive cruise control", "Lane keep assist", "Backup camera"],
         description="The easy, efficient choice for getting around town or a weekend trip. Great on gas, easy to park."),
    dict(make="Toyota", model="Camry", year=2025, trim="SE Hybrid", body="sedan", daily_rate=62, weekly_rate=372,
         deposit=250, seats=5, fuel="Hybrid", mpg="51 combined", color="Midnight Black", color_hex="#1d2026",
         mileage=6400, features=["Hybrid — 50+ mpg", "Wireless charging", "Blind-spot monitor", "Apple CarPlay"],
         description="Comfortable midsize hybrid with room for five and luggage. Ideal for business trips."),
    dict(make="Honda", model="CR-V", year=2025, trim="EX AWD", body="suv", daily_rate=72, weekly_rate=432, deposit=300,
         seats=5, mpg="28 city / 34 hwy", color="Radiant Red", color_hex="#a3161f", mileage=11800,
         features=["All-wheel drive", "39 cu ft cargo", "Heated seats", "Honda Sensing safety suite"],
         description="Roomy compact SUV with all-wheel drive — family trips, airport runs and everything in between."),
    dict(make="Jeep", model="Wrangler", year=2024, trim="Sport S 4x4", body="suv", daily_rate=95, weekly_rate=570,
         deposit=400, seats=5, mpg="17 city / 25 hwy", color="Firecracker Red", color_hex="#c62828", mileage=15300,
         features=["4x4", "Removable top", "Trail rated", "Apple CarPlay"],
         description="Top off, music up. The beach-day, adventure-weekend Jeep everyone asks about."),
    dict(make="Chevrolet", model="Tahoe", year=2025, trim="LT", body="suv", daily_rate=145, weekly_rate=870,
         deposit=500, seats=8, mpg="15 city / 20 hwy", color="Summit White", color_hex="#e9ecef", mileage=9100,
         features=["Seats 8", "Third row", "Rear entertainment-ready", "Tow package"],
         description="Full-size, 8-seat SUV for big groups, events and family vacations."),
    dict(make="Tesla", model="Model 3", year=2024, trim="Long Range", body="sedan", daily_rate=89, weekly_rate=534,
         deposit=400, seats=5, fuel="Electric", mpg="~340 mi range", color="Pearl White", color_hex="#f4f4f2",
         mileage=14200, features=["Electric — no gas stops", "Autopilot", "Supercharger access", "Glass roof"],
         description="All-electric, quick and quiet. Charging tips and a cable come with every rental."),
    dict(make="Ford", model="Mustang", year=2024, trim="EcoBoost Convertible", body="convertible", daily_rate=115,
         weekly_rate=690, deposit=500, seats=4, doors=2, mpg="20 city / 29 hwy", color="Grabber Blue",
         color_hex="#1565c0", mileage=7300, features=["Power soft top", "Digital cluster", "B&O sound", "Sport mode"],
         description="Convertible Mustang for birthdays, date nights, photo shoots — or just because."),
    dict(make="Chrysler", model="Pacifica", year=2024, trim="Touring L", body="van", daily_rate=99, weekly_rate=594,
         deposit=400, seats=7, mpg="19 city / 28 hwy", color="Granite Crystal", color_hex="#4a4f55", mileage=18800,
         features=["Seats 7", "Stow 'n Go seating", "Power sliding doors", "Rear climate"],
         description="The family-trip minivan: sliding doors, tons of room and comfy for long drives."),
    dict(make="Ford", model="F-150", year=2024, trim="XLT SuperCrew 4x4", body="truck", daily_rate=109,
         weekly_rate=654, deposit=500, seats=5, mpg="18 city / 23 hwy", color="Agate Black", color_hex="#202124",
         mileage=16900, features=["4x4", "5.5 ft bed", "Tow hitch", "Pro Power Onboard"],
         description="Moving day, home-improvement runs or towing — America's pickup, ready to work."),
    dict(make="Nissan", model="Kicks", year=2025, trim="SV", body="hatchback", daily_rate=45, weekly_rate=270,
         deposit=200, seats=5, mpg="31 city / 36 hwy", color="Boulder Gray", color_hex="#8d9196", mileage=5100,
         features=["Great on gas", "Apple CarPlay", "Automatic emergency braking", "Easy parking"],
         description="Our best-value ride. Compact, efficient and perfect for city driving."),
]

FOR_SALE = [
    dict(make="Honda", model="Accord", year=2021, trim="EX-L", body="sedan", sale_price=22900, mileage=54200,
         color="Platinum White", color_hex="#ecebe6", mpg="30 city / 38 hwy",
         features=["Leather", "Sunroof", "Heated seats", "Clean title", "One owner"],
         description="Well-kept Accord EX-L with leather and sunroof. Service records available."),
    dict(make="Toyota", model="RAV4", year=2020, trim="XLE AWD", body="suv", sale_price=24500, mileage=61800,
         color="Blueprint", color_hex="#24466b", mpg="27 city / 34 hwy",
         features=["AWD", "Power liftgate", "Toyota Safety Sense", "Clean title"],
         description="The do-everything SUV with AWD and Toyota reliability."),
    dict(make="BMW", model="3 Series", year=2019, trim="330i", body="sedan", sale_price=21800, mileage=58900,
         color="Mineral Gray", color_hex="#5c6166", mpg="26 city / 36 hwy",
         features=["Sport seats", "Navigation", "LED headlights", "Premium package"],
         description="Sharp 330i with the premium package. Drives like it should."),
    dict(make="Ford", model="F-150", year=2022, trim="XLT 4x4", body="truck", sale_price=36900, mileage=41700,
         color="Iconic Silver", color_hex="#b5b8bb", mpg="18 city / 24 hwy",
         features=["4x4", "Tow package", "Bed liner", "Remote start"],
         description="Clean XLT 4x4 with tow package — ready for work and weekends."),
    dict(make="Mercedes-Benz", model="C-Class", year=2018, trim="C 300", body="sedan", sale_price=19500,
         mileage=67400, color="Obsidian Black", color_hex="#16181b", mpg="24 city / 33 hwy",
         features=["Leather", "Panoramic roof", "Burmester-ready", "Keyless go"],
         description="Classic C 300 luxury at a used-car price."),
]


def seed_sample_inventory(db: Database, force: bool = False) -> int:
    if not force and db.scalar("SELECT COUNT(*) FROM vehicles"):
        return 0
    n = 0
    for i, car in enumerate(RENTALS):
        save_vehicle(db, {"listing": "rent", "status": "available", "featured": int(i < 6), "sample": 1, **car})
        n += 1
    for i, car in enumerate(FOR_SALE):
        save_vehicle(db, {"listing": "sale", "status": "available", "featured": int(i < 3), "sample": 1,
                          "deposit": 0, **car})
        n += 1
    db.log("system", "seed", f"{n} sample vehicles added")
    return n
