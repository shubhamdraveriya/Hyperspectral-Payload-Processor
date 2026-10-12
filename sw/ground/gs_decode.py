"""Ground station decoding (SPEC.md 9.2, GS-02 .. GS-04): capture records ->
RS decoding -> AOS frame checks -> packets -> scenes -> decompression.

GroundDecoder is incremental: feed() takes one capture record at a time, from a
.cap file or a live TCP stream, and returns the scenes it completed. The
decoding itself is the Python model's (model/hpdp_model): rs.decode_interleaved
(with the dual-basis conversion), framing.AosDeframer (frame header checks, VC
frame counts, first header pointer), the scene header of the packets, and
ccsds123.decompress.
"""

import struct
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "model"))

from hpdp_model import ccsds123, framing, rs  # noqa: E402
from hpdp_model import formats as f  # noqa: E402

RECORD_BYTES = f.CAPTURE_RECORD_BYTES
SEQ_MOD = 1 << 16                     # capture record sequence numbers
PKT_SEQ_MOD = 1 << 14                 # packet sequence counts
FLAG_ERROR, FLAG_DROPPED, FLAG_RAW = framing.FLAG_INTERNAL_ERROR, framing.FLAG_LINES_DROPPED, framing.FLAG_RAW
MAX_GAP = 64                          # scene-ID gaps reported as missing scenes


@dataclass
class SceneResult:
    """One scene as received. scene is a formats.Scene, or None if it could not be
    reconstructed (reason says why)."""
    scene_id: int
    timestamp: int = 0
    flags: int = 0
    payload: bytes = b""
    complete: bool = False
    scene: object = None
    reason: str = ""
    packets: int = 0
    frames: int = 0                   # science frames that carried it
    rs_corrected: int = 0             # symbols corrected in those frames
    rs_uncorrectable: int = 0         # codeblocks lost while it was being received
    lost_frames: int = 0              # VC frame count gaps while it was being received
    asm_errors: int = 0
    flywheel: int = 0                 # records delivered in FLYWHEEL
    decode_seconds: float = 0.0

    @property
    def ok(self):
        return self.scene is not None

    @property
    def bits_per_sample(self):
        if self.scene is None or not self.payload:
            return None
        s = self.scene
        return 8 * len(self.payload) / (s.nx * s.ny * s.nz)

    @property
    def ratio(self):
        bps = self.bits_per_sample
        return self.scene.depth / bps if bps else None

    def flag_text(self):
        names = [n for bit, n in ((FLAG_ERROR, "internal error"), (FLAG_DROPPED, "lines dropped"),
                                  (FLAG_RAW, "RAW")) if self.flags & bit]
        return ", ".join(names) or "-"


@dataclass
class LinkStats:
    records: int = 0
    sync: dict = field(default_factory=lambda: {s: 0 for s in f.SYNC_STATES})
    asm_errors: int = 0
    seq_gaps: int = 0                 # record sequence numbers not captured (idle frames skipped, drops)
    rs_corrected: int = 0
    rs_uncorrectable: int = 0
    science_frames: int = 0
    idle_frames: int = 0
    other_vc_frames: int = 0
    foreign_frames: int = 0           # decoded, but not this spacecraft's AOS frames
    lost_frames: int = 0              # VC frame count gaps
    packets: int = 0
    other_apid_packets: int = 0
    packet_errors: int = 0            # unreadable packet headers (resynchronized)
    pkt_seq_gaps: int = 0             # science packets missing by sequence count
    scenes_ok: int = 0
    scenes_failed: int = 0
    rs_crosscheck_mismatches: int = 0
    rs_crosschecked: int = 0


class _Pending:
    """Frame statistics waiting to be attributed to the scene being received."""

    def __init__(self):
        self.frames = self.rs_corrected = self.rs_uncorrectable = self.lost_frames = 0
        self.asm_errors = self.flywheel = 0


class GroundDecoder:
    def __init__(self, scid=0x42, vcid=1, apid=0x100, decompress=True, crosscheck=False):
        self.scid, self.vcid, self.apid = scid, vcid, apid
        self.decompress = decompress
        self.stats = LinkStats()
        self.deframer = framing.AosDeframer(scid, vcid)
        self._prev_seq = None
        self._prev_pkt_seq = None
        self._pending = _Pending()
        self._cur = None              # scene being assembled
        self._last_id = None          # last scene ID emitted
        self._codec = _crosscheck_codec() if crosscheck else None

    # ------------------------------------------------------------------
    def feed(self, record):
        """record: 1024 bytes (codeblock + status word). Returns completed SceneResults."""
        st = self.stats
        block, (status,) = record[:f.CODEBLOCK_BYTES], struct.unpack_from("<I", record, f.CODEBLOCK_BYTES)
        asm, state, seq = status & 0x3F, f.SYNC_STATES[(status >> 6) & 3], status >> 16
        st.records += 1
        st.sync[state] += 1
        st.asm_errors += asm
        if self._prev_seq is not None:
            st.seq_gaps += (seq - self._prev_seq - 1) % SEQ_MOD
        self._prev_seq = seq

        frame, counts = rs.decode_interleaved(block, framing.RS_DEPTH)
        if self._codec is not None:
            self._crosscheck(block, frame)
        if frame is None:
            st.rs_uncorrectable += 1
            self._pending.rs_uncorrectable += 1
            self._pending.asm_errors += asm
            return []
        corrected = sum(counts)
        st.rs_corrected += corrected

        w0, = struct.unpack_from(">H", frame)
        vcid = w0 & 0x3F
        if w0 >> 14 != 0b01 or ((w0 >> 6) & 0xFF) != self.scid:
            st.foreign_frames += 1
            return []
        if vcid == framing.IDLE_VCID:
            st.idle_frames += 1
            return []
        if vcid != self.vcid:
            st.other_vc_frames += 1
            return []
        st.science_frames += 1
        p = self._pending
        p.frames += 1
        p.rs_corrected += corrected
        p.asm_errors += asm
        p.flywheel += state == "FLYWHEEL"
        lost_before = self.deframer.lost_frames
        try:
            packets = self.deframer.feed(frame)
        except ValueError:
            # a packet header the RS decoder did not catch: drop the partial
            # packet and resynchronize on the next frame's first header pointer
            st.packet_errors += 1
            self.deframer.buf.clear()
            self.deframer.synced = False
            packets = []
        lost = self.deframer.lost_frames - lost_before
        st.lost_frames += lost
        p.lost_frames += lost

        out = []
        for pkt in packets:
            out += self._packet(pkt)
        return out

    def flush(self):
        """End of input: the scene still being assembled (incomplete)."""
        if self._cur is None:
            return []
        return self._close(self._cur)

    # ------------------------------------------------------------------
    def _packet(self, pkt):
        st = self.stats
        if pkt.apid != self.apid:
            st.other_apid_packets += 1
            return []
        st.packets += 1
        if self._prev_pkt_seq is not None:
            st.pkt_seq_gaps += (pkt.seq_count - self._prev_pkt_seq - 1) % PKT_SEQ_MOD
        self._prev_pkt_seq = pkt.seq_count
        hdr = framing.SceneHeader.from_bytes(pkt.data)
        first = pkt.seq_flags in (framing.SEQ_FIRST, framing.SEQ_UNSEG)
        last = pkt.seq_flags in (framing.SEQ_LAST, framing.SEQ_UNSEG)

        out = []
        cur = self._cur
        if cur is not None and (first or hdr.scene_id != cur["id"]):
            out += self._close(cur)
            cur = None
        if cur is None:
            cur = self._cur = {"id": hdr.scene_id, "ts": hdr.timestamp, "flags": 0, "payload": bytearray(),
                               "next": 0, "good": first, "packets": 0}
        if hdr.packet_index != cur["next"]:
            cur["good"] = False
        cur["next"] = hdr.packet_index + 1
        cur["flags"] |= hdr.flags
        cur["packets"] += 1
        cur["payload"] += pkt.data[framing.SECONDARY_HEADER:]
        if last:
            cur["ended"] = True
            out += self._close(cur)
        return out

    def _close(self, cur):
        self._cur = None
        out = []
        # scenes whose packets were all lost show up as a gap in the scene IDs
        # (a jump of more than MAX_GAP is a new run or a corrupted ID: not filled in)
        if self._last_id is not None and 0 < cur["id"] - self._last_id - 1 <= MAX_GAP:
            for sid in range(self._last_id + 1, cur["id"]):
                out.append(SceneResult(sid, reason="missing (no packets received)"))
                self.stats.scenes_failed += 1
        self._last_id = cur["id"]

        p, self._pending = self._pending, _Pending()
        r = SceneResult(cur["id"], cur["ts"], cur["flags"], bytes(cur["payload"]),
                        cur["good"] and cur.get("ended", False), packets=cur["packets"],
                        frames=p.frames, rs_corrected=p.rs_corrected, rs_uncorrectable=p.rs_uncorrectable,
                        lost_frames=p.lost_frames, asm_errors=p.asm_errors, flywheel=p.flywheel)
        if not r.complete:
            r.reason = "incomplete (packets missing)"
        elif r.flags & FLAG_RAW:
            r.reason = "RAW mode: not CCSDS 123 compressed"
        elif self.decompress:
            t = time.perf_counter()
            try:
                r.scene = ccsds123.decompress(r.payload)
            except Exception as exc:        # a corrupted payload that slipped through
                r.reason = f"decode error: {exc}"
            r.decode_seconds = time.perf_counter() - t
        if r.scene is not None:
            self.stats.scenes_ok += 1
        elif self.decompress or not r.complete:
            self.stats.scenes_failed += 1
        out.append(r)
        return out

    # ------------------------------------------------------------------
    def _crosscheck(self, block, frame):
        """GS-02: decode every codeword again with reedsolo (an independent
        implementation, conventional basis) and compare."""
        import reedsolo
        st = self.stats
        st.rs_crosschecked += 1
        ref = bytearray(framing.FRAME_BYTES)
        ok = True
        for j in range(framing.RS_DEPTH):
            conv = bytearray(rs.FROM_DUAL[b] for b in block[j::framing.RS_DEPTH])
            try:
                data = self._codec.decode(conv)[0]
                ref[j::framing.RS_DEPTH] = bytes(rs.TO_DUAL[c] for c in data)
            except reedsolo.ReedSolomonError:
                ok = False
        if (frame is None) != (not ok) or (ok and frame is not None and bytes(ref) != frame):
            st.rs_crosscheck_mismatches += 1


def _crosscheck_codec():
    import reedsolo
    return reedsolo.RSCodec(nsym=32, nsize=255, fcr=112, prim=0x187, generator=rs.EXP[11], c_exp=8)


# ---------------------------------------------------------------------------
# Record sources
# ---------------------------------------------------------------------------
def capture_records(path):
    """Records of a .cap file (SPEC.md 7)."""
    data = Path(path).read_bytes()
    if len(data) % RECORD_BYTES:
        raise f.FormatError(f"{path}: {len(data)} bytes is not a multiple of {RECORD_BYTES}")
    for off in range(0, len(data), RECORD_BYTES):
        yield data[off:off + RECORD_BYTES]


def tcp_records(host, port, timeout=None):
    """Records from a TCP stream of back-to-back 1024-byte capture records (the
    board's ground upload, SW-05); ends when the sender closes the connection."""
    import socket
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.settimeout(None)
        buf = bytearray()
        while True:
            chunk = s.recv(1 << 16)
            if not chunk:
                break
            buf += chunk
            n = len(buf) // RECORD_BYTES * RECORD_BYTES
            for off in range(0, n, RECORD_BYTES):
                yield bytes(buf[off:off + RECORD_BYTES])
            del buf[:n]
