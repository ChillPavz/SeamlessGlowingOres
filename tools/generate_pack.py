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
    "gold":     dict(art=VANILLA, targets=["minecraft:gold_ore", "minecraft:deepslate_gold_ore"]),
    "lapis":    dict(art=VANILLA, targets=["minecraft:lapis_ore", "minecraft:deepslate_lapis_ore"]),
    "diamond":  dict(art=VANILLA, targets=["minecraft:diamond_ore", "minecraft:deepslate_diamond_ore"]),
    "emerald":  dict(art=VANILLA, targets=["minecraft:emerald_ore", "minecraft:deepslate_emerald_ore"]),
    "redstone": dict(art=VANILLA, targets=["minecraft:redstone_ore", "minecraft:deepslate_redstone_ore"]),
    # Nether gold is its own family: different art, different hosts, and it never touches the
    # overworld gold it would otherwise be grouped with.
    "nether_gold": dict(art=VANILLA, targets=["minecraft:nether_gold_ore"], suffix="gold"),
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
FAMILIES["nether_banglum"] = dict(art="mythic metals",
                                  targets=["mythicmetals:nether_banglum_ore"],
                                  border_file="banglum_border")
FAMILIES["calcite_starrite"] = dict(art="mythic metals",
                                    targets=["mythicmetals:calcite_starrite_ore"],
                                    overlay_file="starrite_overlay2", border_file="starrite_border")
FAMILIES["end_stone_starrite"] = dict(art="mythic metals",
                                      targets=["mythicmetals:end_stone_starrite_ore"],
                                      overlay_file="starrite_overlay2", border_file="starrite_border")

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


def mod_asset_dir(*parts):
    """A directory inside the MOD's assets, or a path that simply will not exist when it is absent.

    The mod lives in a separate repository, so every use of this must tolerate it being missing:
    this pack has to build standalone.
    """
    return os.path.normpath(os.path.join(
        root(), "..", "seamlessores-26.2-multiloader", "common", "src", "main", "resources",
        "assets", MOD_NAMESPACE, *parts))


def mod_block_targets(family):
    """
    Our own blocks in this family, discovered from the mod's blockstates rather than hardcoded.

    The mod names them <host>_<ore>_ore. Reading the real directory means a block we add or drop in
    the mod cannot silently fall out of step with the pack.
    """
    states = mod_asset_dir("blockstates")
    if not os.path.isdir(states):
        return []
    suffix = FAMILIES[family].get("suffix", family)
    # `gold` and `nether_gold` both produce blocks named <host>_gold_ore and are told apart only by
    # the host. Every other family owns its whole suffix - including quartz, whose only variants ARE
    # the nether ones, so it must not be filtered as if it were overworld.
    nether_hosts = {"basalt", "blackstone"}
    if family == "nether_gold":
        allowed = lambda host: host in nether_hosts          # noqa: E731
    elif family == "gold":
        allowed = lambda host: host not in nether_hosts      # noqa: E731
    else:
        allowed = lambda host: True                          # noqa: E731

    found = []
    for name in sorted(os.listdir(states)):
        if not name.endswith(".json"):
            continue
        block = name[:-5]
        if not block.endswith(f"_{suffix}_ore"):
            continue
        if allowed(block[: -len(f"_{suffix}_ore")]):
            found.append(f"{MOD_NAMESPACE}:{block}")
    return found


def family_targets(family):
    return list(FAMILIES[family]["targets"]) + mod_block_targets(family)


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


def build_sheet(family, spec):
    """The whole 8x6 connecting sheet for one family, as a single image."""
    from PIL import Image
    overlay = Image.open(art_path(family, "overlays")).convert("RGBA")
    master = Image.open(art_path(family, "borders")).convert("RGBA")
    if overlay.size != master.size:
        sys.exit(f"{family}: overlay {overlay.size} and border {master.size} must match")
    size = overlay.width
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
FUSION_PREDICATE_KEYS = {"type", "block", "blocks", "ignore_missing", "predicates", "directions"}

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


def warn_if_overlays_drifted():
    """
    Warn when a family's overlay here differs from the MOD's copy of the same file.

    The two are deliberately duplicated: this repo is private and separate, so the pack has to build
    without the mod checked out. But the mod's blocks DRAW the overlay and this pack derives the glow
    and the connected sheet from it, so if the two copies diverge the glow silently stops lining up
    with the blocks it is meant to sit on. Nothing in game would tell you.

    Skipped entirely when the mod is not present alongside, which is the normal standalone case.
    """
    mod_textures = mod_asset_dir("textures", "block")
    if not os.path.isdir(mod_textures):
        return
    drifted = []
    for family in FAMILIES:
        ours = art_path(family, "overlays")
        theirs = os.path.join(mod_textures, f"{FAMILIES[family].get('overlay', family)}_overlay.png")
        if not (os.path.exists(ours) and os.path.exists(theirs)):
            continue
        with open(ours, "rb") as a, open(theirs, "rb") as b:
            if a.read() != b.read():
                drifted.append(family)
    if drifted:
        print(f"  !! OVERLAY DRIFT: {', '.join(drifted)} differ from the mod's copy.")
        print("  !! The glow will not line up with those blocks in game. Sync them before shipping.")


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
        for kind in ("overlays", "borders"):
            if not os.path.exists(art_path(family, kind)):
                sys.exit(f"Missing art: {art_path(family, kind)}")

        texture = f"{family}_overlay"

        # 1. the texture: either the plain overlay, or the 8x6 connecting sheet
        if bordered:
            build_sheet(family, spec).save(os.path.join(tex_dir, f"{texture}.png"))
        else:
            shutil.copyfile(art_path(family, "overlays"), os.path.join(tex_dir, f"{texture}.png"))

        # 2. the mcmeta: emissive always; connecting only for the bordered variant. Both live in the
        #    SAME file, which is why this build needs no separate glow twin per tile.
        meta = {"emissive": True, "render_type": "cutout"}
        if bordered:
            meta.update({
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
                "connections": [
                    {"type": "match_block", "blocks": targets, "ignore_missing": True},
                ],
            })
        check_fusion_keys(meta)
        write_json(os.path.join(tex_dir, f"{texture}.png.mcmeta"), {"fusion": meta})

        # 3. the model that draws it, and 4. the modifier that APPENDS that model to each target
        #    block without touching the block's own model.
        write_json(os.path.join(out_dir, "assets", PACK_NAMESPACE, "models", "block", f"{texture}.json"), {
            "loader": "fusion:model",
            "type": "connecting" if bordered else "base",
            "parent": "block/cube_all",
            "textures": {"all": f"{PACK_NAMESPACE}:block/{texture}"},
        })
        modifier = {
            "targets": targets,
            "append": [f"{PACK_NAMESPACE}:block/{texture}"],
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

        print(f"  {family:<12} {len(targets):2d} targets" + ("  + 47-tile sheet" if bordered else ""))
        built += 1

    built += build_whole_block_glow(out_dir, tex_dir)

    assert_owns_only_its_namespace(out_dir)

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
        total = 0
        for family in FAMILIES:
            targets = family_targets(family)
            total += len(targets)
            print(f"  {family:<12} ({FAMILIES[family]['art']:<15}) {len(targets):2d}  "
                  f"{', '.join(targets)}")
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
