# WTTG3 Juiced Bot

[Русская инструкция](README_RU.md)

A Windows screen-based automation tool for the **Juiced** Steam achievement in *Welcome to the Game III* (WTTG3): get the maximum jackpot on the in-game Merramun slot machine.

This project was made for players who want to earn this rare achievement through normal in-game spins without manually clicking thousands of times. The bot plays using existing saves and fictional in-game currency (YoloYen). It does **not** manipulate the game's memory, change Steam achievements, edit game saves or guarantee a jackpot.

**Tested result:** Juiced was successfully unlocked during a run of this version of the bot's automation. The cumulative spin counter was 7,501 when the achievement monitor detected it. This is one result, not an estimate of the odds or a guaranteed number of spins.

## What the bot does

- Opens Merramun and attempts to verify the maximum bet of 100 YoloYen.
- Spins automatically, counts estimated actual spins separately from mouse clicks, and preserves the cumulative counter between runs.
- Clicks to accelerate recognized payout animations and protects them from the ordinary inactivity watchdog.
- Attempts to skip in-game hacks using the middle mouse button, including dark transition screens.
- Attempts to return to the computer after death using a recorded route; reloads the existing save when direct recovery fails.
- Reloads via **Continue** after MAX LOSS, insufficient funds and certain other failures. It does not use BitHit, start a new game or intentionally overwrite a save.
- Reads the local Steam achievement cache for `ACH_JUICED`. Once the unlock is detected, it stops new spins, attempts to finish the payout, saves evidence and pauses the game if the pause screen can be verified.

## Requirements and preparation

| Item | Recommended / required setting |
| --- | --- |
| Operating system | Windows; desktop Steam client |
| Python | 3.12 (64-bit), with pip and the Python launcher or Python on PATH |
| Game | Welcome to the Game III, Steam version |
| Game interface language | **Russian** (the supplied menu and prompt templates were captured in this language) |
| Game display | 1920 × 1080, visible windowed 16:9 mode; keep the entire client area on screen |
| Mouse sensitivity | 20 |
| In-game browser resolution | 1440p |
| Difficulty | Normal+ was used during development; other difficulties are unverified |
| Save | Existing playable save with access to Merramun and sufficient YoloYen for a 100 bet; about 20,000 YoloYen was used for the original setup |

Make a backup of your own game save before starting. No game save is included in this repository. Prepare your funds and unlock any required in-game access yourself; this bot does not earn starting funds or complete the story. Use the default movement controls or record your own route. Keep Steam running on the account on which you want the achievement.

## Quick start

1. Download or clone this repository. If using GitHub's **Code → Download ZIP**, extract the entire archive into a writable folder. Keep `templates/` and `route.json` next to `bot.py`.
2. Install [Python 3.12](https://www.python.org/downloads/) for Windows if it is not already installed. Enable the Python launcher / PATH option during installation.
3. Double-click `install.bat` and wait for `Installation complete`. This creates a local `.venv` and installs OpenCV, NumPy and MSS. You only need to do this once per installation. Internet access is required for the dependency installation.
4. Start WTTG3 through Steam, apply the settings above and return to its main menu. Your prepared save must be available via **Continue** (`Продолжить`).
5. Double-click `start.bat`. The console should show `WTTG3 SAVE100 v0.2.3-S7 RESPAWN RECOVERY ready`.
6. Switch focus to the game. Press **F5** and check the console for `Juiced status: LOCKED`. If it says `UNKNOWN`, troubleshoot Steam cache detection before unattended farming. If it says `UNLOCKED`, the achievement is already present and the bot will refuse to start farming.
7. With the game focused in its main menu, press **F9**. The bot attempts to load the save, enter the computer, open Merramun and raise the stake to 100. Watch the first recovery cycle to verify that it works with your save.

**Already at the in-game computer desktop?** F9 can start there too. F8 starts farm-only mode from the desktop, without automatic save reloads. Use F9 for unattended recovery and reloading.

**Important:** the bot controls the real mouse and keyboard while the game is focused. Do not use the computer for other tasks while it runs, minimize the game or cover it with other windows. Press **F10** to stop. After farming stops, press **F12** to close the program. Do not assume that a game pause is successful until the console confirms it.

## The included route and recording your own

`route.json` contains a working route captured from the original save's wall-facing spawn. The bot checks a small grayscale image of the room before replaying input, so it will not blindly apply the route to a different room or camera angle. The recording is specific to the original movement controls, sensitivity, spawn position and orientation; it is **not universal**.

If you spawn somewhere else, or the bot keeps standing at the wall and reloading:

1. Stop farming (F10). Manually load your save with **Continue**.
2. If a hack is active, skip it manually by pressing the middle mouse button. Wait for normal gameplay to return.
3. While standing at your usual spawn, before turning or moving, press **F7**. A beep confirms recording started.
4. Walk to your desk, sit down and enter the computer until the normal in-game desktop is visible. Keep the game focused the entire time.
5. Press **F6** on that desktop. A double beep and `route.json saved` confirm success. If the desktop is not recognized, finish entering the computer or skip the hack and press F6 again.
6. Return to the main menu or stay at the desktop and press F9.

If death returns you to the same spawn position and camera direction, **one route is enough**: this version uses `route.json` as the fallback respawn route. If the death spawn is different, use **F4** at the recognized skeleton-wall respawn before moving, repeat steps 4–5, and the bot will save `respawn_route.json`. Keep these two routes separate when their starting positions differ.

## Hotkeys

| Key | Action |
| --- | --- |
| F4 | Begin recording a route from the recognized death/respawn room |
| F5 | Diagnostic screen, state, route-match scores and Steam achievement status |
| F6 | Save the active route when the in-game desktop is visible |
| F7 | Begin recording a route from the normal saved-game spawn |
| F8 | Farm-only mode; start from the in-game computer desktop |
| F9 | Full automation, including save loading and recovery |
| F10 | Stop current automation or cancel recording |
| F12 | Exit the bot when idle; use F10 first if farming |

## What happens when Juiced unlocks?

The bot discovers the local Steam cache for App ID `3869850` and the active account, reads the achievement schema, and checks the bit for `ACH_JUICED`. No Steam Web API key or account ID needs to be entered under normal circumstances. The cache is read-only from the bot's perspective.

After detecting the achievement, the bot tries to finish any recognized payout without starting another spin, saves evidence, and attempts to pause the game. It does not reload the save after detecting the unlock.

Results are written to a new folder under `jackpot_evidence/JUICED_<timestamp>/`, typically containing:

- `report.json` — estimated spin count, detected unlock time and whether payout completion and pause were verified.
- `previous_frames/` — a rolling history of recent game frames (up to 120 seconds and 96 MiB of compressed frames).
- `unlock_detected.png` — screen at detection time.
- `highest_observed_balance.jpg` and `largest_observed_balance_increase.jpg` — when recognized; these are visual balance estimates, not proof of a particular jackpot amount.
- `after_payout_before_pause.png` and `final_screen.png` — where available.

Steam can update its local cache with a delay, and screen capture can miss a brief MAX WIN animation. The saved frames, estimated balances and exact pause result are not guaranteed. Verify the achievement in Steam yourself. If the monitor cannot read the cache, it logs a warning and keeps farming; automatic unlock stopping is unavailable until the cache is readable again.

## Troubleshooting

| Symptom | What to do |
| --- | --- |
| `Run install.bat first` or dependency error | Install Python 3.12, then run `install.bat` again. |
| `Continue button not recognized` | Use Russian interface text, make sure the prepared save exists and the main menu is visible. Never choose New Game/Play to fix this. |
| `Saved spawn not detected` / recovery exhausted | Check sensitivity, controls and spawn direction; re-record the route using F7 and F6. |
| Cannot detect Merramun or stake 100 | Check game/browser resolution, keep Merramun fully visible and close overlapping in-game windows if needed. |
| `Game lost focus` | Focus the game again, ensure the client is fully visible, and restart with F9. |
| `Juiced status: UNKNOWN` | Check that Steam is running, WTTG3 has been launched on the correct account and the achievement cache exists. Run F5 again. |
| `Juiced is already unlocked` | The bot intentionally refuses to farm once this achievement is detected. Check Steam if unexpected. |
| Bot behaves unexpectedly | Press F10, inspect `logs/bot.log` and the diagnostic PNG files created by F5. Reproduce only while watching the game. |

For advanced Steam cache troubleshooting, the bot supports an optional local `steam_achievement.json` in the project folder. Only use it if automatic discovery fails, particularly on systems with multiple accounts. Set `stats_file` to your own actual WTTG3 cache file, e.g.:

```json
{
  "stats_file": "C:/Program Files (x86)/Steam/appcache/stats/UserGameStats_YOUR_ACCOUNT_ID_3869850.bin"
}
```

Replace `YOUR_ACCOUNT_ID` with the real account-specific part of the filename. The corresponding `UserGameStatsSchema_3869850.bin` must be in the same folder. Do not upload either cache file or this personal configuration to GitHub.

## Project files and privacy

- `bot.py` — automation, safety checks and recovery state machine.
- `vision.py`, `stake.py`, `templates.json`, `templates/` — screen recognition; the small reference PNGs are essential runtime assets, **not** the original gameplay screenshots.
- `winio.py`, `fastclick.py` — Windows capture, simulated input, route recording and clicking.
- `spin_counter.py` — estimated spin counting and local persistence.
- `achievement_watch.py` — read-only Steam cache detection and evidence capture.
- `route.json` — bundled spawn route; `respawn_route.json` is optional and can be created locally.
- `install.bat`, `start.bat`, `requirements.txt`, `config.json` — setup and launch.

Local folders `logs/`, `jackpot_evidence/`, `.venv/` and `__pycache__/`, plus `spin_stats.json` and account-specific Steam settings, are excluded from this distribution and ignored by Git. They are created locally when needed. Review diagnostic screenshots and logs for private information before sharing them. Do not delete `templates/`.

## Limitations

This is an unofficial, screen-based tool built for the original Windows setup. Different game versions, interface languages, resolutions, control bindings, save positions or performance can break detection or navigation. There is no guarantee of a jackpot, exact spin count, correct handling of every event, immediate achievement detection or successful pause. Keep backups and use at your own discretion.

Welcome to the Game III and its in-game artwork belong to their respective owners. This project is not affiliated with the game's developer or Valve. It is intended solely for the single-player achievement using fictional in-game currency.
