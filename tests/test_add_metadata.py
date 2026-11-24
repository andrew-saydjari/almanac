"""`almanac add metadata` on a file without fiber mappings (adapted from upstream #66).

Upstream checks this in its own `add metadata` body; in the fork the check
lives in `almanac.metadata.build_metadata`, so it covers the CLI and the API.
"""

import os

import h5py as h5
import numpy as np
import pytest

import almanac.metadata as md
from tests.helpers import MJD, run_cli, write_night
from tests.test_metadata_build import fake_query

# Fail fast instead of retrying with a backoff if the fake query ever breaks.
FAST = dict(query_batch=fake_query, query_workers=0, max_batch_attempts=1, retry_backoff=0.0)


def _no_queries(*args, **kwargs):
    pytest.fail("the database should not be queried")


class TestAddMetadataNoFibers:
    def test_no_fibers_exits_1_and_writes_nothing(self, tmp_path, monkeypatch):
        path = str(tmp_path / "night.h5")
        write_night(path)
        monkeypatch.setattr(md, "_default_query_batch", _no_queries)
        result = run_cli(["add", "metadata", path])
        assert result.exit_code == 1
        assert "no fiber mappings" in result.output
        with h5.File(path, "r") as fp:
            assert "meta" not in fp
            assert "meta_not_found" not in fp
        assert not os.path.exists(f"{path}.meta-checkpoint")

    def test_no_fibers_with_output_does_not_create_output(self, tmp_path, monkeypatch):
        path = str(tmp_path / "night.h5")
        out = str(tmp_path / "targets.h5")
        write_night(path)
        monkeypatch.setattr(md, "_default_query_batch", _no_queries)
        result = run_cli(["add", "metadata", path, "--output", out])
        assert result.exit_code == 1
        assert not os.path.exists(out)

    def test_existing_meta_group_is_preserved_when_no_fibers(self, tmp_path):
        path = str(tmp_path / "night.h5")
        write_night(path)
        with h5.File(path, "a") as fp:
            fp.create_group("meta").create_dataset("sdss_id", data=[1, 2, 3])
        result = run_cli(["add", "metadata", path])
        assert result.exit_code == 1
        with h5.File(path, "r") as fp:
            assert list(fp["meta/sdss_id"][:]) == [1, 2, 3]

    def test_api_raises(self, tmp_path):
        path = str(tmp_path / "night.h5")
        write_night(path)
        with pytest.raises(md.MetadataBuildError, match="no fiber mappings"):
            md.build_metadata(path, query_batch=_no_queries)

    def test_nights_without_fibers_are_counted_and_skipped(self, tmp_path, capsys):
        path = str(tmp_path / "two_nights.h5")
        write_night(path, mjd=MJD)          # no fibers
        write_night(path, mjd=MJD + 1)      # fibers added below
        with h5.File(path, "a") as fp:
            g = fp.create_group(f"raw/apo/{MJD + 1}/fibers/123")
            g.create_dataset("sdss_id", data=np.array([-1, 101, 102], dtype=np.int64))
        assert md.count_nights_with_fibers(path, ("apo", "lco")) == (2, 1)
        assert md.count_nights_with_fibers(path, ("apo",), mjds=[MJD]) == (1, 0)

        md.build_metadata(path, **FAST)
        assert "1 of 2 selected nights" in capsys.readouterr().out
        with h5.File(path, "r") as fp:
            assert sorted(fp["meta/sdss_id"][:].tolist()) == [101, 102]

    def test_explicit_ids_bypass_the_check(self, tmp_path):
        # `ids=` callers supply their own id set; the input is then only provenance.
        path = str(tmp_path / "night.h5")
        write_night(path)
        out = str(tmp_path / "targets.h5")

        md.build_metadata(path, output_path=out, ids=[103], **FAST)
        with h5.File(out, "r") as fp:
            assert fp["meta/sdss_id"][:].tolist() == [103]
