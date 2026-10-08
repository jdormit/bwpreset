"""Legacy corpus checks run when a local Bitwig installation is available."""

import pytest


def bitwig_available():
    from bwpreset.config import bitwig_jar

    try:
        return bitwig_jar().is_file()
    except FileNotFoundError:
        return False


def pytest_collection_modifyitems(items):
    if bitwig_available():
        return
    corpus_modules = {"test_bwpreset.py", "test_curves.py", "test_pack_formats.py"}
    independent = {
        "test_maps_and_tagged_backreferences_round_trip",
        "test_vertical_steps_are_valid",
        "test_invalid_points_are_rejected",
        "test_unknown_point_fields_are_not_silently_dropped",
    }
    for item in items:
        if item.path.name in corpus_modules and item.originalname not in independent:
            item.add_marker(pytest.mark.skip(reason="local Bitwig corpus integration test"))
