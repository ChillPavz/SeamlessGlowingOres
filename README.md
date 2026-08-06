# Seamless Glowing Ores

Resource pack. Every ore glows in the dark, with an optional connected outline that merges between
touching blocks, including across different host stones.

**This repository is private.** The built zips are public on Modrinth and CurseForge; the generator
in `tools/` is not.

| | |
|---|---|
| Minecraft | One file for 1.17 and up. The pack contains no code and declares a wide format range |
| Requires | [Fusion](https://modrinth.com/mod/fusion-connected-textures) **1.3.0 or newer**, and nothing else |
| Wiki | https://chillpavz.com/seamless-glowing-ores |
| Licence | All rights reserved, see `LICENSE` |

## Building

```
py -3.14 tools/generate_pack.py            # builds both zips into build/
py -3.14 tools/generate_pack.py --list     # prints coverage and exits
```

Pillow is required and lives on the 3.14 install. Output:

| File | Contents |
|---|---|
| `build/SeamlessGlowingOres-Glow.zip` | Emissive only |
| `build/SeamlessGlowingOres-Border.zip` | Emissive plus the 47 tile connected outline |

42 families covering 135 blocks: vanilla, Seamless Ores, Create zinc, Mythic Upgrades, Mythic
Metals, plus ancient debris as a whole block glow.

`PACK_FORMAT` is deliberately **15, not 26.2's 88**. Grepping the real client jars shows 1.20.1 knows
only `pack_format`, 1.21.1 adds `supported_formats`, and 1.21.11 adds `min_format`/`max_format`, so
every version from 1.21 up decides compatibility from the declared range and ignores the number. It
only speaks to 1.20.1 and older, where 88 made the launcher mark the pack incompatible. 15 is also
the floor of our range, and the minimum vanilla permits. 1.17 to 1.19.4 still show the warning and
cannot be fixed with one file.

## Layout

| Path | What it holds |
|---|---|
| `assets/overlays/<mod>/<family>_overlay.png` | The ore layer, blobs on transparency |
| `assets/borders/<mod>/<family>_border.png` | Hand drawn full frame border, 16x16, 60 opaque pixels |
| `assets/{overlays,borders}/<mod>/<family>_{overlay,border}<n>.png` | Numbered frames, animated families only |
| `assets/whole_block/` | Full texture copies for blocks that glow entirely, currently ancient debris |
| `assets/icon/` | Art used to build `pack.png` |
| `tools/fusion_full_layout.json` | Fusion's 47 tile layout table, recovered from the mod |
| `tools/generate_pack.py` | The generator |

Our own block ids are read from the mod's `blockstates/` directory at build time rather than
hardcoded, so the pack cannot drift out of step with the mod.

## Four things that will bite you

These each cost a debugging round. The generator now fails the build on all four.

**One missing block used to kill the whole family, vanilla ore included.** Fusion parses a
`model_modifiers` file as a unit: the first target it cannot resolve throws, the file is discarded,
and every other target in it silently loses its glow and its border. Because every vanilla family
also lists this mod's host variants, a user running only Fusion got nothing at all. The fix is
`"ignore_missing_targets": true`, which skips the unresolvable id and keeps the rest, and makes a
family whose mod is entirely absent a clean no-op with no error. It went in at Fusion 1.3.0, which
is why that is the stated minimum. `check_fusion_modifier_keys()` guards the spelling, since
Fusion would ignore a typo here as silently as it ignores any other unknown key.

**Fusion ignores unknown keys in total silence.** No warning, no log line. Writing `connection`
instead of `connections` produced a pack that looked entirely valid and quietly fell back to
same block connection. `check_fusion_keys()` validates every key and predicate type against lists
recovered from the jar. Extend those lists from the jar, never from a guess.

**The pack must never write into `assets/seamlessores/`.** That is the mod's own namespace. Writing a
connecting sheet to `seamlessores:block/<family>_overlay` silently overrode the mod's ore layer
texture, so all 40 variants rendered the 128x96 sheet stretched across the block. A resource pack
overriding mod assets is legal and unremarkable, so nothing warns you.
`assert_owns_only_its_namespace()` refuses to zip if anything lands outside `seamlessglowingores`.

**Test the built zip, not the source tree.** Two separate bugs were caught by grepping the finished
archive and neither was visible in the code.

## The 47 tile layout

`tools/fusion_full_layout.json` was produced by running Fusion's own `FullLayoutHandler.getTilePos`
over all 256 connection states, not by reading documentation and not by guessing. It is one 8 by 6
sheet, cell (7,5) unused, `y` zero is the top row.

Re-derive it the same way if Fusion ever changes the layout. It takes about a minute.

Unlike the OptiFine format this replaced, Fusion names its connection directions in its own field
names, so there is no orientation ambiguity to resolve by experiment.

## Animated ores

`ANIMATED` lists families whose art is numbered (`stormyx_overlay1.png`) instead of single files.
Stormyx is the only one so far: Mythic Metals ships it as a 16x80 texture, five frames, with
`{"animation": {"interpolate": true, "frametime": 20}}`. Those numbers are copied from the mod's own
mcmeta so our layer stays in step with the block underneath, and the owner's five overlay frames were
checked pixel for pixel against the mod's five, in order.

The connecting sheet follows Fusion's own rule, from `ConnectingTextureType.createTexture`: with
animation metadata present and no explicit frame size, one frame is `layout.width x layout.height`
tiles, so frames are whole 128x96 sheets stacked vertically. A **square** image is rejected outright
with "Image must use the 'full' layout's 6 : 8 aspect ratio to support animation", because Fusion
treats a square one as an 8x8 grid to crop. `per_tile_animation` switches to frames stacked inside
each cell instead; we use the default.

Five ore frames against a four step border cycle is handled with `math.lcm`, giving a 20 frame sheet
where frame `k` carries overlay `k%5` and border `k%4`. Both cycles keep their own period at the same
frametime, and they wrap together at the loop point so interpolation stays continuous. The `animation`
section is written beside `fusion` at the root of the mcmeta, not inside it.

## Mythic Metals

Its 21 families are built and shipped, with ids read out of `mythicmetals-0.25.3+1.21.4.jar`. The mod
is Fabric only and stops at 1.21.4, so those targets resolve on 1.21.4 and below and are skipped
everywhere else. That costs nothing now that `ignore_missing_targets` is set, and it is the reason
one file can carry them: on 26.2 they are simply inert rather than an error.

## Credits

The ore overlays are derived from Minecraft's own textures and remain Mojang's property. Zinc is
derived from Create, which is CC0 and MIT licensed. Mythic Upgrades and Mythic Metals are both MIT
licensed.

Borders and the ore art arrangement are original work by chillpavz.
