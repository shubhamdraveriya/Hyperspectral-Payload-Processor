import struct

from hpdp_model import framing as fr


def test_packet_header_layout():
    p = fr.Packet(apid=0x123, seq_flags=fr.SEQ_FIRST, seq_count=0x2ABC, data=b"\x01\x02\x03")
    raw = p.to_bytes()
    w0, w1, length = struct.unpack(">HHH", raw[:6])
    assert w0 == (1 << 11) | 0x123           # version 0, telemetry, secondary header, APID
    assert w1 == (0b01 << 14) | 0x2ABC
    assert length == 2 and raw[6:] == b"\x01\x02\x03"
    assert fr.Packet.from_bytes(raw) == p


def test_scene_header_round_trip():
    h = fr.SceneHeader(0x1234, 7, 0xABCDEF, fr.FLAG_RAW)
    raw = h.to_bytes()
    assert len(raw) == fr.SECONDARY_HEADER
    assert fr.SceneHeader.from_bytes(raw) == h


def test_packetize_sequence_flags():
    pkts, nxt = fr.packetize_scene(bytes(5000), apid=0x100, scene_id=3, timestamp=99, seq_count=16382)
    parsed = [fr.Packet.from_bytes(p) for p in pkts]
    assert [p.seq_flags for p in parsed] == [fr.SEQ_FIRST, fr.SEQ_CONT, fr.SEQ_LAST]
    assert [p.seq_count for p in parsed] == [16382, 16383, 0] and nxt == 1
    assert [len(p.data) - fr.SECONDARY_HEADER for p in parsed] == [2048, 2048, 904]
    single, _ = fr.packetize_scene(b"abc", 0x100, 0, 0)
    assert fr.Packet.from_bytes(single[0]).seq_flags == fr.SEQ_UNSEG


def test_idle_packet():
    raw = fr.idle_packet(20)
    p = fr.Packet.from_bytes(raw)
    assert len(raw) == 20 and p.apid == fr.IDLE_APID and not p.secondary


def frames_for(packet_sizes, flush=True):
    framer = fr.AosFramer(scid=0x42)
    pkts = [fr.Packet(0x100, fr.SEQ_UNSEG, i, bytes([i]) * (n - 6)).to_bytes()
            for i, n in enumerate(packet_sizes)]
    for p in pkts:
        framer.push(p)
    return pkts, framer.frames(flush=flush)


def fhp(frame):
    return struct.unpack_from(">H", frame, fr.FRAME_HEADER)[0] & 0x7FF


def test_frame_layout_and_first_header_pointer():
    pkts, frames = frames_for([500, 500, 2000, 100])
    assert all(len(f) == fr.FRAME_BYTES for f in frames)
    w0 = struct.unpack_from(">H", frames[0])[0]
    assert w0 >> 14 == 0b01 and (w0 >> 6) & 0xFF == 0x42 and w0 & 0x3F == 1
    assert [int.from_bytes(f[2:5], "big") for f in frames] == list(range(len(frames)))
    assert fhp(frames[0]) == 0
    assert fhp(frames[1]) == 1000 - fr.PACKET_ZONE       # third packet starts in frame 1
    assert fhp(frames[2]) == fr.FHP_NONE                 # frame 2 is inside the 2000-byte packet


def test_flush_with_too_little_room_spills_an_idle_packet():
    # leave 3 bytes in the first frame: the idle packet needs a second frame
    _, frames = frames_for([fr.PACKET_ZONE - 3])
    assert len(frames) == 2
    assert fhp(frames[1]) == fr.FHP_NONE


def test_deframer_recovers_packets_and_drops_idle():
    pkts, frames = frames_for([300, 1200, 40, 900, 2500])
    framer = fr.AosFramer(0x42)
    frames.insert(1, framer.idle())
    d = fr.AosDeframer(0x42)
    got = [p.to_bytes() for f in frames for p in d.feed(f)]
    assert got == pkts
    assert d.idle_frames == 1 and d.lost_frames == 0


def test_deframer_resynchronizes_after_a_lost_frame():
    pkts, frames = frames_for([400] * 10)
    d = fr.AosDeframer(0x42)
    got = [p.to_bytes() for i, f in enumerate(frames) if i != 1 for p in d.feed(f)]
    assert d.lost_frames == 1
    # packets wholly before the gap and those starting after the resync point survive
    assert got[:2] == pkts[:2]
    assert got[-1] == pkts[-1]
    assert all(p in pkts for p in got)


def test_reassemble_scenes():
    packets = []
    seq = 0
    for sid, size in [(0, 5000), (1, 100), (2, 3000)]:
        raw, seq = fr.packetize_scene(bytes([sid]) * size, 0x100, sid, 10 * sid, seq_count=seq)
        packets += [fr.Packet.from_bytes(p) for p in raw]
    scenes = fr.reassemble_scenes(packets)
    assert [(s["scene_id"], len(s["payload"]), s["complete"]) for s in scenes] == \
        [(0, 5000, True), (1, 100, True), (2, 3000, True)]
    del packets[1]                                   # middle packet of scene 0
    assert fr.reassemble_scenes(packets)[0]["complete"] is False
