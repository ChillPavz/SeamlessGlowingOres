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

# Resource pack_format for 26.2 is 88. The four-field form is the one verified to load without
# errors on 26.x.
PACK_FORMAT = 88

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

# Mythic Upgrades (MIT, 26.2 on all four loaders, namespace `mythicupgrades`). Block ids read from
# the real jar: 5 overworld gems have stone+deepslate pairs, ruby/sapphire are nether-only,
# ametrine/jade are End-only.
for _gem in ("aquamarine", "citrine", "peridot", "topaz", "necoium"):
    FAMILIES[_gem] = dict(art="mythic upgrades",
                          targets=[f"mythicupgrades:{_gem}_ore",
                                   f"mythicupgrades:deepslate_{_gem}_ore"])
for _gem in ("ruby", "sapphire", "ametrine", "jade"):
    FAMILIES[_gem] = dict(art="mythic upgrades", targets=[f"mythicupgrades:{_gem}_ore"])

# MYTHIC METALS - art is ready (20 families) but DELIBERATELY NOT BUILT.
# The mod is Fabric-only and stops at 1.21.4, so there are no 26.2 block ids to target and no way to
# verify them. Shipping modifiers pointing at blocks that cannot exist would be dead weight at best.
# When the port lands, read the ids out of that jar and add the families here - the art needs no
# changes. See ../PROJECTS.md.
MYTHIC_METALS_PENDING = [
    "adamantite", "aquarium", "banglum", "carmot", "kyber", "manganese", "midas_gold", "morkite",
    "mythril", "orichalcum", "osmium", "palladium", "platinum", "prometheum", "quadrillum",
    "runite", "silver", "starrite", "tin", "unobtainium",
]

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
    """kind is 'overlays' or 'borders'; both are laid out as assets/<kind>/<mod>/<family>_<x>.png."""
    stem = "overlay" if kind == "overlays" else "border"
    return os.path.join(root(), "assets", kind, FAMILIES[family]["art"], f"{family}_{stem}.png")


def layout():
    """The Fusion `full` layout, recovered by running Fusion's own FullLayoutHandler."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fusion_full_layout.json")
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if len(data["tiles"]) != 47:
        sys.exit(f"{path}: expected 47 tiles, found {len(data['tiles'])}")
    return data


def mod_block_targets(family):
    """
    Our own blocks in this family, discovered from the mod's blockstates rather than hardcoded.

    The mod names them <host>_<ore>_ore. Reading the real directory means a block we add or drop in
    the mod cannot silently fall out of step with the pack.
    """
    states = os.path.join(root(), "..", "seamlessores-26.2-multiloader", "common", "src", "main",
                          "resources", "assets", MOD_NAMESPACE, "blockstates")
    states = os.path.normpath(states)
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
        write_json(os.path.join(out_dir, "assets", PACK_NAMESPACE, "fusion", "model_modifiers", "blocks",
                                f"{family}.json"), {
            "targets": targets,
            "append": [f"{PACK_NAMESPACE}:block/{texture}"],
            "show_breaking_overlay": False,
        })

        print(f"  {family:<12} {len(targets):2d} targets" + ("  + 47-tile sheet" if bordered else ""))
        built += 1

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
        print(f"\n  {len(FAMILIES)} families, {total} blocks covered")
        print(f"  pending (art ready, mod not at 26.2): "
              f"{len(MYTHIC_METALS_PENDING)} Mythic Metals families")
        return

    try:
        import PIL  # noqa: F401
    except ImportError:
        sys.exit("Pillow is required. Run this with 'py -3.14'.")

    spec = layout()
    build("glow", spec)
    print()
    build("border", spec)


if __name__ == "__main__":
    main()
