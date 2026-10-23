"""The Learn narrator worker (plan "Wiser weekly content engine" §1.8) — a standalone Railway cron
service. Nothing in this package imports `app.*`; heavy imports live in `child.py` and the engine
`render` functions only. Entry points: `python -m learn_narrator.main` (the cron tick),
`python -m learn_narrator.child` (the render/align children), `python -m learn_narrator.bench`."""
