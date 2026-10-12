"""End-to-end model of the HPDP data chain (SPEC.md 3.1).

satellite(): raw scenes -> correction -> CCSDS 123 -> packets -> AOS frames ->
             RS(255,223) I=4 -> randomizer + ASM -> CADU byte stream
ground():    CADU byte stream -> frame sync -> derandomize -> RS decode ->
             deframe -> reassemble scenes -> decompress
ground_records(): the same from derandomized codeblocks, e.g. the records
             captured by the hardware ground receiver
"""

from dataclasses import dataclass, field

from . import ccsds123, channel, framing, radiometric, rs


@dataclass
class LinkConfig:
    scid: int = 0x42
    vcid: int = 1
    apid: int = 0x100
    packet_data: int = 2048
    idle_frames_between_scenes: int = 0


@dataclass
class GroundStats:
    cadus: int = 0
    rs_corrected_symbols: int = 0
    rs_uncorrectable: int = 0
    lost_frames: int = 0
    idle_frames: int = 0
    foreign_frames: int = 0                         # decoded, but not this spacecraft's AOS frames
    scenes: list = field(default_factory=list)      # per scene: (scene_id, ok, reason)


def _cadu(frame):
    return channel.make_cadu(rs.encode_interleaved(frame, framing.RS_DEPTH))


def satellite(raw_scenes, cal, cfg=None, link=None, first_scene_id=0, timestamps=None):
    """Returns (CADU stream bytes, corrected scenes = what the ground must recover,
    index of the first CADU of each scene)."""
    cfg = cfg or ccsds123.Config()
    link = link or LinkConfig()
    framer = framing.AosFramer(link.scid, link.vcid)
    stream, corrected, starts, seq = bytearray(), [], [], 0
    for i, raw in enumerate(raw_scenes):
        starts.append(len(stream) // channel.CADU_BYTES)
        scene, _ = radiometric.correct(raw, cal) if cal is not None else (raw, None)
        corrected.append(scene)
        payload = ccsds123.compress(scene, cfg)
        ts = timestamps[i] if timestamps else i * scene.ny
        packets, seq = framing.packetize_scene(payload, link.apid, first_scene_id + i, ts,
                                               seq_count=seq, max_data=link.packet_data)
        for p in packets:
            framer.push(p)
        for frame in framer.frames(flush=True):
            stream += _cadu(frame)
        for _ in range(link.idle_frames_between_scenes):
            stream += _cadu(framer.idle())
    return bytes(stream), corrected, starts


def ground(stream, link=None):
    """Returns (list of (scene_id, Scene or None)), GroundStats)."""
    return ground_records([rec.codeblock for rec in channel.frame_sync(stream)], link)


def ground_records(codeblocks, link=None):
    """Ground chain from derandomized 1020-byte codeblocks, in reception order.
    Returns (list of (scene_id, Scene or None)), GroundStats)."""
    link = link or LinkConfig()
    stats = GroundStats()
    deframer = framing.AosDeframer(link.scid, link.vcid)
    packets = []
    for block in codeblocks:
        stats.cadus += 1
        frame, counts = rs.decode_interleaved(block, framing.RS_DEPTH)
        if frame is None:
            stats.rs_uncorrectable += 1
            continue        # the deframer sees the frame-count gap and resynchronizes
        stats.rs_corrected_symbols += sum(counts)
        try:
            packets += deframer.feed(frame)
        except ValueError:
            stats.foreign_frames += 1
    stats.lost_frames = deframer.lost_frames
    stats.idle_frames = deframer.idle_frames
    results = []
    prev_id = None
    for sc in framing.reassemble_scenes(packets):
        # scenes whose packets were all lost show up as a gap in the scene IDs
        if prev_id is not None:
            for sid in range(prev_id + 1, sc["scene_id"]):
                stats.scenes.append((sid, False, "missing"))
                results.append((sid, None))
        prev_id = sc["scene_id"]
        if not sc["complete"]:
            stats.scenes.append((sc["scene_id"], False, "incomplete"))
            results.append((sc["scene_id"], None))
            continue
        try:
            results.append((sc["scene_id"], ccsds123.decompress(sc["payload"])))
            stats.scenes.append((sc["scene_id"], True, ""))
        except Exception as exc:        # corrupted payload that slipped through
            stats.scenes.append((sc["scene_id"], False, f"decode error: {exc}"))
            results.append((sc["scene_id"], None))
    return results, stats
