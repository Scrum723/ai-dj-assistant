# Tales from the Storm Cellar — Story Visualizer v2 (longer + more reactive)

Vanilla JS + Canvas2D. Works offline, nothing to install. v2 builds on Soul's v1 (the original is still in `/workspace/visualizer/`).

## What's new in v2
- **Longer stories:** each Story Show scene now runs about **4 min** by default, roughly one song. Change it with the *Scene length* slider (0.5–15 min), `?sceneMinutes=`, or `,` / `.`. Each act has 3 sub-phases ("part 1/3"). Across a scene, the camera drift changes direction, the palette hue wanders, and zoom builds, so long scenes keep changing. Beats nudge the story forward only a little now. A detected drop still jumps to the climax.
- **Deeper audio analysis** (`js/audio.js`): six bands (sub/bass/low-mid/mid/high/air), each with a smoothed value and a peak envelope. Spectral flux is computed separately for low, mid and high. It detects **kick, snare and hat** onsets with adaptive thresholds, estimates BPM, and tracks beat phase. It also tracks the energy trend, detects **buildups**, and detects **drops** (bass coming back after a breakdown or a buildup). Optional **auto-gain** adjusts to any input level.
- **Reactive layer over every scene:** kicks cause zoom punches, a brightness pulse, a light camera shake and expanding rings. Snares cause lightning flickers (through the strobe-safe limiter). Highs and hats make shimmer sparkles. Buildups bring rising rain streaks, a darker vignette, more saturation and creeping zoom. Drops trigger a big flash, a shockwave and a particle burst. The **Reactivity** slider (0–3) scales all of it.
- **Debug meter** (key **D** or `?debug=1`): six band bars with peak ticks, KICK/SNARE/HAT/BEAT lights, BPM with beat phase, buildup %, flux, auto-gain level and a DROP! marker.

## Mac quick start
1. With the Chief backend running, open `http://127.0.0.1:8000/visualizer/` in Chrome (served by the app — do not use `python3 -m http.server`).
2. **Recommended:** pick your **DJ controller / audio interface** in the device dropdown → *Use input* → allow the mic. No BlackHole needed. (`127.0.0.1` is a secure context; a LAN IP is not, so mic capture will fail there.)
3. **Optional alternative:** install **BlackHole 2ch**. In **Audio MIDI Setup**, click + → *Create Multi-Output Device*, then tick your speakers/headphones **and** BlackHole 2ch (put speakers first and set them as the clock source). Choose that Multi-Output device as the output in macOS or in djay, then pick **BlackHole 2ch** in the visualizer dropdown → *Use input*.
4. Press **D** to check that the meters move. OBS: use **Window Capture** of the Chrome window (H hide UI, F fullscreen) for live audio; or a **Browser Source** at `http://127.0.0.1:8000/visualizer/?ui=0` (demo/file audio only — OBS browser sources generally cannot open mic inputs).
You can also drop any audio file onto the page, or click **Demo tone** (a synthetic 128 BPM test signal).

## Keys
F fullscreen · H hide/show UI · ←/→ prev/next · 1–9, 0 pick scene (Shift = 11–20) · R RANDO-TRON · S Story · X Shuffle · L Loop · P projection · T title cards · ↑/↓ sensitivity · **[ / ] reactivity** · **, / . scene length ∓30 s** · **A auto-gain** · **D debug meter** · ? help

## URL params
`sceneMinutes=4` · `reactivity=1.2` (0–3) · `sens=1` · `autogain=0` · `debug=1` · `scene=N|R` · `t=seconds` (fast-forward) · `demo=1` · `ui=0` · `notitle=1`
Example: `http://127.0.0.1:8000/visualizer/?sceneMinutes=5&reactivity=1.8&debug=1`

Strobe-safe is ON by default: no more than 3 full-screen flashes per second, flashes capped in brightness, and camera shake reduced.
