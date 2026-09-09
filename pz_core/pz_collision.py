import struct
import math
import os

class CollisionMesh:
    def __init__(self, name="collision"):
        self.name = name
        self.positions = []   # [x, y, z]
        self.indices = []     # [v0, v1, v2]
        self.colors = []      # [r, g, b, a]
        self.normals = []     # [nx, ny, nz]
        self.uvs = []         # [u, v]
        self.type = "mesh"    # "polygon", "prism_3d", "box", "sphere"

def _append_box(mesh, minimum, maximum):
    """Append a closed AABB using the native room collision coordinates."""
    x0, y0, z0 = minimum
    x1, y1, z1 = maximum
    base = len(mesh.positions)
    mesh.positions.extend([
        [x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
        [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1],
    ])
    mesh.colors.extend([[0.1, 0.9, 0.4, 0.5]] * 8)
    mesh.indices.extend([
        [base + 0, base + 2, base + 1], [base + 0, base + 3, base + 2],
        [base + 4, base + 5, base + 6], [base + 4, base + 6, base + 7],
        [base + 0, base + 1, base + 5], [base + 0, base + 5, base + 4],
        [base + 1, base + 2, base + 6], [base + 1, base + 6, base + 5],
        [base + 2, base + 3, base + 7], [base + 2, base + 7, base + 6],
        [base + 3, base + 0, base + 4], [base + 3, base + 4, base + 7],
    ])

def parse_cld(data, name="room_cld"):
    """Parse the compact FF3 room CLD records found under ``02_cld``.

    CLD files have a 16-byte header.  The first word is the record count and
    each record is 48 bytes.  Records contain packed collision bounds plus
    plane metadata; the bounds are recovered from their finite float fields.
    """
    if len(data) < 16:
        return []
    record_count, version, _, _ = struct.unpack_from("<4I", data, 0)
    if record_count == 0 or record_count > 4096 or version > 0x100:
        return []
    if 16 + record_count * 48 > len(data):
        return []

    meshes = []
    for record_index in range(record_count):
        offset = 16 + record_index * 48
        primitive_type, primitive_flags = struct.unpack_from("<2I", data, offset)
        values = list(struct.unpack_from("<10f", data, offset + 8))
        finite_values = [
            value for value in values
            if math.isfinite(value) and abs(value) < 100000.0
        ]
        if len(finite_values) < 3:
            continue

        # Type 2 CLD records are compact primitive descriptions rather than
        # triangle lists. Their float payload combines bounds and primitive
        # parameters, so reconstruct the conservative box from all finite
        # coordinates while keeping the two integer fields available for
        # future type-specific decoding.
        points = [
            finite_values[index:index + 3]
            for index in range(0, len(finite_values) - 2, 3)
        ]
        minimum = [
            min(point[axis] for point in points if len(point) == 3)
            for axis in range(3)
        ]
        maximum = [
            max(point[axis] for point in points if len(point) == 3)
            for axis in range(3)
        ]
        if any((maximum[i] - minimum[i]) > 100000.0 for i in range(3)):
            continue
        epsilon = 0.01
        for axis in range(3):
            if maximum[axis] - minimum[axis] < epsilon:
                minimum[axis] -= epsilon
                maximum[axis] += epsilon
        mesh = CollisionMesh(f"{name}_{record_index:04d}")
        mesh.type = "box"
        _append_box(mesh, minimum, maximum)
        # Preserve the source primitive classification for callers that need
        # to distinguish future CLD shape decoders.
        mesh.primitive_types = [{
            "index": record_index,
            "type": primitive_type,
            "flags": primitive_flags,
            "minimum": minimum,
            "maximum": maximum,
            "vertex_start": 0,
        }]
        if mesh.positions:
            meshes.append(mesh)
    return meshes

def parse_cld_folder(folder, name="room_cld"):
    """Load all validated CLD files from an extracted ``02_cld`` folder."""
    if not os.path.isdir(folder):
        return []
    meshes = []
    for filename in sorted(os.listdir(folder)):
        if not filename.lower().endswith(".cld"):
            continue
        path = os.path.join(folder, filename)
        if not os.path.isfile(path):
            continue
        with open(path, "rb") as handle:
            meshes.extend(parse_cld(handle.read(), f"{name}_{os.path.splitext(filename)[0]}"))
    return meshes

def line_cross(line0, line1):
    a0, b0, c0 = line0
    a1, b1, c1 = line1
    denom = (a0 * b1) - (b0 * a1)
    if abs(denom) < 1e-4:
        return None
    x = ((b0 * c1) - (c0 * b1)) / denom
    y = ((c0 * a1) - (a0 * c1)) / denom
    return (x, y)

def parse_room_collision_from_map(map_data, room_idx=0, floor_y=0.0, wall_height=20.0, extrude_3d=True):
    if len(map_data) < 32:
        return []

    floors = struct.unpack('<8I', map_data[:32])

    # Find the floor containing room_idx
    found_floor_off = 0
    found_r_idx = -1
    room_pos_x = 0
    room_pos_z = 0
    room_pos_h = 0

    for fl_idx in range(len(floors)):
        fl_off = floors[fl_idx]
        if fl_off == 0 or fl_off + 52 > len(map_data):
            continue
        secs = struct.unpack('<13I', map_data[fl_off:fl_off+52])

        # Section 0 = MAP_ROOM_DAT
        room_dat_off = secs[0]
        if room_dat_off + 8 > len(map_data):
            continue

        rdat_tbl = struct.unpack('<32I', map_data[room_dat_off:min(len(map_data), room_dat_off+128)])
        room_ids_off = rdat_tbl[0]
        if room_ids_off >= len(map_data):
            continue
        num_rooms = map_data[room_ids_off]
        r_ids = list(map_data[room_ids_off+1 : room_ids_off+1+num_rooms])

        if room_idx in r_ids:
            found_floor_off = fl_off
            found_r_idx = r_ids.index(room_idx)
            if found_r_idx + 1 < len(rdat_tbl):
                r_entry = rdat_tbl[found_r_idx + 1]
                if r_entry + 4 <= len(map_data):
                    host1 = struct.unpack('<I', map_data[r_entry:r_entry+4])[0]
                    if host1 + 8 <= len(map_data):
                        rpos = struct.unpack('<4h', map_data[host1:host1+8])
                        room_pos_x = rpos[0]
                        room_pos_z = rpos[2]
                        room_pos_h = rpos[3]
            break

    if found_floor_off == 0:
        # Fallback to Floor 1 if available
        if len(floors) > 1 and floors[1] > 0 and floors[1] + 52 <= len(map_data):
            found_floor_off = floors[1]
        else:
            return []

    secs = struct.unpack('<13I', map_data[found_floor_off:found_floor_off+52])
    hit_check_off = secs[6] # Section 6 = HIT_CHECK
    if hit_check_off + 8 > len(map_data):
        return []

    hc_tbl = struct.unpack('<64I', map_data[hit_check_off:min(len(map_data), hit_check_off+256)])
    hc_room_ids_off = hc_tbl[0]
    if hc_room_ids_off >= len(map_data):
        return []
    hc_num_rooms = map_data[hc_room_ids_off]
    hc_r_ids = list(map_data[hc_room_ids_off+1 : hc_room_ids_off+1+hc_num_rooms])

    if room_idx not in hc_r_ids:
        if not hc_r_ids:
            return []
        room_idx = hc_r_ids[0]

    hc_target_idx = hc_r_ids.index(room_idx)
    if hc_target_idx + 1 >= len(hc_tbl):
        return []

    r_off = hc_tbl[hc_target_idx + 1]
    if r_off + 4 > len(map_data):
        return []

    data0_off = struct.unpack('<I', map_data[r_off:r_off+4])[0]
    if data0_off + 8 > len(map_data):
        return []

    sq_num = map_data[data0_off + 4]
    if sq_num == 0:
        return []

    col_mesh = CollisionMesh(f"room_collision_{room_idx}")
    col_mesh.type = "prism_3d" if extrude_3d else "polygon"

    eff_height = float(room_pos_h) / 25.0 if room_pos_h > 0 else wall_height

    vert_offset = 0
    for i in range(sq_num):
        slot_off = data0_off + 4 + (i + 1) * 4
        if slot_off + 4 > len(map_data):
            break
        area_off = struct.unpack('<I', map_data[slot_off:slot_off+4])[0]
        if area_off + 32 > len(map_data):
            continue

        lines = []
        for li in range(4):
            lo = area_off + li * 8
            a, b, c = struct.unpack('<hhi', map_data[lo:lo+8])
            lines.append((float(a), float(b), float(c)))

        pairs = [(0,1), (1,2), (2,3), (3,0)]
        pts = []
        for p0, p1 in pairs:
            pt = line_cross(lines[p0], lines[p1])
            if pt:
                pts.append(pt)

        if len(pts) >= 3:
            # Order CCW in XZ plane for consistent outward normals
            area = 0.0
            for j in range(len(pts)):
                p0 = pts[j]
                p1 = pts[(j + 1) % len(pts)]
                area += (p1[0] - p0[0]) * (p1[1] + p0[1])
            if area > 0:
                pts = list(reversed(pts))

            # Transform to room local coordinates
            room_pts_2d = []
            for pt in pts:
                lx = (pt[0] - float(room_pos_z)) / 25.0 if room_pos_z != 0 else pt[0] / 25.0
                lz = -(pt[1] - float(room_pos_x)) / 25.0 if room_pos_x != 0 else -pt[1] / 25.0
                room_pts_2d.append((lx, lz))

            k = len(room_pts_2d)
            base_v = vert_offset

            if extrude_3d:
                # 3D Solid Prism: Bottom floor face + Top ceiling face + Vertical perimeter walls
                for x, z in room_pts_2d:
                    col_mesh.positions.append([x, floor_y, z])
                    col_mesh.colors.append([0.1, 0.9, 0.4, 0.5])
                for x, z in room_pts_2d:
                    col_mesh.positions.append([x, floor_y + eff_height, z])
                    col_mesh.colors.append([0.1, 0.9, 0.4, 0.5])

                # Bottom face (facing -Y down)
                for t in range(k - 2):
                    col_mesh.indices.append([base_v, base_v + t + 1, base_v + t + 2])

                # Top face (facing +Y up)
                for t in range(k - 2):
                    col_mesh.indices.append([base_v + k, base_v + k + t + 2, base_v + k + t + 1])

                # Vertical Perimeter Walls (facing outward)
                for j in range(k):
                    nxt = (j + 1) % k
                    col_mesh.indices.append([base_v + j, base_v + k + nxt, base_v + nxt])
                    col_mesh.indices.append([base_v + j, base_v + k + j, base_v + k + nxt])

                vert_offset += 2 * k
            else:
                # 2D Flat Floor Polygon
                for x, z in room_pts_2d:
                    col_mesh.positions.append([x, floor_y, z])
                    col_mesh.colors.append([0.1, 0.9, 0.4, 0.65])

                for t in range(k - 2):
                    col_mesh.indices.append([base_v, base_v + t + 1, base_v + t + 2])

                vert_offset += k

    return [col_mesh] if col_mesh.positions else []

def parse_all_rooms_collision_from_map(map_data, wall_height=20.0, extrude_3d=True):
    """Parses all rooms collision across the entire map file."""
    if len(map_data) < 32:
        return []

    floors = struct.unpack('<8I', map_data[:32])
    all_meshes = []

    for fl_idx in range(len(floors)):
        fl_off = floors[fl_idx]
        if fl_off == 0 or fl_off + 52 > len(map_data):
            continue
        secs = struct.unpack('<13I', map_data[fl_off:fl_off+52])

        room_dat_off = secs[0]
        if room_dat_off + 8 > len(map_data):
            continue
        rdat_tbl = struct.unpack('<32I', map_data[room_dat_off:min(len(map_data), room_dat_off+128)])
        room_ids_off = rdat_tbl[0]
        if room_ids_off >= len(map_data):
            continue
        num_rooms = map_data[room_ids_off]
        r_ids = list(map_data[room_ids_off+1 : room_ids_off+1+num_rooms])

        for rid in r_ids:
            col = parse_room_collision_from_map(map_data, room_idx=rid, wall_height=wall_height, extrude_3d=extrude_3d)
            if col:
                all_meshes.extend(col)

    return all_meshes

def collision_to_sgd_model(collision_meshes, name="collision"):
    """Converts a list of CollisionMesh objects into an SGDModel suitable for GLB/OBJ export."""
    from pz_core.pz_sgd_ff1 import SGDModel, SGDMesh, SGDMaterial

    model = SGDModel(name)
    mat = SGDMaterial(0, "Collision_Material")
    mat.diffuse = [0.1, 0.9, 0.4, 0.65]
    mat.ambient = [0.1, 0.35, 0.15, 1.0]
    mat.specular = [0.2, 0.2, 0.2, 1.0]
    model.materials.append(mat)

    for i, col in enumerate(collision_meshes):
        if not col.positions or not col.indices:
            continue
        mesh = SGDMesh(getattr(col, 'name', f"collision_{i}"))
        mesh.material_index = 0
        mesh.positions = [list(p) for p in col.positions]
        mesh.indices = [list(idx) for idx in col.indices]
        mesh.colors = [list(c) for c in col.colors] if col.colors else [[0.1, 0.9, 0.4, 0.65]] * len(col.positions)

        # Compute smooth face-averaged normals
        if getattr(col, 'normals', None) and len(col.normals) == len(col.positions):
            mesh.normals = [list(n) for n in col.normals]
        else:
            norms = [[0.0, 0.0, 0.0] for _ in range(len(mesh.positions))]
            for tri in mesh.indices:
                p0 = mesh.positions[tri[0]]
                p1 = mesh.positions[tri[1]]
                p2 = mesh.positions[tri[2]]
                v1 = [p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]]
                v2 = [p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2]]
                cross = [
                    v1[1] * v2[2] - v1[2] * v2[1],
                    v1[2] * v2[0] - v1[0] * v2[2],
                    v1[0] * v2[1] - v1[1] * v2[0]
                ]
                for idx in tri:
                    norms[idx][0] += cross[0]
                    norms[idx][1] += cross[1]
                    norms[idx][2] += cross[2]
            for n in norms:
                l = math.sqrt(n[0] * n[0] + n[1] * n[1] + n[2] * n[2])
                if l > 1e-5:
                    mesh.normals.append([n[0] / l, n[1] / l, n[2] / l])
                else:
                    mesh.normals.append([0.0, 1.0, 0.0])

        # UV coordinates (dummy coordinates for exporter safety)
        mesh.uvs = [[0.0, 0.0] for _ in range(len(mesh.positions))]
        model.meshes.append(mesh)

    return model

def create_bounding_box_mesh(bb_verts, name="bbox"):
    # bb_verts has 8 [x, y, z] points
    mesh = CollisionMesh(name)
    mesh.type = "box"
    if len(bb_verts) < 8:
        return mesh

    mesh.positions = [list(v) for v in bb_verts[:8]]
    for _ in range(8):
        mesh.colors.append([1.0, 0.3, 0.2, 0.6]) # Red-orange

    # 12 triangles for 6 box faces
    box_indices = [
        0, 1, 2,  0, 2, 3, # Front
        4, 6, 5,  4, 7, 6, # Back
        0, 4, 1,  1, 4, 5, # Top
        3, 2, 7,  2, 6, 7, # Bottom
        0, 3, 4,  3, 7, 4, # Left
        1, 5, 2,  2, 5, 6  # Right
    ]
    for i in range(0, len(box_indices), 3):
        mesh.indices.append([box_indices[i], box_indices[i+1], box_indices[i+2]])

    return mesh

def create_sphere_collider_mesh(center, radius=1.8, name="sphere_col", rings=8, sectors=12):
    mesh = CollisionMesh(name)
    mesh.type = "sphere"

    cx, cy, cz = center
    R = 1.0 / (rings - 1)
    S = 1.0 / (sectors - 1)

    for r in range(rings):
        for s in range(sectors):
            y = math.sin(-math.pi / 2 + math.pi * r * R)
            x = math.cos(2 * math.pi * s * S) * math.sin(math.pi * r * R)
            z = math.sin(2 * math.pi * s * S) * math.sin(math.pi * r * R)
            mesh.positions.append([cx + x * radius, cy + y * radius, cz + z * radius])
            mesh.colors.append([0.2, 0.6, 1.0, 0.6]) # Light blue

    for r in range(rings - 1):
        for s in range(sectors - 1):
            i0 = r * sectors + s
            i1 = r * sectors + (s + 1)
            i2 = (r + 1) * sectors + (s + 1)
            i3 = (r + 1) * sectors + s
            mesh.indices.append([i0, i1, i2])
            mesh.indices.append([i0, i2, i3])

    return mesh
