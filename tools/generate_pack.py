#!/usr/bin/env python3
"""
Builds the Seamless Glowing Ores resource pack(s) in FUSION format.

    py -3.14 tools/generate_pack.py           # build both variants
    py -3.14 tools/generate_pack.py --list    # print coverage and exit

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
    "diamond":  dict(art=VANILLA, targets=["minecraft:diamond_ore", "minecraft:deepslate_diamond_ore"]),
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
    # Ice and Fire's sapphire has no deepslate form: its feature targets stone_ore_replaceables only.
    "iaf": ("ice and fire", "iceandfire", {
        "silver":   ("silver", ["silver_ore", "deepslate_silver_ore"]),
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
    # Mystical Agriculture (MIT). Its own models are already two-layer; the glow appends on top.
    "ma": ("mystical agriculture", "mysticalagriculture", {
        "inferium":   ("inferium", ["inferium_ore", "deepslate_inferium_ore"]),
        "prosperity": ("prosperity", ["prosperity_ore", "deepslate_prosperity_ore"]),
        "soulium":    ("soulium", ["soulium_ore"]),
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
# The mod names its variants <host>_<ore>_ore, and that <ore> is NOT the family key here. Keys are
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
                 "tr_cinnabar": "nether", "tr_pyrite": "nether", "tr_sphalerite": "nether",
                 "er_benitoite": "nether", "sg_opal": "overworld"})
SO_HOSTS.update({"expores_experience": "overworld", "expores_nether_experience": "nether"})
# Mekanism itself is not supported by the mod (its ore feature is a custom type the mod's injector
# cannot rebind), so the mek_* families stay source-mod only and take no suffix.

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

# Ruby and sapphire: Mythic Upgrades' families keep only the NETHER variants (the overworld ones are
# Silent's Gems', above), and Silent's Gems' families name the mod's overlay key so the drift check
# compares the right pair of files.
for _gem in ("ruby", "sapphire"):
    FAMILIES[_gem]["hosts"] = "nether"
    FAMILIES[f"sg_{_gem}"]["overlay"] = f"silents_{_gem}"

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

# MEADOW'S ALPINE SALT: BORDER ONLY, no glow. Its salt is Meadow's own art (resources are All Rights
# Reserved) and its two random texture variants cannot be followed by a per-block glow anyway. An
# outline needs neither: it is our own art and does not depend on where the salt sits. The glow
# waits on the author's answer. Glow variant: nothing. Border variant: the outline alone.
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
                         "assets", "overlays", "extreme reactors", f"{_er}_overlay.png.mcmeta")
    with open(_meta, encoding="utf-8") as _handle:
        ANIMATED[f"er_{_er}"] = {k: v for k, v in json.load(_handle)["animation"].items()
                                 if k in ("frametime", "frames")}


def frame_count(image):
    """Frames in a vertical strip, or 1 for a plain square texture."""
    if image.width and image.height % image.width == 0:
        return image.height // image.width
    return 1


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


def art_path(family, kind):
    """
    kind is 'overlays' or 'borders'; both are laid out as assets/<kind>/<mod>/<family>_<x>.png.

    A family may override either with `overlay_file` / `border_file`, which is how a split family
    reuses its sibling's art. See the split families above.
    """
    stem = "overlay" if kind == "overlays" else "border"
    name = FAMILIES[family].get(f"{stem}_file", f"{family}_{stem}")
    return os.path.join(root(), "assets", kind, FAMILIES[family]["art"], f"{name}.png")


def layout():
    """The Fusion `full` layout, recovered by running Fusion's own FullLayoutHandler."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fusion_full_layout.json")
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if len(data["tiles"]) != 47:
        sys.exit(f"{path}: expected 47 tiles, found {len(data['tiles'])}")
    return data


# Host stones the mod uses as an id PREFIX. A block only counts as ours if its prefix is one of
# these exactly. Without that check `basalt_midas_gold_ore` ends with `_gold_ore` and would be
# swept into the vanilla `gold` family, whose prefix would come out as the non-host "basalt_midas".
MOD_HOSTS = {"granite", "diorite", "andesite", "tuff", "basalt", "blackstone"}
NETHER_HOSTS = {"basalt", "blackstone"}


def mod_asset_dirs(*parts):
    """Every branch of the MOD's assets, newest content first.

    One pack file serves every Minecraft version, so it should list the UNION of our blocks across
    branches: `ignore_missing_targets` skips the ones that do not exist on the version in use. The
    branches genuinely differ - Mythic Metals support lives on 1.21.x, so the 26.2 branch alone
    would miss 68 blocks - hence a scan rather than one hardcoded path.

    The mod lives in a separate repository, so this must tolerate all of them being absent: the pack
    has to build standalone.
    """
    parent = os.path.normpath(os.path.join(root(), ".."))
    if not os.path.isdir(parent):
        return []
    found = []
    for name in sorted(os.listdir(parent)):
        if not (name.startswith("seamlessores-") and name.endswith("-multiloader")):
            continue
        path = os.path.join(parent, name, "common", "src", "main", "resources",
                            "assets", MOD_NAMESPACE, *parts)
        if os.path.isdir(path):
            found.append(path)
    return found


def mod_block_targets(family):
    """
    Our own blocks in this family, discovered from the mod's blockstates rather than hardcoded.

    The mod names them <host>_<ore>_ore. Reading the real directory means a block we add or drop in
    the mod cannot silently fall out of step with the pack.
    """
    suffix = FAMILIES[family].get("suffix", family)
    # An ore with BOTH an overworld and a nether form produces the same <host>_<suffix>_ore name for
    # each, and only the host tells them apart: vanilla gold vs nether gold, and Mythic Metals'
    # banglum vs nether banglum. Declared per family rather than hardcoded by name, so the next such
    # pair is one key. Everything else takes any host - including quartz, whose only variants ARE
    # the nether ones, so it must not be filtered as if it were overworld.
    hosts = FAMILIES[family].get("hosts")
    if hosts == "nether":
        allowed = lambda host: host in NETHER_HOSTS          # noqa: E731
    elif hosts == "overworld":
        allowed = lambda host: host not in NETHER_HOSTS      # noqa: E731
    else:
        allowed = lambda host: True                          # noqa: E731

    found = set()
    for states in mod_asset_dirs("blockstates"):
        for name in sorted(os.listdir(states)):
            if not name.endswith(".json"):
                continue
            block = name[:-5]
            if not block.endswith(f"_{suffix}_ore"):
                continue
            host = block[: -len(f"_{suffix}_ore")]
            if host in MOD_HOSTS and allowed(host):
                found.add(f"{MOD_NAMESPACE}:{block}")
    return sorted(found)


def family_targets(family):
    return list(FAMILIES[family]["targets"]) + mod_block_targets(family)


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


def build_sheet(family, spec, ore_layer=True):
    """The whole 8x6 connecting sheet for one family, as a single image.

    With ore_layer=False the tiles carry ONLY the border on a transparent field, which is what an
    animated family needs: its ore glow is appended separately as a plain animated plate, because a
    connecting texture cannot animate.
    """
    from PIL import Image
    master = Image.open(art_path(family, "borders")).convert("RGBA")
    if ore_layer:
        overlay = Image.open(art_path(family, "overlays")).convert("RGBA")
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
            with open(ours, "rb") as a, open(theirs, "rb") as b:
                if a.read() != b.read():
                    branch = os.path.relpath(mod_textures, parent).split(os.sep)[0]
                    branches.append(branch.removeprefix("seamlessores-").removesuffix("-multiloader"))
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
    """
    intruders = set()
    assets = os.path.join(out_dir, "assets")
    if os.path.isdir(assets):
        intruders = {n for n in os.listdir(assets) if n != PACK_NAMESPACE}
    if intruders:
        sys.exit("Pack writes outside its own namespace, refusing to zip: "
                 + ", ".join(f"assets/{n}/" for n in sorted(intruders))
                 + f"\n  (only assets/{PACK_NAMESPACE}/ is allowed - see the namespace note above)")


def build_whole_block_glow(out_dir, tex_dir):
    """
    Ship the whole-block glow entries, identically in both variants.

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
            shutil.copyfile(source, os.path.join(tex_dir, f"{texture}_glow.png"))
            meta = {"emissive": True, "render_type": "cutout"}
            check_fusion_keys(meta)
            write_json(os.path.join(tex_dir, f"{texture}_glow.png.mcmeta"), {"fusion": meta})
            appended[slot] = f"{PACK_NAMESPACE}:block/{texture}_glow"

        write_json(os.path.join(out_dir, "assets", PACK_NAMESPACE, "models", "block", f"{name}_glow.json"), {
            "loader": "fusion:model",
            "type": "base",
            "parent": spec["parent"],
            "textures": appended,
        })
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


def build(variant, spec):
    from PIL import Image

    out_dir = os.path.join(root(), "build", variant)
    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir, exist_ok=True)

    tex_dir = os.path.join(out_dir, "assets", PACK_NAMESPACE, "textures", "block")
    os.makedirs(tex_dir, exist_ok=True)

    bordered = variant == "border"
    write_json(os.path.join(out_dir, "pack.mcmeta"), {"pack": {
        "description": f"{PACK_NAME} - glowing"
                       + (" and connected" if bordered else "")
                       + " ores. Requires Fusion.",
        "pack_format": PACK_FORMAT,
        "supported_formats": [15, 999],
        "min_format": 15,
        "max_format": 999,
    }})
    build_pack_icon(out_dir)

    built = 0
    for family in FAMILIES:
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
        frames = 1 if border_only else frame_count(overlay_art)
        if animation and frames == 1:
            sys.exit(f"{family} is declared in ANIMATED but its overlay is a single frame")
        if frames > 1 and not animation:
            sys.exit(f"{family}'s overlay has {frames} frames but no ANIMATED entry, so it would "
                     f"be stitched as one tall still image")

        # An animated family splits into two appended layers; everything else keeps the single
        # composited sheet that is already proven in game.
        split = bordered and (animation is not None or border_only)
        appended = []

        # --- layer 1: the ore glow -----------------------------------------------------------
        # Plain (non-connecting) for the Glow variant and for any animated Border family, because
        # only a plain sprite can animate. A non-animated Border family folds this layer into the
        # sheet below instead, exactly as before.
        if (not bordered or split) and not border_only:
            texture = f"{family}_overlay"
            shutil.copyfile(art_path(family, "overlays"), os.path.join(tex_dir, f"{texture}.png"))
            meta = {"emissive": True, "render_type": render_type}
            check_fusion_keys(meta)
            payload = {"fusion": meta}
            if animation:
                # The vanilla animation section sits BESIDE `fusion` at the root; Fusion reads it
                # through AnimationMetadataSection and keeps the frame list on its own sprite.
                payload["animation"] = animation
            write_json(os.path.join(tex_dir, f"{texture}.png.mcmeta"), payload)
            write_json(os.path.join(out_dir, "assets", PACK_NAMESPACE, "models", "block",
                                    f"{texture}.json"), {
                "loader": "fusion:model",
                "type": "base",
                "parent": "block/cube_all",
                "textures": {"all": f"{PACK_NAMESPACE}:block/{texture}"},
            })
            appended.append(f"{PACK_NAMESPACE}:block/{texture}")

        # --- layer 2: the connecting border ---------------------------------------------------
        if bordered:
            texture = f"{family}_border" if split else f"{family}_overlay"
            build_sheet(family, spec, ore_layer=not split).save(os.path.join(tex_dir, f"{texture}.png"))
            meta = {
                "emissive": True,
                "render_type": render_type,
                "type": "connecting",
                "layout": "full",
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
            check_fusion_keys(meta)
            write_json(os.path.join(tex_dir, f"{texture}.png.mcmeta"), {"fusion": meta})
            write_json(os.path.join(out_dir, "assets", PACK_NAMESPACE, "models", "block",
                                    f"{texture}.json"), {
                "loader": "fusion:model",
                "type": "connecting",
                "parent": "block/cube_all",
                "textures": {"all": f"{PACK_NAMESPACE}:block/{texture}"},
            })
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
        print(f"  {family:<12} {len(targets):2d} targets{note}")
        built += 1

    built += build_whole_block_glow(out_dir, tex_dir)

    assert_owns_only_its_namespace(out_dir)
    assert_no_excluded_mods(out_dir)

    zip_path = os.path.join(root(), "build", f"SeamlessGlowingOres-{variant.capitalize()}.zip")
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
    args = parser.parse_args()

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
        print(f"\n  {len(FAMILIES) + len(WHOLE_BLOCK_GLOW)} families, {total} blocks covered")
        print(f"  Mythic Metals families ({len(MYTHIC_METALS)}): active only where that mod exists,"
              f" which is 1.21.4 and below")
        return

    try:
        import PIL  # noqa: F401
    except ImportError:
        sys.exit("Pillow is required. Run this with 'py -3.14'.")

    warn_if_overlays_drifted()
    spec = layout()
    build("glow", spec)
    print()
    build("border", spec)


if __name__ == "__main__":
    main()
