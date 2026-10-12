"""Thin glue between sg-urban-spine Silver tables and an unmodified CEA scenario.

Nothing in this package computes energy. It writes CEA's own input files (zone.shp, surroundings.shp,
terrain.tif, weather.epw), runs CEA's own scripts through ``cea workflow``, and reads CEA's own result files.
"""
