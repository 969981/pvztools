import pvztools


def test_version():
    import re, pathlib
    pyproject = pathlib.Path(__file__).resolve().parents[1] / "pyproject.toml"
    m = re.search(r'^version = "([^"]+)"', pyproject.read_text(encoding="utf-8"), re.M)
    assert pvztools.__version__ == m.group(1)
