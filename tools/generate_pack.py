#!/usr/bin/env python3
"""
Builds the Seamless Glowing Ores resource pack(s) in FUSION format.

    py -3.14 tools/generate_pack.py                    # build the six published files
    py -3.14 tools/generate_pack.py --pack pulsing     # the unpublished Pulsing pair, on request
    py -3.14 tools/generate_pack.py --pack animated    # only the animated pair
    py -3.14 tools/generate_pack.py --families diamond # only the named families (a pilot)
    py -3.14 tools/generate_pack.py --list             # print coverage and exit

Six published files from one family table: SeamlessGlowingOres-{Glow,Border}, still,
SeamlessSubtleOres-{Glow,Border} at half strength, and SeamlessAnimatedOres-{Glow,Border}, the same art
with a GLINT (plus SeamlessPulsingOres-{Glow,Border}, breathing, kept but not published): every ore layer is a generated
animation in which a band of light grows out across the ore's own speck pixels, then rests. Nothing
new is drawn; the frames are derived from the overlay art at build time (see glint_frames).

This replaces the OptiFine/Continuity build entirely (kept, unpublished, in
../seamless-glowing-ores-old). Fusion is one mod covering fabric/forge/neoforge/quilt and MC
1.12 -> 26.2, and depends on nothing but the loader - no Sodium, no Indium, no Fabric API and no
Sinytra Connector, which is what made the old dependency chain the single biggest source of user
confusion in this pack category.

WHY THE FUSION BUILD IS STRUCTURALLY BETTER, NOT JUST BETTER SUPPORTED
---------------------------------------------------------------------
1. MODEL MODIFIERS *APPEND* A LAYER instead of replacing a block's model:

       {"targets": ["minecraft:iron_ore", ...], "append": ["<ns>:block/iron_overlay_model"]}

   The OptiFine build had to RE-MODEL every ore onto one shared sprite, because Continuity connects
   by sprite and different sprites can never join a group. That cost every vanilla ore its own
   shading and gave deepslate ores the stone tier's blob colours. Here the host block keeps its own
   texture untouched and we simply add a layer on top, so nothing is lost.

2. CONNECTION IS DECLARED BY BLOCK LIST, not implied by a shared sprite:

       "connection": {"type": "match_block", "blocks": [...every block in the family...]}

   Continuity had no equivalent - `connect=` only offered block/tile/state - and working around it
   is the entire reason the old build existed in the shape it did.

3. EMISSIVE AND CONNECTING LIVE IN THE SAME `<texture>.png.mcmeta`, and the `full` layout is ONE
   8x6 SHEET rather than 47 separate tiles plus 47 `_e` twins. The old build emitted ~2160 files;
   this one emits a couple per family.

Everything above was verified by reading fusion.jar and the author's own packs (fusion-emissive-ores
and fusion-connected-blocks) - not from documentation.
"""

import argparse
import io
import json

import os
import re
import shutil
import sys
import zipfile

PACK_NAME = "Seamless Glowing Ores"
# TWO namespaces, and keeping them apart is not cosmetic.
#
# MOD_NAMESPACE is only ever used to NAME BLOCKS (seamlessores:granite_iron_ore) and to find the
# mod's blockstates. PACK_NAMESPACE is where every file this pack ships lives.
#
# The first build put the pack's assets in `seamlessores` too, which silently overwrote the MOD's
# own `seamlessores:block/<ore>_overlay` textures - the ore layer every variant's model points at.
# A resource pack overrides mod assets, so all 40 variants rendered the 128x96 connecting SHEET
# stretched across the block. A pack must never write into a mod's namespace unless it intends to
# retexture it, and here we intend the opposite: model_modifiers APPEND a layer, so the host block's
# own textures must be left completely alone.
MOD_NAMESPACE = "seamlessores"
PACK_NAMESPACE = "seamlessglowingores"

# The two packs this generator builds. The animated pair keeps a namespace of its own, so either
# pair can sit beside the other in a pack list without one silently overwriting the other's files.
# use_pack() switches PACK_NAME, PACK_NAMESPACE and GLINT before each build.
PACKS = {
    "glowing": dict(name="Seamless Glowing Ores", namespace="seamlessglowingores",
                    zip="SeamlessGlowingOres", glint=False),
    "animated": dict(name="Seamless Animated Ores", namespace="seamlessanimatedores",
                     zip="SeamlessAnimatedOres", glint=True),
    # SUBTLE: the same layers at part strength. The emissive layer is drawn TRANSLUCENT with its alpha
    # scaled, so in the dark it shows a share of full bright, and in daylight (the ore beneath already
    # lit) it looks like the ore. Fusion's element `light_emission` was the first plan, but its appended
    # layers drop the level on the way to the quad (spike, 16 Oct 2026: L 9/11/12 rendered unlit on 26.3
    # Fabric and NeoForge, while vanilla's own light_emission on a plain model lights the block).
    "subtle": dict(name="Seamless Subtle Ores", namespace="seamlesssubtleores",
                   zip="SeamlessSubtleOres", glint=False, effect="subtle"),
    # PULSING: the glint pack's machinery (an animated plate, and pieced border sheets that animate
    # with it), with frames whose ALPHA breathes between a floor and full on a sine, drawn translucent.
    # In daylight the ore beneath is already lit, so it reads as the ore; in the dark it breathes.
    # Never `interpolate` (it crashes Fusion sprites): stored steps played up and down instead.
    # NOT PUBLISHED (owner, 16 Oct 2026: tried in game, not kept for now; more steps may be worth a
    # second look, at a texture memory cost). Built only on request: --pack pulsing.
    "pulsing": dict(name="Seamless Pulsing Ores", namespace="seamlesspulsingores",
                    zip="SeamlessPulsingOres", glint=True, effect="pulse", publish=False),
}
PACK = "glowing"
GLINT = False
EFFECT = None
# The Subtle pack's strength: the alpha its emissive layers are drawn at, in percent. Owner's pick from
# the spike's shots (35, 50, 65).
SUBTLE_ALPHA = int(os.environ.get("SGO_SUBTLE_ALPHA", "50"))


def use_pack(key):
    global PACK, PACK_NAME, PACK_NAMESPACE, GLINT, EFFECT
    PACK = key
    PACK_NAME = PACKS[key]["name"]
    PACK_NAMESPACE = PACKS[key]["namespace"]
    GLINT = PACKS[key]["glint"]
    EFFECT = PACKS[key].get("effect")


def faded(path):
    """Scale a written texture's alpha to SUBTLE_ALPHA percent, in place (the Subtle pack)."""
    from PIL import Image
    image = Image.open(path).convert("RGBA")
    alpha = image.getchannel("A").point(lambda a: a * SUBTLE_ALPHA // 100)
    image.putalpha(alpha)
    image.save(path)

# pack_format is 15, NOT 26.2's own 88, and that is deliberate.
#
# Which field a given Minecraft version reads was checked by grepping the real client jars:
#   1.20.1   knows ONLY pack_format
#   1.21.1   knows pack_format + supported_formats
#   1.21.11+ knows pack_format + supported_formats + min_format/max_format
# So every version from 1.21 up decides compatibility from the RANGE and ignores this number, and
# the only versions it still speaks to are 1.20.1 and older. At 88 those reported the pack as
# incompatible (it still worked, but the launcher marked it red); at 15 it matches 1.20 and 1.20.1
# exactly. 15 is also the floor of our declared range, so the legacy field now agrees with it.
#
# 26.x validates that pack_format sits inside [min_format, max_format] and is >= 15
# (PackFormat$IntermediaryFormat.validatePackFormatForRange), which 15 satisfies. SuperMartijn642's
# own Fusion Emissive Ores pack ships exactly this combination for its 1.20-1.21.8 file.
#
# 1.17 to 1.19.4 still report incompatible, and cannot be fixed: one integer cannot match several
# eras, and min_format may not go below 15 ("Multi-version packs cannot support minimum version of
# less than 15"). The pack still loads there.
PACK_FORMAT = 15

# Art that changed under the same block ids, served by version from one file. Mekanism redrew its tin
# ore between its 1.20.1 build and the next one (1.20.4; it has none for 1.20.2 or 1.20.3), and Dense
# Mekanism's 1.20.1 tin is the old drawing too. So the pack root carries the OLD art, which is all
# 1.20.1 can read, and a pack overlay swaps the current art in from 1.20.2 up: resource pack format 18
# is the first that reads the `overlays` section, and 1.20.1 ignores that section entirely.
#
# The overlay entry carries both range spellings for the same reason the pack section does: 1.20.2 to
# 1.21.x read `formats`, and 1.21.11+ read `min_format` / `max_format` and validate them like the
# pack's own range. Only textures differ between the eras, so the overlay replaces files and never
# adds one; build() refuses to zip if it would.
LEGACY_ART = {
    # family: (old overlay file, old border file), both in the family's own art folder
    "mek_tin": ("old_tin_overlay", "old_tin_border"),
    "dmek_tin": ("old_dense_tin_overlay", "old_dense_tin_border"),
    # Immersive Engineering redrew nickel and uranium after its 1.20.1 build: 1.20.4 and 1.21.1 carry
    # the new drawing, read from all three jars. The colours moved too, so the borders split as well.
    "ie_nickel": ("nickel_overlay_old", "nickel_border_old"),
    "ie_uranium": ("uranium_overlay_old", "uranium_border_old"),
}
CURRENT_ART_DIR = "current_art"
CURRENT_ART_FORMATS = (18, 999)
# Seamless Ores branches whose copy of a LEGACY_ART overlay is the old drawing (the drift check).
LEGACY_BRANCHES = {"1.20.1"}

MC_JAR = os.path.expanduser("~/.gradle/caches/neoformruntime/artifacts/minecraft_26.2_client.jar")

# Ore families. `art` is the subfolder under assets/{overlays,borders}/ holding
# <family>_overlay.png and <family>_border.png. `targets` is every block that should receive the
# appended layer - vanilla, ours, and third parties - and it doubles as the connection list, so
# every block in a family connects to every other by construction.
#
# OUR OWN BLOCKS ARE NOT LISTED HERE. They are discovered from the mod's blockstates folder at build
# time (see mod_block_targets), so the two can never drift apart.
VANILLA = "vanilla"
FAMILIES = {
    "coal":     dict(art=VANILLA, targets=["minecraft:coal_ore", "minecraft:deepslate_coal_ore"]),
    "iron":     dict(art=VANILLA, targets=["minecraft:iron_ore", "minecraft:deepslate_iron_ore"]),
    "copper":   dict(art=VANILLA, targets=["minecraft:copper_ore", "minecraft:deepslate_copper_ore"]),
    "gold":     dict(art=VANILLA, targets=["minecraft:gold_ore", "minecraft:deepslate_gold_ore"],
                    hosts="overworld"),
    "lapis":    dict(art=VANILLA, targets=["minecraft:lapis_ore", "minecraft:deepslate_lapis_ore"]),
    # Born in Chaos' infected diamond is a trap dressed as diamond ore (its models draw the game's own
    # diamond textures, on 1.20.1 and 1.21.1 alike), so it glows and connects as diamond: the disguise
    # holds. Seamless Ores knows its tuff variant's ore as infected_diamond, hence the extra suffix.
    "diamond":  dict(art=VANILLA, targets=["minecraft:diamond_ore", "minecraft:deepslate_diamond_ore",
                                           "born_in_chaos_v1:infected_diamond_ore",
                                           "born_in_chaos_v1:infected_deepslate_diamond_ore"],
                     also_suffixes=["infected_diamond"]),
    "emerald":  dict(art=VANILLA, targets=["minecraft:emerald_ore", "minecraft:deepslate_emerald_ore"]),
    "redstone": dict(art=VANILLA, targets=["minecraft:redstone_ore", "minecraft:deepslate_redstone_ore"]),
    # Nether gold is its own family: different art, different hosts, and it never touches the
    # overworld gold it would otherwise be grouped with.
    "nether_gold": dict(art=VANILLA, targets=["minecraft:nether_gold_ore"], suffix="gold", hosts="nether"),
    "quartz":      dict(art=VANILLA, targets=["minecraft:nether_quartz_ore"]),
    # Create. Mod id and namespace are `create` for every Create variant (Create Fly included).
    "zinc":     dict(art="create", targets=["create:zinc_ore", "create:deepslate_zinc_ore"]),
}

# Blocks that glow as a WHOLE BLOCK instead of getting an ore overlay and a border.
#
# Ancient debris is the only one, and it is a deliberate exception rather than an oversight: its
# texture is its own artwork rather than netherrack with blobs painted on top (133 of 256 pixels
# differ from netherrack, against 33 for nether gold), so a derived overlay would cover half the
# block and hide the host stone. Owner's call: the whole texture glows, and it never gets a border,
# in either variant. The OptiFine build did the same thing and the Fusion rewrite simply lost it.
#
# It is a cube_column, verified from the vanilla model on both 1.20.1 and 26.2:
# parent block/cube_column, `end` = ancient_debris_top, `side` = ancient_debris_side. So the
# appended model needs end/side rather than the `all` every ore family uses.
#
# The art in assets/whole_block/ is a verbatim copy of Mojang's texture, which is what "the whole
# thing glows" requires. Checked pixel-identical across 1.20.1, 1.21.1, 1.21.11 and 26.2, so one
# copy serves the entire range this single file covers.
WHOLE_BLOCK_GLOW = {
    "ancient_debris": dict(
        targets=["minecraft:ancient_debris"],
        parent="block/cube_column",
        textures={"end": "ancient_debris_top", "side": "ancient_debris_side"},
    ),
}

# Mythic Upgrades (MIT, 26.2 on all four loaders, namespace `mythicupgrades`). Block ids read from
# the real jar: 5 overworld gems have stone+deepslate pairs, ruby/sapphire are nether-only,
# ametrine/jade are End-only.
for _gem in ("aquamarine", "citrine", "peridot", "topaz", "necoium"):
    FAMILIES[_gem] = dict(art="mythic upgrades",
                          targets=[f"mythicupgrades:{_gem}_ore",
                                   f"mythicupgrades:deepslate_{_gem}_ore"])
for _gem in ("ruby", "sapphire", "ametrine", "jade"):
    FAMILIES[_gem] = dict(art="mythic upgrades", targets=[f"mythicupgrades:{_gem}_ore"])

# MYTHIC METALS (namespace `mythicmetals`, Fabric only, tops out at 1.21.4 so there is no 26.2
# build). Block ids read from mythicmetals-0.25.3+1.21.4.jar rather than guessed. Included anyway
# because one pack file serves many versions: at 26.2 these targets simply do not resolve, and on
# 1.20.1 or 1.21.4 they light up. `stormyx` is deliberately absent - the mod has it, our art does not.
#
# Note the mod already ships its OWN host variants for a few of these (tuff_orichalcum_ore,
# smooth_basalt_orichalcum_ore, calcite_kyber_ore, end_stone_starrite_ore, blackstone_stormyx_ore),
# so those join the same family and connect to the plain ore by construction.
MYTHIC_METALS = {
    "adamantite":   ["adamantite_ore", "deepslate_adamantite_ore"],
    "aquarium":     ["aquarium_ore"],
    "banglum":      ["banglum_ore"],                     # nether_banglum is split out below
    "carmot":       ["carmot_ore", "deepslate_carmot_ore"],
    "kyber":        ["kyber_ore", "calcite_kyber_ore"],
    "manganese":    ["manganese_ore"],
    "midas_gold":   ["midas_gold_ore"],
    "morkite":      ["morkite_ore", "deepslate_morkite_ore"],
    "mythril":      ["mythril_ore", "deepslate_mythril_ore"],
    "orichalcum":   ["orichalcum_ore", "deepslate_orichalcum_ore",
                     "smooth_basalt_orichalcum_ore", "tuff_orichalcum_ore"],
    "osmium":       ["osmium_ore"],
    "palladium":    ["palladium_ore"],
    "platinum":     ["platinum_ore"],
    "prometheum":   ["prometheum_ore", "deepslate_prometheum_ore"],
    "quadrillum":   ["quadrillum_ore"],
    "runite":       ["runite_ore", "deepslate_runite_ore"],
    "silver":       ["silver_ore"],
    "starrite":     ["starrite_ore"],                    # the host variants are split out below
    "stormyx":      ["stormyx_ore", "blackstone_stormyx_ore"],
    "tin":          ["tin_ore"],
    "unobtainium":  ["unobtainium_ore", "deepslate_unobtainium_ore"],
}
for _metal, _blocks in MYTHIC_METALS.items():
    FAMILIES[_metal] = dict(art="mythic metals",
                            targets=[f"mythicmetals:{b}" for b in _blocks])

# SPLIT FAMILIES: one metal whose hosts do not share one piece of artwork.
#
# Mythic Metals draws nether banglum differently from overworld banglum, and draws its calcite and
# end stone starrite differently from plain starrite. Different art means a different sprite, and a
# sprite is what a connecting texture is keyed on, so these cannot stay in one family.
#
# THEY ARE ALSO DELIBERATELY NOT CONNECTED TO EACH OTHER, and that is the owner's call rather than a
# limitation. Two blocks that look different should not merge into one outline, and in every case
# here they could not meet anyway: banglum and nether banglum are in different dimensions, as are
# calcite and end stone starrite, and plain starrite differs in overlay from the calcite one it
# shares the overworld with. So each is its own family with its own connection list, i.e. five
# separate ores as far as the outline is concerned.
#
# `overlay_file` / `border_file` point at an art file whose name is not <family>_<kind>.png. The two
# starrite host variants share one overlay and all of these reuse their sibling's hand drawn border,
# which suits them: the two banglum overlays are within 8 of each other per channel.
# Both banglum families take the suffix `banglum`, because OUR blocks are named
# <host>_banglum_ore for each form exactly as our gold ones are; the host tells them apart.
FAMILIES["banglum"]["hosts"] = "overworld"
FAMILIES["nether_banglum"] = dict(art="mythic metals",
                                  targets=["mythicmetals:nether_banglum_ore"],
                                  suffix="banglum", hosts="nether",
                                  border_file="banglum_border")
FAMILIES["calcite_starrite"] = dict(art="mythic metals",
                                    targets=["mythicmetals:calcite_starrite_ore"],
                                    overlay_file="starrite_overlay2", border_file="starrite_border")
FAMILIES["end_stone_starrite"] = dict(art="mythic metals",
                                      targets=["mythicmetals:end_stone_starrite_ore"],
                                      overlay_file="starrite_overlay2", border_file="starrite_border")


def add_family(key, **spec):
    """
    Register a family, refusing to overwrite an existing one.

    FAMILIES is a plain dict, so a repeated key silently REPLACES a working family and nothing
    warns. That is not hypothetical: the third party table below collides with Mythic Upgrades on
    ruby, sapphire, peridot, topaz, aquamarine and citrine, and with Mythic Metals on silver, tin,
    osmium, palladium and platinum. Assigning directly would have deleted eleven working families.
    """
    if key in FAMILIES:
        raise SystemExit(f"duplicate family key {key!r}: it would overwrite an existing family")
    FAMILIES[key] = spec


# ---------------------------------------------------------------------------
# THIRD PARTY ORE MODS
#
# Every block id and every host was read out of the real jar, never guessed. All of these place
# their ore through the SAME vanilla replaceables tags, so they are pure restyles.
#
# KEYS ARE PREFIXED BY MOD, and that is load bearing rather than cosmetic. See add_family above.
# Because the key no longer matches the art filename, each family points at its art explicitly with
# overlay_file / border_file.
#
# NONE OF THESE SET `suffix`, DELIBERATELY. mod_block_targets derives the block suffix from the key,
# and Seamless Ores already ships granite_topaz_ore and friends for MYTHIC UPGRADES. Giving Silent
# Gems' topaz a bare suffix of "topaz" would pull those blocks into the wrong family and append two
# overlays to them. Add `suffix` per family only when Seamless Ores actually gains variants for that
# mod, and resolve the ore-name clashes (lead, tin, silver, sapphire) at that point.
#
# prefix -> (art folder, namespace, {family base: (art file stem, [block names])})
_SG_GEMS = ["alexandrite", "ammolite", "aquamarine", "black_diamond", "carnelian", "citrine",
            "garnet", "heliodor", "iolite", "kyanite", "moldavite", "pearl", "peridot",
            "rose_quartz", "ruby", "sapphire", "tanzanite", "topaz", "turquoise", "white_diamond"]

THIRD_PARTY = {
    # Silent Gems: every gem has stone, deepslate AND netherrack forms. Opal is added below this
    # table: it is translucent, so it uses one solved layer and renders translucent.
    # ONE FAMILY PER DIMENSION, deliberately. Each gem has overworld, netherrack and end stone
    # forms, and a family is one sprite, so keeping them apart is what stops an End ore bordering
    # into an overworld one. They share the same artwork; only the sprite differs.
    "sg": ("silent gems", "silentgems", {
        **{g: (g, [f"{g}_ore", f"deepslate_{g}_ore"]) for g in _SG_GEMS},
        **{f"nether_{g}": (g, [f"nether_{g}_ore"]) for g in _SG_GEMS},
        **{f"end_{g}": (g, [f"end_{g}_ore"]) for g in _SG_GEMS},
        "chaos":  ("chaos",  ["chaos_ore", "deepslate_chaos_ore"]),
        "silver": ("silver", ["silver_ore", "deepslate_silver_ore"]),
    }),
    # Mekanism and Dense Mekanism keep their own art but SHARE a connection list per ore, so a dense
    # vein reads as part of the same deposit rather than a separate ore. See connection_targets.
    "mek": ("mekanism", "mekanism", {
        o: (o, [f"{o}_ore", f"deepslate_{o}_ore"])
        for o in ("fluorite", "lead", "osmium", "tin", "uranium")}),
    # Dense Mekanism names its blocks dense_<ore>_ore and dense_deepslate_<ore>_ore.
    "dmek": ("dense mekanism", "densemekanism", {
        o: (f"dense_{o}", [f"dense_{o}_ore", f"dense_deepslate_{o}_ore"])
        for o in ("fluorite", "lead", "osmium", "tin", "uranium")}),
    # Create: The Factory Must Grow. Its bauxite, galena, lignite and fireclay are NOT ores, they are
    # striated deposit blocks placed by create:layered_ore, so they are excluded by design.
    "tfmg": ("create the factory must grow", "tfmg", {
        o: (o, [f"{o}_ore", f"deepslate_{o}_ore"]) for o in ("lead", "lithium", "nickel")}),
    "sgear": ("silent gear", "silentgear", {
        "bort":         ("bort", ["bort_ore", "deepslate_bort_ore"]),
        "crimson_iron": ("crimson_iron", ["crimson_iron_ore", "blackstone_crimson_iron_ore"]),
        "azure_silver": ("azure_silver", ["azure_silver_ore"]),
    }),
    # Powah's art filenames are irregular, so these carry an explicit (overlay, border) pair rather
    # than a stem: the poor and dense files are uraninite_overlay_poor.png and
    # uraninite_overlay_poor_border.png, not uraninite_poor_overlay.png.
    "powah": ("powah", "powah", {
        "uraninite":       (("uraninite_overlay", "uraninite_border"),
                            ["uraninite_ore", "deepslate_uraninite_ore"]),
        "uraninite_poor":  (("uraninite_overlay_poor", "uraninite_overlay_poor_border"),
                            ["uraninite_ore_poor", "deepslate_uraninite_ore_poor"]),
        "uraninite_dense": (("uraninite_overlay_dense", "uraninite_overlay_dense_border"),
                            ["uraninite_ore_dense", "deepslate_uraninite_ore_dense"]),
    }),
    # Ice and Fire Community Edition (the maintained fork, same ids): the owner's art, drawn from CE's own
    # specks (16 Oct 2026). CE redrew silver and sapphire; its deepslate silver has specks of its own, so
    # it is its own family, and so are the mod's deepslate-tier silver variants (tuff, deep blackstone,
    # black argillite), which carry those specks. Sapphire has no deepslate form: its feature targets
    # stone_ore_replaceables only.
    "iaf": ("ice and fire ce", "iceandfire", {
        "silver":   ("silver", ["silver_ore"]),
        "deepslate_silver": ("deepslate_silver", ["deepslate_silver_ore"]),
        "sapphire": ("sapphire", ["sapphire_ore"]),
    }),
    "expores": ("exp ores", "expores", {
        "experience":        ("experience", ["experience_ore", "deepslate_experience_ore"]),
        "nether_experience": ("experience", ["nether_experience_ore"]),
        "end_experience":    ("experience", ["end_experience_ore"]),
    }),
    "things": ("things", "things", {
        "gleaming": ("gleaming", ["gleaming_ore", "deepslate_gleaming_ore"])}),
    "ep": ("energized power", "energizedpower", {
        "tin": ("tin", ["tin_ore", "deepslate_tin_ore"])}),
    # GALOSPHERE IS DELIBERATELY ABSENT. See EXCLUDED_NAMESPACES below.
    # Create: New Age targets stone_ore_replaceables only, so thorium has no deepslate form.
    # magnetite_block is excluded: it is a whole block deposit at 15 veins per chunk, far too common
    # to glow whole, unlike ancient debris at one buried vein per chunk.
    "cna": ("create new age", "create_new_age", {
        "thorium": ("thorium", ["thorium_ore"])}),
    # Tech Reborn (Fabric only; MIT). Overworld ores have stone and deepslate forms; cinnabar, pyrite
    # and sphalerite are netherrack only; peridot, sheldonite, sodalite and tungsten are end stone
    # only (their deepslate blocks exist at 26.x but nothing places them, so they are left out).
    # Uranium exists from 26.x only; at older versions the target simply does not resolve.
    "tr": ("tech reborn", "techreborn", {
        **{o: (o, [f"{o}_ore", f"deepslate_{o}_ore"])
           for o in ("bauxite", "galena", "iridium", "lead", "ruby", "sapphire", "silver", "tin", "uranium")},
        **{o: (o, [f"{o}_ore"]) for o in ("cinnabar", "pyrite", "sphalerite")},
        # The End ores. Their deepslate forms (26.x only) generate nowhere, but a player can still
        # hold one, and the end-stone overlay lands exactly on their specks (measured Sept 2026).
        # Deepslate is an OVERWORLD rock, so those are their own families on the same art: one
        # family per dimension, and variants in different dimensions never connect.
        **{o: (o, [f"{o}_ore"]) for o in ("peridot", "sheldonite", "sodalite", "tungsten")},
        **{f"deepslate_{o}": (o, [f"deepslate_{o}_ore"]) for o in ("peridot", "sheldonite", "sodalite", "tungsten")},
    }),
    # Modern Industrialization (MIT). Platinum, quartz and titanium ores exist but nothing places
    # them; they still glow if a player has one. Iridium GENERATES at 1.20.1 on both tiers (our
    # variant there is mi_iridium) and from 1.21.1 exists as the stone ore only, placed by nothing.
    "mi": ("modern industrialization", "modern_industrialization", {
        **{o: (o, [f"{o}_ore", f"deepslate_{o}_ore"])
           for o in ("antimony", "bauxite", "iridium", "lead", "lignite_coal", "monazite", "nickel", "salt",
                     "tin", "tungsten", "uranium")},
        **{o: (o, [f"{o}_ore"]) for o in ("platinum", "quartz", "titanium")},
    }),
    # Occultism (MIT). Its tier word comes LAST. Iesnium is a separate family below, because its
    # natural ore is hidden and must only glow when uncovered.
    "occ": ("occultism", "occultism", {
        "silver": ("silver", ["silver_ore", "silver_ore_deepslate"])}),
    # Extreme Reactors (MIT). All three animate, each with its own frame list (see ANIMATED).
    "er": ("extreme reactors", "bigreactors", {
        "yellorite": ("yellorite", ["yellorite_ore", "deepslate_yellorite_ore"]),
        "anglesite": ("anglesite", ["anglesite_ore"]),
        "benitoite": ("benitoite", ["benitoite_ore"]),
    }),
    # Immersive Engineering (Blu's License of Common Sense). Used with BluSunrize's permission
    # (14 Sept 2026), on condition of credit wherever credits appear. Block ids read from the 1.20.1
    # and 1.21.1 jars: ore_<metal> and deepslate_ore_<metal> on both. The art matches 1.21.1; IE
    # redrew nickel and uranium since 1.20.1, which is what LEGACY_ART serves.
    #
    # Seamless Ores 4.1.0 added variants of all five (1.21.1 NeoForge, 1.20.1 Forge), and the mod
    # prefixes every one of them ie_<metal> - family key and block suffix are the same string - so
    # they need no SO_SUFFIX line: mod_block_targets falls back to the family key and finds them.
    "ie": ("immersive engineering", "immersiveengineering", {
        o: (o, [f"ore_{o}", f"deepslate_ore_{o}"])
        for o in ("aluminum", "lead", "nickel", "silver", "uranium")}),
    # Oreganized (code MIT, assets All Rights Reserved in its own LICENSE). Used with the permission of
    # Binome of Team Galena (2 Oct 2026). Lead and silver, ids read from the 1.20.1 Forge, 1.20.1
    # Fabric and 1.21.1 NeoForge jars: <ore>_ore and deepslate_<ore>_ore on all three.
    #
    # Seamless Ores 4.2.0 names its variants oreganized_<ore> (1.21.1 NeoForge, 1.20.1 Forge and
    # Fabric), so the family key, the block suffix and the mod's overlay file are one string and
    # need no SO_SUFFIX line. Its tarnished_silver art is for a block Oreganized does not have yet.
    "oreganized": ("oreganized", "oreganized", {
        o: (o, [f"{o}_ore", f"deepslate_{o}_ore"]) for o in ("lead", "silver")}),
    # Mystical Agriculture (MIT). Its own models are already two-layer; the glow appends on top.
    "ma": ("mystical agriculture", "mysticalagriculture", {
        "inferium":   ("inferium", ["inferium_ore", "deepslate_inferium_ore"]),
        "prosperity": ("prosperity", ["prosperity_ore", "deepslate_prosperity_ore"]),
        "soulium":    ("soulium", ["soulium_ore"]),
    }),
    # Malum (All Rights Reserved; used with the permission of Sammy Semicolon, 8 Oct 2026). Ids read from
    # the 1.21.1 NeoForge, 1.20.1 Forge and 1.20.1 Fabric jars. The overlays are the owner's, and two of
    # them are Malum's own glow layers. Cthonic gold has no Seamless Ores variant (Malum's feature only
    # grows its clusters when its own ore is the first target), so here it glows Malum's block alone.
    "malum": ("malum", "malum", {
        "soulstone":      ("soulstone", ["soulstone_ore", "deepslate_soulstone_ore"]),
        "natural_quartz": ("natural_quartz", ["natural_quartz_ore", "deepslate_quartz_ore"]),
        "brilliance":     ("brilliant", ["brilliant_stone", "brilliant_deepslate"]),
        "blazing_quartz": ("blazing_quartz", ["blazing_quartz_ore"]),
        "cthonic_gold":   ("cthonic_gold", ["cthonic_gold_ore"]),
    }),
    # Cobblemon (MPL-2.0). ONE universal overlay per evolution stone serves every rock it sits on,
    # including Cobblemon's own dripstone moon stone and terracotta sun stone. The art is the SOLVED
    # set: Cobblemon's translucent edge ring with the translucency removed (the mod instead draws
    # per-host precomposited overlays, so the two differ by design). The nether fire stone is its own
    # family (one family per dimension, as with Silent's Gems) sharing the same art.
    "cb": ("cobblemon", "cobblemon", {
        **{f"{o}_stone": ((f"solved/{o}_stone_overlay", f"{o}_stone_border"),
                          [f"{o}_stone_ore", f"deepslate_{o}_stone_ore"]
                          + ([f"dripstone_{o}_stone_ore"] if o == "moon" else [])
                          + ([f"terracotta_{o}_stone_ore"] if o == "sun" else []))
           for o in ("dawn", "dusk", "fire", "ice", "leaf", "moon", "shiny", "sun", "thunder", "water")},
        "nether_fire_stone": (("solved/fire_stone_overlay", "fire_stone_border"), ["nether_fire_stone_ore"]),
    }),
}

# Silent's Gems' opal. Owner's call: EVERY opal glow uses the one solved translucent layer, whatever
# rock the block sits on (the mod's own four and our four). It keeps its partial opacity, so these
# families render TRANSLUCENT rather than cutout. One family per dimension, like every other gem.
_OPAL_ART = ("opal_extracted/opal_translucent_layer", "opal_border")
THIRD_PARTY["sg"][2].update({
    "opal":        (_OPAL_ART, ["opal_ore", "deepslate_opal_ore"]),
    "nether_opal": (_OPAL_ART, ["nether_opal_ore"]),
    "end_opal":    (_OPAL_ART, ["end_opal_ore"]),
})

# Families that keep their own artwork but share one connection list. Keyed by family, valued by
# group name; every family naming the same group borders as one body.
CONNECTION_GROUPS = {}
for _o in ("fluorite", "lead", "osmium", "tin", "uranium"):
    CONNECTION_GROUPS[f"mek_{_o}"] = f"mekanism_{_o}"
    CONNECTION_GROUPS[f"dmek_{_o}"] = f"mekanism_{_o}"
for _u in ("powah_uraninite", "powah_uraninite_poor", "powah_uraninite_dense"):
    CONNECTION_GROUPS[_u] = "powah_uraninite"

# WHAT SEAMLESS ORES CALLS THE SAME ORE.
#
# The mod knows each ore by its own name (the ore half of each pair in its ids.json), and that is NOT
# the family key here. Keys are
# prefixed by mod so two mods' identically named ores stay apart (mek_lead vs tfmg_lead), and the
# MOD prefixes an ore of its own whenever the plain name is already taken by another supported mod,
# which is where silents_silver and energized_tin come from.
#
# A family with no entry here covers the source mod's blocks and NONE of ours. That is silent: the
# pack builds, the source mod's ore glows, and our variant of the same ore does not. Everything the
# mod actually ships a variant for therefore needs a line, and `--list` is the check.
_SG_SO_NAME = {"aquamarine": "silents_aquamarine", "citrine": "silents_citrine",
               "peridot": "silents_peridot", "topaz": "silents_topaz", "silver": "silents_silver"}
# Ruby and sapphire: the mod gives Silent's Gems' overworld ruby and Mythic Upgrades' netherrack ruby
# ONE block name, but (fixed Sept 2026) their OWN art: the mod's Silent's Gems entries now use the
# overlay key silents_ruby / silents_sapphire. So each family claims its own hosts: Silent's Gems the
# overworld variants, Mythic Upgrades the nether ones (see the `hosts` set further down). Silent's Gems'
# nether ruby and sapphire have no variants in the mod, so sg_nether_ruby/sapphire take no suffix.
SO_SUFFIX = {}
SO_HOSTS = {}
for _g in _SG_GEMS:
    if _g in ("ruby", "sapphire"):
        SO_SUFFIX[f"sg_{_g}"] = _g
        SO_HOSTS[f"sg_{_g}"] = "overworld"
        continue
    SO_SUFFIX[f"sg_{_g}"] = _SG_SO_NAME.get(_g, _g)
    SO_HOSTS[f"sg_{_g}"] = "overworld"
    # Only eight of the gems have a netherrack form the mod restyles; the rest simply find nothing.
    SO_SUFFIX[f"sg_nether_{_g}"] = _SG_SO_NAME.get(_g, _g)
    SO_HOSTS[f"sg_nether_{_g}"] = "nether"
for _o in ("fluorite", "lead", "osmium", "tin", "uranium"):
    SO_SUFFIX[f"dmek_{_o}"] = f"dense_{_o}"          # densemekanism:dense_<ore>_ore
for _o in ("lead", "lithium", "nickel"):
    SO_SUFFIX[f"tfmg_{_o}"] = _o
for _o in ("soulstone", "natural_quartz", "brilliance", "blazing_quartz"):
    SO_SUFFIX[f"malum_{_o}"] = _o
SO_SUFFIX.update({
    # Chaos and silver are Silent's Gems entries outside the gem list above, so they need their own
    # lines. Silver is one of the names the mod prefixes, because Mythic Metals has a silver too.
    "sg_chaos": "chaos",
    "sg_silver": "silents_silver",
    "sgear_bort": "bort",
    "powah_uraninite": "uraninite",
    "powah_uraninite_poor": "uraninite_poor",
    "powah_uraninite_dense": "uraninite_dense",
    "iaf_silver": "iceandfire_silver",               # prefixed: Silent's Gems has a silver too
    "iaf_deepslate_silver": "iceandfire_silver",     # the same blocks, on the deepslate-tier hosts
    "iaf_sapphire": "iceandfire_sapphire",
    "expores_experience": "experience",
    "expores_nether_experience": "experience",
    "things_gleaming": "gleaming",
    "ep_tin": "energized_tin",                       # prefixed: Mythic Metals has a tin
    "cna_thorium": "thorium",
    # Sept 2026 wave. Prefixed where the mod prefixes (a clash with another supported mod's ore).
    "tr_bauxite": "techreborn_bauxite", "tr_galena": "galena", "tr_iridium": "iridium",
    "tr_lead": "techreborn_lead", "tr_ruby": "techreborn_ruby", "tr_sapphire": "techreborn_sapphire",
    "tr_silver": "techreborn_silver", "tr_tin": "techreborn_tin",
    "tr_cinnabar": "cinnabar", "tr_pyrite": "pyrite", "tr_sphalerite": "sphalerite",
    # Tech Reborn has uranium from 26.x only, prefixed there because the mod's plain "uranium"
    # variants are Modern Industrialization's.
    "tr_uranium": "techreborn_uranium",
    "mi_antimony": "antimony", "mi_bauxite": "mi_bauxite", "mi_iridium": "mi_iridium", "mi_lead": "mi_lead",
    "mi_lignite_coal": "lignite_coal", "mi_monazite": "monazite", "mi_nickel": "mi_nickel",
    "mi_salt": "salt", "mi_tin": "mi_tin", "mi_tungsten": "tungsten", "mi_uranium": "uranium",
    "occ_silver": "occultism_silver",
    "er_yellorite": "yellorite", "er_benitoite": "benitoite",
    "ma_inferium": "inferium", "ma_prosperity": "prosperity",
    "sg_opal": "opal",
    **{f"cb_{o}_stone": f"{o}_stone" for o in ("dawn", "dusk", "fire", "ice", "leaf", "moon", "shiny",
                                              "sun", "thunder", "water")},
    "cb_nether_fire_stone": "fire_stone",
})
SO_HOSTS.update({"cb_fire_stone": "overworld", "cb_nether_fire_stone": "nether",
                 # Pyrite and sphalerite are not pinned: their only other host is cinnabar (Seamless
                 # Ores 4.5.0), and no overworld ore shares their suffix.
                 "tr_cinnabar": "nether",
                 "er_benitoite": "nether", "sg_opal": "overworld"})
SO_HOSTS.update({"expores_experience": "overworld", "expores_nether_experience": "nether"})
# Ice and Fire CE's silver: the deepslate-tier hosts take the deepslate specks (OreType's deepslate overlay).
SO_HOSTS.update({"iaf_silver": "shallow", "iaf_deepslate_silver": "deep"})
# Mekanism's own ore IS supported by the mod from 4.1.0 (its mekanism:ore config turned out to be a
# record with one target list, which is exactly what ForeignOreTargets handles), and the mod prefixes
# every variant mek_<ore>, so family key and block suffix are the same string and these need no
# SO_SUFFIX line: mod_block_targets falls back to the family key and finds them.

for _prefix, (_art, _ns, _ores) in THIRD_PARTY.items():
    for _base, (_stem, _blocks) in _ores.items():
        # A stem is the usual <stem>_overlay.png / <stem>_border.png pair; a 2-tuple names both
        # files outright, for art that does not follow that convention.
        _ovl, _bdr = _stem if isinstance(_stem, tuple) else (f"{_stem}_overlay", f"{_stem}_border")
        _key = f"{_prefix}_{_base}"
        _spec = dict(art=_art, targets=[f"{_ns}:{b}" for b in _blocks],
                     overlay_file=_ovl, border_file=_bdr)
        if _key in CONNECTION_GROUPS:
            _spec["group"] = CONNECTION_GROUPS[_key]
        if _key in SO_SUFFIX:
            _spec["suffix"] = SO_SUFFIX[_key]
        if _key in SO_HOSTS:
            _spec["hosts"] = SO_HOSTS[_key]
        add_family(_key, **_spec)

# The mod's own overlay for a Dense Mekanism ore is dense_<ore>_overlay.png, which is not the family
# key, so the drift check needs the name spelled out. Without it these were never compared at all.
for _o in ("fluorite", "lead", "osmium", "tin", "uranium"):
    FAMILIES[f"dmek_{_o}"]["overlay"] = f"dense_{_o}"

# Ruby and sapphire: Mythic Upgrades' families keep only the NETHER variants (the overworld ones are
# Silent's Gems', above), and Silent's Gems' families name the mod's overlay key so the drift check
# compares the right pair of files.
for _gem in ("ruby", "sapphire"):
    FAMILIES[_gem]["hosts"] = "nether"
    FAMILIES[f"sg_{_gem}"]["overlay"] = f"silents_{_gem}"

# Seamless Ores: Matrix names an ore by Seamless Ores' OVERLAY key, which is unique per ore, where
# Seamless Ores' own ids reuse a name on different hosts (matrix_key). Explicit keys for the exceptions: Silent's Gems' ruby and sapphire (Mythic Upgrades' keep the plain names), and Silent's
# Gems' nether gems, which Matrix has no variant of and which would otherwise claim the overworld ones.
for _gem in ("ruby", "sapphire"):
    FAMILIES[f"sg_{_gem}"]["matrix"] = f"silents_{_gem}"
for _g in _SG_GEMS:
    if f"sg_nether_{_g}" in FAMILIES:
        # Seamless Ores keys these nether_<its key> (OreType.silentNether): the stones other mods place
        # in the Nether band (Create's scoria and scorchia, smooth basalt) take them from 1.21.1.
        FAMILIES[f"sg_nether_{_g}"]["matrix"] = f"nether_{_SG_SO_NAME.get(_g, _g)}"
# The other one-name pairs: the Nether form has its own key, or none (a fire stone in scoria is the one
# fire stone ore, glowed by the overworld family's art).
FAMILIES["expores_nether_experience"]["matrix"] = "nether_experience"
FAMILIES["cb_nether_fire_stone"]["matrix"] = None
# Ice and Fire CE's silver: one Matrix key, split between the two families by the host's tier.
FAMILIES["iaf_deepslate_silver"]["matrix"] = "iceandfire_silver"
FAMILIES["iaf_deepslate_silver"]["overlay"] = "iceandfire_deepslate_silver"
# Malum: the overlay keys of the mod (brilliance is drawn as brilliant), and no variant of cthonic gold.
for _o, _key in (("soulstone", "soulstone"), ("natural_quartz", "natural_quartz"), ("brilliance", "brilliant"),
                 ("blazing_quartz", "blazing_quartz")):
    FAMILIES[f"malum_{_o}"]["matrix"] = _key
    FAMILIES[f"malum_{_o}"]["overlay"] = _key
FAMILIES["malum_cthonic_gold"]["matrix"] = None

for _opal in ("sg_opal", "sg_nether_opal", "sg_end_opal"):
    FAMILIES[_opal]["render_type"] = "translucent"

# OCCULTISM'S IESNIUM IS A HIDDEN ORE. iesnium_ore_natural renders plain NETHERRACK while
# uncovered=false and only shows its texture at uncovered=true (Occultism's Third Eye). Glowing the
# covered state would reveal every hidden ore, so the natural block is targeted by STATE, and its
# border connects only to uncovered neighbours for the same reason. iesnium_ore is the ordinary
# block form, which always shows the texture. Seamless Ores ships no iesnium variants.
add_family("occ_iesnium", art="occultism", overlay_file="iesnium_overlay", border_file="iesnium_border",
           targets=["occultism:iesnium_ore",
                    {"block": "occultism:iesnium_ore_natural", "properties": {"uncovered": "true"}}])

# MEADOW'S ALPINE SALT OUTLINE. The outline is our own art and does not depend on where the salt sits,
# so it stays an ordinary border-only family, the same drawing in every era. The GLOW cannot be a
# family: it follows Meadow's two weighted model variants through a blockstate override (SALT_ERAS,
# below), with Satisfy's permission (14 Sept 2026).
add_family("meadow_alpine_salt", art="meadow", border_file="alpine_salt_border", border_only=True,
           targets=["meadow:alpine_salt_ore"])

# MEADOW joins the VANILLA families rather than getting its own art.
#
# Meadow overrides vanilla's own ore features and adds limestone as a third host, so alpine_<ore>_ore
# is a vanilla ore in a modded stone: our exact mechanic, on someone else's rock. Measured against
# the hand cleaned vanilla overlays, all five land on 100% of Meadow's ore pixels and 0% on bare
# limestone, so they inherit the vanilla overlay and border for free AND connect to vanilla ores of
# the same type by construction, because a connecting texture is keyed on the sprite.
#
# alpine_salt_ore is NOT here: it has two randomly weighted model variants whose blobs share only
# 3 pixels of 39 and 27, and Fusion appends per block rather than per model (checked in its source:
# no predicate or random texture can follow a weighted variant), so one overlay would glow bare
# limestone on half of them. It gets a border-only family above until its author answers.
#
# The full list comes from Meadow's BLOCKSTATES, not from its worldgen: copper, diamond and redstone
# are placed by a path the configured-feature scan did not see, and were missed on the first build.
for _ore in ("coal", "copper", "diamond", "emerald", "gold", "iron", "lapis", "redstone"):
    FAMILIES[_ore]["targets"].append(f"meadow:alpine_{_ore}_ore")

# ANIMATED FAMILIES. Values copied from the SOURCE MOD'S OWN mcmeta so the glow pulses in step with
# the ore underneath it, rather than being chosen.
#
# WHY THESE NEED A SECOND APPENDED MODEL IN THE BORDER VARIANT: a Fusion `connecting` texture CANNOT
# animate. `per_tile_animation` exists but its arithmetic collapses to an empty frame list and the
# game dies with ArithmeticException: / by zero before the main menu. `scrolling` is no help either
# - it is a different value of the same single `type` key, so a texture is connecting OR scrolling,
# never both, and it slides a window rather than flipping frames.
#
# The way out is that `append` takes a LIST. For an animated family the Border variant appends TWO
# models: a plain plate carrying the animated ore glow (the ordinary vanilla sprite path, which does
# animate) and a connecting sheet carrying ONLY the border, still. Non-animated families keep the
# single proven composited sheet, so this new path is confined to the two ores that need it.
# NO `interpolate`, deliberately. The whole crashing code path is
# SpriteContents$Interpolation.apply, which vanilla only ever enters for an INTERPOLATED sprite, and
# it died on `(frame + 1) % frames.size()` with an empty frame list. Every sprite we ship is
# well formed by vanilla's own arithmetic (16x80 -> 5 frames, 16x64 -> 4), so the empty list is
# built inside Fusion's sprite pipeline, which these plates go through and Mythic Metals' own
# textures do not - which is why theirs interpolate happily and ours crashed.
#
# Dropping the flag removes that code path entirely and costs only the cross fade between frames;
# the animation still plays at the same rate. Restore it only with an in-game test to back it up.
ANIMATED = {
    "stormyx":     {"frametime": 20},   # 5 frames, matches Mythic Metals' own stormyx_ore
    "unobtainium": {"frametime": 60},   # 4 frames, matches its deepslate ore
    # Silent Gems' chaos ore is 4 frames at frametime 10 in the mod's own mcmeta. The mod sets
    # interpolate; we deliberately do not, for the reason above.
    "sg_chaos":    {"frametime": 10},
}
# Extreme Reactors plays a custom frame ORDER (a pulse, then a long rest), so frametime alone would be
# wrong. The owner's overlays carry the mod's own mcmeta beside them; read it rather than retype it.
for _er in ("yellorite", "anglesite", "benitoite"):
    _meta = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "assets", "overlays", "published", "extreme reactors", f"{_er}_overlay.png.mcmeta")
    with open(_meta, encoding="utf-8") as _handle:
        ANIMATED[f"er_{_er}"] = {k: v for k, v in json.load(_handle)["animation"].items()
                                 if k in ("frametime", "frames")}

# THE ORE MODS OF SEAMLESS ORES 6.0.0 (22 Oct 2026). Every block id read from the mods' own jars, never
# guessed; the overlays are the owner's. Each family names its mod's art folder and file outright, and
# `matrix` is the ore key Seamless Ores gives its variants (None: the mod has none of this ore).
#
# ONE BORDER PER ORE (owner, 22 Oct 2026). Where an ore is drawn differently on different stones, every
# drawing keeps its own overlay, but they share ONE border, picked from the union of their colours, and
# one connection group, so the ore borders as one body across its stones.
#
# A [bracketed] overlay in the owner's folder is identical to the game's: that block joins the game's own
# family, as Meadow's do above.
def _ore_mod_family(key, art, ns, blocks, stem, matrix=None, border=None, group=None, hosts=None):
    spec = dict(art=art, targets=[f"{ns}:{b}" for b in blocks], overlay_file=f"{stem}_overlay",
                border_file=f"{border or stem}_border", matrix=matrix)
    if group:
        spec["group"] = group
    if hosts:
        spec["hosts"] = hosts
    add_family(key, **spec)


def _mcmeta_animation(art, stem):
    """The source mod's own frame timing, from the mcmeta the owner keeps beside the overlay."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "assets", "overlays", "published", art, f"{stem}_overlay.png.mcmeta")
    with open(path, encoding="utf-8") as handle:
        return {k: v for k, v in json.load(handle)["animation"].items() if k in ("frametime", "frames")}


# Actually Additions (MIT): black quartz, a stone form only.
_ore_mod_family("aa_black_quartz", "actually additions", "actuallyadditions", ["black_quartz_ore"],
                "black_quartz", matrix="black_quartz")

# Alex's Caves (LGPL): its coal, iron and redstone are drawn as the game's and join those families; the
# radrock uranium has art of its own. No Seamless Ores variants (its ores sit in its own stones only).
FAMILIES["coal"]["targets"].append("alexscaves:coprolith_coal_ore")
FAMILIES["iron"]["targets"].append("alexscaves:galena_iron_ore")
FAMILIES["redstone"]["targets"].append("alexscaves:guanostone_redstone_ore")
_ore_mod_family("ac_uranium", "alexs caves", "alexscaves", ["radrock_uranium_ore"], "uranium")

# Bigger Reactors (LGPL): uranium.
_ore_mod_family("br_uranium", "bigger reactors", "biggerreactors", ["uranium_ore", "deepslate_uranium_ore"],
                "uranium", matrix="biggerreactors_uranium")

# Create: Metallurgy (MIT): wolframite, in netherrack only, so no Seamless Ores variants.
_ore_mod_family("cmet_wolframite", "create metallurgy", "createmetallurgy", ["wolframite_ore"], "wolframite")

# Create: Propulsion (MIT): platinum.
_ore_mod_family("cprop_platinum", "create propulsion simulated", "createpropulsion",
                ["platinum_ore", "deepslate_platinum_ore"], "platinum", matrix="createpropulsion_platinum")

# Create: Tank Defenses (AFL): aluminum and titanium.
for _o in ("aluminum", "titanium"):
    _ore_mod_family(f"ctd_{_o}", "create tank defenses", "create_tank_defenses", [f"{_o}_ore", f"{_o}_deepslate_ore"],
                    _o, matrix=f"tankdefenses_{_o}")

# Deeper and Darker (AGPL): the game's eight ores in gloomslate and sculk stone, drawn as the game's, join
# those families; sculk stone lead has art of its own. Its ores sit in its own stones only.
for _o in ("coal", "copper", "diamond", "emerald", "gold", "iron", "lapis", "redstone"):
    FAMILIES[_o]["targets"] += [f"deeperdarker:gloomslate_{_o}_ore", f"deeperdarker:sculk_stone_{_o}_ore"]
_ore_mod_family("dd_lead", "deeper and darker", "deeperdarker", ["sculk_stone_lead_ore"], "lead")

# Embers Rekindled (LGPL): lead and silver are its only ore blocks (its other ore textures name no block).
for _o in ("lead", "silver"):
    _ore_mod_family(f"emb_{_o}", "embers rekindled", "embers", [f"{_o}_ore", f"deepslate_{_o}_ore"], _o,
                    matrix=f"embers_{_o}")

# Enderite Mod (MIT): enderite and cracked enderite are drawn like ancient debris, so they glow as WHOLE
# blocks, with no border (owner, 22 Oct 2026). Enderite is a column on every version (1.20.1 to 26.3).
# Cracked enderite has bumps of its own (235 elements, the same on every version), so its glow is drawn
# over that geometry, copied from the mod's model with its texture slots "0" (side) and "1" (top).
WHOLE_BLOCK_GLOW["enderite_ore"] = dict(
    targets=["enderitemod:enderite_ore"],
    parent="block/cube_column",
    textures={"end": "enderite_ore_top", "side": "enderite_ore_side"},
)
WHOLE_BLOCK_GLOW["cracked_enderite_ore"] = dict(
    targets=["enderitemod:cracked_enderite_ore"],
    parent="block/block",
    textures={"0": "cracked_enderite_ore_side", "1": "cracked_enderite_ore_top", "particle": "cracked_enderite_ore_side"},
    elements="cracked_enderite_ore_elements.json",
)

# Epic Paladins (MIT): arclight, bloodstone, cryorium, the Nether's moonlight, and the End's jade.
for _o, _matrix in (("arclight", "arclight"), ("bloodstone", "epicpaladins_bloodstone"), ("cryorium", "cryorium")):
    _ore_mod_family(f"epal_{_o}", "epic paladins", "epicpaladins", [f"{_o}_ore", f"deepslate_{_o}_ore"], _o,
                    matrix=_matrix)
_ore_mod_family("epal_moonlight", "epic paladins", "epicpaladins", ["moonlight_ore"], "moonlight", matrix="moonlight")
_ore_mod_family("epal_jade", "epic paladins", "epicpaladins", ["jade_ore"], "jade")

# EvilCraft (CC BY 4.0 images): dark ore, glowing in both of its states.
_ore_mod_family("ec_dark", "evilcraft", "evilcraft", ["dark_ore", "dark_ore_deepslate"], "dark", matrix="dark")

# Expanded Delight (MIT): salt.
_ore_mod_family("ed_salt", "expanded delight", "expandeddelight", ["salt_ore", "deepslate_salt_ore"], "salt",
                matrix="expandeddelight_salt")

# Hazen 'N Stuff (PolyForm Shield 1.0.0; "Required Notice: Copyright Hazen"): chlorophyte, dreadstone, and
# zenalite drawn per stone (five drawings, one border). Ids from 1.4.0.14 (1.21.1 NeoForge); the 1.20.1
# "watered-down" edition names and draws its zenalite differently and is not covered. Solar core has no
# overlay yet.
_ore_mod_family("hz_chlorophyte", "hazen n stuff", "hazennstuff", ["chlorophyte_ore"], "chlorophyte",
                matrix="chlorophyte")
_ore_mod_family("hz_dreadstone", "hazen n stuff", "hazennstuff", ["dreadstone_ore"], "dreadstone", matrix="dreadstone")
for _h in ("stone", "deepslate", "end_stone", "abysslate", "voidstone"):
    _ore_mod_family(f"hz_zenalite_{_h}", "hazen n stuff", "hazennstuff", [f"zenalite_{_h}_ore"], f"zenalite_{_h}",
                    border="zenalite", group="hazennstuff_zenalite")

# Majrusz's Progressive Difficulty (MIT): enderium shard ore, End only, so no Seamless Ores variants.
_ore_mod_family("mj_enderium_shard", "majruszs progressive difficulty", "majruszsdifficulty", ["enderium_shard_ore"],
                "enderium_shard")


def frame_count(image):
    """Frames in a vertical strip, or 1 for a plain square texture."""
    if image.width and image.height % image.width == 0:
        return image.height // image.width
    return 1


# THE GLINT (animated pack only). Our own design: a soft band grows out from the middle of the face
# to its corners, lifting only pixels the art already draws, toward white, then the ore rests.
# Silhouette and alpha never change, so the glint can never paint outside the speck or the border
# and never shows on the host stone. Measured in tile pixels, so a 32px overlay sweeps at the same
# speed. The ore layer and the border sheet get the SAME frames and timing, so they glint together.
GLINT_SWEEP_FRAMES = 12     # frames the band takes to cross the block
GLINT_FRAMETIME = 2         # ticks per sweep frame: 12 x 2 = 24 ticks, 1.2 s to cross
GLINT_REST_TICKS = 40       # ticks the plain art is held between sweeps (2 s); one stored frame
GLINT_HALF_WIDTH = 2.5      # band half-width in 16px-tile pixels
GLINT_PEAK = 0.65           # fraction of the way to white at the band's centre (0.55 read slightly dim)
# The shape the band takes. "diamond": it grows out from the middle of the face to the corners, so it
# looks the same from every side. "diagonal" (the first design) sweeps top-left to bottom-right in
# texture space, which reads backwards on a top or bottom face seen from the far side, because those
# faces are seen from all four directions (owner, 6 Oct 2026). One shape on every face: one sprite.
GLINT_SHAPE = "diamond"


# THE PULSE (Pulsing pack). PULSE_STEPS stored frames from PULSE_FLOOR to full alpha on a half cosine,
# played up and back down through the frame list (an index repeats for free): 2 x (steps - 1) entries
# of PULSE_FRAMETIME ticks, 16 x 4 = 64 ticks, 3.2 s a breath. Every ore pulses in step (texture
# animation is global). The floor keeps an ore from vanishing mid-cycle (owner's call, 15 Oct 2026).
PULSE_STEPS = 9
PULSE_FLOOR = 0.2
PULSE_FRAMETIME = 4


def pulse_frames(image):
    """The image at each pulse step's alpha, faintest first. Every frame is the image's own size."""
    import math
    frames = []
    for k in range(PULSE_STEPS):
        level = PULSE_FLOOR + (1 - PULSE_FLOOR) * (1 - math.cos(math.pi * k / (PULSE_STEPS - 1))) / 2
        tile = image.copy()
        tile.putalpha(image.getchannel("A").point(lambda a, level=level: round(a * level)))
        frames.append(tile)
    return frames


PULSE_ANIMATION = {"frametime": PULSE_FRAMETIME,
                   "frames": list(range(PULSE_STEPS)) + list(range(PULSE_STEPS - 2, 0, -1))}


def glint_animation():
    """The animation section of a generated layer: the glint's, or in the Pulsing pack the pulse's."""
    return PULSE_ANIMATION if EFFECT == "pulse" else GLINT_ANIMATION


def generated_render_type(render_type):
    """A generated layer's render type: translucent where its alpha varies (Subtle, Pulsing)."""
    return "translucent" if EFFECT in ("subtle", "pulse") else render_type


def glint_frames(image):
    """The plain image, then every sweep frame (the Pulsing pack: its pulse steps). Each the image's size."""
    if frame_count(image) != 1 or image.width != image.height:
        sys.exit("glint_frames needs a single square frame; an already animated overlay keeps its own")
    if EFFECT == "pulse":
        return pulse_frames(image)
    size = image.width
    width = GLINT_HALF_WIDTH * size / 16
    middle = (size - 1) / 2
    if GLINT_SHAPE == "diamond":
        span = size - 1
        place = lambda x, y: abs(x - middle) + abs(y - middle)
    else:
        span = 2 * (size - 1)
        place = lambda x, y: x + y
    pixels = image.load()
    frames = [image.copy()]
    for frame in range(1, GLINT_SWEEP_FRAMES + 1):
        tile = image.copy()
        # The band starts just inside its origin (the middle, or the top-left corner) and ends just
        # past the far corners, so the first and last sweep frames only graze the art.
        centre = -width + (span + 2 * width) * (frame - 0.5) / GLINT_SWEEP_FRAMES
        out = tile.load()
        for y in range(size):
            for x in range(size):
                r, g, b, a = pixels[x, y]
                distance = abs(place(x, y) - centre)
                if a == 0 or distance >= width:
                    continue
                lift = GLINT_PEAK * (1 - distance / width)
                out[x, y] = (round(r + (255 - r) * lift), round(g + (255 - g) * lift),
                             round(b + (255 - b) * lift), a)
        frames.append(tile)
    return frames


def stack(frames):
    """Frames one under another, the layout vanilla animation reads."""
    from PIL import Image
    width, height = frames[0].size
    strip = Image.new("RGBA", (width, height * len(frames)), (0, 0, 0, 0))
    for i, frame in enumerate(frames):
        strip.paste(frame, (0, i * height))
    return strip


def glint_strip(image):
    return stack(glint_frames(image))


# The rest is ONE stored frame held for GLINT_REST_TICKS, through the frame list, so resting costs
# no texture memory however long it is.
GLINT_ANIMATION = {"frametime": GLINT_FRAMETIME,
                   "frames": [{"index": 0, "time": GLINT_REST_TICKS}] + list(range(1, GLINT_SWEEP_FRAMES + 1))}

# --families limits a build to the named families, for trying a change on one before all of them.
ONLY = None


# Edge names exactly as Fusion names them in TextureConnections. Because Fusion NAMES its
# directions, there is no orientation ambiguity to resolve empirically - which is what caused the
# reflected borders on the Continuity build. Do not reorder.
EDGES = ["top", "right", "bottom", "left"]

# pack.png - a 2x2 of host stones with a quartered ore border over them, built at NATIVE 16x16 (each
# quadrant an 8x8 stone crop, art composited 1:1) then nearest-upscaled, so every pixel stays the
# same size and the border reads as one frame spanning the quadrants rather than art pasted on tiles.
#
# The border is DERIVED FROM THE FAMILY ART rather than a hand-saved file: each quadrant takes the
# matching 8x8 corner of a family's border frame, so the four corners join into one continuous frame
# made of two ore colours on the diagonals. Deriving it means changing a border design updates the
# icon on the next build instead of silently leaving it stale, which is exactly what happened when
# the borders were redrawn. Verified against the hand-made original: 0 of 256 pixels differ.
ICON_STONES = [("granite", "granite"), ("blackstone", "blackstone"),     # top-left, top-right
               ("basalt", "basalt_side"), ("diorite", "diorite")]        # bottom-left, bottom-right
ICON_BORDER_MAIN = "gold"            # top-left and bottom-right quadrants
ICON_BORDER_ALT = "nether_gold"      # top-right and bottom-left quadrants
ICON_OVERLAY_FAMILY = "nether_gold"  # the ore blobs laid over the whole 16x16
ICON_SIZE = 128


def root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def art_path(family, kind, legacy=False):
    """
    kind is 'overlays' or 'borders', laid out as assets/<kind>/<mod>/<family>_<x>.png, except that the
    overlays of published mods sit in assets/overlays/published/<mod>/ (the owner's layout, 21 Oct 2026:
    published, permission and no permission folders; only published art is ever built).

    A family may override either with `overlay_file` / `border_file`, which is how a split family
    reuses its sibling's art. See the split families above. legacy=True picks the old drawing of a
    LEGACY_ART family instead.
    """
    stem = "overlay" if kind == "overlays" else "border"
    name = FAMILIES[family].get(f"{stem}_file", f"{family}_{stem}")
    if legacy:
        name = LEGACY_ART[family][0 if kind == "overlays" else 1]
    return os.path.join(art_dir(kind, FAMILIES[family]["art"]), f"{name}.png")


def art_dir(kind, art):
    """The folder holding one mod's art of this kind (see art_path)."""
    return os.path.join(root(), "assets", kind, *(("published",) if kind == "overlays" else ()), art)


def layout():
    """The Fusion `full` layout, recovered by running Fusion's own FullLayoutHandler."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fusion_full_layout.json")
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if len(data["tiles"]) != 47:
        sys.exit(f"{path}: expected 47 tiles, found {len(data['tiles'])}")
    return data


# The game's own rock, by the mod's host names (the host half of each pair in its ids.json). Only these
# match a family by its suffix and its `hosts` filter; any other host is another mod's stone, matched
# by the Matrix key alone.
MOD_HOSTS = {"granite", "diorite", "andesite", "tuff", "dripstone", "cinnabar", "basalt", "blackstone"}
NETHER_HOSTS = {"basalt", "blackstone"}
# The game's own rock the mod gives the DEEPSLATE tier: an ore with deepslate art of its own wears it there.
DEEP_HOSTS = {"tuff"}


MONOLITH = "seamlessores-monolith"


def mod_asset_dirs(*parts):
    """Every source folder of the MOD's assets, across all its Minecraft bands.

    One pack file serves every Minecraft version, so it should list the UNION of our blocks across
    bands: `ignore_missing_targets` skips the ones that do not exist on the version in use. The bands
    genuinely differ (Mythic Metals support lives on 1.21.x, so 26.x alone would miss 68 blocks),
    hence a scan rather than one hardcoded path.

    The mod's repository keeps every band in one folder, seamlessores-monolith/<band>/, where a band
    folder may hold several builds (1.21/common/src/<era>/resources). Before that, each band was its
    own seamlessores-<band>-multiloader folder: read only when the monolith is not there, so a stale
    old folder cannot keep a block alive in the pack.

    The mod lives in a separate repository, so this must tolerate all of them being absent: the pack
    has to build standalone.
    """
    parent = os.path.normpath(os.path.join(root(), ".."))
    if not os.path.isdir(parent):
        return []
    found = []
    monolith = os.path.join(parent, MONOLITH)
    if os.path.isdir(monolith):
        # The overlay art lives once, in shared/textures/; a band folder keeps only the overlays whose
        # drawing differs on that band (under the same name).
        if parts and parts[0] == "textures":
            pool = os.path.join(monolith, "shared", "textures", *parts[1:])
            if os.path.isdir(pool):
                found.append(pool)
        for band in sorted(os.listdir(monolith)):
            src = os.path.join(monolith, band, "common", "src")
            if not os.path.isdir(src):
                continue
            for era in sorted(os.listdir(src)):
                path = os.path.join(src, era, "resources", "assets", MOD_NAMESPACE, *parts)
                if os.path.isdir(path):
                    found.append(path)
        return found
    for name in sorted(os.listdir(parent)):
        if not (name.startswith("seamlessores-") and name.endswith("-multiloader")):
            continue
        path = os.path.join(parent, name, "common", "src", "main", "resources",
                            "assets", MOD_NAMESPACE, *parts)
        if os.path.isdir(path):
            found.append(path)
    return found


def branch_of(path):
    """The band a mod_asset_dirs() folder belongs to: 1.20.1, 1.21.4, 26.x, as the mod's builds name them."""
    parent = os.path.normpath(os.path.join(root(), ".."))
    rel = os.path.relpath(path, parent).split(os.sep)
    if rel[0] == MONOLITH:
        if rel[1] == "shared":
            return "shared"
        band, era = rel[1], rel[4]
        return band if era == "main" else era
    return rel[0].removeprefix("seamlessores-").removesuffix("-multiloader")


def so_variants():
    """Every Seamless Ores variant as (host, ore key, block id, build folder), from each build's
    seamlessores/ids.json, which the mod's generator writes (6.0.0 on): the ids are never parsed, so a
    change in how the mod names its blocks cannot drop a block from the pack."""
    global _VARIANTS
    if _VARIANTS is None:
        _VARIANTS = []
        monolith = os.path.normpath(os.path.join(root(), "..", MONOLITH))
        for band in sorted(os.listdir(monolith)) if os.path.isdir(monolith) else []:
            src = os.path.join(monolith, band, "common", "src")
            for era in sorted(os.listdir(src)) if os.path.isdir(src) else []:
                path = os.path.join(src, era, "resources", MOD_NAMESPACE, "ids.json")
                if os.path.isfile(path):
                    for pair, block in json.load(open(path, encoding="utf-8"))["ids"].items():
                        host, key = pair.split("|")
                        _VARIANTS.append((host, key, f"{MOD_NAMESPACE}:{block}", os.path.join(src, era)))
        if os.path.isdir(monolith) and not _VARIANTS:
            sys.exit(f"no seamlessores/ids.json in any {MONOLITH} build; regenerate the mod's assets")
    return _VARIANTS


_VARIANTS = None


def mod_block_targets(family):
    """
    Our own blocks in this family, from the mod's ids.json (so_variants), by ore key. In the game's own
    rock: the family's suffix or Matrix key, and any `also_suffixes` (other ores of ours that glow as
    this family, Born in Chaos' infected diamond), on the hosts `hosts` allows (nether, overworld, deep,
    shallow): an ore with an overworld and a nether form (gold and nether gold, banglum) is told apart
    by the host. In another mod's stone: the Matrix key only (None: no such blocks), and a family split
    by tier (Ice and Fire CE's silver) takes only the stones of its own tier.
    """
    spec = FAMILIES[family]
    rock_keys = {spec.get("suffix", family), matrix_key(family)} | set(spec.get("also_suffixes", []))
    hosts = spec.get("hosts")
    found = set()
    for host, key, block, tree in so_variants():
        if host in MOD_HOSTS:
            if key not in rock_keys:
                continue
            deep, nether = host in DEEP_HOSTS, host in NETHER_HOSTS
            if (hosts == "nether" and not nether or hosts == "overworld" and nether
                    or hosts == "deep" and not deep or hosts == "shallow" and (deep or nether)):
                continue
        else:
            if key != matrix_key(family):
                continue
            if hosts in ("deep", "shallow") and (matrix_host_tiers(tree).get(host) == "deepslate") != (hosts == "deep"):
                continue
        found.add(block)
    return sorted(found)


# The stones other mods place (the Matrix ores until they were folded into Seamless Ores) have their
# tier in the mod's host table (its placed(...) stones), which a family split by tier reads. Every
# band has one; a band folder may hold several builds (1.21/common/src/<era>/), each with its own table.
MATRIX_HOST_TABLE = ("java", "com", "chillpavz", "seamlessores", "content", "HostStone.java")


_RETIRED = None


def retired_ids():
    """Seamless Ores' retired ids (tools/<band>/retired_ids.json in the monolith): placeholders drawn as
    their plain stone, which turn into it, so they never get a glow."""
    global _RETIRED
    if _RETIRED is None:
        import glob
        monolith = os.path.normpath(os.path.join(root(), "..", MONOLITH))
        _RETIRED = {f"{MOD_NAMESPACE}:{row['id']}"
                    for path in glob.glob(os.path.join(monolith, "**", "tools", "**", "retired_ids.json"), recursive=True)
                    for row in json.load(open(path, encoding="utf-8"))["retired"]}
    return _RETIRED


def matrix_host_tiers(tree):
    """{host name (create_limestone): its OreTier, lower case} from one build's host table."""
    table = os.path.join(tree, *MATRIX_HOST_TABLE)
    if not os.path.isfile(table):
        return {}
    with open(table, encoding="utf-8") as handle:
        return {f"{mod}_{path}": tier.lower() for mod, path, tier in
                re.findall(r'placed\("(\w+)", "(\w+)",\s*OreTier\.(\w+)', handle.read())}


MATRIX_SUFFIX_PREFIXES = ("tr_", "ep_", "occ_", "sg_", "sgear_", "powah_", "ma_", "cb_", "dmek_", "tfmg_",
                          "iaf_", "expores_", "things_", "er_", "mi_", "cna_")


def matrix_key(family):
    """The ore part of this family's Matrix ids: an explicit "matrix" key, else the suffix, else the name."""
    spec = FAMILIES[family]
    if "matrix" in spec:
        return spec["matrix"]
    # A family named with its mod's prefix (tr_tin) holds the ore's Seamless Ores suffix, which is
    # Matrix's key too (techreborn_tin); every other family is named after the key itself (nether_gold,
    # whose Seamless Ores suffix is plain gold).
    if family.startswith(MATRIX_SUFFIX_PREFIXES):
        return spec.get("suffix", family)
    return family


def check_matrix_keys():
    """Two families claiming one Matrix key would glow one block twice; stop instead."""
    seen = {}
    for family in FAMILIES:
        key = matrix_key(family)
        # A key split between a "deep" and a "shallow" family takes each stone once.
        tier = FAMILIES[family].get("hosts") if FAMILIES[family].get("hosts") in ("deep", "shallow") else ""
        if key is not None and (key, tier) in seen:
            sys.exit(f"families {seen[(key, tier)]} and {family} both claim Matrix ore key {key}")
        seen[(key, tier)] = family


def family_targets(family):
    ours = [t for t in mod_block_targets(family) if t not in retired_ids()]
    return list(FAMILIES[family]["targets"]) + ours


def target_block(target):
    """The block id of a target, which is either a plain id or a Fusion {block, properties} object."""
    return target["block"] if isinstance(target, dict) else target


def connection_predicates(family):
    """Fusion `connections` for a family. Plain ids share one match_block; a state-filtered target
    becomes a match_state with the same properties, so a border never connects into a state the
    glow deliberately skips (iesnium's covered, netherrack-looking form)."""
    targets = connection_targets(family)
    plain = [t for t in targets if not isinstance(t, dict)]
    preds = [{"type": "match_block", "blocks": plain, "ignore_missing": True}] if plain else []
    for t in targets:
        if isinstance(t, dict):
            preds.append({"type": "match_state", "block": t["block"], "properties": t["properties"],
                          "ignore_missing": True})
    return preds


def connection_targets(family):
    """
    The blocks this family's border connects to, which is NOT always its own target list.

    A family may name a `group`, and then every family in that group connects to every other while
    keeping its own sprite and its own artwork. Fusion declares connection per face from a block
    list, so both halves simply omit their border on a shared edge and the outline reads as one
    body across differing art.

    That is what lets a mod's ore tiers read as one deposit: Mekanism's ore beside Dense Mekanism's,
    and Powah's three uraninite grades, which a player sees as one vein rather than three ores.
    """
    group = FAMILIES[family].get("group")
    if not group:
        return family_targets(family)
    blocks = []
    for other, spec in FAMILIES.items():
        if spec.get("group") == group:
            for b in family_targets(other):
                if b not in blocks:
                    blocks.append(b)
    return blocks


def mitre_owners(size):
    """
    Assign every pixel of the master frame to the edge(s) it is closest to, by 45-degree mitring.

    At a corner two edges tie and the pixel belongs to BOTH, so a corner survives as long as either
    of its edges is still drawn - which is how a picture frame should behave. Works unchanged for a
    1px frame or a thicker/higher-resolution one.
    """
    owners = {}
    for y in range(size):
        for x in range(size):
            distance = {"top": y, "right": size - 1 - x, "bottom": size - 1 - y, "left": x}
            nearest = min(distance.values())
            owners[(x, y)] = frozenset(e for e, d in distance.items() if d == nearest)
    return owners


def build_tile(overlay, master, owners, connected_edges, inner_corners):
    """
    One tile of the sheet: the ore art, plus the border on every edge that is NOT connected.

    Border pixels are taken from the hand-drawn master rather than redrawn, so its colour and
    shading survive exactly. Where two adjacent edges connect but the diagonal does not, the corner
    wedge is kept on its own as the inner-corner mark.
    """
    from PIL import Image
    tile = overlay.copy()
    open_edges = {e for e in EDGES if e not in connected_edges}
    inner = {frozenset(c.split("_")) for c in inner_corners}   # "top_right" -> {top, right}

    border = Image.new("RGBA", master.size, (0, 0, 0, 0))
    for (x, y), owned in owners.items():
        pixel = master.getpixel((x, y))
        if pixel[3] == 0:
            continue
        if owned & open_edges:
            border.putpixel((x, y), pixel)
        elif len(owned) == 2 and owned in inner:
            border.putpixel((x, y), pixel)
    tile.alpha_composite(border)
    return tile


def build_sheet(family, spec, ore_layer=True, legacy=False):
    """The whole 8x6 connecting sheet for one family, as a single image.

    With ore_layer=False the tiles carry ONLY the border on a transparent field, which is what an
    animated family needs: its ore glow is appended separately as a plain animated plate, because a
    connecting texture cannot animate.
    """
    from PIL import Image
    master = Image.open(art_path(family, "borders", legacy)).convert("RGBA")
    if ore_layer:
        overlay = Image.open(art_path(family, "overlays", legacy)).convert("RGBA")
        if overlay.size != master.size:
            sys.exit(f"{family}: overlay {overlay.size} and border {master.size} must match. "
                     f"An animated overlay must be declared in ANIMATED so its ore layer is split "
                     f"out instead of being composited into the sheet.")
    else:
        overlay = Image.new("RGBA", master.size, (0, 0, 0, 0))
    size = master.width
    owners = mitre_owners(size)
    width, height = spec["grid"]["width"], spec["grid"]["height"]
    sheet = Image.new("RGBA", (width * size, height * size), (0, 0, 0, 0))
    for tile in spec["tiles"]:
        img = build_tile(overlay, master, owners, tile["connected_edges"], tile["inner_corners"])
        sheet.paste(img, (tile["x"] * size, tile["y"] * size))
    return sheet


# THE PIECED LAYOUT, for every animated border. Fusion's `full` layout makes one sprite per tile, 47 per
# family, and on 26.2 each ANIMATED sprite costs about half a megabyte of native memory whatever its
# size: the full-layout bordered pack measured +5.4 GB over no pack, against +75 MB for the borderless
# one (harness SAO_MEM, 4 Oct 2026). `pieced` stores five tiles and builds every connection state by
# giving each QUARTER of the face one of them (PiecedLayoutHandler), so the cost drops ~9x.
#
# It reproduces the 47-tile sheet exactly because this border is quarter-local: a pixel in the top-left
# quarter is only ever owned by the top edge, the left edge, or both (mitre_owners), so what it shows
# depends only on those two edges and the corner between them, which is what a pieced quarter keys on.
# Tile order, from PiecedLayoutHandler's whole-sprite cases:
PIECED_TILES = [
    ([], []),                                          # 0: nothing connected, full frame
    (EDGES, ["top_left", "top_right", "bottom_left", "bottom_right"]),  # 1: all connected: empty
    (["top", "bottom"], []),                           # 2: top and bottom connected: left/right lines
    (["left", "right"], []),                           # 3: left and right connected: top/bottom lines
    (EDGES, []),                                       # 4: four sides, no diagonals: inner corner marks
]


# A PIECED BORDER MUST SIT OFF THE FACE. Wherever a face's four corners need different pieces, Fusion
# splits it into four quarter quads with new vertices. They lie in the block face's plane but are new
# triangles, so their depth differs from the block's own face by rounding, and the border loses the
# depth test by view angle: whole runs of outline vanish and return as the camera moves (harness
# sweep, 6 Oct 2026, 26.3 Fabric without Sodium; seen by the owner on every loader). The `full`
# layout keeps one quad identical to the block's face and never fights. Lifting the border cube by
# BORDER_LIFT (model units, 16 per block) clears the fight; 0.05 is 0.003 of a block, invisible,
# and above depth precision out to roughly 50 blocks.
BORDER_LIFT = 0.05


def lifted_cube():
    """cube_all's single element, grown by BORDER_LIFT on every side, faces still culled as cube_all."""
    faces = {side: {"texture": "#all", "cullface": side, "uv": [0, 0, 16, 16]}
             for side in ("down", "up", "north", "south", "west", "east")}
    return [{"from": [-BORDER_LIFT] * 3, "to": [16 + BORDER_LIFT] * 3, "faces": faces}]


def build_pieced(master):
    """One frame of the 5x1 pieced border sheet, border only, from the border master."""
    from PIL import Image
    size = master.width
    owners = mitre_owners(size)
    empty = Image.new("RGBA", master.size, (0, 0, 0, 0))
    sheet = Image.new("RGBA", (size * len(PIECED_TILES), size), (0, 0, 0, 0))
    for i, (connected, diagonals) in enumerate(PIECED_TILES):
        # build_tile takes the corners that DO get an inner mark: those whose two edges connect but
        # whose diagonal does not.
        inner = [] if not connected else [c for c in ("top_left", "top_right", "bottom_left", "bottom_right")
                                          if c not in diagonals and set(c.split("_")) <= set(connected)]
        sheet.paste(build_tile(empty, master, owners, connected, inner), (i * size, 0))
    return sheet


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")


def build_pack_icon(out_dir):
    from PIL import Image

    icon = Image.new("RGBA", (16, 16))
    quadrants = [(0, 0), (8, 0), (0, 8), (8, 8)]        # TL, TR, BL, BR - matches ICON_STONES

    with zipfile.ZipFile(MC_JAR) as jar:
        for (_name, texture), (x, y) in zip(ICON_STONES, quadrants):
            stone = Image.open(io.BytesIO(
                jar.read(f"assets/minecraft/textures/block/{texture}.png"))).convert("RGBA")
            icon.paste(stone.crop((0, 0, 8, 8)), (x, y))

    # Each quadrant takes the SAME corner of a border frame that it occupies in the icon, so the
    # four 8x8 pieces line up into one unbroken frame. Main colour on the TL/BR diagonal.
    main = Image.open(art_path(ICON_BORDER_MAIN, "borders")).convert("RGBA")
    alt = Image.open(art_path(ICON_BORDER_ALT, "borders")).convert("RGBA")
    border = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    for (x, y), source in zip(quadrants, (main, alt, alt, main)):
        border.paste(source.crop((x, y, x + 8, y + 8)), (x, y))
    icon.alpha_composite(border)

    icon.alpha_composite(Image.open(art_path(ICON_OVERLAY_FAMILY, "overlays")).convert("RGBA"))
    icon.resize((ICON_SIZE, ICON_SIZE), Image.NEAREST).save(os.path.join(out_dir, "pack.png"))


# Every key Fusion actually reads out of a `<texture>.png.mcmeta`. `emissive` and `render_type` are
# confirmed from the author's own fusion-emissive-ores pack; `type`/`layout` from
# fusion-connected-blocks; the rest were read out of ConnectingTextureType.deserialize().
#
# This list exists because FUSION IGNORES UNKNOWN KEYS IN SILENCE. Writing "connection" instead of
# "connections" produced no warning, no log line and a perfectly valid-looking pack that simply fell
# back to same-block connection. A typo here is invisible in game, so it gets checked at build time.
FUSION_TEXTURE_KEYS = {"emissive", "render_type", "type", "layout", "connections", "default",
                       "per_tile_animation", "sub_texture"}

# Predicate types registered by FusionClient, and the fields each serializer reads.
FUSION_PREDICATE_TYPES = {"match_block", "match_state", "is_same_block", "is_same_state",
                          "is_direction", "is_face_visible", "match_block_in_front",
                          "match_state_in_front", "and", "or", "not", "always", "never"}
FUSION_PREDICATE_KEYS = {"type", "block", "blocks", "ignore_missing", "predicates", "directions",
                         "properties"}   # match_state; verified in MatchStateConnectionPredicate

# Top-level keys of a `fusion/model_modifiers/blocks/<name>.json`, recovered by disassembling
# BlockModelModifierReloadListener.parseResource and listing every key it queries with has().
# Same reason as the list above: a misspelled key here is read by nothing and warned about by
# nothing. `ignore_missing_targets` in particular would silently revert the whole-family failure
# this list is here to prevent - see the modifier written in build().
FUSION_MODIFIER_KEYS = {"targets", "append", "append_models", "default_model_overrides",
                        "priority", "show_breaking_overlay", "pane_culling_fix",
                        "ignore_missing_targets"}


def check_fusion_modifier_keys(modifier):
    """Fail the build on a model-modifier key Fusion would silently ignore."""
    unknown = set(modifier) - FUSION_MODIFIER_KEYS
    if unknown:
        sys.exit(f"Unknown Fusion model-modifier key(s) {sorted(unknown)} - Fusion ignores these "
                 f"silently. Known: {sorted(FUSION_MODIFIER_KEYS)}")


def check_fusion_keys(meta):
    """Fail the build on a key or predicate type Fusion would silently ignore."""
    unknown = set(meta) - FUSION_TEXTURE_KEYS
    if unknown:
        sys.exit(f"Unknown Fusion texture key(s) {sorted(unknown)} - Fusion ignores these silently. "
                 f"Known: {sorted(FUSION_TEXTURE_KEYS)}")
    for predicate in meta.get("connections", []):
        bad_keys = set(predicate) - FUSION_PREDICATE_KEYS
        if bad_keys:
            sys.exit(f"Unknown connection-predicate field(s) {sorted(bad_keys)}")
        if predicate.get("type") not in FUSION_PREDICATE_TYPES:
            sys.exit(f"Unknown connection-predicate type {predicate.get('type')!r}")


# Families whose pack overlay DELIBERATELY differs from the mod's copy, so the drift check skips them.
DRIFT_BY_DESIGN = {
    # Owner's decision: unobtainium is a STILL image in the mod, and animates only with this pack.
    "unobtainium": "still in the mod, animated in the pack",
    # Cobblemon: the mod draws per-host precomposited overlays, the pack one solved universal each.
    **{f"cb_{_o}_stone": "per-host in the mod, solved universal in the pack"
       for _o in ("dawn", "dusk", "fire", "ice", "leaf", "moon", "shiny", "sun", "thunder", "water")},
    "cb_nether_fire_stone": "per-host in the mod, solved universal in the pack",
}


def warn_if_overlays_drifted():
    """
    Warn when a family's overlay here differs from the MOD's copy of the same file.

    The two are deliberately duplicated: this repo is private and separate, so the pack has to build
    without the mod checked out. But the mod's blocks DRAW the overlay and this pack derives the glow
    and the connected sheet from it, so if the two copies diverge the glow silently stops lining up
    with the blocks it is meant to sit on. Nothing in game would tell you.

    Skipped entirely when the mod is not present alongside, which is the normal standalone case.
    """
    dirs = mod_asset_dirs("textures", "block")
    if not dirs:
        return
    drifted = []
    for family in FAMILIES:
        if family in DRIFT_BY_DESIGN:
            continue
        ours = art_path(family, "overlays")
        if not os.path.exists(ours):
            continue
        name = f"{FAMILIES[family].get('overlay', family)}_overlay.png"
        # Compare against every branch that has the file; they should all agree, and a mismatch on
        # any of them is worth surfacing. Named by the branch's own folder, which is the first path
        # part below the parent (walking a fixed number of levels up landed on "src/main").
        parent = os.path.normpath(os.path.join(root(), ".."))
        branches = []
        for mod_textures in dirs:
            theirs = os.path.join(mod_textures, name)
            if not os.path.exists(theirs):
                continue
            branch = branch_of(mod_textures)
            # A LEGACY_ART family's old drawing is the right one on a branch whose mod still has it.
            expected = art_path(family, "overlays", family in LEGACY_ART and branch in LEGACY_BRANCHES)
            with open(expected, "rb") as a, open(theirs, "rb") as b:
                if a.read() != b.read():
                    branches.append(branch)
        if branches:
            drifted.append(f"{family} ({', '.join(branches)})")
    if drifted:
        print(f"  !! OVERLAY DRIFT: {', '.join(drifted)} differ from the mod's copy.")
        print("  !! The glow will not line up with those blocks in game. Sync them before shipping.")


# Mods we deliberately do NOT cover, and the reason, so nobody "completes" the set later.
#
# Both are ALL RIGHTS RESERVED. Our overlays are hand drawn from the mod's own ore art, which is
# derived work, and we have no permission. This is the same call already made for Immersive Ores.
# The bar is permission, not whether anyone would mind: another pack covering Galosphere is not
# evidence that it is allowed, only that they either asked or did not check.
#
# The art stays in assets/ so nothing is lost if permission is ever granted. It is the BUILD that
# must stay clean, which assert_no_excluded_mods enforces over the finished tree.
EXCLUDED_NAMESPACES = {
    "galosphere": "All Rights Reserved, no permission sought",
    "alltheores": "All Rights Reserved, no permission sought",
}


def assert_no_excluded_mods(out_dir):
    """
    Refuse to zip if an excluded mod reached the build, by PATH or by FILE CONTENT.

    Content matters as much as path: a namespace can reach a pack through a model_modifier target
    list or a connection list without ever appearing in a filename. That is exactly how the
    Immersive Ores gating had to work on the old build.
    """
    hits = []
    for root, _dirs, files in os.walk(out_dir):
        for f in files:
            p = os.path.join(root, f)
            rel = os.path.relpath(p, out_dir)
            for ns in EXCLUDED_NAMESPACES:
                if ns in rel.lower():
                    hits.append(f"{rel} (path)")
                    continue
                if f.endswith((".json", ".mcmeta", ".txt", ".md")):
                    try:
                        with io.open(p, encoding="utf-8") as fh:
                            if ns in fh.read().lower():
                                hits.append(f"{rel} (contents: {ns})")
                    except (OSError, UnicodeDecodeError):
                        pass
    if hits:
        sys.exit("Excluded mod reached the build, refusing to zip:\n  " + "\n  ".join(sorted(hits)))


def assert_owns_only_its_namespace(out_dir):
    """
    Refuse to zip if the pack writes into anyone else's namespace.

    This is not hypothetical tidiness. The first Fusion build emitted its connecting SHEETS to
    assets/seamlessores/textures/block/<family>_overlay.png - the mod's OWN ore-layer texture - so
    every one of the 40 variants rendered a 128x96 sheet stretched across the block. Nothing warned;
    a resource pack overriding a mod's assets is completely legal. The only defence is this check.

    model_modifiers APPEND a layer, so we never need to touch a host block's textures. If a future
    change genuinely needs to retexture something, it should be deliberate enough to edit this.

    THE ONE EXCEPTION is Meadow's alpine salt blockstate (SALT_BLOCKSTATE), in the pack root only, and
    it is allowed by exact path: any other file in any other namespace still refuses the zip.
    """
    intruders = set()
    for base in ("", CURRENT_ART_DIR):
        assets = os.path.join(out_dir, base, "assets")
        if not os.path.isdir(assets):
            continue
        for ns in os.listdir(assets):
            if ns == PACK_NAMESPACE:
                continue
            files = [os.path.join(folder, name) for folder, _dirs, names in os.walk(os.path.join(assets, ns))
                     for name in names]
            if not files:
                intruders.add(f"{base}/assets/{ns}".lstrip("/"))
            for path in files:
                rel = os.path.relpath(path, os.path.join(out_dir, base)).replace(os.sep, "/")
                if base or rel != SALT_BLOCKSTATE:
                    intruders.add(f"{base}/{rel}".lstrip("/"))
    if intruders:
        sys.exit("Pack writes outside its own namespace, refusing to zip: "
                 + ", ".join(f"{n}/" for n in sorted(intruders))
                 + f"\n  (only assets/{PACK_NAMESPACE}/ is allowed - see the namespace note above)")


def write_plain_glow(source, target):
    """A plain emissive texture and its mcmeta: the art as drawn, or in the animated pack its glint."""
    payload = {"fusion": {"emissive": True, "render_type": generated_render_type("cutout") if GLINT else "cutout"}}
    check_fusion_keys(payload["fusion"])
    if GLINT:
        from PIL import Image
        glint_strip(Image.open(source).convert("RGBA")).save(target)
        payload["animation"] = glint_animation()
    else:
        shutil.copyfile(source, target)
    write_json(f"{target}.mcmeta", payload)


def build_whole_block_glow(out_dir, tex_dir):
    """
    Ship the whole-block glow entries, identically in both variants (glinted in the animated pack).

    These get no border and no connection, so nothing here depends on `bordered`. The appended model
    is a full opaque copy of the block's own texture marked emissive, which is what makes the entire
    block glow rather than a set of blobs on it.
    """
    for name, spec in WHOLE_BLOCK_GLOW.items():
        appended = {}
        for slot, texture in spec["textures"].items():
            source = os.path.join(root(), "assets", "whole_block", f"{texture}.png")
            if not os.path.exists(source):
                sys.exit(f"Missing whole-block art: {source}")
            write_plain_glow(source, os.path.join(tex_dir, f"{texture}_glow.png"))
            appended[slot] = f"{PACK_NAMESPACE}:block/{texture}_glow"

        model = {
            "loader": "fusion:model",
            "type": "base",
            "parent": spec["parent"],
            "textures": appended,
        }
        if "elements" in spec:
            # A block drawn with its own geometry (not a plain cube) glows over that same geometry.
            with open(os.path.join(root(), "assets", "whole_block", spec["elements"]), encoding="utf-8") as handle:
                model["elements"] = json.load(handle)
        write_json(os.path.join(out_dir, "assets", PACK_NAMESPACE, "models", "block", f"{name}_glow.json"), model)
        modifier = {
            "targets": spec["targets"],
            "append": [f"{PACK_NAMESPACE}:block/{name}_glow"],
            "show_breaking_overlay": False,
            "ignore_missing_targets": True,
        }
        check_fusion_modifier_keys(modifier)
        write_json(os.path.join(out_dir, "assets", PACK_NAMESPACE, "fusion", "model_modifiers", "blocks",
                                f"{name}.json"), modifier)
        print(f"  {name:<12} {len(spec['targets']):2d} targets  whole block glow, no border")
    return len(WHOLE_BLOCK_GLOW)


# MEADOW'S ALPINE SALT GLOW: THE ONE BLOCKSTATE THIS PACK OVERRIDES.
#
# alpine_salt_ore has two weight-1 model variants whose salt sits in different places, and Fusion
# appends per BLOCK, so no model modifier can follow the variant vanilla picked (Fusion 1.3.15 still
# has no predicate that tells weighted variants apart). Satisfy granted the glow on 14 Sept 2026, so
# the pack overrides Meadow's blockstate with the SAME list in the same order with the same weights,
# which leaves vanilla's position-seeded pick unchanged, and each entry points at a Fusion composite
# of Meadow's own model plus our glow layer: the same pair an appended model modifier produces.
# Composite models exist, reading plain model ids, from Fusion 1.3.0 up (checked in 1.3.0 and 1.3.15).
#
# Meadow drew the salt differently per era and named the models differently (read from both jars):
#   1.3.25 (1.20.1): alpine_salt_ore_1, alpine_salt_ore_2
#   1.4.8  (1.21.1): alpine_salt_ore_0, alpine_salt_ore_1
# So the blockstate is written ONCE, naming our own two composites, and the composites and their
# overlay textures split by era exactly like LEGACY_ART: the 1.20.1 pair in the pack root and the
# 1.21.1 pair in current_art/, which only 1.20.2 and up read. Meadow has nothing between 1.20.1 and
# 1.21.1 and nothing at 26.x, where the blockstate names a block that does not exist and is ignored.
#
# WITHOUT FUSION the composites cannot load: on Forge and NeoForge the salt ore shows the missing
# model. On Fabric each composite also names Meadow's model as its `parent`, which vanilla renders.
# Fusion is the pack's one required dependency.
SALT_BLOCKSTATE = "assets/meadow/blockstates/alpine_salt_ore.json"
SALT_ERAS = {
    # era: (the Meadow jar these were read from, [(Meadow's model, our overlay in assets/overlays/meadow)])
    "legacy": ("letsdo-meadow-forge-1.3.25.jar",
               [("meadow:block/alpine_salt_ore_1", "alpine_salt_overlay_1_old"),
                ("meadow:block/alpine_salt_ore_2", "alpine_salt_overlay_2_old")]),
    "current": ("letsdo-meadow-neoforge-1.4.8.jar",
                [("meadow:block/alpine_salt_ore_0", "alpine_salt_overlay_0"),
                 ("meadow:block/alpine_salt_ore_1", "alpine_salt_overlay_1")]),
}


def verify_salt_against_meadow():
    """
    Refuse to build if a Meadow jar beside the pack disagrees with the list the override copies.

    A different order, weight or model name would change which variant a block picks, and the glow
    would sit on bare limestone. The jars live in ../references/jars; without them the pack still
    builds (it must build standalone), and says the check was skipped.
    """
    from PIL import Image
    jars = os.path.normpath(os.path.join(root(), "..", "references", "jars"))
    if len(SALT_ERAS["legacy"][1]) != len(SALT_ERAS["current"][1]):
        sys.exit("SALT_ERAS: both eras must list the same number of variants, one blockstate serves both")
    for era, (jar, entries) in SALT_ERAS.items():
        path = os.path.join(jars, jar)
        if not os.path.exists(path):
            print(f"  -- {jar} is not in references/jars: the {era} salt override was not re-verified")
            continue
        with zipfile.ZipFile(path) as z:
            listed = json.loads(z.read("assets/meadow/blockstates/alpine_salt_ore.json")).get("variants", {}).get("")
            want = [{"model": model, "weight": 1} for model, _art in entries]
            if listed != want:
                sys.exit(f"{jar}: alpine_salt_ore lists {listed}, the override copies {want}. "
                         f"Update SALT_ERAS from the jar before building.")
            for model, art in entries:
                texture = Image.open(io.BytesIO(z.read(
                    f"assets/meadow/textures/block/{model.split('/')[-1]}.png"))).convert("RGBA")
                overlay = Image.open(os.path.join(art_dir("overlays", "meadow"), f"{art}.png")).convert("RGBA")
                opaque = [(x, y) for y in range(16) for x in range(16) if overlay.getpixel((x, y))[3]]
                exact = sum(1 for p in opaque if overlay.getpixel(p)[:3] == texture.getpixel(p)[:3])
                if exact != len(opaque):
                    print(f"  !! {art}: only {exact} of {len(opaque)} pixels match {model} in {jar}")


def build_salt_glow(out_dir, tex_dir, current_tex_dir):
    """Write the alpine salt override: one blockstate, and per era the composites and overlays."""
    write_json(os.path.join(out_dir, *SALT_BLOCKSTATE.split("/")), {"variants": {"": [
        {"model": f"{PACK_NAMESPACE}:block/meadow_alpine_salt_{i}", "weight": 1}
        for i in range(len(SALT_ERAS["legacy"][1]))]}})
    model_dirs = {"legacy": os.path.join(out_dir, "assets", PACK_NAMESPACE, "models", "block"),
                  "current": os.path.join(out_dir, CURRENT_ART_DIR, "assets", PACK_NAMESPACE, "models", "block")}
    tex_dirs = {"legacy": tex_dir, "current": current_tex_dir}
    for era, (_jar, entries) in SALT_ERAS.items():
        os.makedirs(model_dirs[era], exist_ok=True)
        os.makedirs(tex_dirs[era], exist_ok=True)
        for i, (meadow_model, art) in enumerate(entries):
            texture = f"meadow_alpine_salt_{i}_overlay"
            source = os.path.join(art_dir("overlays", "meadow"), f"{art}.png")
            if not os.path.exists(source):
                sys.exit(f"Missing art: {source}")
            write_plain_glow(source, os.path.join(tex_dirs[era], f"{texture}.png"))
            if era == "legacy":
                # The glow layer's model is the same in both eras; only its texture changes.
                write_json(os.path.join(model_dirs[era], f"{texture}.json"), {
                    "loader": "fusion:model",
                    "type": "base",
                    "parent": "block/cube_all",
                    "textures": {"all": f"{PACK_NAMESPACE}:block/{texture}"},
                })
            write_json(os.path.join(model_dirs[era], f"meadow_alpine_salt_{i}.json"), {
                "loader": "fusion:model",
                "type": "composite",
                "parent": meadow_model,      # ignored by Fusion; what vanilla Fabric renders without it
                "models": [meadow_model, f"{PACK_NAMESPACE}:block/{texture}"],
            })
    print(f"  {'meadow_alpine_salt':<12}  1 target   glow through the blockstate override "
          f"(1.20.1 art in the root, 1.21.1 art in {CURRENT_ART_DIR}/)")


def build(variant, spec):
    from PIL import Image

    # Each pack stages in its own folder: build/glow and build/border for the still pair, as always,
    # and build/animated-glow and build/animated-border for the animated pair.
    out_dir = os.path.join(root(), "build", variant if PACK == "glowing" else f"{PACK}-{variant}")
    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir, exist_ok=True)

    tex_dir = os.path.join(out_dir, "assets", PACK_NAMESPACE, "textures", "block")
    os.makedirs(tex_dir, exist_ok=True)

    bordered = variant == "border"
    mcmeta = {"pack": {
        "description": f"{PACK_NAME} - glowing"
                       + (" and connected" if bordered else "")
                       + " ores. Requires Fusion.",
        "pack_format": PACK_FORMAT,
        "supported_formats": [15, 999],
        "min_format": 15,
        "max_format": 999,
    }}
    current_tex_dir = os.path.join(out_dir, CURRENT_ART_DIR, "assets", PACK_NAMESPACE, "textures", "block")
    if LEGACY_ART:
        low, high = CURRENT_ART_FORMATS
        mcmeta["overlays"] = {"entries": [{
            "formats": [low, high], "min_format": low, "max_format": high, "directory": CURRENT_ART_DIR,
        }]}
        os.makedirs(current_tex_dir, exist_ok=True)
    write_json(os.path.join(out_dir, "pack.mcmeta"), mcmeta)
    build_pack_icon(out_dir)

    built = 0
    for family in FAMILIES:
        if ONLY is not None and family not in ONLY:
            continue
        targets = family_targets(family)
        if not targets:
            print(f"  !! {family}: no targets - skipped")
            continue
        border_only = FAMILIES[family].get("border_only", False)
        if border_only and not bordered:
            continue    # nothing to glow: a border-only family exists only in the Border variant
        for kind in (("borders",) if border_only else ("overlays", "borders")):
            if not os.path.exists(art_path(family, kind)):
                sys.exit(f"Missing art: {art_path(family, kind)}")
        render_type = FAMILIES[family].get("render_type", "cutout")

        from PIL import Image
        animation = ANIMATED.get(family)
        overlay_art = None if border_only else Image.open(art_path(family, "overlays")).convert("RGBA")
        # In the animated pack a still overlay gets the glint; an overlay its mod already animates
        # keeps that animation, and so keeps the proven `full` border below.
        glint = None
        if GLINT and overlay_art is not None and animation is None and frame_count(overlay_art) == 1:
            glint = glint_strip(overlay_art)
            animation = glint_animation()
        frames = 1 if border_only else frame_count(glint or overlay_art)
        # The border glints wherever the ore does, and on a border-only family (the salt outline) too.
        border_glint = GLINT and (glint is not None or border_only)
        if animation and frames == 1:
            sys.exit(f"{family} is declared in ANIMATED but its overlay is a single frame")
        if frames > 1 and not animation:
            sys.exit(f"{family}'s overlay has {frames} frames but no ANIMATED entry, so it would "
                     f"be stitched as one tall still image")

        # An animated family splits into two appended layers; everything else keeps the single
        # composited sheet that is already proven in game.
        split = bordered and (animation is not None or border_only)
        appended = []

        # Where each texture goes, and from which drawing. A LEGACY_ART family writes its old art to
        # the pack root and its current art to the overlay; models and modifiers exist once, in the
        # root, because they are the same in both eras.
        eras = [(tex_dir, False)]
        if family in LEGACY_ART:
            if (animation and glint is None) or border_only:
                sys.exit(f"{family}: LEGACY_ART only handles still families with an overlay")
            for kind in ("overlays", "borders"):
                if not os.path.exists(art_path(family, kind, legacy=True)):
                    sys.exit(f"Missing art: {art_path(family, kind, legacy=True)}")
            eras = [(tex_dir, True), (current_tex_dir, False)]

        # --- layer 1: the ore glow -----------------------------------------------------------
        # Plain (non-connecting) for the Glow variant and for any animated Border family, because
        # only a plain sprite can animate. A non-animated Border family folds this layer into the
        # sheet below instead, exactly as before.
        if (not bordered or split) and not border_only:
            texture = f"{family}_overlay"
            for era_dir, legacy in eras:
                if glint is not None:
                    era_art = Image.open(art_path(family, "overlays", legacy)).convert("RGBA")
                    glint_strip(era_art).save(os.path.join(era_dir, f"{texture}.png"))
                else:
                    shutil.copyfile(art_path(family, "overlays", legacy), os.path.join(era_dir, f"{texture}.png"))
            if EFFECT == "subtle":
                for era_dir, _legacy in eras:
                    faded(os.path.join(era_dir, f"{texture}.png"))
            meta = {"emissive": True, "render_type": generated_render_type(render_type)
                    if EFFECT == "subtle" or glint is not None else render_type}
            check_fusion_keys(meta)
            payload = {"fusion": meta}
            if animation:
                # The vanilla animation section sits BESIDE `fusion` at the root; Fusion reads it
                # through AnimationMetadataSection and keeps the frame list on its own sprite.
                payload["animation"] = animation
            for era_dir, _legacy in eras:
                write_json(os.path.join(era_dir, f"{texture}.png.mcmeta"), payload)
            glow_model = {
                "loader": "fusion:model",
                "type": "base",
                "parent": "block/cube_all",
                "textures": {"all": f"{PACK_NAMESPACE}:block/{texture}"},
            }
            write_json(os.path.join(out_dir, "assets", PACK_NAMESPACE, "models", "block",
                                    f"{texture}.json"), glow_model)
            appended.append(f"{PACK_NAMESPACE}:block/{texture}")

        # --- layer 2: the connecting border ---------------------------------------------------
        if bordered:
            texture = f"{family}_border" if split else f"{family}_overlay"
            for era_dir, legacy in eras:
                if border_glint:
                    # The border glints too: one whole border-only pieced sheet per glint frame,
                    # stacked, so every tile carries the same frames and timing as the ore beneath it.
                    master = Image.open(art_path(family, "borders", legacy)).convert("RGBA")
                    stack([build_pieced(frame) for frame in glint_frames(master)]).save(
                        os.path.join(era_dir, f"{texture}.png"))
                else:
                    build_sheet(family, spec, ore_layer=not split, legacy=legacy).save(
                        os.path.join(era_dir, f"{texture}.png"))
            meta = {
                "emissive": True,
                "render_type": render_type,
                "type": "connecting",
                "layout": "pieced" if border_glint else "full",
                # Connect to every block in the family, so a granite iron ore borders seamlessly
                # into vanilla stone and deepslate iron ore. `ignore_missing` keeps the pack quiet
                # when the mod that owns some of these targets is not installed.
                #
                # THE KEY IS `connections`, PLURAL, AND IT TAKES AN ARRAY. Fusion's deserialize()
                # does json.has("connections") -> isJsonArray() -> entries combined with
                # DefaultConnectionPredicates.or(...); a single object is also accepted. The first
                # build used "connection" (singular), which is simply not a key Fusion reads, so it
                # was ignored WITHOUT ANY WARNING and connection fell back to the default
                # is_same_block - i.e. each host stone's variant bordered only against itself.
                "connections": connection_predicates(family),
            }
            if EFFECT == "subtle":
                meta["render_type"] = "translucent"
                for era_dir, _legacy in eras:
                    faded(os.path.join(era_dir, f"{texture}.png"))
            check_fusion_keys(meta)
            sheet_payload = {"fusion": meta}
            if border_glint:
                sheet_payload["animation"] = glint_animation()
                meta["render_type"] = generated_render_type(meta["render_type"])
            for era_dir, _legacy in eras:
                write_json(os.path.join(era_dir, f"{texture}.png.mcmeta"), sheet_payload)
            border_model = {
                "loader": "fusion:model",
                "type": "connecting",
                "parent": "block/cube_all",
                "textures": {"all": f"{PACK_NAMESPACE}:block/{texture}"},
            }
            if border_glint:
                border_model["elements"] = lifted_cube()
                border_model["textures"]["particle"] = border_model["textures"]["all"]
            write_json(os.path.join(out_dir, "assets", PACK_NAMESPACE, "models", "block",
                                    f"{texture}.json"), border_model)
            appended.append(f"{PACK_NAMESPACE}:block/{texture}")

        # The modifier APPENDS those models to each target block without touching its own model.
        # Order matters: the border goes on last so it draws over the ore glow.
        modifier = {
            "targets": targets,
            "append": appended,
            "show_breaking_overlay": False,
            # WITHOUT THIS, ONE MISSING BLOCK KILLS THE WHOLE FAMILY - including vanilla's own ore.
            # Fusion parses the file as a unit: the first target it cannot resolve throws, the file
            # is discarded, and every other target in it silently loses its glow and border. So a
            # user with only Fusion installed got nothing at all, because every vanilla family also
            # lists this mod's host variants. Verified in BlockModelModifierReloadListener: with the
            # flag set, an unresolvable id is skipped and the rest are kept, and a family where
            # NOTHING resolves returns early with no error at all. Added in Fusion 1.3.0 (absent in
            # 1.2.12), and present in every 1.3.12 build down to mc1.16, so it is safe for the whole
            # version range this one file serves.
            "ignore_missing_targets": True,
        }
        check_fusion_modifier_keys(modifier)
        write_json(os.path.join(out_dir, "assets", PACK_NAMESPACE, "fusion", "model_modifiers", "blocks",
                                f"{family}.json"), modifier)

        note = "  + 47-tile sheet" if bordered else ""
        if split:
            note = f"  + animated plate ({frames}f) + border sheet"
        if family in LEGACY_ART:
            note += f"  (old art in the root, current art in {CURRENT_ART_DIR}/)"
        print(f"  {family:<12} {len(targets):2d} targets{note}")
        built += 1

    if ONLY is None:
        build_salt_glow(out_dir, tex_dir, current_tex_dir)
        if not bordered:
            built += 1      # in the Border variant the salt already counts, as its outline family
        built += build_whole_block_glow(out_dir, tex_dir)

    assert_owns_only_its_namespace(out_dir)
    assert_no_excluded_mods(out_dir)
    current_root = os.path.join(out_dir, CURRENT_ART_DIR)
    for folder, _dirs, files in os.walk(current_root):
        for filename in files:
            rel = os.path.relpath(os.path.join(folder, filename), current_root)
            if not os.path.exists(os.path.join(out_dir, rel)):
                sys.exit(f"{CURRENT_ART_DIR}/{rel} has no counterpart in the pack root, so 1.20.1 "
                         f"would not have it. The overlay may only replace files, never add them.")

    zip_path = os.path.join(root(), "build", f"{PACKS[PACK]['zip']}-{variant.capitalize()}.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for folder, _dirs, files in os.walk(out_dir):
            for filename in files:
                full = os.path.join(folder, filename)
                archive.write(full, os.path.relpath(full, out_dir))
    print(f"\n  {built} families -> {os.path.relpath(zip_path, root())} "
          f"({os.path.getsize(zip_path) // 1024} KB)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="print coverage and exit")
    parser.add_argument("--pack", choices=sorted(PACKS), help="build only this pair (default: both)")
    parser.add_argument("--families", help="comma-separated family keys to build, and nothing else")
    args = parser.parse_args()
    check_matrix_keys()
    global ONLY
    if args.families:
        ONLY = set(args.families.split(","))
        unknown = ONLY - set(FAMILIES)
        if unknown:
            sys.exit(f"unknown families: {', '.join(sorted(unknown))}")

    if args.list:
        def label(target):
            # A state-filtered target is a dict; show it the way a blockstate reads.
            if isinstance(target, str):
                return target
            props = ",".join(f"{k}={v}" for k, v in target["properties"].items())
            return f"{target['block']}[{props}]"

        total = 0
        for family in FAMILIES:
            targets = family_targets(family)
            total += len(targets)
            print(f"  {family:<12} ({FAMILIES[family]['art']:<15}) {len(targets):2d}  "
                  f"{', '.join(label(t) for t in targets)}")
        for name, spec in WHOLE_BLOCK_GLOW.items():
            total += len(spec["targets"])
            print(f"  {name:<12} ({'whole block':<15}) {len(spec['targets']):2d}  "
                  f"{', '.join(spec['targets'])}")
        print(f"  {'meadow_alpine_salt':<12} ({'override':<15})  1  meadow:alpine_salt_ore glows through the "
              f"blockstate override; its outline is the border-only family above")
        print(f"\n  {len(FAMILIES) + len(WHOLE_BLOCK_GLOW)} families, {total} blocks covered")
        print(f"  Mythic Metals families ({len(MYTHIC_METALS)}): active only where that mod exists,"
              f" which is 1.21.4 and below")
        return

    try:
        import PIL  # noqa: F401
    except ImportError:
        sys.exit("Pillow is required. Run this with 'py -3.14'.")

    warn_if_overlays_drifted()
    verify_salt_against_meadow()
    spec = layout()
    for pack in ([args.pack] if args.pack else [k for k, v in PACKS.items() if v.get("publish", True)]):
        use_pack(pack)
        print(f"== {PACK_NAME}")
        build("glow", spec)
        print()
        build("border", spec)
        print()


if __name__ == "__main__":
    main()
