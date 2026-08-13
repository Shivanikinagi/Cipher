"""CLI: serve API, run benchmark, simulate demo telemetry."""
from __future__ import annotations

import argparse
import asyncio
import sys

import uvicorn


def cmd_serve(_: argparse.Namespace) -> None:
    uvicorn.run("impact_engine.main:app", host="0.0.0.0", port=8000, reload=False)


def cmd_benchmark(args: argparse.Namespace) -> None:
    from benchmark.run import main as run_benchmark

    sys.exit(run_benchmark(cases=args.cases, seed=args.seed))


def cmd_simulate(args: argparse.Namespace) -> None:
    from demo.simulate import main as simulate

    asyncio.run(simulate(base_url=args.base_url))


def cmd_demo(args: argparse.Namespace) -> None:
    from demo.run import demo_analysis

    result = asyncio.run(demo_analysis())
    print(result.explanation)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="impact-engine")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the FastAPI server")
    serve.set_defaults(func=cmd_serve)

    bench = sub.add_parser(
        "benchmark",
        help="run precision/recall benchmark (static vs hybrid)",
    )
    bench.add_argument("--cases", type=int, default=60)
    bench.add_argument("--seed", type=int, default=42)
    bench.set_defaults(func=cmd_benchmark)

    sim = sub.add_parser(
        "simulate",
        help="stream demo traces/metrics into a running server",
    )
    sim.add_argument("--base-url", default="http://127.0.0.1:8000")
    sim.set_defaults(func=cmd_simulate)

    demo = sub.add_parser("demo", help="run an in-process analysis on the demo graph")
    demo.set_defaults(func=cmd_demo)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()