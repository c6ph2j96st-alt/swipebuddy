# SwipeBuddy

A local, privacy-first Windows assistant that reads profile photos on tinder.com in a browser it controls, lets a local vision-language model (VLM) rate them against *your own* preferences, and suggests (or, in Auto mode, performs) a swipe.

> ## DISCLAIMER - read this first
>
> - **Automating Tinder violates Tinder's Terms of Use.** Your account can be warned, shadow-limited or **permanently banned**. Use of this software is **entirely at your own risk**.
> - SwipeBuddy is **not affiliated with, endorsed by or connected to Tinder or Match Group.** "Tinder" is a trademark of its owner.
> - **You are responsible for respecting other people's privacy.** Photos of other people are processed **only locally** on your computer, are held in memory only, and are **never stored or shared**. Do not use this tool in ways that harm, harass or discriminate against others.
> - The software is provided "as is", without warranty of any kind (see [LICENSE](LICENSE)).

## Features

- Two modes: **Suggest** (default; you confirm every swipe) and **Auto** (requires an explicit confirmation).
- Runs a local VLM through [Ollama](https://ollama.com) (default `qwen2.5vl:7b`); nothing is sent to a cloud service.
- Person detection (YOLOX-Nano, ONNX, CPU) crops the person before judging.
- Your preferences: free text plus toggles (glasses, tattoos, beard, smiling, outdoors, ...).
- Score threshold, daily cap, randomised delays and periodic pauses.
- Stops and hands over to you on CAPTCHA, verification or any unknown page state.
- Statistics are counts only (likes/passes per day).
- Persistent browser profile (Microsoft Edge): you log in once, manually.

## How it works

```
 Edge (Playwright) --screenshots of profile card (RAM only)--+
                                                             v
                                    person detector (ONNX, CPU) -> crop
                                                             |
                                                             v
                          local VLM via Ollama (127.0.0.1:11434) -> {decision, score, reason}
                                                             |
                                                             v
                                  policy: like only if valid AND score >= threshold
                                                             |
                    Suggest mode: you press Like / Pass / Skip   Auto mode: used directly
                                                             |
                                  pacer (random delay, pauses, daily cap)
                                                             |
                                       arrow-key swipe in the browser + counter update
```

Images are never written to disk and are released before the next profile. The VLM's reasoning is shown in the GUI only and is never logged.

## Privacy guarantees

Everything lives in `%LOCALAPPDATA%\SwipeBuddy`:

| Item | Content |
|---|---|
| `config.json` | Your settings and preferences |
| `stats.json` | Counts only (likes/passes per day) |
| `logs\` | Event names and integer counts only (a whitelist filter blocks everything else) |
| `browser-profile\` | The Edge profile (your login session); browser disk cache is disabled |
| `models\` | Downloaded detector weights |

- No profile photos, names, texts or VLM reasoning are stored or logged.
- **No telemetry.** The only network traffic: `tinder.com` (in the browser), `127.0.0.1` Ollama, and model downloads from the `ollama.com` registry (VLM) and GitHub (detector weights). The Ollama URL is restricted to localhost.
- The app never reads or writes login forms or passwords.
- Do **not** set `OLLAMA_DEBUG` - debug logging may record request contents.

## Requirements

- Windows 10 or 11
- NVIDIA GPU with 8 GB VRAM or more recommended (12 GB comfortable for the 7B model); CPU works but is slow
- About 8-9 GB disk space including Ollama and the model
- Microsoft Edge (preinstalled on Windows 11); otherwise Playwright's Chromium is downloaded on first start
- [Ollama](https://ollama.com) for Windows

## Install

1. Download `SwipeBuddy-vX.Y.Z-win64.zip` (and the `.sha256` file to verify it) from the **Releases** page.
2. Unzip and run `SwipeBuddy.exe`.
3. Follow the first-run dialog: accept the disclaimer, install Ollama from ollama.com if it is missing, and let the model download finish.

## Usage

1. Press **Start**. An Edge window opens on tinder.com. **Log in yourself** - the app never handles passwords. Your session is kept in the profile.
2. Set your preferences in the Preferences tab and save.
3. **Suggest mode:** for each profile the decision, score and reason are shown; press Like, Pass or Skip.
   **Auto mode:** decisions are executed after a randomised delay. It needs a confirmation when enabled.
4. **Threshold** (0-100): a like requires a valid "like" verdict with a score at or above it.
5. **Daily cap** stops the engine for the day (it survives restarts). **Delays and pauses** are randomised within the configured ranges.
6. Press **Stop** at any time; it also interrupts a running delay.

## Configuration (`config.json`)

| Field | Default | Meaning |
|---|---|---|
| `threshold` | `70` | Minimum score (0-100) for a like |
| `mode` | `"suggest"` | `"suggest"` or `"auto"` |
| `daily_cap` | `100` | Max swipes per day (1-1000) |
| `delay_min_s` / `delay_max_s` | `3.0` / `9.0` | Random delay before each swipe (min 1 s) |
| `pause_every_min` / `pause_every_max` | `15` / `30` | Swipes between longer pauses |
| `pause_min_s` / `pause_max_s` | `60` / `240` | Length of a pause in seconds |
| `vlm_model` | `"qwen2.5vl:7b"` | Ollama model name |
| `ollama_url` | `"http://127.0.0.1:11434"` | Localhost only |
| `browser_channel` | `"msedge"` | `"msedge"` or `"chromium"` |
| `max_images_per_profile` | `6` | Photos captured per profile (1-10) |
| `disclaimer_accepted` | `false` | Set by the first-run dialog |
| `preferences.free_text` | `""` | Free-text preferences (max 2000 chars) |
| `preferences.attributes` | see right | Booleans: `glasses_ok`, `tattoos_ok`, `beard_ok`, `smoking_ok`, `group_photos_ok` (default true); `prefer_smiling`, `prefer_outdoors`, `prefer_sporty` (default false) |
| `preferences.age_hint_min` / `age_hint_max` | `null` | Age hint passed to the VLM as text |

Invalid values are clamped or reset to defaults on load.

## Troubleshooting

- **Nothing happens / "unknown state":** Tinder changed its layout. Update the selectors in `src/swipebuddy/tinder_selectors.py` (see `TESTING.md`).
- **Ollama not running:** start Ollama (system tray) and re-run the setup check; verify that `http://127.0.0.1:11434` responds.
- **Very slow:** the GPU is not used. Check `nvidia-smi`, update the NVIDIA driver, or switch to `qwen2.5vl:3b`.
- **CAPTCHA or verification:** the app stops. Solve it yourself in the browser and press Start again.
- **Logged out:** log in again in the browser window.

## Development

```
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
pytest
```

Run from source with `set PYTHONPATH=src` and then `python -m swipebuddy` (or `pip install -e .`). `--selftest` checks imports and window creation.

Build: `pyinstaller build.spec --noconfirm --clean` (output in `dist\SwipeBuddy`), then `dist\SwipeBuddy\SwipeBuddy.exe --selftest`.

Release: push a tag `vX.Y.Z`; the workflow tests, builds, self-tests, zips, checksums and publishes. Run `python tools/scan_personal_data.py` before every commit.

## Licenses

- This project: MIT (see [LICENSE](LICENSE)).
- YOLOX-Nano weights: Apache-2.0 (Megvii)
- Qwen2.5-VL via Ollama: model license as stated on its model card
- PySide6: LGPLv3
- Playwright: Apache-2.0
- onnxruntime: MIT

## Svenska

SwipeBuddy är ett lokalt Windows-program som läser profilbilder på tinder.com i en webbläsare, låter en lokal AI-modell (via Ollama) bedöma dem mot dina egna preferenser och föreslår eller utför en swipe.

- **Varning:** Att automatisera Tinder strider mot Tinders användarvillkor och kontot kan bli avstängt. Använd på egen risk. Inte kopplat till Tinder/Match Group.
- **Integritet:** Bilder hanteras bara lokalt i minnet och sparas eller delas aldrig. Ingen telemetri. Bara antal sparas (inga bilder, namn eller motiveringar). Respektera andras integritet.
- **Installera:** ladda ner zip från Releases, packa upp, kör `SwipeBuddy.exe`, följ första-start-dialogen och installera Ollama från ollama.com.
- **Använd:** logga in själv i webbläsarfönstret (appen hanterar aldrig lösenord). Förslag-läge är standard. Vid CAPTCHA stannar appen och du tar över.
- **Krav:** Windows 10/11, helst NVIDIA-GPU med minst 8 GB VRAM, cirka 8-9 GB disk, Microsoft Edge.
