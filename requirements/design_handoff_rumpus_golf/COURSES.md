# Courses

Three templates authored for a 3.0 × 2.0 m play area. Coordinates are **percent of the play-area bounding box** (x across, y toward the camera). At runtime, scale to the user's polygon; clamp obstacles inside it. All obstacles are household objects the player places; the app shows an outline (ghost) per obstacle on the feed and ticks it when depth sees a blob of roughly that footprint (± 40%) inside the outline for 1 s.

| Level | Name | Par | Start (x,y) | Hole (x,y) | Needs | Special rule |
|---|---|---|---|---|---|---|
| Beginner | The Hallway | 2 | 12,50 | 88,50 | 1 hardback book, 1 cushion | Cushion is a bumper; ricochets legal |
| Advanced | Dogleg Left | 3 | 10,82 | 88,16 | 3 hardback books, paper-towel roll cut lengthwise, 1 shoebox | Ball through the tunnel = −1 stroke bonus |
| Expert | The Gauntlet | 4 | 8,50 | 91,50 | 2 shoeboxes, 3 books (stepped ramp), 1 hand towel, 1 cushion | Towel is a hazard: stopping on it = +1, play on |

Obstacle geometry is in `data/courses.json` (x, y, w, h in %, kind, label, item, realSize). Kinds: book (wall), soft (cushion/shoebox), tube (tunnel), hazard (towel), ramp.

Rendering in the course editor: outline polygons projected through the floor→color mapping (same transform as zones). Label above each outline (Outfit 700 26 px). Start circle r 70, "START"; hole circle r 42 mint, "Cup goes here".

Top-down map (S06 cards, course sheets): 3:2 box; book = #f2efe8, soft = #6b7280, tube = #c9c4b8, hazard = coral 45° stripes (#ff6b57 / #b9483a, 4 px), start ring 11% wide, hole dot 7% wide mint.
