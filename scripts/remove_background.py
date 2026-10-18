#!/usr/bin/env python3
"""Matte an existing directory of complete RGB PNG frames."""

from __future__ import annotations

import argparse
from pathlib import Path

from rgb_matte import (
    DEFAULT_BACKGROUND_MAX,
    DEFAULT_BACKGROUND_MIN,
    matte_directory,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--background-min", type=int, default=DEFAULT_BACKGROUND_MIN)
    parser.add_argument("--background-max", type=int, default=DEFAULT_BACKGROUND_MAX)
    parser.add_argument("--edge-pixels", type=int, default=2)
    args = parser.parse_args()
    manifest = matte_directory(
        args.source,
        args.output,
        background_min=args.background_min,
        background_max=args.background_max,
        edge_pixels=args.edge_pixels,
    )
    print(f"manifest={manifest}")


if __name__ == "__main__":
    main()
