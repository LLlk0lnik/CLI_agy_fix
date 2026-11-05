#!/usr/bin/env python3
"""Compatibility entry point: the former HTTP/OAuth bridge has been removed."""
import sys
from acp_terminal import entrypoint

if __name__ == '__main__':
    sys.exit(entrypoint())
