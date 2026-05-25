#!/usr/bin/env python
"""
Run the STEEX Dashboard server.

Usage:
    python run_dashboard.py [--host 0.0.0.0] [--port 5000] [--debug]

The dashboard will be available at http://localhost:5000
"""

import argparse
from frontend.app import create_app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run STEEX Dashboard")
    parser.add_argument("--host", default="0.0.0.0", help="Server host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=5000, help="Server port (default: 5000)")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")

    args = parser.parse_args()

    app = create_app()
    print(f"Starting STEEX Dashboard on {args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug)
