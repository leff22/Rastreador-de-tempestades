import argparse
import json
from dataclasses import replace
from functools import partial
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
import sys
import webbrowser

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config import DEFAULT_CONFIG, TrackingConfig
from src.pipeline import RadarTrackingPipeline
from system_tools import generate_gif, validate_outputs, compare_overlaps


def main():
    parser = argparse.ArgumentParser(description="Núcleos intensos e tendência de direção causal.")
    commands = parser.add_subparsers(dest="command", required=True)
    process = commands.add_parser("process")
    process.add_argument("--input-dir", type=Path, default=ROOT / "inputs" / "rainviewer")
    process.add_argument("--output-root", type=Path, default=ROOT / "outputs")
    process.add_argument("--cache-dir", type=Path, default=ROOT / ".cache" / "detections")
    process.add_argument("--overlap", type=float, default=DEFAULT_CONFIG.overlap_threshold)
    process.add_argument("--preview-gif", action="store_true")
    for name in ("gif", "validate"):
        command = commands.add_parser(name)
        command.add_argument("--output-root", type=Path, default=ROOT / "outputs")
    compare = commands.add_parser("compare")
    compare.add_argument("--cache-dir", type=Path, default=ROOT / ".cache" / "detections")
    compare.add_argument("--output-root", type=Path, default=ROOT / "outputs")
    serve = commands.add_parser("serve-preview")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if args.command == "process":
        config = replace(DEFAULT_CONFIG, overlap_threshold=args.overlap, output_root=args.output_root)
        RadarTrackingPipeline(config).process_directory(args.input_dir, args.output_root, args.cache_dir)
        validate_outputs(args.output_root)
        if args.preview_gif:
            generate_gif(args.output_root)
    elif args.command == "gif":
        generate_gif(args.output_root)
    elif args.command == "validate":
        validate_outputs(args.output_root)
    elif args.command == "compare":
        summary = json.loads((args.output_root / "sequence_summary.json").read_text(encoding="utf-8"))
        config = TrackingConfig(**{**summary["config"], "output_root": args.output_root})
        compare_overlaps(args.cache_dir, args.output_root, config)
    else:
        url = f"http://127.0.0.1:{args.port}/preview/index.html"
        server = ThreadingHTTPServer(("127.0.0.1", args.port), partial(SimpleHTTPRequestHandler, directory=str(ROOT)))
        print(url, flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
