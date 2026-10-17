# Manual end-to-end test checklist

Target machine: Windows 11, RTX 3060 12 GB. Unit tests cannot cover GPU, Ollama or Tinder, so run this before each release.

## 1. Installation & first run
- [ ] 1.1 Unzip the Release zip and run `SwipeBuddy.exe` (no Python installed). *Expected: the first-run dialog appears with the disclaimer.*
- [ ] 1.2 Verify the zip against the `.sha256` file (`Get-FileHash -Algorithm SHA256`). *Expected: hashes match.*
- [ ] 1.3 With Ollama not installed, continue the dialog. *Expected: clear instruction and link to ollama.com; the check fails gracefully.*
- [ ] 1.4 Install Ollama and re-check. *Expected: the check passes.*
- [ ] 1.5 GPU check. *Expected: shows RTX 3060 and 12 GB VRAM, no low-VRAM warning.*
- [ ] 1.6 Start the model download, cancel midway, then restart it. *Expected: the progress bar moves; cancel works; the download resumes.*
- [ ] 1.7 Detector weights download. *Expected: progress shown; the file ends up under `%LOCALAPPDATA%\SwipeBuddy\models`.*
- [ ] 1.8 Finish setup and restart the app. *Expected: the setup dialog is skipped and the main window opens.*

## 2. Browser & login
- [ ] 2.1 Press Start. *Expected: an Edge window opens on tinder.com.*
- [ ] 2.2 Log in manually. *Expected: the app never asks for credentials; the state becomes ready once profiles show.*
- [ ] 2.3 Close the app and start it again. *Expected: still logged in.*
- [ ] 2.4 (Optional) Set `browser_channel` to `chromium`. *Expected: Playwright's Chromium is downloaded with progress if needed.*

## 3. Suggest mode
- [ ] 3.1 Start in Suggest mode. *Expected: decision, score bar and reason appear per profile.*
- [ ] 3.2 Wait without pressing anything. *Expected: no swipe happens.*
- [ ] 3.3 Press Like, then Pass, then Skip. *Expected: Like/Pass swipe in the browser; Skip moves on without a swipe being counted.*
- [ ] 3.4 Observe photo browsing. *Expected: photos are paged with Space; time per profile is reasonable (about 3-10 s).*
- [ ] 3.5 Check GPU load in Task Manager. *Expected: VRAM about 8-9 GB, no overflow.*

## 4. Thresholds & preferences
- [ ] 4.1 Change the threshold from 70 to 95, then to 10, on similar profiles. *Expected: decisions flip accordingly.*
- [ ] 4.2 Edit preferences (free text, toggles) and save. *Expected: persisted in `config.json`; effects visible in the reasons.*
- [ ] 4.3 Enter an invalid value (e.g. delay max below min). *Expected: clamped on save.*

## 5. Auto mode & pacing
- [ ] 5.1 Switch to Auto. *Expected: a confirmation dialog is required.*
- [ ] 5.2 Run about 20 profiles. *Expected: delays between 3 and 9 s (defaults) are noticeable; a longer pause occurs after 15-30 swipes.*
- [ ] 5.3 Observe a pause. *Expected: the status shows a pause.*

## 6. Daily cap
- [ ] 6.1 Set `daily_cap` to 3 and run Auto. *Expected: stops after exactly 3 swipes with a cap message.*
- [ ] 6.2 Restart the app and press Start. *Expected: it refuses to continue (the cap persists).*
- [ ] 6.3 Check `stats.json`. *Expected: counts only.*

## 7. Popups / edge cases
- [ ] 7.1 Get a match. *Expected: the "It's a match" modal is dismissed, or the engine pauses and says so.*
- [ ] 7.2 Other upsell dialogs. *Expected: dismissed, or the engine stops and asks for you.*
- [ ] 7.3 Run until out of profiles. *Expected: the engine reports no profiles and stops cleanly.*
- [ ] 7.4 CAPTCHA/verification (if it occurs). *Expected: the engine stops with a "needs you" message and does not try to bypass it. Solve it manually and restart.*
- [ ] 7.5 Log out in the browser mid-run. *Expected: the engine stops with a logged-out message.*
- [ ] 7.6 Stop Ollama mid-run. *Expected: no crash; profiles are passed or the engine reports an error.*

## 8. Stop / restart
- [ ] 8.1 Press Stop during a delay. *Expected: stops immediately.*
- [ ] 8.2 Press Stop while waiting for confirmation in Suggest mode. *Expected: stops immediately.*
- [ ] 8.3 Close the window while running. *Expected: clean exit, no orphan Edge or SwipeBuddy processes.*
- [ ] 8.4 Start again after a stop. *Expected: works.*

## 9. Privacy verification
- [ ] 9.1 Open the newest file in `%LOCALAPPDATA%\SwipeBuddy\logs`. *Expected: only event names and counts; no names, texts or reasons.*
- [ ] 9.2 Count image files:
  `Get-ChildItem $env:LOCALAPPDATA\SwipeBuddy -Recurse -Include *.png,*.jpg,*.jpeg,*.webp | Measure-Object`
  *Expected: Count is 0 (or only files that are not from profiles; compare with the list before the session).*
- [ ] 9.3 Check the `browser-profile` folder size before and after a 50-profile session. *Expected: stays small (no photo cache growth).*
- [ ] 9.4 Network: open Resource Monitor > Network while running. *Expected: only Edge to tinder.com/CDNs, SwipeBuddy to 127.0.0.1:11434, and model downloads during setup.*
- [ ] 9.5 Confirm `OLLAMA_DEBUG` is not set (`echo $env:OLLAMA_DEBUG`). *Expected: empty.*

## 10. Packaging
- [ ] 10.1 Copy the Release zip to a clean machine without Python and unzip it. *Expected: `SwipeBuddy.exe` starts.*
- [ ] 10.2 Run `Start-Process .\SwipeBuddy.exe -ArgumentList --selftest -Wait -PassThru` and check `ExitCode`. *Expected: 0.*
- [ ] 10.3 Icon and window title look right in the taskbar and Explorer.

## Tinder DOM verification

If the state is "unknown", photos are not captured or swipes do nothing, inspect the live page in the browser dev tools and check `src/swipebuddy/tinder_selectors.py`:
- [ ] `TINDER_URL` and `APP_PATH_PREFIX` still match the real URLs.
- [ ] `CARD_SELECTORS` match the profile card element.
- [ ] `PHOTO_SELECTORS` match the visible photo(s) in the card.
- [ ] `BLOCKED_SELECTORS` match CAPTCHA/verification screens (the engine must stop).
- [ ] `MODAL_SELECTORS` match dialogs (match, upsell), and Escape (`KEY_DISMISS`) closes them.
- [ ] Keys `KEY_LIKE` (ArrowRight), `KEY_PASS` (ArrowLeft) and `KEY_NEXT_PHOTO` (Space) still work on the real site.
- [ ] Logged-out and "no more profiles" markers are still detected.
