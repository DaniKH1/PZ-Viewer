"""Structural parser for Project Zero 3 BMD motion files.

The BMD format is not the MOTN format used by PZ1/older animation files.
This module intentionally exposes the verified container structure first:
header, hierarchy/channel tables, per-bone block references, and compressed
224-byte records.  The record payload is bit-packed and is kept raw until its
quantization is fully identified.
"""

from dataclasses import dataclass
import math
import os
import struct


BMD_MAGIC = b"BMD\x00"
BMD_RECORD_SIZE = 224


@dataclass
class BMDHeader:
    frame_count: int
    record_count: int
    bone_count: int
    header_value: int
    flags: int
    hierarchy_offset: int = 0x0A
    channels_offset: int = 0x23
    track_table_offset: int = 0x54


@dataclass
class BMDTrack:
    bone: int
    start: int
    end: int
    record_start: int
    record_end: int


@dataclass
class BMDFile:
    header: BMDHeader
    parents: list
    translation_channels: list
    tracks: list
    record_offset: int
    records: list
    base_rst: list
    packet_offsets: list
    packet_values: list


class BMDMotionClip:
    def __init__(self, name, bmd):
        self.name = name
        self.bone_num = bmd.header.bone_count
        self.frame_num = bmd.header.frame_count
        self.fps = 30.0
        self.parent_ids = bmd.parents
        self.trans_ids = bmd.translation_channels
        self.frames = _experimental_frames(bmd)


def _experimental_frames(bmd):
    """Expand the currently understood packet shape into viewer keyframes.

    This is deliberately marked experimental: packet groups are known to be
    seven scalar channels, but their final semantic labels are not proven.
    The first three are treated as Euler rotation and the next three as
    translation; scale remains the bind-pose scale.
    """
    if not bmd.base_rst:
        return []
    keyframes = []
    for packet in bmd.packet_values:
        frame = [
            {
                "rot": list(pose["rot"]),
                "scale": list(pose["scale"]),
                "trans": list(pose["trans"]),
            }
            for pose in bmd.base_rst
        ]
        for group in range(min(8, bmd.header.bone_count)):
            values = packet[group * 7:(group + 1) * 7]
            if len(values) < 6:
                continue
            if all(math.isfinite(value) for value in values[:6]):
                frame[group]["rot"] = list(values[:3])
                frame[group]["trans"] = list(values[3:6])
        keyframes.append(frame)
    if not keyframes:
        return [list(bmd.base_rst)]
    frames = []
    for index in range(bmd.header.frame_count):
        source = min(len(keyframes) - 1, index // 2)
        frames.append(keyframes[source])
    return frames


def _read_data(data_or_path):
    if isinstance(data_or_path, (str, os.PathLike)):
        with open(data_or_path, "rb") as stream:
            return stream.read()
    return bytes(data_or_path)


def parse_bmd(data_or_path, name="motion"):
    """Parse the verified BMD container and return a :class:`BMDFile`.

    BMD records are returned as bytes in ``records``.  They are compressed
    transform records, not IEEE-float RST frames; treating them as floats
    produces the severe animation corruption this parser is intended to avoid.
    """
    data = _read_data(data_or_path)
    if len(data) < 0x54 or data[:4] != BMD_MAGIC:
        return None

    frame_count, record_count, bone_count, header_value = struct.unpack_from(
        "<HHHH", data, 4
    )
    if bone_count <= 0 or bone_count > 128:
        return None
    record_offset = len(data) - record_count * BMD_RECORD_SIZE
    if record_count <= 0 or record_offset < 0x54:
        return None

    parents = list(data[0x0A:0x0A + bone_count])
    if len(parents) != bone_count:
        return None

    channels = []
    for index in range(bone_count):
        offset = 0x23 + index * 2
        if offset + 2 > len(data):
            return None
        channels.append(struct.unpack_from("<H", data, offset)[0])

    tracks = []
    for bone in range(bone_count):
        offset = 0x54 + bone * 8
        if offset + 8 > record_offset:
            tracks.append(BMDTrack(bone, 0, 0, 0, 0))
            continue
        start, end = struct.unpack_from("<II", data, offset)
        if start > end or start >= len(data) or end > len(data):
            # Short clips omit the latter track references and the remaining
            # bytes are already the first compressed records.
            tracks.append(BMDTrack(bone, 0, 0, 0, 0))
            continue
        tracks.append(BMDTrack(
            bone=bone,
            start=start,
            end=end,
            record_start=max(0, (start - record_offset) // BMD_RECORD_SIZE),
            record_end=max(0, (end - record_offset) // BMD_RECORD_SIZE),
        ))

    records = [
        data[offset:offset + BMD_RECORD_SIZE]
        for offset in range(record_offset, len(data), BMD_RECORD_SIZE)
    ]
    if len(records) != record_count:
        return None

    header = BMDHeader(
        frame_count=frame_count,
        record_count=record_count,
        bone_count=bone_count,
        header_value=header_value,
        flags=struct.unpack_from("<H", data, 0x0E)[0],
    )
    # The first complete pose is stored as 25 * 9 IEEE754 floats.  The
    # referenced packet payload is also IEEE754 data: each 224-byte packet
    # contains 56 float32 values.  The apparent 112-value half-float pattern
    # comes from inspecting the packet as 16-bit words and is not a valid
    # transform quantization.
    base_rst = []
    base_size = bone_count * 9 * 4
    if record_offset + base_size <= len(data):
        values = struct.unpack_from(f"<{bone_count * 9}f", data, record_offset)
        base_rst = [
            {
                "rot": list(values[index:index + 3]),
                "scale": list(values[index + 3:index + 6]),
                "trans": list(values[index + 6:index + 9]),
            }
            for index in range(0, len(values), 9)
        ]

    packet_offsets = []
    packet_values = []
    # 0x260 is the packet address table for this family of BMD files.  Stop
    # at the first non-address word instead of interpreting compressed data as
    # pointers.
    for offset in range(0x260, record_offset, 4):
        pointer = struct.unpack_from("<I", data, offset)[0]
        if pointer < record_offset or pointer + BMD_RECORD_SIZE > len(data):
            break
        packet_offsets.append(pointer)
        packet_values.append(list(struct.unpack_from("<56f", data, pointer)))

    return BMDFile(
        header=header,
        parents=parents,
        translation_channels=channels,
        tracks=tracks,
        record_offset=record_offset,
        records=records,
        base_rst=base_rst,
        packet_offsets=packet_offsets,
        packet_values=packet_values,
    )


def parse_bmd_motion(data_or_path, name="bmd_motion"):
    bmd = parse_bmd(data_or_path, name=name)
    return BMDMotionClip(name, bmd) if bmd else None
