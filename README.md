# Seamless Glowing Ores

Resource pack. Every ore glows in the dark, with an optional connected outline that merges between
touching blocks, including across different host stones.

**This repository is private.** The built zips are public on Modrinth and CurseForge; the generator
in `tools/` is not.

| | |
|---|---|
| Minecraft | One file. **Publish it tagged 1.21 and up**; it loads far older but see the note below |
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

44 families covering 135 blocks: vanilla, Seamless Ores, Create zinc, Mythic Upgrades, Mythic
Metals, plus ancient debris as a whole block glow. That is 42 distinct ores; banglum and starrite
each split into two families because Mythic Metals draws some of their hosts differently, and a
sprite is what a connecting texture is keyed on.

Split families share a `group`, so both halves declare the same connection block list and still join
into one outline across the boundary even though they are drawn from different art. `overlay_file`
and `border_file` let the second half reuse its sibling's border.

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

## Which versions to actually publish for

The file is version agnostic, so this is a tagging decision rather than a build one, and it can be
widened later without touching the zip.

**Tag 1.21.x, 26.1.x and 26.2.** That is roughly three quarters of the audience by Modrinth download
share (Fabric API: 1.21.x 52%, 26.1.x 18%, 26.2 4%) and it is the range with no caveats at all.

**1.20.x is about 16 to 20%, and 1.20.1 alone is 15%, but it comes with two.** On Fabric there it
needs Indium or nothing renders, and the Indium render path flickers. Worth adding later with both
written on the page; not worth leading with.

**1.19 and below is about 4% combined, and that figure is lifetime downloads, so the live share is
lower still.** It also cannot be made to stop reporting as incompatible: one `pack_format` integer
cannot match several eras and `min_format` may not go below 15. Leave it untagged.

## Stormyx is not animated, and that is settled

Mythic Metals animates that ore through five frames at frametime 20. Matching it was attempted twice
and abandoned, so stormyx is an ordinary family: one overlay, one border, one still frame each.

An animated `full` connecting sheet is not viable. `ConnectingTextureType.createTexture` rebuilds the
metadata as `frameWidth = w / layout.getWidth()`, `frameHeight = h / layout.getHeight()`, then crops
each tile with `ImageHelper.createCropFramed`, whose output is `tile * imageWidth / frameWidth` by
`tile * imageHeight / frameHeight`. For a 128x1920 sheet that is either a 16x16 frame, giving every
tile a 128x1920 sprite read as an 8 by 120 grid (960 junk frames, 47 times over), or with
`per_tile_animation` a 16x320 frame, where the per tile crop is 128x96 and `96 / 320` truncates to
zero rows. The empty frame list then reaches `SpriteContents$Ticker`, which does `% frames.size()`
and crashes the game on load.

A plain, non-connecting Fusion texture CAN animate: `VanillaTextureType.createTexture` calls
`SpriteImageSource.vanilla(image, animationMetadata)`, and `FusionSpriteContents` carries the frame
list and its own ticker. So an animated glow is mechanically available; only the bordered sheet is
closed off. That route is written up under "an animated third variant" below rather than retried
here, since stormyx itself is settled.

## An animated third variant, if it is ever wanted

Feasible for the **Glow style only**, and the blocker is art rather than code.

The mechanism is available: a plain Fusion texture keeps vanilla's `animation` section, so an N frame
vertical strip per family plus `{"animation": {...}}` beside the `fusion` section is all the format
needs. The bordered style can never join in, for the reason above.

The cost is N frames per family across 44 families, which is not worth hand drawing. The cheap route
is to DERIVE the frames from the single overlay each family already has, for example a brightness or
saturation cycle, so no new art is needed and the result stays our own work. Every ore would pulse in
the same rhythm, which is a different look from hand authored shimmer; judge that on one family in
game before generating all 44.

For reference, packs that do animate ores generally replace the block's texture outright rather than
appending a layer, which is why vanilla animation just works for them. That trade gives up everything
the append design buys: other packs showing through underneath, one overlay per ore instead of one
per ore per host, and a file measured in tens of kilobytes rather than thousands of PNGs.

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
