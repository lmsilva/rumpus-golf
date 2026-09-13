# Changelog
Rendered on the "What's new" screen (S21). Newest first. Line prefixes: `new:`, `improved:`, `fixed:`. If this file has no releases, hide the "What's new" entry in Settings.

## v0.4.1 — 2026-09-12 · Latest
- fixed: Settings Camera tab stayed on the mock sensor after picking a real webcam — the startup scan was locking DirectShow devices so the C920 open failed and fell back to mock.
- improved: Settings layout matches the design (rail / 860 px cards / theme preview at left 1288). Apply camera applies the selected device without leaving the page.
- improved: Camera picker lists each device by its Windows driver name (HD Pro Webcam C920, Iriun Webcam, OBS Virtual Camera, …) instead of Camera 0/1/2.
- improved: Light / dark / auto theme now applies immediately and restyles glass, scrims and the letterbox.
- improved: Keyboard navigation is spatial (arrows move to the nearest control) and keeps a visible mint focus ring; Esc/Back works on every screen that shows it.
- new: Favicon (mint cup + player dots).
- fixed: Settings screen did not rebuild after a theme or volume change, so the selected option looked stuck.

## v0.4.0 — 2026-09-12 · Latest
- new: Regular 2D webcam support — no Kinect required. The floor is mapped with a 4-corner homography instead of a depth plane.
- new: Color-only tracking — balls by hue mask, obstacles and the cup by frame differencing against an empty-floor reference.
- new: Settings — Camera tab (device, resolution, backend), Game rules tab (holes, stroke cap, OOB penalty, tunnel bonus), Players and About tabs.
- improved: Settings is now tabbed; Credits and What's new are reachable from the About tab and the side rail.
- fixed: Out-of-bounds penalty can be toggled from Settings.

## v0.3.0 — 2026-09-12 · Latest
- new: Obstacles — scan the floor, confirm / delete / reshape footprints, draw missed ones; balls behind them read "hidden", not lost; mid-game add/ignore prompt.
- new: Xbox controller support with on-screen glyph hints; keyboard hints return when the pad disconnects.
- new: Settings — dark / light / auto theme, music playlist and volumes, announcer voice.
- improved: Long player names scroll instead of clipping in the HUD and scorecard.
- fixed: Ball resting against the cup no longer counted as holed on Kinect v1.

## v0.2.0 — 2026-08-30 · Courses
- new: Three course templates (Hallway, Dogleg Left, Gauntlet) with drag-to-nudge obstacle outlines.
- new: Up to 4 players, 1–9 holes, running scorecard between holes.
- fixed: Out-of-bounds exit point drawn at the last in-bounds position, not the last seen one.

## v0.1.0 — 2026-08-09 · First putt
- new: Floor calibration, play area, start and hole zones, two-ball tracking, one-hole game.
