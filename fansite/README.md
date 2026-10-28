# Uwanja Fans

A mobile-first football fan site for Kenyan Premier League and EPL supporters. It's a static
site with no build step and no server.

- **Fixtures**: upcoming matchdays with kick-off times.
- **Results**: full-time scores, newest matchday first.
- **Table**: standings computed from results (P, W, D, L, GD, points, last-five form).
- **Predict**: a free score predictor league. An exact score earns 3 points and a correct
  result earns 1. Picks and your fan name are saved in the browser.
- **Penalties**: a penalty shootout against a rival club from the same league. Tap a spot in
  the goal to aim, then tap Shoot to stop the power needle. Too soft gets saved, too hard
  goes over or wide, and a top-corner shot in the green band beats even a keeper who guessed
  right. Keep shooting the same way and the keeper starts reading you. It's best of five,
  then sudden death, and your record (played, won, goals, conversion) is saved in the browser.
- **Leaderboard**: your points ranked against sample fans.

Fixtures are sample data generated from a fixed seed. The season is anchored to the current
date, so the next matchday is always the coming Saturday. The "Play next matchday (demo)"
button fills in results so you can see picks being scored. "Reset demo" clears them.

## Run it

```bash
cd fansite
python3 -m http.server 8080
# open http://localhost:8080
```

Opening `index.html` directly also works.

## Going live

To use real fixtures, replace `buildLeague()` in `app.js` with data from a football data API.
To get a shared leaderboard, store predictions on a small backend instead of `localStorage`.
Any static host can serve the folder as-is (GitHub Pages, Netlify, Vercel).
