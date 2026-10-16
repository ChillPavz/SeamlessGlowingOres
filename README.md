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
py -3.14 tools/generate_pack.py                     # builds all eight zips into build/
py -3.14 tools/generate_pack.py --pack animated     # only the animated pair
py -3.14 tools/generate_pack.py --families diamond  # only the named families, for a pilot
py -3.14 tools/generate_pack.py --list              # prints coverage and exits
```

Pillow is required and lives on the 3.14 install. Output:

| File | Contents |
|---|---|
| `build/SeamlessGlowingOres-Glow.zip` | Emissive only |
| `build/SeamlessGlowingOres-Border.zip` | Emissive plus the 47 tile connected outline |
| `build/SeamlessAnimatedOres-Glow.zip` | Emissive, with the glint |
| `build/SeamlessAnimatedOres-Border.zip` | Emissive plus a connected outline, both with the glint |
| `build/SeamlessSubtleOres-Glow.zip` | Emissive at half strength |
| `build/SeamlessSubtleOres-Border.zip` | Emissive plus the connected outline, both at half strength |
| `build/SeamlessPulsingOres-Glow.zip` | Emissive, breathing |
| `build/SeamlessPulsingOres-Border.zip` | Emissive plus a connected outline, both breathing |

`build/` holds whatever was built last, and a `--families` pilot leaves a partial zip there. Build
in full before handing a zip to anyone.

198 families covering 1,622 blocks: vanilla, every Seamless Ores and Seamless Ores: Matrix variant, and the ores of Cobblemon,
Create, Create: New Age, Create: TFMG, Dense Mekanism, Energized Power, Exp Ores, Extreme Reactors,
Ice and Fire, Immersive Engineering, Meadow, Mekanism, Modern Industrialization, Mystical
Agriculture, Mythic Metals, Mythic Upgrades, Occultism, Oreganized, Powah, Silent Gear, Silent's Gems, Tech
Reborn and Things, plus ancient debris as a whole block glow. Run `--list` for the breakdown; the
numbers here come from it.

**A family covers the source mod's ore AND Seamless Ores' variants of it, and those two are matched
differently.** The source mod's blocks are listed outright; ours are discovered from the mod's own
blockstates across every branch folder. The mod names a variant `<host>_<ore>_ore`, and that `<ore>`
is not the family key: keys are prefixed per mod so `mek_lead` and `tfmg_lead` stay apart, and the
mod itself prefixes an ore whose plain name another supported mod already took, which is where
`silents_silver` and `energized_tin` come from. `SO_SUFFIX` maps the two together. **A family missing
from that map silently covers none of our blocks** - the pack still builds and the source mod's ore
still glows, so `--list` is the only thing that catches it.

Seamless Ores: Matrix's variants are discovered the same way, from the `seamlessoresmatrix-*` folders
beside this one. Its ids are `<stonemod>_<stone>_<ore>_ore` with the full ore name, and its hosts are
read from the mod's own host table, so a stone added there reaches the pack on the next build.

Banglum and starrite each spread across several families, because Mythic Metals draws some of their
hosts differently and a sprite is what a connecting texture is keyed on. Those five are also
deliberately not connected to each other: blocks that look different should not merge into one
outline, and none of these pairs can meet anyway. `overlay_file` and `border_file` point a family at
art whose name is not `<family>_<kind>.png`, which is how the two starrite host variants share one
overlay and how all of them reuse a sibling's border.

`PACK_FORMAT` is deliberately **15, not 26.2's 88**. Grepping the real client jars shows 1.20.1 knows
only `pack_format`, 1.21.1 adds `supported_formats`, and 1.21.11 adds `min_format`/`max_format`, so
every version from 1.21 up decides compatibility from the declared range and ignores the number. It
only speaks to 1.20.1 and older, where 88 made the launcher mark the pack incompatible. 15 is also
the floor of our range, and the minimum vanilla permits. 1.17 to 1.19.4 still show the warning and
cannot be fixed with one file.

One file also serves two drawings of the same ore where a mod redrew it between versions. Mekanism
and Dense Mekanism draw their tin ore differently on 1.20.1, so `LEGACY_ART` puts the old drawing in
the pack root, which is all 1.20.1 reads, and the current one in a pack overlay, `current_art/`,
declared in `pack.mcmeta` for resource pack format 18 (1.20.2) and up. The overlay replaces files and
never adds one, and the build refuses to zip if it would. Immersive Engineering redrew nickel and
uranium after its 1.20.1 build (1.20.4 and 1.21.1 have the new drawing), so those two go through
`LEGACY_ART` the same way, borders included, because the colours moved too.

Meadow's alpine salt ore is the one block an appended layer cannot reach. It has two weighted model
variants with the salt in different places, and Fusion appends per block, so the pack overrides
Meadow's blockstate (`SALT_BLOCKSTATE`) with the same list, order and weights, which leaves the game's
pick unchanged, and points each entry at a Fusion `composite` of Meadow's own model plus the glow
layer. Meadow named and drew those models differently on 1.20.1, so the composites and their textures
split by era like `LEGACY_ART`, while the blockstate is written once. `verify_salt_against_meadow()`
refuses to build if a Meadow jar in `../references/jars` lists anything else, and
`assert_owns_only_its_namespace()` allows that one file by exact path and nothing else. Without Fusion
the composites cannot load, so on Forge and NeoForge the salt ore shows the missing model; on Fabric
the composite's `parent` lets vanilla draw Meadow's own model.

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

**Tag 1.21.x, 26.1.x, 26.2 and 26.3.** That is roughly three quarters of the audience by Modrinth
download share (Fabric API: 1.21.x 52%, 26.1.x 18%, 26.2 4%) and it is the range with no caveats at
all. 26.3 needed no change to the file: its client jar reads the same pack metadata fields as 26.2,
so `min_format` 15 and `max_format` 999 already cover its resource format 97.1, and Fusion 1.3.15
supports 26.3 on Fabric and NeoForge.

**1.20.x is about 16 to 20%, and 1.20.1 alone is 15%, but it comes with two.** On Fabric there it
needs Indium or nothing renders, and the Indium render path flickers. Worth adding later with both
written on the page; not worth leading with.

**1.19 and below is about 4% combined, and that figure is lifetime downloads, so the live share is
lower still.** It also cannot be made to stop reporting as incompatible: one `pack_format` integer
cannot match several eras and `min_format` may not go below 15. Leave it untagged.

## Ores their own mod animates

Where the source mod animates an ore, the glow plays the same frames in step with it. `ANIMATED`
holds the timing, copied from each mod's own mcmeta rather than chosen: Mythic Metals' stormyx (five
frames at frametime 20) and unobtainium (four at 60), Silent's Gems' chaos ore (four at 10), and
Extreme Reactors' yellorite, anglesite and benitoite, whose custom frame order is read from the
mcmeta kept beside the overlay. An overlay with several frames and no `ANIMATED` entry fails the
build, since it would otherwise be stitched as one tall still image.

In the Border file such a family appends two models instead of one: a plain plate carrying the
animated glow, and a connecting sheet carrying only the border, still. Every other family keeps the
single composited sheet. None of these plates set `interpolate`: that path crashed on world entry
through Fusion's sprite pipeline (an empty frame list in `SpriteContents$Interpolation`), and leaving
it out costs only the cross fade between frames.

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
list and its own ticker. So an animated glow is mechanically available; only the `full` bordered
sheet is closed off. The animated pair below uses that route, with a `pieced` border instead.

## The animated pair

The animated files are the same families and the same art, with a glint: a soft band grows out from
the middle of each face to its corners, lifting only pixels the art already draws toward white, then
the ore rests. Nothing new is drawn; `glint_frames()` derives the frames at build time. 12 sweep
frames at frametime 2 (1.2 s), then the plain art held for 40 ticks as one stored frame, so the rest
costs no texture memory. The shape is the same from every side, which a diagonal sweep is not on a
top face seen from the far side. They ship in their own namespace, `seamlessanimatedores`, so either
pair can sit beside the other without overwriting it.

Families whose mod already animates the ore (`ANIMATED`) keep that animation and the `full` border.
Every other animated border uses Fusion's `pieced` layout, one border only sheet per glint frame:
`full` makes 47 animated sprites per family and measured +5.4 GB of native memory on 26.2, `pieced`
makes five. A pieced face is split into quarter quads that lose the depth test against the block's
own face by view angle, so whole runs of outline vanish as the camera moves; `lifted_cube()` grows the
border cube by `BORDER_LIFT` (0.003 of a block) on every side, which clears it. Ancient debris and the
alpine salt glint as well.

## The subtle and pulsing pairs

**Subtle** (`seamlesssubtleores`): every generated layer drawn `translucent` with its alpha scaled to
`SUBTLE_ALPHA` (50, the owner's pick from 35, 50 and 65), still emissive. In the dark the ore shows half
of full bright; in daylight the ore beneath is lit anyway. Fusion's element `light_emission` was tried
first: its appended layers drop the level, and L 9, 11 and 12 rendered unlit on 26.3 Fabric and NeoForge
while the game's own `light_emission` on a plain model lit the block.

**Pulsing** (`seamlesspulsingores`): the animated pair's machinery with `pulse_frames()` in place of the
glint: `PULSE_STEPS` (9) stored frames from `PULSE_FLOOR` (20%) to full alpha on a half cosine, played up
and back down through the frame list at `PULSE_FRAMETIME` (4 ticks): 3.2 s a breath, every ore in step.
Never `interpolate`. Families whose mod animates the ore keep that animation at full strength.

Measured on 26.3 NeoForge, Border variants, process memory over no pack: Glowing +44 MB, Subtle +91 MB,
Pulsing +208 MB. Use one of the packs at a time: two stacked append two layers to every ore.

## Mythic Metals

Its 21 families are built and shipped, with ids read out of `mythicmetals-0.25.3+1.21.4.jar`. The mod
is Fabric only and stops at 1.21.4, so those targets resolve on 1.21.4 and below and are skipped
everywhere else. That costs nothing now that `ignore_missing_targets` is set, and it is the reason
one file can carry them: on 26.2 they are simply inert rather than an error.

## Credits

The ore overlays are derived from Minecraft's own textures and remain Mojang's property. Zinc is
derived from Create, which is CC0 and MIT licensed. Mythic Upgrades and Mythic Metals are both MIT
licensed.

The Ice and Fire overlays are drawn from [Ice and Fire Community Edition](https://www.curseforge.com/minecraft/mc-mods/ice-and-fire-community-edition)'s
ore textures (LGPL-3.0).

The Immersive Engineering overlays (aluminum, lead, nickel, silver and uranium) are derived from
[Immersive Engineering](https://modrinth.com/mod/immersiveengineering)'s ore textures and are used
with the permission of BluSunrize.

The alpine salt glow is derived from [Let's Do Meadow](https://modrinth.com/mod/lets-do-meadow)'s
salt art and is used with the permission of Satisfy. Meadow's other alpine ores use the vanilla
overlays, and the alpine salt outline is original.

Borders and the ore art arrangement are original work by chillpavz.
