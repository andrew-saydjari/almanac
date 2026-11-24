# Installation Guide

This guide covers installing this fork of `almanac` (`andrew-saydjari/almanac`, version
0.5.3).

## Requirements

- **Python**: 3.11 or newer
- **Raw data**: local (or mounted) access to the raw APOGEE frames, and to the plate,
  plugmap and `sdsscore` (`confSummary`) files; see the path settings in the
  [Configuration Guide](configuration.md#data-paths)
- **Database**: access to the SDSS database (`sdss5db`), directly or through an SSH tunnel

## Installing the fork

The `sdss-almanac` package on PyPI, and `uv pip install git+https://github.com/sdss/almanac`,
install upstream's almanac, which does not have this fork's changes. Install the fork
from its repository.

### With uv (recommended)

```bash
git clone https://github.com/andrew-saydjari/almanac.git
cd almanac
uv sync --frozen                 # creates .venv from uv.lock
uv run almanac --help
```

### Into an existing environment

```bash
uv pip install -e /path/to/almanac        # or: pip install -e /path/to/almanac
```

### Development installation

```bash
uv sync --frozen --extra dev              # tests
uv sync --frozen --extra dev --extra docs # tests and the Sphinx build
```

## Post-Installation Setup

### Database configuration

The first run creates `~/.almanac/config.yaml` with the defaults. Set the host, port and
user of the database you use, and put the password in `~/.pgpass` (or in a file named by
`PGPASSFILE`):

```bash
almanac config show                         # current settings and the file path
almanac config set sdssdb.host localhost    # e.g. through an SSH tunnel
almanac config set sdssdb.port 63333        # the local end of the tunnel
```

### Data paths

Point `apogee_dir`, `sdsscore_dir`, `mapper_dir` and `platelist_dir` at the local copies
of the raw data if the Utah defaults do not apply:

```bash
almanac config set apogee_dir <path to the raw APOGEE frames>/
```

### Verification

```bash
almanac --help
almanac config show
almanac --mjd 60000 --apo -vv         # one night, read from the raw files
```

## Dependencies

Installed automatically (see `pyproject.toml`; `uv.lock` pins the versions):

- **Data**: `numpy`, `scipy`, `astropy`, `pyerfa`, `h5py`, `healpy`, `pyyaml`, `pydl`
- **Database**: `sdssdb`, `peewee`, `sdss-semaphore`
- **Models**: `pydantic`, `pydantic-core`, `typing-extensions`
- **CLI/Display**: `click`, `tqdm`, `rich`, `colorlog`

Optional extras:

- **`dev`**: `pytest` and plugins, `flake8`, `isort`, `doc8`, `sphinx`, and other tools
- **`docs`**: Sphinx, the `furo` theme, `myst-parser`, `sphinx-autodoc-typehints`,
  `sphinx-copybutton`, `sphinxcontrib-bibtex`, `linkify-it-py`

## Troubleshooting

**Database connection fails**: check the host, port and user in `almanac config show`,
that the tunnel is up, and the `~/.pgpass` entry.

**Missing raw data**: check the path settings; nights without raw files produce no
exposures.

**Permission errors**: almanac writes only the output file you name, its checkpoints
(`add metadata`), and `~/.almanac/config.yaml`.

See the [FAQ](faq.md) for more.
