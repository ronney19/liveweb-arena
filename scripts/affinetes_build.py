#!/usr/bin/env python3
"""
Build LiveWeb Arena Docker image using affinetes.

Usage:
    python scripts/affinetes_build.py [options]

Examples:
    # Build locally
    python scripts/affinetes_build.py

    # Build with custom tag
    python scripts/affinetes_build.py --tag liveweb-arena:v2

    # Build and push to registry
    python scripts/affinetes_build.py --push --registry docker.io/myuser

    # Build without cache
    python scripts/affinetes_build.py --no-cache
"""

import argparse
import sys
from pathlib import Path

# Add project root to path so affinetes can be found
PROJECT_ROOT = Path(__file__).resolve().parent.parent

try:
    import affinetes as af
except ImportError:
    print("Error: affinetes is not installed.")
    print("Install it with: pip install git+https://github.com/AffineFoundation/affinetes.git@main")
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Build LiveWeb Arena image with affinetes")
    parser.add_argument(
        "--tag",
        type=str,
        default="liveweb-arena:latest",
        help="Image tag (default: liveweb-arena:latest)",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Build without Docker cache",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress build output",
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help="Push image to registry after build",
    )
    parser.add_argument(
        "--registry",
        type=str,
        default=None,
        help="Registry URL for push (e.g., docker.io/myuser)",
    )
    args = parser.parse_args()

    print(f"Building LiveWeb Arena image: {args.tag}")
    print(f"Source: {PROJECT_ROOT}")
    print("-" * 50)

    image_tag = af.build_image_from_env(
        env_path=str(PROJECT_ROOT),
        image_tag=args.tag,
        nocache=args.no_cache,
        quiet=args.quiet,
        push=args.push,
        registry=args.registry,
    )

    print("-" * 50)
    print(f"Image built: {image_tag}")

    if args.push:
        print(f"Image pushed to registry")

    print()
    print("To run an evaluation:")
    print(f'  python scripts/affinetes_example.py --image {image_tag}')


if __name__ == "__main__":
    main()
