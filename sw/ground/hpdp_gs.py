"""HPDP ground station (SPEC.md 9.2, M6).

    python sw/ground/hpdp_gs.py decode CAPTURE.cap [options]       from a capture file
    python sw/ground/hpdp_gs.py decode --tcp HOST:PORT [options]   live from the board (SW-05)
    python sw/ground/hpdp_gs.py view SCENE.hpdp ... [--ref ...]    look at reconstructed scenes
    python sw/ground/hpdp_gs.py serve CAPTURE.cap [--port N]       replay a capture over TCP (tests)

decode: capture records (1024 bytes: derandomized codeblock + status word) ->
RS(255,223) decoding with the dual-basis conversion (--crosscheck: every
codeword also through reedsolo) -> AOS frame checks (version, spacecraft ID,
VC frame counts) -> packets (first header pointer, sequence counts) -> scenes
(sequence flags, packet indices, scene IDs) -> CCSDS 123 decompression.
Options:
  --ref DIR|FILES    reference scenes (the model's corrected scenes: ref_NNNN.hpdp
                     from sw/host/run_m5.py): each scene is checked bit for bit (GS-06)
  --out DIR          write the reconstructed scenes as scene_NNNN.hpdp
  --report FILE      Markdown report (link statistics, one row per scene)
  --png DIR          one picture per scene (RGB, band, spectrum, statistics)
  --show             interactive display (GS-05), filled while decoding

Needs numpy and matplotlib only for --show, --png and view (the venv
~/hpdp_gs_venv has them). Exit status 0 when every scene with a reference is
bit-exact and none is missing.
"""

import argparse
import socket
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from gs_decode import RECORD_BYTES, GroundDecoder, SceneResult, capture_records, f, tcp_records  # noqa: E402
from gs_report import link_lines, load_refs, scene_line, verify, write_report  # noqa: E402


def host_port(text):
    host, _, port = text.rpartition(":")
    return host or "127.0.0.1", int(port)


# ---------------------------------------------------------------------------
def decode(a):
    refs = load_refs(a.ref)
    if a.tcp:
        host, port = host_port(a.tcp)
        source_name = f"tcp://{host}:{port}"
        print(f"connecting to {source_name} ...")
        records = tcp_records(host, port, timeout=a.connect_timeout)
    else:
        source_name = a.capture
        records = capture_records(a.capture)
    dec = GroundDecoder(a.scid, a.vcid, a.apid, decompress=not a.no_decompress, crosscheck=a.crosscheck)
    out_dir = Path(a.out) if a.out else None
    png_dir = Path(a.png) if a.png else None
    for d in (out_dir, png_dir):
        if d:
            d.mkdir(parents=True, exist_ok=True)
    results, verdicts = [], {}
    viewer = None
    if a.show:
        from gs_view import Viewer
        viewer = Viewer(refs, tuple(a.rgb) if a.rgb else None, title=f"HPDP ground station: {source_name}")

    def handle(r):
        v = verify(r, refs) if refs else None
        results.append(r)
        if v is not None:
            verdicts[r.scene_id] = v
        print(scene_line(r, v), flush=True)
        if out_dir and r.scene is not None:
            f.write_scene(out_dir / f"scene_{r.scene_id:04d}.hpdp", r.scene)
        if png_dir:
            from gs_view import render_png
            render_png(png_dir / f"scene_{r.scene_id:04d}.png", r, v, refs.get(r.scene_id), dec.stats,
                       tuple(a.rgb) if a.rgb else None)
        if viewer:
            viewer.feed(r, v, dec.stats)

    def run():
        t0 = time.perf_counter()
        n = 0
        for rec in records:
            n += 1
            for r in dec.feed(rec):
                handle(r)
            if viewer and n % 256 == 0:
                viewer.feed(stats=dec.stats)
        for r in dec.flush():
            handle(r)
        if viewer:
            viewer.feed(stats=dec.stats)
        print(f"{n} records in {time.perf_counter() - t0:.1f} s")

    if viewer:
        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        viewer.show()                       # returns when the window is closed
        if worker.is_alive():
            print("window closed while decoding: stopped")
            return 1
    else:
        run()

    for line in link_lines(dec.stats):
        print(line)
    received = {r.scene_id for r in results}
    for sid in sorted(set(refs) - received):
        verdicts[sid] = verify(SceneResult(sid, reason="not received"), refs)
        print(f"scene {sid}: not received -> FAIL")
    if a.report:
        write_report(a.report, source_name, results, verdicts, dec.stats)
        print(f"report: {a.report}")
    if refs:
        passed = sum(v.status == "PASS" for v in verdicts.values())
        failed = sum(v.status == "FAIL" for v in verdicts.values())
        print(("PASS" if not failed else "FAIL") + f": hpdp_gs, {passed} of {passed + failed} scenes bit-exact")
        return 0 if not failed else 1
    print(f"done: {dec.stats.scenes_ok} scenes reconstructed, {dec.stats.scenes_failed} not")
    return 0 if not dec.stats.scenes_failed else 1


def view(a):
    from gs_view import Viewer
    refs = load_refs(a.ref)
    viewer = Viewer(refs, tuple(a.rgb) if a.rgb else None)
    for k, p in enumerate(a.scenes):
        scene = f.read_scene(p)
        r = SceneResult(k, scene=scene, complete=True)
        viewer.feed(r, verify(r, refs) if refs else None)
    viewer.show()
    return 0


def serve(a):
    """Replays a capture over TCP to each client that connects (one at a time)."""
    data = Path(a.capture).read_bytes()
    if len(data) % RECORD_BYTES:
        sys.exit(f"{a.capture}: not a whole number of {RECORD_BYTES}-byte records")
    chunk = 64 * RECORD_BYTES
    delay = chunk * 8 / (a.mbps * 1e6) if a.mbps else 0
    with socket.create_server(("", a.port)) as srv:
        print(f"serving {a.capture} ({len(data) // RECORD_BYTES} records) on port {a.port}"
              + (f" at {a.mbps:g} Mbit/s" if a.mbps else ""))
        while True:
            conn, peer = srv.accept()
            with conn:
                print(f"client {peer[0]}:{peer[1]}")
                for off in range(0, len(data), chunk):
                    conn.sendall(data[off:off + chunk])
                    if delay:
                        time.sleep(delay)
            print("sent, connection closed")
            if not a.loop:
                return 0


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("decode", help="decode a capture file or a live TCP stream")
    d.add_argument("capture", nargs="?", help=".cap file (or gr.bin from run_m5.py)")
    d.add_argument("--tcp", metavar="HOST:PORT", help="live records from the board")
    d.add_argument("--connect-timeout", type=float, default=10.0)
    d.add_argument("--scid", type=lambda s: int(s, 0), default=0x42)
    d.add_argument("--vcid", type=int, default=1)
    d.add_argument("--apid", type=lambda s: int(s, 0), default=0x100)
    d.add_argument("--ref", nargs="+", help="reference scenes: a directory with ref_NNNN.hpdp, or files")
    d.add_argument("--out", help="directory for the reconstructed scenes")
    d.add_argument("--report", help="Markdown report file")
    d.add_argument("--png", help="directory for one picture per scene")
    d.add_argument("--show", action="store_true", help="interactive display")
    d.add_argument("--rgb", nargs=3, type=int, metavar=("R", "G", "B"), help="bands for the colour picture")
    d.add_argument("--crosscheck", action="store_true", help="RS decoding also through reedsolo (slow)")
    d.add_argument("--no-decompress", action="store_true", help="link statistics only")

    v = sub.add_parser("view", help="display reconstructed scenes")
    v.add_argument("scenes", nargs="+", help=".hpdp files")
    v.add_argument("--ref", nargs="+")
    v.add_argument("--rgb", nargs=3, type=int, metavar=("R", "G", "B"))

    s = sub.add_parser("serve", help="replay a capture over TCP")
    s.add_argument("capture")
    s.add_argument("--port", type=int, default=5001)
    s.add_argument("--mbps", type=float, default=0, help="rate limit (default: as fast as possible)")
    s.add_argument("--loop", action="store_true", help="keep serving clients")

    a = p.parse_args()
    if a.cmd == "decode":
        if bool(a.capture) == bool(a.tcp):
            p.error("decode needs a capture file or --tcp, not both")
        return decode(a)
    if a.cmd == "view":
        return view(a)
    return serve(a)


if __name__ == "__main__":
    sys.exit(main())
