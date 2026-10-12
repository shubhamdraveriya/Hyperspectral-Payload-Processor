"""Test vectors for tb_downlink (space_packetizer -> aos_framer -> rs_encoder ->
cadu_framer), from the Python model (framing.py, rs.py, channel.py), built the
way pipeline.satellite() builds its stream.

    python tb/downlink/gen_vectors.py <outdir> [test ...]

For each test, in <outdir>/<test>/:
    cfg.txt        apid pkt_data scid vcid idle_en frame_gap in_pct out_pct scenes
    payload.hex    compressed scene bytes, all scenes; lens.txt bytes per scene
    meta.hex       scene start timestamps
    packets.txt    packet stream: byte first_of_packet last_packet_of_scene last_byte
    frames.hex     science frames (892 bytes each)
    cadus.hex      science CADUs (1024 bytes each)
    idle_cadus.hex idle CADUs with idle frame counts 0, 1, 2, ...
<outdir>/tests.txt lists the tests.
"""

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "model"))

from hpdp_model import channel, framing, rs  # noqa: E402

DEFAULT = dict(apid=0x100, pkt_data=2048, scid=0x42, vcid=1, idle_en=0, frame_gap=0,
               in_pct=100, out_pct=100, lens=(5000, 2048, 37), seed=1, spill=False)

TESTS = {
    "basic":    {},
    "stalls":   dict(in_pct=40, out_pct=50),
    # many short packets; scene lengths chosen so that at least one scene leaves
    # 1..6 bytes in its last frame (the idle packet spills into another frame)
    "spill":    dict(pkt_data=100, lens=None, spill=True, seed=3),
    # scene ends exactly at the end of a frame: no idle packet
    "exact":    dict(lens=(884 * 3 - 14 - 14,), pkt_data=1312),
    "one_byte": dict(pkt_data=1, lens=(30, 1)),
    "idle":     dict(idle_en=1, out_pct=30, lens=(3000, 900)),
    "gap":      dict(idle_en=1, frame_gap=3000, lens=(4000,)),
}


def cadu(frame):
    return channel.make_cadu(rs.encode_interleaved(frame, framing.RS_DEPTH))


def model(t, payloads, stamps):
    framer = framing.AosFramer(t["scid"], t["vcid"])
    packets, frames, seq, spills = [], [], 0, 0
    for sid, (payload, ts) in enumerate(zip(payloads, stamps)):
        pk, seq = framing.packetize_scene(payload, t["apid"], sid, ts, seq_count=seq, max_data=t["pkt_data"])
        for k, p in enumerate(pk):
            framer.push(p)
            last_of_scene = int(k == len(pk) - 1)
            packets += [(b, int(i == 0), last_of_scene, int(i == len(p) - 1)) for i, b in enumerate(p)]
        left = len(framer.buf) % framing.PACKET_ZONE
        if left and framing.PACKET_ZONE - left < 7:
            spills += 1
        frames += framer.frames(flush=True)
    return packets, frames, spills


def scene_lengths(t, rng):
    if t["lens"]:
        return list(t["lens"])
    # search random lengths until a spill happens (at least once)
    while True:
        lens = [rng.randrange(1, 3000) for _ in range(6)]
        payloads = [bytes(n) for n in lens]
        _, _, spills = model(t, payloads, [0] * len(lens))
        if spills:
            return lens


def write_test(out, name, spec):
    t = {**DEFAULT, **spec}
    rng = random.Random(t["seed"])
    lens = scene_lengths(t, rng)
    payloads = [bytes(rng.randrange(256) for _ in range(n)) for n in lens]
    stamps = [rng.randrange(1 << 24) for _ in lens]
    packets, frames, spills = model(t, payloads, stamps)
    if t["spill"] and not spills:
        raise RuntimeError("spill case not produced")
    framer = framing.AosFramer(t["scid"], t["vcid"])
    idle = [cadu(framer.idle()) for _ in range(256)]

    d = out / name
    d.mkdir(parents=True, exist_ok=True)
    fields = [t["apid"], t["pkt_data"], t["scid"], t["vcid"], t["idle_en"], t["frame_gap"],
              t["in_pct"], t["out_pct"], len(lens)]
    (d / "cfg.txt").write_text(" ".join(str(v) for v in fields) + "\n")
    (d / "payload.hex").write_text("".join(f"{b:02x}\n" for p in payloads for b in p))
    (d / "lens.txt").write_text("".join(f"{n}\n" for n in lens))
    (d / "meta.hex").write_text("".join(f"{s:06x}\n" for s in stamps))
    (d / "packets.txt").write_text("".join(f"{b:02x} {f} {s} {l}\n" for b, f, s, l in packets))
    (d / "frames.hex").write_text("".join(f"{b:02x}\n" for f in frames for b in f))
    (d / "cadus.hex").write_text("".join(f"{b:02x}\n" for f in frames for b in cadu(f)))
    (d / "idle_cadus.hex").write_text("".join(f"{b:02x}\n" for c in idle for b in c))
    return len(lens), len(packets), len(frames), spills


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "build/sim/dl")
    names = sys.argv[2:] or list(TESTS)
    for name in names:
        n, p, f, s = write_test(out, name, TESTS[name])
        print(f"{name:10} {n} scenes, {p:6} packet bytes, {f:3} frames, {s} spills")
    (out / "tests.txt").write_text("\n".join(names) + "\n")


if __name__ == "__main__":
    main()
