from pathlib import Path

def test_skeleton_files_exist():
    root = Path(__file__).parents[1]
    assert (root / 'config.yaml').exists()
    assert (root / 'run.py').exists()
