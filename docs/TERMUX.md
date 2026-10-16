# Run VV88 on an Android phone (Termux)

The whole system (clipping engine, autopilot and the control website) runs on the phone. You open the
website in the phone's browser at `http://127.0.0.1:8088`.

## What you need

- Termux from **F-Droid** or the Termux GitHub releases. The Play Store version is outdated and can't
  install packages.
- About 3 GB free space, Wi-Fi, and the phone on a charger for the first setup (it compiles a few parts).

## 1. Unpack

Save `VV88.zip` to your phone's **Download** folder, then in Termux:

```bash
termux-setup-storage          # tap "Allow" so Termux can read Download
pkg install -y unzip
cd ~ && unzip -o ~/storage/downloads/VV88.zip
```

## 2. Set up (one time, 20-40 minutes)

```bash
cd ~/VV88 && bash scripts/termux_setup.sh
```

It installs Python, FFmpeg and build tools, compiles the Python packages that have no Android build, and
builds **whisper.cpp**, the speech-to-text engine used for captions on phones. It also downloads the `base`
model and writes a phone-tuned `config.yaml`. For faster but rougher captions, use the `tiny` model:
`VV88_WHISPER_MODEL=tiny bash scripts/termux_setup.sh`. It ends with `vv88 doctor`; every line should
say `ok`, except optional ones such as OpenCV.

## 3. Start

```bash
cd ~/VV88 && bash scripts/termux_start.sh
```

It prints the website address and password and opens the browser. Stop it with
`bash scripts/termux_stop.sh`. Logs are in `data/web.log`.

## Linking your accounts from the phone

Open **Connections** on the website. **Connect** opens the platform's own sign-in page in the phone's
browser and comes back to `http://127.0.0.1:8088/oauth/callback`. This works because the website and the
browser are on the same phone. Register that exact address in each developer app (see `docs/CONNECT.md`),
or use the Postiz key instead. Nothing is posted until you switch the publishing backend from
`dry_run` on the Settings page.

## Keep it running

- Allow the Termux notification. `termux_start.sh` takes a wake lock so it keeps working with the
  screen off.
- Settings > Apps > Termux > Battery: set it to **Unrestricted**. Otherwise Android may stop it at night.
- Turn on **Autopilot** on the dashboard. It runs discovery, clipping and posting on a timer while the
  website is running.

## Good to know

- Phones render more slowly than PCs. Press **Benchmark** on the Settings page to see how many clips a
  day this phone can make.
- Face-tracked reframing needs OpenCV. If Termux has no OpenCV package for your phone, clips use a
  blurred-background centre crop instead.
- To update: unzip the new `VV88.zip` over the old folder with the same `unzip -o` command. Your
  `config.yaml`, `campaigns.yaml`, `.env` (keys and password) and `data/` (clips, database) are not in
  the zip, so they're kept.
