"""CCSDS Space Packets (133.0-B) and AOS transfer frames (732.0-B) as used by
the HPDP (SPEC.md 6.5 and 6.6)."""

import struct
from dataclasses import dataclass

# Space packets -------------------------------------------------------------
PRIMARY_HEADER = 6
SECONDARY_HEADER = 8
IDLE_APID = 0x7FF
IDLE_FILL = 0x55
SEQ_CONT, SEQ_FIRST, SEQ_LAST, SEQ_UNSEG = 0b00, 0b01, 0b10, 0b11
FLAG_INTERNAL_ERROR, FLAG_LINES_DROPPED, FLAG_RAW = 0x01, 0x02, 0x04

# AOS frames -----------------------------------------------------------------
RS_DEPTH = 4
FRAME_BYTES = 223 * RS_DEPTH            # 892: one frame per RS codeblock
FRAME_HEADER = 6
MPDU_HEADER = 2
PACKET_ZONE = FRAME_BYTES - FRAME_HEADER - MPDU_HEADER   # 884
IDLE_VCID = 63
FHP_NONE = 0x7FF


@dataclass
class Packet:
    apid: int
    seq_flags: int
    seq_count: int
    data: bytes                 # packet data field (secondary header + user data)
    secondary: bool = True

    def to_bytes(self):
        w0 = (0 << 13) | (0 << 12) | (int(self.secondary) << 11) | self.apid
        w1 = (self.seq_flags << 14) | (self.seq_count & 0x3FFF)
        return struct.pack(">HHH", w0, w1, len(self.data) - 1) + self.data

    @classmethod
    def from_bytes(cls, raw):
        w0, w1, length = struct.unpack_from(">HHH", raw)
        if w0 >> 13:
            raise ValueError("unsupported packet version")
        data = bytes(raw[PRIMARY_HEADER:PRIMARY_HEADER + length + 1])
        return cls(w0 & 0x7FF, w1 >> 14, w1 & 0x3FFF, data, bool(w0 & 0x800))


@dataclass
class SceneHeader:
    """Secondary header of a science packet (SPEC.md 6.5)."""

    scene_id: int
    packet_index: int
    timestamp: int      # 24-bit line timestamp of the scene start
    flags: int = 0

    def to_bytes(self):
        return struct.pack(">HH", self.scene_id & 0xFFFF, self.packet_index & 0xFFFF) + \
            (self.timestamp & 0xFFFFFF).to_bytes(3, "big") + bytes([self.flags & 0xFF])

    @classmethod
    def from_bytes(cls, raw):
        sid, idx = struct.unpack_from(">HH", raw)
        return cls(sid, idx, int.from_bytes(raw[4:7], "big"), raw[7])


def idle_packet(total_length, seq_count=0):
    """Idle packet of exactly total_length bytes (>= 7)."""
    if total_length < PRIMARY_HEADER + 1:
        raise ValueError("an idle packet needs at least 7 bytes")
    return Packet(IDLE_APID, SEQ_UNSEG, seq_count, bytes([IDLE_FILL]) * (total_length - PRIMARY_HEADER),
                  secondary=False).to_bytes()


def packetize_scene(payload, apid, scene_id, timestamp, flags=0, seq_count=0, max_data=2048):
    """Split one scene's compressed bitstream into science packets.

    Returns (list of packet bytes, next sequence count)."""
    chunks = [payload[i:i + max_data] for i in range(0, len(payload), max_data)] or [b""]
    out = []
    for i, chunk in enumerate(chunks):
        if len(chunks) == 1:
            sf = SEQ_UNSEG
        elif i == 0:
            sf = SEQ_FIRST
        elif i == len(chunks) - 1:
            sf = SEQ_LAST
        else:
            sf = SEQ_CONT
        sec = SceneHeader(scene_id, i, timestamp, flags).to_bytes()
        out.append(Packet(apid, sf, seq_count, sec + chunk).to_bytes())
        seq_count = (seq_count + 1) & 0x3FFF
    return out, seq_count


# AOS framer ------------------------------------------------------------------
def frame_header(scid, vcid, vc_count):
    w0 = (0b01 << 14) | ((scid & 0xFF) << 6) | (vcid & 0x3F)
    return struct.pack(">H", w0) + (vc_count & 0xFFFFFF).to_bytes(3, "big") + b"\x00"


def idle_frame(scid, vc_count):
    return frame_header(scid, IDLE_VCID, vc_count) + bytes([IDLE_FILL]) * (FRAME_BYTES - FRAME_HEADER)


class AosFramer:
    """Packs packets into fixed-length AOS frames on one virtual channel (M_PDU)."""

    def __init__(self, scid, vcid=1):
        self.scid, self.vcid = scid, vcid
        self.vc_count = 0
        self.idle_count = 0
        self.buf = bytearray()
        self.starts = []          # offsets in buf where packets begin

    def push(self, packet_bytes):
        self.starts.append(len(self.buf))
        self.buf += packet_bytes

    def _emit(self):
        zone = bytes(self.buf[:PACKET_ZONE])
        fhp = next((s for s in self.starts if s < PACKET_ZONE), FHP_NONE)
        del self.buf[:PACKET_ZONE]
        self.starts = [s - PACKET_ZONE for s in self.starts if s >= PACKET_ZONE]
        frame = frame_header(self.scid, self.vcid, self.vc_count) + struct.pack(">H", fhp) + zone
        self.vc_count = (self.vc_count + 1) & 0xFFFFFF
        return frame

    def frames(self, flush=False):
        """Emit every complete frame; with flush, pad the last one with an idle packet."""
        out = []
        while len(self.buf) >= PACKET_ZONE:
            out.append(self._emit())
        if flush and self.buf:
            space = PACKET_ZONE - len(self.buf)
            # an idle packet needs >= 7 bytes; if less room is left it spills into one more frame
            self.push(idle_packet(space if space >= 7 else space + PACKET_ZONE))
            while self.buf:
                out.append(self._emit())
        return out

    def idle(self):
        frame = idle_frame(self.scid, self.idle_count)
        self.idle_count = (self.idle_count + 1) & 0xFFFFFF
        return frame


class AosDeframer:
    """Extracts packets from AOS frames; resynchronizes on the first header pointer after a gap."""

    def __init__(self, scid, vcid=1):
        self.scid, self.vcid = scid, vcid
        self.expected = None
        self.buf = bytearray()
        self.synced = False
        self.lost_frames = 0
        self.idle_frames = 0

    def feed(self, frame):
        """Feed one frame; returns the list of complete non-idle Packets."""
        if len(frame) != FRAME_BYTES:
            raise ValueError("wrong frame length")
        w0, = struct.unpack_from(">H", frame)
        if w0 >> 14 != 0b01 or ((w0 >> 6) & 0xFF) != self.scid:
            raise ValueError("not an AOS frame of this spacecraft")
        vcid = w0 & 0x3F
        if vcid == IDLE_VCID:
            self.idle_frames += 1
            return []
        if vcid != self.vcid:
            return []
        count = int.from_bytes(frame[2:5], "big")
        if self.expected is not None and count != self.expected:
            self.lost_frames += (count - self.expected) & 0xFFFFFF
            self.buf.clear()
            self.synced = False
        self.expected = (count + 1) & 0xFFFFFF
        fhp = struct.unpack_from(">H", frame, FRAME_HEADER)[0] & 0x7FF
        zone = frame[FRAME_HEADER + MPDU_HEADER:]
        if not self.synced:
            if fhp == FHP_NONE:
                return []
            zone = zone[fhp:]
            self.synced = True
        self.buf += zone
        return self._drain()

    def _drain(self):
        out = []
        while len(self.buf) >= PRIMARY_HEADER:
            length = struct.unpack_from(">H", self.buf, 4)[0] + 1 + PRIMARY_HEADER
            if len(self.buf) < length:
                break
            pkt = Packet.from_bytes(self.buf[:length])
            del self.buf[:length]
            if pkt.apid != IDLE_APID:
                out.append(pkt)
        return out


def reassemble_scenes(packets):
    """Group science packets back into scenes.

    Returns a list of dicts: scene_id, timestamp, flags, payload, complete (no gaps)."""
    scenes, cur = [], None
    for p in packets:
        hdr = SceneHeader.from_bytes(p.data)
        chunk = p.data[SECONDARY_HEADER:]
        if p.seq_flags in (SEQ_FIRST, SEQ_UNSEG) or cur is None or hdr.scene_id != cur["scene_id"]:
            if cur is not None:
                scenes.append(cur)
            cur = {"scene_id": hdr.scene_id, "timestamp": hdr.timestamp, "flags": hdr.flags,
                   "payload": bytearray(), "next_index": 0, "complete": True,
                   "started": p.seq_flags in (SEQ_FIRST, SEQ_UNSEG)}
        if hdr.packet_index != cur["next_index"]:
            cur["complete"] = False
        cur["next_index"] = hdr.packet_index + 1
        cur["flags"] |= hdr.flags
        cur["payload"] += chunk
        if p.seq_flags in (SEQ_LAST, SEQ_UNSEG):
            cur["ended"] = True
            scenes.append(cur)
            cur = None
    if cur is not None:
        scenes.append(cur)
    out = []
    for s in scenes:
        out.append({"scene_id": s["scene_id"], "timestamp": s["timestamp"], "flags": s["flags"],
                    "payload": bytes(s["payload"]),
                    "complete": s["complete"] and s.get("started", False) and s.get("ended", False)})
    return out
