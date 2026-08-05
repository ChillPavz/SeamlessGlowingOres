# Seamless Glowing Ores

Resource pack. Every ore glows in the dark, with an optional connected outline that merges between
touching blocks, including across different host stones.

**This repository is private.** The built zips are public on Modrinth and CurseForge; the generator
in `tools/` is not.

| | |
|---|---|
| Minecraft | 26.2 |
| Requires | [Fusion](https://modrinth.com/mod/fusion-connected-textures), and nothing else |
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

20 ore families covering 74 blocks: vanilla, Seamless Ores, Create zinc, Mythic Upgrades.

## Layout

| Path | What it holds |
|---|---|
| `assets/overlays/<mod>/<family>_overlay.png` | The ore layer, blobs on transparency |
| `assets/borders/<mod>/<family>_border.png` | Hand drawn full frame border, 16x16, 60 opaque pixels |
| `assets/icon/` | Art used to build `pack.png` |
| `tools/fusion_full_layout.json` | Fusion's 47 tile layout table, recovered from the mod |
| `tools/generate_pack.py` | The generator |

Our own block ids are read from the mod's `blockstates/` directory at build time rather than
hardcoded, so the pack cannot drift out of step with the mod.

## Three things that will bite you

These each cost a debugging round. The generator now fails the build on all three.

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

## Not built

`MYTHIC_METALS_PENDING` lists 20 families whose art is finished and in `assets/`, but Mythic Metals
is Fabric only and stops at 1.21.4, so there are no 26.2 block ids to target or verify. When that
port lands, add the ids; the art needs no changes.

## Credits

The ore overlays are derived from Minecraft's own textures and remain Mojang's property. Zinc is
derived from Create, which is CC0 and MIT licensed. Mythic Upgrades is MIT licensed.

Borders and the ore art arrangement are original work by chillpavz.
