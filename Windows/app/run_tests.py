#!/usr/bin/env python3
import pathlib
import sys
import unittest

if __name__ == '__main__':
    suite = unittest.defaultTestLoader.discover(str(pathlib.Path(__file__).resolve().parent / 'tests'))
    sys.exit(0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1)
