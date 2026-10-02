import os
import sys
import json
import math
import base64
import binascii
import zipfile
import struct
import re
import time
import uuid
import threading
import logging
import traceback
from io import BytesIO
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# Add parent directory to path so pz_core can be imported
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image
from pz_core.common.pz_pk2 import iter_embedded_tim2, unpack_pk2, unpack_room_pk2
from pz_core.ff2.pz_pk2_ff2 import (
    iter_embedded_tim2 as iter_embedded_tim2_ff2,
    reconstruct_sgd_textures as reconstruct_sgd_textures_ff2,
    unpack_pk2 as unpack_pk2_ff2,
)
from pz_core.ff3.pz_pk4 import (
    flip_uvs_vertical,
    iter_pk4_entries,
    parse_pk4_model,
)
from pz_core.ff1.pz_sgd_ff1 import (
    is_ff1_sgd,
    merge_sgd_models,
    parse_sgd as parse_sgd_ff1,
)
from pz_core.ff1.pz_lighting_ff1 import (
    apply_ff1_xbox_room_lighting,
    read_lit_sidecar,
)
from pz_core.ff3.pz_sgd_ff3 import merge_sgd_models as merge_sgd_ff3, parse_sgd as parse_sgd_ff3
from pz_core.ff2.pz_sgd_ff2 import merge_sgd_models as merge_sgd_ff2, parse_sgd as parse_sgd_ff2
from pz_core.ff1.pz_mdl_ff1 import FF1MDLError, parse_ff1_mdl
from pz_core.ff2.pz_mdl_ff2 import FF2MDLError, parse_ff2_mdl
from pz_core.ff1x.pz_mpx_ff1x import XboxMPXError, parse_xbox_asset
from pz_core.ff1x.pz_pkx_ff1x import PKXError, parse_pkx as parse_ff1x_pkx
from pz_core.ff1x.pz_xpr0 import XPR0Error
from pz_core.export.pz_export_xbox import (
    export_obj as export_xbox_aware_obj,
    export_glb as export_xbox_aware_glb,
)
from pz_core.ff2w.pz_mdlb_ff2w import (
    FF2WError,
    parse_ff2w_model,
    parse_ff2w_textures,
)
from pz_core.ff3.pz_tim2_ff3 import decode_tim2, render_tim2_clut_variation
from pz_core.ff1.pz_tim2_ff1 import reconstruct_sgd_textures
from pz_core.ff2.pz_tim2_ff2 import decode_tim2 as decode_tim2_ff2
from pz_core.common.pz_collision import (
    parse_room_collision_from_map,
    parse_all_rooms_collision_from_map,
    collision_to_sgd_model,
    parse_cld,
    parse_cld_folder
)
from pz_core.export.pz_export import export_glb, export_obj, export_dae, export_fbx, export_textures_png

# Where the user's own files live.
#
# Run from source that is the project root: two levels up from viewer/server.py.
# Frozen into a single exe it is the folder holding the exe, which is ONE level
# up -- two would walk straight out of the folder the user put it in and write
# into whatever sits above, or fail on a read-only parent.
#
# Static assets are the exception: they are read-only and ship inside the bundle,
# so they are read from wherever __file__ points -- PyInstaller's temporary
# folder when frozen. Anything the *user* creates has to live next to the
# executable instead, because that temporary folder is deleted on exit.
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
EXPORTS_DIR = os.path.join(APP_DIR, "exports")
PREFERENCES_FILE = os.path.join(APP_DIR, "PZViewer_paths.json")
os.makedirs(EXPORTS_DIR, exist_ok=True)


# Games the Asset Browser can browse. "ff2w" is the Wii release of Fatal
# Frame 2, whose models live in .mdlb / .pk2b containers instead of the PS2
# SGD archives, so it needs its own parser but shares the same folder plumbing.
GAME_IDS = ("ff1", "ff1x", "ff2", "ff2w", "ff3")
GAME_LABELS = {
    "ff1": "Fatal Frame 1 Files",
    "ff1x": "Fatal Frame 1 XBOX Files",
    "ff2": "Fatal Frame 2 Files",
    "ff2w": "Fatal Frame 2 Wii Files",
    "ff3": "Fatal Frame 3 Files",
}

XPR0_MAGIC = b"XPR0"

# Recolour archives: an XPR file can hold the geometry of an MPX in a different
# colourway, and an MPX with no same-stem XPR at all can borrow one. The order
# here is the cycle order and it is never reordered at request time -- the active
# archive is reported separately, so clicking through the variants cannot shuffle
# the list under the user's cursor.
#
# m000_spe5.xpr and m000_spe6.xpr were proven to be colourways of m000_miku4
# while their sibling MPXs still existed (those three files were byte-for-byte
# identical, SHA-256 8443b2188f90...); the MPX copies have since been deleted.
# m000_miku2 has no same-stem archive, so its colourways are the spe set.
XPR_RECOLOUR_VARIANTS = {
    "m000_miku4": ("m000_miku4.xpr", "m000_spe5.xpr", "m000_spe6.xpr"),
    "m000_miku2": ("m000_spe1.xpr", "m000_spe2.xpr", "m000_spe3.xpr"),
}


def _xpr_variants_for(mpx_path):
    """XPR files that recolour this MPX's geometry, in the order to cycle them.

    Only the archives that actually exist in the same folder are returned, so a
    partially copied data set degrades to the ones that are there rather than to
    a load failure.
    """
    stem = os.path.splitext(os.path.basename(mpx_path))[0].casefold()
    folder = os.path.dirname(os.path.abspath(mpx_path))
    found = []
    for name in XPR_RECOLOUR_VARIANTS.get(stem, ()):
        if os.path.isfile(os.path.join(folder, name)):
            found.append(name)
    return found


def _has_xbox_texture_archive(file_path):
    """True when a .mdl embeds an XPR0 texture archive, i.e. it is an Xbox asset.

    The PS2 and Xbox FF1 builds share the PK2_HEAD container and the .mdl
    extension, so the name cannot tell them apart. Xbox assets embed an XPR0
    archive and carry 0x1060 SGD records; neither appears anywhere in the PS2
    set. This replaced the deleted legacy reader's content sniffer for the sole
    purpose of refusing the file instead of mis-parsing it.
    """
    try:
        with open(file_path, "rb") as handle:
            return handle.read(4096).find(XPR0_MAGIC) != -1
    except OSError:
        return False
# Extensions the browser lists per game. Only loadable model containers are
# listed: for FF2 Wii the .ppdb/.anmb/.pakb/.zldb siblings are texture and
# animation payloads, and offering them as browsable models only produced
# unopenable entries in the file tree. "all" keeps the full union.
GAME_EXTENSIONS = {
    # .xpr is deliberately absent everywhere: it is the texture sidecar that
    # sits next to a sibling .mpx and is bound to it automatically, so listing it
    # only offers a row that opens an archive with no geometry of its own. The
    # load path still accepts one if the path is typed directly.
    'ff1': ('.mdl', '.pk2', '.sgd', '.tim2', '.mpx'),
    # The Xbox FF1 build. Characters arrive as .mpx and object/room assets as
    # .pkx; both carry geometry and XPR0 textures. The .mdl files are the same
    # PK2_HEAD container as the PS2 ones but use geometry records the PS2 parser
    # does not read. .mpk and .acs are non-standalone containers kept out of tree.
    'ff1x': ('.mpx', '.pkx'),
    'ff2': ('.mdl', '.pk2', '.sgd', '.tim2', '.tm2'),
    'ff3': ('.pk4', '.sgd', '.tm2'),
    'ff2w': ('.mdlb', '.pk2b'),
    'all': ('.mdl', '.pk2', '.pk4', '.sgd', '.cld', '.obj', '.tm2', '.tim2', '.png',
            '.mdlb', '.pk2b', '.mpx', '.pkx'),
}

# FF2 Wii ships each model in its own unit scale, so there is no single global
# factor. Measured against the FF3 assets the viewer already displays:
#   character 0.678 tall -> x45.5 lands on FF3's 30.9
#   room      30.7 wide  -> x3.26 lands on FF3's 100
#   door       0.9 wide  -> x20.0 lands on FF3's 18.0
# Items share the door convention (a key is 0.05 units, a pair of glasses 0.15),
# so both use x20. Keyed on container *and* bone count: a pk2b with one bone is
# room geometry, anything above that is a prop.
FF2W_SCALE_CHARACTER = 45.5
FF2W_SCALE_ROOM = 3.26
FF2W_SCALE_PROP = 20.0

def load_folder_preferences():
    try:
        with open(PREFERENCES_FILE, "r", encoding="utf-8") as preferences:
            data = json.load(preferences)
        return {
            key: str(value).replace("\\", "/")
            for key, value in data.items()
            if key in GAME_IDS and isinstance(value, str) and value.strip()
        }
    except (OSError, ValueError, TypeError):
        return {}


def save_folder_preference(game, path):
    if game not in GAME_IDS:
        return load_folder_preferences()
    paths = load_folder_preferences()
    if path and path.strip():
        paths[game] = path.strip().replace("\\", "/")
    else:
        paths.pop(game, None)
    temporary = PREFERENCES_FILE + ".tmp"
    with open(temporary, "w", encoding="utf-8") as preferences:
        json.dump(paths, preferences, indent=2)
        preferences.write("\n")
    os.replace(temporary, PREFERENCES_FILE)
    return paths


def folder_is_within_root(path, root):
    """Return whether path is root itself or a descendant of root."""
    try:
        return os.path.commonpath([
            os.path.abspath(path),
            os.path.abspath(root),
        ]) == os.path.abspath(root)
    except ValueError:
        return False

CURRENT_STATE = {
    "model": None,
    "textures": [],
    "collision": [],
    "source_file": "",
    "model_type": "none"
}
LOAD_CACHE = {}
BATCH_JOBS = {}  # job_id -> {status, total, done, failed, skipped, current, errors, log, output_dir}

_LOAD_LOGGER = logging.getLogger("pzviewer.load")
if not _LOAD_LOGGER.handlers:
    _LOAD_LOGGER.addHandler(logging.StreamHandler())
_LOAD_LOGGER.setLevel(logging.INFO)
_LOAD_LOGGER.propagate = False


class LoadProgress:
    """Immediate, compact progress output for one viewer file load."""

    def __init__(self, file_path):
        self.file_path = os.path.basename(os.path.normpath(file_path))
        self.started = time.perf_counter()

    def log(self, phase, message=""):
        elapsed_ms = (time.perf_counter() - self.started) * 1000.0
        suffix = f" {message}" if message else ""
        _LOAD_LOGGER.info(
            "[load] file=%s phase=%s elapsed_ms=%.1f%s",
            self.file_path, phase, elapsed_ms, suffix
        )

    def error(self, phase, exc):
        self.log(
            phase,
            f"error={type(exc).__name__}: {exc} "
            f"traceback={traceback.format_exc().splitlines()[-1] if traceback.format_exc() else 'n/a'}"
        )

# Numbered SGD components that must never be merged into a loaded model.
# Keys are FF3 character pack prefixes under character\model, values are the
# component numbers to drop. These packs keep a "shado" helper body (the
# ch000 shadow, shared by the first character costumes) that duplicates the real
# bone names, so it z-fights and hides the character in the viewer. ch006 is the
# odd one out: it stops at 0011 and stores the shadow there instead. Some later
# packs also keep a non-rendering final component (including collision data).
SKIPPED_SGD_COMPONENTS = {
    "ch000": frozenset({15}),
    "ch001": frozenset({15}),
    "ch002": frozenset({15}),
    "ch003": frozenset({15}),
    "ch004": frozenset({15}),
    "ch005": frozenset({15}),
    "ch006": frozenset({11}),
    "ch007": frozenset({15}),
    "ch066": frozenset({14}),
    "ch200": frozenset({15}),
    "ch201": frozenset({15}),
    "ch202": frozenset({13}),
    "ch203": frozenset({16}),
    "ch210": frozenset({15}),
    "ch211": frozenset({15}),
    "ch212": frozenset({14}),
    "ch213": frozenset({16}),
    "ch220": frozenset({18}),
    "ch221": frozenset({18}),
}

FF3_CHARACTER_DISPLAY_NAMES = {
    "ch000": "Rei",
    "ch001": "Miku",
    "ch002": "Kei",
    "ch003": "Rei",
    "ch004": "Miku",
    "ch005": "Kei",
    "ch006": "Rei",
    "ch007": "Miku",
    "ch008": "Yuu",
    "ch009": "Mio",
    "ch010": "Yuu",
    "ch011": "Mafuyu",
    "ch012": "Yoshino",
    "ch018": "Miku",
    "ch021": "Yoshino",
    "ch050": "Mayu",
    "ch032": "Reika",
    "ch034": "Yashuu",
    "ch052": "Reika",
    "ch056": "Yoshino",
    "ch058": "Reika",
    "ch060": "Reika",
    "ch066": "Miku",
    "ch067": "Miku",
    "ch200": "Rei",
    "ch201": "Rei",
    "ch202": "Rei",
    "ch203": "Rei",
    "ch210": "Miku",
    "ch211": "Miku",
    "ch212": "Miku",
    "ch213": "Miku",
    "ch220": "Kei",
    "ch221": "Kei",
}


def character_pack_prefix(path):
    """Return the FF3 character pack prefix (e.g. ch000) for a model path.

    Character packs live in character\\model\\<prefix>_pk4, so the pack folder
    is the entry that follows "model" in the path.
    """
    parts = os.path.normcase(os.path.abspath(path)).split(os.sep)
    for index, part in enumerate(parts[:-1]):
        if part == "model":
            match = re.match(r"([a-z]+\d+)", os.path.splitext(parts[index + 1])[0])
            if match:
                return match.group(1)
    return None


def ff3_character_display_name(entry):
    """Add a verified character name to an FF3 pack's filetree label."""
    match = re.match(r"(ch\d{3})_pk4$", entry, re.IGNORECASE)
    character = match.group(1).lower() if match else None
    display_name = FF3_CHARACTER_DISPLAY_NAMES.get(character)
    return f"{display_name} ({entry})" if display_name else entry


def skipped_sgd_components(path):
    """Return the numbered SGD components excluded for a model path."""
    return SKIPPED_SGD_COMPONENTS.get(character_pack_prefix(path), frozenset())


def is_hidden_ff3_model_variant(target_dir, entry, game):
    """Hide auxiliary *_00/_01/_02_pk4 packs in FF3's character model folder."""
    normalized_dir = os.path.normpath(target_dir)
    return (
        game == 'ff3'
        and os.path.basename(normalized_dir).casefold() == 'model'
        and os.path.basename(os.path.dirname(normalized_dir)).casefold() == 'character'
        and entry.casefold().endswith(('_00_pk4', '_01_pk4', '_02_pk4'))
    )


def is_hidden_ff3_character_entry(target_dir, entry, game):
    """Hide non-model folders from FF3's character filetree."""
    normalized_dir = os.path.normpath(target_dir)
    return (
        game == 'ff3'
        and os.path.basename(normalized_dir).casefold() == 'character'
        and entry.casefold() in {'shape', 'char_shadow_pk4', 'motion'}
    )


def is_hidden_ff3_root_entry(target_dir, entry, saved_root, game):
    """Hide the camera folder from FF3's configured root filetree."""
    if game != 'ff3' or not saved_root:
        return False
    normalized_dir = os.path.normcase(os.path.abspath(target_dir))
    normalized_root = os.path.normcase(os.path.abspath(saved_root))
    if normalized_dir == normalized_root and entry.casefold() == 'camera':
        return True
    hidden_children = {
        'room': {'data'},
        'door': {'motion'},
        'furniture': {'motion'},
    }
    return (
        os.path.normcase(os.path.dirname(os.path.abspath(target_dir))) == normalized_root
        and entry.casefold() in hidden_children.get(
            os.path.basename(os.path.normpath(target_dir)).casefold(), set()
        )
    )


def is_hidden_ff2_camera_folder(target_dir, entry, saved_root, game):
    """Hide the camera folder directly under FF2's configured root."""
    if game not in {'ff2', 'ff2w'} or not saved_root or entry.casefold() != 'camera':
        return False
    return os.path.normcase(os.path.abspath(target_dir)) == os.path.normcase(
        os.path.abspath(saved_root)
    )


def is_hidden_ff2w_room_auxiliary_folder(target_dir, entry, saved_root, game):
    """Hide auxiliary MH/PZB/ZLD folders under FF2 Wii's room category."""
    if game != 'ff2w' or not saved_root or entry.casefold() not in {'mh', 'pzb', 'zld'}:
        return False
    room_dir = os.path.join(saved_root, 'room')
    return os.path.normcase(os.path.abspath(target_dir)) == os.path.normcase(
        os.path.abspath(room_dir)
    )


def is_hidden_ff2_empty_furniture_pk2(target_dir, entry, saved_root, game):
    """Hide the known empty FF2 furniture PK2 archives from the filetree."""
    if game != 'ff2' or not saved_root or entry.casefold() not in {'f110.pk2', 'f111.pk2'}:
        return False
    furniture_dir = os.path.join(saved_root, 'furniture')
    return os.path.normcase(os.path.abspath(target_dir)) == os.path.normcase(
        os.path.abspath(furniture_dir)
    )


def is_hidden_ff1_animation_folder(target_dir, entry, saved_root, game):
    """Hide FF1's animation folder directly under the configured man directory."""
    if game != 'ff1' or not saved_root or entry.casefold() != 'anm':
        return False
    man_dir = os.path.join(saved_root, 'man')
    return os.path.normcase(os.path.abspath(target_dir)) == os.path.normcase(
        os.path.abspath(man_dir)
    )


def is_hidden_ff1x_furnmime_folder(target_dir, entry, saved_root, game):
    """Hide FF1 Xbox's auxiliary furniture MIME folder at the configured root."""
    if game != 'ff1x' or not saved_root or entry.casefold() != 'furnmime':
        return False
    return os.path.normcase(os.path.abspath(target_dir)) == os.path.normcase(
        os.path.abspath(saved_root)
    )


def resolve_ff1_man_directory(target_dir, saved_root, game):
    """Skip FF1's man wrapper and browse its model directory directly."""
    if game != 'ff1' or not saved_root:
        return target_dir
    man_dir = os.path.join(saved_root, 'man')
    if (
        os.path.normcase(os.path.abspath(target_dir))
        == os.path.normcase(os.path.abspath(man_dir))
        and os.path.isdir(os.path.join(man_dir, 'mdl'))
    ):
        return os.path.join(man_dir, 'mdl')
    return target_dir


def resolve_ff1_parent_directory(target_dir, saved_root, parent_dir, game):
    """Return from FF1's man/mdl directory to the configured game root."""
    if game != 'ff1' or not saved_root:
        return parent_dir
    mdl_dir = os.path.join(saved_root, 'man', 'mdl')
    if os.path.normcase(os.path.abspath(target_dir)) == os.path.normcase(
        os.path.abspath(mdl_dir)
    ):
        return os.path.abspath(saved_root)
    return parent_dir


def resolve_ff1x_man_directory(target_dir, saved_root, game):
    """Open FF1 Xbox's man category directly in its model directory."""
    if game != 'ff1x' or not saved_root:
        return target_dir
    man_dir = os.path.join(saved_root, 'man')
    mdl_dir = os.path.join(man_dir, 'mdl')
    if (
        os.path.normcase(os.path.abspath(target_dir))
        == os.path.normcase(os.path.abspath(man_dir))
        and os.path.isdir(mdl_dir)
    ):
        return mdl_dir
    return target_dir


def resolve_ff1x_parent_directory(target_dir, saved_root, parent_dir, game):
    """Return from FF1 Xbox's man/mdl directory to its configured game root."""
    if game != 'ff1x' or not saved_root:
        return parent_dir
    mdl_dir = os.path.join(saved_root, 'man', 'mdl')
    if os.path.normcase(os.path.abspath(target_dir)) == os.path.normcase(
        os.path.abspath(mdl_dir)
    ):
        return os.path.abspath(saved_root)
    return parent_dir


def resolve_ff2_room_directory(target_dir, saved_root, game):
    """Open FF2's room category directly in its PK2 asset folder."""
    if game != 'ff2' or not saved_root:
        return target_dir
    room_dir = os.path.join(saved_root, 'room')
    pk2_dir = os.path.join(room_dir, 'pk2')
    if (
        os.path.normcase(os.path.abspath(target_dir))
        == os.path.normcase(os.path.abspath(room_dir))
        and os.path.isdir(pk2_dir)
    ):
        return pk2_dir
    return target_dir


def resolve_ff2_man_directory(target_dir, saved_root, game):
    """Open FF2's man category directly in its model directory."""
    if game != 'ff2' or not saved_root:
        return target_dir
    man_dir = os.path.join(saved_root, 'man')
    mdl_dir = os.path.join(man_dir, 'mdl')
    if (
        os.path.normcase(os.path.abspath(target_dir))
        == os.path.normcase(os.path.abspath(man_dir))
        and os.path.isdir(mdl_dir)
    ):
        return mdl_dir
    return target_dir


def resolve_ff2_parent_directory(target_dir, saved_root, parent_dir, game):
    """Return from FF2's room PK2 or man model directory to the game root."""
    if game != 'ff2' or not saved_root:
        return parent_dir
    shortcut_dirs = (
        os.path.join(saved_root, 'room', 'pk2'),
        os.path.join(saved_root, 'man', 'mdl'),
    )
    normalized_target = os.path.normcase(os.path.abspath(target_dir))
    if any(
        normalized_target == os.path.normcase(os.path.abspath(directory))
        for directory in shortcut_dirs
    ):
        return os.path.abspath(saved_root)
    return parent_dir


def is_hidden_ff3_furniture_archive(target_dir, entry, saved_root, game):
    """Hide standalone furniture PK4 files alongside the browsable pack folders."""
    if game != 'ff3' or not saved_root or not entry.casefold().endswith('.pk4'):
        return False
    furniture_model_dir = os.path.join(saved_root, 'furniture', 'model')
    return os.path.normcase(os.path.abspath(target_dir)) == os.path.normcase(
        os.path.abspath(furniture_model_dir)
    )


def is_hidden_ff3_character_pack_entry(target_dir, entry, game):
    """Hide auxiliary resource folders inside FF3 model packs."""
    normalized_dir = os.path.normpath(target_dir)
    pack_name = os.path.basename(normalized_dir).casefold()
    category = os.path.basename(os.path.dirname(os.path.dirname(normalized_dir))).casefold()
    resource_folders = {
        'character': {'01_tpk', '02_cld', '03_mono', '04_flgs'},
        'accessory': {'01_tpk', '02_mono', '03_flgs'},
        'door': {'01_tpk', '02_mono', '03_flgs'},
        'fly': {'01_tpk', '02_cls', '03_mono', '04_flgs'},
        'furniture': {'01_tpk', '02_mono', '03_flgs'},
        'object': {'01_tpk', '02_mono', '03_flgs'},
        'room': {'01_tpk', '02_cld', '02_mono', '03_mono', '03_flgs', '04_flgs'},
    }
    pack_pattern = r'r[a-z]+\d+[a-z]*_pk4' if category == 'room' else (
        r'(?:ch|a|d|f|o|fly)\d+(?:_pk4)?'
    )
    return (
        game == 'ff3'
        and os.path.basename(os.path.dirname(normalized_dir)).casefold() == 'model'
        and category in resource_folders
        and re.fullmatch(pack_pattern, pack_name) is not None
        and entry.casefold() in resource_folders[category]
    )


def resolve_ff3_category_directory(target_dir, saved_root, game):
    """Skip an FF3 model category wrapper when opening it from the game root."""
    model_categories = {
        'character', 'accessory', 'door', 'fly', 'furniture', 'object', 'room'
    }
    normalized_dir = os.path.abspath(target_dir)
    normalized_root = os.path.abspath(saved_root) if saved_root else ""
    if (game == 'ff3' and normalized_root
            and os.path.basename(normalized_dir).casefold() in model_categories
            and os.path.normcase(os.path.dirname(normalized_dir))
            == os.path.normcase(normalized_root)):
        model_dir = os.path.join(normalized_dir, 'model')
        if os.path.isdir(model_dir):
            return model_dir
    return target_dir


def ff3_model_pack_sgd_directory(pack_dir):
    """Return an FF3 model pack's nested SGD directory when it exists."""
    pack_name = os.path.basename(os.path.normpath(pack_dir))
    pack_category = os.path.basename(
        os.path.dirname(os.path.dirname(os.path.normpath(pack_dir)))
    ).casefold()
    pack_pattern = (
        r'r[a-z]+\d+[a-z]*_pk4'
        if pack_category == 'room'
        else r'(?:ch|a|d|f|o|fly)\d+_pk4'
    )
    if re.fullmatch(pack_pattern, pack_name, re.IGNORECASE) is None:
        return None
    sgd_dir = os.path.join(pack_dir, '00_mpk', 'mpk_pk4', '00_sgd')
    return sgd_dir if os.path.isdir(sgd_dir) else None


def resolve_ff3_model_pack_directory(target_dir, saved_root, game):
    """Open FF3 model packs directly at their nested SGD directory."""
    if not saved_root or game != 'ff3':
        return target_dir
    normalized_dir = os.path.abspath(target_dir)
    for category in ('character', 'accessory', 'door', 'fly', 'furniture', 'object', 'room'):
        model_dir = os.path.join(os.path.abspath(saved_root), category, 'model')
        if os.path.normcase(os.path.dirname(normalized_dir)) == os.path.normcase(model_dir):
            return ff3_model_pack_sgd_directory(normalized_dir) or target_dir
    return target_dir


def resolve_ff3_parent_directory(target_dir, saved_root, parent_dir, game):
    """Return from FF3 model packs to model, and from model to game root."""
    if not saved_root or game != 'ff3':
        return parent_dir
    normalized_dir = os.path.normcase(os.path.abspath(target_dir))
    normalized_root = os.path.normcase(os.path.abspath(saved_root))
    categories = ('character', 'accessory', 'door', 'fly', 'furniture', 'object', 'room')
    model_dirs = [
        os.path.normcase(os.path.abspath(os.path.join(saved_root, category, 'model')))
        for category in categories
    ]
    if normalized_dir in model_dirs:
        return normalized_root
    for category in categories:
        model_dir = os.path.abspath(os.path.join(saved_root, category, 'model'))
        try:
            relative = os.path.relpath(os.path.abspath(target_dir), model_dir)
        except ValueError:
            continue
        parts = relative.split(os.sep)
        if (
            len(parts) == 4
            and re.fullmatch(
                r'(?:ch|a|d|f|o|fly)\d+_pk4|r[a-z]+\d+[a-z]*_pk4',
                parts[0], re.IGNORECASE
            )
            and [part.casefold() for part in parts[1:]]
            == ['00_mpk', 'mpk_pk4', '00_sgd']
        ):
            sgd_dir = ff3_model_pack_sgd_directory(os.path.join(model_dir, parts[0]))
            if sgd_dir and normalized_dir == os.path.normcase(os.path.abspath(sgd_dir)):
                return model_dir
    return parent_dir


def numbered_sgd_files(sgd_dir, excluded_indexes=(), exclude_path=None):
    """List numbered *.sgd names in sgd_dir, skipping excluded components."""
    excluded = set(excluded_indexes)
    if exclude_path:
        exclude_path = os.path.normcase(os.path.abspath(exclude_path))
    return sorted(
        name for name in os.listdir(sgd_dir)
        if name.lower().endswith('.sgd')
        and os.path.splitext(name)[0].isdigit()
        and int(os.path.splitext(name)[0]) not in excluded
        and (not exclude_path
             or os.path.normcase(os.path.join(sgd_dir, name)) != exclude_path)
    )


def ff3_model_pack_load_path(pack_dir):
    """Find the first renderable SGD used to auto-load an FF3 model pack."""
    sgd_dir = ff3_model_pack_sgd_directory(pack_dir)
    if not sgd_dir:
        return None
    excluded = set(skipped_sgd_components(pack_dir))
    if os.path.basename(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.normpath(pack_dir))))
    ).casefold() != 'room':
        excluded.add(15)
        if not any(os.path.splitext(name)[0] == "15" for name in os.listdir(sgd_dir)):
            excluded.add(14)
    candidates = numbered_sgd_files(sgd_dir, excluded)
    return os.path.join(sgd_dir, candidates[0]) if candidates else None


def serialize_textures(textures):
    """Encode PIL surfaces as the data-URI list the frontend expects."""
    tex_list = []
    for img in textures or []:
        buf = BytesIO()
        img.save(buf, format='PNG')
        w, h = img.size
        alpha_min = alpha_max = 255
        alpha_has_partial = False
        if "A" in img.getbands():
            alpha = img.getchannel("A")
            alpha_min, alpha_max = alpha.getextrema()
            if alpha_min < 255:
                histogram = alpha.histogram()
                alpha_has_partial = any(histogram[1:255])
        tex_list.append({
            "data_uri": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode('ascii'),
            "width": w,
            "height": h,
            "has_alpha": alpha_min < 255,
            "alpha_min": alpha_min,
            "alpha_max": alpha_max,
            "alpha_has_partial": alpha_has_partial,
        })
    return tex_list


def serialize_model(model, textures=None, collision_meshes=None, model_type="model"):
    tex_list = serialize_textures(textures)
    materials_data = []
    for mat in getattr(model, 'materials', []):
        materials_data.append({
            "index": mat.index,
            "name": mat.name,
            "ambient": mat.ambient,
            "diffuse": mat.diffuse,
            "specular": mat.specular,
            "texture_index": mat.texture_index
        })

    meshes_data = []
    total_verts = 0
    total_tris = 0
    position_values = []
    texture_dimensions = {
        index: list(img.size)
        for index, img in enumerate(textures or [])
        if img is not None and hasattr(img, "size")
    }
    foliage_diagnostics = []

    for m in model.meshes:
        total_verts += len(m.positions)
        total_tris += len(m.indices)
        position_values.extend(
            value for point in m.positions for value in point
            if isinstance(value, (int, float)) and math.isfinite(value)
        )
        tex_id = getattr(m, "texture_index_override", -1)
        if tex_id < 0 and 0 <= m.material_index < len(materials_data):
            tex_id = materials_data[m.material_index]["texture_index"]
        if m.name in {"mesh_b111_t0x12", "mesh_b112_t0x12"}:
            mat = (
                model.materials[m.material_index]
                if 0 <= m.material_index < len(model.materials)
                else None
            )
            uv_values_for_mesh = [
                value
                for uv in getattr(m, "uvs", [])
                for value in uv
                if isinstance(value, (int, float)) and math.isfinite(value)
            ]
            foliage_diagnostics.append({
                "mesh": m.name,
                "material_index": m.material_index,
                "material_name": getattr(mat, "name", None),
                "tbp0": getattr(mat, "tbp0", None),
                "tex0_low": getattr(mat, "tex0_low", None),
                "texture_index": tex_id,
                "texture_dimensions": texture_dimensions.get(tex_id),
                "vertex_count": len(m.positions),
                "triangle_count": len(m.indices),
                "uv_min": min(uv_values_for_mesh) if uv_values_for_mesh else None,
                "uv_max": max(uv_values_for_mesh) if uv_values_for_mesh else None,
                "uv_samples": [list(uv) for uv in getattr(m, "uvs", [])[:6]],
                "named_uv_flip_applied": bool(
                    getattr(m, "_named_uv_flip_applied", False)
                ),
            })

        meshes_data.append({
            "name": m.name,
            "material_index": m.material_index,
            "tex_id": tex_id,
            "bone_index": m.bone_index,
            "positions": [coord for p in m.positions for coord in p],
            "normals": [coord for n in m.normals for coord in n],
            "uvs": [coord for uv in m.uvs for coord in uv],
            "colors": [c for col in m.colors for c in (col[:3] if len(col) >= 3 else [1.0, 1.0, 1.0])],
            "indices": [idx for tri in m.indices for idx in tri]
        })

    uv_values = [
        uv for mesh in getattr(model, 'meshes', [])
        for uv_pair in getattr(mesh, 'uvs', [])
        for uv in uv_pair
        if isinstance(uv, (int, float))
    ]
    texture_mapped = sum(
        1 for mat in getattr(model, 'materials', [])
        if getattr(mat, 'texture_index', -1) >= 0
    )

    bones_data = []
    for b in getattr(model, 'bones', []):
        pos = [b.matrix[12], b.matrix[13], b.matrix[14]] if len(b.matrix) >= 15 and any(b.matrix[12:15]) else b.trans
        bones_data.append({
            "id": b.index,
            "index": b.index,
            "parent": b.parent,
            "matrix": b.matrix,
            "pos": pos,
            "rot": b.rot[:3] if len(b.rot) >= 3 else [0.0, 0.0, 0.0]
        })

    all_col = (collision_meshes or []) + getattr(model, 'collision_meshes', [])
    col_polygons = []
    col_spheres = []
    col_boxes = []

    for c in all_col:
        c_type = getattr(c, 'type', 'poly')
        if c_type == 'sphere':
            # Extract sphere center
            cx = sum(p[0] for p in c.positions) / max(1, len(c.positions))
            cy = sum(p[1] for p in c.positions) / max(1, len(c.positions))
            cz = sum(p[2] for p in c.positions) / max(1, len(c.positions))
            radius = 25.0
            if c.positions:
                p0 = c.positions[0]
                radius = ((p0[0]-cx)**2 + (p0[1]-cy)**2 + (p0[2]-cz)**2)**0.5
            col_spheres.append({"center": [cx, cy, cz], "radius": radius})
        elif c_type in ('bbox', 'box'):
            min_p = [min(p[i] for p in c.positions) for i in range(3)] if c.positions else [-10,-10,-10]
            max_p = [max(p[i] for p in c.positions) for i in range(3)] if c.positions else [10,10,10]
            col_boxes.append({"min": min_p, "max": max_p})
        else:
            for tri in c.indices:
                if len(tri) >= 3 and tri[0] < len(c.positions) and tri[1] < len(c.positions) and tri[2] < len(c.positions):
                    col_polygons.append([c.positions[tri[0]], c.positions[tri[1]], c.positions[tri[2]]])

    collision_struct = {
        "polygons": col_polygons,
        "spheres": col_spheres,
        "boxes": col_boxes
    }

    diagnostics = {
        "materials_mapped": texture_mapped,
        "materials_total": len(getattr(model, 'materials', [])),
        "uv_min": min(uv_values) if uv_values else 0.0,
        "uv_max": max(uv_values) if uv_values else 0.0,
        "position_min": min(position_values) if position_values else 0.0,
        "position_max": max(position_values) if position_values else 0.0,
        "position_values": len(position_values)
    }
    diagnostics["texture_alpha"] = []
    for texture_index, image in enumerate(textures or []):
        alpha_min = alpha_max = 255
        if "A" in image.getbands():
            alpha_min, alpha_max = image.getchannel("A").getextrema()
        linked = []
        for mat in getattr(model, "materials", []):
            if getattr(mat, "texture_index", -1) == texture_index:
                tex0 = getattr(mat, "tex0", 0) or getattr(mat, "tex0_low", 0)
                linked.append({
                    "material": getattr(mat, "name", ""),
                    "tbp0": tex0 & 0x3FFF if tex0 else None,
                    "psm": (tex0 >> 20) & 0x3F if tex0 else None,
                    "cbp": (tex0 >> 37) & 0x3FFF if tex0 else None,
                    "csm": (tex0 >> 55) & 1 if tex0 else None,
                    "csa": (tex0 >> 56) & 0x1F if tex0 else None,
                })
        diagnostics["texture_alpha"].append({
            "texture_index": texture_index,
            "width": image.width,
            "height": image.height,
            "alpha_min": alpha_min,
            "alpha_max": alpha_max,
            "has_alpha": alpha_min < 255,
            "materials": linked,
        })
    if bones_data:
        bone_ids = {bone["id"] for bone in bones_data}
        diagnostics["skeleton"] = {
            "bone_count": len(bones_data),
            "roots": [bone["id"] for bone in bones_data if bone["parent"] < 0],
            "invalid_parents": [
                {"id": bone["id"], "parent": bone["parent"]}
                for bone in bones_data
                if bone["parent"] >= 0 and bone["parent"] not in bone_ids
            ],
            "self_parents": [
                bone["id"] for bone in bones_data if bone["parent"] == bone["id"]
            ],
            "non_degenerate_matrices": sum(
                1 for bone in bones_data
                if len(bone["matrix"]) >= 16 and
                all(math.isfinite(value) for value in bone["matrix"])
            ),
            "weighted_meshes": sum(
                1 for mesh in meshes_data
                if 0 <= mesh["bone_index"] < len(bones_data)
            ),
        }
    if hasattr(model, "texture_debug"):
        diagnostics["texture_debug"] = model.texture_debug
    if hasattr(model, "parse_diagnostics"):
        diagnostics["parser"] = model.parse_diagnostics
    diagnostics["foliage_meshes"] = foliage_diagnostics
    diagnostics["foliage_uv_corrections"] = getattr(
        model, "foliage_uv_corrections", []
    )

    return {
        "filename": getattr(model, 'export_name', getattr(model, 'name', 'model')),
        "name": getattr(model, 'name', 'model'),
        "type": model_type,
        "model_type": model_type,
        "meshes": meshes_data,
        "materials": materials_data,
        "bones": bones_data,
        "collision": collision_struct,
        "textures": tex_list,
        "uvs_flipped": bool(getattr(model, "uvs_are_flipped", False)),
        # FF2 Wii atlases are small (16..512 px) and are drawn magnified across
        # whole surfaces, so they need linear filtering or nearest sampling
        # breaks them into hard texel blocks. The TIM2 based games keep nearest
        # magnification because their pixel art is meant to stay crisp.
        "texture_mag_linear": bool(getattr(model, "texture_mag_linear", False)),
        "stats": {
            "vertices": total_verts,
            "triangles": total_tris,
            "submeshes": len(meshes_data),
            "bones": len(bones_data),
            "textures": len(tex_list)
        },
        "diagnostics": diagnostics
    }


def apply_named_foliage_uv_corrections(model):
    """Apply the narrow PS2 foliage correction after every parse/merge path."""
    target_names = frozenset({"mesh_b111_t0x12", "mesh_b112_t0x12"})
    corrected = []
    for mesh in getattr(model, "meshes", []):
        mesh_name = str(getattr(mesh, "name", "")).lower()
        is_foliage = (
            mesh_name in target_names
            or re.fullmatch(r"mesh_b(?:111|112)_t0x12", mesh_name) is not None
        )
        if is_foliage and not getattr(mesh, "_named_uv_flip_applied", False):
            mesh.uvs = [[u, 1.0 - v] for u, v in mesh.uvs]
            mesh._named_uv_flip_applied = True
            corrected.append(mesh.name)
    model.foliage_uv_corrections = corrected
    return model

def find_textures_for_model(
    file_path,
    model,
    progress=None,
    decode_texture=decode_tim2,
    iter_embedded=iter_embedded_tim2,
):
    if progress:
        progress.log("material_texture_mapping", "status=start")
    base_name = os.path.splitext(os.path.basename(file_path))[0]
    dir_path = os.path.dirname(os.path.abspath(file_path))

    candidate_dirs = [
        os.path.join(dir_path, base_name),
        dir_path,
        os.path.join(os.path.dirname(dir_path), "room", base_name),
    ]
    # Linked PK2 room extraction keeps SGD files in a directory named
    # <room>_pk2_linked; texture payloads, when extracted, live beside it or
    # in the corresponding original room directory.
    if "_pk2_linked" in base_name.lower():
        room_stem = base_name[:base_name.lower().index("_pk2_linked")]
        candidate_dirs.extend((
            os.path.join(dir_path, room_stem),
            os.path.join(dir_path, room_stem + "_pk2"),
        ))

    # Fatal Frame 3: mpk -> tpk discovery (model packages map to sibling texture packages)
    curr = os.path.abspath(file_path)
    for _ in range(5):
        parent = os.path.dirname(curr)
        if parent == curr:
            break
        curr = parent
        if os.path.isdir(curr):
            for item in os.listdir(curr):
                if 'tpk' in item.lower():
                    tpk_full = os.path.join(curr, item)
                    for root, subdirs, files in os.walk(tpk_full):
                        if any(f.lower().endswith(('.tm2', '.tim2', '.png')) for f in files) or 'tm2' in os.path.basename(root).lower():
                            if root not in candidate_dirs:
                                candidate_dirs.append(root)

    found_images_by_tbp0 = {}
    texture_variants_by_tbp0 = {}
    found_images_by_name = {}
    found_images_by_resource_index = {}
    base_timgs_by_tbp0 = {}
    tbp0_resource_index = {}
    clut_only_entries = []
    archive_paths = []
    ff1_texture_debug = None
    if os.path.splitext(file_path)[1].lower() == '.pk4':
        archive_dir = os.path.dirname(os.path.abspath(file_path))
        archive_paths.append(file_path)
        archive_paths.extend(
            os.path.join(archive_dir, fname)
            for fname in os.listdir(archive_dir)
            if fname.lower().endswith('.pk4') and 'tpk' in fname.lower()
        )
    elif os.path.splitext(file_path)[1].lower() == '.pk2':
        archive_paths.append(file_path)

    texture_files = set()
    for cdir in candidate_dirs:
        if os.path.exists(cdir) and os.path.isdir(cdir):
            for root, _, files in os.walk(cdir):
                for fname in sorted(files):
                    fpath = os.path.join(root, fname)
                    if fpath in texture_files:
                        continue
                    texture_files.add(fpath)
                    if not os.path.isfile(fpath):
                        continue
                    ext = os.path.splitext(fname)[1].lower()
                    stem = os.path.splitext(fname)[0].lower()
                    if ext in ('.png', '.jpg', '.jpeg', '.bmp', '.tga'):
                        try:
                            img = Image.open(fpath)
                            found_images_by_name[stem] = img
                            # TPK extraction tools emit tex/<index>.png beside
                            # the matching 00_tm2/<index>.tm2 resource.
                            if os.path.basename(root).lower() == "tex" and stem.isdigit():
                                found_images_by_resource_index[int(stem)] = img
                        except Exception:
                            pass
                    elif ext in ('.tm2', '.tim2'):
                        try:
                            with open(fpath, 'rb') as tf:
                                timgs = decode_texture(tf.read())
                                if timgs:
                                    for timg in timgs:
                                        tex0 = timg.get('gs_tex0', 0)
                                        tbp0 = tex0 & 0x3FFF
                                        if timg.get('is_clut_only'):
                                            clut_only_entries.append((stem, tbp0, timg))
                                        else:
                                            img = timg.get('image')
                                            if img:
                                                if tbp0 >= 0:
                                                    found_images_by_tbp0[tbp0] = img
                                                    base_timgs_by_tbp0[tbp0] = timg
                                                    if stem.isdigit():
                                                        tbp0_resource_index.setdefault(tbp0, int(stem))
                                                    texture_variants_by_tbp0.setdefault(tbp0, []).append(img)
                                                found_images_by_name[stem] = img
                        except Exception:
                            pass

    for archive_path in archive_paths:
        try:
            if archive_path.lower().endswith(".pk4"):
                entries = (
                    entry for entry in iter_pk4_entries(archive_path)
                    if entry["type"] in ("tm2", "tim2")
                )
            else:
                entries = None
            if archive_path.lower().endswith(".pk2"):
                timgs_with_entries = (
                    (timg, timg.get("_pk2_entry_index", -1))
                    for timg in iter_embedded(archive_path)
                )
                for timg, entry_index in timgs_with_entries:
                    if timg.get("is_clut_only"):
                        clut_only_entries.append((
                            f"{os.path.splitext(os.path.basename(archive_path))[0]}_{entry_index}",
                            timg.get("gs_tex0", 0) & 0x3FFF,
                            timg,
                        ))
                        continue
                    img = timg.get("image")
                    if not img:
                        continue
                    tbp0 = timg.get("gs_tex0", 0) & 0x3FFF
                    stem = f"{os.path.splitext(os.path.basename(archive_path))[0]}_{entry_index}"
                    if tbp0 >= 0:
                        found_images_by_tbp0[tbp0] = img
                        base_timgs_by_tbp0[tbp0] = timg
                        if str(entry_index).isdigit():
                            tbp0_resource_index.setdefault(tbp0, int(entry_index))
                        texture_variants_by_tbp0.setdefault(tbp0, []).append(img)
                    found_images_by_name[stem.lower()] = img
            else:
                for entry in entries:
                    for timg in decode_tim2(entry["data"]):
                        img = timg.get("image")
                        if not img:
                            continue
                        tbp0 = timg.get("gs_tex0", 0) & 0x3FFF
                        stem = f"{os.path.splitext(os.path.basename(archive_path))[0]}_{entry['index']}"
                        if tbp0 >= 0:
                            found_images_by_tbp0[tbp0] = img
                            base_timgs_by_tbp0[tbp0] = timg
                            if str(entry["index"]).isdigit():
                                tbp0_resource_index.setdefault(tbp0, int(entry["index"]))
                            texture_variants_by_tbp0.setdefault(tbp0, []).append(img)
                        found_images_by_name[stem.lower()] = img
        except (OSError, ValueError, struct.error):
            raise

    # FF1 item SGDs carry their own headerless GS uploads. Match only the
    # material's complete TEX0 metadata to the reconstructed TBP0 image.
    if (
        getattr(model, "sgd_format", "") in ("ff1", "ff2")
        and os.path.splitext(file_path)[1].lower() == ".sgd"
    ):
        with open(file_path, "rb") as sgd_file:
            sgd_data = sgd_file.read()
        ff1_texture_debug = {}
        embedded_images, embedded_uploads = reconstruct_sgd_textures(
            sgd_data,
            getattr(model, "materials", []),
            diagnostics=ff1_texture_debug,
        )
        for tbp0, image in embedded_images.items():
            found_images_by_tbp0[tbp0] = image
            base_timgs_by_tbp0[tbp0] = {"image": image}
            texture_variants_by_tbp0.setdefault(tbp0, []).append(image)
        ff1_texture_debug["source"] = (
            "embedded_sgd_gs_ff2"
            if getattr(model, "sgd_format", "") == "ff2"
            else "embedded_sgd_gs"
        )
        ff1_texture_debug["upload_count"] = len(embedded_uploads)

    # Render palette variations from CLUT-only files
    for stem, tbp0, clut_timg in clut_only_entries:
        base_timg = base_timgs_by_tbp0.get(tbp0)
        if base_timg:
            var_img = render_tim2_clut_variation(base_timg, clut_timg)
            if var_img:
                found_images_by_name[stem] = var_img
                texture_variants_by_tbp0.setdefault(tbp0, []).append(var_img)

    if progress:
        progress.log(
            "material_texture_mapping",
            f"files={len(texture_files)} tbp0_candidates={len(found_images_by_tbp0)} "
            f"name_candidates={len(found_images_by_name)}"
        )
    if not found_images_by_tbp0 and not found_images_by_name:
        if ff1_texture_debug is not None:
            model.texture_debug = {
                "ff1_embedded_gs": ff1_texture_debug,
                "unmapped_materials": [
                    {
                        "index": mat.index,
                        "name": mat.name,
                        "tbp0": getattr(mat, "tbp0", 0),
                        "tex0_low": getattr(mat, "tex0_low", 0),
                    }
                    for mat in getattr(model, "materials", [])
                    if getattr(mat, "tex0_low", 0)
                ],
            }
        if progress:
            progress.log("material_texture_mapping", "mapped=0 textures=0")
        return []

    for mat in getattr(model, 'materials', []):
        mat.texture_index = -1

    textures = []
    def has_meaningful_alpha(img):
        if not img or "A" not in img.getbands():
            return False
        alpha_min, alpha_max = img.getchannel("A").getextrema()
        return alpha_min < 255 and alpha_max > 0

    # Match by hardware TBP0 first. This is exact and avoids assigning a
    # same-named texture from another resource to a mesh (notably rre02's
    # kaidan1 panel behind the denwa material).
    for mat in getattr(model, 'materials', []):
        mat_name_clean = mat.name.lower().replace('-', '_').replace(' ', '_').strip()
        mat_stem = os.path.splitext(mat_name_clean)[0]
        tbp0 = getattr(mat, 'tbp0', 0)
        if tbp0 >= 0 and tbp0 in found_images_by_tbp0:
            variants = texture_variants_by_tbp0.get(tbp0, [])
            use_variant = '_cl' in mat_stem and len(variants) > 1
            resource_img = found_images_by_resource_index.get(tbp0_resource_index.get(tbp0))
            decoded_img = found_images_by_tbp0[tbp0]
            # Extracted indexed PNGs can flatten a TIM2 alpha channel. Keep
            # the decoded resource when it is the only alpha-bearing version.
            if use_variant:
                img = variants[1]
            elif resource_img and has_meaningful_alpha(decoded_img) and not has_meaningful_alpha(resource_img):
                img = decoded_img
            else:
                img = resource_img or decoded_img
            if img not in textures:
                textures.append(img)
            mat.texture_index = textures.index(img)

    # Match remaining materials by name/stem.
    for mat in getattr(model, 'materials', []):
        if mat.texture_index >= 0:
            continue
        mat_name_clean = mat.name.lower().replace('-', '_').replace(' ', '_').strip()
        mat_stem = os.path.splitext(mat_name_clean)[0]
        for stem, img in found_images_by_name.items():
            stem_clean = stem.replace('-', '_').replace(' ', '_')
            if mat_stem and (mat_stem in stem_clean or stem_clean in mat_stem):
                if img not in textures:
                    textures.append(img)
                mat.texture_index = textures.index(img)
                break

    # 4. Fallback: single image
    if len(getattr(model, 'materials', [])) == 1 and len(found_images_by_name) == 1 and not textures:
        img = list(found_images_by_name.values())[0]
        textures.append(img)
        model.materials[0].texture_index = 0

    # mesh_b91_t0x32 is a small ita1-cl00 submesh that shares material 45
    # with other geometry. Its UVs target the original 0011.tm2 image, not
    # the CLUT variation selected for the shared material.
    mesh_overrides = []
    target_tbp0 = 11348
    target_img = found_images_by_resource_index.get(11)
    if target_img is None and tbp0_resource_index.get(target_tbp0) == 11:
        target_img = base_timgs_by_tbp0.get(target_tbp0, {}).get("image")
    if target_img is not None and tbp0_resource_index.get(target_tbp0) == 11:
        if target_img not in textures:
            textures.append(target_img)
        target_texture_index = textures.index(target_img)
        for mesh in getattr(model, "meshes", []):
            if (
                mesh.name == "mesh_b91_t0x32"
                and mesh.indices
                and 0 <= mesh.material_index < len(model.materials)
                and model.materials[mesh.material_index].tbp0 == target_tbp0
            ):
                mesh.texture_index_override = target_texture_index
                mesh_overrides.append({
                    "mesh": mesh.name,
                    "material_index": mesh.material_index,
                    "tbp0": target_tbp0,
                    "resource_index": 11,
                    "texture_index": target_texture_index,
                })

    if progress:
        progress.log(
            "material_texture_mapping",
            f"mapped={sum(1 for m in getattr(model, 'materials', []) if m.texture_index >= 0)} "
            f"materials={len(getattr(model, 'materials', []))} textures={len(textures)}"
        )
    # Keep the hardware-address mapping available in the serialized response.
    # This makes a missing TPK resource distinguishable from a viewer-side
    # assignment problem without changing the texture selection behavior.
    model.texture_debug = {
        "candidate_tbp0": sorted(found_images_by_tbp0),
        "candidate_names": sorted(found_images_by_name),
        "resource_tbp0": {
            str(tbp0): tbp0_resource_index[tbp0]
            for tbp0 in sorted(tbp0_resource_index)
        },
        "unmapped_materials": [
            {
                "index": mat.index,
                "name": mat.name,
                "tbp0": mat.tbp0,
                "tex0_low": mat.tex0_low,
            }
            for mat in getattr(model, 'materials', [])
            if mat.texture_index < 0
        ],
        "mesh_overrides": mesh_overrides,
    }
    if ff1_texture_debug is not None:
        model.texture_debug["ff1_embedded_gs"] = ff1_texture_debug
    return textures

def handle_load_file(file_path, game="", xpr_override=None):
    xpr_variants = []
    xpr_active = None
    progress = LoadProgress(file_path)
    progress.log("request_path_validation", f"path={os.path.abspath(file_path)}")
    if not os.path.exists(file_path):
        progress.log("request_path_validation", "status=missing")
        return {"error": f"File not found: {file_path}"}
    progress.log("request_path_validation", "status=ok")
    cache_key = os.path.normcase(os.path.abspath(file_path))
    try:
        cache_stamp = (os.path.getmtime(file_path), os.path.getsize(file_path))
    except OSError:
        cache_stamp = None
    cache_key = (cache_key, game)
    # An explicit sidecar is part of the identity of the result, not a view of
    # the same one: the same MPX with a different XPR is a different colourway.
    if xpr_override:
        cache_key = (cache_key, os.path.normcase(os.path.abspath(xpr_override)))
    # Xbox depends on a sidecar; do not reuse JSON-only cache/state from another asset.
    xbox_sidecar_asset = os.path.splitext(file_path)[1].lower() in ('.mpx', '.xpr')
    cached = None if xbox_sidecar_asset else LOAD_CACHE.get(cache_key)
    if cached and cached[0] == cache_stamp:
        cached_response = dict(cached[1])
        cached_response["diagnostics"] = dict(cached[1].get("diagnostics", {}))
        cached_response["diagnostics"]["cache"] = "hit"
        progress.log("response_completion", "cache=hit")
        return cached_response
    load_started = time.perf_counter()

    source_dir = file_path if os.path.isdir(file_path) else None
    if os.path.isdir(file_path):
        # Support loading model package directory (e.g. 00_sgd containing 0000.sgd)
        entries = sorted(os.listdir(file_path))
        sgd_files = [f for f in entries if f.lower().endswith('.sgd')]
        pk_files = [f for f in entries if f.lower().endswith(('.pk2', '.pk4'))]
        if sgd_files:
            file_path = os.path.join(file_path, sgd_files[0])
        elif pk_files:
            file_path = os.path.join(file_path, pk_files[0])
        else:
            nested_sgds = []
            for root, _, files in os.walk(file_path):
                nested_sgds.extend(
                    os.path.join(root, f)
                    for f in files
                    if f.lower().endswith(".sgd")
                )
            if nested_sgds:
                file_path = sorted(nested_sgds)[0]
        progress.log(
            "request_path_validation",
            f"directory_selected={os.path.basename(file_path)}"
        )

    ext = os.path.splitext(file_path)[1].lower()
    base_name = os.path.splitext(os.path.basename(file_path))[0]
    progress.log("file_type_detection", f"extension={ext or '<none>'} base={base_name}")

    model = None
    textures = []
    collision = []
    model_type = "model"
    sgd_parse_metrics = {
        "sgd_processed": 0,
        "sgd_omitted": 0,
        "sgd_format": "",
        "unpack_records": 0,
        "unpack_skipped_invalid": 0,
        "unpack_skipped_oversized": 0,
        "unpack_skipped_budget": 0,
        "unpack_vertices": 0,
    }

    use_ff2_parser = game.lower() == "ff2"
    unpack_pk2_parser = unpack_pk2_ff2 if use_ff2_parser else unpack_pk2
    decode_texture = decode_tim2_ff2 if use_ff2_parser else decode_tim2
    iter_embedded = iter_embedded_tim2_ff2 if use_ff2_parser else iter_embedded_tim2
    reconstruct_textures = (
        reconstruct_sgd_textures_ff2 if use_ff2_parser
        else reconstruct_sgd_textures
    )

    if ext == '.pk2':
        entries = unpack_pk2_parser(file_path)
        progress.log("pk2_extraction", f"entries={len(entries)}")
        if not entries:
            with open(file_path, "rb") as pk2_file:
                header = pk2_file.read(4)
            if len(header) == 4 and struct.unpack("<I", header)[0] == 0:
                return {
                    "error": (
                        f"PK2 archive is empty (zero entries; no geometry to load): "
                        f"{file_path}"
                    )
                }
            progress.log("pk2_extraction", "status=error entries=0")
            return {"error": f"PK2 does not contain a room SGD: {file_path}"}
        lit_path = os.path.splitext(file_path)[0] + ".lit"
        lit_data = open(lit_path, "rb").read() if os.path.exists(lit_path) else None
        parsed_entries = 0
        skipped_entries = []
        ff2_item_structure = None
        normalized_pk2_path = os.path.normcase(os.path.abspath(file_path))
        is_ff2_furniture_pk2 = (
            use_ff2_parser
            and ("\\furniture\\" in normalized_pk2_path or "/furniture/" in normalized_pk2_path)
        )
        is_ff2_item_pk2 = use_ff2_parser and re.search(
            r"(?:^|[-_])i\d{3}(?:_|\.|$)", base_name.lower()
        ) is not None
        if is_ff2_furniture_pk2 or is_ff2_item_pk2:
            ff2_item_structure = {
                "format": (
                    "ff2_furniture_package" if is_ff2_furniture_pk2
                    else "ff2_item_package"
                ),
                "entries": [
                    {
                        "index": entry.get("index"),
                        "type": entry.get("type", ""),
                        "size": len(entry.get("data", b"")),
                    }
                    for entry in entries
                ],
            }
            item_sgd_offset = entries[0].get("offset") if entries else None
            if not isinstance(item_sgd_offset, int) or item_sgd_offset < 0:
                return {"error": f"FF2 item PK2 has no valid SGD entry: {file_path}"}
            try:
                with open(file_path, "rb") as item_file:
                    item_data = item_file.read()
                model = parse_sgd_ff2(
                    item_data[item_sgd_offset:],
                    name=base_name,
                    lit_data=lit_data,
                )
            except (ValueError, IndexError, struct.error) as exc:
                progress.error("ff2_item_sgd_parse", exc)
                return {"error": f"Failed to parse FF2 item geometry: {exc}"}
            if not model or not model.meshes:
                return {"error": f"No renderable geometry found in FF2 item PK2: {file_path}"}
            textures = [
                picture["image"]
                for picture in iter_embedded(file_path)
                if picture.get("image") is not None
            ]
            if len(textures) == 1:
                for material in model.materials:
                    material.texture_index = 0
            parsed_entries = 1
            ff2_item_structure["sgd_offset"] = item_sgd_offset
            ff2_item_structure["meshes"] = len(model.meshes)
            ff2_item_structure["decoded_textures"] = len(textures)
            ff2_item_structure["materials_mapped"] = (
                len(model.materials) if len(textures) == 1 else 0
            )
            progress.log(
                "ff2_item_sgd_parse",
                f"offset={item_sgd_offset} meshes={len(model.meshes)} "
                f"materials={len(model.materials)}"
            )
            if is_ff2_furniture_pk2:
                item_payload = item_data[item_sgd_offset:]
                texture_debug = {}
                try:
                    images, uploads = reconstruct_textures(
                        item_payload,
                        model.materials,
                        diagnostics=texture_debug,
                    )
                    for material in model.materials:
                        image = images.for_material(material)
                        if image is None:
                            continue
                        texture_index = next(
                            (index for index, existing in enumerate(textures)
                             if existing is image),
                            -1,
                        )
                        if texture_index < 0:
                            textures.append(image)
                            texture_index = len(textures) - 1
                        material.texture_index = texture_index
                    ff2_item_structure["gs_uploads"] = len(uploads)
                    ff2_item_structure["materials_mapped"] = sum(
                        material.texture_index >= 0 for material in model.materials
                    )
                except (ValueError, struct.error) as exc:
                    progress.error("ff2_furniture_texture_reconstruction", exc)
                ff2_item_structure["texture_reconstruction"] = texture_debug
                progress.log(
                    "ff2_furniture_texture_reconstruction",
                    f"textures={len(textures)} "
                    f"uploads={ff2_item_structure.get('gs_uploads', 0)}"
                )
        # Keep TEX0 descriptions per PK2 entry.  Reconstructing after merging
        # all entries makes auxiliary materials query unrelated VRAM, while
        # reconstructing only the first entry loses late panel textures.
        entry_materials = {}
        for index, entry in (enumerate(entries) if ff2_item_structure is None else ()):
            try:
                part = (parse_sgd_ff2 if use_ff2_parser else parse_sgd_ff1)(
                    entry["data"],
                    name=f"{base_name}_{index:04d}",
                    lit_data=lit_data,
                )
            except (ValueError, IndexError, struct.error) as exc:
                progress.error("sgd_parse", exc)
                skipped_entries.append({
                    "index": index,
                    "type": entry.get("type", ""),
                    "reason": f"{type(exc).__name__}: {exc}"
                })
                continue
            if not part or not part.meshes:
                skipped_entries.append({
                    "index": index,
                    "type": entry.get("type", ""),
                    "reason": "no renderable meshes"
                })
                continue
            parsed_entries += 1
            entry_materials[entry["index"]] = list(part.materials)
            progress.log(
                "sgd_parse",
                f"entry={index} type={entry.get('type', '') or 'unknown'} "
                f"meshes={len(part.meshes)} materials={len(getattr(part, 'materials', []))}"
            )
            if model is None:
                model = part
            else:
                merge_sgd_models(model, part)
                progress.log(
                    "geometry_merge",
                    f"entry={index} meshes={len(model.meshes)}"
                )
        if model and ff2_item_structure is not None:
            model_type = "prop" if is_ff2_furniture_pk2 else "item"
            model.texture_debug = getattr(model, "texture_debug", {})
            model.texture_debug["ff2_item_structure"] = ff2_item_structure
        elif model:
            progress.log(
                "geometry_merge",
                f"status=complete entries_parsed={parsed_entries} meshes={len(model.meshes)}"
            )
            # PK2 room textures are authoritative GS uploads. Do not let
            # adjacent name-matched files override a material before the
            # TEX0/TBP0 reconstruction below (notably 01_05_kabeS).
            textures = []
            for material in model.materials:
                material.texture_index = -1
            # FF1/FF2 rooms carry raw GS uploads in TRI2 blocks, not TIM2 files.
            # Reconstruct each SGD independently and match only exact TBP0.
            gs_texture_debug = []
            # The first GS upload stream initializes the room VRAM.  Include
            # valid TEX0 descriptions from later PK2 parts when querying that
            # VRAM; those parts contain the panel materials but no duplicate
            # upload stream of their own.
            gs_reconstruction_materials = [
                material
                for materials in entry_materials.values()
                for material in materials
                if 1 << (((getattr(material, "tex0", 0) or 0) >> 30) & 0xF) >= 16
            ]
            for entry in entries:
                try:
                    entry_debug = {"entry_index": entry["index"]}
                    reconstruction_materials = entry_materials.get(entry["index"], [])
                    if entry["index"] == min(entry_materials):
                        reconstruction_materials = gs_reconstruction_materials
                    images, _uploads = reconstruct_textures(
                        entry["data"],
                        reconstruction_materials,
                        diagnostics=entry_debug
                    )
                    progress.log(
                        "gs_vram_texture_reconstruction",
                        f"entry={entry['index']} uploads={len(_uploads)} "
                        f"detected={len(entry_debug.get('detected_uploads', []))} "
                        f"images={len(images)} tbp0={entry_debug.get('decoded_tbp0', [])}"
                    )
                    gs_texture_debug.append(entry_debug)
                    if _uploads:
                        for mat in model.materials:
                            img = images.get(getattr(mat, "tbp0", -1))
                            if img is not None:
                                # PIL image equality compares pixels, so two
                                # distinct GS textures with identical/flat
                                # pixels can collapse to the wrong index.
                                texture_index = next(
                                    (index for index, existing in enumerate(textures)
                                     if existing is img),
                                    -1,
                                )
                                if texture_index < 0:
                                    textures.append(img)
                                    texture_index = len(textures) - 1
                                mat.texture_index = texture_index
                except (ValueError, struct.error) as exc:
                    progress.error("gs_vram_texture_reconstruction", exc)
                    continue
            model.texture_debug = getattr(model, "texture_debug", {})
            model.texture_debug[
                "ff2_headerless_gs" if use_ff2_parser else "ff1_headerless_gs"
            ] = gs_texture_debug
            model_type = "room"
            progress.log("gs_vram_texture_reconstruction", f"textures={len(textures)}")
            if ff2_item_structure is not None:
                model.texture_debug = getattr(model, "texture_debug", {})
                model.texture_debug["ff2_item_structure"] = ff2_item_structure
        elif ff2_item_structure is not None:
            return {"error": f"No renderable geometry found in FF2 item PK2: {file_path}"}

    elif ext == '.mpk':
        # A model *pack*, not a standalone asset: it is the geometry half that
        # the .mdl/.mpx containers pull in automatically, so it has no textures
        # of its own and would load as an untextured model. It is also hidden
        # from the browser. Point the user at the real container instead.
        progress.error("mpk_not_standalone", f"file={base_name}")
        return {
            "error": (
                f"{base_name}.mpk is a model pack, not a standalone model. "
                f"Open the matching .mdl (or .mpx) instead -- it bundles this "
                f"geometry together with the textures."
            )
        }

    elif ext == '.acs':
        # Accessory/attach set. Its geometry is a separate 0x1060-only piece
        # that this reader does not decode, so it is hidden from the browser
        # and refused here rather than shown with the character's meshes.
        progress.error("acs_not_standalone", f"file={base_name}")
        return {
            "error": (
                f"{base_name}.acs is an accessory set, not a standalone model. "
                f"Open the matching .mdl or .mpx instead."
            )
        }

    elif ext == '.pkx':
        try:
            pkx_result = parse_ff1x_pkx(file_path, name=base_name)
        except (PKXError, XboxMPXError, XPR0Error) as exc:
            progress.error("ff1x_pkx_parse", exc)
            return {"error": f"Unsupported or malformed FF1 Xbox PKX: {exc}"}
        model = pkx_result.model
        textures = [texture["image"] for texture in pkx_result.textures]
        asset_category = os.path.basename(os.path.dirname(file_path)).casefold()
        model_type = "room" if asset_category == "room" else (
            "item" if asset_category == "item" else "prop"
        )
        model.parse_diagnostics = pkx_result.diagnostics
        if model_type == "room":
            lit_data, lit_path = read_lit_sidecar(file_path)
            lighting = apply_ff1_xbox_room_lighting(model, lit_data)
            if lit_path:
                lighting["source"] = os.path.basename(lit_path)
            pkx_result.diagnostics["ff1_lighting"] = lighting
        progress.log(
            "ff1x_pkx_parse",
            f"status=complete segments={pkx_result.diagnostics['package_segments']} "
            f"meshes={len(model.meshes)} materials={len(model.materials)} "
            f"textures={len(textures)}"
        )

    elif ext in ('.mdl', '.mpx', '.xpr'):
        # The PS2 and Xbox builds share both the PK2_HEAD container and the .mdl
        # extension, so sniff the payload before choosing a parser.  Both .mdl
        # flavours sit in this tree: the PS2 characters as man\mdl\mNNN_name.mdl
        # and the Xbox ones inside a man\mdl\mNNN_name\ subfolder, so the
        # extension alone cannot tell them apart.  Only the MPX/XPR containers
        # are Xbox now; the legacy .mdl Xbox reader is gone, so an Xbox .mdl
        # falls through to the PS2 parser below and is rejected there.
        if ext in ('.mpx', '.xpr'):
            xpr_variants = _xpr_variants_for(file_path)
            # What the parser binds on its own: the archive with this model's own
            # name, if there is one. It is also the default the cycle starts at.
            same_stem = os.path.splitext(base_name)[0].casefold()
            default_xpr = next((n for n in xpr_variants
                                if os.path.splitext(n)[0].casefold() == same_stem),
                               None)
            # An explicit sidecar overrides the same-stem one, which is how the
            # recolour variants are reached: several XPR files hold the same
            # geometry in different colourways. The viewer sends a bare file
            # name, so it is resolved next to the model rather than against the
            # server's working directory.
            xpr_override = xpr_override.strip() if xpr_override else None
            folder = os.path.dirname(os.path.abspath(file_path))
            if xpr_override and not os.path.isabs(xpr_override):
                candidate = os.path.join(folder, xpr_override)
                if os.path.isfile(candidate):
                    xpr_override = candidate
                else:
                    progress.error("ff1x_parse", f"xpr sidecar not found: {xpr_override}")
                    return {"error": f"XPR sidecar not found: {xpr_override}"}
            if xpr_override:
                xpr_active = os.path.basename(xpr_override)
                if xpr_active not in xpr_variants:
                    # An archive outside the declared set still loads, but it is
                    # not offered in the cycle and does not switch the button on.
                    xpr_variants = []
            elif default_xpr:
                xpr_active = default_xpr
            elif xpr_variants:
                # Declared colourways but no same-stem archive, so the parser
                # would bind nothing and every material would come up unbound --
                # a model with no visible textures at all. The first declared
                # colourway is bound instead, which is what the cycle would
                # select on the first click anyway. m000_miku2 is the case that
                # needs this: it ships no m000_miku2.xpr of its own.
                default_xpr = xpr_variants[0]
                xpr_active = default_xpr
                if os.path.isfile(os.path.join(folder, default_xpr)):
                    xpr_override = os.path.join(folder, default_xpr)
            try:
                xbox_result = parse_xbox_asset(file_path, name=base_name,
                                               xpr=xpr_override)
            except (XboxMPXError, XPR0Error) as exc:
                progress.error("ff1x_parse", exc)
                return {"error": f"Unsupported or malformed FF1 Xbox asset: {exc}"}
            xd = xbox_result.diagnostics
            # A bare .xpr is a texture archive with no geometry of its own. The
            # viewer needs a model to render, so report the surfaces (encoded
            # properly) without pretending there is a scene.
            if xbox_result.model is None:
                if not xbox_result.textures:
                    progress.error("ff1x_parse", "no geometry and no textures")
                    return {
                        "error": (
                            f"{base_name}: XPR0 texture archive has no usable "
                            f"texture surfaces"
                        )
                    }
                progress.log(
                    "ff1x_parse",
                    f"status=textures_only surfaces={len(xbox_result.textures)}",
                )
                if ext == '.xpr':
                    # A standalone archive must not leave the previous model selected for export.
                    from pz_core.ff3.pz_sgd_ff3 import SGDModel
                    CURRENT_STATE.update({"model": SGDModel(base_name),
                        "textures": [p["image"] for p in xbox_result.textures],
                        "collision": [], "source_file": file_path, "model_type": "textures"})
                return {
                    "filename": os.path.basename(file_path),
                    "model_type": "textures",
                    "type": "textures",
                    "meshes": [],
                    "bones": [],
                    "materials": [],
                    "textures": serialize_textures(
                        [p["image"] for p in xbox_result.textures]
                    ),
                    "uvs_flipped": True,
                    "diagnostics": xd,
                }
            model = xbox_result.model
            textures = [p["image"] for p in xbox_result.textures]
            model_type = "character"
            model.parse_diagnostics = xd
            progress.log(
                "ff1x_parse",
                f"status=complete sgd1050={xd['sgd1050_entries']} "
                f"sgd1060={xd['sgd1060_entries']} xpr0_surfaces={xd['xpr0_textures']} "
                f"meshes={len(model.meshes)} textures={len(textures)} "
                f"bound={xd['bound_by']}"
            )
        else:
            if game.lower() == "ff2":
                try:
                    mdl_result = parse_ff2_mdl(file_path, name=base_name)
                except FF2MDLError as exc:
                    progress.error("ff2_mdl_parse", exc)
                    return {"error": f"Unsupported or malformed FF2 MDL: {exc}"}
                model = mdl_result.model
                textures = mdl_result.textures
                model_type = "character"
                model.parse_diagnostics = mdl_result.diagnostics
                progress.log(
                    "ff2_mdl_parse",
                    f"status=complete model_entries="
                    f"{mdl_result.diagnostics['model_entries_parsed']} "
                    f"meshes={len(model.meshes)} textures={len(textures)}"
                )
            else:
                # An Xbox .mdl lands here too: both builds share the PK2_HEAD
                # container and the .mdl extension. They are handed to the PS2
                # parser, which reads the 0x1050 geometry records of the ones whose
                # layout it understands -- for those the geometry comes out
                # identical to the PS2 original, only without textures, because the
                # surfaces live in the embedded XPR0 archive. The rest it refuses,
                # and this turns that refusal into a message that points at the
                # container which does carry the geometry and the textures.
                try:
                    mdl_result = parse_ff1_mdl(file_path, name=base_name)
                except FF1MDLError as exc:
                    progress.error("mdl_parse", exc)
                    message = f"Unsupported or malformed FF1 MDL: {exc}"
                    if _has_xbox_texture_archive(file_path):
                        message += (
                            " (This is an Xbox build container: its surfaces are in an "
                            "embedded XPR0 archive and its geometry uses records the PS2 "
                            "reader does not know. Open the .mpx next to it instead.)"
                        )
                    return {"error": message}
                model = mdl_result.model
                textures = mdl_result.textures
                model_type = "character"
                model.parse_diagnostics = mdl_result.diagnostics
                progress.log(
                    "mdl_parse",
                    f"status=complete model_entries={mdl_result.diagnostics['model_entries_parsed']} "
                    f"meshes={len(model.meshes)} textures={len(textures)}"
                )

    elif ext in ('.mdlb', '.pk2b'):
        mapped = 0
        textures = []
        try:
            model = parse_ff2w_model(file_path, name=base_name)
            # FF2 Wii keeps textures out of the model container: the images
            # live in a sibling PPDB with the same stem (ch000_bontage.mdlb ->
            # ch000_bontage.ppdb, rch00.pk2b -> rch00.ppdb). Rooms additionally
            # ship a <stem>Mono.ppdb lightmap that is not bound to materials.
            ppdb_path = os.path.splitext(file_path)[0] + '.ppdb'
            if os.path.exists(ppdb_path):
                texture_set = parse_ff2w_textures(ppdb_path, model)
                textures = list(texture_set)
                for material in model.materials:
                    # ETAM may pair the diffuse texture with a separate alpha
                    # mask. Prefer the composited slot when that pair exists;
                    # binding only material_slots drops hair/lash/lace alpha.
                    slot = texture_set.material_alpha_slots.get(material.index, -1)
                    if slot < 0:
                        slot = texture_set.material_slots.get(material.index, -1)
                    if slot >= 0:
                        material.texture_index = slot
                        mapped += 1
            else:
                progress.log(
                    "mdlb_parse",
                    f"status=warning missing_texture_container "
                    f"expected={os.path.basename(ppdb_path)}"
                )
        except FF2WError as exc:
            progress.error("mdlb_parse", exc)
            return {"error": f"Unsupported or malformed FF2 Wii asset: {exc}"}
        model.name = base_name
        # .mdlb is always a skinned character. .pk2b covers rooms, doors and
        # items: rooms are static (one dummy bone, baked vertex colours) while
        # doors and items carry a couple of bones so they must stay props.
        if ext == '.mdlb':
            model_type = "character"
        elif len(model.bones) <= 1:
            model_type = "room"
        else:
            model_type = "prop"
        # FF2 Wii .mdlb atlases are wound the opposite way to the FF3 ones, so the
        # texture must be left unflipped for every FF2 Wii asset. The pk2b
        # surfaces keep that same unflipped orientation -- their earlier
        # problem was a sheared UV layout from a mis-detected vertex stride,
        # not an inversion, so flipping them was never the fix.
        model.uvs_are_flipped = True

        if ext == '.mdlb':
            scale = FF2W_SCALE_CHARACTER
        elif model_type == "room":
            scale = FF2W_SCALE_ROOM
        else:
            scale = FF2W_SCALE_PROP
        # Remembered so the glTF/GLB export can undo this and write real metres
        # (MODEL_UNIT_TO_METRES) the way the MDLB Viewer exports, instead of the
        # viewer units this scale exists to produce.
        model.ff2w_applied_scale = scale
        # The skeleton has to travel with the geometry: the viewer reads a bone's
        # world position out of matrix[12:15], not out of bone.trans, so scaling
        # only the vertex data left the rig at the original size. The 3x3 block
        # keeps its baked 0.05 bone scale and rotation; only the translation
        # column is rescaled, which is the correct way to change units.
        for mesh in model.meshes:
            mesh.positions = [[c * scale for c in p] for p in mesh.positions]
        for bone in model.bones:
            bone.trans = [c * scale for c in bone.trans]
            if len(getattr(bone, "matrix", ())) >= 16:
                bone.matrix = list(bone.matrix)
                bone.matrix[12] *= scale
                bone.matrix[13] *= scale
                bone.matrix[14] *= scale
        progress.log(
            "mdlb_parse",
            f"status=complete container={ext} meshes={len(model.meshes)} "
            f"bones={len(model.bones)} materials={len(model.materials)} "
            f"textures={len(textures)} materials_mapped={mapped}"
        )

    elif ext == '.pk4':
        normalized_path = os.path.normcase(os.path.abspath(file_path))
        is_room_path = "\\room\\" in normalized_path
        is_object_path = "\\object\\" in normalized_path
        is_furniture_path = "\\furniture\\" in normalized_path
        is_accessory_path = "\\accessory\\" in normalized_path
        is_fly_path = "\\fly\\" in normalized_path
        model = parse_pk4_model(
            file_path,
            name=base_name,
            flip_uv=False
        )
        progress.log(
            "pk4_extraction",
            f"status=complete meshes={len(getattr(model, 'meshes', [])) if model else 0}"
        )
        if model:
            # Keep the selected archive name for the export folder, even when
            # the first SGD inside the archive uses a numeric display name.
            model.name = base_name
            textures = find_textures_for_model(
                file_path,
                model,
                progress,
                decode_texture=decode_texture,
                iter_embedded=iter_embedded,
            )
            model_type = "room" if is_room_path else (
                "character" if "character" in file_path.lower() else "prop"
            )
            # FF3 room images need the WebGL-origin inversion, while FF3
            # character atlases already match the parsed character UVs.
            model.uvs_are_flipped = model_type != "character"
            if is_room_path:
                cld_candidates = [
                    os.path.join(os.path.dirname(file_path), "02_cld"),
                    os.path.join(os.path.splitext(file_path)[0], "02_cld"),
                    os.path.join(os.path.dirname(file_path), f"{base_name}_pk4", "02_cld"),
                    os.path.join(os.path.dirname(os.path.dirname(file_path)), base_name, "02_cld"),
                ]
                for cld_dir in cld_candidates:
                    collision = parse_cld_folder(cld_dir, name=f"{base_name}_collision")
                    if collision:
                        break

    elif ext == '.sgd':
        path_lower = os.path.normcase(os.path.abspath(file_path))
        with open(file_path, 'rb') as f:
            base_data = f.read()
        lit_data = None
        if source_dir:
            linked_name = os.path.basename(source_dir)
            room_stem = linked_name.split("_pk2_linked", 1)[0]
            room_dir = os.path.dirname(source_dir)
            lit_candidates = [
                os.path.join(room_dir, room_stem + ".lit"),
                os.path.join(os.path.dirname(room_dir), "room", room_stem + ".lit"),
            ]
            for lit_path in lit_candidates:
                if os.path.exists(lit_path):
                    with open(lit_path, "rb") as lf:
                        lit_data = lf.read()
                    break
        # Standalone SGD files are FF3 resources by default; FF1 item names,
        # item paths, and generated PK2-linked paths select the FF1 parser.
        use_ff1_parser = is_ff1_sgd(base_data, file_path)
        parse_sgd = (
            parse_sgd_ff2 if use_ff2_parser else
            (parse_sgd_ff1 if use_ff1_parser else parse_sgd_ff3)
        )
        merge_sgd = (
            merge_sgd_ff2 if use_ff2_parser else
            (merge_sgd_models if use_ff1_parser else merge_sgd_ff3)
        )
        model = parse_sgd(base_data, name=base_name, lit_data=lit_data)
        sgd_parse_metrics["sgd_processed"] += 1
        for key, value in getattr(model, "parse_diagnostics", {}).items():
            if key in sgd_parse_metrics:
                if key == "sgd_format":
                    sgd_parse_metrics[key] = value
                else:
                    sgd_parse_metrics[key] += value
        progress.log(
            "sgd_parse",
            f"entry=base meshes={len(getattr(model, 'meshes', []))} "
            f"materials={len(getattr(model, 'materials', []))}"
        )
        path_lower = os.path.normcase(os.path.abspath(file_path))
        is_room_sgd = "\\room\\" in path_lower or "/room/" in path_lower
        is_object_sgd = "\\object\\" in path_lower or "/object/" in path_lower
        is_furniture_sgd = "\\furniture\\" in path_lower or "/furniture/" in path_lower
        is_door_sgd = "\\door\\" in path_lower or "/door/" in path_lower
        is_accessory_sgd = "\\accessory\\" in path_lower or "/accessory/" in path_lower
        is_fly_sgd = "\\fly\\" in path_lower or "/fly/" in path_lower
        is_ff1_texture_sgd = (
            use_ff1_parser
            and (
                "\\item\\" in path_lower
                or "/item/" in path_lower
                or "\\furniture\\" in path_lower
                or "/furniture/" in path_lower
                or "\\door\\" in path_lower
                or "/door/" in path_lower
                or re.search(r"(?:^|[-_])i\d{3}(?:_|\.|$)", os.path.basename(path_lower))
                is not None
            )
        )
        model_type = "room" if is_room_sgd else "prop"
        if not is_room_sgd and "character" in path_lower:
            model_type = "character"
        if not use_ff1_parser:
            model.uvs_are_flipped = model_type != "character"
        elif is_ff1_texture_sgd:
            # Standalone FF1 furniture/door images already have the PNG origin
            # expected by the parsed UVs. Items retain the legacy image flip.
            furniture_or_door = is_furniture_sgd or is_door_sgd
            model.uvs_are_flipped = furniture_or_door
            model.texture_debug = getattr(model, "texture_debug", {})
            model.texture_debug["ff1_item_orientation"] = {
                "uvs_flipped": furniture_or_door,
                "texture_flip_y": not furniture_or_door,
                "correction": (
                    "frontend_texture_flip_only"
                    if not furniture_or_door
                    else "native_uv_orientation_no_texture_flip"
                ),
            }

        # ── Auto-merge sibling numbered SGDs (e.g. 0000–0015 for one character) ──
        sgd_dir    = os.path.dirname(file_path)
        sgd_stem   = os.path.splitext(os.path.basename(file_path))[0]

        if sgd_stem.isdigit():
            # Some packs ship components the viewer must ignore (ch000 keeps its
            # shadow body in 0015.sgd), and 0015 is the collision component for
            # the rest. Never start the merge from one of those.
            skipped_indexes = set(skipped_sgd_components(file_path))
            collision_indexes = set()
            if not is_room_sgd:
                collision_indexes.add(15)
                if not any(
                    os.path.splitext(name)[0] == "15" for name in os.listdir(sgd_dir)
                ):
                    collision_indexes.add(14)

            if int(sgd_stem) in skipped_indexes | collision_indexes:
                candidates = numbered_sgd_files(
                    sgd_dir, skipped_indexes | collision_indexes
                )
                if candidates:
                    file_path = os.path.join(sgd_dir, candidates[0])
                    sgd_stem = os.path.splitext(candidates[0])[0]
                    with open(file_path, 'rb') as f:
                        model = parse_sgd(f.read(), name=sgd_stem)

            # Collect all sibling numbered SGDs sorted, excluding the file we just loaded
            all_numbered = numbered_sgd_files(
                sgd_dir, skipped_indexes, exclude_path=file_path
            )

            if all_numbered:
                # Ensure we have bones — if not, look for them in a sibling first
                if not model.bones:
                    for sib in all_numbered:
                        try:
                            with open(os.path.join(sgd_dir, sib), 'rb') as sf:
                                candidate = parse_sgd(sf.read(), name=sib)
                            if candidate and candidate.bones:
                                model.bones = candidate.bones
                                break
                        except Exception:
                            pass

                # Merge every other numbered SGD into the base model
                merged_count = 0
                for sib_name in all_numbered:
                    sib_path = os.path.join(sgd_dir, sib_name)
                    try:
                        with open(sib_path, 'rb') as sf:
                            sib_data = sf.read()
                        sib_model = parse_sgd(
                            sib_data,
                            name=os.path.splitext(sib_name)[0],
                            lit_data=lit_data,
                            external_bones=model.bones if model.bones else None,
                        )
                        sgd_parse_metrics["sgd_processed"] += 1
                        for key, value in getattr(sib_model, "parse_diagnostics", {}).items():
                            if key in sgd_parse_metrics:
                                sgd_parse_metrics[key] += value
                        if sib_model and sib_model.meshes:
                            merge_sgd(model, sib_model)
                            merged_count += 1
                    except Exception as e:
                        sgd_parse_metrics["sgd_omitted"] += 1
                        progress.error("geometry_merge", e)

                if merged_count > 0:
                    model.name = sgd_stem  # keep original stem as display name
                    model_type = "room" if is_room_sgd else "character"
                    progress.log(
                        "geometry_merge",
                        f"merged_siblings={merged_count} meshes={len(model.meshes)}"
                    )
        # ────────────────────────────────────────────────────────────────────────

        textures = find_textures_for_model(
            file_path,
            model,
            progress,
            decode_texture=decode_texture,
            iter_embedded=iter_embedded,
        )
        if is_ff1_texture_sgd:
            model.texture_debug = getattr(model, "texture_debug", {})
            furniture_or_door = is_furniture_sgd or is_door_sgd
            model.texture_debug["ff1_item_orientation"] = {
                "uvs_flipped": furniture_or_door,
                "texture_flip_y": not furniture_or_door,
                "correction": (
                    "frontend_texture_flip_only"
                    if not furniture_or_door
                    else "native_uv_orientation_no_texture_flip"
                ),
            }

    elif ext == '.obj' and os.path.basename(file_path).lower().startswith('msn'):
        with open(file_path, 'rb') as f:
            col_meshes = parse_all_rooms_collision_from_map(f.read())
        if col_meshes:
            collision = col_meshes
            model = collision_to_sgd_model(col_meshes, name=base_name)
            model_type = "map_collision"
        else:
            return {"error": f"No collision data found in {file_path}"}

    elif ext == '.cld':
        with open(file_path, 'rb') as f:
            collision = parse_cld(f.read(), name=base_name)
        if collision:
            model = collision_to_sgd_model(collision, name=base_name)
            model_type = "collision"
        else:
            return {"error": f"Failed to parse collision data from {file_path}"}

    if not model:
        return {"error": f"Failed to parse model from {file_path}"}

    # Apply after sibling/PK4 merging so extracted SGD loads receive the same
    # narrow correction as direct PK4 loads.
    apply_named_foliage_uv_corrections(model)

    # FF2 PS2 room maps use the opposite texture origin from the default
    # WebGL upload path. Furniture PK2 assets have the same inversion, but
    # keep both corrections scoped to their respective FF2 PK2 categories.
    if use_ff2_parser and ext == ".pk2" and is_ff2_furniture_pk2:
        model.uvs_are_flipped = True
    elif use_ff2_parser and ext == ".pk2" and model_type == "room":
        model.uvs_are_flipped = True

    # Every model, whatever the game or the container it came out of. These
    # atlases are low resolution and are magnified across whole surfaces, so
    # nearest sampling breaks them into hard texel blocks that are not in the
    # game: the MDLB Viewer, which reads the very same PS2-style containers,
    # uploads with GL_LINEAR magnification (mdlb_viewer.py:603).
    # Magnification only. Minification keeps nearest plus mipmaps, because that
    # is what avoids the aliasing linear sampling would introduce when a texture
    # is shrunk rather than enlarged.
    model.texture_mag_linear = True

    # The export folder must follow the selected file, not an internal SGD
    # name such as 0000.
    model.export_name = os.path.splitext(os.path.basename(file_path))[0]
    CURRENT_STATE["model"] = model
    CURRENT_STATE["textures"] = textures
    CURRENT_STATE["collision"] = collision
    CURRENT_STATE["source_file"] = file_path
    CURRENT_STATE["model_type"] = model_type

    progress.log(
        "serialization",
        f"meshes={len(getattr(model, 'meshes', []))} materials={len(getattr(model, 'materials', []))} "
        f"textures={len(textures)}"
    )
    response = serialize_model(model, textures, collision, model_type)
    if xpr_variants:
        response["xpr_variants"] = xpr_variants
        response["xpr_active"] = xpr_active
        # The viewer only offers the recolour switch where it is meaningful: one
        # model with more than one palette. Deciding it here keeps the rule out
        # of the frontend, which otherwise has to re-derive it from the file name.
        response["xpr_variant_switch"] = len(xpr_variants) > 1
    if ext == '.pk2':
        response["diagnostics"].update({
            "pk2_entries": len(entries),
            "pk2_parsed": parsed_entries,
            "pk2_skipped": len(skipped_entries),
            "pk2_skipped_entries": skipped_entries,
        })
    if ext == '.sgd':
        response["diagnostics"].update(sgd_parse_metrics)
    response["diagnostics"]["load_ms"] = round((time.perf_counter() - load_started) * 1000.0, 1)
    response["diagnostics"]["cache"] = "miss"
    if not xbox_sidecar_asset:
        LOAD_CACHE[cache_key] = (cache_stamp, response)
    # Keep memory bounded while retaining the common repeat-load fast path.
    if len(LOAD_CACHE) > 4:
        LOAD_CACHE.pop(next(iter(LOAD_CACHE)))
    progress.log(
        "response_completion",
        f"status=ok model_type={model_type} meshes={len(model.meshes)} "
        f"textures={len(textures)} bytes={len(json.dumps(response))}"
    )
    return response


# ─────────────────────────────────────────────────────────────────────────────
# Batch Convert helpers
# ─────────────────────────────────────────────────────────────────────────────

def find_batch_files(source_dir, recursive=True):
    """Walk source_dir and return a list of paths that handle_load_file can load.

    Recognised targets:
    - *_pk4 directories  (FF3 character packs — passed as dirs, handle_load_file
                          resolves the nested SGD automatically)
    - *.pk4 files        (FF3 object / prop / room models)
    - *.mdl files        (FF1 character models, PS2 or Xbox build)
    - *.mpx files        (FF1 Xbox character models)
    - *.pkx files        (FF1 Xbox item, furniture, door and room models)
    - *.pk2 files        (FF1/FF2 room models)

    The Xbox model *pack* (.mpk) and accessory set (.acs) are deliberately not
    listed: they are not standalone models and are resolved automatically by
    the containers that own them.
    """
    results = []
    seen = set()
    file_exts = {'.pk4', '.mdl', '.mpx', '.pkx', '.pk2', '.mdlb', '.pk2b'}

    def add(path):
        norm = os.path.normcase(os.path.abspath(path))
        if norm not in seen:
            seen.add(norm)
            results.append(path)

    if recursive:
        for root, dirs, files in os.walk(source_dir):
            # Capture *_pk4 dirs as single units and skip walking into them
            pk4_dirs = [d for d in dirs if d.lower().endswith('_pk4')]
            for d in pk4_dirs:
                add(os.path.join(root, d))
            for d in pk4_dirs:
                dirs.remove(d)

            for f in files:
                if os.path.splitext(f)[1].lower() in file_exts:
                    add(os.path.join(root, f))
    else:
        for entry in sorted(os.listdir(source_dir)):
            full = os.path.join(source_dir, entry)
            if os.path.isdir(full):
                if entry.lower().endswith('_pk4'):
                    add(full)
            elif os.path.splitext(entry)[1].lower() in file_exts:
                add(full)

    return results


def batch_export_worker(job_id, source_dir, output_dir, fmt, options):
    """Background thread: load each file and export it."""
    recursive = options.get('recursive', True)
    include_textures = options.get('include_textures', True)
    include_colors = options.get('include_colors', True)
    # Off by default: flattening the skeleton to its bind pose is wrong for
    # anything that is going to be animated, and nothing that is not animated
    # wants it either. The viewer no longer offers it as a choice.
    tpose_only = options.get('tpose_only', False)
    game = options.get('game', '')
    conflict = options.get('conflict', 'skip')  # 'skip' or 'overwrite'
    mirror_structure = options.get('mirror_structure', True)

    def log(msg):
        BATCH_JOBS[job_id]['log'].append(msg)

    try:
        files = find_batch_files(source_dir, recursive)
        BATCH_JOBS[job_id]['total'] = len(files)

        if not files:
            BATCH_JOBS[job_id]['status'] = 'done'
            BATCH_JOBS[job_id]['message'] = 'No convertible files found in the selected folder.'
            return

        for file_path in files:
            if BATCH_JOBS[job_id].get('cancelled'):
                BATCH_JOBS[job_id]['status'] = 'cancelled'
                return

            display = os.path.basename(file_path)
            BATCH_JOBS[job_id]['current'] = display

            try:
                result = handle_load_file(file_path, game=game)
                if 'error' in result:
                    raise ValueError(result['error'])

                model = CURRENT_STATE['model']
                textures = CURRENT_STATE['textures'] if include_textures else []
                model_type = CURRENT_STATE.get('model_type', 'model')

                if not model:
                    raise ValueError('No model parsed')

                # Always derive the export name from the original input path,
                # NOT from model.export_name: for _pk4 directories handle_load_file
                # resolves them to the internal 0000.sgd, making export_name="0000".
                export_name = os.path.splitext(display)[0]

                if mirror_structure:
                    # For directories, rel is relative to the parent of file_path;
                    # for files, rel is relative to the file's directory.
                    abs_fp = os.path.abspath(file_path)
                    ref_dir = abs_fp if os.path.isdir(abs_fp) else os.path.dirname(abs_fp)
                    rel = os.path.relpath(ref_dir, os.path.abspath(source_dir))
                    # rel == '.' means the asset is directly in source_dir
                    asset_dir = os.path.join(output_dir, rel, export_name) if rel != '.' else os.path.join(output_dir, export_name)
                else:
                    asset_dir = os.path.join(output_dir, export_name)

                os.makedirs(asset_dir, exist_ok=True)
                out_path = os.path.join(asset_dir, f"{export_name}.{fmt}")

                if conflict == 'skip' and os.path.exists(out_path):
                    BATCH_JOBS[job_id]['skipped'] += 1
                    BATCH_JOBS[job_id]['done'] += 1
                    log(f"⏭ Skipped (exists): {display}")
                    continue

                if textures and include_textures:
                    tex_dir = os.path.join(asset_dir, f"{export_name}_textures")
                    export_textures_png(model, textures, tex_dir)

                include_armature = model_type != 'room'
                if fmt in ('glb', 'gltf'):
                    export_xbox_aware_glb(model, out_path, export_t_pose=tpose_only,
                               include_vertex_colors=include_colors,
                               textures=textures,
                               include_armature=include_armature)
                elif fmt == 'obj':
                    export_xbox_aware_obj(model, out_path,
                               include_vertex_colors=include_colors,
                               textures=textures)
                elif fmt == 'dae':
                    export_dae(model, out_path, export_t_pose=tpose_only,
                               include_vertex_colors=include_colors)
                elif fmt == 'fbx':
                    export_fbx(model, out_path, export_t_pose=tpose_only,
                               include_vertex_colors=include_colors)
                else:
                    raise ValueError(f"Unsupported format: {fmt}")

                BATCH_JOBS[job_id]['done'] += 1
                log(f"✓ {display}")

            except Exception as exc:
                BATCH_JOBS[job_id]['failed'] += 1
                BATCH_JOBS[job_id]['done'] += 1
                err = f"✗ {display}: {str(exc)[:150]}"
                BATCH_JOBS[job_id]['errors'].append(err)
                log(err)

        BATCH_JOBS[job_id]['status'] = 'done'
        BATCH_JOBS[job_id]['current'] = ''

    except Exception as exc:
        BATCH_JOBS[job_id]['status'] = 'error'
        BATCH_JOBS[job_id]['error'] = str(exc)


class PZViewerHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def end_headers(self):
        # style.css and index.html are requested with no cache-busting token
        # (only app.js carries ?v=), so the browser kept a stale copy after an
        # edit and applied CSS that no longer matched the file on disk. That is
        # what silently disabled the theme overrides. `no-store` is used rather
        # than `no-cache` on purpose: entries cached before this header existed
        # carry no validators, so the browser treats them as fresh and never
        # revalidates, while no-store also forbids storing the new copy. These
        # assets are a few KB served from localhost, so re-reading them is free.
        if b'Cache-Control' not in getattr(self, '_headers_buffer', b''):
            self.send_header('Cache-Control', 'no-store, must-revalidate')
        super().end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == '/api/preferences':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(load_folder_preferences()).encode('utf-8'))
            return

        if parsed.path == '/api/browse':
            qs = parse_qs(parsed.query)
            target_dir = qs.get('dir', [''])[0].strip()
            game = qs.get('game', ['all'])[0].lower()
            saved_root = load_folder_preferences().get(game) if game in GAME_IDS else ""
            if saved_root and not folder_is_within_root(target_dir, saved_root):
                self.send_response(403)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    "error": "Navigation outside the selected root folder is not allowed",
                    "root": saved_root.replace("\\", "/")
                }).encode('utf-8'))
                return
            file_extensions = GAME_EXTENSIONS.get(game, GAME_EXTENSIONS['all'])
            if not target_dir:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Choose a folder first"}).encode('utf-8'))
                return
            if not os.path.exists(target_dir):
                self.send_response(404)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Folder not found"}).encode('utf-8'))
                return

            target_dir = resolve_ff1_man_directory(target_dir, saved_root, game)
            target_dir = resolve_ff1x_man_directory(target_dir, saved_root, game)
            target_dir = resolve_ff2_room_directory(target_dir, saved_root, game)
            target_dir = resolve_ff2_man_directory(target_dir, saved_root, game)
            target_dir = resolve_ff3_category_directory(target_dir, saved_root, game)
            target_dir = resolve_ff3_model_pack_directory(
                target_dir, saved_root, game
            )
            
            absolute_target = os.path.abspath(target_dir)
            parent_dir = os.path.dirname(absolute_target)
            parent_dir = resolve_ff1_parent_directory(
                absolute_target, saved_root, parent_dir, game
            )
            parent_dir = resolve_ff1x_parent_directory(
                absolute_target, saved_root, parent_dir, game
            )
            parent_dir = resolve_ff2_parent_directory(
                absolute_target, saved_root, parent_dir, game
            )
            parent_dir = resolve_ff3_parent_directory(
                absolute_target, saved_root, parent_dir, game
            )
            if saved_root:
                saved_root_abs = os.path.abspath(saved_root)
                # The browser never walks out of the configured game folder. At
                # the root itself the parent is therefore the root, so no
                # ".. (Parent Directory)" row is offered and the up button is
                # disabled: the real parent of the root is outside it and would
                # otherwise be one click away.
                if os.path.normcase(absolute_target) == os.path.normcase(saved_root_abs):
                    parent_dir = saved_root_abs
                elif not folder_is_within_root(parent_dir, saved_root_abs):
                    parent_dir = saved_root_abs
            parent_dir = parent_dir.replace('\\', '/')
            items = []
            try:
                all_entries = sorted(os.listdir(target_dir))

                # Detect folders that contain a multi-part numbered SGD pack
                # (e.g. 0000.sgd … 0015.sgd).  Only show the anchor (lowest-numbered)
                # and hide the rest so the browser doesn't show 16 entries per character.
                numbered_sgds = [
                    e for e in all_entries
                    if e.lower().endswith('.sgd') and os.path.splitext(e)[0].isdigit()
                ]
                hidden_numbered = set()
                if len(numbered_sgds) > 1:
                    anchor = numbered_sgds[0]         # e.g. "0000.sgd"
                    hidden_numbered = set(numbered_sgds[1:])  # hide the rest

                # The Xbox .mdl set is hidden from the tree. Only two of its 64
                # files can be read at all, by the PS2 parser, and only without
                # textures -- the geometry records of the other 62 are not the
                # ones it knows. Listing 64 rows that mostly open to an error is
                # worse than not listing them; the .mpx next to each one is the
                # container that really works. The load path still accepts them
                # if the path is typed directly.
                #
                # Two ways of recognising the situation, because either one alone
                # misses cases: browsing the ff1x root hides them wherever that
                # root happens to point, and a folder literally named XBOX hides
                # them even when reached by navigating in from the PS2 ff1 tree,
                # where the request still says game=ff1.
                hide_xbox_mdl = (game == 'ff1x' or os.path.basename(
                    os.path.normpath(target_dir)).casefold() == 'xbox')
                for entry in all_entries:
                    if entry in hidden_numbered:
                        continue
                    full_p = os.path.join(target_dir, entry)
                    is_dir = os.path.isdir(full_p)
                    ext = os.path.splitext(entry)[1].lower()
                    if is_dir and is_hidden_ff3_model_variant(target_dir, entry, game):
                        continue
                    if is_dir and is_hidden_ff3_character_entry(target_dir, entry, game):
                        continue
                    if is_dir and is_hidden_ff3_character_pack_entry(target_dir, entry, game):
                        continue
                    if is_dir and is_hidden_ff3_root_entry(
                            target_dir, entry, saved_root, game):
                        continue
                    if is_dir and is_hidden_ff1_animation_folder(
                            target_dir, entry, saved_root, game):
                        continue
                    if is_dir and is_hidden_ff1x_furnmime_folder(
                            target_dir, entry, saved_root, game):
                        continue
                    if is_dir and is_hidden_ff2_camera_folder(
                            target_dir, entry, saved_root, game):
                        continue
                    if is_dir and is_hidden_ff2w_room_auxiliary_folder(
                            target_dir, entry, saved_root, game):
                        continue
                    if not is_dir and is_hidden_ff2_empty_furniture_pk2(
                            target_dir, entry, saved_root, game):
                        continue
                    if not is_dir and is_hidden_ff3_furniture_archive(
                            target_dir, entry, saved_root, game):
                        continue
                    if hide_xbox_mdl and ext == '.mdl':
                        continue
                    if not is_dir and ext not in file_extensions:
                        continue
                    t_str = "dir" if is_dir else ext.lstrip('.')
                    load_path = None
                    if is_dir and game == 'ff3':
                        for category in (
                            'character', 'accessory', 'door', 'fly', 'furniture', 'object',
                            'room'
                        ):
                            model_dir = os.path.join(saved_root, category, 'model')
                            if os.path.normcase(os.path.abspath(target_dir)) == os.path.normcase(
                                    os.path.abspath(model_dir)):
                                load_path = ff3_model_pack_load_path(full_p)
                                break
                    # Mark the anchor of a multi-part pack specially
                    if not is_dir and entry in numbered_sgds and hidden_numbered:
                        t_str = "sgd_pack"
                    display_entry = entry
                    if not hidden_numbered or entry not in numbered_sgds:
                        if (
                            game == 'ff3'
                            and os.path.normcase(os.path.abspath(target_dir))
                            == os.path.normcase(os.path.abspath(os.path.join(
                                saved_root, 'character', 'model'
                            )))
                        ):
                            display_entry = ff3_character_display_name(entry)
                    else:
                        display_entry = f"{entry}  (+{len(hidden_numbered)} parts)"
                    item = {
                        "name": display_entry,
                        "path": full_p.replace('\\', '/'),
                        "type": t_str,
                        "is_dir": is_dir,
                        "size": 0 if is_dir else os.path.getsize(full_p)
                    }
                    if load_path:
                        item["load_path"] = load_path.replace('\\', '/')
                    items.append(item)
            except Exception as e:
                pass

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({
                "current_dir": target_dir.replace('\\', '/'),
                "parent_dir": parent_dir,
                "items": items
            }).encode('utf-8'))
            return

        elif parsed.path == '/api/choose_folder':
            qs = parse_qs(parsed.query)
            init_dir = qs.get('dir', [''])[0]
            game = qs.get('game', ['all'])[0].lower()
            picker_titles = {game_id: 'Select ' + label for game_id, label in GAME_LABELS.items()}
            chosen = ""
            try:
                import tkinter as tk
                from tkinter import filedialog
                root = tk.Tk()
                root.withdraw()
                root.attributes('-topmost', True)
                chosen = filedialog.askdirectory(
                    initialdir=init_dir or None,
                    title=picker_titles.get(game, "Select Fatal Frame / Project Zero Folder")
                )
                root.destroy()
            except Exception as e:
                pass

            resp = {"status": "ok", "chosen": chosen.replace('\\', '/') if chosen else ""}
            if resp["chosen"] and game in GAME_IDS:
                try:
                    save_folder_preference(game, resp["chosen"])
                except OSError as exc:
                    _LOAD_LOGGER.warning("Could not save folder preference: %s", exc)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(resp).encode('utf-8'))
            return

        elif parsed.path == '/api/batch_status':
            qs = parse_qs(parsed.query)
            job_id = qs.get('job', [''])[0]
            job = BATCH_JOBS.get(job_id)
            if not job:
                self.send_response(404)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'Batch job not found'}).encode('utf-8'))
                return
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(job).encode('utf-8'))
            return

        elif parsed.path == '/api/load':
            qs = parse_qs(parsed.query)
            file_path = qs.get('path', [''])[0]
            try:
                res = handle_load_file(file_path, qs.get("game", [""])[0],
                                       xpr_override=qs.get("xpr", [""])[0])
                status_code = 400 if "error" in res else 200
            except (OSError, ValueError, struct.error, RuntimeError) as exc:
                progress = LoadProgress(file_path)
                progress.error("error", exc)
                res = {"error": f"Failed to load asset: {exc}"}
                status_code = 500
            self.send_response(status_code)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(res).encode('utf-8'))
            return

        elif parsed.path == '/api/export_textures':
            model = CURRENT_STATE.get("model")
            textures = CURRENT_STATE.get("textures", [])
            if self.command == "POST":
                try:
                    content_length = int(self.headers.get("Content-Length", "0"))
                    payload = json.loads(self.rfile.read(content_length) or b"{}")
                    serialized_textures = payload.get("textures")
                    if isinstance(serialized_textures, list):
                        decoded_textures = []
                        for record in serialized_textures:
                            if not isinstance(record, dict):
                                continue
                            data_uri = record.get("data_uri")
                            if not isinstance(data_uri, str) or "," not in data_uri:
                                continue
                            encoded = data_uri.split(",", 1)[1]
                            image = Image.open(BytesIO(base64.b64decode(encoded))).convert("RGBA")
                            decoded_textures.append(image)
                        if decoded_textures:
                            textures = decoded_textures
                except (ValueError, TypeError, json.JSONDecodeError, binascii.Error) as exc:
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": f"Invalid texture payload: {exc}"}).encode('utf-8'))
                    return
            if not model or not textures:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"error": "No textures loaded for current model."}).encode('utf-8'))
                return

            source_file = CURRENT_STATE.get("source_file", "")
            base_name = os.path.splitext(os.path.basename(source_file))[0] or getattr(model, 'export_name', getattr(model, 'name', 'model'))
            tex_subfolder = os.path.join(EXPORTS_DIR, f"{base_name}_textures")
            saved = export_textures_png(model, textures, tex_subfolder)

            zip_buf = BytesIO()
            with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
                for t_name, t_path, idx in saved:
                    zf.write(t_path, arcname=t_name)

            zip_bytes = zip_buf.getvalue()
            self.send_response(200)
            self.send_header('Content-Type', 'application/zip')
            self.send_header('Content-Disposition', f'attachment; filename="{base_name}_textures.zip"')
            self.send_header('Content-Length', str(len(zip_bytes)))
            self.end_headers()
            self.wfile.write(zip_bytes)
            return

        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)

        if parsed.path == '/api/export_textures':
            req = json.loads(post_data.decode('utf-8')) if post_data else {}
            model = CURRENT_STATE.get("model")
            records = req.get("textures", [])
            textures = []
            try:
                for record in records:
                    data_uri = record.get("data_uri", "")
                    if not data_uri.startswith("data:image/") or ";base64," not in data_uri:
                        raise ValueError("Texture record does not contain a base64 data_uri")
                    encoded = data_uri.split(";base64,", 1)[1]
                    image = Image.open(BytesIO(base64.b64decode(encoded, validate=True)))
                    image.load()
                    textures.append(image)
            except (ValueError, binascii.Error, OSError) as exc:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"error": f"Invalid texture data: {exc}"}).encode('utf-8'))
                return
            if not model or not textures:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"error": "No texture records were supplied."}).encode('utf-8'))
                return
            base_name = os.path.splitext(os.path.basename(
                req.get("filename", "") or CURRENT_STATE.get("source_file", "")
            ))[0] or getattr(model, 'name', 'model')
            tex_subfolder = os.path.join(EXPORTS_DIR, f"{base_name}_textures")
            saved = export_textures_png(model, textures, tex_subfolder)
            zip_buf = BytesIO()
            with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
                for t_name, t_path, _idx in saved:
                    zf.write(t_path, arcname=t_name)
            zip_bytes = zip_buf.getvalue()
            self.send_response(200)
            self.send_header('Content-Type', 'application/zip')
            self.send_header('Content-Disposition', f'attachment; filename="{base_name}_textures.zip"')
            self.send_header('Content-Length', str(len(zip_bytes)))
            self.end_headers()
            self.wfile.write(zip_bytes)
            return

        if parsed.path == '/api/preferences':
            req = json.loads(post_data.decode('utf-8')) if post_data else {}
            game = req.get("game", "")
            path = req.get("path", "")
            try:
                preferences = save_folder_preference(game, path)
                status_code = 200
            except (OSError, ValueError, TypeError) as exc:
                preferences = {"error": str(exc)}
                status_code = 500
            self.send_response(status_code)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(preferences).encode('utf-8'))
            return

        if parsed.path == '/api/load':
            req = json.loads(post_data.decode('utf-8')) if post_data else {}
            file_path = req.get('path', '')
            res = handle_load_file(file_path, req.get("game", ""))
            status_code = 400 if "error" in res else 200
            self.send_response(status_code)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(res).encode('utf-8'))
            return

        elif parsed.path in ('/api/export', '/api/export_collision'):
            req = json.loads(post_data.decode('utf-8')) if post_data else {}
            fmt = req.get('format', 'glb').lower()
            target = req.get('target', 'model').lower()
            if parsed.path == '/api/export_collision':
                target = 'collision'

            # ----------------------------------------------------
            # Collision-Only Export
            # ----------------------------------------------------
            if target == 'collision':
                collision = CURRENT_STATE.get("collision", [])
                base_model = CURRENT_STATE.get("model")
                base_name = getattr(base_model, 'name', 'model') if base_model else 'map'

                if not collision:
                    # Auto-check if source file was a room and collision can be found
                    src = CURRENT_STATE.get("source_file", "")
                    bname = os.path.splitext(os.path.basename(src))[0]
                    if bname.startswith('r') and len(bname) >= 4 and bname[1:4].isdigit():
                        try:
                            ridx = int(bname[1:4])
                            map_dir = "f:/Project Zero Modding/Obscura/bin/map_data"
                            for msn_idx in range(5):
                                map_file = os.path.join(map_dir, f"msn0{msn_idx}map.obj")
                                if os.path.exists(map_file):
                                    with open(map_file, 'rb') as mf:
                                        col_meshes = parse_room_collision_from_map(mf.read(), room_idx=ridx)
                                        if col_meshes:
                                            collision = col_meshes
                                            CURRENT_STATE["collision"] = collision
                                            break
                        except Exception:
                            pass

                if not collision:
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "No 3D collision geometry available to export for this asset."}).encode('utf-8'))
                    return

                col_model = collision_to_sgd_model(collision, name=f"{base_name}_collision")
                export_filename = f"{base_name}_collision.{fmt}"
                out_path = os.path.join(EXPORTS_DIR, export_filename)

                try:
                    if fmt in ('glb', 'gltf'):
                        export_glb(col_model, out_path, export_t_pose=True, include_vertex_colors=True, include_armature=False)
                    elif fmt == 'obj':
                        export_obj(col_model, out_path, include_vertex_colors=True)
                    elif fmt == 'dae':
                        export_dae(col_model, out_path, export_t_pose=True, include_vertex_colors=True)
                    elif fmt == 'fbx':
                        export_fbx(col_model, out_path, export_t_pose=True, include_vertex_colors=True)
                    else:
                        raise ValueError(f"Unsupported collision export format: {fmt}")

                    with open(out_path, 'rb') as f:
                        file_bytes = f.read()

                    content_type = 'model/gltf-binary' if fmt == 'glb' else 'application/octet-stream'
                    self.send_response(200)
                    self.send_header('Content-Type', content_type)
                    self.send_header('Content-Disposition', f'attachment; filename="{export_filename}"')
                    self.send_header('Content-Length', str(len(file_bytes)))
                    self.end_headers()
                    self.wfile.write(file_bytes)
                    return

                except Exception as e:
                    self.send_response(500)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": f"Collision export failed: {str(e)}"}).encode('utf-8'))
                    return

            # ----------------------------------------------------
            # Standard 3D Model Export
            # ----------------------------------------------------
            include_colors = req.get('include_colors', True)
            include_textures = req.get('include_textures', True)
            include_collision = req.get('include_collision', False)
            tpose_only = req.get('tpose_only', False)

            model = CURRENT_STATE["model"]
            if not model:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"error": "No model loaded"}).encode('utf-8'))
                return

            source_file = CURRENT_STATE.get("source_file", "")
            base_name = os.path.splitext(os.path.basename(source_file))[0]
            if not base_name:
                base_name = getattr(model, 'export_name', '') or getattr(model, 'name', 'model')
            base_name = os.path.basename(base_name) or 'model'
            selected_destination = req.get('destination') or ''
            if selected_destination:
                asset_export_dir = os.path.abspath(os.path.expanduser(selected_destination))
            else:
                asset_export_dir = os.path.join(EXPORTS_DIR, base_name)
            os.makedirs(asset_export_dir, exist_ok=True)
            export_filename = f"{base_name}.{fmt}"
            out_path = os.path.join(asset_export_dir, export_filename)

            textures = CURRENT_STATE["textures"] if include_textures else []

            try:
                # Always extract and convert textures to PNG in exports folder
                tex_subfolder = os.path.join(asset_export_dir, f"{base_name}_textures")
                saved_tex = []
                if textures and include_textures:
                    saved_tex = export_textures_png(model, textures, tex_subfolder)

                # Check if zip bundle requested or textures only
                if fmt in ('zip', 'glb_zip', 'obj_zip') or target == 'textures':
                    zip_filename = f"{base_name}_with_textures.zip" if target != 'textures' else f"{base_name}_textures.zip"
                    zip_path = os.path.join(asset_export_dir, zip_filename)
                    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                        if target != 'textures':
                            if fmt == 'obj_zip':
                                obj_path = os.path.join(asset_export_dir, f"{base_name}.obj")
                                export_xbox_aware_obj(model, obj_path, include_vertex_colors=include_colors, textures=textures)
                                zf.write(obj_path, arcname=f"{base_name}.obj")
                                mtl_path = os.path.join(asset_export_dir, f"{base_name}.mtl")
                                if os.path.exists(mtl_path):
                                    zf.write(mtl_path, arcname=f"{base_name}.mtl")
                            else:
                                glb_path = os.path.join(asset_export_dir, f"{base_name}.glb")
                                export_xbox_aware_glb(model, glb_path, export_t_pose=tpose_only,
                                           include_vertex_colors=include_colors,
                                           textures=textures,
                                           include_armature=(CURRENT_STATE.get("model_type") != "room"))
                                zf.write(glb_path, arcname=f"{base_name}.glb")

                        # Add all texture PNGs
                        for t_name, t_path, idx in saved_tex:
                            zf.write(t_path, arcname=(t_name if fmt == 'obj_zip' and
                                getattr(model, 'xbox_native_mpx', False) else f"textures/{t_name}"))

                    with open(zip_path, 'rb') as f:
                        file_bytes = f.read()

                    self.send_response(200)
                    self.send_header('Content-Type', 'application/zip')
                    self.send_header('Content-Disposition', f'attachment; filename="{zip_filename}"')
                    self.send_header('Content-Length', str(len(file_bytes)))
                    self.end_headers()
                    self.wfile.write(file_bytes)
                    return

                if fmt in ('glb', 'gltf'):
                    export_xbox_aware_glb(model, out_path, export_t_pose=tpose_only,
                               include_vertex_colors=include_colors,
                               textures=textures,
                               include_armature=(CURRENT_STATE.get("model_type") != "room"))
                elif fmt == 'obj':
                    export_xbox_aware_obj(model, out_path,
                               include_vertex_colors=include_colors,
                               textures=textures)
                elif fmt == 'dae':
                    export_dae(model, out_path, export_t_pose=tpose_only,
                               include_vertex_colors=include_colors)
                elif fmt == 'fbx':
                    export_fbx(model, out_path, export_t_pose=tpose_only,
                               include_vertex_colors=include_colors)
                else:
                    raise ValueError(f"Unsupported export format: {fmt}")

                # Read the exported file and send back directly for download
                with open(out_path, 'rb') as f:
                    file_bytes = f.read()

                content_type = 'model/gltf-binary' if fmt == 'glb' else 'application/octet-stream'
                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Disposition', f'attachment; filename="{export_filename}"')
                self.send_header('Content-Length', str(len(file_bytes)))
                self.end_headers()
                self.wfile.write(file_bytes)
                return

            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"error": f"Export failed: {str(e)}"}).encode('utf-8'))
                return

        elif parsed.path == '/api/batch_export':
            req = json.loads(post_data.decode('utf-8')) if post_data else {}
            source_dir = req.get('source_dir', '').strip()
            output_dir = (req.get('output_dir', '') or '').strip()
            fmt = req.get('format', 'glb').lower()
            game = req.get('game', '')

            if not source_dir or not os.path.isdir(source_dir):
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': f'Invalid source directory: {source_dir}'}).encode('utf-8'))
                return

            if not output_dir:
                output_dir = os.path.join(EXPORTS_DIR, 'batch')
            os.makedirs(output_dir, exist_ok=True)

            # Auto-detect game from path if not provided
            if not game:
                path_lower = source_dir.lower().replace('\\', '/')
                # The Wii release lives under "Project Zero 2 Wii", so the
                # plain ff2 patterns below would also match it. Check it first
                # because its containers are .mdlb/.pk2b, not .pk2.
                if 'wii' in path_lower:
                    game = 'ff2w'
                elif '/ff3' in path_lower or 'ff3 modding' in path_lower or '/zero3' in path_lower:
                    game = 'ff3'
                elif '/ff2' in path_lower or 'fatal frame 2' in path_lower or '/zero2' in path_lower:
                    game = 'ff2'
                elif '/ff1' in path_lower or 'obscura' in path_lower or '/zero1' in path_lower:
                    game = 'ff1'

            job_id = uuid.uuid4().hex[:8]
            BATCH_JOBS[job_id] = {
                'status': 'running',
                'total': 0,
                'done': 0,
                'failed': 0,
                'skipped': 0,
                'current': 'Scanning files...',
                'errors': [],
                'log': [],
                'output_dir': output_dir.replace('\\', '/'),
                'started': time.time(),
            }

            options = {
                'recursive': req.get('recursive', True),
                'include_textures': req.get('include_textures', True),
                'include_colors': req.get('include_colors', True),
                'tpose_only': req.get('tpose_only', True),
                'game': game,
                'conflict': req.get('conflict', 'skip'),
                'mirror_structure': req.get('mirror_structure', True),
            }

            t = threading.Thread(
                target=batch_export_worker,
                args=(job_id, source_dir, output_dir, fmt, options),
                daemon=True
            )
            t.start()

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'job_id': job_id, 'output_dir': output_dir.replace('\\', '/')}).encode('utf-8'))
            return

        elif parsed.path == '/api/batch_cancel':
            req = json.loads(post_data.decode('utf-8')) if post_data else {}
            job_id = req.get('job_id', '')
            if job_id in BATCH_JOBS:
                BATCH_JOBS[job_id]['cancelled'] = True
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'status': 'cancel_requested'}).encode('utf-8'))
            return

        self.send_error(404)

def run_server(port=8088):
    server = HTTPServer(('127.0.0.1', port), PZViewerHandler)
    print(f"PZ Viewer Server running on http://127.0.0.1:{port}")
    server.serve_forever()

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8088
    run_server(port)
